"""Tests for the HubSpot UI Extension callback Lambdas (reads + writes).

The submit_form_to_ace monolith was split into ui_extension_reads (GET
/solutions, GET /aws-products) and ui_extension_writes (POST /submit,
POST /update) with a shared _ui_extension_common helper module that
owns signature validation, CORS preflight, replay protection, and path
allowlisting. These tests cover all four routes by routing each event
to the right handler via the shim below; the test bodies stay
unchanged so the regression surface is identical to the pre-split
suite.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.lambdas import _ui_extension_common as common_mod
from src.lambdas import ui_extension_reads as reads_mod
from src.lambdas import ui_extension_writes as writes_mod


def _handler_for(event: dict[str, Any]):
    """Route the test event to the right Lambda handler.

    Mirrors API Gateway: GET /ui-extension/{solutions,aws-products} ->
    reads_mod; POST /ui-extension/{submit,update} -> writes_mod. The
    OPTIONS preflight path is detected ahead of routing so either
    Lambda answers it identically.
    """
    path = (
        event.get("requestContext", {}).get("http", {}).get("path")
        or event.get("rawPath")
        or event.get("path")
        or ""
    )
    if "/solutions" in path or "/aws-products" in path:
        return reads_mod.handler
    return writes_mod.handler


class _LambdaShim:
    """Backwards-compatible facade so existing tests that reference
    ``lambda_mod.<attr>`` continue to work after the Lambda split. The
    attribute lookup walks (reads_mod, writes_mod, common_mod) in order
    so a test that imported a function from the monolith still resolves.
    Attribute assignments (the kind ``monkeypatch.setattr`` issues for
    stubbing out SyncStateManager etc.) propagate to every module that
    declares the attribute so both reads_mod and writes_mod see the
    stub.
    """

    @staticmethod
    def handler(event, context=None):
        return _handler_for(event)(event, context)

    def __getattr__(self, name: str):
        for mod in (reads_mod, writes_mod, common_mod):
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        applied = False
        for mod in (reads_mod, writes_mod, common_mod):
            if hasattr(mod, name) or name in {"_aws_products_cache", "_secrets_client"}:
                # The two write-targets above are valid even when the
                # attribute is currently None on the module (cache resets).
                if hasattr(mod, name):
                    setattr(mod, name, value)
                    applied = True
        if not applied:
            # Tests sometimes monkeypatch.setattr unknown attributes; fall
            # back to assigning on the writes module so existing test
            # expectations of "the monolith module had this" succeed.
            setattr(writes_mod, name, value)


lambda_mod = _LambdaShim()

# Reuse the same secret across tests so signature math stays consistent.
SECRET = "0xCAFEBABE-not-a-real-hubspot-client-secret"
BASE_URL = "https://np1hq84j21.execute-api.us-east-1.amazonaws.com"


def _sign(method: str, url: str, body: bytes, ts_ms: int) -> str:
    raw = method.encode() + url.encode() + body + str(ts_ms).encode()
    return base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()


def _event(
    method: str,
    path: str,
    body: str = "",
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw = body.encode("utf-8") if body else b""
    ts_ms = int(time.time() * 1000)
    # Build the query string the way API Gateway HTTP API exposes it
    # (rawQueryString is a URL-encoded "a=b&c=d" with no leading "?").
    raw_query = ""
    if query:
        import urllib.parse

        raw_query = urllib.parse.urlencode(query)
    signed_url = BASE_URL + path + (f"?{raw_query}" if raw_query else "")
    sig = _sign(method, signed_url, raw, ts_ms)
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "rawPath": path,
        "rawQueryString": raw_query,
        "headers": {
            "X-HubSpot-Signature-v3": sig,
            "X-HubSpot-Request-Timestamp": str(ts_ms),
        },
        "queryStringParameters": query,
        "body": body,
        "isBase64Encoded": False,
    }


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.hubspot import signature as _sig

    _sig.clear_secret_cache()
    lambda_mod._aws_products_cache = None
    lambda_mod._solutions_cache.clear()
    lambda_mod._secrets_client = None

    monkeypatch.setenv("UI_EXTENSION_BASE_URL", BASE_URL)
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET_NAME", "govwin-hubspot/hubspot-webhook")
    monkeypatch.setenv("ACE_TRIGGER_STAGES", "3590200042")
    monkeypatch.setenv("ACE_CATALOG", "Sandbox")
    monkeypatch.setenv("AWS_USE_FIPS_ENDPOINT", "false")

    # Stub the replay-protection SyncStateManager at the handler level so
    # tests don't need to wire up DynamoDB. Every signed request hits
    # state.reserve_webhook_signature() before routing; an unmocked state
    # manager tries to talk to DDB and fails with NoCredentialsError.
    # Tests that need to assert on SyncStateManager behavior (dedup,
    # mapping lookup) patch over this with their own MagicMock.
    default_state = MagicMock()
    default_state.reserve_webhook_signature = MagicMock(return_value=True)
    default_state.get_ace_mapping = MagicMock(return_value=None)
    monkeypatch.setattr(lambda_mod, "SyncStateManager", MagicMock(return_value=default_state))


@pytest.fixture
def mock_secrets() -> Any:
    client = MagicMock()
    client.get_secret_value = MagicMock(
        return_value={"SecretString": json.dumps({"client_secret": SECRET})}
    )
    lambda_mod._secrets_client = client
    return client


@pytest.fixture
def mock_hubspot() -> Any:
    """Patch the HubSpotClient context manager used by _handle_submit."""
    hs = MagicMock()
    hs.update_deal = MagicMock(return_value={})

    @patch.object(lambda_mod, "HubSpotClient")
    def _wrapper(_mock_cls: MagicMock) -> Any:
        return hs

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=hs)
    cm.__exit__ = MagicMock(return_value=False)
    return hs, cm


# ------Signature validation------


class TestSignatureValidation:
    def test_rejects_missing_signature_headers(self, mock_secrets: Any) -> None:
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/ui-extension/submit"}},
            "rawPath": "/ui-extension/submit",
            "headers": {},
            "body": "{}",
        }
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 401

    def test_rejects_wrong_signature(self, mock_secrets: Any) -> None:
        event = _event("POST", "/ui-extension/submit", body="{}")
        event["headers"]["X-HubSpot-Signature-v3"] = "obviously-wrong"
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 401

    def test_accepts_valid_signature(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Use the GET /aws-products path because it has no further requirements.
        monkeypatch.setattr(lambda_mod, "_load_aws_products", lambda: [])
        event = _event("GET", "/ui-extension/aws-products")
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200


# ------GET /aws-products------


class TestAwsProductsEndpoint:
    def test_returns_bundled_catalog(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sample = [
            {"Identifier": "AWSLambda", "Name": "AWS Lambda", "Family": "Compute"},
            {"Identifier": "AmazonS3", "Name": "Amazon S3", "Family": "Storage"},
        ]
        # Force the cache directly so we don't depend on filesystem path inside
        # the test environment (zipping order matters in deployment).
        from src.models import AwsProductSummary

        lambda_mod._aws_products_cache = [AwsProductSummary.model_validate(p) for p in sample]
        event = _event("GET", "/ui-extension/aws-products")
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert len(body["products"]) == 2
        assert body["products"][0]["Identifier"] == "AWSLambda"


# ------GET /solutions------


class TestSolutionsEndpoint:
    def test_returns_active_solutions_for_catalog(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bypass ACEClient by stubbing _load_solutions.
        from src.models import SolutionSummary

        monkeypatch.setattr(
            lambda_mod,
            "_load_solutions",
            lambda catalog: [
                SolutionSummary(
                    Id="S-0051246",
                    Name="Pandora Cloud Professional Services",
                    Category="Professional Service",
                    Status="Active",
                )
            ],
        )
        # ?catalog=AWS is now intentionally IGNORED by _handle_solutions.
        # The Lambda always serves the server-trusted config.ace.catalog
        # (Sandbox in this test) regardless of what the client claims --
        # see the security review note about the original implementation
        # silently returning Sandbox data labeled "AWS" because the
        # underlying ACEClient ignored the parameter. We now reject the
        # client claim and trust env config.
        event = _event("GET", "/ui-extension/solutions", query={"catalog": "AWS"})
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["catalog"] == "Sandbox"
        assert body["solutions"][0]["Id"] == "S-0051246"

    def test_defaults_catalog_to_env_when_query_missing(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, str] = {}

        def _stub(catalog: str) -> list[Any]:
            captured["catalog"] = catalog
            return []

        monkeypatch.setattr(lambda_mod, "_load_solutions", _stub)
        event = _event("GET", "/ui-extension/solutions")
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        assert captured["catalog"] == "Sandbox"  # from env


# ------POST /submit------


def _good_payload() -> dict[str, Any]:
    return {
        "deal_id": "326365244126",
        "govwin_opp_id": "DEMO-TEST-001",
        "govwin_agency": "Test Agency",
        "govwin_industry": "Government",
        "description": "A meaningful description that is more than twenty characters long.",
        "dealname": "Test Opportunity",
        "amount": 36000.0,
        "closedate": "2026-12-31",
        "ace_partner_need": ["Deal Support"],
        "ace_delivery_model": ["Professional Services"],
        "ace_use_case": "Migration / Database Migration",
        "ace_opportunity_type": "Net New Business",
        "ace_sales_activities": ["Initialized discussions with customer"],
        "ace_aws_account_id": "555049241846",
    }


class TestSubmitEndpoint:
    def test_happy_path_returns_202(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Stub the dedup check to return no existing mapping.
        mock_state_mgr = MagicMock()
        mock_state_mgr.get_ace_mapping = MagicMock(return_value=None)
        monkeypatch.setattr(lambda_mod, "SyncStateManager", MagicMock(return_value=mock_state_mgr))
        # Stub HubSpotClient as a context manager.
        hs = MagicMock()
        hs.update_deal = MagicMock(return_value={})
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=hs)
        cm.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(lambda_mod, "HubSpotClient", MagicMock(return_value=cm))

        body = json.dumps(_good_payload())
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)

        assert response["statusCode"] == 202
        result = json.loads(response["body"])
        assert result["status"] == "queued"
        assert result["govwin_opp_id"] == "DEMO-TEST-001"

        # Two PATCHes: first properties, then dealstage flip.
        assert hs.update_deal.call_count == 2
        first_call = hs.update_deal.call_args_list[0]
        second_call = hs.update_deal.call_args_list[1]
        assert "govwin_opp_id" in first_call.args[1]
        assert first_call.args[1]["govwin_opp_id"] == "DEMO-TEST-001"
        assert second_call.args[1] == {"dealstage": "3590200042"}
        # closedate must be normalized from YYYY-MM-DD to UTC-midnight epoch ms.
        # 2026-12-31 00:00 UTC = 1798675200000 ms.
        assert first_call.args[1]["closedate"] == 1798675200000


class TestClosedateNormalization:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("2026-12-31", 1798675200000),
            ("2026-05-31", 1780185600000),
            ("1798675200000", 1798675200000),
            ("1798675200", 1798675200000),
            ("", ""),
        ],
    )
    def test_normalizes(self, raw: str, expected: Any) -> None:
        assert lambda_mod._closedate_to_epoch_ms(raw) == expected

    def test_unparseable_strings_pass_through(self) -> None:
        assert lambda_mod._closedate_to_epoch_ms("not-a-date") == "not-a-date"

    def test_rejects_invalid_govwin_opp_id(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _good_payload()
        payload["govwin_opp_id"] = "has spaces and / slashes"
        body = json.dumps(payload)
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 400
        result = json.loads(response["body"])
        assert any(e["field"] == "govwin_opp_id" for e in result["errors"])

    def test_rejects_invalid_aws_account_id(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _good_payload()
        payload["ace_aws_account_id"] = "12345"  # too short
        body = json.dumps(payload)
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 400
        result = json.loads(response["body"])
        assert any(e["field"] == "ace_aws_account_id" for e in result["errors"])

    def test_rejects_unknown_enum_value(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _good_payload()
        payload["ace_use_case"] = "Quantum Levitation"  # not in the AWS enum
        body = json.dumps(payload)
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 400
        result = json.loads(response["body"])
        assert any(e["field"] == "ace_use_case" for e in result["errors"])

    def test_rejects_too_many_aws_products(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _good_payload()
        payload["ace_aws_products"] = [f"AwsProduct{i:02d}" for i in range(21)]
        body = json.dumps(payload)
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 400
        result = json.loads(response["body"])
        assert any(e["field"] == "ace_aws_products" for e in result["errors"])

    def test_dedup_returns_409_with_existing_opportunity_id(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_state_mgr = MagicMock()
        mock_state_mgr.get_ace_mapping = MagicMock(return_value={"ace_opportunity_id": "O13740398"})
        monkeypatch.setattr(lambda_mod, "SyncStateManager", MagicMock(return_value=mock_state_mgr))
        body = json.dumps(_good_payload())
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 409
        result = json.loads(response["body"])
        assert result["status"] == "already_submitted"
        assert result["ace_opportunity_id"] == "O13740398"

    def test_rejects_short_description(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _good_payload()
        payload["description"] = "too short"
        body = json.dumps(payload)
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 400
        result = json.loads(response["body"])
        assert any(e["field"] == "description" for e in result["errors"])

    def test_national_security_requires_government_industry(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _good_payload()
        payload["govwin_industry"] = "Software and Internet"
        payload["ace_national_security"] = "Yes"
        body = json.dumps(payload)
        event = _event("POST", "/ui-extension/submit", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 400
        result = json.loads(response["body"])
        assert any(e["field"] == "ace_national_security" for e in result["errors"])


# ------Replay protection (B1.4)------


class TestReplayProtection:
    """A signed request replayed within the freshness window must be rejected
    with status='replay_detected' (distinct from the dedup 409 which uses
    status='already_submitted'). The form's 409 handler reads body.status
    to render the right copy; earlier it always assumed dedup and
    interpolated body.ace_opportunity_id (undefined on replay)."""

    def test_replay_returns_409_with_status_replay_detected(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the replay-protection dedup check to fail.
        replay_state = MagicMock()
        replay_state.reserve_webhook_signature = MagicMock(return_value=False)
        monkeypatch.setattr(lambda_mod, "SyncStateManager", MagicMock(return_value=replay_state))
        # GET /aws-products is enough to hit the replay check.
        event = _event("GET", "/ui-extension/aws-products")
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 409
        body = json.loads(response["body"])
        assert body["status"] == "replay_detected"
        # Critically: no ace_opportunity_id field (which the form would
        # otherwise try to interpolate into the "already submitted" copy).
        assert "ace_opportunity_id" not in body

    def test_fresh_signature_passes_through(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Default fixture's reserve_webhook_signature returns True.
        # Stub the products cache so the handler returns quickly.
        monkeypatch.setattr(lambda_mod, "_load_aws_products", lambda: [])
        event = _event("GET", "/ui-extension/aws-products")
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200


# ------POST /update integration tests (B3.2)------


def _good_update_payload() -> dict[str, Any]:
    return {
        "deal_id": "326811999945",
        "govwin_opp_id": "DEMO-TEST-001",
        "lifecycle_stage": "Qualified",
    }


def _stub_ace_get_opportunity_response() -> dict[str, Any]:
    return {
        "Id": "O13753208",
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
                    "StreetAddress": "123 Test Way",
                    "City": "Vandenberg",
                },
            },
        },
        "Project": {
            "Title": "Existing Project",
            "CustomerBusinessProblem": "x" * 30,
            "CustomerUseCase": "Migration / Database Migration",
            "DeliveryModels": ["Professional Services"],
            "ExpectedCustomerSpend": [
                {
                    "Amount": "1000.00",
                    "CurrencyCode": "USD",
                    "Frequency": "Monthly",
                    "TargetCompany": "PC",
                }
            ],
            "SalesActivities": ["Initialized discussions with customer"],
        },
        "LifeCycle": {
            "Stage": "Qualified",
            "ReviewStatus": "Submitted",
            "TargetCloseDate": "2026-12-31",
        },
        "RelatedEntityIdentifiers": {"AwsProducts": [], "Solutions": []},
    }


def _patch_update_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mapping: dict[str, Any] | None = None,
    get_opp_raises: Exception | None = None,
    update_raises: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Set up ACEClient + HubSpotClient + SyncStateManager mocks for /update.

    Returns (hubspot_mock, ace_mock) so tests can assert on call args.
    """
    state = MagicMock()
    state.reserve_webhook_signature = MagicMock(return_value=True)
    state.get_ace_mapping = MagicMock(return_value=mapping)
    state.update_ace_mapping = MagicMock()
    monkeypatch.setattr(lambda_mod, "SyncStateManager", MagicMock(return_value=state))

    ace = MagicMock()
    if get_opp_raises is not None:
        ace.get_opportunity.side_effect = get_opp_raises
    else:
        ace.get_opportunity.return_value = _stub_ace_get_opportunity_response()
    if update_raises is not None:
        ace.update_with_retry.side_effect = update_raises
    else:
        ace.update_with_retry.return_value = {
            "Id": "O13753208",
            "LastModifiedDate": "2026-05-27T16:00:00Z",
            "LifeCycle": {"ReviewStatus": "Submitted"},
        }
    monkeypatch.setattr(lambda_mod, "ACEClient", MagicMock(return_value=ace))

    hs = MagicMock()
    hs.update_deal = MagicMock(return_value={})
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=hs)
    cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(lambda_mod, "HubSpotClient", MagicMock(return_value=cm))
    return hs, ace


class TestUpdateEndpoint:
    """B3.2: /ui-extension/update had zero tests. These cover the
    happy path, all-empty payload, AWS errors with safe-message mapping,
    deal-id mismatch (409 path), and the mapping-deal-id backfill.
    """

    def test_happy_path_returns_200(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hs, ace = _patch_update_dependencies(
            monkeypatch,
            mapping={"ace_opportunity_id": "O13753208", "hubspot_deal_id": "326811999945"},
        )
        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        result = json.loads(response["body"])
        assert result["status"] == "updated"
        assert result["ace_opportunity_id"] == "O13753208"
        ace.update_with_retry.assert_called_once()
        # HubSpot writeback fired (cosell_status + lifecycle_stage).
        hs.update_deal.assert_called_once()

    def test_all_empty_payload_still_calls_update(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BD opens the form, picks the same Stage that's already on AWS,
        clicks Update without changing anything else. The mapper produces
        a payload identical to the scrubbed current; AWS still accepts
        the no-op UpdateOpportunity (PUT semantics preserve existing state)."""
        _, ace = _patch_update_dependencies(
            monkeypatch,
            mapping={"ace_opportunity_id": "O13753208", "hubspot_deal_id": "326811999945"},
        )
        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        ace.update_with_retry.assert_called_once()

    def test_get_opportunity_failure_returns_502(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.ace.client import ACEAPIError

        _patch_update_dependencies(
            monkeypatch,
            mapping={"ace_opportunity_id": "O13753208", "hubspot_deal_id": "326811999945"},
            get_opp_raises=ACEAPIError("GetOpportunity broken", code="ResourceNotFoundException"),
        )
        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 502
        result = json.loads(response["body"])
        # Safe-message mapping: BD sees a generic error, no AWS detail.
        assert (
            "could not load" in result["message"].lower()
            or "try again" in result["message"].lower()
        )

    def test_no_ace_mapping_returns_409(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Update before submit: no DDB mapping at all. 409 with message."""
        _patch_update_dependencies(monkeypatch, mapping={})
        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 409

    def test_mapping_deal_id_mismatch_returns_409(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DDB mapping points at a DIFFERENT HubSpot deal: refuse."""
        _patch_update_dependencies(
            monkeypatch,
            mapping={"ace_opportunity_id": "O13753208", "hubspot_deal_id": "999000999000"},
        )
        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 409
        result = json.loads(response["body"])
        assert "different" in result["message"].lower() or "999000999000" in result["message"]

    def test_mapping_missing_deal_id_triggers_backfill(
        self, mock_secrets: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy mapping rows lack hubspot_deal_id; the handler backfills."""
        state = MagicMock()
        state.reserve_webhook_signature = MagicMock(return_value=True)
        # Mapping has ace_opportunity_id but NO hubspot_deal_id.
        state.get_ace_mapping = MagicMock(return_value={"ace_opportunity_id": "O13753208"})
        state.update_ace_mapping = MagicMock()
        monkeypatch.setattr(lambda_mod, "SyncStateManager", MagicMock(return_value=state))

        ace = MagicMock()
        ace.get_opportunity.return_value = _stub_ace_get_opportunity_response()
        ace.update_with_retry.return_value = {
            "Id": "O13753208",
            "LastModifiedDate": "x",
            "LifeCycle": {"ReviewStatus": "Submitted"},
        }
        monkeypatch.setattr(lambda_mod, "ACEClient", MagicMock(return_value=ace))

        hs = MagicMock()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=hs)
        cm.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(lambda_mod, "HubSpotClient", MagicMock(return_value=cm))

        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        # Backfill was attempted (the call to update_ace_mapping with hubspot_deal_id).
        backfill_calls = [
            c
            for c in state.update_ace_mapping.call_args_list
            if c.kwargs.get("hubspot_deal_id") == "326811999945"
        ]
        assert backfill_calls, "Expected a backfill update_ace_mapping call with the deal_id"

    @pytest.mark.parametrize(
        "error_code, expected_phrase",
        [
            ("ValidationException", "invalid or missing"),
            ("ConflictException", "changed since"),
            ("AccessDeniedException", "lacks permission"),
            ("ThrottlingException", "rate-limiting"),
        ],
    )
    def test_safe_message_per_error_code(
        self,
        error_code: str,
        expected_phrase: str,
        mock_secrets: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.ace.client import ACEAPIError

        _patch_update_dependencies(
            monkeypatch,
            mapping={"ace_opportunity_id": "O13753208", "hubspot_deal_id": "326811999945"},
            update_raises=ACEAPIError("sensitive detail with PII", code=error_code),
        )
        body = json.dumps(_good_update_payload())
        event = _event("POST", "/ui-extension/update", body=body)
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 502
        result = json.loads(response["body"])
        # Safe message present.
        assert expected_phrase in result["message"].lower()
        # Sensitive AWS detail NOT in the response.
        assert "sensitive detail with PII" not in result["message"]
