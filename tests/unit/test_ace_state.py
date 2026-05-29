"""Tests for ACE-related helpers on SyncStateManager."""

from __future__ import annotations

import pytest

from src.sync.state import SyncStateManager


@pytest.fixture
def state(app_config, mock_aws_env) -> SyncStateManager:
    return SyncStateManager(app_config)


class TestACEMapping:
    def test_get_returns_none_when_missing(self, state: SyncStateManager) -> None:
        assert state.get_ace_mapping("OPP-NONE") is None

    def test_update_then_get(self, state: SyncStateManager) -> None:
        state.update_ace_mapping(
            govwin_id="OPP1",
            ace_opportunity_id="O1",
            last_modified_date="2026-04-29T00:00:00Z",
            client_token="tok-1",
            hubspot_deal_id="deal-1",
        )
        record = state.get_ace_mapping("OPP1")
        assert record is not None
        assert record["ace_opportunity_id"] == "O1"
        assert record["last_modified_date"] == "2026-04-29T00:00:00Z"
        assert record["client_token"] == "tok-1"
        assert record["hubspot_deal_id"] == "deal-1"

    def test_update_merges_without_clobbering_prior_fields(self, state: SyncStateManager) -> None:
        """Second update must not erase the fields written by the first."""
        state.update_ace_mapping(
            govwin_id="OPP1",
            ace_opportunity_id="O1",
            client_token="tok-1",
        )
        state.update_ace_mapping(
            govwin_id="OPP1",
            ace_engagement_invitation_id="EI1",
            ace_task_id="T1",
        )
        record = state.get_ace_mapping("OPP1")
        assert record is not None
        assert record["ace_opportunity_id"] == "O1"
        assert record["client_token"] == "tok-1"
        assert record["ace_engagement_invitation_id"] == "EI1"
        assert record["ace_task_id"] == "T1"


class TestReserveClientToken:
    def test_returns_supplied_token_when_no_existing(self, state: SyncStateManager) -> None:
        result = state.reserve_client_token("OPP1", "tok-new")
        assert result == "tok-new"

    def test_returns_existing_token_on_retry(self, state: SyncStateManager) -> None:
        first = state.reserve_client_token("OPP1", "tok-1")
        second = state.reserve_client_token("OPP1", "tok-2")
        assert first == "tok-1"
        assert second == "tok-1"  # idempotent on redelivery


class TestReserveTaskClientToken:
    def test_persists_first_call(self, state: SyncStateManager) -> None:
        # Need a parent ACE mapping first since the helper does an UpdateItem.
        state.reserve_client_token("OPP1", "create-tok")
        result = state.reserve_task_client_token("OPP1", "task-tok-1")
        assert result == "task-tok-1"

    def test_idempotent_on_retry(self, state: SyncStateManager) -> None:
        state.reserve_client_token("OPP1", "create-tok")
        first = state.reserve_task_client_token("OPP1", "task-tok-1")
        second = state.reserve_task_client_token("OPP1", "task-tok-2")
        assert first == "task-tok-1"
        assert second == "task-tok-1"


class TestEventDedup:
    def test_atomic_first_sighting_returns_true(self, state: SyncStateManager) -> None:
        assert state.mark_event_seen_atomic("evt-x") is True

    def test_atomic_second_sighting_returns_false(self, state: SyncStateManager) -> None:
        state.mark_event_seen_atomic("evt-x")
        assert state.mark_event_seen_atomic("evt-x") is False

    def test_unseen_event_returns_false(self, state: SyncStateManager) -> None:
        assert state.is_event_seen("evt-x") is False

    def test_seen_after_atomic_mark(self, state: SyncStateManager) -> None:
        assert state.mark_event_seen_atomic("evt-x") is True
        assert state.is_event_seen("evt-x") is True


class TestInvitationLookup:
    def test_returns_none_when_no_match(self, state: SyncStateManager) -> None:
        assert state.find_govwin_by_invitation_id("nope") is None

    def test_returns_govwin_id_when_invitation_matches(self, state: SyncStateManager) -> None:
        state.update_ace_mapping(
            govwin_id="OPP1",
            ace_opportunity_id="O1",
            ace_engagement_invitation_id="engi-abc",
            hubspot_deal_id="deal-1",
        )
        assert state.find_govwin_by_invitation_id("engi-abc") == "OPP1"

    def test_find_by_hubspot_deal_id(self, state: SyncStateManager) -> None:
        state.update_ace_mapping(
            govwin_id="OPP1",
            ace_opportunity_id="O1",
            hubspot_deal_id="deal-42",
        )
        assert state.find_govwin_by_hubspot_deal_id("deal-42") == "OPP1"

    def test_subsequent_update_refreshes_existing_reverse_index_ttl(
        self, state: SyncStateManager
    ) -> None:
        """Updates that omit invitation/deal args must still refresh both reverse rows.

        Otherwise the reverse-lookup rows decay on a separate TTL clock from the
        forward ACE# mapping and the 365-day window resets only when the caller
        happens to pass the same id again.
        """
        state.update_ace_mapping(
            govwin_id="OPP1",
            ace_opportunity_id="O1",
            ace_engagement_invitation_id="engi-abc",
            hubspot_deal_id="deal-42",
        )
        before_inv = state._mappings_table.get_item(Key={"pk": "INV#engi-abc", "sk": "REVERSE"})[
            "Item"
        ]
        before_deal = state._mappings_table.get_item(Key={"pk": "DEAL#deal-42", "sk": "REVERSE"})[
            "Item"
        ]

        # Subsequent update touches an unrelated field. Reverse rows must
        # still refresh from the values already persisted on the forward row.
        state.update_ace_mapping(govwin_id="OPP1", last_modified_date="2026-05-01T00:00:00Z")

        after_inv = state._mappings_table.get_item(Key={"pk": "INV#engi-abc", "sk": "REVERSE"})[
            "Item"
        ]
        after_deal = state._mappings_table.get_item(Key={"pk": "DEAL#deal-42", "sk": "REVERSE"})[
            "Item"
        ]
        assert after_inv["updated_at"] >= before_inv["updated_at"]
        assert after_deal["updated_at"] >= before_deal["updated_at"]
        assert state.find_govwin_by_invitation_id("engi-abc") == "OPP1"
        assert state.find_govwin_by_hubspot_deal_id("deal-42") == "OPP1"
