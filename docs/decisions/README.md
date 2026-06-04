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
| [0002](0002-sandbox-first-ace-catalog.md) | Sandbox-first ACE catalog default | Accepted |
| [0003](0003-direct-partner-central-client.md) | Direct AWS Partner Central Selling API client, not a paid third-party connector | Accepted |
| [0004](0004-orchestrator-worker-sqs.md) | Orchestrator and worker via SQS, not a Step Functions Map state | Accepted |
| [0005](0005-two-queue-webhook-routing.md) | Two-queue webhook routing for HubSpot inbound events | Accepted |
| [0006](0006-atomic-client-token-reservation.md) | Atomic ClientToken reservation in DynamoDB | Accepted |
| [0007](0007-dynamodb-and-secrets-manager.md) | DynamoDB for state, Secrets Manager for credentials | Accepted |
| [0008](0008-hubspot-batch-upsert-id-property.md) | HubSpot batch upsert via idProperty, not search-before-upsert | Accepted |
| [0009](0009-marked-for-sync-default.md) | Marked-for-sync default for GovWin discovery | Accepted |
| [0010](0010-lambda-arm64-runtime.md) | ARM64 (Graviton2) Lambda runtime | Accepted |

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
