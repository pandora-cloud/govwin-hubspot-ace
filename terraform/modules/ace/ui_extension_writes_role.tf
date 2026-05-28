# IAM role for the write-side HubSpot UI Extension callback Lambda
# (ui_extension_writes).
#
# Routes served by this role:
#   POST /ui-extension/submit  - HubSpot deal property + dealstage PATCH
#                                (CreateOpportunity itself runs from the
#                                trusted shared role via the webhook
#                                pipeline; this Lambda only flips
#                                dealstage to the ACE trigger).
#   POST /ui-extension/update  - GetOpportunity + UpdateOpportunity +
#                                AssociateOpportunity / DisassociateOpportunity
#                                + HubSpot deal property PATCH.
#
# CreateOpportunity and StartEngagementFromOpportunityTask are
# INTENTIONALLY excluded; those run from submit_to_ace under the shared
# project Lambda role after the webhook pipeline fires.
#
# Catalog gate: every partnercentral statement carries the same
# Catalog condition. A compromise of this Lambda cannot write to the
# production AWS catalog from a Sandbox deployment.

data "aws_iam_policy_document" "ui_extension_writes_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ui_extension_writes" {
  name               = "${var.name_prefix}-ui-ext-writes-role"
  assume_role_policy = data.aws_iam_policy_document.ui_extension_writes_assume.json
  description        = "Write-side HubSpot UI Extension callback Lambda. Update / Associate / Disassociate + HubSpot PATCH; no Create."
}

data "aws_iam_policy_document" "ui_extension_writes" {
  # CloudWatch Logs (own group).
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.name_prefix}-ui-ext-writes:*"]
  }

  # HubSpot signing secret (signature validation) + HubSpot private app
  # token (deal-property PATCHes on /submit and /update). Both read-only.
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.hubspot_webhook.arn,
      var.hubspot_secret_arn,
    ]
  }

  # DynamoDB on the entity-mappings table:
  #   GetItem      - read ACE mapping by govwin_id for dedup / update mapping
  #   PutItem      - signature replay reservation + ClientToken reservation
  #   UpdateItem   - refresh LastModifiedDate after a successful UpdateOpportunity
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [var.entity_mappings_table_arn]
  }

  # Partner Central reads consumed by /update: GetOpportunity for the
  # scrub_for_update base, ListOpportunities for any cross-check.
  statement {
    actions = [
      "partnercentral:GetOpportunity",
      "partnercentral:ListOpportunities",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "partnercentral:Catalog"
      values   = [var.ace_catalog]
    }
  }

  # Partner Central writes for /update: UpdateOpportunity carries BD
  # edits; AssociateOpportunity / DisassociateOpportunity apply the
  # AWS Products diff. CreateOpportunity and
  # StartEngagementFromOpportunityTask are INTENTIONALLY excluded.
  statement {
    actions = [
      "partnercentral:UpdateOpportunity",
      "partnercentral:AssociateOpportunity",
      "partnercentral:DisassociateOpportunity",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "partnercentral:Catalog"
      values   = [var.ace_catalog]
    }
  }

  # KMS for the pipeline CMK encrypting the entity-mappings table and
  # the SQS queues this Lambda may indirectly fan-out into. Without
  # these, ConditionalPutItem on the DDB table fails with
  # KMSAccessDeniedException once the table is CMK-encrypted.
  statement {
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = [var.kms_key_arn]
  }

  # X-Ray. Cannot be resource-scoped (AWS-mandated wildcard).
  statement {
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ui_extension_writes" {
  name   = "${var.name_prefix}-ui-ext-writes-policy"
  role   = aws_iam_role.ui_extension_writes.id
  policy = data.aws_iam_policy_document.ui_extension_writes.json
}
