"""React to inbound EventBridge events from ``aws.partnercentral-selling``.

Per the AWS reference, event payloads carry only IDs in ``detail.opportunity``
and ``detail.engagementInvitation``. The handler resolves those to the local
HubSpot deal via ``PartnerOpportunityIdentifier`` (which our CreateOpportunity
populated with the GovWin opp ID) and updates the deal stage accordingly.

Idempotency: each event id is recorded atomically via
``mark_event_seen_atomic`` on first sighting; concurrent retries see the
existing record and return early.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.config import load_config
from src.hubspot.client import HubSpotClient
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# AWS Partner Central opportunity-id format (O followed by digits / dashes).
# Defense-in-depth on the EventBridge -> HubSpot write-back path.
_AWS_OPP_ID_PATTERN = re.compile(r"^O[A-Z0-9-]{1,99}$")


# Keys must match boto3 LifeCycle.ReviewStatus casing exactly; in
# particular 'In review' is lowercase-r. Statuses not in the map are
# intentionally ignored ('Pending Submission' fires on every Create but
# doesn't move the deal stage).


_DEALSTAGE_BY_AWS_REVIEW: dict[str, str] = {
    "Submitted": "Submitted to AWS",
    "In review": "Under AWS Review",
    "Approved": "Approved by AWS",
    "Action Required": "Action Required",
    "Rejected": "Closed Lost",
    "Expired": "Closed Lost",
}


def _publish_orphan_alert(
    *, config: Any, aws_opp_id: str, partner_opp_id: str | None, reason: str
) -> None:
    """Thin wrapper that builds the message + delegates to src.alerts.

    See src.alerts.publish_alert for the actual SNS publish path and
    error-detail redaction.
    """
    from src.alerts import publish_alert

    subject = f"ACE orphan opportunity (no HubSpot deal): {aws_opp_id}"
    message = (
        "AWS Partner Central emitted an event for an opportunity that does "
        "not correspond to any HubSpot deal we can find.\n\n"
        f"AWS opportunity id: {aws_opp_id}\n"
        f"Partner opportunity identifier on AWS: {partner_opp_id or '(missing)'}\n"
        f"Catalog: {config.ace.catalog}\n"
        f"Reason: {reason}\n\n"
        "Either claim this opportunity into HubSpot by creating a deal with "
        "govwin_opp_id set to the partner identifier above, or close the "
        "opportunity on the AWS side if it was created in error."
    )
    publish_alert(
        config=config,
        subject=subject,
        message=message,
    )


def _resolve_or_heal_deal_id(
    *,
    state: SyncStateManager,
    hubspot: HubSpotClient,
    partner_id: str,
) -> tuple[str | None, str | None]:
    """Find the HubSpot deal id bound to a partner opportunity identifier.

    First checks DDB (the fast path). On cache miss, falls back to HubSpot
    search by ``govwin_opp_id``. If the search finds a deal, backfill DDB
    so future events for this opp skip the search round-trip.

    Returns ``(deal_id, heal_reason)``. ``heal_reason`` is None on direct
    hit; ``"backfilled-from-hubspot"`` when we recovered via search;
    ``"genuine-orphan"`` when neither DDB nor HubSpot knows about this id.
    """
    mapping = state.get_ace_mapping(partner_id) or {}
    deal_id = mapping.get("hubspot_deal_id")
    if deal_id:
        return str(deal_id), None
    try:
        deal = hubspot.search_deal_by_govwin_id(partner_id)
    except Exception:  # noqa: BLE001 -- defensive, lookup is best-effort
        logger.exception(
            "handle_ace_event: HubSpot search failed for govwin_opp_id=%s; treating as orphan",
            partner_id,
        )
        return None, "hubspot-search-failed"
    if not deal:
        return None, "genuine-orphan"
    found_deal_id = str(deal.get("id") or "")
    if not found_deal_id:
        return None, "genuine-orphan"
    logger.info(
        "handle_ace_event: self-heal; backfilling DDB mapping for govwin=%s deal=%s",
        partner_id,
        found_deal_id,
    )
    try:
        state.update_ace_mapping(
            govwin_id=partner_id,
            hubspot_deal_id=found_deal_id,
        )
    except Exception:  # noqa: BLE001 -- best-effort backfill
        logger.exception(
            "handle_ace_event: DDB backfill failed for govwin=%s; processing will continue",
            partner_id,
        )
    return found_deal_id, "backfilled-from-hubspot"


def _update_hubspot_stage(
    hubspot: HubSpotClient, deal_id: str, target_label: str
) -> tuple[bool, str | None]:
    """Resolve a stage label to its pipeline ID and patch the deal.

    Returns ``(success, reason)``. ``success`` is True when the deal was
    patched, False when the call was a deliberate no-op (stage label not in
    the configured pipeline, or deal is archived in HubSpot). Archived deals
    are an expected end-state; BD has dispositioned the opp in HubSpot --
    and must not fire SNS alerts.
    """
    stage_id = hubspot.get_stage_id_by_label(target_label)
    if not stage_id:
        logger.warning(
            "handle_ace_event: stage label %r not in pipeline; skipping update of %s",
            target_label,
            deal_id,
        )
        return False, "stage label not in pipeline"
    # Cheap pre-flight: avoid update_deal on an archived deal so a stale
    # AWS-side EventBridge event doesn't surface as a 404 / SNS alert.
    if hubspot.is_deal_archived(deal_id):
        logger.info(
            "handle_ace_event: deal %s is archived in HubSpot; skipping stage update",
            deal_id,
        )
        return False, "deal archived in HubSpot"
    hubspot.update_deal(deal_id, {"dealstage": stage_id})
    return True, None


def _handle_opportunity_event(
    detail: dict[str, Any],
    *,
    state: SyncStateManager,
    ace: ACEClient,
    hubspot: HubSpotClient,
) -> dict[str, Any]:
    """Opportunity Created / Updated.

    Detail carries only ``opportunity.identifier`` (the AWS Id). We must
    GetOpportunity to recover ``PartnerOpportunityIdentifier`` (our GovWin
    id) and the current ``LifeCycle.ReviewStatus``.
    """
    opp = detail.get("opportunity") or {}
    aws_id = opp.get("identifier")
    if not aws_id:
        return {"status": "skipped", "reason": "no opportunity.identifier"}
    try:
        full = ace.get_opportunity(str(aws_id))
    except ACEAPIError as exc:
        logger.warning("get_opportunity %s failed: %s", aws_id, exc)
        return {"status": "skipped", "reason": f"get_opportunity {exc.code}"}

    partner_id = full.get("PartnerOpportunityIdentifier")
    if not partner_id:
        # No partner id on the opportunity means we have nothing to match
        # against. Real orphan: AWS opp exists with no cross-reference.
        config = load_config()
        _publish_orphan_alert(
            config=config,
            aws_opp_id=str(aws_id),
            partner_opp_id=None,
            reason="AWS opportunity has no PartnerOpportunityIdentifier; cannot map.",
        )
        return {"status": "skipped", "reason": "no PartnerOpportunityIdentifier on opportunity"}
    partner_id_str = str(partner_id)
    deal_id, heal_reason = _resolve_or_heal_deal_id(
        state=state, hubspot=hubspot, partner_id=partner_id_str
    )
    if not deal_id:
        # Self-heal exhausted; this is a real orphan. Alert once via SNS.
        config = load_config()
        _publish_orphan_alert(
            config=config,
            aws_opp_id=str(aws_id),
            partner_opp_id=partner_id_str,
            reason=heal_reason or "no HubSpot deal carries this govwin_opp_id",
        )
        return {
            "status": "skipped",
            "reason": f"orphan-after-self-heal ({heal_reason})",
        }
    if heal_reason:
        logger.info(
            "handle_ace_event: opportunity event self-healed via %s for govwin=%s deal=%s",
            heal_reason,
            partner_id_str,
            deal_id,
        )

    review_status = str((full.get("LifeCycle") or {}).get("ReviewStatus") or "")

    # One archive check up front; both write-back and stage update consume
    # the cached result. Treats transient HubSpot errors as "fail closed":
    # if we cannot determine the archive state, skip both writes rather
    # than risk overwriting an archived record (which would cause a
    # spurious 404 alert) or skip a real stage update.
    try:
        deal_archived = hubspot.is_deal_archived(str(deal_id))
    except Exception:  # noqa: BLE001 -- defensive
        logger.exception(
            "handle_ace_event: is_deal_archived failed for %s; failing closed",
            deal_id,
        )
        return {"status": "skipped", "reason": "archive check failed"}
    if deal_archived:
        return {"status": "skipped", "reason": "deal archived in HubSpot"}

    # Build the unified write-back: cosell id / status / score (always)
    # plus dealstage (when the ReviewStatus maps to a known stage label).
    # One PATCH per event is half the API budget and atomic from
    # HubSpot's perspective.
    aws_id_value = str(full.get("Id") or "")[:100]
    if aws_id_value and not _AWS_OPP_ID_PATTERN.match(aws_id_value):
        # Defense-in-depth: AWS shouldn't return a malformed Id, but bound
        # the write-back to the documented opportunity-id shape so a
        # contract drift can't write garbage to the HubSpot deal property.
        logger.warning(
            "handle_ace_event: AWS returned non-canonical Id %r; not writing",
            aws_id_value,
        )
        aws_id_value = ""

    writeback: dict[str, Any] = {}
    if aws_id_value:
        writeback["govwin_aws_cosell_id"] = aws_id_value
    if review_status:
        writeback["govwin_aws_cosell_status"] = review_status[:80]
    # Mirror the AWS-side LifeCycle.Stage onto the deal so the Update
    # form can pre-fill the stage dropdown to the current value. Without
    # this, the form falls back to a hard-coded "Qualified" default for
    # in-flight opportunities; BD opens the Update form on an Approved
    # opp and the dropdown shows Qualified, which would walk the stage
    # backward on submit.
    #
    # Only write when the value actually differs from the current deal
    # property. Earlier we wrote unconditionally, which fired a property-
    # change webhook -> update_in_ace -> GetOpportunity + UpdateOpportunity
    # (no-op against AWS state) on every inbound event. That burned write
    # quota and could trigger another inbound event loop.
    lifecycle_stage = str((full.get("LifeCycle") or {}).get("Stage") or "")[:80]
    if lifecycle_stage:
        try:
            current_deal = hubspot.get_deal(str(deal_id), properties=["govwin_ace_lifecycle_stage"])
            current_stage = (current_deal.get("properties") or {}).get("govwin_ace_lifecycle_stage")
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception(
                "handle_ace_event: get_deal for lifecycle_stage compare failed for %s; "
                "writing anyway",
                deal_id,
            )
            current_stage = None
        if current_stage != lifecycle_stage:
            writeback["govwin_ace_lifecycle_stage"] = lifecycle_stage

    # AWS-side product associations, mirrored to the deal so the card can
    # render a "syncing" pill whenever the BD-edited govwin_ace_aws_products
    # disagrees with AWS's current set. The card polls the deal; this is
    # the only signal it has during the async Associate/Disassociate
    # window after /ui-extension/update.
    aws_products = list((full.get("RelatedEntityIdentifiers") or {}).get("AwsProducts") or [])
    cosell_products = ";".join(sorted(p for p in aws_products if p and p != "Other"))
    writeback["govwin_aws_cosell_products"] = cosell_products[:1024]
    engagement_score = (full.get("AwsOpportunitySummary") or {}).get("MarketplaceEngagementScore")
    if engagement_score is not None:
        writeback["govwin_aws_marketplace_engagement_score"] = str(engagement_score)[:50]

    target_stage = _DEALSTAGE_BY_AWS_REVIEW.get(review_status)
    stage_label_id: str | None = None
    if target_stage:
        stage_label_id = hubspot.get_stage_id_by_label(target_stage)
        if stage_label_id:
            writeback["dealstage"] = stage_label_id

    if writeback:
        try:
            hubspot.update_deal(str(deal_id), writeback)
        except Exception:  # noqa: BLE001 -- write-back is best-effort
            logger.exception(
                "handle_ace_event: write-back PATCH failed for deal %s",
                deal_id,
            )

    if not target_stage:
        if review_status == "Pending Submission":
            return {
                "status": "no-op",
                "reason": "Pending Submission is informational; no HubSpot stage change",
            }
        return {
            "status": "skipped",
            "reason": f"no HubSpot stage label maps to ReviewStatus={review_status!r}",
        }
    if not stage_label_id:
        return {"status": "skipped", "reason": "stage label not in pipeline"}

    last_modified = full.get("LastModifiedDate")
    state.update_ace_mapping(
        govwin_id=str(partner_id),
        last_modified_date=str(last_modified) if last_modified else None,
    )
    return {"status": "updated", "deal_id": deal_id, "stage": target_stage}


# Domains we trust to identify AWS-side reviewers / PDMs. EngagementInvitation
# contacts whose email is outside this set are never persisted as HubSpot
# Contacts; AWS reviewers occasionally include customer-side contacts in
# invitation payloads, and a malicious / mistaken AWS-side actor could supply
# any string here. Forwarding those into HubSpot via upsert would clobber
# real customer contact records that share the email.
_HYPERSCALER_DOMAINS: frozenset[str] = frozenset({"amazon.com", "aws.com"})

# Marker used to distinguish Hyperscaler-Contact records this Lambda created
# from real customer contacts. Refusing to overwrite a contact that lacks
# this marker is the second layer of defense against the upsert-hijack
# vector: even if the domain check is somehow bypassed, the existing real
# contact survives because it doesn't carry the marker.
_HYPERSCALER_LEAD_STATUS = "HYPERSCALER_CONTACT"


def _mask_email(email: str) -> str:
    """Partial-mask an email for log lines so CloudWatch never carries the
    raw address. Federal-contractor compliance posture (NIST SI-12) treats
    PII in operational logs as a control gap.
    """
    if not email or "@" not in email:
        return "(unknown)"
    local, _, domain = email.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def _is_hyperscaler_email(email: str) -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in _HYPERSCALER_DOMAINS


def _create_hyperscaler_contacts(
    *,
    invitation: dict[str, Any],
    deal_id: str,
    company_id: str | None,
    hubspot: HubSpotClient,
) -> int:
    """Create HubSpot Contact records for AWS-side participants.

    When AWS publishes EngagementInvitation events, the
    invitation detail can include AWS reviewer / PDM contacts. We mirror
    them as HubSpot Contacts labeled "Hyperscaler Contact" and associate
    each one to the deal and (when known) the company.

    Three layers of defense against the upsert-hijack vector (an AWS-side
    payload supplying an email that matches a real HubSpot contact):

    1. Email domain must be in ``_HYPERSCALER_DOMAINS`` (amazon.com /
       aws.com). Outside that, skip with a masked log line.
    2. If the email already exists in HubSpot AND the existing record was
       not previously created by this Lambda (no ``hs_lead_status =
       HYPERSCALER_CONTACT`` marker), skip the upsert and only
       create the association. The real customer contact is never
       overwritten.
    3. Email is masked before any log line so CloudWatch never carries
       the raw value.

    Best-effort: contact creation failures are logged but never block stage
    updates.
    """
    aws_contacts = invitation.get("invitationContacts") or invitation.get("contacts") or []
    created = 0
    for c in aws_contacts:
        email = (c.get("email") or "").strip()
        first = (c.get("firstName") or c.get("first_name") or "").strip()
        last = (c.get("lastName") or c.get("last_name") or "").strip()
        if not email:
            continue
        if not _is_hyperscaler_email(email):
            logger.warning(
                "hyperscaler contact rejected: email domain not in allowlist (%s)",
                _mask_email(email),
            )
            continue
        try:
            existing = hubspot.find_contact_by_email(email)
            existing_props = (existing or {}).get("properties") or {}
            if existing and existing_props.get("hs_lead_status") != _HYPERSCALER_LEAD_STATUS:
                # Real customer contact; do not overwrite. Just associate
                # to the deal so the AWS reviewer linkage is visible.
                contact_id = str(existing.get("id") or "")
                if contact_id:
                    hubspot.associate_objects("contacts", contact_id, "deals", deal_id)
                    if company_id:
                        hubspot.associate_objects("contacts", contact_id, "companies", company_id)
                logger.info(
                    "hyperscaler contact: existing non-hyperscaler record skipped overwrite (%s)",
                    _mask_email(email),
                )
                continue
            response = hubspot.upsert_contact(
                {
                    "email": email,
                    "firstname": first,
                    "lastname": last,
                    "company": "AWS",
                    "jobtitle": c.get("businessTitle") or "AWS Partner Development Manager",
                    "lifecyclestage": "other",
                    "hs_lead_status": _HYPERSCALER_LEAD_STATUS,
                }
            )
            contact_id = str(response.get("id") or "")
            if contact_id:
                hubspot.associate_objects("contacts", contact_id, "deals", deal_id)
                if company_id:
                    hubspot.associate_objects("contacts", contact_id, "companies", company_id)
                created += 1
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception("hyperscaler contact upsert failed (%s)", _mask_email(email))
    return created


def _handle_invitation_event(
    detail_type: str,
    detail: dict[str, Any],
    *,
    state: SyncStateManager,
    ace: ACEClient,
    hubspot: HubSpotClient,
) -> dict[str, Any]:
    invitation = detail.get("engagementInvitation") or {}
    invitation_id = invitation.get("id")
    if not invitation_id:
        return {"status": "skipped", "reason": "no engagementInvitation.id"}

    if detail_type == "Engagement Invitation Created":
        # Receiver-side referrals from AWS: we don't auto-create the deal in
        # v1 (BD approval in HubSpot is required first). Notify via log; the
        # Phase 4 referral handler will pick this up.
        if invitation.get("participantType") == "Receiver":
            logger.info(
                "ace.invitation.created.receiver invitation_id=%s engagementId=%s",
                invitation_id,
                invitation.get("engagementId"),
            )
        # Sender-side: AWS may include the reviewer's contact info. Create
        # Hyperscaler Contact records for visibility.
        try:
            partner_id = state.find_govwin_by_invitation_id(str(invitation_id))
            mapping = state.get_ace_mapping(partner_id) if partner_id else None
            deal_id = (mapping or {}).get("hubspot_deal_id")
            if deal_id:
                company = hubspot.get_associated_company(str(deal_id))
                company_id = str(company.get("id")) if company else None
                _create_hyperscaler_contacts(
                    invitation=invitation,
                    deal_id=str(deal_id),
                    company_id=company_id,
                    hubspot=hubspot,
                )
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception("hyperscaler contact creation failed")
        return {"status": "logged", "invitation_id": invitation_id}

    if detail_type == "Engagement Invitation Accepted":
        target_stage = "Approved by AWS"
    elif detail_type in {"Engagement Invitation Rejected", "Engagement Invitation Expired"}:
        target_stage = "Closed Lost"
    else:
        return {"status": "skipped", "reason": f"unhandled detail-type {detail_type}"}

    # Resolve invitation_id -> govwin_id (the PartnerOpportunityIdentifier
    # we mint and store as the partner-side cross-reference) by querying
    # DynamoDB on the engagement_invitation_id we persisted at submit
    # time. Naming note: ``govwin_id`` here is the partner identifier
    # the AWS schema calls ``PartnerOpportunityIdentifier``; we use
    # ``govwin_id`` consistently inside this codebase because it's also
    # the GovWin opportunity id when the deal originated from GovWin
    # discovery. Earlier code shadowed this as ``partner_id`` which is
    # the AWS field name and led to confused reviews.
    try:
        govwin_id = state.find_govwin_by_invitation_id(str(invitation_id))
    except Exception:  # noqa: BLE001 -- defensive, lookup is best-effort
        logger.exception("invitation lookup failed for %s", invitation_id)
        govwin_id = None
    if not govwin_id:
        # No invitation->govwin_id mapping in DDB means we never recorded
        # this invitation. Either it predates the current Lambda code or
        # came through a manual path. Alert; the on-call can decide
        # whether to claim the invitation into HubSpot.
        config = load_config()
        _publish_orphan_alert(
            config=config,
            aws_opp_id=f"invitation:{invitation_id}",
            partner_opp_id=None,
            reason=(
                f"Engagement invitation {invitation_id} has no DDB lookup. "
                "Likely created outside this codebase."
            ),
        )
        return {"status": "skipped", "reason": "no mapping for invitation"}
    deal_id, heal_reason = _resolve_or_heal_deal_id(
        state=state, hubspot=hubspot, partner_id=str(govwin_id)
    )
    if not deal_id:
        config = load_config()
        _publish_orphan_alert(
            config=config,
            aws_opp_id=f"invitation:{invitation_id}",
            partner_opp_id=str(govwin_id),
            reason=heal_reason or "no HubSpot deal carries this govwin_opp_id",
        )
        return {
            "status": "skipped",
            "reason": f"orphan-after-self-heal ({heal_reason})",
        }
    if heal_reason:
        logger.info(
            "handle_ace_event: invitation event self-healed via %s for govwin=%s deal=%s",
            heal_reason,
            govwin_id,
            deal_id,
        )
    success, reason = _update_hubspot_stage(hubspot, str(deal_id), target_stage)
    if not success:
        return {"status": "skipped", "reason": reason or "stage update no-op"}
    return {"status": "updated", "deal_id": deal_id, "stage": target_stage}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    config = load_config()
    state = SyncStateManager(config)
    event_id = str(event.get("id") or "")
    detail_type = str(event.get("detail-type") or "")
    logger.info(
        "handle_ace_event.received id=%s detail-type=%s source=%s",
        event_id,
        detail_type,
        event.get("source"),
    )
    if event_id and not state.mark_event_seen_atomic(
        event_id, ttl_seconds=config.ace.event_dedup_ttl_seconds
    ):
        logger.info("handle_ace_event: dedup hit for %s", event_id)
        return {"status": "duplicate", "event_id": event_id}

    detail = event.get("detail") or {}
    ace = ACEClient(config)

    with HubSpotClient(config) as hubspot:
        if detail_type in {"Opportunity Created", "Opportunity Updated"}:
            result = _handle_opportunity_event(detail, state=state, ace=ace, hubspot=hubspot)
        elif detail_type.startswith("Engagement Invitation"):
            result = _handle_invitation_event(
                detail_type, detail, state=state, ace=ace, hubspot=hubspot
            )
        else:
            result = {"status": "skipped", "reason": f"unhandled detail-type {detail_type}"}
    logger.info("handle_ace_event.result %s", result)
    return result
