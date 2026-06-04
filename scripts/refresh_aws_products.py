"""Refresh ``resources/aws_products.json`` from AWS's canonical catalog.

Audience: maintainer. Re-fetches the AWS-published AwsProducts catalog
and commits the result so downstream deployers ship a current copy.

AWS Partner Central publishes the AwsProducts catalog used by
``AssociateOpportunity(RelatedEntityType=AwsProducts)`` at a public GitHub
URL, linked from the AssociateOpportunity API reference page itself:

    https://github.com/aws-samples/partner-crm-integration-samples
                                /blob/main/resources/aws_products.json

This script downloads the raw JSON, wraps it in the local schema (version
date + source attribution + the manual "Other" escape-hatch entry the form
depends on), and writes ``resources/aws_products.json``. The schema wrapper
is what the backend Lambda and HubSpot setup script consume.

Two source modes are supported:

* ``--source github`` (default): fetch from the canonical raw GitHub URL.
  Idempotent; safe to run on every release.
* ``--source csv --csv <path>``: parse a CSV exported from Partner Central
  (ACE Pipeline Manager > Bulk operations > Import). Useful when GitHub is
  blocked from the operator's network or when the GitHub copy lags the
  authenticated Partner Central catalog.

In both modes the script prints a diff of added / removed identifiers so
reviewers can see exactly what AWS changed since the last refresh.

Usage:
    uv run scripts/refresh_aws_products.py                    # GitHub fetch
    uv run scripts/refresh_aws_products.py --source csv \\
                              --csv ~/Downloads/aws_products.csv

Exit codes:
    0  resources/aws_products.json updated (or already in sync)
    1  source unreachable or unreadable
    2  source schema does not match expected columns/fields
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "resources" / "aws_products.json"
DEFAULT_CSV = Path("/tmp/aws_products.csv")
CANONICAL_URL = (
    "https://raw.githubusercontent.com/aws-samples/"
    "partner-crm-integration-samples/main/resources/aws_products.json"
)

# CSV columns the Partner Central export ships (as of May 2026). Keep in sync
# with what the bulk-import page actually downloads; AWS has changed column
# names twice in the past year, so we tolerate either snake_case or PascalCase
# headers.
EXPECTED_HEADERS = {
    "identifier": ("identifier", "Identifier", "ProductIdentifier", "Product Identifier"),
    "name": ("name", "Name", "ProductName", "Product Name"),
    "family": ("family", "Family", "Category", "Service Family"),
}

# Always preserved across refreshes. The form treats Other as the free-text
# escape hatch when AWS hasn't catalogued a service the BD team needs.
OTHER_ENTRY = {
    "Identifier": "Other",
    "Name": "Other AWS Service (specify in description)",
    "Family": "Other",
}


def _resolve_column(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    """Pick the first header that matches any alias, case-insensitively."""
    lower_to_actual = {h.lower(): h for h in headers}
    for alias in aliases:
        actual = lower_to_actual.get(alias.lower())
        if actual:
            return actual
    return None


def _load_csv(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        col_id = _resolve_column(headers, EXPECTED_HEADERS["identifier"])
        col_name = _resolve_column(headers, EXPECTED_HEADERS["name"])
        col_family = _resolve_column(headers, EXPECTED_HEADERS["family"])
        if not col_id or not col_name:
            print(
                "ERROR: CSV missing required columns. Found headers: "
                f"{headers}. Expected at least an identifier column "
                f"(one of {EXPECTED_HEADERS['identifier']}) and a name column "
                f"(one of {EXPECTED_HEADERS['name']}).",
                file=sys.stderr,
            )
            sys.exit(2)
        out = []
        for row in reader:
            identifier = (row.get(col_id) or "").strip()
            name = (row.get(col_name) or "").strip()
            family = (row.get(col_family) or "").strip() if col_family else ""
            if identifier and identifier.lower() != "other":
                out.append({"Identifier": identifier, "Name": name, "Family": family})
        return out


def _diff(old: list[dict[str, str]], new: list[dict[str, str]]) -> tuple[set[str], set[str]]:
    old_ids = {p["Identifier"] for p in old}
    new_ids = {p["Identifier"] for p in new}
    return new_ids - old_ids, old_ids - new_ids


def _load_github() -> list[dict[str, str]]:
    """Fetch the canonical JSON list from the public aws-samples repo."""
    try:
        with urllib.request.urlopen(CANONICAL_URL, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"ERROR: failed to fetch {CANONICAL_URL}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: canonical JSON did not parse: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, list):
        print("ERROR: canonical JSON top-level is not a list", file=sys.stderr)
        sys.exit(2)
    out: list[dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("Identifier", "")).strip()
        if not identifier or identifier == "Other":
            continue
        normalized = {
            "Identifier": identifier,
            "Name": str(entry.get("Name", "")).strip(),
            "Family": str(entry.get("Family", "")).strip(),
        }
        desc = entry.get("Description")
        if desc:
            desc_str = str(desc).strip()
            if 0 < len(desc_str) <= 250:
                normalized["Description"] = desc_str
        out.append(normalized)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("github", "csv"),
        default="github",
        help="Where to read the catalog from (default: github canonical raw JSON)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=(
            "Path to the Partner Central AWS Products CSV (only with "
            f"--source csv; default: {DEFAULT_CSV})"
        ),
    )
    args = parser.parse_args()

    if args.source == "github":
        new_products = _load_github()
    else:
        if not args.csv.exists():
            print(f"ERROR: CSV not found at {args.csv}", file=sys.stderr)
            sys.exit(1)
        new_products = _load_csv(args.csv)
    new_products.append(OTHER_ENTRY)

    existing = json.loads(JSON_PATH.read_text()) if JSON_PATH.exists() else {"products": []}
    added, removed = _diff(existing.get("products", []), new_products)

    payload = {
        "version": __import__("datetime").date.today().isoformat(),
        "source": (
            "Refreshed from AWS Partner Central > ACE Pipeline Manager > Import. "
            "See scripts/refresh_aws_products.py for procedure."
        ),
        "products": new_products,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {JSON_PATH} ({len(new_products)} products)")
    if added:
        print(f"  added ({len(added)}): {sorted(added)}")
    if removed:
        print(f"  removed ({len(removed)}): {sorted(removed)}")
    if not added and not removed:
        print("  no identifier changes")


if __name__ == "__main__":
    main()
