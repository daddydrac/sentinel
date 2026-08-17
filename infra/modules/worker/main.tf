data "aws_partition" "current" {}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.prefix}-worker"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "this" {
  name = "${var.prefix}-worker"
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
  name = "${var.prefix}-worker"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"]
        Resource = var.workflow_table_arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.evidence_bucket_arn}/evidence/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = var.foundation_model_arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeGateway"]
        Resource = var.mcp_gateway_arn
      }
    ]
  })
}

resource "aws_lambda_function" "this" {
  function_name    = "${var.prefix}-worker"
  role             = aws_iam_role.this.arn
  runtime          = "python3.12"
  handler          = "handlers.worker_handler.lambda_handler"
  filename         = var.lambda_zip_path
  source_code_hash = var.lambda_source_code_hash
  memory_size      = var.memory_size
  timeout          = var.timeout_seconds

  environment {
    variables = {
      TABLE_NAME       = var.workflow_table_name
      EVIDENCE_BUCKET  = var.evidence_bucket_name
      BEDROCK_MODEL_ID = var.foundation_model_id
      MCP_GATEWAY_URL  = var.mcp_gateway_url
      AWS_RETRY_MODE   = "adaptive"
      AWS_MAX_ATTEMPTS = "8"
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy_attachment.logs]
}
