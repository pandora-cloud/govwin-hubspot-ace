# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs) for significant
technical and project decisions in the govwin-hubspot-integration repository.

An ADR captures the context, options, decision, and consequences of one
choice that shapes the system. ADRs are dated and numbered sequentially.
Once accepted, an ADR is not edited; if a decision changes, a new ADR
supersedes the old one and the older record is updated only to note the
superseding ADR.

The format follows Michael Nygard's lightweight template: Status, Context,
Decision, Consequences.

## Accepted records

| # | Title | Status |
|---|---|---|
| [0001](0001-license-apache-2.0.md) | License: Apache License 2.0 | Accepted |

## Decisions worth recording (backlog)

These decisions are documented in code or in commentary today, but warrant
their own ADRs so the rationale survives independently of the source. They
are listed in rough priority order; contributions welcome.

- **Sandbox-first ACE catalog default.** Why production deployments must
  explicitly set `ace_catalog = "AWS"` and why the IAM policy carries a
  `partnercentral:Catalog: Sandbox` condition in Sandbox mode.
- **Direct AWS Partner Central Selling API client instead of a paid
  third-party connector.** Why the integration owns its boto3 client and
  the resulting tradeoffs.
- **Orchestrator + worker via SQS instead of a Step Function Map.** Why
  the inter-state payload limit and per-batch retry semantics drove the
  current Lambda + SQS topology.
- **Two-queue webhook routing.** Why deal-stage transitions and content
  property changes consume separate SQS queues (avoiding the
  load-balancing-vs-fan-out trap of multiple consumers on one queue).
- **Atomic ClientToken reservation in DynamoDB.** Why both
  `CreateOpportunity` and `StartEngagementFromOpportunityTask` reserve
  tokens with conditional writes so concurrent SQS retries cannot mint
  duplicate ACE opportunities.
- **DynamoDB for state, Secrets Manager for credentials.** Why each is
  preferred over alternatives such as RDS or SSM Parameter Store.
- **HubSpot batch upsert with `idProperty` instead of search-before-upsert.**
  Why the integration relies on idempotent upserts to halve the HubSpot
  API call count.
- **Marked-for-sync default in GovWin discovery.** Why only BD-marked
  opportunities flow into HubSpot rather than every opportunity in the
  GovWin tenant.
- **ARM64 (Graviton2) Lambda runtime.** Why the integration deploys on
  ARM64 and the cost / performance tradeoff that motivated it.

## Writing a new ADR

1. Pick the next sequential number.
2. Copy the structure from
   [0001-license-apache-2.0.md](0001-license-apache-2.0.md): four sections
   (Status, Context, Decision, Consequences), one-sentence-per-line
   Markdown, dated.
3. Add an entry to the table above with the new title and status.
4. Open the change as a pull request like any other documentation change.

ADRs are durable. Treat them as appendix-only history: the goal is for a
future reader to understand what was true at the time of acceptance.
