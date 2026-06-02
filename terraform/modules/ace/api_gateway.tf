# API Gateway HTTP API in front of the webhook receiver Lambda.

resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.name_prefix}-hubspot-webhook"
  protocol_type = "HTTP"
  description   = "HubSpot webhook receiver for the GovWin to AWS Partner Central pipeline"
}

resource "aws_apigatewayv2_integration" "webhook" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.hubspot_webhook_receiver.invoke_arn
  payload_format_version = "2.0"
  # Must match or exceed the receiver Lambda timeout (10s). HubSpot
  # tolerates occasional slow responses well past the documented 5s
  # budget; the cold-start SNS-publish path on a CMK-encrypted topic
  # can take a few seconds, and a 5s API Gateway cap was cutting the
  # connection before the Lambda finished.
  timeout_milliseconds = 10000
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /hubspot"
  target    = "integrations/${aws_apigatewayv2_integration.webhook.id}"
}

# Access log group for the webhook stage. CUI/compliance evaluators expect
# the inbound request log to exist as audit evidence, not just Lambda logs.
resource "aws_cloudwatch_log_group" "webhook_access" {
  name              = "/aws/apigateway/${var.name_prefix}-hubspot-webhook"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "webhook" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.webhook_access.arn
    # No request/response bodies: the body carries the HubSpot signature and
    # deal payload. Log only routing/outcome metadata for the audit trail.
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      integrationErr = "$context.integrationErrorMessage"
      responseLength = "$context.responseLength"
    })
  }

  default_route_settings {
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }
}

resource "aws_lambda_permission" "api_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.hubspot_webhook_receiver.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}
