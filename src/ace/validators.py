"""Input validators for ACE-related identifiers.

Used at trust boundaries (HubSpot webhook events, DynamoDB pk construction,
URL path interpolation) so a malformed value cannot pivot into a
path-injection or DynamoDB key collision.
"""

from __future__ import annotations

import re

_HUBSPOT_OBJECT_ID = re.compile(r"^[0-9]+$")
_GOVWIN_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_AWS_OPPORTUNITY_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_AWS_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")


def is_valid_hubspot_object_id(value: str | None) -> bool:
    """HubSpot object ids are positive integers serialized as digit strings.

    Also caps length at 32 chars (HubSpot ids are ~12-15 digits today;
    32 is well above that and bounds the cost of any downstream scan or
    URL interpolation).
    """
    if not value:
        return False
    if len(value) > 32:
        return False
    return bool(_HUBSPOT_OBJECT_ID.match(value))


def is_valid_govwin_id(value: str | None) -> bool:
    """GovWin opp ids are alphanumeric (e.g. OPP263150, BID13141848)."""
    return bool(value) and bool(_GOVWIN_ID.match(value or ""))


def is_valid_aws_opportunity_id(value: str | None) -> bool:
    """AWS opportunity ids match ``[A-Za-z0-9_-]+`` (typically O[0-9]{15})."""
    return bool(value) and bool(_AWS_OPPORTUNITY_ID.match(value or ""))


def is_valid_aws_account_id(value: str | None) -> bool:
    """AWS account ids are exactly 12 digits.

    Used by submit_form_to_ace and the UI Extension form to validate
    ``govwin_ace_aws_account_id`` before placing it on
    ``Customer.Account.AwsAccountId`` in the CreateOpportunity payload.
    AWS does not document an explicit regex on this field but rejects
    anything that is not a 12-digit string.
    """
    return bool(value) and bool(_AWS_ACCOUNT_ID.match(value or ""))
