# 6. Atomic ClientToken reservation in DynamoDB

Date: 2026-06-04

## Status

Accepted

## Context

AWS Partner Central's `CreateOpportunity` and `StartEngagementFromOpportunityTask` operations both accept a `ClientToken` parameter for idempotency. Repeated calls with the same `ClientToken` return the result of the original call rather than creating a duplicate. Without a `ClientToken`, concurrent retries can create duplicate opportunities or duplicate engagement tasks, and AWS Partner Central has no `DeleteOpportunity` API to undo this.

The integration uses SQS to decouple the webhook receiver from `submit_to_ace`. SQS at-least-once delivery means the same message can be delivered twice in quick succession (visibility timeout race, failed batch retries, deliberate redrives). If two concurrent invocations of `submit_to_ace` for the same deal each mint a fresh UUID for the `ClientToken`, they will be treated by AWS as two separate submissions and both succeed, producing exactly the duplicate opportunity we are trying to avoid.

The naive fix (mint the `ClientToken` inside the Lambda and use a deterministic value based on the deal id) has the issue that the token still has to be persisted somewhere for the resume-from-step flow; the next retry needs to know what value to use. Persisting after the first API call is too late: the race already happened.

## Decision

The `ClientToken` is generated and persisted atomically in DynamoDB before the first API call, using a conditional write. The flow for `submit_to_ace`:

1. Compute the DynamoDB key for the deal (an `ACE#{govwin_id}` partition key pattern).
2. Issue a conditional `PutItem` with `ConditionExpression: attribute_not_exists(client_token)`, supplying a freshly generated UUID.
3. If the put succeeds, this invocation owns the submission for this deal. Proceed with the AWS API call using the persisted token.
4. If the put fails with `ConditionalCheckFailedException`, another invocation already reserved the token. Read the existing row to recover the value, and use it in the AWS API call. Both invocations will succeed at the AWS level because they share the same `ClientToken`.

The same pattern applies to `StartEngagementFromOpportunityTask`, with its own separate `ClientToken` and its own atomic reservation step.

## Consequences

Positive:

- Concurrent SQS redeliveries cannot create duplicate ACE opportunities. The atomic conditional write ensures exactly one `ClientToken` per deal per operation, regardless of how many Lambdas race.
- The resume-from-step flow works naturally: a retried message reloads the row, sees the persisted token, and reuses it.
- The DynamoDB row also stores the AWS opportunity id, the engagement task id, and the `LastModifiedDate` (used for optimistic locking by `update_in_ace`), so the deal's AWS-side state has one persistent record.

Negative:

- Two DynamoDB conditional writes are required on the happy path of `submit_to_ace` (one for `CreateOpportunity`, one for `StartEngagementFromOpportunityTask`). The write cost is negligible at this scale but worth noting.
- The DynamoDB row becomes a coordination surface that must be present and consistent for the integration to work. Loss of the table (operator error during disaster recovery) requires careful reconciliation against the AWS side. The disaster recovery runbook in `docs/operations.md` covers this.

Operational:

- The `scripts/reconcile.py` operator tool walks the four sources of truth (DynamoDB row, HubSpot deal property snapshot, AWS `GetOpportunity` response, AWS `ListOpportunities` collision check) and prints them side by side for diagnosing self-heal mismatch alerts.
- The `update_in_ace` self-heal verify uses the persisted `PartnerOpportunityIdentifier` to refuse writes to AWS opportunities that do not belong to the deal in question, even if the DynamoDB row was tampered with.
