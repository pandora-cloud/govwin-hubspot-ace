# Lambda + API Gateway routes for the HubSpot UI Extension callback.
#
# The Submit-to-AWS CRM card calls these three routes via hubspot.fetch().
# Auth is the standard X-HubSpot-Signature-v3 path shared with the legacy
# webhook receiver. The Lambda runs the same source zip / dependency layer
# / shared IAM role used by the other ACE Lambdas.
#
# Routes (all on the existing HTTP API in api_gateway.tf):
#   POST /ui-extension/submit         submit_form_to_ace.handler
#   GET  /ui-extension/solutions      submit_form_to_ace.handler
#   GET  /ui-extension/aws-products   submit_form_to_ace.handler

resource "aws_lambda_function" "submit_form_to_ace" {
  function_name                  = "${var.name_prefix}-submit-form-to-ace"
  role                           = var.lambda_role_arn
  handler                        = "src.lambdas.submit_form_to_ace.handler"
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  timeout                        = 15
  memory_size                    = 256
  reserved_concurrent_executions = 5
  filename                       = var.lambda_source_zip
  source_code_hash               = var.lambda_source_hash
  layers                         = [var.lambda_layer_arn]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = merge(local.ace_env, {
      # Base URL HubSpot signs against; per-request the Lambda appends the
      # actual path observed by API Gateway. Same API id as the webhook
      # receiver because we add the routes to the existing HTTP API.
      UI_EXTENSION_BASE_URL = "https://${aws_apigatewayv2_api.webhook.id}.execute-api.${var.aws_region}.amazonaws.com"
    })
  }
}

resource "aws_cloudwatch_log_group" "submit_form_to_ace" {
  name              = "/aws/lambda/${aws_lambda_function.submit_form_to_ace.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_integration" "submit_form" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.submit_form_to_ace.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}

resource "aws_apigatewayv2_route" "submit_form_submit" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /ui-extension/submit"
  target    = "integrations/${aws_apigatewayv2_integration.submit_form.id}"
}

resource "aws_apigatewayv2_route" "submit_form_solutions" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /ui-extension/solutions"
  target    = "integrations/${aws_apigatewayv2_integration.submit_form.id}"
}

resource "aws_apigatewayv2_route" "submit_form_products" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /ui-extension/aws-products"
  target    = "integrations/${aws_apigatewayv2_integration.submit_form.id}"
}

resource "aws_lambda_permission" "submit_form_api_invoke" {
  statement_id  = "AllowAPIGatewayInvokeSubmitForm"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.submit_form_to_ace.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}
