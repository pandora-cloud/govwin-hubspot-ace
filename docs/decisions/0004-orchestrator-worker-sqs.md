# 4. Orchestrator and worker via SQS, not a Step Functions Map state

Date: 2026-06-04

## Status

Accepted (supersedes the prior Step Functions chain used in early versions)

## Context

The GovWin to HubSpot sync runs on a schedule. On each tick the system needs to:

1. Refresh the GovWin OAuth token.
2. Discover changed opportunities (marked-for-sync list, or saved search, or bookmark, depending on configuration).
3. Fetch full details for each opportunity batch.
4. Map and upsert each batch into HubSpot, including company and contact associations.
5. Advance the sync cursor and write per-opportunity update timestamps.

Two architectural patterns are commonly used for this kind of fan-out workload on AWS:

1. **Step Functions with a `Map` state**: an Express or Standard workflow with one orchestration step that fans out batches into parallel iterations.
2. **Orchestrator Lambda fanning out to SQS, plus a worker Lambda draining the queue**: SQS messages carry one batch each, worker concurrency is governed by Lambda reserved concurrency.

An earlier version of this project used the Step Functions pattern. We hit two specific limits.

First, the Step Functions `Map` state passes the entire input payload between states, subject to a 256 KB inter-state payload limit. With batch sizes of even tens of opportunities, plus rich nested company and contact data, payloads ballooned and forced ugly compaction tricks.

Second, the `Map` state's retry semantics apply at the iteration level: a single iteration failure could mark the whole `Map` execution failed even when the failing batch was independent of the rest. Batch-level retry isolation required wrapping each iteration in additional error catches, which obscured the real failure mode.

## Decision

The project uses the orchestrator-plus-SQS-plus-worker pattern.

- `govwin_orchestrator` Lambda runs on the EventBridge Scheduler tick. It refreshes the OAuth token, runs discovery, and writes one SQS message per opportunity batch into the sync queue.
- `govwin_worker` Lambda is wired to the sync queue as an event source. Each invocation drains one or more SQS messages, fetches details, maps fields, and upserts into HubSpot.
- Worker concurrency is governed by Lambda `reservedConcurrentExecutions` (default 2), sized for the GovWin 4,000 calls per hour quota.
- Partial-batch failures are reported via `ReportBatchItemFailures` so a single stuck batch does not block the rest of the queue.

The Step Functions state machine and the `Map` iteration logic were retired.

## Consequences

Positive:

- No 256 KB inter-state payload limit. Each SQS message carries one batch; payload size is bounded by the batch and not by the cumulative workflow state.
- Independent per-batch retries. A failing batch returns to the queue; the rest of the queue continues to drain.
- Concurrency control via Lambda reserved concurrency is well-understood, observable in CloudWatch, and lets us right-size against the upstream GovWin quota directly.
- Cost is lower: Standard Step Functions charge per state transition; SQS plus Lambda charges per message and per invocation, which is cheaper for this volume.

Negative:

- The workflow no longer has a single visual representation. Operators have to look at two Lambdas plus a queue rather than one Step Functions execution graph. The `docs/architecture.md` diagrams compensate.
- Concurrency tuning is now in `reservedConcurrentExecutions` rather than `Map.maxConcurrency`. Slightly less obvious from the code; documented in `docs/operations.md`.

Operational:

- The sync DLQ depth alarm in `monitoring` catches stuck batches. The DLQ replay procedure is in `docs/operations.md`.
- A future event source change (for example moving to EventBridge Pipes, or to Lambda direct integration with another queue type) would touch the worker's event-source wiring without requiring a re-architecture of the orchestrator.
