"""Tests for the submit_form_to_ace Lambda (UI Extension callback endpoint).

Covers all three routes (POST /submit, GET /solutions, GET /aws-products),
signature validation reuse, server-side enum re-validation, dedup against
the existing ACE mapping, and the two-phase HubSpot PATCH ordering
(properties first, then dealstage).
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

from src.lambdas import submit_form_to_ace as lambda_mod

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
    sig = _sign(method, BASE_URL + path, raw, ts_ms)
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "rawPath": path,
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


#------Signature validation------


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


#------GET /aws-products------


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

        lambda_mod._aws_products_cache = [
            AwsProductSummary.model_validate(p) for p in sample
        ]
        event = _event("GET", "/ui-extension/aws-products")
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert len(body["products"]) == 2
        assert body["products"][0]["Identifier"] == "AWSLambda"


#------GET /solutions------


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
        event = _event("GET", "/ui-extension/solutions", query={"catalog": "AWS"})
        response = lambda_mod.handler(event, context=None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["catalog"] == "AWS"
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


#------POST /submit------


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
        monkeypatch.setattr(
            lambda_mod, "SyncStateManager", MagicMock(return_value=mock_state_mgr)
        )
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
        mock_state_mgr.get_ace_mapping = MagicMock(
            return_value={"ace_opportunity_id": "O13740398"}
        )
        monkeypatch.setattr(
            lambda_mod, "SyncStateManager", MagicMock(return_value=mock_state_mgr)
        )
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
