# Dedicated minimal IAM role for the public-facing HubSpot UI Extension
# callback Lambda (submit_form_to_ace).
#
# Why split: this Lambda is internet-reachable via API Gateway HTTP API
# (POST /ui-extension/submit, POST /ui-extension/update, GET
# /ui-extension/solutions, GET /ui-extension/aws-products). Before this
# role existed, the Lambda inherited the shared project role, which carries
# CreateOpportunity, full Secrets Manager access (including the GovWin
# refresh token), and both DynamoDB tables. A parsing or dependency
# vulnerability in this public Lambda would have yielded a pipeline-wide
# compromise. The webhook receiver Lambda was hardened with a dedicated
# minimal role; this is the parallel for the UI Extension surface.
#
# Granted to this role (verified against src/lambdas/submit_form_to_ace.py
# 2026-05-27):
#
#   1. secretsmanager:GetSecretValue on the HubSpot signing secret (for
#      X-HubSpot-Signature-v3 verification) AND the HubSpot private-app
#      token (for the HubSpotClient that PATCHes deal properties at
#      submit/update time).
#   2. dynamodb:GetItem + ConditionalPutItem + UpdateItem on the entity-
#      mappings table for ACE mapping lookup and ClientToken / signature
#      replay reservations.
#   3. partnercentral-selling read APIs (Get, List) and the non-Create
#      mutators needed by the /update path (UpdateOpportunity,
#      AssociateOpportunity, DisassociateOpportunity, ListSolutions).
#      CreateOpportunity and StartEngagementFromOpportunityTask are
#      INTENTIONALLY excluded -- they are only invoked from the worker
#      Lambdas (submit_to_ace) which run on the trusted shared role.
#      The /submit path here PATCHes HubSpot dealstage to trigger the
#      existing webhook -> SQS -> submit_to_ace pipeline; we never call
#      CreateOpportunity directly from this Lambda.
#   4. CloudWatch Logs (own log group) and X-Ray.
#
# Catalog gate: all partnercentral statements include the same Catalog
# condition (var.ace_catalog) used by the shared role's iam.tf. A
# compromise of this Lambda cannot write to the production AWS catalog
# from a Sandbox deployment.

data "aws_iam_policy_document" "submit_form_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "submit_form" {
  name               = "${var.name_prefix}-submit-form-role"
  assume_role_policy = data.aws_iam_policy_document.submit_form_assume.json
  description        = "Internet-reachable HubSpot UI Extension callback. Minimal IAM by design; see submit_form_role.tf."
}

data "aws_iam_policy_document" "submit_form" {
  # CloudWatch Logs.
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.name_prefix}-submit-form-to-ace:*"]
  }

  # HubSpot signing secret (for X-HubSpot-Signature-v3 verification) +
  # HubSpot private-app token (for the deal-property PATCHes the Lambda
  # makes on submit and update). Both are read-only.
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.hubspot_webhook.arn,
      var.hubspot_secret_arn,
    ]
  }

  # DynamoDB on the entity-mappings table:
  #   - GetItem: read ACE mapping by govwin_id for dedup / update verification
  #   - PutItem: signature replay reservation (WHK# fingerprint) and
  #     ClientToken reservation via conditional writes
  #   - UpdateItem: refresh LastModifiedDate after a successful UpdateOpportunity
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [var.entity_mappings_table_arn]
  }

  # Partner Central reads. ListSolutions feeds the SolutionPicker dropdown;
  # GetOpportunity feeds the update form's diff against current AWS state.
  statement {
    actions = [
      "partnercentral:GetOpportunity",
      "partnercentral:ListOpportunities",
      "partnercentral:ListSolutions",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "partnercentral:Catalog"
      values   = [var.ace_catalog]
    }
  }

  # Partner Central writes used by the /update path: UpdateOpportunity
  # carries BD edits, AssociateOpportunity / DisassociateOpportunity
  # apply the AWS Products diff. CreateOpportunity and
  # StartEngagementFromOpportunityTask are intentionally NOT granted
  # here; those run from submit_to_ace under the shared role.
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

  # KMS for the customer-managed CMK encrypting the pipeline SQS queues
  # and DynamoDB-at-rest entries this Lambda writes/reads. Without these,
  # ConditionalPutItem on the entity-mappings table fails with
  # KMSAccessDeniedException once the table is configured for CMK-at-rest
  # (deferred). Granted now so the role doesn't need a follow-up policy
  # change when DynamoDB CMK is wired.
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

resource "aws_iam_role_policy" "submit_form" {
  name   = "${var.name_prefix}-submit-form-policy"
  role   = aws_iam_role.submit_form.id
  policy = data.aws_iam_policy_document.submit_form.json
}
