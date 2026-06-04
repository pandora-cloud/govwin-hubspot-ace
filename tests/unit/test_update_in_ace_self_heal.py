"""Self-heal path for update_in_ace when the DDB cache is missing the
opportunity_id.

DDB is treated as a cache; the HubSpot deal's ``govwin_aws_cosell_id``
property is the source of truth (it is written by submit_to_ace and
handle_ace_event whenever the AWS side acks the create). If the cache
is empty for any reason (test rollback, deploy-time drift, manual
mapping deletion), update_in_ace re-derives from HubSpot and backfills
the cache rather than dropping the message.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.lambdas import update_in_ace as update_mod


@pytest.fixture
def state_mock() -> MagicMock:
    state = MagicMock()
    state.find_govwin_by_hubspot_deal_id = MagicMock(return_value="DEMO-CACHE-MISS-001")
    state.get_ace_mapping = MagicMock(return_value={})  # cache miss
    state.update_ace_mapping = MagicMock()
    return state


@pytest.fixture
def hubspot_mock() -> MagicMock:
    hs = MagicMock()
    hs.get_deal = MagicMock(return_value={"id": "100000000002", "properties": {}})
    return hs


@pytest.fixture
def ace_mock() -> MagicMock:
    ace = MagicMock()
    return ace


def _event(prop: str = "dealname", value: str = "Test Deal") -> dict[str, Any]:
    return {
        "objectId": 100000000002,
        "subscriptionType": "object.propertyChange",
        "propertyName": prop,
        "propertyValue": value,
    }


def test_self_heal_recovers_from_deal_property(state_mock, hubspot_mock, ace_mock) -> None:
    """DDB miss + deal carries govwin_aws_cosell_id => backfill cache, proceed."""
    # The govwin id resolution itself works (it lives in deal properties);
    # the failure is the opportunity_id cache miss.
    hubspot_mock.get_deal.return_value = {
        "id": "100000000002",
        "properties": {
            "govwin_opp_id": "DEMO-CACHE-MISS-001",
            "govwin_aws_cosell_id": "O10000005",
        },
    }
    # _resolve_govwin_id reads via state first then hubspot; stub state to return
    state_mock.find_govwin_by_hubspot_deal_id.return_value = "DEMO-CACHE-MISS-001"
    # _resolve_property_value will read the deal too; provide the value.
    # PartnerOpportunityIdentifier matches govwin_id so the self-heal
    # verify step succeeds.
    ace_mock.get_opportunity.return_value = {
        "Id": "O10000005",
        "PartnerOpportunityIdentifier": "DEMO-CACHE-MISS-001",
        "LastModifiedDate": "2026-05-27T00:00:00Z",
        "Project": {"Title": "Old Title"},
    }
    ace_mock.update_with_retry.return_value = {
        "LastModifiedDate": "2026-05-27T01:00:00Z",
    }

    result = update_mod._process_event(
        _event(),
        config=MagicMock(),
        state=state_mock,
        ace=ace_mock,
        hubspot=hubspot_mock,
    )

    # Backfill was attempted with the recovered opportunity id.
    state_mock.update_ace_mapping.assert_any_call(
        govwin_id="DEMO-CACHE-MISS-001",
        ace_opportunity_id="O10000005",
        hubspot_deal_id="100000000002",
    )
    # We did NOT return "skipped: no ace mapping yet"; the self-heal kicked in.
    assert result["status"] != "skipped"


def test_returns_skipped_when_neither_cache_nor_deal_have_opportunity_id(
    state_mock, hubspot_mock, ace_mock
) -> None:
    """Cache miss + no deal property => legit "skipped" (create not yet complete)."""
    hubspot_mock.get_deal.return_value = {
        "id": "100000000002",
        "properties": {
            "govwin_opp_id": "DEMO-CACHE-MISS-001",
            # govwin_aws_cosell_id absent; AWS side never acked yet.
        },
    }
    state_mock.find_govwin_by_hubspot_deal_id.return_value = "DEMO-CACHE-MISS-001"

    result = update_mod._process_event(
        _event(),
        config=MagicMock(),
        state=state_mock,
        ace=ace_mock,
        hubspot=hubspot_mock,
    )

    assert result["status"] == "skipped"
    assert "ace mapping yet" in result["reason"]
    # No AWS call should have been attempted.
    ace_mock.update_with_retry.assert_not_called()
    # No backfill should have been attempted.
    state_mock.update_ace_mapping.assert_not_called()


def test_self_heal_refuses_when_partner_id_mismatches(state_mock, hubspot_mock, ace_mock) -> None:
    """The HubSpot deal points at AWS opp X, but X's PartnerOpportunityIdentifier
    does NOT equal the govwin_id we're processing. This could be a hand-edited
    deal redirecting our pipeline at a foreign-partner opportunity. Refuse
    to mutate; backfill nothing; SNS-alert; return skipped."""
    hubspot_mock.get_deal.return_value = {
        "id": "100000000002",
        "properties": {
            "govwin_opp_id": "DEMO-CACHE-MISS-001",
            "govwin_aws_cosell_id": "O10000005",  # someone else's opp
        },
    }
    state_mock.find_govwin_by_hubspot_deal_id.return_value = "DEMO-CACHE-MISS-001"
    # AWS-side opp reports a DIFFERENT PartnerOpportunityIdentifier.
    ace_mock.get_opportunity.return_value = {
        "Id": "O10000005",
        "PartnerOpportunityIdentifier": "SOMEONE-ELSES-OPP",
    }

    result = update_mod._process_event(
        _event(),
        config=MagicMock(),
        state=state_mock,
        ace=ace_mock,
        hubspot=hubspot_mock,
    )

    assert result["status"] == "skipped"
    assert "verification" in result["reason"].lower()
    # No AWS mutations, no DDB backfill; safety first.
    ace_mock.update_with_retry.assert_not_called()
    state_mock.update_ace_mapping.assert_not_called()


def test_self_heal_refuses_cross_deal_rebind(state_mock, hubspot_mock, ace_mock) -> None:
    """A partially-populated DDB ACE# row bound to a DIFFERENT
    hubspot_deal_id must NOT be rebound via the BD-editable
    govwin_aws_cosell_id deal property. Refusing the rebind protects
    against the H1 attack scenario where an attacker with HubSpot edit
    access + AWS Sandbox console access could redirect updates between
    deals by collision-matching PartnerOpportunityIdentifier values.
    """
    # ACE# row exists with hubspot_deal_id set, but the incoming event
    # comes from a different deal. ace_opportunity_id is empty (partial
    # state; this is the only way the self-heal path runs at all).
    state_mock.get_ace_mapping.return_value = {
        "hubspot_deal_id": "111111111111",
    }
    # Resolution returns the govwin_id of the bound row.
    state_mock.find_govwin_by_hubspot_deal_id.return_value = "DEMO-CACHE-MISS-001"

    config = MagicMock()
    config.ace.catalog = "Sandbox"

    result = update_mod._process_event(
        _event(),
        config=config,
        state=state_mock,
        ace=ace_mock,
        hubspot=hubspot_mock,
    )

    assert result["status"] == "skipped"
    assert "cross-deal rebind" in result["reason"]
    # Did NOT call get_deal for the cosell_id recovery (refused before reaching it).
    assert hubspot_mock.get_deal.call_count == 0
    # Did NOT call AWS GetOpportunity for verify either.
    assert ace_mock.get_opportunity.call_count == 0


def test_self_heal_survives_hubspot_get_deal_failure(state_mock, hubspot_mock, ace_mock) -> None:
    """When the self-heal HubSpot lookup fails, we land in "skipped" (not retried)."""
    state_mock.find_govwin_by_hubspot_deal_id.return_value = "DEMO-CACHE-MISS-001"

    call_count = [0]

    def _flaky_get_deal(deal_id: str, properties: list[str] | None = None) -> dict[str, Any]:
        # First call (govwin id resolution) succeeds; second (cache backfill) fails.
        call_count[0] += 1
        if call_count[0] == 1:
            return {"id": deal_id, "properties": {"govwin_opp_id": "DEMO-CACHE-MISS-001"}}
        raise RuntimeError("HubSpot 5xx")

    hubspot_mock.get_deal.side_effect = _flaky_get_deal

    result = update_mod._process_event(
        _event(),
        config=MagicMock(),
        state=state_mock,
        ace=ace_mock,
        hubspot=hubspot_mock,
    )

    assert result["status"] == "skipped"
    assert "ace mapping yet" in result["reason"]
