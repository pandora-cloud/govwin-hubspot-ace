"""Closed-Lost stage/reason race-condition self-heal.

When BD saves a form that sets both ``lifecycle_stage='Closed Lost'`` and
``lifecycle_closed_lost_reason`` simultaneously, HubSpot emits two
property-change webhooks. They arrive in undefined order. AWS rejects
Stage='Closed Lost' without a Reason -> permanent error -> SNS alert,
and the deal ends "Closed Lost" in HubSpot but stuck at the prior Stage
in AWS.

``_ensure_closed_lost_pair_consistency`` reads the missing companion
property from the deal so both fields are in the same UpdateOpportunity
regardless of webhook order. These tests assert that behavior.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.lambdas.update_in_ace import _ensure_closed_lost_pair_consistency


@pytest.fixture
def hubspot_mock() -> MagicMock:
    return MagicMock()


def test_stage_first_backfills_reason_from_deal(hubspot_mock: MagicMock) -> None:
    """Stage webhook arrives first: backfill ClosedLostReason from the deal."""
    payload = {
        "LifeCycle": {"Stage": "Closed Lost"},
        "Project": {},
    }
    hubspot_mock.get_deal.return_value = {
        "id": "326811999945",
        "properties": {"govwin_ace_closed_lost_reason": "Price"},
    }
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    assert payload["LifeCycle"]["Stage"] == "Closed Lost"
    assert payload["LifeCycle"]["ClosedLostReason"] == "Price"
    hubspot_mock.get_deal.assert_called_once_with(
        "326811999945", properties=["govwin_ace_closed_lost_reason"]
    )


def test_reason_first_backfills_stage_when_deal_has_closed_lost(
    hubspot_mock: MagicMock,
) -> None:
    """Reason webhook arrives first AND deal says Closed Lost: backfill Stage."""
    payload = {
        "LifeCycle": {"ClosedLostReason": "Price", "Stage": "Qualified"},
        "Project": {},
    }
    hubspot_mock.get_deal.return_value = {
        "id": "326811999945",
        "properties": {"govwin_ace_lifecycle_stage": "Closed Lost"},
    }
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    assert payload["LifeCycle"]["Stage"] == "Closed Lost"
    assert payload["LifeCycle"]["ClosedLostReason"] == "Price"


def test_reason_without_closed_lost_stage_is_dropped(hubspot_mock: MagicMock) -> None:
    """Reason webhook with deal Stage != Closed Lost: drop the orphan reason."""
    payload = {
        "LifeCycle": {"ClosedLostReason": "Price", "Stage": "Qualified"},
        "Project": {},
    }
    hubspot_mock.get_deal.return_value = {
        "id": "326811999945",
        "properties": {"govwin_ace_lifecycle_stage": "Qualified"},
    }
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    assert "ClosedLostReason" not in payload["LifeCycle"]
    # Stage remains as Qualified (we don't touch it).
    assert payload["LifeCycle"]["Stage"] == "Qualified"


def test_consistent_pair_is_no_op(hubspot_mock: MagicMock) -> None:
    """Both Stage and Reason already set: no get_deal call."""
    payload = {
        "LifeCycle": {"Stage": "Closed Lost", "ClosedLostReason": "Price"},
        "Project": {},
    }
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    hubspot_mock.get_deal.assert_not_called()
    assert payload["LifeCycle"]["Stage"] == "Closed Lost"
    assert payload["LifeCycle"]["ClosedLostReason"] == "Price"


def test_no_closed_lost_in_payload_is_no_op(hubspot_mock: MagicMock) -> None:
    """Stage != Closed Lost AND no ClosedLostReason: no work to do."""
    payload = {
        "LifeCycle": {"Stage": "Qualified"},
        "Project": {},
    }
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    hubspot_mock.get_deal.assert_not_called()
    assert payload["LifeCycle"] == {"Stage": "Qualified"}


def test_get_deal_failure_falls_through(hubspot_mock: MagicMock) -> None:
    """A HubSpot get_deal failure during companion lookup is non-fatal."""
    payload = {
        "LifeCycle": {"Stage": "Closed Lost"},
        "Project": {},
    }
    hubspot_mock.get_deal.side_effect = RuntimeError("HubSpot 5xx")
    # Should not raise; the function logs and returns.
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    # Stage remains; AWS will reject this UpdateOpportunity which surfaces
    # via the existing permanent-error SNS alert path; preferred over
    # silently writing an incomplete payload.
    assert payload["LifeCycle"] == {"Stage": "Closed Lost"}


def test_deal_missing_reason_property_drops_through(hubspot_mock: MagicMock) -> None:
    """Deal exists but has no closed_lost_reason property: do not backfill."""
    payload = {
        "LifeCycle": {"Stage": "Closed Lost"},
        "Project": {},
    }
    hubspot_mock.get_deal.return_value = {
        "id": "326811999945",
        "properties": {},  # no govwin_ace_closed_lost_reason
    }
    _ensure_closed_lost_pair_consistency(payload, hubspot_mock, "326811999945")
    # No ClosedLostReason was injected; AWS will reject the call but at
    # least we didn't write garbage.
    assert "ClosedLostReason" not in payload["LifeCycle"]
