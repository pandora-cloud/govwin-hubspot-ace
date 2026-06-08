# `terraform/modules/dynamodb`

Provisions the two DynamoDB tables the integration uses for sync state and entity mappings.

## What it creates

- `${name_prefix}-sync-state`: the `SYNC_CURSOR` row plus per-opportunity `OPP#{govwin_id}` rows that track the last-seen GovWin `updateDate` for change detection. Composite key `(pk, sk)`.
- `${name_prefix}-entity-mappings`: GovWin-to-HubSpot id mappings (`GOVENTITY#`, `CONTACT#`, `COMPANY#` rows), `ACE#{govwin_id}` rows holding AWS opportunity ids / ClientTokens / LastModifiedDate for optimistic locking, `EVT#{event_id}` rows for EventBridge event dedup, and `WHK#{signature_fingerprint}` rows for webhook replay-attack defense. Composite key `(pk, sk)`. TTLs vary per row pattern (180 days for sync state, 365 days for ACE mappings, 24 hours for EventBridge dedup, 10 minutes for webhook reservations); see `docs/architecture.md` for the full list.

Both tables are PAY_PER_REQUEST, encrypted at rest under the pipeline KMS CMK, with DynamoDB Streams enabled (currently unconsumed but cheap to leave on for future event-driven extensions), and point-in-time recovery enabled for disaster recovery.

## Required inputs

`name_prefix`, `kms_key_arn` (from `modules/kms`).

## Outputs

Table names and ARNs for `modules/lambda` and `modules/ace` to grant scoped IAM access.

## Depends on

`modules/kms`. Applies before `modules/lambda`, `modules/ace`, and `modules/govwin_sync`.
