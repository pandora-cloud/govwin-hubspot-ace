"""Targeted coverage for ui_extension_reads handler branches.

The reads Lambda's happy paths are exercised via test_ui_extension.py.
This file fills the remaining branches: missing aws_products.json,
list_active_solutions failing transiently, /aws-products file-cache
re-use, and the 405 dispatch when an unknown method hits an
allowlisted path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.lambdas import _ui_extension_common as common_mod
from src.lambdas import ui_extension_reads as reads_mod

SECRET = "0xCAFEBABE"
BASE_URL = "https://api.example.com"


def _sign(method: str, url: str, body: bytes, ts_ms: int) -> str:
    raw = method.encode() + url.encode() + body + str(ts_ms).encode()
    return base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()


def _event(method: str, path: str, body: str = "") -> dict[str, Any]:
    raw = body.encode("utf-8") if body else b""
    ts_ms = int(time.time() * 1000)
    sig = _sign(method, BASE_URL + path, raw, ts_ms)
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "rawPath": path,
        "rawQueryString": "",
        "headers": {
            "X-HubSpot-Signature-v3": sig,
            "X-HubSpot-Request-Timestamp": str(ts_ms),
        },
        "body": body,
        "isBase64Encoded": False,
    }


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.hubspot import signature as _sig

    _sig.clear_secret_cache()
    reads_mod._aws_products_cache = None
    reads_mod._solutions_cache.clear()
    common_mod._secrets_client = None
    monkeypatch.setenv("UI_EXTENSION_BASE_URL", BASE_URL)
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET_NAME", "test/hubspot-webhook")
    monkeypatch.setenv("ACE_CATALOG", "Sandbox")
    monkeypatch.setenv("AWS_USE_FIPS_ENDPOINT", "false")

    state = MagicMock()
    state.reserve_webhook_signature.return_value = True
    monkeypatch.setattr(common_mod, "SyncStateManager", lambda *_a, **_kw: state)


@pytest.fixture(autouse=True)
def _secrets_client() -> Any:
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": json.dumps({"client_secret": SECRET})}
    common_mod._secrets_client = client
    return client


def test_aws_products_file_missing_returns_empty_list(monkeypatch) -> None:
    """A missing resources/aws_products.json file must NOT crash; the
    handler returns an empty product list so the SubmitForm picker
    just shows no AWS products rather than rendering a 500."""
    from pathlib import Path

    monkeypatch.setattr(reads_mod, "AWS_PRODUCTS_PATH", Path("/nonexistent/path/no.json"))
    event = _event("GET", "/ui-extension/aws-products")
    response = reads_mod.handler(event, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["products"] == []


def test_aws_products_cache_reused_on_second_call(monkeypatch) -> None:
    """Second invocation serves from the in-memory cache without re-
    reading the file (Lambda init persistence). The pre-populated
    cache is the cheap way to simulate the warm state."""
    from src.models import AwsProductSummary

    sample = [AwsProductSummary.model_validate({
        "Identifier": "AmazonEC2Linux",
        "Name": "Amazon EC2",
        "Family": "Compute",
    })]
    monkeypatch.setattr(reads_mod, "_aws_products_cache", sample)
    # Point the path at /nonexistent to prove the file is NOT opened.
    monkeypatch.setattr(reads_mod, "AWS_PRODUCTS_PATH", "/nonexistent/no.json")

    response = reads_mod.handler(_event("GET", "/ui-extension/aws-products"), None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["products"]) == 1
    assert body["products"][0]["Identifier"] == "AmazonEC2Linux"


def test_list_active_solutions_failure_returns_empty(monkeypatch) -> None:
    """A transient AWS error during ListSolutions returns [] rather
    than 500. The SolutionPicker shows an empty dropdown, BD picks
    the cached default, the form still submits."""
    from src.ace.client import ACEAPIError

    class _ExplodingACE:
        def __init__(self, *_, **__) -> None:
            pass

        def list_active_solutions(self) -> list:
            raise ACEAPIError("Sandbox transient", code="ServiceUnavailableException")

    monkeypatch.setattr(reads_mod, "ACEClient", _ExplodingACE)

    response = reads_mod.handler(_event("GET", "/ui-extension/solutions"), None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["catalog"] == "Sandbox"
    assert body["solutions"] == []


def test_solutions_cache_does_not_poison_on_empty(monkeypatch) -> None:
    """An empty list (Sandbox catalog typical case) must NOT be cached:
    a transient failure that returns [] would otherwise persist the
    empty result for the full TTL window."""
    sample = [{"Id": "S-1", "Name": "S1", "Category": "Cat", "Status": "Active"}]
    call_count = [0]

    class _ACE:
        def __init__(self, *_, **__) -> None:
            pass

        def list_active_solutions(self) -> list:
            call_count[0] += 1
            return [] if call_count[0] == 1 else sample

    monkeypatch.setattr(reads_mod, "ACEClient", _ACE)

    # First call: empty list, NOT cached.
    response = reads_mod.handler(_event("GET", "/ui-extension/solutions"), None)
    assert json.loads(response["body"])["solutions"] == []
    # Second call: hits ACE again (empty was not cached) and returns real data.
    response = reads_mod.handler(_event("GET", "/ui-extension/solutions"), None)
    body = json.loads(response["body"])
    assert len(body["solutions"]) == 1
    assert call_count[0] == 2


def test_unknown_method_on_allowed_path_returns_405(monkeypatch) -> None:
    """A POST to /ui-extension/solutions (allowlisted but GET-only)
    should land in the _dispatch 405 branch. Signature verifies, path
    is allowlisted, but the handler refuses the method."""
    # signed_url omits the trailing slash; same shape the handler uses.
    body = ""
    ts_ms = int(time.time() * 1000)
    url = BASE_URL + "/ui-extension/solutions"
    raw = b"POST" + url.encode() + body.encode() + str(ts_ms).encode()
    sig = base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()
    event = {
        "requestContext": {"http": {"method": "POST", "path": "/ui-extension/solutions"}},
        "rawPath": "/ui-extension/solutions",
        "rawQueryString": "",
        "headers": {
            "X-HubSpot-Signature-v3": sig,
            "X-HubSpot-Request-Timestamp": str(ts_ms),
        },
        "body": body,
        "isBase64Encoded": False,
    }
    response = reads_mod.handler(event, None)
    assert response["statusCode"] == 405
