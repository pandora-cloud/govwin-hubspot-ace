"""Regression + edge-case tests for ``map_update_form_to_ace_payload``.

Before this file existed, the function was untested. The bug we fixed on
2026-05-27 was a real production regression: the mapper popped
``LifeCycle.ReviewStatus``/``ReviewComments``/``ReviewStatusReason``
which AWS treats as field clears under UpdateOpportunity PUT semantics.
The "ReviewStatus preserved through round-trip" test below is the
specific regression guard.

The function takes:
* ``form``: an UpdateFormRequest
* ``current``: GetOpportunity output (the scrub_for_update echo base)
* ``config``: AppConfig (catalog)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.ace.mapper import map_update_form_to_ace_payload
from src.models import SubmitFormMarketing, UpdateFormRequest


def _form(**overrides: object) -> UpdateFormRequest:
    """Build an UpdateFormRequest with sensible defaults for tests."""
    base: dict[str, object] = {
        "deal_id": "100000000001",
        "govwin_opp_id": "DEMO-TEST-001",
        "lifecycle_stage": "Qualified",
    }
    base.update(overrides)
    return UpdateFormRequest.model_validate(base)


def _current() -> dict:
    """Realistic GetOpportunity output with non-trivial existing state."""
    return {
        "Id": "O10000001",
        "PartnerOpportunityIdentifier": "DEMO-TEST-001",
        "LastModifiedDate": "2026-05-27T15:00:00Z",
        "PrimaryNeedsFromAws": ["Co-Sell - Deal Support"],
        "OpportunityType": "Net New Business",
        "NationalSecurity": "No",
        "Customer": {
            "Account": {
                "CompanyName": "USSF SLD45",
                "Industry": "Government",
                "WebsiteUrl": "https://example.mil",
                "Address": {
                    "CountryCode": "US",
                    "PostalCode": "12345",
                    "StateOrRegion": "California",
                    "StreetAddress": "123 Test Way",
                    "City": "Vandenberg",
                },
            },
            "Contacts": [
                {"FirstName": "Test", "LastName": "Contact", "Email": "t@example.mil"},
            ],
        },
        "Project": {
            "Title": "Existing Project Title",
            "CustomerBusinessProblem": "The existing customer business problem text "
            "must satisfy the 20-char minimum.",
            "CustomerUseCase": "Migration / Database Migration",
            "DeliveryModels": ["Professional Services"],
            "ExpectedCustomerSpend": [
                {
                    "Amount": "1000.00",
                    "CurrencyCode": "USD",
                    "Frequency": "Monthly",
                    "TargetCompany": "Pandora Cloud LLC",
                }
            ],
            "SalesActivities": ["Initialized discussions with customer"],
        },
        "LifeCycle": {
            "Stage": "Qualified",
            "TargetCloseDate": "2026-12-31",
            # The bug: these were being popped, which AWS treats as a clear.
            "ReviewStatus": "Submitted",
            "ReviewComments": "AWS reviewer thinks this is fine",
            "ReviewStatusReason": "Routine submission",
            "NextStepsHistory": [],
        },
    }


def _config() -> MagicMock:
    config = MagicMock()
    config.ace.catalog = "Sandbox"
    return config


# ---------------------------------------------------------------------------
# The headline regression
# ---------------------------------------------------------------------------


class TestReviewStatusPreserved:
    """The 2026-05-27 regression: the mapper popped LifeCycle.ReviewStatus
    et al., which AWS treats as a field clear under PUT semantics. The
    fix preserves them through the round-trip. Without this test, the
    bug could silently reappear under refactor."""

    def test_review_status_round_trips(self) -> None:
        form = _form()
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert payload["LifeCycle"]["ReviewStatus"] == "Submitted"

    def test_review_comments_round_trips(self) -> None:
        form = _form()
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert payload["LifeCycle"]["ReviewComments"] == "AWS reviewer thinks this is fine"

    def test_review_status_reason_round_trips(self) -> None:
        form = _form()
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert payload["LifeCycle"]["ReviewStatusReason"] == "Routine submission"


# ---------------------------------------------------------------------------
# LifeCycle stage transitions
# ---------------------------------------------------------------------------


class TestLifeCycleStage:
    def test_closed_lost_without_reason_drops_stage(self) -> None:
        """Defense-in-depth: if validation slips through and the form
        submits Stage=Closed Lost without a Reason, the mapper drops the
        Stage from the payload rather than send a known-bad payload."""
        form = _form(
            lifecycle_stage="Closed Lost",
            lifecycle_closed_lost_reason=None,
        )
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        # Stage was popped because there's no Reason.
        assert payload["LifeCycle"].get("Stage") != "Closed Lost"

    def test_closed_lost_with_reason_sets_both(self) -> None:
        form = _form(
            lifecycle_stage="Closed Lost",
            lifecycle_closed_lost_reason="Price",
        )
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert payload["LifeCycle"]["Stage"] == "Closed Lost"
        assert payload["LifeCycle"]["ClosedLostReason"] == "Price"

    def test_walking_back_from_closed_lost_clears_reason(self) -> None:
        """When the form sets Stage to something OTHER than Closed Lost,
        any existing ClosedLostReason in the AWS current state must be
        cleared from the payload (AWS rejects Reason without Closed-Lost Stage)."""
        current = _current()
        current["LifeCycle"]["Stage"] = "Closed Lost"
        current["LifeCycle"]["ClosedLostReason"] = "Price"
        form = _form(lifecycle_stage="Qualified")
        payload = map_update_form_to_ace_payload(form=form, current=current, config=_config())
        assert payload["LifeCycle"]["Stage"] == "Qualified"
        assert "ClosedLostReason" not in payload["LifeCycle"]


# ---------------------------------------------------------------------------
# Industry / OtherIndustry coupling
# ---------------------------------------------------------------------------


class TestIndustryCoupling:
    def test_industry_change_to_closed_enum_drops_otherindustry(self) -> None:
        current = _current()
        current["Customer"]["Account"]["Industry"] = "Other"
        current["Customer"]["Account"]["OtherIndustry"] = "Defense Logistics"
        form = _form(govwin_industry="Government")
        payload = map_update_form_to_ace_payload(form=form, current=current, config=_config())
        assert payload["Customer"]["Account"]["Industry"] == "Government"
        assert "OtherIndustry" not in payload["Customer"]["Account"]

    def test_industry_other_sets_otherindustry(self) -> None:
        form = _form(govwin_industry="Defense Logistics")
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert payload["Customer"]["Account"]["Industry"] == "Other"
        assert payload["Customer"]["Account"]["OtherIndustry"] == "Defense Logistics"


# ---------------------------------------------------------------------------
# Marketing block (Source=None must omit the block entirely)
# ---------------------------------------------------------------------------


class TestMarketingBlock:
    def test_marketing_source_none_omits_block(self) -> None:
        form = _form(marketing=SubmitFormMarketing(Source="None"))
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert "Marketing" not in payload

    def test_marketing_activity_emits_block(self) -> None:
        form = _form(
            marketing=SubmitFormMarketing(
                Source="Marketing Activity",
                Channels=["Email", "Live Event"],
                CampaignName="Q2 outreach",
                AwsFundingUsed="No",
            )
        )
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        assert payload["Marketing"]["Source"] == "Marketing Activity"
        assert payload["Marketing"]["Channels"] == ["Email", "Live Event"]
        assert payload["Marketing"]["CampaignName"] == "Q2 outreach"


# ---------------------------------------------------------------------------
# No-op safety
# ---------------------------------------------------------------------------


class TestNoOpSafety:
    def test_empty_form_preserves_current(self) -> None:
        """An UpdateFormRequest where the BD changed nothing should
        produce a payload that's effectively the scrubbed current state,
        with the Stage from the form (always required) overlaid."""
        form = _form()
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        # Customer account survives untouched.
        assert payload["Customer"]["Account"]["CompanyName"] == "USSF SLD45"
        assert payload["Project"]["Title"] == "Existing Project Title"
        assert payload["Project"]["CustomerUseCase"] == "Migration / Database Migration"
        # PartnerOpportunityIdentifier preserved from current (the GovWin
        # cross-reference is required on every Update).
        assert payload["PartnerOpportunityIdentifier"] == "DEMO-TEST-001"


# ---------------------------------------------------------------------------
# Negative cases for AwsAccountId placement
# ---------------------------------------------------------------------------


class TestAwsAccountId:
    def test_aws_account_id_sets_customer_account_field(self) -> None:
        form = _form(ace_aws_account_id="123456789012")
        payload = map_update_form_to_ace_payload(form=form, current=_current(), config=_config())
        # The 2026-05-26 fix: AwsAccountId belongs on Customer.Account, not Project.
        assert payload["Customer"]["Account"]["AwsAccountId"] == "123456789012"
        assert "AwsAccountId" not in payload.get("Project", {})

    def test_unspecified_aws_account_id_preserves_existing(self) -> None:
        current = _current()
        current["Customer"]["Account"]["AwsAccountId"] = "999999999999"
        form = _form()  # form.ace_aws_account_id is None
        payload = map_update_form_to_ace_payload(form=form, current=current, config=_config())
        # We don't overwrite a previously-set AwsAccountId when BD didn't
        # touch the field on the form.
        assert payload["Customer"]["Account"]["AwsAccountId"] == "999999999999"
