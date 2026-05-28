# Cross-cutting customer-managed KMS CMK for the GovWin -> HubSpot -> ACE
# pipeline.
#
# Originally lived inside modules/ace, but ace consumes outputs from
# monitoring and dynamodb (sns_topic_arn, table arns), which made it
# impossible for those modules to also consume the key without a
# dependency cycle. Hoisted into its own leaf module so all three
# (ace, monitoring, dynamodb) can encrypt at-rest data with one
# auditable key without circular wiring.
#
# Key policy grants:
#   * AWS account root (so IAM policies on individual roles take effect)
#   * sqs.amazonaws.com (queue + DLQ messages)
#   * sns.amazonaws.com (topic messages)
#   * events.amazonaws.com + scheduler.amazonaws.com (EventBridge writes
#     to SQS, Scheduler publishes to SNS)
#   * logs.<region>.amazonaws.com (future log group migration; scoped by
#     EncryptionContext to our own /aws/lambda/<prefix>-* groups)
#   * dynamodb.amazonaws.com (DDB at-rest encryption; scoped by ViaService
#     to our region so cross-region DDB calls can't reuse the key)
#
# Mutating use of the key requires the principal to be in the same
# account (root statement) AND tagged Application=<name_prefix> for
# administrative actions issued from the deployer role; see
# terraform/bootstrap/deployer_role.tf for the deployer-side scoping.

variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "pipeline" {
  description             = "${var.name_prefix} HubSpot->ACE pipeline at-rest encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.pipeline_kms.json
  tags = {
    Application = var.name_prefix
    Purpose     = "pipeline-encryption"
  }
}

resource "aws_kms_alias" "pipeline" {
  name          = "alias/${var.name_prefix}-pipeline"
  target_key_id = aws_kms_key.pipeline.key_id
}

data "aws_iam_policy_document" "pipeline_kms" {
  statement {
    sid     = "EnableIAMUserPermissions"
    actions = ["kms:*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    resources = ["*"]
  }

  statement {
    sid = "AllowSQSService"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    principals {
      type        = "Service"
      identifiers = ["sqs.amazonaws.com"]
    }
    resources = ["*"]
  }

  statement {
    sid = "AllowSNSService"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }
    resources = ["*"]
  }

  statement {
    sid = "AllowEventBridgeAndSchedulerServices"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    principals {
      type = "Service"
      identifiers = [
        "events.amazonaws.com",
        "scheduler.amazonaws.com",
      ]
    }
    resources = ["*"]
  }

  # CloudWatch Logs prep grant. Scoped via EncryptionContext to project
  # log groups; the principal becomes useful as soon as the operator
  # opts a log group into this CMK.
  statement {
    sid = "AllowCloudWatchLogsService"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
      "kms:Describe*",
    ]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.name_prefix}-*"]
    }
  }

  # DynamoDB at-rest encryption. ViaService restricts the grant to DDB
  # calls in our own region; cross-region principals can't reuse this
  # key for foreign DDB access even with kms:Decrypt elsewhere.
  statement {
    sid = "AllowDynamoDBService"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
      "kms:Describe*",
    ]
    principals {
      type        = "Service"
      identifiers = ["dynamodb.amazonaws.com"]
    }
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${var.aws_region}.amazonaws.com"]
    }
  }
}

output "pipeline_key_arn" {
  value = aws_kms_key.pipeline.arn
}

output "pipeline_key_id" {
  value = aws_kms_key.pipeline.key_id
}

output "pipeline_alias" {
  value = aws_kms_alias.pipeline.name
}
