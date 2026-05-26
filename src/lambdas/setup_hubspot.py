"""One-time HubSpot setup: create custom properties, groups, and pipeline.

Also seeds the *dynamic* option sets for properties whose values come from
sources outside this codebase:

* ``govwin_ace_aws_products`` -- options sourced from
  ``resources/aws_products.json`` (513 AWS product Identifiers, refreshed
  via ``scripts/refresh_aws_products.py``).
* ``govwin_ace_solution_id`` -- options sourced from a live ACE
  ``ListSolutions`` call so BD's HubSpot dropdown stays in sync with the
  Pandora Cloud Solutions catalog registered in Partner Central.

The static option sets (delivery model, partner need, use case,
competitor, etc.) are baked into ``src/hubspot/properties.py`` and
patched in by ``ensure_property`` on every run.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.config import load_config
from src.hubspot.client import HubSpotAPIError, HubSpotClient
from src.hubspot.properties import DEAL_PROPERTIES
from src.models import HubSpotProperty

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# When deployed, resources/ ships at /var/task/resources via the source zip.
_RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
AWS_PRODUCTS_PATH = _RESOURCES_DIR / "aws_products.json"


def _aws_product_options() -> list[dict[str, str]]:
    """Read resources/aws_products.json into HubSpot option dicts.

    Skips the local "Other" escape-hatch entry; AWS rejects unknown
    Identifiers on AssociateOpportunity, so it should not be on the
    HubSpot dropdown either. Labels include the product family so BD can
    scan the picker visually (e.g. "Amazon S3 (Storage)").
    """
    if not AWS_PRODUCTS_PATH.exists():
        logger.warning(
            "aws_products.json missing at %s; AWS Products picker will be empty",
            AWS_PRODUCTS_PATH,
        )
        return []
    payload = json.loads(AWS_PRODUCTS_PATH.read_text(encoding="utf-8"))
    products = payload.get("products") if isinstance(payload, dict) else payload
    options: list[dict[str, str]] = []
    for entry in products or []:
        identifier = str(entry.get("Identifier", "")).strip()
        if not identifier or identifier == "Other":
            continue
        name = str(entry.get("Name", "")).strip() or identifier
        family = str(entry.get("Family", "")).strip()
        label = f"{name} ({family})" if family and family != "Other" else name
        options.append({"label": label, "value": identifier})
    return options


def _solution_options(config: Any) -> list[dict[str, str]]:
    """Call ListSolutions and turn the Active set into HubSpot options.

    The Sandbox catalog typically has zero Solutions. When that happens
    we return an empty option list and the form's SolutionPicker hides
    itself; the mapper falls back to Project.OtherSolutionDescription.
    """
    try:
        ace = ACEClient(config)
        solutions = ace.list_active_solutions()
    except ACEAPIError as exc:
        logger.warning(
            "ListSolutions failed during setup; leaving govwin_ace_solution_id "
            "options empty. error=%s",
            exc,
        )
        return []
    options: list[dict[str, str]] = []
    for s in solutions:
        identifier = str(s.get("Id", "")).strip()
        if not identifier:
            continue
        name = str(s.get("Name", "")).strip() or identifier
        category = str(s.get("Category", "")).strip()
        label = f"{name} ({category})" if category else name
        options.append({"label": label, "value": identifier})
    return options


def _patch_property_options(
    properties: list[HubSpotProperty],
    name: str,
    options: list[dict[str, str]],
) -> None:
    """Mutate the matching HubSpotProperty.options in-place so ensure_property
    pushes the new option set on the next PATCH."""
    for prop in properties:
        if prop.name == name:
            prop.options = options
            return
    raise KeyError(f"no HubSpotProperty with name={name!r}")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one-time HubSpot setup.

    Creates:
    - Property group "govwin" on deals, companies, contacts
    - All custom properties (static option sets from properties.py)
    - GovWin deal pipeline with stages (verified, not created)

    Also seeds the dynamic option sets for ``govwin_ace_aws_products``
    (from resources/aws_products.json) and ``govwin_ace_solution_id``
    (from ACE ListSolutions).

    This Lambda is idempotent and safe to run multiple times. The
    ``ensure_property`` helper PATCHes options on every run so option-set
    drift (e.g. AWS publishing new products) flows in on the next deploy.
    """
    config = load_config()

    # Seed dynamic option sets BEFORE running ensure_all_properties so the
    # PATCH carries the right options. Both are best-effort: empty options
    # are still valid (the form just shows an empty picker for that field).
    try:
        aws_product_options = _aws_product_options()
        _patch_property_options(
            DEAL_PROPERTIES, "govwin_ace_aws_products", aws_product_options
        )
        logger.info("seeded %d AWS Products options", len(aws_product_options))
    except (KeyError, json.JSONDecodeError) as exc:
        logger.warning("failed to seed AWS Products options: %s", exc)

    try:
        solution_options = _solution_options(config)
        _patch_property_options(
            DEAL_PROPERTIES, "govwin_ace_solution_id", solution_options
        )
        logger.info("seeded %d Solution options", len(solution_options))
    except (KeyError, HubSpotAPIError, ACEAPIError) as exc:
        logger.warning("failed to seed Solution options: %s", exc)

    with HubSpotClient(config) as client:
        result = client.setup()

    logger.info(
        "HubSpot setup complete: pipeline=%s, %d deal props, %d company props, %d contact props",
        result["pipeline_id"],
        result["deal_properties"],
        result["company_properties"],
        result["contact_properties"],
    )
    return result
