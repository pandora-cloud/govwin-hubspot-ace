"""Shared helpers for the HubSpot UI Extension callback Lambdas.

The four callback routes (GET /solutions, GET /aws-products, POST /submit,
POST /update) used to run on a single Lambda function (submit_form_to_ace).
Splitting into reads + writes Lambdas means each gets a much smaller IAM
role (the reads Lambda has no DynamoDB, no HubSpot private app token, no
Partner Central writes). This module factors out the signature validation,
path allowlisting, CORS preflight, and replay protection that both
Lambdas need to do identically.

Both Lambdas import :func:`serve_request` and pass in their own
``allowed_paths`` set and ``dispatch`` callable. ``serve_request`` returns
the final HTTP response or delegates to ``dispatch`` after all gates pass.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError
from pydantic import BaseModel

from src.aws_clients import make_client
from src.config import load_config
from src.hubspot.signature import (
    SignatureConfigError,
    get_signing_secret,
    validate_signature,
)
from src.models import FormFieldError, SubmitFormErrorResponse
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)

# 64 KiB. The form payload is well under 10 KiB even with a full
# 20-product diff and a populated Marketing block.
MAX_BODY_BYTES = 64 * 1024

# CORS Origin allowlist for the OPTIONS preflight reflection. HubSpot
# tenants live on a regional sub-host (NA1, EU1, JP1, AP1) and the iframe
# runs on the same host the user logged into. The card iframe usually
# goes through hubspot.fetch (same-origin), but the OPTIONS preflight
# still has to satisfy the browser.
_ALLOWED_HUBSPOT_ORIGINS: frozenset[str] = frozenset(
    {
        "https://app.hubspot.com",
        "https://app-na1.hubspot.com",
        "https://app-na2.hubspot.com",
        "https://app-eu1.hubspot.com",
        "https://app-eu2.hubspot.com",
        "https://app-jp1.hubspot.com",
        "https://app-ap1.hubspot.com",
        "https://app-na1-sandbox.hubspot.com",
    }
)


class _ConfigError(Exception):
    """Raised when the Lambda is missing required configuration."""


_secrets_client: Any | None = None


def ensure_clients(region: str) -> None:
    """Lazy-init the Secrets Manager client used for signature validation."""
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = make_client("secretsmanager", region)


def _lower(headers: dict[str, Any] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _resolve_route(event: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(method, path, raw_query_string)`` from the API Gateway HTTP API event."""
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()
    raw_path = (
        event.get("requestContext", {}).get("http", {}).get("path")
        or event.get("rawPath")
        or event.get("path")
        or ""
    )
    raw_query = event.get("rawQueryString") or ""
    return method, raw_path, raw_query


def ok(body: Any, status: int = 200) -> dict[str, Any]:
    if isinstance(body, BaseModel) or hasattr(body, "model_dump_json"):
        payload = body.model_dump_json(by_alias=True)
    else:
        payload = json.dumps(body)
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": payload,
    }


def err(
    status: int,
    body_status: str,
    *,
    message: str | None = None,
    errors: list[FormFieldError] | None = None,
) -> dict[str, Any]:
    body = SubmitFormErrorResponse(status=body_status, message=message, errors=errors or [])
    return ok(body, status=status)


def _required_target_url(path: str, query_string: str, allowed_paths: frozenset[str]) -> str:
    """Reconstruct the URL HubSpot signed against for this request.

    Path is validated against the per-Lambda allowlist BEFORE the URL is
    used for signature reconstruction so an attacker-controlled path
    can't forge a target_url that signature validation would happily
    accept.
    """
    base = os.environ.get("UI_EXTENSION_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise _ConfigError("UI_EXTENSION_BASE_URL is not configured")
    if not path.startswith("/"):
        path = "/" + path
    if path not in allowed_paths:
        raise _ConfigError(f"path not in allowlist: {path!r}")
    url = base + path
    if query_string:
        url = f"{url}?{query_string}"
    return url


def _validate_request_signature(
    method: str, raw_body: bytes, headers: dict[str, str], target_url: str
) -> tuple[bool, str | None]:
    signature = headers.get("x-hubspot-signature-v3", "")
    timestamp = headers.get("x-hubspot-request-timestamp", "")
    if not signature or not timestamp:
        return False, "missing signature"
    config = load_config()
    # ``ensure_clients`` is called at handler entry, but a code path that
    # reaches here without calling it would silently AttributeError on
    # ``None.get_secret_value``. The prior ``assert`` was stripped under
    # ``python -O``; this guard is explicit.
    if _secrets_client is None:
        ensure_clients(config.aws.region)
    try:
        secret = get_signing_secret(_secrets_client, config.aws.hubspot_webhook_secret_name)
    except SignatureConfigError as exc:
        logger.error("ui-extension signing secret unavailable: %s", exc)
        return False, "misconfigured"
    except ClientError as exc:
        logger.error("ui-extension secrets fetch failed: %s", exc)
        return False, "secret unavailable"
    ok_sig = validate_signature(
        method=method,
        url=target_url,
        raw_body=raw_body,
        signature_header=signature,
        timestamp_header=timestamp,
        secret=secret,
        max_age_seconds=config.ace.webhook_max_age_seconds,
    )
    if not ok_sig:
        if logger.isEnabledFor(logging.DEBUG):
            body_sha = hashlib.sha256(raw_body).hexdigest()[:16]
            logger.debug(
                "signature mismatch debug: method=%s url=%s body_len=%d body_sha=%s "
                "timestamp=%s signature_prefix=%s",
                method,
                target_url,
                len(raw_body),
                body_sha,
                timestamp,
                (signature or "")[:16],
            )
        else:
            logger.info("ui-extension: signature mismatch")
    return ok_sig, None if ok_sig else "invalid signature"


def _preflight_response(headers: dict[str, str]) -> dict[str, Any]:
    request_origin = headers.get("origin", "")
    allowed_origin = (
        request_origin if request_origin in _ALLOWED_HUBSPOT_ORIGINS else "https://app.hubspot.com"
    )
    return {
        "statusCode": 204,
        "headers": {
            "Access-Control-Allow-Origin": allowed_origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        },
    }


def serve_request(
    event: dict[str, Any],
    *,
    allowed_paths: frozenset[str],
    dispatch: Callable[[str, str, bytes, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run the shared pre-dispatch gates and delegate to the per-Lambda dispatcher.

    Gates in order:
      1. OPTIONS preflight -> 204 + CORS headers.
      2. Body size cap (64 KiB).
      3. Path allowlist (404 on unknown path; same status whether the
         caller hit /foo or /ui-extension/submit on the wrong Lambda).
      4. HubSpot signature validation against the reconstructed target URL.
      5. Replay protection via DynamoDB fingerprint reservation.

    Dispatch signature: ``(method, path, raw_body, event) -> response``.
    """
    config = load_config()
    ensure_clients(config.aws.region)
    method, path, raw_query = _resolve_route(event)
    headers = _lower(event.get("headers"))

    if method == "OPTIONS":
        return _preflight_response(headers)

    raw_body_str = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body_str)
    else:
        raw_body = raw_body_str.encode("utf-8")
    if len(raw_body) > MAX_BODY_BYTES:
        return err(413, "validation_failed", message="payload too large")

    try:
        target_url = _required_target_url(path, raw_query, allowed_paths)
    except _ConfigError as exc:
        logger.warning("ui-extension rejected: %s", exc)
        return err(404, "validation_failed", message="not found")

    sig_ok, sig_reason = _validate_request_signature(
        method=method, raw_body=raw_body, headers=headers, target_url=target_url
    )
    if not sig_ok:
        logger.warning("ui-extension rejected: %s", sig_reason)
        if sig_reason in ("secret unavailable", "misconfigured"):
            return err(500, "unauthorized", message="server error")
        return err(401, "unauthorized", message="unauthorized")

    sig_value = headers.get("x-hubspot-signature-v3", "")
    ts_value = headers.get("x-hubspot-request-timestamp", "")
    fingerprint = hashlib.sha256((sig_value + "|" + ts_value).encode("utf-8")).hexdigest()
    state = SyncStateManager(config)
    if not state.reserve_webhook_signature(
        fingerprint, ttl_seconds=config.ace.webhook_max_age_seconds
    ):
        logger.warning(
            "ui-extension rejected: replay detected for fingerprint=%s...",
            fingerprint[:12],
        )
        return err(409, "replay_detected", message="request was retried; ignored")

    return dispatch(method, path, raw_body, event)
