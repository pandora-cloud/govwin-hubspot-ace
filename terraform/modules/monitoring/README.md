# `terraform/modules/monitoring`

Provisions the cross-cutting observability surface: one SNS topic for all operational notifications, one catch-all DLQ for orphaned messages, and CloudWatch alarms for every Lambda's error count, duration, and throttling.

## What it creates

- SNS topic `${name_prefix}-notifications` (CMK-encrypted) for failure notifications. Requires either an email subscriber (`notification_email`) or `enable_notifications=false`; the module's variable validation rejects an empty topic with alarms still enabled.
- Catch-all DLQ `${name_prefix}-dlq` (CMK-encrypted) for messages that exhaust their queue-specific DLQ retries.
- Per-Lambda CloudWatch alarms: errors, throttles, duration p95. Topics fire to the SNS notifications topic.
- The fan-out detector alarm (`${name_prefix}-update-in-ace-high-rate`) covered in the operations runbook.

## Required inputs

`name_prefix`, `kms_key_arn` (from `modules/kms`), the list of Lambda function names (`monitored_lambda_names`), `enable_notifications`, `notification_email`, `update_in_ace_fanout_threshold` (set to 0 to disable that specific alarm).

## Outputs

`sns_topic_arn`, `dlq_url`, `dlq_arn` (consumed by `modules/ace`, `modules/govwin_sync`, `modules/lambda`).

## Depends on

`modules/kms`. Applies before `modules/lambda`, `modules/ace`, `modules/govwin_sync`.
