"""CI drift guards for ALLOWED_* enums in src.ace.mapper.

Same pattern as test_scrub_for_update_drift.py but applied to every closed-
enum constant we maintain. When AWS adds a new value to (say)
``Project.CompetitorName``, our ``ALLOWED_COMPETITORS`` set becomes a
subset; BD submits the new value via the form and ``_validate_enums``
rejects it. This test fails CI on that drift so the maintainer can
extend the set BEFORE a real submission breaks.

Skips gracefully when boto3 / botocore is unavailable (lint-only envs).
"""

from __future__ import annotations

import pytest

try:
    import boto3  # noqa: F401  -- imported to register the service model
    from botocore.session import Session

    _HAS_BOTOCORE = True
except ImportError:  # pragma: no cover
    _HAS_BOTOCORE = False


def _shape_for_path(model: object, path: list[str]) -> object:
    """Walk a dotted path into a botocore Shape, returning the leaf shape.

    Path segments are member names; a trailing ``[]`` on a name means the
    member is a list and we should descend into the list's ``.member``
    (the per-element shape). E.g. ``["Project", "DeliveryModels[]"]``
    -> ``CreateOpportunityRequest.Project.DeliveryModels.member``.
    """
    shape = model
    for segment in path:
        is_list = segment.endswith("[]")
        name = segment[:-2] if is_list else segment
        shape = shape.members[name]  # type: ignore[attr-defined]
        if is_list:
            shape = shape.member  # type: ignore[attr-defined]
    return shape


def _load_create_input_shape() -> object:
    session = Session()
    service_model = session.get_service_model("partnercentral-selling")
    return service_model.operation_model("CreateOpportunity").input_shape


@pytest.mark.skipif(not _HAS_BOTOCORE, reason="botocore not installed; lint-only environment")
@pytest.mark.parametrize(
    "constant_name, shape_path",
    [
        ("ALLOWED_DELIVERY_MODELS", ["Project", "DeliveryModels[]"]),
        ("ALLOWED_OPPORTUNITY_TYPES", ["OpportunityType"]),
        ("ALLOWED_ORIGINS", ["Origin"]),
        ("ALLOWED_NATIONAL_SECURITY", ["NationalSecurity"]),
        ("ALLOWED_MARKETING_SOURCES", ["Marketing", "Source"]),
        ("ALLOWED_MARKETING_CHANNELS", ["Marketing", "Channels[]"]),
        ("ALLOWED_FUNDING_USED", ["Marketing", "AwsFundingUsed"]),
        ("ALLOWED_COMPETITORS", ["Project", "CompetitorName"]),
        ("ALLOWED_INDUSTRIES", ["Customer", "Account", "Industry"]),
        ("ALLOWED_LIFECYCLE_STAGES", ["LifeCycle", "Stage"]),
        ("ALLOWED_CLOSED_LOST_REASONS", ["LifeCycle", "ClosedLostReason"]),
        ("ALLOWED_SOFTWARE_REVENUE_DELIVERY_MODELS", ["SoftwareRevenue", "DeliveryModel"]),
    ],
)
def test_enum_constant_matches_botocore(constant_name: str, shape_path: list[str]) -> None:
    from src.ace import mapper

    constant = set(getattr(mapper, constant_name))
    boto_shape = _shape_for_path(_load_create_input_shape(), shape_path)
    boto_values = set(boto_shape.enum)  # type: ignore[attr-defined]

    missing = boto_values - constant
    extra = constant - boto_values

    assert not missing, (
        f"{constant_name} is missing AWS-published values: {sorted(missing)}. "
        f"AWS path: {'.'.join(shape_path)}. Add them to src/ace/mapper.py."
    )
    assert not extra, (
        f"{constant_name} contains values not in the live boto3 model: "
        f"{sorted(extra)}. Either AWS removed them or there's a typo. "
        f"AWS path: {'.'.join(shape_path)}."
    )
