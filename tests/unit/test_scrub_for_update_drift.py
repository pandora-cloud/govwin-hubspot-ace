"""Drift guard for ``ACEClient.scrub_for_update`` against the boto3 model.

``UpdateOpportunity`` has PUT semantics: any field present in the boto3
input shape but NOT echoed back from ``scrub_for_update`` gets silently
cleared on the AWS side. We hit this on 2026-05-27 when a custom mapper
popped ``LifeCycle.ReviewStatus`` and wiped reviewer state on every
update.

The fix is two-part:
  1. Add new fields to ``UPDATE_OPPORTUNITY_ALLOWED_FIELDS`` whenever AWS
     extends the schema.
  2. This test, which fails CI when the boto3 model and our allowed set
     drift apart, so the engineer fixing a missed field doesn't have to
     wait for a BD complaint about a vanishing column.

Skipped if boto3 / botocore is unavailable (e.g., minimal lint envs).
"""

from __future__ import annotations

import pytest

try:
    import boto3  # noqa: F401  -- imported to register the service model
    from botocore.session import Session

    _HAS_BOTOCORE = True
except ImportError:  # pragma: no cover
    _HAS_BOTOCORE = False


def _update_input_top_level_fields() -> set[str]:
    """Return the top-level member names of UpdateOpportunity's input shape.

    Reads the live botocore service model.
    """
    session = Session()
    service_model = session.get_service_model("partnercentral-selling")
    op = service_model.operation_model("UpdateOpportunity")
    return set(op.input_shape.members.keys())


@pytest.mark.skipif(not _HAS_BOTOCORE, reason="botocore not installed; lint-only environment")
def test_scrub_allowed_matches_botocore_update_input_shape() -> None:
    from src.ace import client as ace_client

    boto_fields = _update_input_top_level_fields()
    allowed = ace_client.UPDATE_OPPORTUNITY_ALLOWED_FIELDS
    caller = ace_client.UPDATE_OPPORTUNITY_CALLER_FIELDS

    missing_from_allowed = boto_fields - allowed - caller
    extra_in_allowed = allowed - boto_fields

    assert not missing_from_allowed, (
        "AWS UpdateOpportunity has new top-level field(s) that our "
        "scrub_for_update allowlist doesn't echo. The next update on any "
        "existing opportunity will silently CLEAR these fields on the AWS "
        "side. Add them to UPDATE_OPPORTUNITY_ALLOWED_FIELDS in "
        f"src/ace/client.py: {sorted(missing_from_allowed)}"
    )

    assert not extra_in_allowed, (
        "scrub_for_update's allowlist references fields not present in "
        "the boto3 UpdateOpportunity input shape; boto3 will raise "
        "ParamValidationError when these are sent. Remove from "
        f"UPDATE_OPPORTUNITY_ALLOWED_FIELDS: {sorted(extra_in_allowed)}"
    )
