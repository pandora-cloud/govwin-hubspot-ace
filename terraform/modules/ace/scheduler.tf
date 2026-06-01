# EventBridge Scheduler that runs the reconcile-pending sweep on a cadence.
# This is the backstop for the event-driven reconcile in handle_ace_event:
# AWS Partner Central EventBridge delivery is best-effort, so an approve /
# reject event can be dropped, leaving a deal's deferred edits parked. The
# sweep catches those.

resource "aws_sqs_queue" "reconcile_sweep_dlq" {
  name                              = "${var.name_prefix}-ace-reconcile-sweep-dlq"
  message_retention_seconds         = 14 * 24 * 3600 # 14 days
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
}

data "aws_iam_policy_document" "reconcile_scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "reconcile_scheduler" {
  name               = "${var.name_prefix}-ace-reconcile-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.reconcile_scheduler_assume.json
}

data "aws_iam_policy_document" "reconcile_scheduler_invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.reconcile_pending.arn]
  }
  # Scheduler delivers to the DLQ when an invocation fails after exhausting
  # retries. Without this a missed sweep is silent.
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.reconcile_sweep_dlq.arn]
  }
  # SendMessage to the CMK-encrypted DLQ needs envelope-encryption access.
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "reconcile_scheduler" {
  name   = "${var.name_prefix}-ace-reconcile-scheduler-policy"
  role   = aws_iam_role.reconcile_scheduler.id
  policy = data.aws_iam_policy_document.reconcile_scheduler_invoke.json
}

resource "aws_scheduler_schedule" "reconcile_pending" {
  name        = "${var.name_prefix}-ace-reconcile-pending"
  description = "Replays HubSpot edits deferred while an ACE opportunity was in AWS review"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.reconcile_sweep_schedule
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.reconcile_pending.arn
    role_arn = aws_iam_role.reconcile_scheduler.arn

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

    dead_letter_config {
      arn = aws_sqs_queue.reconcile_sweep_dlq.arn
    }
  }
}
