"""Generate ``docs/reference/hubspot-properties.md`` from the authoritative
property definitions in :mod:`src.hubspot.properties`.

Run via :code:`make docs-properties` (preferred) or directly:

.. code-block:: shell

    python scripts/generate_hubspot_properties_doc.py

The output file is overwritten in place. CI runs the same generation and
diffs the result against the committed file; a non-empty diff fails the
build, so the reference cannot drift away from the code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "docs" / "reference" / "hubspot-properties.md"

sys.path.insert(0, str(REPO_ROOT))

from src.hubspot.properties import (  # noqa: E402  (path manipulation above)
    COMPANY_PROPERTIES,
    CONTACT_PROPERTIES,
    DEAL_PROPERTIES,
    GOVWIN_STATUS_TO_STAGE,
    PIPELINE_NAME,
)

#------Section Renderers------


def _md_escape(value: Any) -> str:
    """Escape pipe characters so they don't break the markdown table."""
    if value is None:
        return ""
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _render_property_table(title: str, anchor: str, properties: list[Any]) -> str:
    """Render one markdown table for a property list, sorted by group then name.

    ``properties`` is a list of ``HubSpotProperty`` pydantic models from
    :mod:`src.hubspot.properties`.
    """
    rows = sorted(
        properties,
        key=lambda p: (p.group_name or "", p.name or ""),
    )

    lines = [
        f"## {title}",
        "",
        f"**Total:** {len(properties)} properties.",
        "",
        "| Name | Label | Type | Field Type | Group | Description |",
        "|---|---|---|---|---|---|",
    ]
    for p in rows:
        lines.append(
            "| "
            + " | ".join(
                _md_escape(getattr(p, attr, ""))
                for attr in ("name", "label", "type", "field_type", "group_name", "description")
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_stage_map() -> str:
    """Render the GovWin status -> HubSpot stage label map sorted by stage."""
    # Group statuses under each stage label so the doc reads top-down by stage.
    by_stage: dict[str, list[str]] = {}
    for status, stage in GOVWIN_STATUS_TO_STAGE.items():
        by_stage.setdefault(stage, []).append(status)

    lines = [
        "## Pipeline stage mapping",
        "",
        f'Default pipeline label: **"{PIPELINE_NAME}"**.',
        "",
        "Map of GovWin opportunity `status` values to HubSpot pipeline stage labels.",
        "Statuses not in this map fall through to the `Other` stage with a CloudWatch",
        "warning so the unmapped status surfaces for review.",
        "",
        "| HubSpot Stage Label | GovWin Statuses |",
        "|---|---|",
    ]
    for stage in sorted(by_stage):
        statuses = ", ".join(f"`{s}`" for s in sorted(by_stage[stage]))
        lines.append(f"| {stage} | {statuses} |")
    lines.append("")
    return "\n".join(lines)


#------Main Driver------


def render() -> str:
    """Render the full reference document as a single Markdown string."""
    deal_total = len(DEAL_PROPERTIES)
    company_total = len(COMPANY_PROPERTIES)
    contact_total = len(CONTACT_PROPERTIES)
    grand_total = deal_total + company_total + contact_total

    header = [
        "<!--",
        "GENERATED FILE. Do not edit by hand.",
        "Source of truth: src/hubspot/properties.py",
        "Regenerate via: make docs-properties",
        "-->",
        "",
        "# HubSpot Custom Properties Reference",
        "",
        (
            "Authoritative listing of every HubSpot custom property the integration "
            "creates, generated from the property definitions in "
            "[`src/hubspot/properties.py`](../../src/hubspot/properties.py)."
        ),
        "",
        f"**Total:** {grand_total} properties ({deal_total} deal + "
        f"{company_total} company + {contact_total} contact). "
        "All custom properties use the `govwin_` prefix; built-in HubSpot "
        "properties such as `dealname`, `amount`, `closedate`, and `description` "
        "are populated directly by the sync without a custom-property definition.",
        "",
    ]

    sections = [
        _render_property_table("Deal properties", "deal", DEAL_PROPERTIES),
        _render_property_table("Company properties", "company", COMPANY_PROPERTIES),
        _render_property_table("Contact properties", "contact", CONTACT_PROPERTIES),
        _render_stage_map(),
    ]

    return "\n".join(header + sections)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = render()
    if not output.endswith("\n"):
        output += "\n"
    OUTPUT_PATH.write_text(output)
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
