# EventBridge rules for AWS Partner Central events.
# All rules target the same handler Lambda; the handler dispatches by
# detail-type and idempotently dedups by event id.
#
# Defense-in-depth on the rule patterns: the default bus already rejects
# PutEvents from foreign callers specifying source = "aws.*", but adding
# `account` to the pattern means even a misconfigured custom bus or a
# future cross-account event-bus policy cannot land non-local events on
# our handler.
#
# AWS publishes ten detail-types from `aws.partnercentral-selling`. We
# subscribe to eight of them (see docs/reference/aws-partner-central/
# eventbridge-events.md for the full matrix). Two are deliberately
# filtered out at this layer:
#
#   - `Engagement Member Added` (informational; AWS publishes when a
#     new member joins an engagement, no action required on our side)
#   - `Engagement Updated` (informational; engagement metadata changed,
#     no action required on our side)
#
# Filtering them at the EventBridge rule pattern means they never
# invoke our Lambda at all, so we do not pay invocation cost to immediately
# skip them in code. If the action policy changes for either type, add the
# detail-type to the matching rule below; the handler-side dispatch will
# already route it through the relevant branch.

data "aws_caller_identity" "current" {}

resource "aws_cloudwatch_event_rule" "opportunity_changes" {
  name        = "${var.name_prefix}-ace-opportunity-changes"
  description = "Opportunity Created/Updated events for our catalog"
  event_pattern = jsonencode({
    source        = ["aws.partnercentral-selling"]
    account       = [data.aws_caller_identity.current.account_id]
    "detail-type" = ["Opportunity Created", "Opportunity Updated"]
    detail = {
      catalog = [var.ace_catalog]
    }
  })
}

resource "aws_cloudwatch_event_rule" "invitation_outcomes" {
  name        = "${var.name_prefix}-ace-invitation-outcomes"
  description = "Engagement invitation lifecycle events"
  event_pattern = jsonencode({
    source  = ["aws.partnercentral-selling"]
    account = [data.aws_caller_identity.current.account_id]
    "detail-type" = [
      "Engagement Invitation Created",
      "Engagement Invitation Accepted",
      "Engagement Invitation Rejected",
      "Engagement Invitation Expired",
    ]
    detail = {
      catalog = [var.ace_catalog]
    }
  })
}

resource "aws_cloudwatch_event_rule" "engagement_lifecycle" {
  name        = "${var.name_prefix}-ace-engagement-lifecycle"
  description = "Engagement and resource-snapshot lifecycle events"
  event_pattern = jsonencode({
    source  = ["aws.partnercentral-selling"]
    account = [data.aws_caller_identity.current.account_id]
    "detail-type" = [
      "Engagement Created",
      "Engagement Resource Snapshot Created",
    ]
    detail = {
      catalog = [var.ace_catalog]
    }
  })
}

resource "aws_cloudwatch_event_target" "opportunity_changes" {
  rule      = aws_cloudwatch_event_rule.opportunity_changes.name
  target_id = "handle_ace_event"
  arn       = aws_lambda_function.handle_ace_event.arn
}

resource "aws_cloudwatch_event_target" "invitation_outcomes" {
  rule      = aws_cloudwatch_event_rule.invitation_outcomes.name
  target_id = "handle_ace_event"
  arn       = aws_lambda_function.handle_ace_event.arn
}

resource "aws_cloudwatch_event_target" "engagement_lifecycle" {
  rule      = aws_cloudwatch_event_rule.engagement_lifecycle.name
  target_id = "handle_ace_event"
  arn       = aws_lambda_function.handle_ace_event.arn
}

resource "aws_lambda_permission" "eventbridge_opportunity" {
  statement_id  = "AllowEventBridgeOpportunity"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.handle_ace_event.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.opportunity_changes.arn
}

resource "aws_lambda_permission" "eventbridge_invitation" {
  statement_id  = "AllowEventBridgeInvitation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.handle_ace_event.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.invitation_outcomes.arn
}

resource "aws_lambda_permission" "eventbridge_engagement" {
  statement_id  = "AllowEventBridgeEngagement"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.handle_ace_event.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.engagement_lifecycle.arn
}
