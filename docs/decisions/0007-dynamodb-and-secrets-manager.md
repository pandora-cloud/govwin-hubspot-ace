# 7. DynamoDB for state, Secrets Manager for credentials

Date: 2026-06-04

## Status

Accepted

## Context

The integration needs to persist two distinct kinds of data on AWS:

1. **Per-opportunity state**: GovWin to HubSpot id mappings, per-opportunity update timestamps for incremental sync, the `ACE#{govwin_id}` rows that hold AWS opportunity ids, `ClientToken` values, `LastModifiedDate`, and engagement task ids. High-frequency reads and writes, key-value access pattern, no analytical queries.
2. **External-service credentials**: GovWin client id and secret, GovWin OAuth tokens (access plus refresh, with the 12-hour and 30-day TTLs), HubSpot Private App or Service Key token, HubSpot webhook signing secret. Read-only at runtime, must be encrypted at rest, must support rotation, must produce an audit trail.

Reasonable alternatives for the state surface include DynamoDB, Amazon Relational Database Service (RDS), or Amazon Elasticache. Reasonable alternatives for credentials include AWS Secrets Manager, AWS Systems Manager (SSM) Parameter Store with `SecureString`, or environment variables.

## Decision

**State: DynamoDB.**

- PAY_PER_REQUEST billing mode. No provisioned capacity to manage; cost scales with usage. At the project's expected scale (~1,000 to ~100,000 opportunities) the bill rounds to a few dollars per month.
- Customer-managed Key Management Service (KMS) key for encryption at rest, scoped via key policy, auditable via CloudTrail.
- DynamoDB Streams enabled to support future event-driven extensions (not currently consumed; left enabled because the cost is negligible and turning streams on later requires a table-level re-write).
- Two tables: `sync_state` (incremental cursors and per-opportunity update timestamps) and `entity_mappings` (`ACE#`, `EVT#`, `WHK#`, and the `GOVENTITY#`/`CONTACT#`/`COMPANY#` id-mapping rows).

**Credentials: Secrets Manager.**

- Secrets Manager over SSM Parameter Store `SecureString` because Secrets Manager has first-class support for automatic rotation, separate read-quota budgets from SSM, and a richer audit trail.
- Four secrets per deployment: GovWin client credentials (id plus secret plus password), GovWin OAuth token cache (access plus refresh, refreshed by the orchestrator), HubSpot REST token, and the HubSpot webhook signing secret. The first three are provisioned by `terraform/modules/secrets`; the webhook signing secret is provisioned by `terraform/modules/ace` because only the receiver Lambda in that module reads it.
- IAM grants are per-secret, with Lambda execution roles allowed only the specific secrets they need; the principle of least privilege.

## Consequences

Positive:

- The per-component cost is dominated by Secrets Manager's flat per-secret fee (~$0.40 each per month) and KMS's flat fee (~$1 per month), not by usage. Predictable at scale.
- KMS encryption gives a defensible "controlled at rest" story for federal compliance posture (NIST 800-53 SC-13, CMMC L2 SC.L2-3.13.11), with key use logged to CloudTrail.
- DynamoDB conditional writes underpin the atomic `ClientToken` reservation in ADR 0006; this would have been more complex on RDS.
- Rotating a HubSpot or GovWin credential is a Secrets Manager update plus a Lambda environment refresh; no code change.

Negative:

- DynamoDB lacks ad-hoc analytical queries. Anything more complex than key-value lookup requires either an export to Amazon Athena or a separate analytical store. Out of scope for this project.
- Secrets Manager costs more per secret than SSM Parameter Store does. At four secrets per deployment, the difference is roughly $1.50 to $2 per month, which we judged acceptable for the rotation and audit trail benefits.
- The customer-managed KMS key adds ~$1 per month over AWS-managed KMS keys, in exchange for explicit key policy control and CloudTrail visibility on key use.

Operational:

- The disaster recovery runbook in `docs/operations.md` covers the table restore procedure and the order in which to re-seed Secrets Manager values.
- A future move off DynamoDB would touch every Lambda and the disaster recovery runbook; this is the foundation that the rest of the integration sits on.
