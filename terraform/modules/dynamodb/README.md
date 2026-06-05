# `terraform/modules/dynamodb`

Provisions the two DynamoDB tables the integration uses for sync state and entity mappings.

## What it creates

- `${name_prefix}-sync-state`: per-opportunity update timestamps, GovWin OAuth token cursors, sync run state. Composite key `(pk, sk)`.
- `${name_prefix}-entity-mappings`: GovWin to HubSpot id mappings, the `ACE#{govwin_id}` rows that hold AWS opportunity ids / ClientTokens / LastModifiedDate, the `WHK#{event_id}` rows for webhook replay detection. Composite key `(pk, sk)`.

Both tables are PAY_PER_REQUEST, encrypted at rest under the pipeline KMS CMK, with DynamoDB Streams enabled (currently unconsumed but cheap to leave on for future event-driven extensions), and point-in-time recovery enabled for disaster recovery.

## Required inputs

`name_prefix`, `kms_key_arn` (from `modules/kms`).

## Outputs

Table names and ARNs for `modules/lambda` and `modules/ace` to grant scoped IAM access.

## Depends on

`modules/kms`. Applies before `modules/lambda`, `modules/ace`, and `modules/govwin_sync`.
