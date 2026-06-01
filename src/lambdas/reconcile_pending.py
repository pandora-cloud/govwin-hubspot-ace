"""Scheduled backstop that replays deferred ACE edits the event trigger missed.

The primary reconcile trigger is the ``Opportunity Updated`` EventBridge
event handled in :mod:`src.lambdas.handle_ace_event`. EventBridge delivery
from ``aws.partnercentral-selling`` is officially best-effort, so an
approve/reject event can be dropped, leaving a deal's deferred edits parked
forever. This Lambda runs on a schedule, scans for parked edits, and for
any opportunity that has since become editable, replays them via the shared
:func:`src.ace.reconcile.reconcile_pending_props`.

Rows whose opportunity is still review-locked are skipped and stay parked
for the next sweep.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.ace.reconcile import RECONCILABLE_REVIEW_STATUSES, reconcile_pending_props
from src.config import load_config
from src.hubspot.client import HubSpotClient
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def _govwin_id_from_pk(pk: str) -> str:
    """Strip the ``ACE#`` prefix off a mapping partition key."""
    return pk[len("ACE#") :] if pk.startswith("ACE#") else pk


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Sweep parked reconcile rows and replay edits for now-editable opps.

    :returns: A summary dict with counts of rows scanned / reconciled /
        skipped (still locked) / errored.
    """
    config = load_config()
    state = SyncStateManager(config)
    ace = ACEClient(config)

    rows = state.scan_pending_reconcile()
    reconciled = 0
    skipped_locked = 0
    errored = 0

    with HubSpotClient(config) as hubspot:
        for row in rows:
            govwin_id = _govwin_id_from_pk(str(row.get("pk") or ""))
            ace_id = str(row.get("ace_opportunity_id") or "")
            deal_id = str(row.get("hubspot_deal_id") or "")
            props = row.get("pending_reconcile_props")
            if not (govwin_id and ace_id and deal_id and props):
                logger.warning(
                    "reconcile_pending: incomplete parked row govwin=%s; skipping",
                    govwin_id or "?",
                )
                continue
            try:
                full = ace.get_opportunity(ace_id)
            except ACEAPIError as exc:
                logger.warning("reconcile_pending: get_opportunity %s failed: %s", ace_id, exc)
                errored += 1
                continue
            review_status = str((full.get("LifeCycle") or {}).get("ReviewStatus") or "")
            if review_status not in RECONCILABLE_REVIEW_STATUSES:
                skipped_locked += 1
                continue
            try:
                reconcile_pending_props(
                    ace=ace,
                    hubspot=hubspot,
                    state=state,
                    govwin_id=govwin_id,
                    ace_id=ace_id,
                    deal_id=deal_id,
                    props=set(props),
                    current_full=full,
                )
                reconciled += 1
            except Exception:  # noqa: BLE001 -- one bad row must not abort the sweep
                logger.exception(
                    "reconcile_pending: reconcile failed for govwin=%s; left parked",
                    govwin_id,
                )
                errored += 1

    summary = {
        "scanned": len(rows),
        "reconciled": reconciled,
        "skipped_locked": skipped_locked,
        "errored": errored,
    }
    logger.info("reconcile_pending: %s", summary)
    return summary
