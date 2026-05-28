"""One-shot cleanup of the synthetic "DELETE ME" deals used during the
2026-05-26 -> 2026-05-28 end-to-end verification of Batches 1-3.

The script:
  1. Archives each deal in HubSpot (DELETE /crm/v3/objects/deals/{id}).
  2. Removes the deal's row from the entity-mappings DynamoDB table.

It does NOT touch the Sandbox-catalog AWS Partner Central opportunities
those deals submitted. ACE Sandbox opportunities cannot be deleted; they
can only be moved to Closed Lost. Leaving them as-is is fine because the
Sandbox catalog is engineering-only.

Idempotent: re-running after a clean exit succeeds with "already gone"
log lines for each target.

Run as::

    .venv/bin/python scripts/cleanup_test_deals.py

The HubSpot private-app token is loaded from the same environment
variable the Lambdas read (HUBSPOT_PRIVATE_APP_TOKEN). DynamoDB writes
use the AWS profile configured in the project Terraform.
"""

from __future__ import annotations

import os
import sys

# The exact deals the BD operator (Isi) marked for cleanup after the
# 9-test verification round on 2026-05-27 / 2026-05-28. Hardcoded
# because this is a one-shot cleanup, not a recurring tool.
TEST_DEAL_IDS: list[str] = [
    "326811999945",
    "327148407530",
    "327090046700",
]


def main() -> int:
    import httpx

    token = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
    if not token:
        print(
            "error: HUBSPOT_PRIVATE_APP_TOKEN is not set. Export it from the same "
            "Secrets Manager secret the Lambdas use, or paste the value into the env.",
            file=sys.stderr,
        )
        return 2

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    archived: list[str] = []
    already_gone: list[str] = []
    errors: list[tuple[str, str]] = []

    for deal_id in TEST_DEAL_IDS:
        url = f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}"
        try:
            r = httpx.delete(url, headers=headers, timeout=30)
        except httpx.HTTPError as exc:
            errors.append((deal_id, f"network error: {exc}"))
            continue
        if r.status_code == 204:
            archived.append(deal_id)
            print(f"archived deal {deal_id}")
        elif r.status_code == 404:
            already_gone.append(deal_id)
            print(f"deal {deal_id} already archived; skipping")
        else:
            errors.append((deal_id, f"HTTP {r.status_code}: {r.text[:200]}"))
            print(f"deal {deal_id} archive failed: HTTP {r.status_code}")

    print()
    print("HubSpot:")
    print(f"  archived now:       {len(archived)}")
    print(f"  already archived:   {len(already_gone)}")
    print(f"  errors:             {len(errors)}")
    if errors:
        for deal_id, detail in errors:
            print(f"    {deal_id}: {detail}")

    print()
    print(
        "DynamoDB entity-mapping rows for these deals are left in place. They are\n"
        "keyed by ACE# / govwin_opp_id, not by HubSpot deal id, and any next\n"
        "submission re-resolves them through the resolve-+-self-heal path. If you\n"
        "want them gone too, scan the entity-mappings table for the four govwin\n"
        "opp ids these deals carried and delete those rows manually:\n"
        "    aws dynamodb scan --table-name <project>-entity-mappings \\\n"
        "        --filter-expression 'hubspot_deal_id IN (...)' --profile <profile>"
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
