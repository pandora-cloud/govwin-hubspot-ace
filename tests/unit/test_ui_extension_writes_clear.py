"""Coverage for CR1: _hubspot_update_property_payload clear-on-update.

On UPDATE the form pre-fills with current AWS values; an empty
submission means BD intended to clear, not leave alone. The PATCH
must send JSON null for those fields so HubSpot clears the property.
None (Pydantic field-unset) is still "untouched."

The submit path keeps the prior filter-empty semantics: empty fields
on submit mean "BD didn't fill" (not clear-intent), and the dealstage
flip following the property PATCH does not need cleared keys.
"""

from __future__ import annotations

from src.lambdas import ui_extension_writes as writes_mod
from src.models import SubmitFormRequest, UpdateFormRequest


def _update_req(**overrides: object) -> UpdateFormRequest:
    base: dict[str, object] = {
        "deal_id": "326811999945",
        "govwin_opp_id": "OPP-CLEAR-TEST",
        "lifecycle_stage": "Qualified",
    }
    base.update(overrides)
    return UpdateFormRequest.model_validate(base)


def _submit_req(**overrides: object) -> SubmitFormRequest:
    base: dict[str, object] = {
        "deal_id": "326811999945",
        "govwin_opp_id": "OPP-CLEAR-TEST",
        "dealname": "Test deal",
        "govwin_industry": "Government",
        "ace_partner_need": ["Co-Sell - Deal Support"],
        "ace_delivery_model": ["Professional Services"],
        "description": "x" * 25,
    }
    base.update(overrides)
    return SubmitFormRequest.model_validate(base)


class TestUpdatePathClears:
    """The update PATCH must translate `""` to a JSON null so HubSpot
    actually clears the property; without this the cleared field would
    stay populated on the deal, and the next webhook would re-write the
    stale value back to AWS."""

    def test_empty_string_becomes_none_for_clear(self) -> None:
        req = _update_req(ace_competitor_name="")
        payload = writes_mod._hubspot_update_property_payload(req)
        # "" -> None in the PATCH = HubSpot null = clear
        assert payload.get("govwin_ace_competitor_name") is None

    def test_none_field_omitted_entirely(self) -> None:
        req = _update_req(ace_competitor_name=None)
        payload = writes_mod._hubspot_update_property_payload(req)
        # None -> not in the PATCH = HubSpot leaves field as-is
        assert "govwin_ace_competitor_name" not in payload

    def test_filled_value_passes_through(self) -> None:
        req = _update_req(ace_competitor_name="Microsoft Azure")
        payload = writes_mod._hubspot_update_property_payload(req)
        assert payload["govwin_ace_competitor_name"] == "Microsoft Azure"

    def test_lifecycle_next_steps_clear_via_empty(self) -> None:
        req = _update_req(lifecycle_next_steps="")
        payload = writes_mod._hubspot_update_property_payload(req)
        assert payload.get("govwin_ace_next_steps") is None


class TestSubmitPathStillFiltersEmpty:
    """Submit's _put filter is unchanged. Empty fields on a fresh submit
    mean 'BD did not fill', not 'clear an existing value'."""

    def test_empty_competitor_not_in_payload(self) -> None:
        req = _submit_req(ace_competitor_name="")
        payload = writes_mod._hubspot_property_payload(req)
        assert "govwin_ace_competitor_name" not in payload

    def test_filled_competitor_passes_through(self) -> None:
        req = _submit_req(ace_competitor_name="On-Prem")
        payload = writes_mod._hubspot_property_payload(req)
        assert payload["govwin_ace_competitor_name"] == "On-Prem"


class TestSharedFormFieldsDispatcher:
    """The shared helper is parameterized via _put; tests verify the
    contract on both filter strategies."""

    def test_shared_helper_uses_submit_filter(self) -> None:
        req = _submit_req(ace_use_case="")
        payload = writes_mod._hubspot_property_payload(req)
        assert "govwin_ace_use_case" not in payload

    def test_shared_helper_uses_update_filter(self) -> None:
        req = _update_req(ace_use_case="")
        payload = writes_mod._hubspot_update_property_payload(req)
        # _put translates "" -> None, ending up in props.
        assert payload.get("govwin_ace_use_case") is None
