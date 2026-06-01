"""Tests for the scheduled reconcile-pending sweep Lambda."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ace.client import ACEAPIError
from src.lambdas import reconcile_pending


def _row(govwin: str, status_unused: str = "", *, deal: str = "deal-1") -> dict:
    return {
        "pk": f"ACE#{govwin}",
        "sk": "MAPPING",
        "ace_opportunity_id": f"O-{govwin}",
        "hubspot_deal_id": deal,
        "pending_reconcile_props": {"amount"},
    }


def _opp(review_status: str) -> dict:
    return {
        "Id": "O-X",
        "PartnerOpportunityIdentifier": "X",
        "LifeCycle": {"ReviewStatus": review_status},
        "LastModifiedDate": "2026-06-01T00:00:00Z",
    }


@pytest.fixture
def wired():
    state = MagicMock()
    ace = MagicMock()
    hubspot = MagicMock()
    hubspot.__enter__.return_value = hubspot
    hubspot.__exit__.return_value = False
    with (
        patch.object(reconcile_pending, "load_config", return_value=MagicMock()),
        patch.object(reconcile_pending, "SyncStateManager", return_value=state),
        patch.object(reconcile_pending, "ACEClient", return_value=ace),
        patch.object(reconcile_pending, "HubSpotClient", return_value=hubspot),
    ):
        yield state, ace, hubspot


def test_reconciles_editable_row(wired):
    state, ace, hubspot = wired
    state.scan_pending_reconcile.return_value = [_row("OPP1")]
    ace.get_opportunity.return_value = _opp("Approved")
    with patch.object(reconcile_pending, "reconcile_pending_props") as recon:
        summary = reconcile_pending.handler({}, context=None)
    recon.assert_called_once()
    kwargs = recon.call_args.kwargs
    assert kwargs["govwin_id"] == "OPP1"
    assert kwargs["ace_id"] == "O-OPP1"
    assert summary["reconciled"] == 1
    assert summary["skipped_locked"] == 0


def test_skips_review_locked_row(wired):
    state, ace, hubspot = wired
    state.scan_pending_reconcile.return_value = [_row("OPP1")]
    ace.get_opportunity.return_value = _opp("Submitted")
    with patch.object(reconcile_pending, "reconcile_pending_props") as recon:
        summary = reconcile_pending.handler({}, context=None)
    recon.assert_not_called()
    assert summary["skipped_locked"] == 1
    assert summary["reconciled"] == 0


def test_mixed_batch_counts(wired):
    state, ace, hubspot = wired
    state.scan_pending_reconcile.return_value = [_row("A"), _row("B")]
    ace.get_opportunity.side_effect = [_opp("Approved"), _opp("In review")]
    with patch.object(reconcile_pending, "reconcile_pending_props"):
        summary = reconcile_pending.handler({}, context=None)
    assert summary["scanned"] == 2
    assert summary["reconciled"] == 1
    assert summary["skipped_locked"] == 1


def test_incomplete_row_skipped(wired):
    state, ace, hubspot = wired
    state.scan_pending_reconcile.return_value = [
        {"pk": "ACE#OPP1", "sk": "MAPPING", "pending_reconcile_props": {"amount"}}
    ]
    with patch.object(reconcile_pending, "reconcile_pending_props") as recon:
        summary = reconcile_pending.handler({}, context=None)
    recon.assert_not_called()
    ace.get_opportunity.assert_not_called()
    assert summary["reconciled"] == 0


def test_get_opportunity_error_counts_as_errored(wired):
    state, ace, hubspot = wired
    state.scan_pending_reconcile.return_value = [_row("OPP1")]
    ace.get_opportunity.side_effect = ACEAPIError("gone", code="ResourceNotFoundException")
    summary = reconcile_pending.handler({}, context=None)
    assert summary["errored"] == 1
    assert summary["reconciled"] == 0


def test_reconcile_exception_does_not_abort_sweep(wired):
    state, ace, hubspot = wired
    state.scan_pending_reconcile.return_value = [_row("A"), _row("B")]
    ace.get_opportunity.return_value = _opp("Approved")
    with patch.object(
        reconcile_pending,
        "reconcile_pending_props",
        side_effect=[RuntimeError("boom"), {"status": "reconciled"}],
    ):
        summary = reconcile_pending.handler({}, context=None)
    # First row errors, second still processed.
    assert summary["errored"] == 1
    assert summary["reconciled"] == 1
