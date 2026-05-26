"""HubSpot ``X-HubSpot-Signature-v3`` validation and signing-secret retrieval.

Shared between the legacy webhook receiver (``src.lambdas.hubspot_webhook_receiver``)
and any new endpoint that accepts signed callbacks from a HubSpot Developer
Platform 2026.03 app, e.g. the UI Extension submit endpoint. Both code paths
need the same constant-time HMAC-SHA256 check against the app's client secret
and the same TTL'd Secrets Manager fetch.

The helpers here do not own a boto3 client. Callers pass one in via
:func:`get_signing_secret` so the module is friendly to:

* lazy Lambda init (clients constructed on first invoke, not at import),
* unit tests that pass a moto / stub client,
* multiple Lambdas in the same package sharing one cache across imports.

There is one process-wide ``_secret_cache`` so a warm Lambda container does
not re-fetch the secret every webhook delivery.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

#------Constants and module-level state------

SECRET_CACHE_TTL_SECONDS = 300

# Cache scoped to the process. Re-fetched after TTL or on explicit
# :func:`clear_secret_cache` (used by tests).
_secret_cache: dict[str, tuple[str, float]] = {}


class SignatureConfigError(Exception):
    """Raised when a webhook signing secret is missing or malformed in Secrets Manager."""


#------Public API------


def get_signing_secret(secrets_client: Any, secret_name: str) -> str:
    """Return the HubSpot app's signing secret (a.k.a. ``client_secret``).

    The signing secret is what HubSpot uses to compute ``X-HubSpot-Signature-v3``
    on every webhook and every ``hubspot.fetch()`` callback from a UI Extension.
    It is stored in AWS Secrets Manager as a JSON blob with either a
    ``client_secret`` or ``clientSecret`` key (both are accepted for
    historical reasons).

    :param secrets_client: A boto3 ``secretsmanager`` client. The caller owns
        construction; this keeps the function free of region/profile concerns
        and makes the cache deterministic in tests.
    :param secret_name: Fully qualified Secrets Manager secret id
        (e.g. ``"govwin-hubspot-prod/hubspot-webhook"``).
    :returns: The signing secret string.
    :raises SignatureConfigError: When the secret payload is not JSON or is
        missing both ``client_secret`` and ``clientSecret``.
    """
    cached = _secret_cache.get(secret_name)
    now = time.time()
    if cached and (now - cached[1]) < SECRET_CACHE_TTL_SECONDS:
        return cached[0]
    response = secrets_client.get_secret_value(SecretId=secret_name)
    raw = response.get("SecretString", "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SignatureConfigError("hubspot signing secret is not valid JSON") from exc
    secret = parsed.get("client_secret") or parsed.get("clientSecret")
    if not isinstance(secret, str) or not secret:
        raise SignatureConfigError("hubspot signing secret missing client_secret")
    _secret_cache[secret_name] = (secret, now)
    return secret


def validate_signature(
    *,
    method: str,
    url: str,
    raw_body: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: str,
    max_age_seconds: int = 300,
) -> bool:
    """Constant-time validation of an ``X-HubSpot-Signature-v3`` header.

    The HubSpot v3 scheme signs ``method || url || raw_body || timestamp_ms``
    with HMAC-SHA256 keyed on the app's client secret, base64-encodes the
    digest, and sends both the signature and the timestamp in headers. We
    recompute and compare in constant time, then reject anything outside
    the freshness window for replay protection.

    :param method: HTTP method as received by API Gateway (e.g. ``"POST"``).
    :param url: Full request URL as HubSpot saw it, including scheme and host
        but not headers.
    :param raw_body: Exact request body bytes (must not be re-encoded; HubSpot
        signs the bytes on the wire).
    :param signature_header: Value of ``X-HubSpot-Signature-v3``.
    :param timestamp_header: Value of ``X-HubSpot-Request-Timestamp`` (epoch
        milliseconds, integer-formatted).
    :param secret: The signing secret from :func:`get_signing_secret`.
    :param max_age_seconds: Freshness window in seconds. The default 300s
        (5 minutes) matches HubSpot's published guidance.
    :returns: ``True`` when the signature is valid AND the timestamp falls
        within the freshness window. ``False`` for any failure mode; callers
        should always reply 401 on a False without leaking the reason.
    """
    try:
        ts_ms = int(timestamp_header)
    except (TypeError, ValueError):
        return False
    if ts_ms <= 0:
        return False
    # Reject anything older than the policy window. Mild future-tolerance for
    # clock skew across HubSpot edge nodes.
    age_ms = time.time() * 1000 - ts_ms
    if age_ms > max_age_seconds * 1000 or age_ms < -max_age_seconds * 1000:
        return False
    raw = method.encode() + url.encode() + raw_body + timestamp_header.encode()
    expected = base64.b64encode(
        hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature_header)


def clear_secret_cache() -> None:
    """Drop the process-wide cache. Intended for test teardown."""
    _secret_cache.clear()
