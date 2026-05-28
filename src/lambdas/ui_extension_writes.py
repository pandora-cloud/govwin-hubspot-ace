"""Write-side HubSpot UI Extension callbacks.

Two endpoints, both mutate state:

* ``POST /ui-extension/submit`` -- validates a full SubmitFormRequest,
  PATCHes the deal's GovWin/ACE properties + flips dealstage to the ACE
  trigger. The dealstage flip fires the existing
  hubspot_webhook_receiver -> submit SQS -> submit_to_ace pipeline so
  the actual CreateOpportunity call still runs from the trusted
  worker role (this Lambda never carries CreateOpportunity IAM).

* ``POST /ui-extension/update`` -- validates an UpdateFormRequest,
  fetches the AWS opportunity, scrub_for_update + apply form delta,
  calls UpdateOpportunity synchronously. AWS Products diff is delegated
  to the async update_in_ace path via the webhook on the
  govwin_ace_aws_products property change (avoids the 1-write/sec
  product association loop that previously could overrun API Gateway's
  29s timeout).

IAM role: secrets (webhook signing + HubSpot private app), DynamoDB on
entity-mappings, partnercentral reads + non-Create writes (Update,
Associate, Disassociate, ListSolutions). CreateOpportunity is INTENTIONALLY
excluded; it stays on the trusted shared role.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC
from typing import Any

from pydantic import ValidationError

from src.ace import mapper as ace_mapper
from src.ace.client import ACEAPIError, ACEClient
from src.ace.validators import (
    is_valid_aws_account_id,
    is_valid_govwin_id,
    is_valid_hubspot_object_id,
)
from src.config import load_config
from src.hubspot.client import HubSpotAPIError, HubSpotClient
from src.lambdas._ui_extension_common import err, ok, serve_request
from src.models import (
    FormFieldError,
    SubmitFormRequest,
    SubmitFormResponse,
    UpdateFormRequest,
    UpdateFormResponse,
)
from src.sync.state import SyncStateManager

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/ui-extension/submit",
        "/ui-extension/update",
    }
)


# ---------------------------------------------------------------------------
# Server-side enum validation
# ---------------------------------------------------------------------------


def _validate_enums(req: SubmitFormRequest) -> list[FormFieldError]:
    """Re-check that every closed-enum field matches the AWS source of truth.

    The form is supposed to enforce this client-side via the generated
    enums file, but we re-check on the server so a bypassed client cannot
    produce a payload AWS will reject (or worse, accept silently into
    the wrong field).
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
                return

    translated_needs = [ace_mapper._normalize_partner_need(n) for n in req.ace_partner_need]
    _check_list("ace_partner_need", translated_needs, ace_mapper.ALLOWED_PRIMARY_NEEDS)
    _check_list("ace_delivery_model", req.ace_delivery_model, ace_mapper.ALLOWED_DELIVERY_MODELS)
    _check_list(
        "ace_sales_activities", req.ace_sales_activities, ace_mapper.ALLOWED_SALES_ACTIVITIES
    )
    _check("ace_use_case", req.ace_use_case, ace_mapper.ALLOWED_CUSTOMER_USE_CASES)
    _check("ace_opportunity_type", req.ace_opportunity_type, ace_mapper.ALLOWED_OPPORTUNITY_TYPES)
    _check("ace_competitor_name", req.ace_competitor_name, ace_mapper.ALLOWED_COMPETITORS)
    _check(
        "ace_national_security",
        req.ace_national_security,
        ace_mapper.ALLOWED_NATIONAL_SECURITY,
    )

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
            FormFieldError(
                field="ace_aws_account_id", message="AWS account id must be 12 digits"
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
                field="ace_partner_need", message="At least one PartnerNeed is required"
            )
        )
    if not req.ace_delivery_model:
        errors.append(
            FormFieldError(
                field="ace_delivery_model", message="At least one DeliveryModel is required"
            )
        )
    if req.description is not None and 0 < len(req.description) < 20:
        errors.append(
            FormFieldError(
                field="description",
                message="Description must be at least 20 characters when provided",
            )
        )

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


def _validate_update_enums(req: UpdateFormRequest) -> list[FormFieldError]:
    """Server-side closed-enum validation for the update form.

    Mirrors :func:`_validate_enums` but adapted for the update-only
    fields (LifeCycle.Stage, ClosedLostReason) and without the create-only
    "PartnerNeed required" / "DeliveryModel required" rules: on update,
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
            "ace_sales_activities",
            req.ace_sales_activities,
            ace_mapper.ALLOWED_SALES_ACTIVITIES,
        )
    _check("ace_use_case", req.ace_use_case, ace_mapper.ALLOWED_CUSTOMER_USE_CASES)
    _check("ace_opportunity_type", req.ace_opportunity_type, ace_mapper.ALLOWED_OPPORTUNITY_TYPES)
    _check("ace_competitor_name", req.ace_competitor_name, ace_mapper.ALLOWED_COMPETITORS)
    _check(
        "ace_national_security", req.ace_national_security, ace_mapper.ALLOWED_NATIONAL_SECURITY
    )
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
                "marketing.channels",
                req.marketing.channels,
                ace_mapper.ALLOWED_MARKETING_CHANNELS,
            )

    if req.ace_aws_account_id and not is_valid_aws_account_id(req.ace_aws_account_id):
        errors.append(
            FormFieldError(
                field="ace_aws_account_id", message="AWS account id must be 12 digits"
            )
        )
    if req.description is not None and 0 < len(req.description) < 20:
        errors.append(
            FormFieldError(
                field="description",
                message="Description must be at least 20 characters when provided",
            )
        )
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


# ---------------------------------------------------------------------------
# HubSpot PATCH payload builders
# ---------------------------------------------------------------------------


def _apply_shared_form_fields(req: Any, props: dict[str, Any], _put: Any) -> None:
    """Apply HubSpot-deal property fields shared by submit and update routes.

    SubmitFormRequest and UpdateFormRequest are distinct Pydantic models
    but carry the same attribute names for these fields. The route-specific
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
        # Strip the local Other escape hatch before persisting.
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
    """Build the HubSpot deal PATCH payload from a SubmitFormRequest.

    The dealstage is intentionally NOT included; the caller patches it
    in a separate request so the webhook receiver sees a clean
    property-change event for ``dealstage`` and routes it to the submit
    SQS queue.
    """
    props: dict[str, Any] = {"govwin_opp_id": req.govwin_opp_id}

    def _put(key: str, value: Any) -> None:
        if value is not None and value != "":
            props[key] = value

    _put("govwin_agency", req.govwin_agency)
    if req.closedate:
        _put("closedate", _closedate_to_epoch_ms(req.closedate))
    _put("govwin_ace_next_steps", req.ace_next_steps)
    _apply_shared_form_fields(req, props, _put)
    return props


def _hubspot_update_property_payload(req: UpdateFormRequest) -> dict[str, Any]:
    """Build the HubSpot deal PATCH payload from an UpdateFormRequest."""
    props: dict[str, Any] = {}

    def _put(key: str, value: Any) -> None:
        if value is not None and value != "":
            props[key] = value

    _put("govwin_ace_next_steps", req.lifecycle_next_steps)
    if req.lifecycle_target_close_date:
        props["closedate"] = _closedate_to_epoch_ms(req.lifecycle_target_close_date)
    _apply_shared_form_fields(req, props, _put)
    return props


def _closedate_to_epoch_ms(value: str) -> int | str:
    """Convert the form's closedate string to a millisecond epoch.

    HubSpot stores ``closedate`` as a millisecond epoch on the wire.
    The form's date input emits ``YYYY-MM-DD``; we anchor it to UTC
    midnight which is the same convention HubSpot uses internally when
    a user picks a date in the UI. Numeric inputs pass through with
    seconds-vs-ms detection by magnitude.

    On parse failure we return the raw string so the upstream PATCH
    still has a value to send; HubSpot will reject it with a clean 4xx
    that the handler surfaces as a 502 (no silent drop).
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
        # Fall back to the production default we discovered during the
        # 2025 deploy iteration.
        return "3590200042"
    return raw.split(",")[0].strip()


# ---------------------------------------------------------------------------
# /submit handler
# ---------------------------------------------------------------------------


def _handle_submit(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError:
        return err(400, "validation_failed", message="invalid json body")

    # Defensive double-decode. hubspot.fetch sometimes returns a body that
    # is itself a JSON-encoded string (the first json.loads yields a str,
    # not a dict). Parse one more time so Pydantic sees the dict.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return err(400, "validation_failed", message="invalid json body (string)")

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
            FormFieldError(field=".".join(str(p) for p in e["loc"]), message=e["msg"])
            for e in exc.errors()
        ]
        return err(400, "validation_failed", errors=errors)

    if not is_valid_hubspot_object_id(req.deal_id):
        return err(
            400,
            "validation_failed",
            errors=[FormFieldError(field="deal_id", message="must be a numeric HubSpot object id")],
        )
    if not is_valid_govwin_id(req.govwin_opp_id):
        return err(
            400,
            "validation_failed",
            errors=[FormFieldError(field="govwin_opp_id", message="must match [A-Za-z0-9_-]+")],
        )

    enum_errors = _validate_enums(req)
    if enum_errors:
        return err(400, "validation_failed", errors=enum_errors)

    config = load_config()
    state = SyncStateManager(config)
    existing = state.get_ace_mapping(req.govwin_opp_id) or {}
    existing_opp_id = existing.get("ace_opportunity_id")
    if existing_opp_id:
        return ok(
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
    #      that property change to the submit SQS queue, which the
    #      existing submit_to_ace Lambda drains.
    with HubSpotClient(config) as hubspot:
        try:
            hubspot.update_deal(req.deal_id, _hubspot_property_payload(req))
        except HubSpotAPIError as exc:
            logger.exception("ui-extension HubSpot property patch failed")
            return err(
                502, "validation_failed", message=f"HubSpot property update failed: {exc}"
            )

        try:
            hubspot.update_deal(req.deal_id, {"dealstage": _trigger_stage_id()})
        except HubSpotAPIError as exc:
            logger.exception("ui-extension dealstage flip failed")
            return err(
                502, "validation_failed", message=f"HubSpot dealstage flip failed: {exc}"
            )

    logger.info(
        "ui-extension queued submission deal=%s govwin=%s products=%d",
        req.deal_id,
        req.govwin_opp_id,
        len(req.ace_aws_products),
    )
    return ok(
        SubmitFormResponse(
            deal_id=req.deal_id,
            govwin_opp_id=req.govwin_opp_id,
            status="queued",
            message="Submission queued. Watch the deal card for AWS-side updates.",
        ),
        status=202,
    )


# ---------------------------------------------------------------------------
# /update handler
# ---------------------------------------------------------------------------


def _handle_update(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError:
        return err(400, "validation_failed", message="invalid json body")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return err(400, "validation_failed", message="invalid json body (string)")

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
            FormFieldError(field=".".join(str(p) for p in e["loc"]), message=e["msg"])
            for e in exc.errors()
        ]
        return err(400, "validation_failed", errors=errors)

    if not is_valid_hubspot_object_id(req.deal_id):
        return err(
            400,
            "validation_failed",
            errors=[FormFieldError(field="deal_id", message="must be a numeric HubSpot object id")],
        )
    if not is_valid_govwin_id(req.govwin_opp_id):
        return err(
            400,
            "validation_failed",
            errors=[FormFieldError(field="govwin_opp_id", message="must match [A-Za-z0-9_-]+")],
        )

    enum_errors = _validate_update_enums(req)
    if enum_errors:
        return err(400, "validation_failed", errors=enum_errors)

    config = load_config()
    state = SyncStateManager(config)
    mapping = state.get_ace_mapping(req.govwin_opp_id) or {}
    ace_opportunity_id = mapping.get("ace_opportunity_id")
    if not ace_opportunity_id:
        return err(
            409,
            "validation_failed",
            message=(
                "No AWS opportunity is bound to this deal yet. Submit it via "
                "'Submit to AWS' first, then come back to update it."
            ),
        )

    # The DDB mapping must point at the same HubSpot deal we got the
    # request from. If a mapping drifts (someone manually edited
    # govwin_opp_id on the wrong deal), refuse rather than silently
    # update the wrong AWS opportunity. Backfill the deal_id on legacy
    # rows before comparing.
    mapping_deal_id = str(mapping.get("hubspot_deal_id") or "")
    if not mapping_deal_id:
        try:
            state.update_ace_mapping(
                govwin_id=req.govwin_opp_id, hubspot_deal_id=req.deal_id
            )
            mapping_deal_id = req.deal_id
        except Exception:  # noqa: BLE001 -- best-effort backfill
            logger.exception(
                "ui-extension update: deal_id backfill failed for %s", req.govwin_opp_id
            )
    if mapping_deal_id and mapping_deal_id != req.deal_id:
        return err(
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
        return err(
            502,
            "validation_failed",
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
        safe_messages = {
            "ValidationException": (
                "AWS rejected the update due to invalid or missing fields. "
                "Check the form values; if all look correct, contact the admin "
                "(error logged with code ValidationException)."
            ),
            "ConflictException": (
                "The AWS opportunity changed since the form opened. "
                "Reload the deal and try again."
            ),
            "AccessDeniedException": (
                "The pipeline lacks permission to apply this update. "
                "Contact the admin (error logged with code AccessDeniedException)."
            ),
            "ThrottlingException": (
                "AWS is rate-limiting requests. Wait a moment and try again."
            ),
        }
        code = exc.code or "Unknown"
        message = safe_messages.get(code, f"AWS rejected the update ({code}). Contact the admin.")
        return err(502, "validation_failed", message=message)

    # AWS Products diff is applied ASYNC: the HubSpot PATCH below writes
    # the new govwin_ace_aws_products semicolon-joined string, which fires
    # a property-change webhook that update_in_ace's
    # _handle_aws_products_diff consumes and turns into
    # Associate/Disassociate calls. Doing it inline here turned the
    # /update endpoint into a 1-write/sec-per-product loop that could
    # overrun API Gateway's 29s timeout on diffs of 5+ items.
    to_associate: list[str] = []
    to_disassociate: list[str] = []

    try:
        state.update_ace_mapping(
            govwin_id=req.govwin_opp_id,
            ace_opportunity_id=str(ace_opportunity_id),
            last_modified_date=str(response.get("LastModifiedDate"))
            if response.get("LastModifiedDate")
            else None,
            hubspot_deal_id=req.deal_id,
        )
    except Exception:  # noqa: BLE001 -- best-effort
        logger.exception(
            "ui-extension update: DDB mapping refresh failed for %s", req.govwin_opp_id
        )

    # HubSpot writeback. Best-effort: a HubSpot PATCH failure does not
    # roll back the successful AWS UpdateOpportunity.
    try:
        with HubSpotClient(config) as hubspot:
            hs_props = _hubspot_update_property_payload(req)
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
    return ok(
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _dispatch(method: str, path: str, raw_body: bytes, _event: dict[str, Any]) -> dict[str, Any]:
    if method == "POST" and path == "/ui-extension/submit":
        return _handle_submit(raw_body)
    if method == "POST" and path == "/ui-extension/update":
        return _handle_update(raw_body)
    return err(405, "validation_failed", message="method not allowed")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return serve_request(event, allowed_paths=_ALLOWED_PATHS, dispatch=_dispatch)
