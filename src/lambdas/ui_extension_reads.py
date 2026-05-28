"""Read-only HubSpot UI Extension callbacks.

Two endpoints, no mutations:

* ``GET /ui-extension/solutions`` -- returns the partner's catalog of
  active Solutions. Calls ``partnercentral:ListSolutions`` once per
  TTL window per catalog. Used by SolutionPicker in the form.

* ``GET /ui-extension/aws-products`` -- returns the AWS Products catalog
  bundled in ``resources/aws_products.json``. Static file read; no
  AWS API calls.

This Lambda is internet-reachable via API Gateway HTTP API. Its IAM
role carries ONLY: Secrets Manager read on the webhook signing secret,
partnercentral:ListSolutions (catalog-conditioned), CloudWatch Logs,
X-Ray. No DynamoDB, no HubSpot private app token, no Partner Central
mutators. A parser/dependency CVE on this surface cannot reach the
ACE submit pipeline or the HubSpot deal record.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.config import load_config
from src.lambdas._ui_extension_common import err, ok, serve_request
from src.models import (
    AwsProductListResponse,
    AwsProductSummary,
    SolutionListResponse,
    SolutionSummary,
)

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/ui-extension/solutions",
        "/ui-extension/aws-products",
    }
)

RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
AWS_PRODUCTS_PATH = RESOURCES_DIR / "aws_products.json"

_aws_products_cache: list[AwsProductSummary] | None = None
_solutions_cache: dict[str, tuple[list[SolutionSummary], float]] = {}
_SOLUTIONS_TTL_SECONDS = 300


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
    # full TTL window. ListSolutions is on the 10/sec read quota; one
    # call per form open is fine.
    if summaries:
        _solutions_cache[catalog] = (summaries, now)
    return summaries


def _handle_solutions() -> dict[str, Any]:
    # The catalog the Lambda is deployed against is the only catalog
    # whose solutions we can list (IAM pins partnercentral:Catalog). We
    # ignore any ?catalog= query and echo the server-trusted catalog so
    # the client cannot misrepresent which catalog the response came from.
    config = load_config()
    catalog = (config.ace.catalog or "Sandbox").strip()
    solutions = _load_solutions(catalog)
    return ok(SolutionListResponse(catalog=catalog, solutions=solutions))


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
    return ok(AwsProductListResponse(products=_load_aws_products()))


def _dispatch(method: str, path: str, _raw_body: bytes, _event: dict[str, Any]) -> dict[str, Any]:
    if method == "GET" and path == "/ui-extension/solutions":
        return _handle_solutions()
    if method == "GET" and path == "/ui-extension/aws-products":
        return _handle_aws_products()
    return err(405, "validation_failed", message="method not allowed")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return serve_request(event, allowed_paths=_ALLOWED_PATHS, dispatch=_dispatch)
