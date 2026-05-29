"""Apply HubSpot deal field changes to a previously-submitted ACE opportunity.

Triggered by SQS for property-change events on already-mapped deals
(``amount``, ``closedate``, ``dealname``, ``description``). Each event:

1. Looks up the mapped GovWin id (preferring the ACE mapping by HubSpot
   deal id; falls back to fetching the deal and reading
   ``govwin_opp_id``).
2. Builds an ACE update kwargs dict from the changed property.
3. Calls ``UpdateOpportunity`` with optimistic locking.

Permanent errors (ValidationException, AccessDeniedException) are logged
and the SQS message is allowed to be deleted (no batch failure entry) so
poison messages do not loop indefinitely. Transient errors (Throttling,
InternalServer, Conflict) propagate as batch failures for SQS redelivery.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.ace.mapper import MRR_MONTHS_PER_YEAR
from src.ace.validators import is_valid_hubspot_object_id
from src.config import load_config
from src.hubspot.client import HubSpotClient
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


_PERMANENT_ERROR_CODES: set[str] = {
    "ValidationException",
    "AccessDeniedException",
    "ResourceNotFoundException",
    "BadRequestException",
}


def _publish_update_error_alert(*, config: Any, deal_id: str, prop: str, error: str) -> None:
    """Thin wrapper that builds the message + delegates to src.alerts.

    The actual SNS publish + error-detail redaction lives in
    ``src.alerts.publish_alert``. Without this Lambda's wrapper the call
    sites would need to inline the deal-id / property formatting at every
    failure path.
    """
    from src.alerts import publish_alert

    subject = f"ACE update rejected (deal {deal_id})"
    message = (
        "A HubSpot property change could not be applied to the corresponding "
        "AWS Partner Central opportunity. The deal in HubSpot is now out of "
        "sync with AWS and may need manual reconciliation.\n\n"
        f"HubSpot deal id: {deal_id}\n"
        f"Changed property: {prop}\n"
        f"Catalog: {config.ace.catalog}\n"
        "Full detail (with field values redacted) is appended below; the\n"
        "unredacted detail stays in CloudWatch."
    )
    publish_alert(
        config=config,
        subject=subject,
        message=message,
        error_detail=error,
    )


def _ensure_closed_lost_pair_consistency(
    payload: dict[str, Any], hubspot: HubSpotClient, deal_id: str
) -> None:
    """Backfill the Stage/ClosedLostReason companion from HubSpot when missing.

    See _process_event for the failure mode (out-of-order webhooks lose the
    Closed-Lost transition). Reads the missing companion from the deal --
    the deal is the source of truth for what BD intended.
    """
    life_cycle = dict(payload.get("LifeCycle") or {})
    stage = life_cycle.get("Stage")
    reason = life_cycle.get("ClosedLostReason")
    if stage == "Closed Lost" and not reason:
        try:
            deal = hubspot.get_deal(deal_id, properties=["govwin_ace_closed_lost_reason"])
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception(
                "update_in_ace: get_deal for closed-lost reason backfill failed deal=%s",
                deal_id,
            )
            return
        deal_reason = (deal.get("properties") or {}).get("govwin_ace_closed_lost_reason")
        if deal_reason:
            life_cycle["ClosedLostReason"] = str(deal_reason)
            payload["LifeCycle"] = life_cycle
            logger.info(
                "update_in_ace: closed-lost pair self-heal; backfilled "
                "ClosedLostReason from deal=%s",
                deal_id,
            )
    elif reason and stage != "Closed Lost":
        # The other direction: the reason webhook fired but Stage hasn't
        # been set yet. Read the deal's stage; if it says Closed Lost,
        # set Stage too. Otherwise drop the orphan ClosedLostReason since
        # AWS rejects Reason without Closed-Lost Stage.
        try:
            deal = hubspot.get_deal(deal_id, properties=["govwin_ace_lifecycle_stage"])
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception(
                "update_in_ace: get_deal for closed-lost stage backfill failed deal=%s",
                deal_id,
            )
            return
        deal_stage = (deal.get("properties") or {}).get("govwin_ace_lifecycle_stage")
        if deal_stage == "Closed Lost":
            life_cycle["Stage"] = "Closed Lost"
            payload["LifeCycle"] = life_cycle
            logger.info(
                "update_in_ace: closed-lost pair self-heal; backfilled "
                "Stage=Closed Lost from deal=%s",
                deal_id,
            )
        else:
            life_cycle.pop("ClosedLostReason", None)
            payload["LifeCycle"] = life_cycle
            logger.info(
                "update_in_ace: closed-lost pair self-heal; dropped orphan "
                "ClosedLostReason because deal Stage=%r",
                deal_stage,
            )


# Per-property delta handlers. Each returns True if the payload was
# actually mutated, False otherwise. Dispatch via ``_DELTA_HANDLERS``.


def _handle_amount(payload: dict[str, Any], value: Any, partner_company_name: str) -> bool:
    try:
        total = float(value)
    except (TypeError, ValueError):
        return False
    project = dict(payload.get("Project") or {})
    if total <= 0:
        project.pop("ExpectedCustomerSpend", None)
        payload["Project"] = project
        return True
    # Match the create-path MRR convention: HubSpot stores annual/total
    # contract value, AWS expects ExpectedCustomerSpend.Amount paired
    # with Frequency=Monthly. Divide by 12. Without this, an amount
    # update via webhook would write a value 12x the create-path
    # baseline; a real divergence between the two paths.
    monthly = total / MRR_MONTHS_PER_YEAR
    project["ExpectedCustomerSpend"] = [
        {
            "Amount": f"{monthly:.2f}",
            "CurrencyCode": "USD",
            "Frequency": "Monthly",
            "TargetCompany": partner_company_name,
        }
    ]
    payload["Project"] = project
    return True


def _handle_closedate(payload: dict[str, Any], value: Any, _: str) -> bool:
    # HubSpot delivers closedate as either a YYYY-MM-DD string or a
    # millisecond epoch depending on which API set it (the UI sets epoch
    # ms; the form's PATCH sets epoch ms; legacy paths may set ISO). AWS
    # TargetCloseDate is strictly YYYY-MM-DD. Normalize.
    raw = str(value).strip()
    normalized: str | None = None
    # Require >=10 digits to count as an epoch; anything shorter
    # (e.g. "20261231" with no separators) is rejected as malformed
    # rather than misread as some 1970-era epoch.
    if raw.isdigit() and len(raw) >= 10:
        try:
            n = int(raw)
            ms = n if n >= 1_000_000_000_000 else n * 1000
            from datetime import UTC as _UTC
            from datetime import datetime as _dt

            normalized = _dt.fromtimestamp(ms / 1000, tz=_UTC).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            normalized = None
    elif len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
        normalized = raw[:10]
    if normalized is None:
        logger.warning("update_in_ace: unparseable closedate %r; skipping", raw)
        return False
    life_cycle = dict(payload.get("LifeCycle") or {})
    life_cycle["TargetCloseDate"] = normalized
    payload["LifeCycle"] = life_cycle
    return True


def _handle_dealname(payload: dict[str, Any], value: Any, _: str) -> bool:
    # CustomerBusinessProblem is intentionally NOT derived from dealname
    # here. The create path sets a valid >= 20 char CustomerBusinessProblem
    # from the deal description; that's the authoritative source.
    project = dict(payload.get("Project") or {})
    project["Title"] = str(value)[:255]
    payload["Project"] = project
    return True


def _handle_description(payload: dict[str, Any], value: Any, _: str) -> bool:
    # CustomerBusinessProblem has a server-side regex (?s).{20,2000}.
    # If the new description is below the minimum, pad with the existing
    # project title (mirrors the create-path behavior). If neither is long
    # enough, skip rather than write a value AWS will reject.
    project = dict(payload.get("Project") or {})
    text = str(value)[:2000]
    if len(text) < 20:
        title = str(project.get("Title") or "")[:200]
        text = f"{title}: {text}"[:2000] if title else text
    if len(text) < 20:
        return False
    project["CustomerBusinessProblem"] = text
    payload["Project"] = project
    return True


def _handle_use_case(payload: dict[str, Any], value: Any, _: str) -> bool:
    project = dict(payload.get("Project") or {})
    project["CustomerUseCase"] = str(value)
    payload["Project"] = project
    return True


def _project_text_setter(aws_field: str, *, max_length: int | None = None):
    """Build a handler that sets/clears a free-text Project.<aws_field>.

    Empty / whitespace-only values clear the field (drop the key from the
    payload). AWS UpdateOpportunity rejects empty strings on most
    regex-validated fields, so dropping is the correct "clear" semantics
    under PUT.
    """

    def _handler(payload: dict[str, Any], value: Any, _: str) -> bool:
        text = str(value).strip() if value is not None else ""
        project = dict(payload.get("Project") or {})
        if text:
            project[aws_field] = text[:max_length] if max_length else text
        else:
            project.pop(aws_field, None)
        payload["Project"] = project
        return True

    return _handler


def _life_cycle_text_setter(aws_field: str, *, max_length: int | None = None):
    """Build a handler that sets/clears a free-text LifeCycle.<aws_field>."""

    def _handler(payload: dict[str, Any], value: Any, _: str) -> bool:
        text = str(value).strip() if value is not None else ""
        life_cycle = dict(payload.get("LifeCycle") or {})
        if text:
            life_cycle[aws_field] = text[:max_length] if max_length else text
        else:
            life_cycle.pop(aws_field, None)
        payload["LifeCycle"] = life_cycle
        return True

    return _handler


def _handle_aws_account_id(payload: dict[str, Any], value: Any, _: str) -> bool:
    # AWS account id must be 12 digits if present; otherwise we clear.
    text = str(value).strip() if value is not None else ""
    project = dict(payload.get("Project") or {})
    if text and text.isdigit() and len(text) == 12:
        project["CustomerAwsAccountId"] = text
    else:
        project.pop("CustomerAwsAccountId", None)
    payload["Project"] = project
    return True


# Marketing-block property -> AWS field. Used by both the dispatch table
# (one handler row per HubSpot property) and the test suite.
_MARKETING_PROP_TO_FIELD: dict[str, str] = {
    "govwin_ace_marketing_source": "Source",
    "govwin_ace_marketing_campaign_name": "CampaignName",
    "govwin_ace_marketing_use_cases": "UseCases",
    "govwin_ace_marketing_channel": "Channels",
    "govwin_ace_marketing_dev_funded": "AwsFundingUsed",
}


def _make_marketing_handler(aws_field: str):
    """Build a handler for one Marketing.<aws_field> property.

    All marketing-block edits flow through the same final block so the AWS
    rules stay consistent:
      (1) Marketing.Source is REQUIRED on every UpdateOpportunity (2026-05+).
      (2) Companion fields are rejected when Source is not "Marketing Activity".
    """

    def _handler(payload: dict[str, Any], value: Any, _: str) -> bool:
        text = str(value).strip() if value is not None else ""
        marketing = dict(payload.get("Marketing") or {})
        if aws_field in ("UseCases", "Channels"):
            # HubSpot stores multi-select enum properties as ";"-joined
            # strings ("Email;Live Event"). Split before sending; earlier
            # we were wrapping the raw string as ``[text]`` which sent
            # ``Channels=["Email;Live Event"]`` to AWS and failed the
            # enum validator.
            entries = [v.strip() for v in text.split(";") if v.strip()] if text else []
            if entries:
                marketing[aws_field] = entries
            else:
                marketing.pop(aws_field, None)
        elif text:
            marketing[aws_field] = text
        else:
            marketing.pop(aws_field, None)
        source = marketing.get("Source")
        if source in (None, "", "None"):
            payload["Marketing"] = {"Source": "None"}
        elif source != "Marketing Activity":
            payload["Marketing"] = {"Source": source}
        else:
            payload["Marketing"] = marketing
        return True

    return _handler


def _handle_lifecycle_stage(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    life_cycle = dict(payload.get("LifeCycle") or {})
    if text:
        life_cycle["Stage"] = text
    else:
        life_cycle.pop("Stage", None)
    # If walking BACK from Closed Lost, AWS rejects the lingering
    # ClosedLostReason; drop it so the payload validates.
    if text != "Closed Lost":
        life_cycle.pop("ClosedLostReason", None)
    payload["LifeCycle"] = life_cycle
    return True


def _handle_closed_lost_reason(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    life_cycle = dict(payload.get("LifeCycle") or {})
    if text:
        life_cycle["ClosedLostReason"] = text
    else:
        life_cycle.pop("ClosedLostReason", None)
    payload["LifeCycle"] = life_cycle
    return True


def _handle_partner_need(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    if not text:
        # Multi-value clears are not currently sent to AWS: list-valued
        # AWS fields don't accept empty arrays via UpdateOpportunity in
        # this client, so an empty incoming list is a no-op rather than
        # a clear. Log so the no-op is visible in CloudWatch (CR3).
        logger.warning(
            "update_in_ace: empty value for govwin_ace_partner_need; "
            "no-op (clearing multi-value AWS fields is not supported on this path)"
        )
        return False
    payload["PrimaryNeedsFromAws"] = [v.strip() for v in text.split(";") if v.strip()]
    return True


def _handle_delivery_model(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    if not text:
        logger.warning(
            "update_in_ace: empty value for govwin_ace_delivery_model; "
            "no-op (clearing multi-value AWS fields is not supported on this path)"
        )
        return False
    project = dict(payload.get("Project") or {})
    project["DeliveryModels"] = [v.strip() for v in text.split(";") if v.strip()]
    payload["Project"] = project
    return True


def _handle_sales_activities(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    if not text:
        logger.warning(
            "update_in_ace: empty value for govwin_ace_sales_activities; "
            "no-op (clearing multi-value AWS fields is not supported on this path)"
        )
        return False
    project = dict(payload.get("Project") or {})
    project["SalesActivities"] = [v.strip() for v in text.split(";") if v.strip()]
    payload["Project"] = project
    return True


def _handle_national_security(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    if text not in ("Yes", "No"):
        return False
    payload["NationalSecurity"] = text
    return True


def _handle_opportunity_type(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    if not text:
        return False
    from src.ace.mapper import ALLOWED_OPPORTUNITY_TYPES

    if text not in ALLOWED_OPPORTUNITY_TYPES:
        return False
    payload["OpportunityType"] = text
    return True


def _handle_industry(payload: dict[str, Any], value: Any, _: str) -> bool:
    text = str(value).strip() if value is not None else ""
    if not text:
        return False
    # Route through the create-path normalizer so Industry + OtherIndustry
    # stay in sync. Without popping OtherIndustry when the new Industry is
    # a closed-enum value, AWS rejects with ValidationException.
    from src.ace.mapper import normalize_industry

    cust = dict(payload.get("Customer") or {})
    account = dict(cust.get("Account") or {})
    industry_enum, other = normalize_industry(text)
    account["Industry"] = industry_enum
    if other:
        account["OtherIndustry"] = other
    else:
        account.pop("OtherIndustry", None)
    cust["Account"] = account
    payload["Customer"] = cust
    return True


# Dispatch table: HubSpot property name -> handler callable.
# Note: ``govwin_ace_aws_products`` and ``govwin_ace_solution_id`` are NOT in
# this map. Those flow through Associate/Disassociate calls (see
# ``_handle_aws_products_diff`` / ``_handle_solution_diff``) rather than
# UpdateOpportunity; ``_process_event`` short-circuits to those handlers
# before calling ``_apply_delta``.
_DeltaHandler = "Callable[[dict[str, Any], Any, str], bool]"
_DELTA_HANDLERS: dict[str, Any] = {
    "amount": _handle_amount,
    "closedate": _handle_closedate,
    "dealname": _handle_dealname,
    "description": _handle_description,
    "govwin_ace_use_case": _handle_use_case,
    "govwin_ace_competitor_name": _project_text_setter("CompetitorName", max_length=255),
    "govwin_ace_additional_comments": _project_text_setter("AdditionalComments", max_length=255),
    "govwin_ace_aws_account_id": _handle_aws_account_id,
    "govwin_ace_next_steps": _life_cycle_text_setter("NextSteps", max_length=255),
    "govwin_ace_related_opportunity_id": _project_text_setter("RelatedOpportunityIdentifier"),
    "govwin_ace_lifecycle_stage": _handle_lifecycle_stage,
    "govwin_ace_closed_lost_reason": _handle_closed_lost_reason,
    "govwin_ace_partner_need": _handle_partner_need,
    "govwin_ace_delivery_model": _handle_delivery_model,
    "govwin_ace_sales_activities": _handle_sales_activities,
    "govwin_ace_national_security": _handle_national_security,
    "govwin_ace_opportunity_type": _handle_opportunity_type,
    "govwin_industry": _handle_industry,
    **{prop: _make_marketing_handler(field) for prop, field in _MARKETING_PROP_TO_FIELD.items()},
}


def _apply_delta(
    payload: dict[str, Any],
    prop: str,
    value: Any,
    partner_company_name: str = "Partner Company",
) -> bool:
    """Mutate ``payload`` (an UpdateOpportunity body) for one property change.

    AWS UpdateOpportunity has PUT semantics; the caller has already fetched
    and scrubbed the current opportunity into ``payload``. This function
    only edits the field that changed. Returns True if the delta was
    applied; False to skip (irrelevant property, empty value, or invalid
    enum value).

    Dispatches to a per-property handler in ``_DELTA_HANDLERS``.
    """
    if value is None or value == "":
        return False
    handler = _DELTA_HANDLERS.get(prop)
    if handler is None:
        return False
    return bool(handler(payload, value, partner_company_name))


def _resolve_govwin_id(state: SyncStateManager, hubspot: HubSpotClient, deal_id: str) -> str | None:
    """Find the GovWin id for a HubSpot deal, preferring the ACE mapping."""
    direct = state.find_govwin_by_hubspot_deal_id(deal_id)
    if direct:
        return direct
    try:
        deal = hubspot.get_deal(deal_id, properties=["govwin_opp_id", "govwin_iq_opp_id"])
    except Exception:  # noqa: BLE001 -- best-effort fallback
        logger.exception("update_in_ace: get_deal %s failed", deal_id)
        return None
    properties = deal.get("properties") or {}
    govwin_id = properties.get("govwin_opp_id") or properties.get("govwin_iq_opp_id")
    return str(govwin_id) if govwin_id else None


# HubSpot webhooks include the changed property value in propertyValue,
# but for long string fields the value can be truncated by HubSpot. For
# these we ignore propertyValue and re-fetch the full deal record.
_REFETCH_FROM_HUBSPOT_PROPERTIES: frozenset[str] = frozenset(
    {
        "description",
        "dealname",
        "govwin_ace_additional_comments",
        "govwin_ace_next_steps",
    }
)


def _resolve_property_value(
    hs_event: dict[str, Any],
    hubspot: HubSpotClient,
    deal_id: str,
    prop: str,
) -> Any:
    """Return the authoritative value for the changed property.

    For free-text properties HubSpot may truncate ``propertyValue`` in the
    webhook delivery, so we fetch the deal directly. For other properties
    (enums, dates, numbers) the webhook value is authoritative.
    """
    if prop in _REFETCH_FROM_HUBSPOT_PROPERTIES:
        try:
            deal = hubspot.get_deal(deal_id, properties=[prop])
        except Exception:  # noqa: BLE001 -- best effort, fall back to webhook value
            logger.exception("update_in_ace: get_deal %s failed; using webhook value", deal_id)
            return hs_event.get("propertyValue")
        return (deal.get("properties") or {}).get(prop) or hs_event.get("propertyValue")
    return hs_event.get("propertyValue")


def _handle_aws_products_diff(
    *,
    ace: ACEClient,
    ace_id: str,
    deal_id: str,
    hubspot: HubSpotClient,
) -> dict[str, Any]:
    """Diff requested AWS Products against AWS-side current set; Associate/Disassociate.

    The AWS Products association list is not part of the UpdateOpportunity
    input shape; it's managed by Associate/Disassociate calls. The set BD
    wants is on the deal as ``govwin_ace_aws_products`` (";"-joined). The
    current AWS-side set comes from GetOpportunity's
    ``RelatedEntityIdentifiers.AwsProducts`` field. Diff and apply.

    Partial failures (one product invalid for the catalog) log a WARNING
    but do not raise: the opp itself is in a consistent state with the
    products that DID associate. Same pattern as submit_to_ace.
    """
    deal = hubspot.get_deal(deal_id, properties=["govwin_ace_aws_products"])
    requested_raw = (deal.get("properties") or {}).get("govwin_ace_aws_products") or ""
    requested = [v.strip() for v in requested_raw.split(";") if v.strip() and v.strip() != "Other"]
    current_full = ace.get_opportunity(ace_id)
    current = list((current_full.get("RelatedEntityIdentifiers") or {}).get("AwsProducts") or [])
    cur_set = {p for p in current if p and p != "Other"}
    req_set = set(requested)
    to_associate = [p for p in requested if p in (req_set - cur_set)]
    to_disassociate = sorted(cur_set - req_set)
    associated: list[str] = []
    disassociated: list[str] = []
    failures: list[str] = []
    for product_id in to_associate:
        try:
            ace.associate_opportunity(
                opportunity_identifier=ace_id,
                related_entity_identifier=product_id,
                related_entity_type="AwsProducts",
            )
            associated.append(product_id)
        except ACEAPIError as exc:
            if exc.code == "ConflictException":
                continue
            logger.warning(
                "update_in_ace.products: associate %s on opp=%s failed: %s",
                product_id,
                ace_id,
                exc,
            )
            failures.append(f"associate {product_id}: {exc.code}")
    for product_id in to_disassociate:
        try:
            ace.disassociate_opportunity(
                opportunity_identifier=ace_id,
                related_entity_identifier=product_id,
                related_entity_type="AwsProducts",
            )
            disassociated.append(product_id)
        except ACEAPIError as exc:
            logger.warning(
                "update_in_ace.products: disassociate %s on opp=%s failed: %s",
                product_id,
                ace_id,
                exc,
            )
            failures.append(f"disassociate {product_id}: {exc.code}")
    return {
        "status": "updated",
        "ace_opportunity_id": ace_id,
        "products_associated": associated,
        "products_disassociated": disassociated,
        "failures": failures,
    }


def _handle_solution_diff(
    *,
    ace: ACEClient,
    ace_id: str,
    deal_id: str,
    hubspot: HubSpotClient,
) -> dict[str, Any]:
    """Diff requested Solution id against AWS-side current; Associate/Disassociate.

    Mirrors _handle_aws_products_diff but for the singular Solution
    association (AWS convention: one Solution per opportunity). The set
    BD wants is on the deal as ``govwin_ace_solution_id`` (single value,
    may be empty). Current AWS-side value comes from
    GetOpportunity's ``RelatedEntityIdentifiers.Solutions[0]``.

    Partial failures log a WARNING and surface in the return dict so the
    outer handler can SNS-alert if needed.
    """
    deal = hubspot.get_deal(deal_id, properties=["govwin_ace_solution_id"])
    requested = (deal.get("properties") or {}).get("govwin_ace_solution_id") or ""
    requested = requested.strip()
    current_full = ace.get_opportunity(ace_id)
    current_list = list((current_full.get("RelatedEntityIdentifiers") or {}).get("Solutions") or [])
    current = current_list[0] if current_list else ""
    associated: list[str] = []
    disassociated: list[str] = []
    failures: list[str] = []
    if requested == current:
        return {
            "status": "updated",
            "ace_opportunity_id": ace_id,
            "solution_associated": "",
            "solution_disassociated": "",
        }
    # Disassociate the old one first (AWS allows only one Solution).
    if current:
        try:
            ace.disassociate_opportunity(
                opportunity_identifier=ace_id,
                related_entity_identifier=current,
                related_entity_type="Solutions",
            )
            disassociated.append(current)
        except ACEAPIError as exc:
            logger.warning(
                "update_in_ace.solution: disassociate %s on opp=%s failed: %s",
                current,
                ace_id,
                exc,
            )
            failures.append(f"disassociate {current}: {exc.code}")
    if requested:
        try:
            ace.associate_opportunity(
                opportunity_identifier=ace_id,
                related_entity_identifier=requested,
                related_entity_type="Solutions",
            )
            associated.append(requested)
        except ACEAPIError as exc:
            if exc.code != "ConflictException":
                logger.warning(
                    "update_in_ace.solution: associate %s on opp=%s failed: %s",
                    requested,
                    ace_id,
                    exc,
                )
                failures.append(f"associate {requested}: {exc.code}")
    return {
        "status": "updated",
        "ace_opportunity_id": ace_id,
        "solution_associated": associated[0] if associated else "",
        "solution_disassociated": disassociated[0] if disassociated else "",
        "failures": failures,
    }


def _process_event(
    hs_event: dict[str, Any],
    *,
    config: Any,
    state: SyncStateManager,
    ace: ACEClient,
    hubspot: HubSpotClient,
) -> dict[str, Any]:
    raw_deal_id = str(hs_event.get("objectId") or "")
    if not is_valid_hubspot_object_id(raw_deal_id):
        return {"status": "skipped", "reason": "invalid objectId"}
    deal_id = raw_deal_id

    prop = hs_event.get("propertyName")
    if not prop:
        return {"status": "skipped", "reason": "no propertyName"}

    govwin_id = _resolve_govwin_id(state, hubspot, deal_id)
    if not govwin_id:
        logger.warning(
            "update_in_ace: skipping deal=%s prop=%s; deal has no govwin_opp_id "
            "and no reverse-index entry; the deal is not part of this integration",
            deal_id,
            prop,
        )
        return {"status": "skipped", "reason": "no govwin mapping"}

    mapping = state.get_ace_mapping(govwin_id) or {}
    ace_id = mapping.get("ace_opportunity_id")
    if not ace_id:
        # DDB cache miss for opportunity_id. Self-heal: read the AWS opp
        # id off the HubSpot deal directly (govwin_aws_cosell_id is
        # written by submit_to_ace + handle_ace_event whenever the AWS
        # side acks the creation). If the deal carries one, backfill
        # the DDB cache and proceed; if not, the opp truly doesn't exist
        # yet (the user is trying to update before create completed --
        # legitimate "skipped" case).
        #
        # Refuse the self-heal if the DDB ACE# row was previously bound
        # to a DIFFERENT hubspot_deal_id. That partial-row state means
        # the mapping is associated with another deal; backfilling here
        # would silently redirect updates between deals (see security
        # review H1).
        existing_deal_id = str(mapping.get("hubspot_deal_id") or "")
        if existing_deal_id and existing_deal_id != deal_id:
            logger.warning(
                "update_in_ace: self-heal refused; govwin=%s mapping is already "
                "bound to a different deal=%s (incoming deal=%s); refusing to "
                "rebind via the BD-editable govwin_aws_cosell_id property",
                govwin_id,
                existing_deal_id,
                deal_id,
            )
            try:
                _publish_update_error_alert(
                    config=config,
                    deal_id=deal_id,
                    prop=str(prop),
                    error=(
                        f"Self-heal refused: govwin_opp_id={govwin_id} is already "
                        f"bound to deal {existing_deal_id} in DynamoDB; this "
                        f"event came from deal {deal_id}. A deal property edit "
                        f"may be attempting to rebind across deals."
                    ),
                )
            except Exception:  # noqa: BLE001 -- best-effort
                logger.exception("update_in_ace: SNS publish for cross-deal rebind refusal failed")
            return {"status": "skipped", "reason": "cross-deal rebind refused"}
        try:
            deal = hubspot.get_deal(deal_id, properties=["govwin_aws_cosell_id"])
            ace_id = (deal.get("properties") or {}).get("govwin_aws_cosell_id")
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception("update_in_ace: get_deal self-heal failed for deal=%s", deal_id)
            ace_id = None
        if not ace_id:
            return {"status": "skipped", "reason": "no ace mapping yet"}
        # Trust verification before mutating. ``govwin_aws_cosell_id`` is a
        # BD-editable HubSpot property; without this check, a hand-edited
        # value could redirect this Lambda's UpdateOpportunity to a
        # different (potentially foreign-partner) AWS opportunity. Verify
        # AWS's PartnerOpportunityIdentifier matches the govwin_id we
        # resolved before persisting the cache and proceeding.
        try:
            verify = ace.get_opportunity(str(ace_id))
        except ACEAPIError as exc:
            logger.warning(
                "update_in_ace: self-heal verify GetOpportunity(%s) failed: %s",
                ace_id,
                exc,
            )
            return {"status": "skipped", "reason": "self-heal verify failed"}
        verify_partner_id = str(verify.get("PartnerOpportunityIdentifier") or "")
        if verify_partner_id != govwin_id:
            logger.warning(
                "update_in_ace: self-heal mismatch; deal carries opp=%s but its "
                "PartnerOpportunityIdentifier=%r does not equal expected govwin=%s; "
                "refusing to mutate",
                ace_id,
                verify_partner_id,
                govwin_id,
            )
            try:
                _publish_update_error_alert(
                    config=config,
                    deal_id=deal_id,
                    prop=str(prop),
                    error=(
                        f"Self-heal mismatch: HubSpot deal points at AWS opp {ace_id} "
                        f"but its PartnerOpportunityIdentifier={verify_partner_id!r} "
                        f"does not match expected govwin_opp_id={govwin_id}. "
                        "Manual reconciliation needed."
                    ),
                )
            except Exception:  # noqa: BLE001 -- best-effort
                logger.exception("update_in_ace: SNS publish for self-heal mismatch failed")
            return {"status": "skipped", "reason": "self-heal verification failed"}
        logger.info(
            "update_in_ace: self-heal; backfilling ace_opportunity_id=%s "
            "for govwin=%s from HubSpot deal property (verified)",
            ace_id,
            govwin_id,
        )
        try:
            state.update_ace_mapping(
                govwin_id=govwin_id,
                ace_opportunity_id=str(ace_id),
                hubspot_deal_id=deal_id,
            )
        except Exception:  # noqa: BLE001 -- best-effort backfill
            logger.exception(
                "update_in_ace: DDB cache backfill failed for govwin=%s; "
                "continuing with the recovered id",
                govwin_id,
            )

    # Products are handled via AssociateOpportunity / DisassociateOpportunity,
    # not UpdateOpportunity. Diff the requested set (from the deal) against
    # the AWS-side current set (from GetOpportunity.RelatedEntityIdentifiers)
    # and fire one Associate or Disassociate per delta. We short-circuit
    # before the UpdateOpportunity payload build because products are not
    # in the UpdateOpportunity input shape.
    if prop == "govwin_ace_aws_products":
        return _handle_aws_products_diff(
            ace=ace, ace_id=str(ace_id), deal_id=deal_id, hubspot=hubspot
        )
    if prop == "govwin_ace_solution_id":
        return _handle_solution_diff(ace=ace, ace_id=str(ace_id), deal_id=deal_id, hubspot=hubspot)

    # AWS UpdateOpportunity has PUT semantics: any field omitted from the
    # request is treated as being cleared. Fetch the current opportunity,
    # whitelist to the subset of fields UpdateOpportunity accepts, and
    # apply only the property-change delta on top.
    current = ace.get_opportunity(str(ace_id))
    payload = ACEClient.scrub_for_update(current)
    value = _resolve_property_value(hs_event, hubspot, deal_id, str(prop))
    if not _apply_delta(payload, str(prop), value, config.ace.partner_company_name):
        return {"status": "skipped", "reason": f"no relevant field for {prop}"}

    # Closed-Lost stage/reason companion read.
    #
    # HubSpot emits one property-change event per changed field. When BD
    # saves a form that sets both lifecycle_stage="Closed Lost" AND
    # lifecycle_closed_lost_reason="Price" in one submit, two webhooks
    # arrive in undefined order:
    #   - Stage-first: payload sets Stage="Closed Lost" but
    #     ClosedLostReason isn't in scrubbed AWS state yet -> AWS rejects
    #     ValidationException -> permanent error + SNS alert; the second
    #     webhook (Reason) eventually arrives but Stage is no longer in
    #     the payload, so AWS never gets the Closed-Lost transition.
    #   - Reason-first: works correctly because Stage doesn't require Reason.
    #
    # Fix: whenever the post-_apply_delta payload has Stage="Closed Lost"
    # without a Reason (or vice-versa), backfill the companion from the
    # HubSpot deal so this UpdateOpportunity carries both. The deal is
    # the source of truth for what BD actually wanted.
    _ensure_closed_lost_pair_consistency(payload, hubspot, deal_id)

    response = ace.update_with_retry(
        identifier=str(ace_id),
        updates=payload,
        known_last_modified_date=current.get("LastModifiedDate"),
    )
    state.update_ace_mapping(
        govwin_id=govwin_id,
        last_modified_date=str(response.get("LastModifiedDate"))
        if response.get("LastModifiedDate")
        else None,
        hubspot_deal_id=deal_id,
    )
    return {"status": "updated", "ace_opportunity_id": str(ace_id)}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    config = load_config()
    # Pre-warm SNS for the permanent-error + self-heal-mismatch alert
    # paths; first publish after cold start would otherwise add ~5s to
    # the error feedback to BD.
    from src.alerts import ensure_sns_client

    ensure_sns_client(config.aws.region)
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
                logger.warning("update_in_ace: invalid JSON in message %s", message_id)
                # Permanent error: do not retry.
                continue
            try:
                results.append(
                    _process_event(hs_event, config=config, state=state, ace=ace, hubspot=hubspot)
                )
            except ACEAPIError as exc:
                if exc.code in _PERMANENT_ERROR_CODES:
                    deal_id = str((hs_event or {}).get("objectId") or "?")
                    prop = str((hs_event or {}).get("propertyName") or "?")
                    logger.warning(
                        "update_in_ace: permanent error %s for message %s prop=%s; dropping",
                        exc.code,
                        message_id,
                        prop,
                    )
                    # Surface the rejection so AWS state divergence isn't
                    # silent. Two channels:
                    #   1. SNS to the on-call inbox so someone notices.
                    #   2. HubSpot writeback to a deal field so BD sees the
                    #      reason on the deal record next time they open it.
                    try:
                        _publish_update_error_alert(
                            config=config,
                            deal_id=deal_id,
                            prop=prop,
                            error=f"AWS {exc.code}: {exc}",
                        )
                    except Exception:  # noqa: BLE001 -- best-effort
                        logger.exception("update_in_ace: SNS publish for permanent error failed")
                    if is_valid_hubspot_object_id(deal_id):
                        from src.hubspot.client import _redact_hubspot_error_body

                        redacted = _redact_hubspot_error_body(str(exc))
                        try:
                            hubspot.update_deal(
                                deal_id,
                                {
                                    "govwin_ace_next_steps": (
                                        f"AWS rejected UpdateOpportunity ({exc.code}) "
                                        f"for property '{prop}': {redacted[:1400]}"
                                    ),
                                },
                            )
                        except Exception:  # noqa: BLE001 -- writeback is best-effort
                            logger.exception(
                                "update_in_ace: HubSpot writeback failed for deal %s",
                                deal_id,
                            )
                    continue
                logger.warning(
                    "update_in_ace: transient %s for message %s; retrying via SQS",
                    exc.code,
                    message_id,
                )
                failures.append({"itemIdentifier": message_id})
            except Exception:  # noqa: BLE001 -- batch-failure path
                logger.exception("update_in_ace failed for message %s", message_id)
                failures.append({"itemIdentifier": message_id})

    return {"results": results, "batchItemFailures": failures}
