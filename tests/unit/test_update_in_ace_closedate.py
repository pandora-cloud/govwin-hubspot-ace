"""Tests for the closedate normalization in update_in_ace._apply_delta.

HubSpot can deliver closedate as either YYYY-MM-DD or as a millisecond
epoch (legacy paths set ISO, the form's PATCH sets ms). AWS
``TargetCloseDate`` is strictly YYYY-MM-DD. Garbage values must NOT
land on the payload (would surface as a permanent ValidationException
in submit_to_ace, with the false-alarm SNS alert).
"""

from __future__ import annotations

import pytest

from src.lambdas.update_in_ace import _apply_delta


def _empty_payload() -> dict:
    return {"Project": {}, "LifeCycle": {}}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-12-31", "2026-12-31"),
        ("2026-12-31T00:00:00Z", "2026-12-31"),  # truncate at 10
        ("1798675200000", "2026-12-31"),  # ms epoch
        ("1798675200", "2026-12-31"),  # seconds epoch
    ],
)
def test_closedate_normalizes_to_iso_date(raw: str, expected: str) -> None:
    payload = _empty_payload()
    assert _apply_delta(payload, "closedate", raw) is True
    assert payload["LifeCycle"]["TargetCloseDate"] == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not-a-date",
        "20261231",  # no separators; <10 digits so not treated as epoch
        "abc/def/ghi",
        "0",  # 1-digit epoch is rejected (we require >=10)
        "12345",  # short numeric, rejected as ambiguous
    ],
)
def test_closedate_unparseable_is_dropped(raw: str) -> None:
    payload = _empty_payload()
    assert _apply_delta(payload, "closedate", raw) is False
    assert "TargetCloseDate" not in payload["LifeCycle"]


def test_closedate_whitespace_is_trimmed() -> None:
    payload = _empty_payload()
    assert _apply_delta(payload, "closedate", "  2026-12-31  ") is True
    assert payload["LifeCycle"]["TargetCloseDate"] == "2026-12-31"


def test_closedate_empty_is_no_op() -> None:
    payload = _empty_payload()
    assert _apply_delta(payload, "closedate", "") is False
    assert _apply_delta(payload, "closedate", None) is False


def test_closedate_overflow_is_dropped() -> None:
    payload = _empty_payload()
    # Year 9999 in epoch: 253402300800 sec = 253402300800000 ms.
    # Use something the platform can't represent.
    assert _apply_delta(payload, "closedate", "9" * 30) is False
