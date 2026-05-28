"""Coverage for CR3: multi-value handler clear-warning logs.

AWS UpdateOpportunity does not accept empty arrays via this client's
PUT semantics, so a "BD cleared all values" intent on
``govwin_ace_partner_need``, ``govwin_ace_delivery_model``, or
``govwin_ace_sales_activities`` ends up as a no-op rather than a true
clear. The handlers log a WARNING so the no-op shows up in CloudWatch.
"""

from __future__ import annotations

import logging

import pytest

from src.lambdas import update_in_ace as update_mod


@pytest.mark.parametrize(
    "handler,prop_name",
    [
        (update_mod._handle_partner_need, "govwin_ace_partner_need"),
        (update_mod._handle_delivery_model, "govwin_ace_delivery_model"),
        (update_mod._handle_sales_activities, "govwin_ace_sales_activities"),
    ],
)
def test_empty_value_returns_false_and_warns(
    handler, prop_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    payload: dict = {}
    with caplog.at_level(logging.WARNING, logger="src.lambdas.update_in_ace"):
        result = handler(payload, "", "Partner Co")
    assert result is False
    # Payload was not mutated.
    assert payload == {}
    # WARNING surfaced.
    assert any(prop_name in rec.message for rec in caplog.records)
    assert any("no-op" in rec.message for rec in caplog.records)


@pytest.mark.parametrize(
    "handler,prop_name",
    [
        (update_mod._handle_partner_need, "govwin_ace_partner_need"),
        (update_mod._handle_delivery_model, "govwin_ace_delivery_model"),
        (update_mod._handle_sales_activities, "govwin_ace_sales_activities"),
    ],
)
def test_none_returns_false_and_warns(
    handler, prop_name: str, caplog: pytest.LogCaptureFixture
) -> None:
    payload: dict = {}
    with caplog.at_level(logging.WARNING, logger="src.lambdas.update_in_ace"):
        result = handler(payload, None, "Partner Co")
    assert result is False
    assert any(prop_name in rec.message for rec in caplog.records)


def test_partner_need_filled_returns_true_no_warn(caplog: pytest.LogCaptureFixture) -> None:
    payload: dict = {}
    with caplog.at_level(logging.WARNING, logger="src.lambdas.update_in_ace"):
        result = update_mod._handle_partner_need(
            payload, "Co-Sell - Deal Support;Co-Sell - Pricing Assistance", "Partner Co"
        )
    assert result is True
    assert payload["PrimaryNeedsFromAws"] == [
        "Co-Sell - Deal Support",
        "Co-Sell - Pricing Assistance",
    ]
    assert not any("no-op" in rec.message for rec in caplog.records)


def test_delivery_model_filled_returns_true_no_warn(caplog: pytest.LogCaptureFixture) -> None:
    payload: dict = {}
    with caplog.at_level(logging.WARNING, logger="src.lambdas.update_in_ace"):
        result = update_mod._handle_delivery_model(
            payload, "Professional Services", "Partner Co"
        )
    assert result is True
    assert payload["Project"]["DeliveryModels"] == ["Professional Services"]
    assert not any("no-op" in rec.message for rec in caplog.records)
