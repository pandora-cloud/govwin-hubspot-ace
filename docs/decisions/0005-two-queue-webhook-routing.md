# 5. Two-queue webhook routing for HubSpot inbound events

Date: 2026-06-04

## Status

Accepted

## Context

The integration receives HubSpot deal property change events via a webhook to API Gateway. Two distinct downstream behaviors are triggered:

1. **Deal stage transitions** to a "Submit to AWS" stage cause `submit_to_ace` to run the three-call `CreateOpportunity` to `AssociateOpportunity` to `StartEngagementFromOpportunityTask` flow against AWS Partner Central.
2. **Content property changes** (`amount`, `closedate`, `dealname`, `description`, the `govwin_ace_*` family) cause `update_in_ace` to issue `UpdateOpportunity` with optimistic locking.

These two paths have very different throughput, latency, retry, and rate-limiting profiles. `submit_to_ace` is rate-limited against the AWS Selling API's 1 write per second, can run for several seconds end to end, and must reserve `ClientToken` slots atomically in DynamoDB. `update_in_ace` is also rate-limited but typically faster, frequently no-op (the same property gets touched repeatedly by HubSpot fan-out behavior), and rejects with `ConflictException` on stale `LastModifiedDate`.

Three routing topologies were considered:

1. **One queue, one Lambda** that switches on event type and does both paths. Simpler but tightly couples the two retry policies and the rate-limit budgets.
2. **One queue, two Lambdas as separate event sources** on the same queue. SQS would load-balance messages across the two consumers, which is exactly the wrong behavior: every event needs to go to *the right* Lambda, not to a random one.
3. **Two queues, one Lambda each**, with the receiver routing each event to the correct queue based on the changed property.

The second option fails the routing semantics outright. The first option couples two very different downstream profiles into one Lambda.

## Decision

Use two queues, one Lambda each. The webhook receiver (`hubspot_webhook_receiver`) validates the `X-HubSpot-Signature-v3` signature, parses the changed property, and routes:

- `dealstage` events into the submit queue, consumed by `submit_to_ace`.
- `amount`, `closedate`, `dealname`, `description`, and `govwin_ace_*` events into the update queue, consumed by `update_in_ace`.

A small `AUDIT_ONLY_PROPERTIES` set in `_webhook_routing.py` carves out properties (currently `govwin_aws_cosell_id`) that should never trigger an outbound API call. Hand-edits of those properties fire an SNS alert via the audit handler instead of an outbound write.

## Consequences

Positive:

- Each downstream Lambda owns its own queue, its own DLQ, its own concurrency budget, and its own monitoring. Operations are simpler because the two paths cannot interfere with each other's queue depth or retry budget.
- The fan-out detector alarm (`update-in-ace-high-rate`) is meaningful: a high message rate on the update queue specifically indicates a runaway property-change loop on the update path, independent of submission throughput.
- Adding a third downstream behavior (audit, reconciliation, future paths) is straightforward: add a queue, add a Lambda, add a routing rule.

Negative:

- Two queues plus two DLQs to monitor instead of one. The `monitoring` module wires alarms for both.
- The receiver becomes a routing decision point. If a new property type is added, the receiver's routing table must be updated; otherwise the event is silently dropped.

Operational:

- `docs/operations.md` lists both DLQs in the stuck-deal recovery runbook.
- The audit-only path (for `govwin_aws_cosell_id` hand-edits) protects the integration's invariant that only the integration token writes that property. Any other source firing an SNS alert surfaces tampering or a mis-installed second integration on the same HubSpot portal.
