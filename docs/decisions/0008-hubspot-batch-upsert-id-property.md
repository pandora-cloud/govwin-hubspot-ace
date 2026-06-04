# 8. HubSpot batch upsert via idProperty, not search-before-upsert

Date: 2026-06-04

## Status

Accepted

## Context

The GovWin worker Lambda needs to either create new HubSpot deals, companies, and contacts for newly synced opportunities, or update the existing records if they have already been synced. The HubSpot CRM API v3 has multiple paths for this:

1. **Search-before-upsert.** For each opportunity, issue a `POST /crm/v3/objects/deals/search` to find the deal by a `govwin_opp_id` filter. If a result is returned, `PATCH` it. If not, `POST` a new deal. Two API calls per deal at minimum, more for company and contact rows.
2. **Batch upsert with `idProperty`.** Use `POST /crm/v3/objects/deals/batch/upsert` with `idProperty: "govwin_opp_id"`. HubSpot performs the lookup server-side and either creates or updates each row in the batch. One API call per batch (up to 100 rows), regardless of how many are new versus existing.

HubSpot enforces a sliding-window rate limit of 100 requests per 10 seconds. The search-before-upsert path doubles the API call count and burns the rate budget faster.

## Decision

Use the batch upsert path with `idProperty` set to the unique GovWin id on the deal, company, and contact custom properties (`govwin_opp_id`, `govwin_company_id`, `govwin_contact_id`).

The HubSpot client (`src/hubspot/client.py`) exposes one `batch_upsert` method per object type. The worker Lambda groups all opportunities in a batch by type, calls each upsert, and writes the response back into DynamoDB along with the assigned HubSpot id.

A sliding-window rate limiter inside the client smooths the call rate to stay inside HubSpot's published budget, with backoff and retry on 429 responses.

## Consequences

Positive:

- API call count is roughly halved compared to search-before-upsert. The worker stays well inside HubSpot's 100 per 10 seconds budget even at peak throughput.
- The race condition where two concurrent invocations both search, both find no result, and both create duplicate records is eliminated by relying on HubSpot's server-side uniqueness check on `idProperty`.
- The upsert API is the right level of abstraction: "ensure this record exists with these properties," not "imperatively check then choose to insert or update."

Negative:

- All identifying ids (`govwin_opp_id` and friends) have to be HubSpot custom properties with the "Has unique value" constraint enabled. They are; documented in `src/hubspot/properties.py`.
- The upsert API does not return a meaningful diff between the prior and new values; if the worker needs to know what changed, it has to compute the diff itself or read the row back. Currently this is acceptable: the worker is a one-way push, no diff is needed.

Operational:

- The HubSpot 4xx error redactor in `_redact_hubspot_error_body` strips `propertyValue` and `localizedErrorMessage` from logs and SNS alerts so a failed upsert does not leak the GovWin or customer data that was being upserted. See `src/hubspot/client.py`.
- A future schema change in HubSpot (renaming `govwin_opp_id`, for example) would require a coordinated migration of both the custom property and the `idProperty` references in the worker. This is a known coupling point; left as a documented constraint.
