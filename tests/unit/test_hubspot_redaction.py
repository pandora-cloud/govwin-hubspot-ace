"""Tests for ``_redact_hubspot_error_body`` in src.hubspot.client.

HubSpot's property-validation responses echo the rejected value back in
``propertyValue`` and ``localizedErrorMessage``. The rejected value can
contain deal descriptions, customer names, and other CUI-eligible content
BD types into HubSpot. The redaction keeps the diagnostic skeleton
(error code, message, propertyName) and drops the value echoes so a
CloudWatch leak doesn't surface what was actually being submitted.
"""

from __future__ import annotations

import json

from src.hubspot.client import _redact_hubspot_error_body


def test_empty_body_returns_empty() -> None:
    assert _redact_hubspot_error_body("") == ""
    assert _redact_hubspot_error_body("   ") == ""


def test_non_json_html_truncated_no_parse() -> None:
    html = "<html><body>" + "x" * 800 + "</body></html>"
    out = _redact_hubspot_error_body(html)
    # Returned truncated to 512 with no JSON manipulation.
    assert out.startswith("<html>")
    assert len(out) <= 512


def test_malformed_json_falls_back_to_truncation() -> None:
    body = '{"errors": [{"message": "broken'  # unclosed
    out = _redact_hubspot_error_body(body)
    assert out.startswith('{"errors')


def test_redacts_property_value_field() -> None:
    body = json.dumps(
        {
            "status": "error",
            "errors": [
                {
                    "message": "value not allowed",
                    "code": "INVALID_OPTION",
                    "context": {"propertyName": ["govwin_ace_marketing_channel"]},
                    "propertyValue": "ConfidentialCustomerName;ProprietaryProgram",
                }
            ],
        }
    )
    out = _redact_hubspot_error_body(body)
    redacted = json.loads(out)
    assert redacted["errors"][0]["propertyValue"] == "<redacted>"
    # Diagnostic fields preserved.
    assert redacted["errors"][0]["code"] == "INVALID_OPTION"
    assert redacted["errors"][0]["message"] == "value not allowed"
    assert "ConfidentialCustomerName" not in out


def test_redacts_localized_error_message() -> None:
    body = json.dumps(
        {
            "errors": [
                {
                    "code": "INVALID_OPTION",
                    "localizedErrorMessage": "CUSTOMER_PII was not one of the allowed options",
                }
            ],
        }
    )
    out = _redact_hubspot_error_body(body)
    assert "CUSTOMER_PII" not in out
    assert "INVALID_OPTION" in out


def test_redacts_nested_property_value() -> None:
    body = json.dumps(
        {
            "validation": {
                "details": [
                    {"propertyValue": "secret1"},
                    {"propertyValue": "secret2"},
                ]
            },
        }
    )
    out = _redact_hubspot_error_body(body)
    assert "secret1" not in out
    assert "secret2" not in out
    assert out.count("<redacted>") == 2


def test_caps_redacted_output_to_1024() -> None:
    body = json.dumps({"message": "x" * 5000})
    out = _redact_hubspot_error_body(body)
    assert len(out) <= 1024
