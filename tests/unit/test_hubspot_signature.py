"""Tests for the extracted HubSpot signature validator and signing-secret cache.

Covers the shared helpers in :mod:`src.hubspot.signature`. The webhook receiver
(``src.lambdas.hubspot_webhook_receiver``) and any future Lambda that accepts
signed callbacks from a HubSpot Developer Platform 2026.03 app both import these,
so the regression surface must be covered here.
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

from src.hubspot.signature import (
    SECRET_CACHE_TTL_SECONDS,
    SignatureConfigError,
    clear_secret_cache,
    get_signing_secret,
    validate_signature,
)

#------Fixtures------


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_secret_cache()


def _make_secrets_client(secret_string: str) -> Any:
    client = MagicMock()
    client.get_secret_value = MagicMock(return_value={"SecretString": secret_string})
    return client


def _make_signature(
    method: str,
    url: str,
    body: bytes,
    timestamp_ms: int,
    secret: str,
) -> str:
    raw = method.encode() + url.encode() + body + str(timestamp_ms).encode()
    return base64.b64encode(
        hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    ).decode()


#------get_signing_secret------


class TestGetSigningSecret:
    def test_returns_client_secret_from_snake_case_key(self) -> None:
        client = _make_secrets_client(json.dumps({"client_secret": "abc123"}))
        assert get_signing_secret(client, "test/secret") == "abc123"

    def test_returns_client_secret_from_camel_case_key(self) -> None:
        client = _make_secrets_client(json.dumps({"clientSecret": "abc123"}))
        assert get_signing_secret(client, "test/secret") == "abc123"

    def test_caches_secret_across_calls_within_ttl(self) -> None:
        client = _make_secrets_client(json.dumps({"client_secret": "abc123"}))
        get_signing_secret(client, "test/secret")
        get_signing_secret(client, "test/secret")
        get_signing_secret(client, "test/secret")
        assert client.get_secret_value.call_count == 1

    def test_re_fetches_after_ttl_expires(self) -> None:
        client = _make_secrets_client(json.dumps({"client_secret": "v1"}))
        get_signing_secret(client, "test/secret")
        # Force TTL expiry by mutating the cached timestamp.
        from src.hubspot.signature import _secret_cache

        cached_value, _ = _secret_cache["test/secret"]
        _secret_cache["test/secret"] = (cached_value, time.time() - SECRET_CACHE_TTL_SECONDS - 1)
        client.get_secret_value = MagicMock(
            return_value={"SecretString": json.dumps({"client_secret": "v2"})}
        )
        assert get_signing_secret(client, "test/secret") == "v2"

    def test_raises_on_non_json_secret(self) -> None:
        client = _make_secrets_client("not json")
        with pytest.raises(SignatureConfigError):
            get_signing_secret(client, "test/secret")

    def test_raises_when_secret_payload_missing_client_secret(self) -> None:
        client = _make_secrets_client(json.dumps({"some_other_key": "abc"}))
        with pytest.raises(SignatureConfigError):
            get_signing_secret(client, "test/secret")

    def test_raises_when_client_secret_is_empty_string(self) -> None:
        client = _make_secrets_client(json.dumps({"client_secret": ""}))
        with pytest.raises(SignatureConfigError):
            get_signing_secret(client, "test/secret")

    def test_raises_when_client_secret_is_not_a_string(self) -> None:
        client = _make_secrets_client(json.dumps({"client_secret": 42}))
        with pytest.raises(SignatureConfigError):
            get_signing_secret(client, "test/secret")


#------validate_signature------


class TestValidateSignature:
    def _common_args(self) -> dict[str, Any]:
        ts_ms = int(time.time() * 1000)
        body = b'{"event":"x"}'
        secret = "topsecret"
        sig = _make_signature("POST", "https://example.com/hook", body, ts_ms, secret)
        return {
            "method": "POST",
            "url": "https://example.com/hook",
            "raw_body": body,
            "signature_header": sig,
            "timestamp_header": str(ts_ms),
            "secret": secret,
        }

    def test_accepts_fresh_valid_signature(self) -> None:
        assert validate_signature(**self._common_args()) is True

    def test_rejects_tampered_body(self) -> None:
        args = self._common_args()
        args["raw_body"] = b'{"event":"y"}'
        assert validate_signature(**args) is False

    def test_rejects_wrong_secret(self) -> None:
        args = self._common_args()
        args["secret"] = "wrong"
        assert validate_signature(**args) is False

    def test_rejects_wrong_method(self) -> None:
        args = self._common_args()
        args["method"] = "PUT"
        assert validate_signature(**args) is False

    def test_rejects_wrong_url(self) -> None:
        args = self._common_args()
        args["url"] = "https://example.com/other"
        assert validate_signature(**args) is False

    def test_rejects_signature_older_than_window(self) -> None:
        args = self._common_args()
        ts_ms = int(time.time() * 1000) - 600_000  # 10 min ago
        body = args["raw_body"]
        args["timestamp_header"] = str(ts_ms)
        args["signature_header"] = _make_signature(
            "POST", args["url"], body, ts_ms, args["secret"]
        )
        assert validate_signature(max_age_seconds=300, **args) is False

    def test_rejects_signature_too_far_in_future(self) -> None:
        args = self._common_args()
        ts_ms = int(time.time() * 1000) + 600_000  # 10 min ahead
        body = args["raw_body"]
        args["timestamp_header"] = str(ts_ms)
        args["signature_header"] = _make_signature(
            "POST", args["url"], body, ts_ms, args["secret"]
        )
        assert validate_signature(max_age_seconds=300, **args) is False

    def test_rejects_non_numeric_timestamp(self) -> None:
        args = self._common_args()
        args["timestamp_header"] = "not-a-number"
        assert validate_signature(**args) is False

    def test_rejects_negative_timestamp(self) -> None:
        args = self._common_args()
        args["timestamp_header"] = "-1"
        assert validate_signature(**args) is False

    def test_default_max_age_is_300_seconds(self) -> None:
        args = self._common_args()
        ts_ms = int(time.time() * 1000) - 250_000  # 250s ago, within default 300s
        body = args["raw_body"]
        args["timestamp_header"] = str(ts_ms)
        args["signature_header"] = _make_signature(
            "POST", args["url"], body, ts_ms, args["secret"]
        )
        assert validate_signature(**args) is True
