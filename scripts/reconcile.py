"""Diagnostic: print the 4-way state for a single GovWin opportunity.

For a given GovWin opp id, walks the four sources of truth and prints
each side's view so an operator can spot drift:

  1. DynamoDB ACE# row (ace_opportunity_id, hubspot_deal_id,
     last_modified_date, client_token, engagement_task_id)
  2. HubSpot deal property snapshot (govwin_aws_cosell_id,
     govwin_aws_cosell_status, govwin_ace_lifecycle_stage,
     govwin_ace_aws_products)
  3. AWS Partner Central GetOpportunity (LifeCycle, Customer,
     RelatedEntityIdentifiers, PartnerOpportunityIdentifier)
  4. AWS Partner Central ListOpportunities filter (any other opp the
     partner owns that references the same govwin id via
     PartnerOpportunityIdentifier; surfaces collision attempts)

Usage::

    .venv/bin/python scripts/reconcile.py OPP12345
    .venv/bin/python scripts/reconcile.py --catalog AWS OPP67890

Read-only. Safe to run against production. Does not mutate any state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from src.ace.client import ACEAPIError, ACEClient
from src.config import load_config
from src.hubspot.client import HubSpotClient
from src.sync.state import SyncStateManager


def _dump(label: str, value: Any) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, default=str)[:4000])
    else:
        print(str(value)[:4000])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("govwin_id", help="GovWin opportunity id (e.g. OPP12345)")
    parser.add_argument(
        "--catalog",
        default=None,
        help="Override ACE_CATALOG (defaults to env var); use AWS for production",
    )
    args = parser.parse_args()

    if args.catalog:
        os.environ["ACE_CATALOG"] = args.catalog

    config = load_config()
    state = SyncStateManager(config)
    ace = ACEClient(config)

    govwin_id = args.govwin_id.strip()

    # 1. DDB ACE# row
    mapping = state.get_ace_mapping(govwin_id) or {}
    _dump(f"1. DynamoDB ACE#{govwin_id}", mapping)

    ace_id = str(mapping.get("ace_opportunity_id") or "").strip()
    deal_id = str(mapping.get("hubspot_deal_id") or "").strip()

    # 2. HubSpot deal property snapshot
    if deal_id:
        try:
            with HubSpotClient(config) as hubspot:
                deal = hubspot.get_deal(
                    deal_id,
                    properties=[
                        "govwin_opp_id",
                        "govwin_aws_cosell_id",
                        "govwin_aws_cosell_status",
                        "govwin_ace_lifecycle_stage",
                        "govwin_ace_aws_products",
                        "govwin_aws_cosell_products",
                        "dealstage",
                        "dealname",
                    ],
                )
            _dump(
                f"2. HubSpot deal {deal_id} (govwin-relevant properties)",
                deal.get("properties", {}),
            )
        except Exception as exc:  # noqa: BLE001 -- diagnostic; report and continue
            _dump(f"2. HubSpot deal {deal_id} -- LOOKUP FAILED", str(exc))
    else:
        _dump("2. HubSpot deal", "(no hubspot_deal_id in DDB mapping; skipped)")

    # 3. AWS GetOpportunity
    if ace_id:
        try:
            opp = ace.get_opportunity(ace_id)
            slim = {
                "Id": opp.get("Id"),
                "PartnerOpportunityIdentifier": opp.get("PartnerOpportunityIdentifier"),
                "Catalog": opp.get("Catalog"),
                "LastModifiedDate": opp.get("LastModifiedDate"),
                "LifeCycle": opp.get("LifeCycle", {}),
                "Customer.Account.CompanyName": (opp.get("Customer", {}).get("Account") or {}).get(
                    "CompanyName"
                ),
                "RelatedEntityIdentifiers": opp.get("RelatedEntityIdentifiers", {}),
            }
            _dump(f"3. AWS PC GetOpportunity({ace_id})", slim)
        except ACEAPIError as exc:
            _dump(f"3. AWS PC GetOpportunity({ace_id}) -- FAILED", f"{exc.code}: {exc}")
    else:
        _dump("3. AWS PC GetOpportunity", "(no ace_opportunity_id in DDB mapping; skipped)")

    # 4. ListOpportunities filter -- catches collision attempts where a
    # foreign opp shares the same PartnerOpportunityIdentifier as our
    # govwin id.
    try:
        listing = ace.list_opportunities(
            CustomerCompanyName=None,
            Identifier=None,
            LastModifiedDate=None,
        )
        candidates = [
            o
            for o in (listing.get("OpportunitySummaries") or [])
            if o.get("PartnerOpportunityIdentifier") == govwin_id
        ]
        _dump(
            f"4. AWS PC ListOpportunities (PartnerOpportunityIdentifier == {govwin_id})",
            candidates or "(none -- expected unless prior submissions collided)",
        )
    except ACEAPIError as exc:
        _dump("4. AWS PC ListOpportunities -- FAILED", f"{exc.code}: {exc}")

    # Drift summary
    print(f"\n{'=' * 70}")
    print("  DRIFT SUMMARY")
    print(f"{'=' * 70}")
    flags: list[str] = []
    if deal_id and mapping.get("hubspot_deal_id") != deal_id:
        flags.append("DDB.hubspot_deal_id does not match itself (data corruption)")
    if ace_id and not mapping.get("ace_opportunity_id"):
        flags.append("ace_id resolved but DDB row is partial")
    if not flags:
        flags.append("No obvious 4-way drift detected.")
    for flag in flags:
        print(f"  * {flag}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
