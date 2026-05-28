variable "name_prefix" {
  type = string
}
variable "enable_notifications" {
  type    = bool
  default = true
}
variable "notification_email" {
  type    = string
  default = ""
}
variable "kms_key_arn" {
  description = "Pipeline CMK ARN from the kms module. Encrypts the SNS notifications topic and the catch-all DLQ messages."
  type        = string
}
variable "update_in_ace_fanout_threshold" {
  description = <<-EOT
    update_in_ace invocations-per-minute that, when sustained over 15
    minutes, fires the fan-out alarm. Default 30/min: comfortably above
    Pandora's BD-load envelope (~3-5/min peak) and well below the AWS
    Partner Central 60/min write quota ceiling. Raise for deployments
    that legitimately run at higher steady-state load; set to 0 to
    disable the alarm. See docs/operations.md "Scaling and webhook
    fan-out" for the upgrade path when this fires.
  EOT
  type        = number
  default     = 30
}
# Lambda + DLQ + Scheduler names are computed from name_prefix to keep the
# monitoring module self-contained. Adding a Lambda elsewhere in the project
# requires bumping the local list below; that's a deliberate trade-off
# against introducing a module dependency cycle (lambda <-> monitoring) when
# the alarms reference resource attributes from the other modules.

locals {
  monitored_lambda_names = [
    "${var.name_prefix}-setup-hubspot",
    "${var.name_prefix}-govwin-orchestrator",
    "${var.name_prefix}-govwin-worker",
    "${var.name_prefix}-hubspot-webhook-receiver",
    "${var.name_prefix}-submit-to-ace",
    "${var.name_prefix}-update-in-ace",
    "${var.name_prefix}-handle-ace-event",
    "${var.name_prefix}-ui-ext-reads",
    "${var.name_prefix}-ui-ext-writes",
  ]

  monitored_dlq_names = [
    "${var.name_prefix}-govwin-sync-dlq",
    "${var.name_prefix}-ace-submit-dlq",
    "${var.name_prefix}-ace-update-dlq",
  ]

  scheduler_name = "${var.name_prefix}-govwin-sync"
}

# Notifications topic. Subscribers should be terminal sinks only (email,
# Slack, PagerDuty). NO Lambda subscriber may write back to HubSpot from
# an SNS-delivered message; doing so would open a feedback loop with
# handle_ace_event -> HubSpot PATCH -> property webhook -> update_in_ace
# -> UpdateOpportunity -> EventBridge -> handle_ace_event. The audit
# alert handler in hubspot_webhook_receiver.py guards one direction of
# that loop; keeping the SNS topic a leaf guards the other.
resource "aws_sns_topic" "sync_notifications" {
  name              = "${var.name_prefix}-notifications"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.enable_notifications && var.notification_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.sync_notifications.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name_prefix}-dlq"
  message_retention_seconds         = 1209600 # 14 days
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
}

# ---------------------------------------------------------------------------
# Alarms
#
# Naming convention: <prefix>-<surface>-<symptom>. State change on any alarm
# fires AlarmActions = [SNS topic above]. The email subscriber gets a single
# notification per state transition (OK -> ALARM or ALARM -> OK).
# ---------------------------------------------------------------------------

# Per-Lambda Errors. ANY non-zero error in 5 minutes pages. The Lambdas
# already implement their own batchItemFailures / SNS-on-terminal-failure
# paths, so a CloudWatch Errors increment means something escaped that net
# (uncaught exception, OOM, init failure).
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = toset(local.monitored_lambda_names)
  alarm_name          = "${each.value}-errors"
  alarm_description   = "Lambda ${each.value} threw at least one uncaught exception in the last 5 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = each.value }
  alarm_actions       = [aws_sns_topic.sync_notifications.arn]
  ok_actions          = [aws_sns_topic.sync_notifications.arn]
}

# Per-Lambda Throttles. With reservedConcurrency tightly tuned, a throttle
# means the worker is bumping its own ceiling (sustained > 2 concurrent for
# the worker, > 5 for submit_to_ace, etc). Catch this before it backs up
# the SQS queues into the DLQ.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each            = toset(local.monitored_lambda_names)
  alarm_name          = "${each.value}-throttles"
  alarm_description   = "Lambda ${each.value} was throttled in the last 5 minutes; raise reservedConcurrency or look at upstream burst."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = each.value }
  alarm_actions       = [aws_sns_topic.sync_notifications.arn]
}

# Catch-all DLQ depth. Any non-zero depth on the project's DLQs means a
# message exhausted SQS retries and is now waiting for human attention.
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = toset(concat(
    [aws_sqs_queue.dlq.name],
    local.monitored_dlq_names,
  ))
  alarm_name          = "${each.value}-depth"
  alarm_description   = "Dead-letter queue ${each.value} contains messages awaiting investigation."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = each.value }
  alarm_actions       = [aws_sns_topic.sync_notifications.arn]
  ok_actions          = [aws_sns_topic.sync_notifications.arn]
}

# EventBridge Scheduler dropped invocations. The Scheduler fires the
# orchestrator hourly; if it fails to invoke (concurrency throttle, IAM
# revocation, target gone), the invocation lands in the configured DLQ.
# This alarm covers the case where the scheduler-side failure is itself
# silent (e.g. role policy misconfiguration prevents the DLQ write).
resource "aws_cloudwatch_metric_alarm" "scheduler_failures" {
  alarm_name          = "${var.name_prefix}-scheduler-target-errors"
  alarm_description   = "EventBridge Scheduler ${local.scheduler_name} failed to invoke its target. The hourly orchestrator tick was missed."
  namespace           = "AWS/Scheduler"
  metric_name         = "TargetErrorCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    ScheduleGroup = "default"
    ScheduleName  = local.scheduler_name
  }
  alarm_actions = [aws_sns_topic.sync_notifications.arn]
}

# Webhook receiver 5xx burst. The receiver responds to API Gateway with 200
# / 401 / 409 / 500. A 5xx storm means something behind the validator is
# broken (Secrets Manager unreachable, DynamoDB throttled, upstream SQS).
resource "aws_cloudwatch_metric_alarm" "webhook_5xx" {
  alarm_name          = "${var.name_prefix}-webhook-5xx-burst"
  alarm_description   = "HubSpot webhook receiver returned multiple 5xx in 5 minutes; check Lambda logs and DLQ."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.sync_notifications.arn]
}

# update_in_ace fan-out signal.
#
# Each HubSpot deal-property webhook fires one update_in_ace invocation
# and one AWS Partner Central UpdateOpportunity call. AWS limits writes
# to 1/sec per partner; sustained high invocation rates on this Lambda
# indicate either a bulk operation (acceptable but worth knowing) or
# fan-out from multiple properties changing on the same deal (which
# could be coalesced; see docs/operations.md "Scaling and webhook
# fan-out" for the upgrade path).
#
# Threshold rationale: Pandora's BD-load envelope (5-50 ops/day) yields
# ~3-5 invocations/min peak burst. The AWS quota ceiling is 60/min
# (1/sec sustained). 30/min averaged over 15 minutes (3 evaluation
# periods of 5 min each) is well above normal Pandora load AND well
# below the AWS hard ceiling, so operators get a "you're approaching
# saturation" signal with time to react rather than a "you've already
# overrun" surprise.
#
# Adjust ``var.update_in_ace_fanout_threshold`` if the deployment runs
# at a steady-state load that legitimately exceeds 30/min. Setting it
# to 0 disables the alarm entirely.
resource "aws_cloudwatch_metric_alarm" "update_in_ace_fanout" {
  count               = var.update_in_ace_fanout_threshold > 0 ? 1 : 0
  alarm_name          = "${var.name_prefix}-update-in-ace-high-rate"
  alarm_description   = <<-EOT
    update_in_ace invocations exceeded the fan-out threshold for 15
    minutes. Common causes: (a) BD ran a bulk recategorize across many
    deals; benign, queue drains in minutes; (b) one deal's webhooks
    are fanning out across many properties per save; coalescing the
    update path would eliminate this. See docs/operations.md
    "Scaling and webhook fan-out" for the diagnosis runbook.
  EOT
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.update_in_ace_fanout_threshold * 5 # threshold is per-minute; period is 5 min
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = "${var.name_prefix}-update-in-ace" }
  alarm_actions       = [aws_sns_topic.sync_notifications.arn]
  ok_actions          = [aws_sns_topic.sync_notifications.arn]
}

output "sns_topic_arn" {
  value = aws_sns_topic.sync_notifications.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}
