# `terraform/modules/govwin_sync`

Provisions the GovWin-to-HubSpot half of the pipeline: the orchestrator Lambda that runs on a schedule, the worker Lambda that drains the sync queue, the queue itself with a DLQ, and the EventBridge Scheduler that triggers the orchestrator.

## What it creates

- `govwin_orchestrator` Lambda: refreshes the GovWin OAuth token, runs the configured discovery mode (marked-for-sync, saved-search, bookmarked, or date-range), batches the changed opportunities, fans them out as SQS messages.
- `govwin_worker` Lambda: SQS event source. Fetches each opportunity's full bundle from GovWin, maps via `src/sync/mapper.py`, batch-upserts to HubSpot. Reports partial-batch failures via `ReportBatchItemFailures`.
- One SQS queue (`${name_prefix}-govwin-sync`) and its DLQ.
- An EventBridge Scheduler rule that invokes the orchestrator on a configurable cadence (default: `rate(1 hour)`).
- IAM execution roles scoped to the relevant secrets, DDB tables, queue ARNs.

## Required inputs

`name_prefix`, `aws_region`, layer ARN (from `modules/lambda`), shared execution role (also from `modules/lambda`), DDB table names/ARNs, secret ARNs/names, `sns_topic_arn`, `sync_schedule`, `worker_concurrency`, the GovWin discovery filter variables.

## Outputs

`orchestrator_function_name`, `worker_function_name`, `sync_queue_url`, `sync_queue_arn` for `modules/monitoring` to attach alarms.

## Depends on

`modules/kms`, `modules/dynamodb`, `modules/secrets`, `modules/lambda`. Applies in parallel with `modules/ace` once those four are ready.
