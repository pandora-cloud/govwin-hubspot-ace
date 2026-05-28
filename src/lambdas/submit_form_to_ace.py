"""HubSpot UI Extension callback endpoint.

Three HTTP routes on a single Lambda, all signed with
``X-HubSpot-Signature-v3`` exactly like the existing webhook receiver:

* ``POST /ui-extension/submit`` accepts a :class:`SubmitFormRequest`
  payload from the Submit-to-AWS card. We re-validate every enum
  server-side, dedup-check against the existing ACE mapping in
  DynamoDB, PATCH the deal properties in one batch, and finally
  PATCH the dealstage to the configured ACE trigger stage. From
  there the existing webhook -> SQS -> submit_to_ace pipeline drives
  the actual AWS API calls; the form path stops at the dealstage
  flip so audit history stays correct.
* ``GET /ui-extension/solutions`` returns the partner's catalog of
  Active Solutions for the configured ACE catalog. Used to populate
  the SolutionPicker dropdown.
* ``GET /ui-extension/aws-products`` returns the AWS Products catalog
  bundled in ``resources/aws_products.json``. Used to populate the
  AwsProductsPicker multi-select.

Signature validation reuses :mod:`src.hubspot.signature`. HubSpot's
``hubspot.fetch()`` API automatically signs the request using the
Developer Platform app's client secret, which is exactly the same
signing scheme the legacy webhook receiver consumes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from datetime import UTC
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError

from src.ace import mapper as ace_mapper
from src.ace.client import ACEAPIError, ACEClient
from src.ace.validators import (
    is_valid_aws_account_id,
    is_valid_govwin_id,
    is_valid_hubspot_object_id,
)
from src.aws_clients import make_client
from src.config import load_config
from src.hubspot.client import HubSpotAPIError, HubSpotClient
from src.hubspot.signature import (
    SignatureConfigError,
    get_signing_secret,
    validate_signature,
)
from src.models import (
    AwsProductListResponse,
    AwsProductSummary,
    FormFieldError,
    SolutionListResponse,
    SolutionSummary,
    SubmitFormErrorResponse,
    SubmitFormRequest,
    SubmitFormResponse,
    UpdateFormRequest,
    UpdateFormResponse,
)
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ------Constants------

# 64 KiB. The form payload is well under 10 KiB even with a full
# 20-product diff and a populated Marketing block. Tight bound limits
# the cost of a malicious or runaway client sending oversize bodies.
MAX_BODY_BYTES = 64 * 1024
RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
AWS_PRODUCTS_PATH = RESOURCES_DIR / "aws_products.json"

# Cache the AWS Products file in memory once loaded; refresh requires a
# Lambda redeploy because the JSON ships in the deployment zip.
_aws_products_cache: list[AwsProductSummary] | None = None

# Solutions cache. ACE read quota is 10/sec so we don't strictly need
# this, but a 5-minute TTL avoids burning quota on every form open.
_solutions_cache: dict[str, tuple[list[SolutionSummary], float]] = {}
_SOLUTIONS_TTL_SECONDS = 300

_secrets_client: Any | None = None


def _ensure_clients(region: str) -> None:
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = make_client("secretsmanager", region)


class _ConfigError(Exception):
    pass


# ------HTTP helpers------


# Exact paths the Lambda will accept. Anything else is rejected before
# signature validation; route matching no longer uses endswith() which
# would have matched a crafted path like "/foo/ui-extension/submit".
_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/ui-extension/submit",
        "/ui-extension/update",
        "/ui-extension/solutions",
        "/ui-extension/aws-products",
    }
)

# CORS Origin allowlist for the OPTIONS preflight reflection. HubSpot
# tenants live on a regional sub-host (NA1, EU1, JP1, AP1) and the iframe
# runs on the same host the user logged into. The CORS reflection lets
# users in any HubSpot region's portal load the card without bouncing
# off a preflight failure. The legacy NA1 default stays in place for
# unrecognized Origins (defensive: never echo a header we did not
# explicitly allow).
_ALLOWED_HUBSPOT_ORIGINS: frozenset[str] = frozenset(
    {
        "https://app.hubspot.com",
        "https://app-na1.hubspot.com",
        "https://app-na2.hubspot.com",
        "https://app-eu1.hubspot.com",
        "https://app-eu2.hubspot.com",
        "https://app-jp1.hubspot.com",
        "https://app-ap1.hubspot.com",
        # Sandbox / developer-platform variants observed in 2025.2+ UI
        # extension previews.
        "https://app-na1-sandbox.hubspot.com",
        "https://app.hubspotqa.com",
    }
)


def _required_target_url(path: str, query_string: str = "") -> str:
    """Return the full URL HubSpot signed against for this request.

    HubSpot's signature scheme covers ``method || url || raw_body || timestamp``
    so the validator needs the exact URL the UI Extension called via
    ``hubspot.fetch()``. ``hubspot.fetch`` signs the URL WITH its query
    string (e.g. ``...?catalog=Sandbox``), so we must reconstruct it the
    same way or the HMAC compare returns false and we 401 every GET that
    carries query params (SolutionPicker, AwsProductsPicker).

    Composed from the API Gateway base URL (env var; same value for all
    routes on this Lambda) and the path observed by API Gateway. The
    path is validated against :data:`_ALLOWED_PATHS` BEFORE the URL is
    used for signature reconstruction so attacker-controlled paths can't
    forge a target_url that signature validation would happily accept.
    """
    base = os.environ.get("UI_EXTENSION_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise _ConfigError("UI_EXTENSION_BASE_URL is not configured")
    if not path.startswith("/"):
        path = "/" + path
    if path not in _ALLOWED_PATHS:
        raise _ConfigError(f"path not in allowlist: {path!r}")
    url = base + path
    if query_string:
        url = f"{url}?{query_string}"
    return url


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


def _ok(body: Any, status: int = 200) -> dict[str, Any]:
    if hasattr(body, "model_dump_json"):
        # Serialize Pydantic responses by alias so the wire format uses the
        # AWS-canonical field names (Id, Name, Category, Identifier, ...).
        payload = body.model_dump_json(by_alias=True)
    else:
        payload = json.dumps(body)
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": payload,
    }


def _err(
    status: int,
    body_status: str,
    *,
    message: str | None = None,
    errors: list[FormFieldError] | None = None,
) -> dict[str, Any]:
    body = SubmitFormErrorResponse(status=body_status, message=message, errors=errors or [])
    return _ok(body, status=status)


# ------Signature validation------


def _validate_request_signature(
    method: str, raw_body: bytes, headers: dict[str, str], target_url: str
) -> tuple[bool, str | None]:
    signature = headers.get("x-hubspot-signature-v3", "")
    timestamp = headers.get("x-hubspot-request-timestamp", "")
    if not signature or not timestamp:
        return False, "missing signature"
    config = load_config()
    try:
        assert _secrets_client is not None
        secret = get_signing_secret(_secrets_client, config.aws.hubspot_webhook_secret_name)
    except SignatureConfigError as exc:
        logger.error("ui-extension signing secret unavailable: %s", exc)
        return False, "misconfigured"
    except ClientError as exc:
        logger.error("ui-extension secrets fetch failed: %s", exc)
        return False, "secret unavailable"
    ok = validate_signature(
        method=method,
        url=target_url,
        raw_body=raw_body,
        signature_header=signature,
        timestamp_header=timestamp,
        secret=secret,
        max_age_seconds=config.ace.webhook_max_age_seconds,
    )
    if not ok:
        # Detailed mismatch context is useful for diffing against the
        # HubSpot signer, but at INFO level it gives any attacker grinding
        # the endpoint a precise oracle (the URL we hash against + a body
        # sha they can fingerprint). Gate behind LOG_LEVEL=DEBUG so the
        # data is recoverable when the operator opts in but never leaked
        # by default. At INFO we log only the structural fact.
        if logger.isEnabledFor(logging.DEBUG):
            import hashlib as _hashlib

            body_sha = _hashlib.sha256(raw_body).hexdigest()[:16]
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
    return ok, None if ok else "invalid signature"


# ------Enum validation for /submit------


def _validate_enums(req: SubmitFormRequest) -> list[FormFieldError]:
    """Server-side check that every closed-enum field matches the AWS source of truth.

    The form is supposed to enforce this client-side via the generated enums
    file, but we re-check on the server so a bypassed client cannot produce
    a payload AWS will reject (or worse, accept silently into the wrong field).
    """
    errors: list[FormFieldError] = []

    def _check(field: str, value: str | None, allowed: set[str] | frozenset[str]) -> None:
        if value is None or value == "":
            return
        if value not in allowed:
            errors.append(
                FormFieldError(
                    field=field, message=f"value {value!r} is not in the AWS-published enum"
                )
            )

    def _check_list(field: str, values: list[str], allowed: set[str] | frozenset[str]) -> None:
        for v in values:
            if v not in allowed:
                errors.append(
                    FormFieldError(
                        field=field,
                        message=f"value {v!r} is not in the AWS-published enum",
                    )
                )
                return  # one error per field is plenty for the form UI

    # Translate HubSpot short labels for PartnerNeed before checking against the
    # AWS-canonical enum, so the form can submit either form.
    translated_needs = [ace_mapper._normalize_partner_need(n) for n in req.ace_partner_need]
    _check_list("ace_partner_need", translated_needs, ace_mapper.ALLOWED_PRIMARY_NEEDS)
    _check_list("ace_delivery_model", req.ace_delivery_model, ace_mapper.ALLOWED_DELIVERY_MODELS)
    _check_list(
        "ace_sales_activities",
        req.ace_sales_activities,
        ace_mapper.ALLOWED_SALES_ACTIVITIES,
    )
    _check("ace_use_case", req.ace_use_case, ace_mapper.ALLOWED_CUSTOMER_USE_CASES)
    _check(
        "ace_opportunity_type",
        req.ace_opportunity_type,
        ace_mapper.ALLOWED_OPPORTUNITY_TYPES,
    )
    _check(
        "ace_competitor_name",
        req.ace_competitor_name,
        ace_mapper.ALLOWED_COMPETITORS,
    )
    _check(
        "ace_national_security",
        req.ace_national_security,
        ace_mapper.ALLOWED_NATIONAL_SECURITY,
    )

    # Cross-field rule: AWS rejects NationalSecurity=Yes unless Industry=Government.
    if (req.ace_national_security or "").strip() == "Yes" and (
        req.govwin_industry or ""
    ).strip().lower() not in {"government", ""}:
        errors.append(
            FormFieldError(
                field="ace_national_security",
                message="NationalSecurity=Yes is only valid when Industry=Government",
            )
        )

    if req.marketing is not None:
        _check(
            "marketing.source",
            req.marketing.source,
            ace_mapper.ALLOWED_MARKETING_SOURCES,
        )
        _check(
            "marketing.aws_funding_used",
            req.marketing.aws_funding_used,
            ace_mapper.ALLOWED_FUNDING_USED,
        )
        if req.marketing.channels:
            _check_list(
                "marketing.channels",
                req.marketing.channels,
                ace_mapper.ALLOWED_MARKETING_CHANNELS,
            )

    if req.ace_aws_account_id and not is_valid_aws_account_id(req.ace_aws_account_id):
        errors.append(
            FormFieldError(
                field="ace_aws_account_id",
                message="AWS account id must be 12 digits",
            )
        )

    if len(req.ace_aws_products) > ace_mapper.MAX_AWS_PRODUCTS_PER_OPPORTUNITY:
        errors.append(
            FormFieldError(
                field="ace_aws_products",
                message=(
                    "AWS Products limit is "
                    f"{ace_mapper.MAX_AWS_PRODUCTS_PER_OPPORTUNITY} per opportunity"
                ),
            )
        )

    if not req.ace_partner_need:
        errors.append(
            FormFieldError(
                field="ace_partner_need",
                message="At least one PartnerNeed is required",
            )
        )
    if not req.ace_delivery_model:
        errors.append(
            FormFieldError(
                field="ace_delivery_model",
                message="At least one DeliveryModel is required",
            )
        )
    if req.description is not None and 0 < len(req.description) < 20:
        errors.append(
            FormFieldError(
                field="description",
                message="Description must be at least 20 characters when provided",
            )
        )

    # Catch closedate parse failures up-front so BD sees a field-level
    # error instead of a downstream HubSpot 400 surfaced as a generic
    # 502. _closedate_to_epoch_ms returns the raw string on parse
    # failure; we re-run the conversion here and flag if it didn't
    # produce a numeric epoch.
    if req.closedate:
        normalized = _closedate_to_epoch_ms(req.closedate)
        if not isinstance(normalized, int):
            errors.append(
                FormFieldError(
                    field="closedate",
                    message="Close date must be YYYY-MM-DD or an epoch number",
                )
            )

    return errors


# ------/submit handler------


def _apply_shared_form_fields(
    req: Any,
    props: dict[str, Any],
    _put: Any,
) -> None:
    """Apply HubSpot-deal property fields shared by both the submit and update routes.

    SubmitFormRequest and UpdateFormRequest are distinct Pydantic models but
    carry the same attribute names for the fields below. The route-specific
    builders own the differences (govwin_opp_id, govwin_agency, closedate
    placement, next_steps placement).
    """
    _put("govwin_industry", req.govwin_industry)
    _put("description", req.description)
    _put("dealname", req.dealname)
    _put("amount", req.amount)

    if req.ace_partner_need:
        _put("govwin_ace_partner_need", ";".join(req.ace_partner_need))
    if req.ace_delivery_model:
        _put("govwin_ace_delivery_model", ";".join(req.ace_delivery_model))
    if req.ace_sales_activities:
        _put("govwin_ace_sales_activities", ";".join(req.ace_sales_activities))
    _put("govwin_ace_use_case", req.ace_use_case)
    _put("govwin_ace_opportunity_type", req.ace_opportunity_type)
    _put("govwin_ace_competitor_name", req.ace_competitor_name)
    _put("govwin_ace_other_competitor_names", req.ace_other_competitor_names)
    _put("govwin_ace_aws_account_id", req.ace_aws_account_id)
    _put("govwin_ace_national_security", req.ace_national_security)
    _put("govwin_ace_solution_id", req.ace_solution_id)
    if req.ace_aws_products:
        # Strip the local Other escape hatch before persisting so the
        # downstream submit_to_ace loop never tries to associate it with AWS.
        filtered = [p for p in req.ace_aws_products if p != "Other"]
        if filtered:
            _put("govwin_ace_aws_products", ";".join(filtered))
    _put("govwin_ace_additional_comments", req.ace_additional_comments)
    _put("govwin_ace_related_opportunity_id", req.ace_related_opportunity_id)

    if req.marketing:
        _put("govwin_ace_marketing_source", req.marketing.source)
        _put("govwin_ace_marketing_campaign_name", req.marketing.campaign_name)
        if req.marketing.channels:
            _put("govwin_ace_marketing_channel", ";".join(req.marketing.channels))
        if req.marketing.use_cases:
            _put("govwin_ace_marketing_use_cases", ";".join(req.marketing.use_cases))
        _put("govwin_ace_marketing_dev_funded", req.marketing.aws_funding_used)


def _hubspot_property_payload(req: SubmitFormRequest) -> dict[str, Any]:
    """Build the HubSpot deal PATCH payload from a validated form request.

    The dealstage is intentionally NOT included here; the caller patches it
    in a separate request so the webhook receiver sees a clean property-change
    event for ``dealstage`` and routes it to the submit SQS queue.
    """
    props: dict[str, Any] = {
        "govwin_opp_id": req.govwin_opp_id,
    }

    def _put(key: str, value: Any) -> None:
        if value is not None and value != "":
            props[key] = value

    _put("govwin_agency", req.govwin_agency)
    if req.closedate:
        # HubSpot's closedate property is a millisecond epoch. The form sends
        # "YYYY-MM-DD" from a date picker; HubSpot's API will sometimes accept
        # that and sometimes 400 depending on which deal-properties endpoint
        # the SDK touches, so normalize here.
        _put("closedate", _closedate_to_epoch_ms(req.closedate))
    _put("govwin_ace_next_steps", req.ace_next_steps)
    _apply_shared_form_fields(req, props, _put)
    return props


def _closedate_to_epoch_ms(value: str) -> int | str:
    """Convert the form's closedate string to a millisecond epoch.

    HubSpot stores ``closedate`` as a millisecond epoch on the wire. The form's
    date input emits ``YYYY-MM-DD``; we anchor it to UTC midnight which is the
    same convention HubSpot uses internally when a user picks a date in the UI.
    Numeric inputs are passed through (with seconds-vs-ms detection by
    magnitude) so a future caller posting an epoch still works.

    On parse failure we return the original string so the upstream PATCH still
    has a value to send; HubSpot will reject it with a clean 4xx that the
    submit handler surfaces as a 502 to the form (no silent drop).
    """
    from datetime import datetime

    raw = value.strip()
    if not raw:
        return raw
    if raw.isdigit():
        n = int(raw)
        return n if n >= 1_000_000_000_000 else n * 1000
    try:
        dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return raw


def _trigger_stage_id() -> str:
    """Return the dealstage ID that fires the existing submit_to_ace webhook."""
    raw = os.environ.get("ACE_TRIGGER_STAGES", "").strip()
    if not raw:
        # Fall back to the production default we discovered today.
        return "3590200042"
    return raw.split(",")[0].strip()


def _handle_submit(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError:
        return _err(400, "validation_failed", message="invalid json body")

    # Defensive double-decode. HubSpot's hubspot.fetch proxy wraps the body as
    # a JSON string inside a "requestBody" field, and the unwrap behavior on
    # the receiving side has been observed to produce a body that is itself a
    # JSON-encoded string (i.e. the first json.loads returns a str, not a
    # dict). When that happens, parse one more time so Pydantic sees the dict.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return _err(400, "validation_failed", message="invalid json body (string)")

    logger.info(
        "submit endpoint received body: type=%s keys=%s raw_len=%d",
        type(payload).__name__,
        list(payload.keys())[:5] if isinstance(payload, dict) else None,
        len(raw_body),
    )

    try:
        req = SubmitFormRequest.model_validate(payload)
    except ValidationError as exc:
        errors = [
            FormFieldError(
                field=".".join(str(p) for p in err["loc"]),
                message=err["msg"],
            )
            for err in exc.errors()
        ]
        return _err(400, "validation_failed", errors=errors)

    if not is_valid_hubspot_object_id(req.deal_id):
        return _err(
            400,
            "validation_failed",
            errors=[FormFieldError(field="deal_id", message="must be a numeric HubSpot object id")],
        )
    if not is_valid_govwin_id(req.govwin_opp_id):
        return _err(
            400,
            "validation_failed",
            errors=[
                FormFieldError(
                    field="govwin_opp_id",
                    message="must match [A-Za-z0-9_-]+",
                )
            ],
        )

    enum_errors = _validate_enums(req)
    if enum_errors:
        return _err(400, "validation_failed", errors=enum_errors)

    config = load_config()
    state = SyncStateManager(config)
    existing = state.get_ace_mapping(req.govwin_opp_id) or {}
    existing_opp_id = existing.get("ace_opportunity_id")
    if existing_opp_id:
        # Dedup: the deal already has an opportunity in ACE. Surface it so
        # the form can render the existing state instead of re-creating.
        return _ok(
            SubmitFormResponse(
                deal_id=req.deal_id,
                govwin_opp_id=req.govwin_opp_id,
                status="already_submitted",
                ace_opportunity_id=str(existing_opp_id),
                message="Opportunity already exists in AWS Partner Central",
            ),
            status=409,
        )

    # PATCH HubSpot in two phases:
    #   1. Property updates (everything the mapper will consume on submit).
    #   2. Stage flip to the ACE trigger. The webhook receiver dispatches
    #      that property change to the submit SQS queue, which the existing
    #      submit_to_ace Lambda drains.
    with HubSpotClient(config) as hubspot:
        try:
            hubspot.update_deal(req.deal_id, _hubspot_property_payload(req))
        except HubSpotAPIError as exc:
            logger.exception("ui-extension HubSpot property patch failed")
            return _err(
                502,
                "validation_failed",
                message=f"HubSpot property update failed: {exc}",
            )

        try:
            hubspot.update_deal(req.deal_id, {"dealstage": _trigger_stage_id()})
        except HubSpotAPIError as exc:
            logger.exception("ui-extension dealstage flip failed")
            return _err(
                502,
                "validation_failed",
                message=f"HubSpot dealstage flip failed: {exc}",
            )

    logger.info(
        "ui-extension queued submission deal=%s govwin=%s products=%d",
        req.deal_id,
        req.govwin_opp_id,
        len(req.ace_aws_products),
    )
    return _ok(
        SubmitFormResponse(
            deal_id=req.deal_id,
            govwin_opp_id=req.govwin_opp_id,
            status="queued",
            message="Submission queued. Watch the deal card for AWS-side updates.",
        ),
        status=202,
    )


# ------/update handler------


def _validate_update_enums(req: UpdateFormRequest) -> list[FormFieldError]:
    """Server-side closed-enum validation for the update form.

    Mirrors :func:`_validate_enums` but adapted for the update-only fields
    (LifeCycle.Stage, ClosedLostReason) and without the create-only
    "PartnerNeed required" / "DeliveryModel required" rules -- on update,
    BD can leave those untouched if they don't want to change them.
    """
    errors: list[FormFieldError] = []

    def _check(field: str, value: str | None, allowed: set[str] | frozenset[str]) -> None:
        if value is None or value == "":
            return
        if value not in allowed:
            errors.append(
                FormFieldError(
                    field=field, message=f"value {value!r} is not in the AWS-published enum"
                )
            )

    def _check_list(field: str, values: list[str], allowed: set[str] | frozenset[str]) -> None:
        for v in values:
            if v not in allowed:
                errors.append(
                    FormFieldError(
                        field=field, message=f"value {v!r} is not in the AWS-published enum"
                    )
                )
                return

    if req.ace_partner_need:
        translated = [ace_mapper._normalize_partner_need(n) for n in req.ace_partner_need]
        _check_list("ace_partner_need", translated, ace_mapper.ALLOWED_PRIMARY_NEEDS)
    if req.ace_delivery_model:
        _check_list(
            "ace_delivery_model", req.ace_delivery_model, ace_mapper.ALLOWED_DELIVERY_MODELS
        )
    if req.ace_sales_activities:
        _check_list(
            "ace_sales_activities", req.ace_sales_activities, ace_mapper.ALLOWED_SALES_ACTIVITIES
        )
    _check("ace_use_case", req.ace_use_case, ace_mapper.ALLOWED_CUSTOMER_USE_CASES)
    _check("ace_opportunity_type", req.ace_opportunity_type, ace_mapper.ALLOWED_OPPORTUNITY_TYPES)
    _check("ace_competitor_name", req.ace_competitor_name, ace_mapper.ALLOWED_COMPETITORS)
    _check("ace_national_security", req.ace_national_security, ace_mapper.ALLOWED_NATIONAL_SECURITY)

    # LifeCycle. Stage is required by the form; closed-lost-reason conditional.
    _check("lifecycle_stage", req.lifecycle_stage, ace_mapper.ALLOWED_LIFECYCLE_STAGES)
    if req.lifecycle_stage == "Closed Lost":
        if not req.lifecycle_closed_lost_reason:
            errors.append(
                FormFieldError(
                    field="lifecycle_closed_lost_reason",
                    message="Required when LifeCycle Stage is 'Closed Lost'",
                )
            )
        else:
            _check(
                "lifecycle_closed_lost_reason",
                req.lifecycle_closed_lost_reason,
                ace_mapper.ALLOWED_CLOSED_LOST_REASONS,
            )

    if req.marketing is not None:
        _check("marketing.source", req.marketing.source, ace_mapper.ALLOWED_MARKETING_SOURCES)
        _check(
            "marketing.aws_funding_used",
            req.marketing.aws_funding_used,
            ace_mapper.ALLOWED_FUNDING_USED,
        )
        if req.marketing.channels:
            _check_list(
                "marketing.channels", req.marketing.channels, ace_mapper.ALLOWED_MARKETING_CHANNELS
            )

    if req.ace_aws_account_id and not is_valid_aws_account_id(req.ace_aws_account_id):
        errors.append(
            FormFieldError(field="ace_aws_account_id", message="AWS account id must be 12 digits")
        )
    if req.description is not None and 0 < len(req.description) < 20:
        # Mirrors the create-path /submit rule: AWS rejects
        # CustomerBusinessProblem shorter than 20 chars. Without this
        # check, the update mapper silently drops the field and BD gets
        # no feedback about why their text didn't land.
        errors.append(
            FormFieldError(
                field="description",
                message="Description must be at least 20 characters when provided",
            )
        )
    # Same closedate-format guard as the create path. lifecycle_target_close_date
    # is the update-side field name.
    if req.lifecycle_target_close_date:
        normalized = _closedate_to_epoch_ms(req.lifecycle_target_close_date)
        if not isinstance(normalized, int):
            errors.append(
                FormFieldError(
                    field="lifecycle_target_close_date",
                    message="Close date must be YYYY-MM-DD or an epoch number",
                )
            )
    if len(req.ace_aws_products) > ace_mapper.MAX_AWS_PRODUCTS_PER_OPPORTUNITY:
        errors.append(
            FormFieldError(
                field="ace_aws_products",
                message=(
                    "AWS Products limit is "
                    f"{ace_mapper.MAX_AWS_PRODUCTS_PER_OPPORTUNITY} per opportunity"
                ),
            )
        )
    return errors


def _hubspot_update_property_payload(req: UpdateFormRequest) -> dict[str, Any]:
    """Build the HubSpot deal PATCH payload from an UpdateFormRequest.

    The HubSpot writeback mirrors what the BD operator entered so the
    HubSpot deal stays consistent with the AWS-side opportunity. The form
    handles the AWS UpdateOpportunity itself synchronously; this PATCH is
    purely about keeping HubSpot's view in sync. Best-effort: a HubSpot
    PATCH failure does not roll back the successful AWS UpdateOpportunity.
    """
    props: dict[str, Any] = {}

    def _put(key: str, value: Any) -> None:
        if value is not None and value != "":
            props[key] = value

    _put("govwin_ace_next_steps", req.lifecycle_next_steps)
    if req.lifecycle_target_close_date:
        # Same epoch-ms normalization as create path so HubSpot doesn't
        # have to guess at the wire format.
        props["closedate"] = _closedate_to_epoch_ms(req.lifecycle_target_close_date)
    _apply_shared_form_fields(req, props, _put)
    return props


def _diff_aws_products(*, requested: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    """Return (to_associate, to_disassociate) AWS Product identifiers.

    Order is preserved from ``requested`` for predictable Associate ordering.
    "Other" is filtered out of BOTH sides because it's a local escape hatch
    that never maps to a real AWS product identifier. A legacy opp may have
    "Other" recorded in its associations from earlier behavior; symmetric
    filtering prevents a spurious Disassociate("Other") call that AWS would
    reject with ValidationException.
    """
    req_set = {p for p in requested if p and p != "Other"}
    cur_set = {p for p in current if p and p != "Other"}
    to_associate = [p for p in requested if p in (req_set - cur_set)]
    to_disassociate = sorted(cur_set - req_set)
    return to_associate, to_disassociate


def _handle_update(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError:
        return _err(400, "validation_failed", message="invalid json body")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return _err(400, "validation_failed", message="invalid json body (string)")

    logger.info(
        "update endpoint received body: type=%s keys=%s raw_len=%d",
        type(payload).__name__,
        list(payload.keys())[:5] if isinstance(payload, dict) else None,
        len(raw_body),
    )

    try:
        req = UpdateFormRequest.model_validate(payload)
    except ValidationError as exc:
        errors = [
            FormFieldError(
                field=".".join(str(p) for p in err["loc"]),
                message=err["msg"],
            )
            for err in exc.errors()
        ]
        return _err(400, "validation_failed", errors=errors)

    if not is_valid_hubspot_object_id(req.deal_id):
        return _err(
            400,
            "validation_failed",
            errors=[FormFieldError(field="deal_id", message="must be a numeric HubSpot object id")],
        )
    if not is_valid_govwin_id(req.govwin_opp_id):
        return _err(
            400,
            "validation_failed",
            errors=[
                FormFieldError(
                    field="govwin_opp_id",
                    message="must match [A-Za-z0-9_-]+",
                )
            ],
        )

    enum_errors = _validate_update_enums(req)
    if enum_errors:
        return _err(400, "validation_failed", errors=enum_errors)

    config = load_config()
    state = SyncStateManager(config)
    mapping = state.get_ace_mapping(req.govwin_opp_id) or {}
    ace_opportunity_id = mapping.get("ace_opportunity_id")
    if not ace_opportunity_id:
        return _err(
            409,
            "validation_failed",
            message=(
                "No AWS opportunity is bound to this deal yet. Submit it via "
                "'Submit to AWS' first, then come back to update it."
            ),
        )

    # Sanity check: the DDB mapping must point at the same HubSpot deal we
    # got the request from. If a mapping ever drifts (someone manually
    # edited govwin_opp_id on the wrong deal), refuse rather than silently
    # update the wrong AWS opportunity.
    # Backfill the deal_id on the mapping if missing (legacy rows from
    # before we persisted hubspot_deal_id). The self-heal in
    # handle_ace_event already does this for inbound events; do it here
    # too so the mismatch guard below has a value to compare against.
    mapping_deal_id = str(mapping.get("hubspot_deal_id") or "")
    if not mapping_deal_id:
        try:
            state.update_ace_mapping(
                govwin_id=req.govwin_opp_id,
                hubspot_deal_id=req.deal_id,
            )
            mapping_deal_id = req.deal_id
        except Exception:  # noqa: BLE001 -- best-effort
            logger.exception(
                "ui-extension update: deal_id backfill failed for %s", req.govwin_opp_id
            )
    if mapping_deal_id and mapping_deal_id != req.deal_id:
        return _err(
            409,
            "validation_failed",
            message=(
                f"GovWin id {req.govwin_opp_id} is bound to a different HubSpot "
                f"deal ({mapping_deal_id}). Refusing to apply this update."
            ),
        )

    ace = ACEClient(config)
    try:
        current = ace.get_opportunity(str(ace_opportunity_id))
    except ACEAPIError as exc:
        logger.exception(
            "ui-extension update: GetOpportunity failed for opp=%s code=%s",
            ace_opportunity_id,
            exc.code,
        )
        return _err(
            502,
            "validation_failed",
            # Generic message to the client; AWS error strings can echo
            # field values that include customer / competitor names from
            # the existing opp payload. Full detail stays in CloudWatch.
            message="AWS could not load the current opportunity. Try again in a moment.",
        )

    update_payload = ace_mapper.map_update_form_to_ace_payload(
        form=req, current=current, config=config
    )
    try:
        response = ace.update_with_retry(
            identifier=str(ace_opportunity_id),
            updates=update_payload,
            known_last_modified_date=current.get("LastModifiedDate"),
        )
    except ACEAPIError as exc:
        logger.warning(
            "ui-extension update: UpdateOpportunity failed code=%s opp=%s detail=%s",
            exc.code,
            ace_opportunity_id,
            str(exc)[:500],
        )
        # Map known codes to safe BD-readable messages. ValidationException
        # is the most common (bad enum, missing required field); the
        # underlying detail string can include PII so we don't echo it.
        safe_messages = {
            "ValidationException": (
                "AWS rejected the update due to invalid or missing fields. "
                "Check the form values; if all look correct, contact the admin "
                "(error logged with code ValidationException)."
            ),
            "ConflictException": (
                "The AWS opportunity changed since the form opened. Reload the deal and try again."
            ),
            "AccessDeniedException": (
                "The pipeline lacks permission to apply this update. "
                "Contact the admin (error logged with code AccessDeniedException)."
            ),
            "ThrottlingException": ("AWS is rate-limiting requests. Wait a moment and try again."),
        }
        code = exc.code or "Unknown"
        message = safe_messages.get(code, f"AWS rejected the update ({code}). Contact the admin.")
        return _err(502, "validation_failed", message=message)

    # AWS Products diff is now applied ASYNC: the HubSpot PATCH below
    # writes the new ``govwin_ace_aws_products`` semicolon-joined string,
    # which fires a property-change webhook that update_in_ace's
    # _handle_aws_products_diff (added 2026-05-27) consumes and turns
    # into Associate/Disassociate calls. Doing it inline here turned
    # the /update endpoint into a 1-write/sec-per-product loop that
    # could blow past API Gateway's 29s timeout on diffs of 5+ items.
    # The async path:
    #   - returns the user's update HTTP response in ~3s
    #   - completes the AWS product association within ~30s of save
    #   - is idempotent under SQS at-least-once delivery (Associate
    #     returns ConflictException on a re-fire; ignored)
    # See diff in update_in_ace.py:_handle_aws_products_diff.
    to_associate: list[str] = []  # computed async by webhook; placeholders
    to_disassociate: list[str] = []  # for the response.

    # Persist the new LastModifiedDate so the next update knows the latest
    # version without an extra GetOpportunity round-trip.
    try:
        state.update_ace_mapping(
            govwin_id=req.govwin_opp_id,
            ace_opportunity_id=str(ace_opportunity_id),
            last_modified_date=str(response.get("LastModifiedDate"))
            if response.get("LastModifiedDate")
            else None,
            hubspot_deal_id=req.deal_id,
        )
    except Exception:  # noqa: BLE001 -- mapping update is best-effort
        logger.exception(
            "ui-extension update: DDB mapping refresh failed for %s", req.govwin_opp_id
        )

    # HubSpot writeback so the deal record reflects what BD just entered.
    # Best-effort: a HubSpot PATCH failure does not roll back AWS.
    try:
        with HubSpotClient(config) as hubspot:
            hs_props = _hubspot_update_property_payload(req)
            # Stamp cosell_status so the card's StatusBadge tracks the post-
            # update AWS state. Prefer the ReviewStatus echoed in the
            # UpdateOpportunity response (authoritative for what AWS now
            # has) over the pre-update value from ``current``; reviewer
            # actions can advance ReviewStatus between Get and Update.
            if req.lifecycle_stage:
                post_review = str(
                    (response.get("LifeCycle") or {}).get("ReviewStatus")
                    or current.get("LifeCycle", {}).get("ReviewStatus")
                    or "Submitted"
                )[:80]
                hs_props["govwin_aws_cosell_status"] = (
                    "Launched"
                    if req.lifecycle_stage == "Launched"
                    else "Closed Lost"
                    if req.lifecycle_stage == "Closed Lost"
                    else post_review
                )
                # Stamp the AWS-side LifeCycle.Stage on the deal too so
                # the next form open pre-fills the dropdown correctly
                # without waiting for the EventBridge round-trip.
                hs_props["govwin_ace_lifecycle_stage"] = req.lifecycle_stage
            hubspot.update_deal(req.deal_id, hs_props)
    except Exception:  # noqa: BLE001 -- best-effort
        logger.exception(
            "ui-extension update: HubSpot writeback failed for deal %s; AWS opp %s "
            "was still updated successfully",
            req.deal_id,
            ace_opportunity_id,
        )

    logger.info(
        "ui-extension applied update deal=%s govwin=%s opp=%s stage=%s "
        "products(+%d/-%d -- applied async via webhook -> update_in_ace)",
        req.deal_id,
        req.govwin_opp_id,
        ace_opportunity_id,
        req.lifecycle_stage,
        len(to_associate),
        len(to_disassociate),
    )
    return _ok(
        UpdateFormResponse(
            deal_id=req.deal_id,
            govwin_opp_id=req.govwin_opp_id,
            ace_opportunity_id=str(ace_opportunity_id),
            status="updated",
            lifecycle_stage=req.lifecycle_stage,
            products_added=to_associate,
            products_removed=to_disassociate,
            message=(
                f"Opportunity {ace_opportunity_id} updated. "
                f"Products: +{len(to_associate)} / -{len(to_disassociate)}."
            ),
        ),
        status=200,
    )


# ------/solutions handler------


def _load_solutions(catalog: str) -> list[SolutionSummary]:
    cached = _solutions_cache.get(catalog)
    now = time.time()
    if cached and (now - cached[1]) < _SOLUTIONS_TTL_SECONDS:
        return cached[0]
    config = load_config()
    ace = ACEClient(config)
    try:
        raw = ace.list_active_solutions()
    except ACEAPIError as exc:
        logger.error("ace.list_active_solutions failed: %s", exc)
        return []
    summaries = [SolutionSummary.model_validate(r) for r in raw]
    # Don't cache an empty list: legitimate empty catalogs (Sandbox today)
    # are rare AND a transient ListSolutions failure has been observed to
    # return [] without raising, which would poison the cache for the
    # full TTL window. The downstream call is cheap (10 reads/sec quota,
    # one call per form open), so re-issuing is fine.
    if summaries:
        _solutions_cache[catalog] = (summaries, now)
    return summaries


def _handle_solutions(query: dict[str, str]) -> dict[str, Any]:
    # The catalog the Lambda is deployed against is the only catalog whose
    # solutions we can list (the IAM role pins partnercentral:Catalog).
    # Earlier we accepted a ?catalog= query arg and cached per-catalog, but
    # the underlying ACEClient always called against config.ace.catalog,
    # so a Sandbox deployment serving ?catalog=AWS returned Sandbox data
    # labeled "AWS" -- misleading. Ignore the query and always echo the
    # server-trusted catalog.
    config = load_config()
    catalog = (config.ace.catalog or "Sandbox").strip()
    solutions = _load_solutions(catalog)
    return _ok(SolutionListResponse(catalog=catalog, solutions=solutions))


# ------/aws-products handler------


def _load_aws_products() -> list[AwsProductSummary]:
    global _aws_products_cache
    if _aws_products_cache is not None:
        return _aws_products_cache
    try:
        with AWS_PRODUCTS_PATH.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        logger.error("aws_products.json missing at %s", AWS_PRODUCTS_PATH)
        _aws_products_cache = []
        return _aws_products_cache
    products = payload.get("products") if isinstance(payload, dict) else payload
    _aws_products_cache = [AwsProductSummary.model_validate(p) for p in (products or [])]
    return _aws_products_cache


def _handle_aws_products() -> dict[str, Any]:
    return _ok(AwsProductListResponse(products=_load_aws_products()))


# ------Entry point------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    config = load_config()
    _ensure_clients(config.aws.region)
    method, path, raw_query = _resolve_route(event)
    headers = _lower(event.get("headers"))

    if method == "OPTIONS":
        # CORS preflight. The HubSpot UI Extension iframe runs inside HubSpot's
        # origin via the hubspot.fetch proxy, so CORS isn't actually used in
        # the steady state -- but the OPTIONS response still has to satisfy
        # the browser's preflight check. Origin is reflected only if it
        # matches an allowlist of known HubSpot region hosts; anything else
        # gets the NA1 default so the response is never literal "*".
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

    raw_body_str = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body_str)
    else:
        raw_body = raw_body_str.encode("utf-8")
    if len(raw_body) > MAX_BODY_BYTES:
        return _err(413, "validation_failed", message="payload too large")

    try:
        target_url = _required_target_url(path, raw_query)
    except _ConfigError as exc:
        # _required_target_url validates the path against an allowlist; an
        # unknown path lands here. 404 is the right surface to the client
        # so attackers cannot probe to learn allowed paths.
        logger.warning("ui-extension rejected: %s", exc)
        return _err(404, "validation_failed", message="not found")

    sig_ok, sig_reason = _validate_request_signature(
        method=method, raw_body=raw_body, headers=headers, target_url=target_url
    )
    if not sig_ok:
        # Map the internal reason to a status code without leaking which
        # branch failed. A Secrets-Manager outage should look like 500
        # (server problem); anything else looks like 401 (auth problem).
        # The reason itself stays in CloudWatch only.
        logger.warning("ui-extension rejected: %s", sig_reason)
        if sig_reason in ("secret unavailable", "misconfigured"):
            return _err(500, "unauthorized", message="server error")
        return _err(401, "unauthorized", message="unauthorized")

    # Replay protection. The signature has already been verified and is
    # within the freshness window; this catches a replay of the same
    # signed request within the window (5 minutes). We hash the
    # (signature, timestamp) tuple so the fingerprint doesn't equal the
    # raw signature (which has the same length and entropy as the MAC).
    # The 2x window TTL ensures a replay inside the window can't slip
    # past the reservation expiry.
    sig_value = headers.get("x-hubspot-signature-v3", "")
    ts_value = headers.get("x-hubspot-request-timestamp", "")
    fingerprint = hashlib.sha256((sig_value + "|" + ts_value).encode("utf-8")).hexdigest()
    state = SyncStateManager(config)
    if not state.reserve_webhook_signature(
        fingerprint, ttl_seconds=config.ace.webhook_max_age_seconds * 2
    ):
        logger.warning(
            "ui-extension rejected: replay detected for fingerprint=%s...",
            fingerprint[:12],
        )
        # Status string distinct from the dedup 409 returned by /submit
        # when a govwin id already has an ACE opportunity. Without this
        # distinction the form's 409 handler reads body.ace_opportunity_id
        # (undefined on replay) and renders "(undefined)" to BD.
        return _err(409, "replay_detected", message="request was retried; ignored")

    # Exact-match routing. ``endswith`` would have matched a crafted path
    # like ``/foo/ui-extension/submit`` and routed it to the handler. The
    # path was already validated against ``_ALLOWED_PATHS`` in
    # _required_target_url, but we re-check here so a future change to the
    # validator doesn't silently re-introduce the prefix-match foot-gun.
    if method == "POST" and path == "/ui-extension/submit":
        return _handle_submit(raw_body)
    if method == "POST" and path == "/ui-extension/update":
        return _handle_update(raw_body)
    if method == "GET" and path == "/ui-extension/solutions":
        query = event.get("queryStringParameters") or {}
        return _handle_solutions({str(k): str(v) for k, v in query.items()})
    if method == "GET" and path == "/ui-extension/aws-products":
        return _handle_aws_products()

    return _err(404, "validation_failed", message="not found")
