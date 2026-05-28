# Minimal IAM role for the read-only HubSpot UI Extension callback Lambda
# (ui_extension_reads).
#
# Routes served by this role:
#   GET /ui-extension/solutions      - partnercentral:ListSolutions
#   GET /ui-extension/aws-products   - static file read from the
#                                      bundled resources/aws_products.json
#
# Why split: previously this Lambda inherited the same role as the
# write-side (UpdateOpportunity, AssociateOpportunity, DynamoDB writes,
# HubSpot private app token). A parser or dependency CVE on this
# internet-reachable endpoint would have escalated to the full
# write-side blast radius. The reads role carries no writes, no DDB, no
# HubSpot private app token, no Update / Associate / Disassociate. The
# only token it touches is the webhook signing secret (for
# X-HubSpot-Signature-v3 validation).

data "aws_iam_policy_document" "ui_extension_reads_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ui_extension_reads" {
  name               = "${var.name_prefix}-ui-ext-reads-role"
  assume_role_policy = data.aws_iam_policy_document.ui_extension_reads_assume.json
  description        = "Read-only HubSpot UI Extension callback Lambda. ListSolutions + signing-secret only; no DDB / no HubSpot PAT / no ACE writes."
}

data "aws_iam_policy_document" "ui_extension_reads" {
  # CloudWatch Logs (own group).
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.name_prefix}-ui-ext-reads:*"]
  }

  # HubSpot signing secret for X-HubSpot-Signature-v3 validation. NOT
  # the HubSpot private app token (reads endpoints never PATCH HubSpot).
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.hubspot_webhook.arn]
  }

  # Partner Central ListSolutions only. Catalog-conditioned so a Sandbox
  # deployment cannot list production-catalog solutions.
  statement {
    actions   = ["partnercentral:ListSolutions"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "partnercentral:Catalog"
      values   = [var.ace_catalog]
    }
  }

  # DynamoDB on the entity-mappings table is needed even for "reads"
  # because the shared _ui_extension_common module's replay-protection
  # path issues a conditional PutItem on the WHK# fingerprint to detect
  # signed-request replays. Without this grant, every request would
  # 500 at the reservation step.
  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [var.entity_mappings_table_arn]
  }

  # KMS for the pipeline CMK. DynamoDB CMK at-rest requires Decrypt /
  # GenerateDataKey on every reserve_webhook_signature write.
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

resource "aws_iam_role_policy" "ui_extension_reads" {
  name   = "${var.name_prefix}-ui-ext-reads-policy"
  role   = aws_iam_role.ui_extension_reads.id
  policy = data.aws_iam_policy_document.ui_extension_reads.json
}
