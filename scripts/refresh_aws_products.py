"""Refresh ``resources/aws_products.json`` from the authoritative Partner Central catalog.

The AwsProducts catalog used by ``AssociateOpportunity(RelatedEntityType=AwsProducts)``
is published only inside an authenticated AWS Partner Central session. There is
no public URL and no ``ListAwsProducts`` API in the partnercentral-selling SDK
(as of May 2026). To refresh the local JSON:

1. Log in to https://partnercentral.awspartner.com
2. Navigate to ACE Pipeline Manager → Bulk operations → Import (legacy URL:
   https://partnercentral.awspartner.com/partnercentral2/s/import-export).
3. Download the **AWS Products** reference CSV.
4. Save it as ``/tmp/aws_products.csv`` (or pass ``--csv <path>``).
5. Run this script. It rewrites ``resources/aws_products.json`` from the CSV,
   preserving the manual "Other" escape-hatch entry the form depends on.

The script is idempotent and prints a diff of added / removed identifiers so
reviewers can see exactly what AWS changed since the last refresh.

Usage:
    uv run scripts/refresh_aws_products.py            # /tmp/aws_products.csv
    uv run scripts/refresh_aws_products.py --csv path/to/file.csv

Exit codes:
    0  resources/aws_products.json updated (or already in sync)
    1  CSV missing or unreadable
    2  CSV header does not match expected columns
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "resources" / "aws_products.json"
DEFAULT_CSV = Path("/tmp/aws_products.csv")

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to the Partner Central AWS Products CSV (default: {DEFAULT_CSV})",
    )
    args = parser.parse_args()

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
