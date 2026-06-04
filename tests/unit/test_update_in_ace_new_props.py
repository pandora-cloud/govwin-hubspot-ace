"""Tests for the new BD-editable property handlers in update_in_ace.

Added with the async /ui-extension/update path (Arch #2). The form's
PATCH to HubSpot fires one webhook per property change; each webhook
ends up here as a property->value delta the Lambda turns into an
UpdateOpportunity payload field (or, for AWS Products, a separate
Associate/Disassociate diff).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.lambdas.update_in_ace import (
    _apply_delta,
    _handle_aws_products_diff,
    _handle_solution_diff,
)


def _empty_payload() -> dict:
    return {"Project": {}, "LifeCycle": {}, "Customer": {"Account": {}}}


class TestLifecycleStage:
    @pytest.mark.parametrize(
        "stage",
        [
            "Prospect",
            "Qualified",
            "Technical Validation",
            "Business Validation",
            "Committed",
            "Launched",
            "Closed Lost",
        ],
    )
    def test_sets_stage(self, stage: str) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, "govwin_ace_lifecycle_stage", stage) is True
        assert payload["LifeCycle"]["Stage"] == stage

    def test_clearing_walks_back_from_closed_lost(self) -> None:
        payload = _empty_payload()
        payload["LifeCycle"]["ClosedLostReason"] = "Price"
        payload["LifeCycle"]["Stage"] = "Closed Lost"
        # Walking back to Qualified should drop the ClosedLostReason.
        assert _apply_delta(payload, "govwin_ace_lifecycle_stage", "Qualified") is True
        assert payload["LifeCycle"]["Stage"] == "Qualified"
        assert "ClosedLostReason" not in payload["LifeCycle"]

    def test_empty_is_no_op(self) -> None:
        """Empty value means "BD didn't change this"; preserve current AWS state."""
        payload = _empty_payload()
        payload["LifeCycle"]["Stage"] = "Qualified"
        assert _apply_delta(payload, "govwin_ace_lifecycle_stage", "") is False
        # Stage in payload untouched; the UpdateOpportunity will preserve it.
        assert payload["LifeCycle"]["Stage"] == "Qualified"


class TestClosedLostReason:
    def test_sets_reason(self) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, "govwin_ace_closed_lost_reason", "Price") is True
        assert payload["LifeCycle"]["ClosedLostReason"] == "Price"

    def test_empty_is_no_op(self) -> None:
        """Empty reason on its own preserves existing AWS state."""
        payload = _empty_payload()
        payload["LifeCycle"]["ClosedLostReason"] = "Price"
        assert _apply_delta(payload, "govwin_ace_closed_lost_reason", "") is False
        assert payload["LifeCycle"]["ClosedLostReason"] == "Price"


class TestMultiValueEnums:
    @pytest.mark.parametrize(
        "prop, payload_key, value, expected",
        [
            (
                "govwin_ace_partner_need",
                "PrimaryNeedsFromAws",
                "Co-Sell - Deal Support;Co-Sell - Pricing Assistance",
                ["Co-Sell - Deal Support", "Co-Sell - Pricing Assistance"],
            ),
            ("govwin_ace_partner_need", "PrimaryNeedsFromAws", "Deal Support", ["Deal Support"]),
        ],
    )
    def test_partner_need_split(
        self, prop: str, payload_key: str, value: str, expected: list[str]
    ) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, prop, value) is True
        assert payload[payload_key] == expected

    def test_delivery_model_split(self) -> None:
        payload = _empty_payload()
        assert (
            _apply_delta(payload, "govwin_ace_delivery_model", "SaaS or PaaS;Managed Services")
            is True
        )
        assert payload["Project"]["DeliveryModels"] == ["SaaS or PaaS", "Managed Services"]

    def test_sales_activities_split(self) -> None:
        payload = _empty_payload()
        assert (
            _apply_delta(
                payload,
                "govwin_ace_sales_activities",
                "Initialized discussions with customer;Conducted POC / Demo",
            )
            is True
        )
        assert payload["Project"]["SalesActivities"] == [
            "Initialized discussions with customer",
            "Conducted POC / Demo",
        ]


class TestSingleEnums:
    def test_national_security_accepts_yes_no(self) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, "govwin_ace_national_security", "Yes") is True
        assert payload["NationalSecurity"] == "Yes"

    def test_national_security_rejects_garbage(self) -> None:
        payload = _empty_payload()
        # Updated 2026-05-27: returns False (no-op) rather than silent True.
        # Earlier behavior was misleading; caller fired UpdateOpportunity
        # for a "successful" update that wrote nothing.
        assert _apply_delta(payload, "govwin_ace_national_security", "Maybe") is False
        assert "NationalSecurity" not in payload

    def test_opportunity_type(self) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, "govwin_ace_opportunity_type", "Net New Business") is True
        assert payload["OpportunityType"] == "Net New Business"

    def test_industry(self) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, "govwin_industry", "Government") is True
        assert payload["Customer"]["Account"]["Industry"] == "Government"


class TestAwsProductsDiff:
    def _setup(
        self,
        *,
        requested: str,
        current: list[str],
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        ace = MagicMock()
        ace.get_opportunity.return_value = {
            "RelatedEntityIdentifiers": {"AwsProducts": current},
        }
        ace.associate_opportunity.return_value = None
        ace.disassociate_opportunity.return_value = None
        hubspot = MagicMock()
        hubspot.get_deal.return_value = {
            "properties": {"govwin_ace_aws_products": requested},
        }
        return ace, hubspot, MagicMock()

    def test_adds_new_products(self) -> None:
        ace, hubspot, _ = self._setup(
            requested="AWSLambda;AmazonS3",
            current=["AWSLambda"],
        )
        result = _handle_aws_products_diff(
            ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot
        )
        ace.associate_opportunity.assert_called_once_with(
            opportunity_identifier="O10000003",
            related_entity_identifier="AmazonS3",
            related_entity_type="AwsProducts",
        )
        ace.disassociate_opportunity.assert_not_called()
        assert result["products_associated"] == ["AmazonS3"]
        assert result["products_disassociated"] == []

    def test_removes_dropped_products(self) -> None:
        ace, hubspot, _ = self._setup(
            requested="AWSLambda",
            current=["AWSLambda", "AmazonS3", "AWSAppMesh"],
        )
        result = _handle_aws_products_diff(
            ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot
        )
        ace.associate_opportunity.assert_not_called()
        # Sorted disassociate order is deterministic.
        assert sorted(result["products_disassociated"]) == ["AWSAppMesh", "AmazonS3"]

    def test_other_is_filtered_symmetrically(self) -> None:
        ace, hubspot, _ = self._setup(
            requested="AWSLambda;Other",  # local escape-hatch
            current=["AWSLambda", "Other"],  # legacy state with "Other"
        )
        result = _handle_aws_products_diff(
            ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot
        )
        ace.associate_opportunity.assert_not_called()
        ace.disassociate_opportunity.assert_not_called()
        assert result["products_associated"] == []
        assert result["products_disassociated"] == []

    def test_empty_requested_disassociates_all(self) -> None:
        ace, hubspot, _ = self._setup(
            requested="",
            current=["AWSLambda", "AmazonS3"],
        )
        _handle_aws_products_diff(ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot)
        assert ace.disassociate_opportunity.call_count == 2

    def test_no_change_is_no_op(self) -> None:
        ace, hubspot, _ = self._setup(
            requested="AWSLambda;AmazonS3",
            current=["AmazonS3", "AWSLambda"],
        )
        result = _handle_aws_products_diff(
            ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot
        )
        ace.associate_opportunity.assert_not_called()
        ace.disassociate_opportunity.assert_not_called()
        assert result["status"] == "updated"

    def test_conflict_on_associate_is_swallowed(self) -> None:
        from src.ace.client import ACEAPIError

        ace, hubspot, _ = self._setup(
            requested="AWSLambda",
            current=[],
        )
        ace.associate_opportunity.side_effect = ACEAPIError(
            "already associated", code="ConflictException"
        )
        result = _handle_aws_products_diff(
            ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot
        )
        assert result["products_associated"] == []  # didn't count the conflict as success
        assert result["status"] == "updated"

    def test_validation_failure_on_one_product_doesnt_fail_others(self) -> None:
        from src.ace.client import ACEAPIError

        ace, hubspot, _ = self._setup(
            requested="AWSLambda;NotARealProduct;AmazonS3",
            current=[],
        )

        # NotARealProduct fails; the others succeed.
        def _conditional_associate(**kwargs: Any) -> None:
            if kwargs["related_entity_identifier"] == "NotARealProduct":
                raise ACEAPIError("invalid", code="ValidationException")
            return None

        ace.associate_opportunity.side_effect = _conditional_associate
        result = _handle_aws_products_diff(
            ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot
        )
        assert "AWSLambda" in result["products_associated"]
        assert "AmazonS3" in result["products_associated"]
        assert "NotARealProduct" not in result["products_associated"]
        # B1.8: non-Conflict failures now surface in the result so the
        # outer handler can SNS-alert. Previously they were swallowed
        # silently at WARNING level.
        assert any("NotARealProduct" in f for f in result.get("failures", []))


class TestEmptyMultiValueReturnsFalse:
    """B1.5: empty values for multi-value enums must return False so the
    caller knows nothing was applied (was returning True silently)."""

    @pytest.mark.parametrize(
        "prop",
        [
            "govwin_ace_partner_need",
            "govwin_ace_delivery_model",
            "govwin_ace_sales_activities",
        ],
    )
    def test_empty_string_multi_value_returns_false(self, prop: str) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, prop, "") is False
        # Strip-only string also returns False (top-level guard catches "").
        # A whitespace-only payload would land in _apply_delta as the original
        # string (not stripped at the gate); confirm it still returns False.

    def test_opportunity_type_garbage_returns_false(self) -> None:
        payload = _empty_payload()
        assert _apply_delta(payload, "govwin_ace_opportunity_type", "Not a Real Type") is False
        assert "OpportunityType" not in payload


class TestSolutionDiff:
    def _setup(
        self,
        *,
        requested: str,
        current: list[str],
    ) -> tuple[MagicMock, MagicMock]:
        ace = MagicMock()
        ace.get_opportunity.return_value = {
            "RelatedEntityIdentifiers": {"Solutions": current},
        }
        ace.associate_opportunity.return_value = None
        ace.disassociate_opportunity.return_value = None
        hubspot = MagicMock()
        hubspot.get_deal.return_value = {
            "properties": {"govwin_ace_solution_id": requested},
        }
        return ace, hubspot

    def test_adds_new_solution(self) -> None:
        ace, hubspot = self._setup(requested="S-0050888", current=[])
        result = _handle_solution_diff(ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot)
        ace.associate_opportunity.assert_called_once_with(
            opportunity_identifier="O10000003",
            related_entity_identifier="S-0050888",
            related_entity_type="Solutions",
        )
        ace.disassociate_opportunity.assert_not_called()
        assert result["solution_associated"] == "S-0050888"

    def test_replaces_solution(self) -> None:
        ace, hubspot = self._setup(requested="S-0050888", current=["S-0050887"])
        _handle_solution_diff(ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot)
        # Disassociate fires before Associate (AWS allows only one Solution).
        ace.disassociate_opportunity.assert_called_once_with(
            opportunity_identifier="O10000003",
            related_entity_identifier="S-0050887",
            related_entity_type="Solutions",
        )
        ace.associate_opportunity.assert_called_once_with(
            opportunity_identifier="O10000003",
            related_entity_identifier="S-0050888",
            related_entity_type="Solutions",
        )

    def test_removes_solution_when_cleared(self) -> None:
        ace, hubspot = self._setup(requested="", current=["S-0050887"])
        result = _handle_solution_diff(ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot)
        ace.disassociate_opportunity.assert_called_once()
        ace.associate_opportunity.assert_not_called()
        assert result["solution_disassociated"] == "S-0050887"

    def test_no_change_is_no_op(self) -> None:
        ace, hubspot = self._setup(requested="S-0050888", current=["S-0050888"])
        result = _handle_solution_diff(ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot)
        ace.associate_opportunity.assert_not_called()
        ace.disassociate_opportunity.assert_not_called()
        assert result["status"] == "updated"

    def test_conflict_on_associate_is_swallowed(self) -> None:
        from src.ace.client import ACEAPIError

        ace, hubspot = self._setup(requested="S-0050888", current=[])
        ace.associate_opportunity.side_effect = ACEAPIError(
            "already associated", code="ConflictException"
        )
        result = _handle_solution_diff(ace=ace, ace_id="O10000003", deal_id="123", hubspot=hubspot)
        # Conflict means AWS already has the association we wanted; the
        # call was logically a no-op. Don't fail.
        assert result["status"] == "updated"
        assert result.get("failures") == []


class TestGovwinIndustryHandler:
    """B1.1: govwin_industry handler must clear OtherIndustry when the new
    industry is a closed-enum value."""

    def test_change_from_other_to_closed_enum_drops_otherindustry(self) -> None:
        payload = {
            "Customer": {
                "Account": {"Industry": "Other", "OtherIndustry": "Defense Logistics"},
            },
            "Project": {},
            "LifeCycle": {},
        }
        result = _apply_delta(payload, "govwin_industry", "Government")
        assert result is True
        assert payload["Customer"]["Account"]["Industry"] == "Government"
        assert "OtherIndustry" not in payload["Customer"]["Account"]

    def test_empty_industry_is_no_op(self) -> None:
        payload = _empty_payload()
        result = _apply_delta(payload, "govwin_industry", "")
        assert result is False
        assert "Industry" not in payload["Customer"]["Account"]
