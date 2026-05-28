"""Receive HubSpot webhook deliveries, validate the signature, and enqueue.

Triggered by API Gateway HTTP API. Validates ``X-HubSpot-Signature-v3`` and
pushes events onto SQS for asynchronous processing. Must respond within the
documented 5-second budget so heavy lifting (CreateOpportunity etc.) happens
off the webhook critical path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any

from botocore.exceptions import ClientError

from src.aws_clients import make_client
from src.config import load_config
from src.hubspot.signature import (
    SignatureConfigError,
    get_signing_secret,
    validate_signature,
)
from src.lambdas._webhook_routing import classify_property_change
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


# Hard limits to keep DoS exposure bounded. HubSpot batches up to ~100
# events per delivery (per the developer docs), so 100 events / 1 MiB body
# is a generous ceiling that still constrains the Lambda runtime.
MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_EVENTS_PER_DELIVERY = 100
SQS_BATCH_SIZE = 10

_secrets_client: Any | None = None
_sqs_client: Any | None = None


def _ensure_clients(region: str) -> None:
    """Lazy-initialize boto3 clients (avoids no-region errors at import time).

    Includes the SNS client used by the audit-event path so the first
    audit publish does not pay boto3 + FIPS endpoint init inside the
    5-10s receiver budget.
    """
    global _secrets_client, _sqs_client
    if _secrets_client is None:
        _secrets_client = make_client("secretsmanager", region)
    if _sqs_client is None:
        _sqs_client = make_client("sqs", region)
    # Pre-warm the SNS client behind src.alerts so the first audit-event
    # publish on this container does not pay cold-init cost.
    from src.alerts import ensure_sns_client

    ensure_sns_client(region)


class _ConfigError(Exception):
    """Raised when a required webhook config value is missing or malformed."""


def _lower(headers: dict[str, Any] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _required_target_url() -> str:
    """Return the configured webhook URL or raise. The URL must match what
    HubSpot signed against, so we never reconstruct it from request headers.
    """
    url = os.environ.get("HUBSPOT_WEBHOOK_TARGET_URL", "").strip()
    if not url:
        raise _ConfigError("HUBSPOT_WEBHOOK_TARGET_URL is not configured")
    return url


# Sources HubSpot stamps on property changes that come from any
# integration token (not just ours). Used in two filters:
#
# 1. :func:`_route_event` drops update-class events whose changeSource
#    is INTEGRATION AND sourceId matches our own app id, breaking the
#    write -> webhook -> write feedback loop on the error-writeback
#    path in update_in_ace.
# 2. :func:`_process_audit_events` SNS-alerts on audit-class events
#    whose source is NOT INTEGRATION, OR whose sourceId does not match
#    our app (foreign-integration writes to integration-owned properties).
_INTEGRATION_CHANGE_SOURCES: frozenset[str] = frozenset({"INTEGRATION", "INTEGRATIONS_PLATFORM"})


def _route_event(ev: Any, *, our_app_id: str = "") -> str:
    """Decide whether an event belongs on submit / update / audit / drop.

    Routing is keyed off ``_webhook_routing.classify_property_change`` so
    the receiver and the deploy-time subscription registrar share the
    same canonical property list. Unknown properties are dropped.

    Update events that originated from our own integration are dropped
    instead of routed: re-processing a property we just wrote produces
    a feedback loop (writeback -> webhook -> update_in_ace -> UpdateOpp
    -> permanent error writeback -> ...). The audit path keeps its own
    sourceId check in ``_process_audit_events``; this filter only
    affects the update routing.

    :param ev: HubSpot webhook event dict.
    :param our_app_id: Configured ``HUBSPOT_INTEGRATION_APP_ID``. When
        empty (test / local), the integration-from-us filter is bypassed
        and all update events route normally.
    :returns: ``"submit"``, ``"update"``, ``"audit"``, or ``"drop"``.
    """
    if not isinstance(ev, dict):
        return "drop"
    if ev.get("subscriptionType") != "object.propertyChange":
        return "drop"
    target = classify_property_change(ev.get("propertyName"))
    if target == "update" and our_app_id:
        change_source = str(ev.get("changeSource") or "").upper()
        source_id = str(ev.get("sourceId") or "")
        if change_source in _INTEGRATION_CHANGE_SOURCES and source_id == our_app_id:
            return "drop"
    return target


def _send_sqs_batches(queue_url: str, events: list[Any]) -> int:
    """Enqueue events using SendMessageBatch (10 per call). Returns count sent."""
    if not events:
        return 0
    assert _sqs_client is not None
    sent = 0
    for offset in range(0, len(events), SQS_BATCH_SIZE):
        chunk = events[offset : offset + SQS_BATCH_SIZE]
        entries = [
            {
                "Id": str(offset + i),
                "MessageBody": json.dumps(ev),
                "MessageAttributes": {
                    "subscriptionType": {
                        "DataType": "String",
                        "StringValue": str(
                            (ev or {}).get("subscriptionType", "unknown")
                            if isinstance(ev, dict)
                            else "unknown"
                        ),
                    }
                },
            }
            for i, ev in enumerate(chunk)
        ]
        try:
            response = _sqs_client.send_message_batch(QueueUrl=queue_url, Entries=entries)
            sent += len(response.get("Successful", []))
            failed = response.get("Failed", [])
            if failed:
                logger.warning("sqs batch had %d failed entries", len(failed))
        except ClientError as exc:
            logger.exception("send_message_batch failed: %s", exc)
    return sent


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway HTTP API -> Lambda integration entry point."""
    config = load_config()
    _ensure_clients(config.aws.region)
    headers = _lower(event.get("headers"))
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "POST"
    ).upper()

    if method != "POST":
        return {"statusCode": 405, "body": "Method Not Allowed"}

    raw_body_str = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body_str)
    else:
        raw_body = raw_body_str.encode("utf-8")

    if len(raw_body) > MAX_BODY_BYTES:
        logger.warning("hubspot webhook rejected: body %d bytes exceeds cap", len(raw_body))
        return {"statusCode": 413, "body": "payload too large"}

    signature = headers.get("x-hubspot-signature-v3", "")
    timestamp = headers.get("x-hubspot-request-timestamp", "")
    if not signature or not timestamp:
        logger.warning("hubspot webhook rejected: missing signature/timestamp headers")
        return {"statusCode": 401, "body": "missing signature"}

    try:
        target_url = _required_target_url()
        secret = get_signing_secret(_secrets_client, config.aws.hubspot_webhook_secret_name)
    except (_ConfigError, SignatureConfigError) as exc:
        logger.error("hubspot webhook config error: %s", exc)
        return {"statusCode": 500, "body": "misconfigured"}
    except ClientError as exc:
        logger.error("Failed to fetch webhook signing secret: %s", exc)
        return {"statusCode": 500, "body": "secret unavailable"}

    if not validate_signature(
        method=method,
        url=target_url,
        raw_body=raw_body,
        signature_header=signature,
        timestamp_header=timestamp,
        secret=secret,
        max_age_seconds=config.ace.webhook_max_age_seconds,
    ):
        logger.warning("hubspot webhook rejected: signature mismatch")
        return {"statusCode": 401, "body": "invalid signature"}

    # Replay protection: signature is valid AND fresh, but the same signed
    # body could be replayed within the freshness window (HubSpot allows up
    # to 5 minutes for clock skew). We reserve a fingerprint in DynamoDB
    # with TTL equal to the freshness window; replays past that window
    # already fail the signature timestamp check, so a longer TTL provides
    # no marginal protection.
    #
    # We hash (signature, timestamp) rather than use the signature directly
    # so a leaked CloudWatch log cannot be used to fingerprint legitimate
    # requests; the fingerprint is one-way and rotates per request.
    fingerprint = hashlib.sha256((signature + "|" + timestamp).encode("utf-8")).hexdigest()
    state = SyncStateManager(config)
    if not state.reserve_webhook_signature(
        fingerprint, ttl_seconds=config.ace.webhook_max_age_seconds
    ):
        logger.warning(
            "hubspot webhook rejected: replay detected for fingerprint=%s...",
            fingerprint[:12],
        )
        return {"statusCode": 409, "body": "replay detected"}

    submit_queue = config.aws.ace_submission_queue_url
    update_queue = config.aws.ace_update_queue_url
    if not submit_queue or not update_queue:
        logger.error("ACE submission/update queue URLs are not configured")
        return {"statusCode": 500, "body": "misconfigured"}

    try:
        events = json.loads(raw_body.decode("utf-8")) if raw_body else []
    except json.JSONDecodeError:
        logger.warning("hubspot webhook rejected: invalid JSON body")
        return {"statusCode": 400, "body": "invalid json"}
    if not isinstance(events, list):
        events = [events]

    if len(events) > MAX_EVENTS_PER_DELIVERY:
        logger.warning("hubspot webhook rejected: %d events exceeds cap", len(events))
        return {"statusCode": 413, "body": "too many events"}

    submit_events: list[Any] = []
    update_events: list[Any] = []
    audit_events: list[Any] = []
    dropped = 0
    our_app_id = (os.environ.get("HUBSPOT_INTEGRATION_APP_ID") or "").strip()
    for ev in events:
        target = _route_event(ev, our_app_id=our_app_id)
        if target == "submit":
            submit_events.append(ev)
        elif target == "update":
            update_events.append(ev)
        elif target == "audit":
            audit_events.append(ev)
        else:
            dropped += 1

    enqueued_submit = _send_sqs_batches(submit_queue, submit_events)
    enqueued_update = _send_sqs_batches(update_queue, update_events)
    audited = _process_audit_events(audit_events, config=config)
    logger.info(
        "hubspot webhook accepted: submit=%d update=%d audit=%d dropped=%d",
        enqueued_submit,
        enqueued_update,
        audited,
        dropped,
    )
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "submit": enqueued_submit,
                "update": enqueued_update,
                "audit": audited,
                "dropped": dropped,
            }
        ),
    }


def _process_audit_events(events: list[Any], *, config: Any) -> int:
    """Inline SNS alert for hand-edits of audit-only deal properties.

    Audit events are handled inline in the receiver (no SQS detour)
    because they are rare, the receiver already loads the SNS topic
    via config, and the alert is purely advisory. A failure to publish
    is logged but does not fail the webhook response: HubSpot would
    retry the delivery, the integration source filter would re-evaluate
    on retry, and we'd alert twice instead of zero times; acceptable.
    """
    if not events:
        return 0
    from src.alerts import publish_alert

    expected_app_id = (os.environ.get("HUBSPOT_INTEGRATION_APP_ID") or "").strip()
    sent = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        change_source = str(ev.get("changeSource") or "").upper()
        source_id = str(ev.get("sourceId") or "")
        if change_source in _INTEGRATION_CHANGE_SOURCES and (
            not expected_app_id or source_id == expected_app_id
        ):
            # Legitimate write from our own Lambda (handle_ace_event
            # mirrors the AWS opp id onto the deal). Not an alert event.
            # When HUBSPOT_INTEGRATION_APP_ID is unset (test / local),
            # accept any INTEGRATION source to avoid false alerts.
            continue
        deal_id = str(ev.get("objectId") or "?")
        prop = str(ev.get("propertyName") or "?")
        new_value = str(ev.get("propertyValue") or "")[:80]
        is_foreign_integration = change_source in _INTEGRATION_CHANGE_SOURCES
        subject = (
            f"AWS Co-sell ID written by foreign HubSpot integration "
            f"(deal {deal_id}, app {source_id})"
            if is_foreign_integration
            else f"AWS Co-sell ID hand-edited on HubSpot deal {deal_id}"
        )
        try:
            publish_alert(
                config=config,
                subject=subject,
                message=(
                    "A HubSpot property the integration owns was changed by a "
                    "writer that is not this integration. The next deal save "
                    "on this record will trip the update_in_ace self-heal "
                    "verify and refuse the AWS write; this alert surfaces the "
                    "edit immediately so the operator can reconcile before BD "
                    "attempts another update.\n\n"
                    f"HubSpot deal id: {deal_id}\n"
                    f"Property: {prop}\n"
                    f"Change source: {change_source or 'unknown'}\n"
                    f"New value (first 80 chars): {new_value!r}\n"
                    f"Source id: {source_id!r}\n"
                    f"Expected app id: {expected_app_id or '<unset>'}"
                ),
            )
            sent += 1
        except Exception:  # noqa: BLE001 -- alert is advisory
            logger.exception(
                "audit alert publish failed for deal=%s prop=%s", deal_id, prop
            )
    return sent
