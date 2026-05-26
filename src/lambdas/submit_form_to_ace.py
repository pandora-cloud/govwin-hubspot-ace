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
import json
import logging
import os
import time
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
)
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

#------Constants------

MAX_BODY_BYTES = 256 * 1024  # 256 KiB; the form payload is small
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


#------HTTP helpers------


def _required_target_url(path: str) -> str:
    """Return the full URL HubSpot signed against for this request.

    HubSpot's signature scheme covers ``method || url || raw_body || timestamp``
    so the validator needs the exact URL the UI Extension called via
    ``hubspot.fetch()``. We compose it from the API Gateway base URL (env
    var; same value for all three routes on this Lambda) and the request
    path observed by API Gateway.
    """
    base = os.environ.get("UI_EXTENSION_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise _ConfigError("UI_EXTENSION_BASE_URL is not configured")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _lower(headers: dict[str, Any] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _resolve_route(event: dict[str, Any]) -> tuple[str, str]:
    """Return ``(method, path)`` from the API Gateway HTTP API event shape."""
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
    return method, raw_path


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
    body = SubmitFormErrorResponse(
        status=body_status, message=message, errors=errors or []
    )
    return _ok(body, status=status)


#------Signature validation------


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
        secret = get_signing_secret(
            _secrets_client, config.aws.hubspot_webhook_secret_name
        )
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
    return ok, None if ok else "invalid signature"


#------Enum validation for /submit------


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

    def _check_list(
        field: str, values: list[str], allowed: set[str] | frozenset[str]
    ) -> None:
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
    translated_needs = [
        ace_mapper._normalize_partner_need(n) for n in req.ace_partner_need
    ]
    _check_list("ace_partner_need", translated_needs, ace_mapper.ALLOWED_PRIMARY_NEEDS)
    _check_list("ace_delivery_model", req.ace_delivery_model, ace_mapper.ALLOWED_DELIVERY_MODELS)
    _check_list(
        "ace_sales_activities",
        req.ace_sales_activities,
        ace_mapper.ALLOWED_SALES_ACTIVITIES,
    )
    _check(
        "ace_use_case", req.ace_use_case, ace_mapper.ALLOWED_CUSTOMER_USE_CASES
    )
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

    return errors


#------/submit handler------


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
    _put("govwin_industry", req.govwin_industry)
    _put("description", req.description)
    _put("dealname", req.dealname)
    _put("amount", req.amount)
    if req.closedate:
        # HubSpot expects ISO-8601 / epoch ms; pass through unchanged.
        _put("closedate", req.closedate)

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
    _put("govwin_ace_next_steps", req.ace_next_steps)
    _put("govwin_ace_related_opportunity_id", req.ace_related_opportunity_id)

    if req.marketing:
        _put("govwin_ace_marketing_source", req.marketing.source)
        _put("govwin_ace_marketing_campaign_name", req.marketing.campaign_name)
        if req.marketing.channels:
            _put("govwin_ace_marketing_channel", ";".join(req.marketing.channels))
        if req.marketing.use_cases:
            _put("govwin_ace_marketing_use_cases", ";".join(req.marketing.use_cases))
        _put("govwin_ace_marketing_dev_funded", req.marketing.aws_funding_used)

    return props


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


#------/solutions handler------


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
    _solutions_cache[catalog] = (summaries, now)
    return summaries


def _handle_solutions(query: dict[str, str]) -> dict[str, Any]:
    config = load_config()
    catalog = (query.get("catalog") or config.ace.catalog).strip() or "Sandbox"
    solutions = _load_solutions(catalog)
    return _ok(SolutionListResponse(catalog=catalog, solutions=solutions))


#------/aws-products handler------


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


#------Entry point------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    config = load_config()
    _ensure_clients(config.aws.region)
    method, path = _resolve_route(event)
    headers = _lower(event.get("headers"))

    if method == "OPTIONS":
        # CORS preflight. The HubSpot UI Extension iframe doesn't actually need
        # CORS (hubspot.fetch handles origin) but API Gateway may still send one.
        return {"statusCode": 204, "headers": {"Access-Control-Allow-Origin": "*"}}

    raw_body_str = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body_str)
    else:
        raw_body = raw_body_str.encode("utf-8")
    if len(raw_body) > MAX_BODY_BYTES:
        return _err(413, "validation_failed", message="payload too large")

    try:
        target_url = _required_target_url(path)
    except _ConfigError as exc:
        logger.error("ui-extension config error: %s", exc)
        return _err(500, "validation_failed", message="misconfigured")

    sig_ok, sig_reason = _validate_request_signature(
        method=method, raw_body=raw_body, headers=headers, target_url=target_url
    )
    if not sig_ok:
        logger.warning("ui-extension rejected: %s", sig_reason)
        return _err(401, "unauthorized", message=sig_reason)

    if method == "POST" and path.endswith("/ui-extension/submit"):
        return _handle_submit(raw_body)
    if method == "GET" and path.endswith("/ui-extension/solutions"):
        query = event.get("queryStringParameters") or {}
        return _handle_solutions({str(k): str(v) for k, v in query.items()})
    if method == "GET" and path.endswith("/ui-extension/aws-products"):
        return _handle_aws_products()

    return _err(404, "validation_failed", message=f"no route for {method} {path}")
