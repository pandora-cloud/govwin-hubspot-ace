"""Submit a HubSpot deal to AWS Partner Central via the three-call flow.

Triggered by SQS (events from ``hubspot_webhook_receiver``). For each event:

1. Fetch the full HubSpot deal record.
2. Atomically reserve a ClientToken in DynamoDB (idempotent on retry).
3. Call ``CreateOpportunity`` -> persist ``Id`` + ``LastModifiedDate``.
4. Call ``AssociateOpportunity`` with the configured Solution.
5. Call ``StartEngagementFromOpportunityTask`` to submit (with a
   separately-reserved task ClientToken so retries reuse it).

The mapping is reloaded between steps and updated via merge semantics so a
SQS redelivery resumes from the last persisted step. Permanent ACE errors
(ValidationException, AccessDeniedException) are dropped from the SQS batch
without retry; transient errors are reported as batch item failures.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.ace.mapper import (
    ACEMappingError,
    aws_products_for_deal,
    map_hubspot_deal_to_ace_create_payload,
    resolve_solution_id,
)
from src.ace.validators import is_valid_govwin_id, is_valid_hubspot_object_id
from src.config import load_config
from src.hubspot.client import HubSpotAPIError, HubSpotClient
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


_PERMANENT_ERROR_CODES: set[str] = {
    "ValidationException",
    "AccessDeniedException",
    "ResourceNotFoundException",
    "BadRequestException",
}


_sns_client: Any | None = None


def _publish_permanent_error_alert(
    *,
    config: Any,
    deal_id: str,
    govwin_id: str,
    aws_step: str = "AWS write",
    error: str,
) -> None:
    """Thin wrapper that builds the message + delegates to src.alerts.

    The actual SNS publish + error-detail redaction lives in
    ``src.alerts.publish_alert``. This Lambda's wrapper exists to keep
    the call sites readable and pin the per-step intro copy that's
    specific to the submit pipeline.
    """
    from src.alerts import publish_alert

    subject = f"ACE {aws_step} rejected (deal {deal_id})"
    intro = {
        "mapping": (
            "A HubSpot deal could not be mapped to a valid CreateOpportunity "
            "payload. The deal needs to be corrected in HubSpot before resubmission."
        ),
        "CreateOpportunity": (
            "AWS Partner Central rejected the initial CreateOpportunity "
            "submission. The opportunity does not exist on the AWS side."
        ),
        "AssociateOpportunity": (
            "AWS Partner Central rejected an AssociateOpportunity call. The "
            "opportunity exists but a Solution or Product association failed."
        ),
        "StartEngagementFromOpportunityTask": (
            "AWS Partner Central rejected StartEngagementFromOpportunityTask. "
            "The opportunity exists but is not in the AWS reviewer queue."
        ),
    }.get(aws_step, f"AWS Partner Central rejected the {aws_step} call.")
    message = (
        f"{intro}\n\n"
        f"HubSpot deal id: {deal_id}\n"
        f"GovWin opp id: {govwin_id}\n"
        f"Catalog: {config.ace.catalog}\n"
        f"AWS step: {aws_step}\n"
        "Full detail (with field values redacted) is appended below; the\n"
        "unredacted detail stays in CloudWatch."
    )
    publish_alert(
        config=config,
        subject=subject,
        message=message,
        error_detail=error,
    )


# Backwards-compat alias for any test importing the old name.
_publish_mapping_error_alert = _publish_permanent_error_alert


def _trigger_stages() -> set[str]:
    raw = os.environ.get("ACE_TRIGGER_STAGES", "submit_to_aws,submitted_to_aws")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _is_submit_trigger(hs_event: dict[str, Any]) -> bool:
    """Decide whether an inbound HubSpot event represents a submit-to-ACE intent."""
    if hs_event.get("subscriptionType") != "object.propertyChange":
        return False
    if hs_event.get("propertyName") != "dealstage":
        return False
    new_value = str(hs_event.get("propertyValue") or "").strip().lower()
    return new_value in _trigger_stages()


_DEAL_PROPERTIES_FOR_MAPPING = [
    "dealname",
    "amount",
    "closedate",
    "description",
    "dealstage",
    "hubspot_owner_id",
    "govwin_opp_id",
    "govwin_iq_opp_id",
    "govwin_agency",
    "govwin_industry",
    "govwin_primary_requirement",
    "govwin_ace_partner_need",
    "govwin_ace_delivery_model",
    "govwin_ace_solution_id",
    "govwin_ace_solution",  # legacy alias
    "govwin_ace_use_case",
    "govwin_ace_other_solution_description",
    "govwin_ace_opportunity_type",
    # Extended BD-editable property surface for richer AWS submissions:
    "govwin_ace_marketing_source",
    "govwin_ace_marketing_campaign_name",
    "govwin_ace_marketing_use_cases",
    "govwin_ace_marketing_channel",
    "govwin_ace_marketing_dev_funded",
    "govwin_ace_competitor_name",
    "govwin_ace_additional_comments",
    "govwin_ace_aws_account_id",
    "govwin_ace_next_steps",
    "govwin_ace_related_opportunity_id",
    "govwin_ace_aws_products",
]

_COMPANY_PROPERTIES_FOR_MAPPING = [
    "name",
    "industry",
    "domain",
    "website",
    "address",
    "city",
    "state",
    "zip",
    "country",
]

_CONTACT_PROPERTIES_FOR_MAPPING = [
    "firstname",
    "lastname",
    "email",
    "phone",
    "jobtitle",
    # PII gate: only forward contacts whose lifecyclestage flags
    # customer-side intent. Hyperscaler-Contact records (AWS-side
    # participants the EventBridge handler created) are filtered out
    # via hs_lead_status.
    "lifecyclestage",
    "hs_lead_status",
]


def _load_deal(hubspot: HubSpotClient, deal_id: str) -> dict[str, Any]:
    """Fetch a deal with the properties we need for ACE mapping."""
    return hubspot.get_deal(deal_id, properties=_DEAL_PROPERTIES_FOR_MAPPING)


def _load_associated_records(
    hubspot: HubSpotClient, deal_id: str, deal: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Fetch the deal's associated company, contacts, and owner.

    Each fetch is best-effort: if HubSpot returns 404 or the association
    doesn't exist yet, we fall through with None / [] rather than blocking
    the submission. The mapper handles missing associated records by
    falling back to GovWin-derived deal properties.
    """
    company = hubspot.get_associated_company(deal_id, properties=_COMPANY_PROPERTIES_FOR_MAPPING)
    contacts = hubspot.get_associated_contacts(deal_id, properties=_CONTACT_PROPERTIES_FOR_MAPPING)
    owner_id = ""
    props = deal.get("properties") or deal
    if isinstance(props, dict):
        owner_id = str(props.get("hubspot_owner_id") or "")
    owner: dict[str, Any] | None = None
    if owner_id:
        try:
            owner = hubspot.get_owner(owner_id)
        except HubSpotAPIError as exc:
            # The owners endpoint requires the crm.objects.owners.read scope;
            # legacy private apps may not have it. AWS accepts an empty
            # OpportunityTeam at CreateOpportunity time, so degrade gracefully
            # rather than fail the whole submission. The mapper falls back
            # to a no-team payload when owner is None.
            logger.warning(
                "submit_to_ace: get_owner failed for deal=%s owner=%s status=%s; "
                "continuing with empty OpportunityTeam. Fix by adding "
                "crm.objects.owners.read to the HubSpot app scope set.",
                deal_id,
                owner_id,
                getattr(exc, "status_code", "unknown"),
            )
    return company, contacts, owner


def _process_event(
    hs_event: dict[str, Any],
    *,
    config: Any,
    state: SyncStateManager,
    ace: ACEClient,
    hubspot: HubSpotClient,
) -> dict[str, Any]:
    deal_id = str(hs_event.get("objectId") or "")
    if not is_valid_hubspot_object_id(deal_id):
        return {"status": "skipped", "reason": "invalid objectId"}
    if not _is_submit_trigger(hs_event):
        return {"status": "skipped", "reason": "not a submit-to-aws stage change"}

    deal = _load_deal(hubspot, deal_id)
    properties = deal.get("properties") or deal
    govwin_id = properties.get("govwin_opp_id") or properties.get("govwin_iq_opp_id")
    if not govwin_id or not is_valid_govwin_id(str(govwin_id)):
        return {"status": "skipped", "reason": "deal missing or invalid govwin_opp_id"}
    govwin_id = str(govwin_id)

    # Step 1: reserve ClientToken atomically.
    create_token = state.reserve_client_token(govwin_id, ACEClient.new_client_token())

    # Reload the mapping so we operate on the post-reservation snapshot.
    mapping = state.get_ace_mapping(govwin_id) or {}
    ace_opportunity_id = mapping.get("ace_opportunity_id") or ""
    last_modified_date = mapping.get("last_modified_date")

    # Step 2: CreateOpportunity if we don't already have an Id.
    if not ace_opportunity_id:
        company, contacts, owner = _load_associated_records(hubspot, deal_id, deal)
        try:
            payload = map_hubspot_deal_to_ace_create_payload(
                deal,
                config,
                client_token=create_token,
                company=company,
                contacts=contacts,
                owner=owner,
            )
        except ACEMappingError as exc:
            logger.warning("ACE mapping failed for deal %s: %s", deal_id, exc)
            _publish_permanent_error_alert(
                config=config,
                deal_id=deal_id,
                govwin_id=govwin_id,
                aws_step="mapping",
                error=str(exc),
            )
            return {"status": "rejected", "reason": str(exc)}

        try:
            response = ace.create_opportunity(payload)
        except ACEAPIError as create_exc:
            # Annotate the exception with the AWS step that failed so the
            # outer handler's writeback can craft a step-specific message
            # AND so a single source of truth for the rejection text wins
            # (previously the inner block wrote a detailed reason that the
            # outer block then overwrote with a generic one).
            create_exc.aws_step = "CreateOpportunity"  # type: ignore[attr-defined]
            raise
        ace_opportunity_id = response["Id"]
        last_modified_date = response.get("LastModifiedDate")
        state.update_ace_mapping(
            govwin_id=govwin_id,
            ace_opportunity_id=str(ace_opportunity_id),
            last_modified_date=str(last_modified_date) if last_modified_date else None,
            client_token=create_token,
            hubspot_deal_id=deal_id,
        )
        # Surface the AWS-side identifiers on the HubSpot deal immediately
        # so BD doesn't have to wait for an EventBridge round-trip to see
        # the opportunity in their CRM. handle_ace_event will update
        # govwin_aws_cosell_status on subsequent ReviewStatus changes.
        try:
            hubspot.update_deal(
                deal_id,
                {
                    "govwin_aws_cosell_id": str(ace_opportunity_id),
                    "govwin_aws_cosell_status": "Pending Submission",
                },
            )
        except Exception:  # noqa: BLE001 -- write-back is best-effort
            logger.exception(
                "submit_to_ace: write-back of aws_cosell_id failed for deal %s",
                deal_id,
            )
        logger.info("ace.created opportunity_id=%s govwin_id=%s", ace_opportunity_id, govwin_id)
        mapping = state.get_ace_mapping(govwin_id) or mapping

    # Step 3: AssociateOpportunity. Skipped when no Solution ID is configured
    # (e.g. Sandbox where no Approved solution is registered); in that case
    # the create-opportunity payload included OtherSolutionDescription, which
    # AWS accepts as the alternative.
    solution_id = resolve_solution_id(deal, config)
    if solution_id and not mapping.get("ace_task_id"):
        try:
            ace.associate_opportunity(
                opportunity_identifier=str(ace_opportunity_id),
                related_entity_identifier=solution_id,
                related_entity_type="Solutions",
            )
            logger.info("ace.associated solution=%s opp=%s", solution_id, ace_opportunity_id)
        except ACEAPIError as exc:
            if exc.code != "ConflictException":
                # Tag the step so the outer permanent-error handler can
                # SNS-alert with the right subject + body.
                exc.aws_step = "AssociateOpportunity"  # type: ignore[attr-defined]
                raise
            # AWS does not return the existing associated solution from the
            # error, so we cannot distinguish "same solution" from "different
            # solution already associated." Log loudly so an operator notices
            # if the deal's intended solution was changed between attempts.
            logger.warning(
                "ace.associate conflict on opp=%s solution=%s; existing "
                "association assumed correct (verify if solution changed)",
                ace_opportunity_id,
                solution_id,
            )
    elif not solution_id:
        logger.info(
            "ace.associate skipped: no Solution ID configured; relying on "
            "OtherSolutionDescription on opp=%s",
            ace_opportunity_id,
        )

    # Step 3b: AssociateOpportunity for any BD-tagged AWS products. Idempotent
    # via ConflictException -- a redelivered SQS message won't re-associate.
    aws_products = aws_products_for_deal(deal)
    if aws_products and not mapping.get("ace_task_id"):
        product_failures: list[str] = []
        for product_id in aws_products:
            try:
                ace.associate_opportunity(
                    opportunity_identifier=str(ace_opportunity_id),
                    related_entity_identifier=product_id,
                    related_entity_type="AwsProducts",
                )
                logger.info(
                    "ace.associated awsproduct=%s opp=%s",
                    product_id,
                    ace_opportunity_id,
                )
            except ACEAPIError as exc:
                if exc.code == "ConflictException":
                    continue  # already associated
                # Real failure (typo'd identifier, ResourceNotFoundException,
                # ValidationException). Don't fail the whole submission
                # because one product is invalid -- but DO surface to BD
                # via SNS so the bad value gets fixed in HubSpot.
                logger.warning(
                    "ace.associate awsproduct=%s failed: %s",
                    product_id,
                    exc,
                )
                product_failures.append(f"{product_id}: {exc.code}")
        if product_failures:
            # Intentionally NOT publishing the BD-facing SNS alert here.
            # CreateOpportunity already succeeded -- the opp exists in AWS,
            # the deal is advancing through the pipeline, and only a subset
            # of the product associations were rejected. Reusing the
            # "submission rejected" alert template here is misleading
            # (we got a near-miss alert that read like a hard failure
            # during the 2026-05-27 E2E test). The failing identifiers
            # are still logged at WARNING above so they're discoverable
            # in CloudWatch when BD asks why an associated product is
            # missing on the AWS-side opportunity. Common cause: a product
            # in our shipped catalog (resources/aws_products.json, sourced
            # from the production AWS Partner Central catalog) is not yet
            # carried by the Sandbox catalog.
            logger.info(
                "ace.associate partial failures on opp=%s (informational, "
                "opp creation already succeeded): %d/%d products rejected: %s",
                ace_opportunity_id,
                len(product_failures),
                len(aws_products),
                "; ".join(product_failures),
            )

    # Step 4: StartEngagementFromOpportunityTask. Reuse a persisted task token
    # so that an SQS retry hits the same idempotency key on the AWS side.
    if not mapping.get("ace_task_id"):
        task_token = state.reserve_task_client_token(govwin_id, ACEClient.new_client_token())
        try:
            task_response = ace.start_engagement_from_opportunity_task(
                opportunity_identifier=str(ace_opportunity_id),
                client_token=task_token,
            )
        except ACEAPIError as exc:
            exc.aws_step = "StartEngagementFromOpportunityTask"  # type: ignore[attr-defined]
            raise
        state.update_ace_mapping(
            govwin_id=govwin_id,
            ace_engagement_invitation_id=task_response.get("EngagementInvitationId"),
            ace_task_id=task_response.get("TaskId"),
            hubspot_deal_id=deal_id,
        )
        logger.info(
            "ace.engagement_started task=%s opp=%s",
            task_response.get("TaskId"),
            ace_opportunity_id,
        )

    return {
        "status": "submitted",
        "ace_opportunity_id": str(ace_opportunity_id),
        "govwin_id": govwin_id,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS event source mapping entry point."""
    config = load_config()
    state = SyncStateManager(config)
    ace = ACEClient(config)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with HubSpotClient(config) as hubspot:
        for record in event.get("Records", []):
            message_id = record.get("messageId", "?")
            try:
                hs_event = json.loads(record.get("body", "{}"))
            except json.JSONDecodeError:
                logger.warning("submit_to_ace: invalid JSON in message %s", message_id)
                # Permanent error: drop the message rather than retry.
                continue
            try:
                result = _process_event(
                    hs_event,
                    config=config,
                    state=state,
                    ace=ace,
                    hubspot=hubspot,
                )
                results.append(result)
            except ACEAPIError as exc:
                if exc.code in _PERMANENT_ERROR_CODES:
                    logger.warning(
                        "submit_to_ace: permanent error %s for message %s; dropping. detail=%s",
                        exc.code,
                        message_id,
                        str(exc),
                    )
                    deal_id = str((hs_event or {}).get("objectId") or "?")
                    # Resolve govwin_id from the deal so the SNS alert and
                    # HubSpot writeback both reference the real id rather
                    # than "(unknown)". get_deal failures are non-fatal --
                    # the alert still goes out with whatever we have.
                    govwin_id_for_alert = "(unknown)"
                    if is_valid_hubspot_object_id(deal_id):
                        try:
                            deal_obj = hubspot.get_deal(deal_id, properties=["govwin_opp_id"])
                            govwin_id_for_alert = (deal_obj.get("properties") or deal_obj).get(
                                "govwin_opp_id"
                            ) or "(missing on deal)"
                        except Exception:  # noqa: BLE001 -- best-effort
                            logger.exception("submit_to_ace: get_deal for SNS alert failed")
                    # Resolve which AWS step failed. CreateOpportunity tags
                    # the exception via aws_step (see _process_event); other
                    # steps don't tag yet but we can infer from the call
                    # stack info logged at warning time. Default to a
                    # generic label.
                    aws_step = getattr(exc, "aws_step", "AWS write")
                    # SNS alert. Best-effort; a publish failure does not
                    # propagate (the SQS message is still dropped to avoid
                    # poison-message loops).
                    try:
                        _publish_permanent_error_alert(
                            config=config,
                            deal_id=deal_id,
                            govwin_id=govwin_id_for_alert,
                            aws_step=aws_step,
                            error=f"AWS {exc.code}: {exc}",
                        )
                    except Exception:  # noqa: BLE001 -- alert is best-effort
                        logger.exception("submit_to_ace: SNS publish for permanent error failed")
                    # HubSpot writeback so the deal record reflects the
                    # rejection. Writes the AWS error blob (trimmed to
                    # HubSpot single-line text-property max of 480 chars)
                    # so BD sees the actual reason instead of a generic
                    # "see email" message. Was previously a generic
                    # message that overwrote a more detailed inner writeback.
                    if is_valid_hubspot_object_id(deal_id):
                        reason_blob = f"AWS rejected {aws_step} ({exc.code}): {exc}"[:480]
                        try:
                            hubspot.update_deal(
                                deal_id,
                                {
                                    "govwin_aws_cosell_status": "Action Required",
                                    "govwin_ace_next_steps": reason_blob,
                                },
                            )
                        except Exception:  # noqa: BLE001 -- writeback is best-effort
                            logger.exception(
                                "submit_to_ace: rejection writeback failed for deal %s",
                                deal_id,
                            )
                    continue
                logger.warning(
                    "submit_to_ace: transient %s for message %s; retrying via SQS",
                    exc.code,
                    message_id,
                )
                failures.append({"itemIdentifier": message_id})
            except Exception:  # noqa: BLE001 -- batch-failure path
                logger.exception("submit_to_ace failed for message %s", message_id)
                failures.append({"itemIdentifier": message_id})

    return {"results": results, "batchItemFailures": failures}
