"""CI drift guards for HubSpot enumeration property option sets.

For each ``govwin_ace_*`` property declared in
``src/hubspot/properties.py`` with ``type="enumeration"``, the option
``value`` field MUST equal the corresponding ``ALLOWED_*`` constant in
``src/ace/mapper.py``. The fan-out scenario this catches:

* AWS adds "AWS Marketing Central Offer" to Marketing.Source. (Already
  guarded against by test_ace_enums_drift.py.)
* Maintainer extends ``ALLOWED_MARKETING_SOURCES`` in the mapper.
* Maintainer forgets to extend the matching HubSpot property options.
* BD picks the new value in the form; ``_validate_enums`` passes; form
  PATCHes the property; HubSpot rejects with PROPERTY_DOESNT_EXIST
  options error. The form sees a 502 with no field hint.

This test pairs each enumeration property with its mapper constant and
asserts the symmetric difference is empty.

The HubSpot property declarations carry slightly noisier metadata than
the mapper (label, fieldType, description); we only compare the set of
option ``value`` strings.
"""

from __future__ import annotations

import pytest

from src.ace import mapper
from src.hubspot.properties import DEAL_PROPERTIES

# Map of HubSpot property name -> ALLOWED_* attribute name in src.ace.mapper.
# Only enumeration-typed properties with a closed ACE-side enum need to be
# listed. Free-text properties (govwin_ace_aws_products, *_use_cases) are
# not in this map; aws_products is seeded from resources/aws_products.json
# instead of an ACE enum.
_HUBSPOT_TO_ALLOWED: dict[str, str] = {
    # govwin_ace_partner_need is deliberately NOT in this map. The HubSpot
    # property stores the BD-readable short labels ("Deal Support");
    # ALLOWED_PRIMARY_NEEDS holds the AWS-canonical long form
    # ("Co-Sell - Deal Support"). _normalize_partner_need bridges them
    # at submit time. Tested separately below.
    "govwin_ace_delivery_model": "ALLOWED_DELIVERY_MODELS",
    "govwin_ace_use_case": "ALLOWED_CUSTOMER_USE_CASES",
    "govwin_ace_sales_activities": "ALLOWED_SALES_ACTIVITIES",
    "govwin_ace_competitor_name": "ALLOWED_COMPETITORS",
    "govwin_ace_marketing_channel": "ALLOWED_MARKETING_CHANNELS",
    "govwin_ace_marketing_source": "ALLOWED_MARKETING_SOURCES",
    "govwin_ace_marketing_dev_funded": "ALLOWED_FUNDING_USED",
    "govwin_ace_industry": "ALLOWED_INDUSTRIES",
    "govwin_ace_opportunity_type": "ALLOWED_OPPORTUNITY_TYPES",
    "govwin_ace_origin": "ALLOWED_ORIGINS",
    "govwin_ace_national_security": "ALLOWED_NATIONAL_SECURITY",
    "govwin_ace_closed_lost_reason": "ALLOWED_CLOSED_LOST_REASONS",
}


def _hubspot_property(name: str) -> object | None:
    for prop in DEAL_PROPERTIES:
        if prop.name == name:
            return prop
    return None


@pytest.mark.parametrize("hubspot_name, allowed_attr", sorted(_HUBSPOT_TO_ALLOWED.items()))
def test_hubspot_options_match_ace_allowed(hubspot_name: str, allowed_attr: str) -> None:
    prop = _hubspot_property(hubspot_name)
    if prop is None:
        # Some enums (Origin, Industry, etc.) may not be exposed as a
        # HubSpot deal property today. Skip rather than fail -- absence
        # is intentional, only DRIFT between declared HubSpot options
        # and the ACE enum is a real issue.
        pytest.skip(f"HubSpot property {hubspot_name} is not declared")
    if prop.type != "enumeration":  # type: ignore[attr-defined]
        pytest.skip(f"HubSpot property {hubspot_name} is type={prop.type!r}, not enumeration")  # type: ignore[attr-defined]
    options = prop.options or []  # type: ignore[attr-defined]
    hubspot_values = {opt["value"] for opt in options}
    allowed = set(getattr(mapper, allowed_attr))
    missing = allowed - hubspot_values
    extra = hubspot_values - allowed
    assert not missing, (
        f"HubSpot property {hubspot_name} is missing options that are in "
        f"mapper.{allowed_attr}: {sorted(missing)}. BD will submit a value "
        f"the form accepts but HubSpot's property PATCH rejects."
    )
    assert not extra, (
        f"HubSpot property {hubspot_name} has options NOT in mapper."
        f"{allowed_attr}: {sorted(extra)}. BD can pick a value the AWS "
        f"validator rejects."
    )


def test_partner_need_short_labels_bridge_to_aws_canonical() -> None:
    """govwin_ace_partner_need uses short labels; _normalize_partner_need
    must accept every HubSpot option and translate to a value that lives
    in ALLOWED_PRIMARY_NEEDS."""
    prop = _hubspot_property("govwin_ace_partner_need")
    if prop is None or prop.type != "enumeration":  # type: ignore[attr-defined]
        pytest.skip("partner_need not declared as enumeration")
    hubspot_values = [opt["value"] for opt in (prop.options or [])]  # type: ignore[attr-defined]
    for short_label in hubspot_values:
        translated = mapper._normalize_partner_need(short_label)
        assert translated in mapper.ALLOWED_PRIMARY_NEEDS, (
            f"HubSpot partner_need option {short_label!r} did not translate "
            f"to a value in ALLOWED_PRIMARY_NEEDS via _normalize_partner_need. "
            f"Either add the short->long pair to _PRIMARY_NEED_PAIRS or remove "
            f"the HubSpot option."
        )
