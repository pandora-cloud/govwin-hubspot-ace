"""Shared SNS alert helper for the GovWin -> HubSpot -> ACE pipeline.

Consolidates the per-Lambda ``_sns_client`` + ``_publish_*_alert`` pattern
that was duplicated across ``submit_to_ace.py``, ``update_in_ace.py``,
``handle_ace_event.py``, and ``submit_form_to_ace.py``.

Key behaviors:

* One process-wide SNS boto3 client, lazy-init on first publish.
* Best-effort: a publish failure logs the exception but does NOT
  propagate, so SQS / Lambda semantics for the calling code are
  unchanged.
* Error-detail redaction: AWS Partner Central validation messages
  routinely echo field values (customer names, competitor names,
  description excerpts). The SNS body would otherwise carry that to
  whatever email subscribers the topic feeds, which puts CUI in
  inboxes outside our control. ``error_detail`` is run through the
  same redactor as HubSpot 4xx bodies.
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from src.aws_clients import make_client
from src.hubspot.client import _redact_hubspot_error_body

logger = logging.getLogger(__name__)


_sns_client: Any | None = None


def publish_alert(
    *,
    config: Any,
    subject: str,
    message: str,
    error_detail: str | None = None,
) -> None:
    """Publish an SNS alert with optional redacted error detail appended.

    :param config: ``AppConfig`` (provides region + topic ARN).
    :param subject: SNS subject line; truncated to 100 chars per AWS limit.
    :param message: human-readable body. Keep concise; subscribers receive
        this verbatim via email/SMS depending on subscription type.
    :param error_detail: optional raw error string (typically an AWS or
        HubSpot exception ``str(exc)``). Redacted before inclusion so
        propertyValue and localizedErrorMessage echoes of customer-typed
        content don't reach inboxes.
    """
    topic_arn = config.aws.sns_topic_arn
    if not topic_arn:
        logger.info("alerts: no topic configured; skipping publish (subject=%r)", subject)
        return
    global _sns_client
    if _sns_client is None:
        _sns_client = make_client("sns", config.aws.region)
    body = message
    if error_detail:
        body = (
            body
            + "\n\nDetail (redacted of property values):\n"
            + _redact_hubspot_error_body(error_detail)
        )
    try:
        _sns_client.publish(
            TopicArn=topic_arn,
            Subject=subject[:100],
            Message=body,
        )
    except ClientError as exc:
        logger.exception("alerts: SNS publish failed (subject=%r): %s", subject, exc)


def clear_client_cache() -> None:
    """Drop the lazy SNS client. Intended for test teardown."""
    global _sns_client
    _sns_client = None
