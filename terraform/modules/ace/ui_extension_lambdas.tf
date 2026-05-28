# HubSpot UI Extension callback Lambdas (post-split: 2026-05-28).
#
# Replaces the single submit_form_to_ace Lambda with a reads + writes
# pair so each carries the minimum IAM needed for its routes:
#
#   ui_extension_reads:  GET /ui-extension/solutions
#                        GET /ui-extension/aws-products
#                        Role: ListSolutions + signing-secret only
#
#   ui_extension_writes: POST /ui-extension/submit
#                        POST /ui-extension/update
#                        Role: full update path (DDB, HubSpot PAT,
#                        Update / Associate / Disassociate)
#
# Both Lambdas ship the same source zip and dependency layer; the
# difference is the handler entry point and the attached IAM role.
# Both wire into the existing HTTP API in api_gateway.tf.
#
# /update is synchronous: GetOpportunity + UpdateOpportunity. AWS
# Products diff is offloaded to update_in_ace via the webhook on the
# govwin_ace_aws_products property change (avoids the 1-write/sec loop
# that previously could overrun API Gateway's 29s timeout). Timeouts
# stay sized just under the 30s API GW HTTP cap.

#; Read-only Lambda ---------------------------------------------------

resource "aws_lambda_function" "ui_extension_reads" {
  function_name                  = "${var.name_prefix}-ui-ext-reads"
  role                           = aws_iam_role.ui_extension_reads.arn
  handler                        = "src.lambdas.ui_extension_reads.handler"
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  timeout                        = 10
  memory_size                    = 192
  reserved_concurrent_executions = 5
  filename                       = var.lambda_source_zip
  source_code_hash               = var.lambda_source_hash
  layers                         = [var.lambda_layer_arn]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = merge(local.ace_env, {
      UI_EXTENSION_BASE_URL = "https://${aws_apigatewayv2_api.webhook.id}.execute-api.${var.aws_region}.amazonaws.com"
    })
  }
}

resource "aws_cloudwatch_log_group" "ui_extension_reads" {
  name              = "/aws/lambda/${aws_lambda_function.ui_extension_reads.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_integration" "ui_extension_reads" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ui_extension_reads.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "ui_extension_solutions" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /ui-extension/solutions"
  target    = "integrations/${aws_apigatewayv2_integration.ui_extension_reads.id}"
}

resource "aws_apigatewayv2_route" "ui_extension_aws_products" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "GET /ui-extension/aws-products"
  target    = "integrations/${aws_apigatewayv2_integration.ui_extension_reads.id}"
}

resource "aws_lambda_permission" "ui_extension_reads_api_invoke" {
  statement_id  = "AllowAPIGatewayInvokeUiExtReads"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ui_extension_reads.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

#; Write-side Lambda --------------------------------------------------

resource "aws_lambda_function" "ui_extension_writes" {
  function_name                  = "${var.name_prefix}-ui-ext-writes"
  role                           = aws_iam_role.ui_extension_writes.arn
  handler                        = "src.lambdas.ui_extension_writes.handler"
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  timeout                        = 28
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
      UI_EXTENSION_BASE_URL = "https://${aws_apigatewayv2_api.webhook.id}.execute-api.${var.aws_region}.amazonaws.com"
    })
  }
}

resource "aws_cloudwatch_log_group" "ui_extension_writes" {
  name              = "/aws/lambda/${aws_lambda_function.ui_extension_writes.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_integration" "ui_extension_writes" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ui_extension_writes.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "ui_extension_submit" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /ui-extension/submit"
  target    = "integrations/${aws_apigatewayv2_integration.ui_extension_writes.id}"
}

resource "aws_apigatewayv2_route" "ui_extension_update" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /ui-extension/update"
  target    = "integrations/${aws_apigatewayv2_integration.ui_extension_writes.id}"
}

resource "aws_lambda_permission" "ui_extension_writes_api_invoke" {
  statement_id  = "AllowAPIGatewayInvokeUiExtWrites"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ui_extension_writes.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}
