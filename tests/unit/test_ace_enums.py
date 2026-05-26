"""Boto3 drift detection for the closed enums in :mod:`src.ace.mapper`.

Each ALLOWED_* constant in mapper.py captures a closed enum that the AWS
Partner Central Selling API enforces on CreateOpportunity. If AWS adds,
removes, or renames a value in the boto3 service model and we keep
referencing the old set, the mapper will quietly produce payloads that
fail server-side validation only at submission time.

These tests pull the live boto3 service model and diff it against each
ALLOWED_* constant. Drift surfaces here in CI rather than in a production
SLD45-style outage.

Two enums in mapper.py are NOT covered here because they are server-only
(boto3 lists them as plain strings):

* ``ALLOWED_CUSTOMER_USE_CASES`` — the boto3 model has no enum; the audit
  doc explains that the list is sourced from ValidationException messages.
  See :mod:`tests.unit.test_ace_mapper` for use-case coverage.
* ``ALLOWED_OPPORTUNITY_TEAM_BUSINESS_TITLES`` — server-side enum we
  discovered via INVALID_VALUE error during the SLD45 submission. Covered
  separately below with a sentinel test so its origin is documented.
"""

from __future__ import annotations

import pytest

from src.ace import mapper


def _input_shape():
    import botocore.session

    return (
        botocore.session.Session()
        .get_service_model("partnercentral-selling")
        .operation_model("CreateOpportunity")
        .input_shape
    )


def _find_enum(path: str) -> set[str]:
    """Walk the CreateOpportunity input shape and return the enum at ``path``.

    ``path`` uses dotted member names with ``[]`` for list members, e.g.
    ``Project.DeliveryModels[]`` or ``LifeCycle.Stage``.
    """
    shape = _input_shape()
    segments = path.split(".")
    current = shape
    for seg in segments:
        is_list = seg.endswith("[]")
        if is_list:
            seg = seg[:-2]
        current = current.members[seg]
        if is_list:
            current = current.member
    assert getattr(current, "enum", None), f"no enum at path {path}"
    return set(current.enum)


@pytest.mark.parametrize(
    "constant,boto3_path",
    [
        (mapper.ALLOWED_PRIMARY_NEEDS, "PrimaryNeedsFromAws[]"),
        (mapper.ALLOWED_DELIVERY_MODELS, "Project.DeliveryModels[]"),
        (mapper.ALLOWED_INDUSTRIES, "Customer.Account.Industry"),
        (mapper.ALLOWED_SALES_ACTIVITIES, "Project.SalesActivities[]"),
        (mapper.ALLOWED_COMPETITORS, "Project.CompetitorName"),
        (mapper.ALLOWED_MARKETING_CHANNELS, "Marketing.Channels[]"),
        (mapper.ALLOWED_OPPORTUNITY_TYPES, "OpportunityType"),
        (mapper.ALLOWED_ORIGINS, "Origin"),
        (mapper.ALLOWED_NATIONAL_SECURITY, "NationalSecurity"),
        (mapper.ALLOWED_MARKETING_SOURCES, "Marketing.Source"),
        (mapper.ALLOWED_FUNDING_USED, "Marketing.AwsFundingUsed"),
        (mapper.ALLOWED_CLOSED_LOST_REASONS, "LifeCycle.ClosedLostReason"),
        (mapper.ALLOWED_LIFECYCLE_STAGES, "LifeCycle.Stage"),
        (mapper.ALLOWED_SOFTWARE_REVENUE_DELIVERY_MODELS, "SoftwareRevenue.DeliveryModel"),
    ],
)
def test_enum_matches_boto3_service_model(
    constant: frozenset[str] | set[str], boto3_path: str
) -> None:
    boto3_values = _find_enum(boto3_path)
    assert set(constant) == boto3_values, (
        f"Enum drift at {boto3_path}.\n"
        f"  missing from mapper: {sorted(boto3_values - set(constant))}\n"
        f"  unknown in mapper:   {sorted(set(constant) - boto3_values)}"
    )


def test_opportunity_team_business_title_documents_server_enum() -> None:
    """The OpportunityTeam[].BusinessTitle enum is server-only, not in boto3.

    Confirmed via INVALID_VALUE response from CreateOpportunity on
    2026-05-26 (SLD45 submission O13740398). If AWS later publishes this
    enum in the boto3 model, expand the parametrize above to include it.
    """
    assert mapper.ALLOWED_OPPORTUNITY_TEAM_BUSINESS_TITLES == frozenset({
        "PartnerAccountManager",
        "OpportunityOwner",
    })
