# 9. Marked-for-sync default for GovWin discovery

Date: 2026-06-04

## Status

Accepted

## Context

The GovWin Web Service Application Programming Interface (WSAPI) exposes several distinct ways to discover opportunities:

1. **Marked for download** (`?markedVersion=2.2`): the BD user explicitly marks an opportunity in the GovWin Intelligence Quotient (IQ) web user interface by clicking "Add to Web Services Download." Only marked opportunities are returned. Marking is per-user and persists across sync runs.
2. **Saved search** (`?savedSearchId=...`): the BD user creates a saved search in GovWin IQ; the WSAPI returns whatever matches the saved search criteria at sync time.
3. **Bookmarked** (`?markedOpps=true`): returns opportunities the calling user has bookmarked in GovWin IQ.
4. **Date range** (`?oppSelectionDateFrom=...`): returns all opportunities updated since a given date, with no other filter.

The two extremes are "sync everything" (mode 4 with an early date) and "sync only what BD explicitly wants" (mode 1). A GovWin tenant for an established federal partner typically holds tens of thousands of opportunities; pulling all of them into HubSpot would dilute the deal pipeline with records BD has no interest in, blow through the GovWin 4,000 calls per hour quota on a first sync, and turn HubSpot into a search-and-filter exercise for the BD team.

## Decision

The default discovery mode is **marked for sync** (mode 1). Only opportunities the BD team explicitly marks in GovWin IQ are pulled into HubSpot.

The other three modes are supported and selectable via Terraform variables (`govwin_saved_search_id`, `govwin_bookmarked_only`, `govwin_marked_version`). Operators who prefer a saved-search workflow can opt in; the marked-for-sync default is what a first-time deployer gets without thinking about it.

The integration treats marking as the BD team's explicit intent signal. Unmarking does not delete a previously synced HubSpot deal; once a deal has been pulled in and possibly edited by BD, removing it from GovWin's marked list does not silently delete it from HubSpot. Operators can clean up unwanted deals in HubSpot directly.

## Consequences

Positive:

- HubSpot's deal pipeline reflects the BD team's pursuit intent rather than the GovWin tenant's entire catalog.
- Initial sync cost is bounded by what the BD team has actually marked, not by tenant size.
- Marking is a familiar BD-side workflow in GovWin IQ; the integration does not introduce a new user interface for opportunity selection.
- The 4,000 calls per hour GovWin quota is much harder to hit when discovery is scoped to a curated list of tens or low hundreds of opportunities.

Negative:

- BD must remember to mark opportunities in GovWin IQ for them to flow through. A deal that exists in GovWin but is not marked simply does not appear in HubSpot. This is documented in `docs/bd-user-guide.md` and is the most common "missing deal" support question.
- Unmarking behavior (no automatic deletion) means BD's HubSpot pipeline can drift from the marked set over time. Acceptable for a pipeline that BD is actively curating; would be wrong for a system that aims to reflect GovWin's authoritative state.

Operational:

- The marked-for-sync discovery is the basis of the end-to-end smoke matrix in `docs/testing-in-your-account.md`. Operators run the smoke against marked test opportunities and verify each opportunity type and status mapping.
- Switching the discovery mode after deployment is a Terraform variable change; no data migration required, but the implications for BD's existing pipeline should be considered before flipping.
