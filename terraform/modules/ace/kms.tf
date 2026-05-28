# Customer-managed KMS CMK for the HubSpot -> ACE pipeline.
#
# The default sqs_managed_sse uses an AWS-owned key invisible to our
# IAM / CloudTrail surface. For DoD / FedRAMP posture, encrypt at rest
# with a customer-managed CMK so:
#
#   1. Key usage shows up in CloudTrail under our own KMS keyId, not the
#      generic "alias/aws/sqs" we cannot audit individually.
#   2. Key rotation, deletion, and policy changes are explicit.
#   3. We can grant kms:Decrypt to specific Lambda execution roles only,
#      so a compromised non-pipeline IAM principal can't read queue
#      contents even if it inherits sqs:ReceiveMessage from a wildcard.
#
# Used by: submit + submit_dlq + update + update_dlq SQS queues.
#
# The SNS topic for orphan/rejection alerts lives in the monitoring
# module and currently uses ``alias/aws/sns`` (AWS-managed key). Moving
# it onto this CMK requires either relocating the topic into this module
# or threading the key ARN cross-module; deferred to a follow-up since
# SNS payloads are bounded to subject+message strings the operator
# already gets via email.

resource "aws_kms_key" "pipeline" {
  description             = "${var.name_prefix} HubSpot->ACE pipeline at-rest encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.pipeline_kms.json
  tags = {
    Application = var.name_prefix
    Purpose     = "sqs+sns-encryption"
  }
}

resource "aws_kms_alias" "pipeline" {
  name          = "alias/${var.name_prefix}-pipeline"
  target_key_id = aws_kms_key.pipeline.key_id
}

# Account-root statement is required so IAM policies on individual
# principals (Lambda roles) can actually take effect. AWS-managed key
# policies all use this pattern; see
# https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-default.html
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

  # SQS and SNS services need direct grants to encrypt/decrypt messages
  # on behalf of the Lambdas that own the queues/topic. Without this,
  # SQS managed encryption fails with KMSAccessDeniedException.
  statement {
    sid     = "AllowSQSService"
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
    sid     = "AllowSNSService"
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

  # EventBridge writing to SQS / publishing to SNS uses scheduler.amazonaws.com
  # and events.amazonaws.com as the principal.
  statement {
    sid     = "AllowEventBridgeAndSchedulerServices"
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

  # CloudWatch Logs encryption. Not currently in use by this stack -- log
  # groups encrypt at rest with AWS-owned keys by default -- but granting
  # the principal now means a future hardening pass that opts log groups
  # into this CMK doesn't have to come back to the policy. Scoped via
  # EncryptionContext so the grant is only usable for our own log groups,
  # not arbitrary cross-account groups.
  statement {
    sid     = "AllowCloudWatchLogsService"
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

  # DynamoDB at-rest encryption. Same prep-work pattern as the Logs grant
  # above. Today both project tables use AWS-owned keys (the default for
  # server_side_encryption { enabled = true }). The future hardening pass
  # that flips dynamodb/main.tf onto this CMK only needs a key ARN
  # parameter; the key policy is already in place.
  #
  # Scoping by ViaService restricts the grant to DDB requests originating
  # in our own region, so even a cross-region principal that somehow
  # obtained kms:Decrypt cannot use this key for a foreign DDB call.
  statement {
    sid     = "AllowDynamoDBService"
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

# aws_caller_identity.current is already declared in eventbridge.tf.
