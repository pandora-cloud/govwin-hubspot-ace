"""Coverage for H2: audit-alert sourceId cross-check.

A property change on govwin_aws_cosell_id from an INTEGRATION source
should be considered "ours" ONLY when ev.sourceId matches the
configured HUBSPOT_INTEGRATION_APP_ID. Any other integration on the
same HubSpot portal writing to the property is operator-relevant and
fires a distinct "foreign integration" alert.
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

from src.lambdas import hubspot_webhook_receiver as receiver

SECRET = "topsecret"
TARGET_URL = "https://api.example.com/hubspot"
OUR_APP_ID = "12345678"


def _signed_headers(method: str, url: str, body: bytes) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    raw = method.encode() + url.encode() + body + ts.encode()
    sig = base64.b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()
    return {"x-hubspot-signature-v3": sig, "x-hubspot-request-timestamp": ts}


def _api_event(method: str, body: str, headers: dict[str, str]) -> dict:
    return {
        "requestContext": {
            "http": {"method": method, "path": "/hubspot"},
            "domainName": "api.example.com",
        },
        "rawPath": "/hubspot",
        "headers": headers,
        "body": body,
        "isBase64Encoded": False,
    }


@pytest.fixture(autouse=True)
def _reset_secret_cache():
    from src.hubspot import signature as _sig

    _sig.clear_secret_cache()
    yield
    _sig.clear_secret_cache()


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("HUBSPOT_WEBHOOK_TARGET_URL", TARGET_URL)
    monkeypatch.setenv("HUBSPOT_WEBHOOK_SECRET_NAME", "test/hubspot-webhook")
    monkeypatch.setenv("HUBSPOT_INTEGRATION_APP_ID", OUR_APP_ID)
    monkeypatch.setenv(
        "ACE_SUBMISSION_QUEUE_URL",
        "https://sqs.us-east-1.amazonaws.com/000000000000/test-ace-submit",
    )
    monkeypatch.setenv(
        "ACE_UPDATE_QUEUE_URL",
        "https://sqs.us-east-1.amazonaws.com/000000000000/test-ace-update",
    )


@pytest.fixture(autouse=True)
def _stub_state(monkeypatch):
    state = MagicMock()
    state.reserve_webhook_signature.return_value = True
    monkeypatch.setattr(receiver, "SyncStateManager", lambda *_a, **_kw: state)


@pytest.fixture
def mock_clients() -> tuple[Any, Any]:
    secrets = MagicMock()
    secrets.get_secret_value.return_value = {"SecretString": json.dumps({"client_secret": SECRET})}
    sqs = MagicMock()
    sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}], "Failed": []}
    with (
        patch.object(receiver, "_secrets_client", secrets),
        patch.object(receiver, "_sqs_client", sqs),
        patch.object(receiver, "_ensure_clients", lambda *_: None),
    ):
        yield secrets, sqs


def test_integration_event_from_our_app_does_not_alert(mock_clients) -> None:
    body = json.dumps(
        [
            {
                "objectId": 100000000001,
                "subscriptionType": "object.propertyChange",
                "propertyName": "govwin_aws_cosell_id",
                "propertyValue": "O10000001",
                "changeSource": "INTEGRATION",
                "sourceId": OUR_APP_ID,
            }
        ]
    )
    headers = _signed_headers("POST", TARGET_URL, body.encode())
    with patch("src.alerts.publish_alert") as mock_pub:
        response = receiver.handler(_api_event("POST", body, headers), context=None)
    assert response["statusCode"] == 200
    mock_pub.assert_not_called()
    body_json = json.loads(response["body"])
    assert body_json["audit"] == 0


def test_integration_event_from_foreign_app_alerts(mock_clients) -> None:
    body = json.dumps(
        [
            {
                "objectId": 100000000001,
                "subscriptionType": "object.propertyChange",
                "propertyName": "govwin_aws_cosell_id",
                "propertyValue": "O99999999",
                "changeSource": "INTEGRATION",
                "sourceId": "99999999",  # different integration app
            }
        ]
    )
    headers = _signed_headers("POST", TARGET_URL, body.encode())
    with patch("src.alerts.publish_alert") as mock_pub:
        response = receiver.handler(_api_event("POST", body, headers), context=None)
    assert response["statusCode"] == 200
    mock_pub.assert_called_once()
    call = mock_pub.call_args.kwargs
    assert "foreign HubSpot integration" in call["subject"]
    assert "99999999" in call["subject"]
    assert "INTEGRATION" in call["message"]
    body_json = json.loads(response["body"])
    assert body_json["audit"] == 1


def test_no_app_id_configured_accepts_any_integration(mock_clients, monkeypatch) -> None:
    """In test / local environments without HUBSPOT_INTEGRATION_APP_ID
    set, INTEGRATION events must not false-alert."""
    monkeypatch.delenv("HUBSPOT_INTEGRATION_APP_ID", raising=False)
    body = json.dumps(
        [
            {
                "objectId": 100000000001,
                "subscriptionType": "object.propertyChange",
                "propertyName": "govwin_aws_cosell_id",
                "propertyValue": "O10000001",
                "changeSource": "INTEGRATION",
                "sourceId": "anyone",
            }
        ]
    )
    headers = _signed_headers("POST", TARGET_URL, body.encode())
    with patch("src.alerts.publish_alert") as mock_pub:
        response = receiver.handler(_api_event("POST", body, headers), context=None)
    assert response["statusCode"] == 200
    mock_pub.assert_not_called()


def test_crm_ui_edit_still_alerts_with_hand_edit_subject(mock_clients) -> None:
    body = json.dumps(
        [
            {
                "objectId": 100000000001,
                "subscriptionType": "object.propertyChange",
                "propertyName": "govwin_aws_cosell_id",
                "propertyValue": "O99999999",
                "changeSource": "CRM_UI",
                "sourceId": "user-42",
            }
        ]
    )
    headers = _signed_headers("POST", TARGET_URL, body.encode())
    with patch("src.alerts.publish_alert") as mock_pub:
        receiver.handler(_api_event("POST", body, headers), context=None)
    mock_pub.assert_called_once()
    call = mock_pub.call_args.kwargs
    assert "hand-edited" in call["subject"]
    assert "CRM_UI" in call["message"]
