"""Tests for the shared reconcile_pending_props helper (src.ace.reconcile)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.ace.client import ACEAPIError
from src.ace.reconcile import (
    BLOCKED_REVIEW_STATUSES,
    RECONCILABLE_REVIEW_STATUSES,
    reconcile_pending_props,
)

DEAL_ID = "100000000005"


def _current_full() -> dict:
    return {
        "Id": "O-EXISTING",
        "Catalog": "Sandbox",
        "PartnerOpportunityIdentifier": "OPP1234",
        "LastModifiedDate": "2026-05-30T00:00:00Z",
        "Project": {
            "Title": "Existing Title",
            "CustomerBusinessProblem": "An existing description well over twenty chars.",
        },
        "LifeCycle": {"ReviewStatus": "Approved", "TargetCloseDate": "2026-12-31"},
    }


def _mocks(deal_props: dict):
    ace = MagicMock()
    ace.update_with_retry.return_value = {"LastModifiedDate": "2026-06-01T00:00:00Z"}
    hubspot = MagicMock()
    hubspot.get_deal.return_value = {"id": DEAL_ID, "properties": deal_props}
    state = MagicMock()
    return ace, hubspot, state


def _call(ace, hubspot, state, props, full=None):
    return reconcile_pending_props(
        ace=ace,
        hubspot=hubspot,
        state=state,
        govwin_id="OPP1234",
        ace_id="O-EXISTING",
        deal_id=DEAL_ID,
        props=set(props),
        current_full=full or _current_full(),
    )


def test_status_constants_are_disjoint():
    assert BLOCKED_REVIEW_STATUSES.isdisjoint(RECONCILABLE_REVIEW_STATUSES)


def test_happy_replay_coalesces_into_one_update():
    ace, hubspot, state = _mocks({"amount": "120000", "closedate": "2027-06-30"})
    result = _call(ace, hubspot, state, {"amount", "closedate"})
    assert result["status"] == "reconciled"
    # Both parked props land in ONE UpdateOpportunity call.
    ace.update_with_retry.assert_called_once()
    payload = ace.update_with_retry.call_args.kwargs["updates"]
    assert payload["Project"]["ExpectedCustomerSpend"][0]["Amount"] == "10000.00"
    assert payload["LifeCycle"]["TargetCloseDate"] == "2027-06-30"
    # Deal read exactly once, batching all props.
    hubspot.get_deal.assert_called_once()
    assert sorted(hubspot.get_deal.call_args.kwargs["properties"]) == ["amount", "closedate"]
    state.update_ace_mapping.assert_called_once()
    state.clear_pending_reconcile_props.assert_called_once_with("OPP1234")


def test_success_writeback_replaces_queued_note():
    ace, hubspot, state = _mocks({"amount": "120000"})
    _call(ace, hubspot, state, {"amount"})
    hubspot.update_deal.assert_called_once()
    note = hubspot.update_deal.call_args.args[1]["govwin_ace_next_steps"]
    assert "applied to AWS after review" in note
    assert "amount" in note
    assert len(note) <= 255


def test_noop_does_not_write_success_note():
    ace, hubspot, state = _mocks({"amount": ""})
    _call(ace, hubspot, state, {"amount"})
    hubspot.update_deal.assert_not_called()


def test_known_last_modified_date_passed_through():
    ace, hubspot, state = _mocks({"amount": "120000"})
    _call(ace, hubspot, state, {"amount"})
    assert (
        ace.update_with_retry.call_args.kwargs["known_last_modified_date"] == "2026-05-30T00:00:00Z"
    )


def test_noop_when_no_delta_applies_clears_set():
    # Deal value is empty -> _apply_delta returns False -> nothing to send.
    ace, hubspot, state = _mocks({"amount": ""})
    result = _call(ace, hubspot, state, {"amount"})
    assert result["status"] == "noop"
    ace.update_with_retry.assert_not_called()
    state.clear_pending_reconcile_props.assert_called_once_with("OPP1234")


def test_empty_props_clears_and_noops():
    ace, hubspot, state = _mocks({})
    result = _call(ace, hubspot, state, set())
    assert result["status"] == "noop"
    ace.update_with_retry.assert_not_called()
    hubspot.get_deal.assert_not_called()
    state.clear_pending_reconcile_props.assert_called_once_with("OPP1234")


def test_merit_rejection_clears_set_and_writes_back():
    ace, hubspot, state = _mocks({"amount": "120000"})
    ace.update_with_retry.side_effect = ACEAPIError("field is invalid", code="ValidationException")
    result = _call(ace, hubspot, state, {"amount"})
    assert result["status"] == "rejected"
    # Don't loop forever on a genuinely bad value.
    state.clear_pending_reconcile_props.assert_called_once_with("OPP1234")
    # Reason surfaced on the deal.
    assert hubspot.update_deal.called


def test_review_locked_race_is_reraised_and_left_parked():
    ace, hubspot, state = _mocks({"amount": "120000"})
    ace.update_with_retry.side_effect = ACEAPIError(
        "ACTION_NOT_PERMITTED: cannot be modified in Submitted, Rejected or In review status",
        code="ValidationException",
    )
    with pytest.raises(ACEAPIError):
        _call(ace, hubspot, state, {"amount"})
    state.clear_pending_reconcile_props.assert_not_called()


def test_transient_error_is_reraised_and_left_parked():
    ace, hubspot, state = _mocks({"amount": "120000"})
    ace.update_with_retry.side_effect = ACEAPIError("slow", code="ThrottlingException")
    with pytest.raises(ACEAPIError):
        _call(ace, hubspot, state, {"amount"})
    state.clear_pending_reconcile_props.assert_not_called()
    state.update_ace_mapping.assert_not_called()
