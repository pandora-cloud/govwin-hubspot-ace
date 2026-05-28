"""Coverage for the expanded redactor (M3) in src.hubspot.client.

The redactor protects multiple egress paths from leaking customer-typed
content: CloudWatch logs (HubSpot 4xx error bodies), SNS alert bodies
(AWS PartnerCentral ValidationException strings), and HubSpot deal
property writeback (rejected-payload echoes that would be visible to
anyone with deal-read).
"""

from __future__ import annotations

import json

import pytest

from src.hubspot.client import _redact_hubspot_error_body


class TestRedactKeys:
    """Full-redact keys must be replaced wholesale; trim-keys are
    truncated; everything else passes through."""

    @pytest.mark.parametrize(
        "redact_key",
        [
            "propertyValue",
            "localizedErrorMessage",
            "CompanyName",
            "Email",
            "Phone",
            "WebsiteUrl",
            "Reason",
        ],
    )
    def test_full_redact_keys(self, redact_key: str) -> None:
        body = json.dumps({redact_key: "secret-customer-string", "code": "x"})
        out = _redact_hubspot_error_body(body)
        parsed = json.loads(out)
        assert parsed[redact_key] == "<redacted>"
        assert parsed["code"] == "x"

    @pytest.mark.parametrize("trim_key", ["message", "Message", "ErrorMessage"])
    def test_trim_keys_truncate(self, trim_key: str) -> None:
        long = "Customer ACME Corp rejected because " + ("x" * 500)
        body = json.dumps({trim_key: long, "code": "x"})
        out = _redact_hubspot_error_body(body)
        parsed = json.loads(out)
        assert len(parsed[trim_key]) <= 200
        assert parsed[trim_key] == long[:200]


class TestRedactNested:
    """The scrubber must walk dicts and lists recursively."""

    def test_nested_dict(self) -> None:
        body = json.dumps(
            {
                "errors": [
                    {"propertyName": "amount", "propertyValue": "PII-1"},
                    {"propertyName": "dealname", "propertyValue": "PII-2"},
                ]
            }
        )
        out = _redact_hubspot_error_body(body)
        parsed = json.loads(out)
        assert parsed["errors"][0]["propertyValue"] == "<redacted>"
        assert parsed["errors"][1]["propertyValue"] == "<redacted>"
        # propertyName is NOT in the redact set; should be preserved.
        assert parsed["errors"][0]["propertyName"] == "amount"

    def test_deeply_nested(self) -> None:
        body = json.dumps(
            {
                "outer": {
                    "inner": {"deeper": {"CompanyName": "Acme Inc"}},
                }
            }
        )
        out = _redact_hubspot_error_body(body)
        parsed = json.loads(out)
        assert parsed["outer"]["inner"]["deeper"]["CompanyName"] == "<redacted>"


class TestRedactNonJson:
    """Non-JSON bodies (HTML 503 page, plaintext, empty) trim to 512 and
    do not blow up."""

    def test_html_body_trimmed(self) -> None:
        html = "<html><body>503 Service Unavailable: " + ("x" * 600) + "</body></html>"
        out = _redact_hubspot_error_body(html)
        assert len(out) <= 512
        assert out.startswith("<html>")

    def test_empty_body(self) -> None:
        assert _redact_hubspot_error_body("") == ""
        assert _redact_hubspot_error_body(None) == ""  # type: ignore[arg-type]

    def test_invalid_json_trimmed(self) -> None:
        # Starts with { so it tries to parse; falls back on JSONDecodeError.
        broken = "{not really json: " + ("x" * 600)
        out = _redact_hubspot_error_body(broken)
        assert len(out) <= 512


class TestRedactOutputCap:
    """Even when the JSON path runs, the final output is capped at 1024."""

    def test_large_json_trimmed_to_1024(self) -> None:
        body = json.dumps({"a": "x" * 5000, "code": "x"})
        out = _redact_hubspot_error_body(body)
        assert len(out) <= 1024
