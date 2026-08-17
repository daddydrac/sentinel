data "aws_partition" "current" {}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.prefix}-chat"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "this" {
  name = "${var.prefix}-chat"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "this" {
  name = "${var.prefix}-chat"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = var.workflow_table_arn
      },
      {
        # Converse replaces the retired Bedrock Agents action group. Scoped to
        # the single foundation model this chat path is allowed to call.
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = var.foundation_model_arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = [var.guardrail_arn, "${var.guardrail_arn}:*"]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeGateway"]
        Resource = var.mcp_gateway_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sagemaker:InvokeEndpoint", "sagemaker:InvokeEndpointWithResponseStream"]
        Resource = var.sagemaker_endpoint_arn
      },
      {
        Effect   = "Allow"
        Action   = ["appsync:EventPublish"]
        Resource = "${var.appsync_api_arn}/channelNamespace/sessions"
      },
      {
        # The Converse transcript is written here between approval rounds and
        # read back when the approved plan executes.
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${var.evidence_bucket_arn}/chat-sessions/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
      }
    ]
  })
}

resource "aws_lambda_function" "this" {
  function_name    = "${var.prefix}-chat"
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  handler          = "handlers.chat_handler.lambda_handler"
  filename         = var.lambda_zip_path
  source_code_hash = var.lambda_source_code_hash
  memory_size      = var.memory_size
  timeout          = var.timeout_seconds

  environment {
    variables = {
      TABLE_NAME              = var.workflow_table_name
      BEDROCK_MODEL_ID        = var.foundation_model_id
      GUARDRAIL_ID            = var.guardrail_id
      GUARDRAIL_VERSION       = var.guardrail_version
      SAGEMAKER_ENDPOINT_NAME = var.sagemaker_endpoint_name
      MCP_GATEWAY_URL         = var.mcp_gateway_url
      APPSYNC_API_ARN         = var.appsync_api_arn
      APPSYNC_HTTP_ENDPOINT   = var.appsync_http_endpoint
      EVIDENCE_BUCKET         = var.evidence_bucket_name
      KMS_KEY_ARN             = var.kms_key_arn
      MAX_NEW_TOKENS          = tostring(var.max_new_tokens)
      AWS_RETRY_MODE          = "adaptive"
      AWS_MAX_ATTEMPTS        = "8"
    }
  }

  reserved_concurrent_executions = 20

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy_attachment.logs]
}

resource "aws_lambda_function_event_invoke_config" "this" {
  function_name                = aws_lambda_function.this.function_name
  maximum_retry_attempts       = 1
  maximum_event_age_in_seconds = 3600
}
