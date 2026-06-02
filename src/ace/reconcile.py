"""Replay HubSpot edits that were deferred while an ACE opportunity was in review.

AWS Partner Central rejects ``UpdateOpportunity`` while an opportunity's
review status is Submitted/In review/Rejected. ``update_in_ace`` parks the
changed HubSpot property names on the DynamoDB mapping
(``pending_reconcile_props``) instead of dropping them. Once the
opportunity becomes editable again, the deferred properties are replayed
here: the deal's *current* values are read straight from HubSpot and
re-applied in a single coalesced ``UpdateOpportunity``.

This module is the single reconcile implementation shared by two triggers:

* the event-driven path in :mod:`src.lambdas.handle_ace_event` (fires on the
  ``Opportunity Updated`` EventBridge event AWS emits on approve/reject), and
* the scheduled sweep in :mod:`src.lambdas.reconcile_pending` (a backstop for
  EventBridge's best-effort delivery).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.ace.client import ACEAPIError, ACEClient
from src.lambdas.update_in_ace import (
    _PERMANENT_ERROR_CODES,
    _apply_delta,
    _ensure_closed_lost_pair_consistency,
    _is_review_locked_error,
)

if TYPE_CHECKING:
    from src.hubspot.client import HubSpotClient
    from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)

# ------Review-status gating------
# AWS blocks UpdateOpportunity while the opportunity is in these review
# states; deferred edits stay parked until the opportunity leaves them.
BLOCKED_REVIEW_STATUSES: frozenset[str] = frozenset({"Submitted", "In review", "Rejected"})
# States in which UpdateOpportunity is accepted again, so a parked edit can
# be replayed. "Action Required" is editable (AWS asked the partner to
# revise and resubmit); "Approved" is the post-validation editable state.
RECONCILABLE_REVIEW_STATUSES: frozenset[str] = frozenset({"Approved", "Action Required"})


def reconcile_pending_props(
    *,
    ace: ACEClient,
    hubspot: HubSpotClient,
    state: SyncStateManager,
    govwin_id: str,
    ace_id: str,
    deal_id: str,
    props: set[str],
    current_full: dict[str, Any],
) -> dict[str, Any]:
    """Replay deferred HubSpot edits onto a now-editable ACE opportunity.

    The caller is responsible for confirming the opportunity's review
    status is reconcilable before calling (the event handler reads it from
    the inbound event; the sweep reads it via GetOpportunity).

    :param ace: ACE client.
    :param hubspot: HubSpot client (open).
    :param state: DynamoDB state manager.
    :param govwin_id: GovWin global opportunity id (mapping key).
    :param ace_id: AWS opportunity id to update.
    :param deal_id: Originating HubSpot deal id; current values are read
        from it.
    :param props: HubSpot property names parked during review.
    :param current_full: The current GetOpportunity response, reused for
        the PUT-semantics base payload and ``LastModifiedDate``.
    :returns: A status dict: ``reconciled`` (an update was applied),
        ``noop`` (no parked prop produced a delta), or ``rejected`` (AWS
        permanently rejected the replay; the parked set was cleared to
        avoid an infinite retry).
    :raises ACEAPIError: On transient errors, or if AWS is unexpectedly
        still review-locked (the parked set is left intact for retry).
    """
    if not props:
        state.clear_pending_reconcile_props(govwin_id)
        return {"status": "noop", "reason": "no pending props"}

    payload = ACEClient.scrub_for_update(current_full)

    # Read the deal's CURRENT values once. Reading straight from HubSpot
    # (rather than the parked webhook values) sidesteps webhook truncation
    # and naturally coalesces multiple parked edits into one UpdateOpportunity.
    try:
        deal = hubspot.get_deal(deal_id, properties=sorted(props))
    except Exception:  # noqa: BLE001 -- best-effort; nothing to replay without it
        logger.exception("reconcile: get_deal %s failed; leaving props parked", deal_id)
        raise ACEAPIError(
            f"reconcile: could not read deal {deal_id}", code="HubSpotReadFailed"
        ) from None
    deal_props = deal.get("properties") or {}

    applied = False
    for prop in sorted(props):
        if _apply_delta(payload, prop, deal_props.get(prop)):
            applied = True

    _ensure_closed_lost_pair_consistency(payload, hubspot, deal_id)

    if not applied:
        # Every parked prop resolved to an empty / irrelevant value (e.g.
        # the deal field was cleared after the edit was parked). Nothing to
        # send; drop the parked set so this row stops being swept.
        state.clear_pending_reconcile_props(govwin_id)
        logger.info("reconcile: govwin=%s no applicable delta; cleared parked props", govwin_id)
        return {"status": "noop", "reason": "no applicable delta"}

    try:
        response = ace.update_with_retry(
            identifier=ace_id,
            updates=payload,
            known_last_modified_date=current_full.get("LastModifiedDate"),
        )
    except ACEAPIError as exc:
        if _is_review_locked_error(exc):
            # Race: review status flipped back to blocked between the
            # caller's check and now. Leave the props parked for the next
            # trigger; do not clear.
            logger.warning(
                "reconcile: govwin=%s still review-locked; leaving props parked",
                govwin_id,
            )
            raise
        if exc.code in _PERMANENT_ERROR_CODES:
            # A genuine merit rejection (bad field value), not a status
            # lock. Clear the parked set so we don't loop forever on a
            # value AWS will never accept, and surface it on the deal.
            logger.warning(
                "reconcile: govwin=%s permanent rejection %s; clearing parked props",
                govwin_id,
                exc.code,
            )
            _writeback_rejection(hubspot, deal_id, exc)
            state.clear_pending_reconcile_props(govwin_id)
            return {"status": "rejected", "ace_opportunity_id": ace_id, "error": exc.code}
        # Transient (Throttling / Conflict-exhausted / InternalServer):
        # leave parked and let the caller decide whether to retry.
        raise

    state.update_ace_mapping(
        govwin_id=govwin_id,
        last_modified_date=str(response.get("LastModifiedDate"))
        if response.get("LastModifiedDate")
        else None,
        hubspot_deal_id=deal_id,
    )
    state.clear_pending_reconcile_props(govwin_id)
    _writeback_success(hubspot, deal_id, sorted(props))
    logger.info(
        "reconcile: govwin=%s opp=%s replayed %d parked prop(s)",
        govwin_id,
        ace_id,
        len(props),
    )
    return {"status": "reconciled", "ace_opportunity_id": ace_id, "props": sorted(props)}


def _writeback_success(hubspot: HubSpotClient, deal_id: str, props: list[str]) -> None:
    """Replace the deferral "queued" note once the parked edits actually land on AWS."""
    from src.ace.validators import is_valid_hubspot_object_id

    if not is_valid_hubspot_object_id(deal_id):
        return
    note = f"Deferred edit(s) applied to AWS after review completed: {', '.join(props)}."
    try:
        hubspot.update_deal(deal_id, {"govwin_ace_next_steps": note[:255]})
    except Exception:  # noqa: BLE001 -- writeback is best-effort
        logger.exception("reconcile: success writeback failed for deal %s", deal_id)


def _writeback_rejection(hubspot: HubSpotClient, deal_id: str, exc: ACEAPIError) -> None:
    """Best-effort note onto the deal when a replayed edit is permanently rejected."""
    from src.ace.validators import is_valid_hubspot_object_id
    from src.hubspot.client import _redact_hubspot_error_body

    if not is_valid_hubspot_object_id(deal_id):
        return
    redacted = _redact_hubspot_error_body(str(exc))
    try:
        hubspot.update_deal(
            deal_id,
            {
                "govwin_ace_next_steps": (
                    f"AWS rejected the deferred edit after review ({exc.code}): {redacted[:1400]}"
                )
            },
        )
    except Exception:  # noqa: BLE001 -- writeback is best-effort
        logger.exception("reconcile: rejection writeback failed for deal %s", deal_id)
