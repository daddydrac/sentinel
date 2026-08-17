data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "archive_file" "context" {
  type        = "zip"
  source_dir  = var.model_context_path
  output_path = "${path.module}/model-context.zip"
}

resource "aws_s3_object" "context" {
  bucket                 = var.bucket_name
  key                    = "model/build/${data.archive_file.context.output_base64sha256}.zip"
  source                 = data.archive_file.context.output_path
  source_hash            = data.archive_file.context.output_base64sha256
  server_side_encryption = "aws:kms"
  kms_key_id             = var.kms_key_arn
}

resource "aws_ecr_repository" "this" {
  name                 = "${var.prefix}-chat"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain only the five newest inference images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "build" {
  name              = "/aws/codebuild/${var.prefix}-chat"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "endpoint" {
  name              = "/aws/sagemaker/Endpoints/${var.prefix}-chat"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "build" {
  name = "${var.prefix}-model-build"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "build" {
  name = "${var.prefix}-model-build"
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.build.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.bucket_arn}/${aws_s3_object.context.key}"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.kms_key_arn
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
          # buildspec.yml verifies the pushed image in post_build; without this
          # the push succeeds and the build is then marked FAILED.
          "ecr:DescribeImages"
        ]
        Resource = aws_ecr_repository.this.arn
      }
    ]
  })
}

resource "aws_codebuild_project" "this" {
  name         = "${var.prefix}-chat-image"
  service_role = aws_iam_role.build.arn

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    compute_type    = "BUILD_GENERAL1_LARGE"
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }
    environment_variable {
      name  = "ECR_REPOSITORY_URI"
      value = aws_ecr_repository.this.repository_url
    }
    environment_variable {
      name  = "ECR_REPOSITORY_NAME"
      value = aws_ecr_repository.this.name
    }
    environment_variable {
      name  = "IMAGE_TAG"
      value = "latest"
    }
  }

  logs_config {
    cloudwatch_logs { group_name = aws_cloudwatch_log_group.build.name }
  }

  source {
    type      = "S3"
    location  = "${var.bucket_name}/${aws_s3_object.context.key}"
    buildspec = "buildspec.yml"
  }
}

resource "aws_iam_role" "sagemaker" {
  name = "${var.prefix}-sagemaker-chat"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sagemaker" {
  name = "${var.prefix}-sagemaker-chat"
  role = aws_iam_role.sagemaker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = aws_ecr_repository.this.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:${data.aws_partition.current.partition}:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/sagemaker/Endpoints/${var.prefix}-chat*"
      }
    ]
  })
}

resource "aws_sagemaker_model" "this" {
  count = var.enable_endpoint ? 1 : 0

  name               = "${var.prefix}-chat"
  execution_role_arn = aws_iam_role.sagemaker.arn

  primary_container {
    image = "${aws_ecr_repository.this.repository_url}@${var.image_digest}"
    environment = {
      MODEL_ID             = var.model_id
      MODEL_REVISION       = var.model_revision
      MAX_NEW_TOKENS       = tostring(var.max_new_tokens)
      HF_HUB_OFFLINE       = "0"
      TRANSFORMERS_OFFLINE = "0"
    }
  }

  lifecycle {
    precondition {
      condition     = can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
      error_message = "The SageMaker endpoint requires the immutable ECR digest produced by deploy.sh."
    }
  }

  depends_on = [aws_codebuild_project.this]
}

resource "aws_sagemaker_endpoint_configuration" "this" {
  count = var.enable_endpoint ? 1 : 0

  name = "${var.prefix}-chat"
  production_variants {
    variant_name           = "primary"
    model_name             = aws_sagemaker_model.this[0].name
    initial_instance_count = var.initial_instance_count
    instance_type          = var.instance_type
    initial_variant_weight = 1

    # Weights are not baked into the image; the container pulls the pinned Qwen
    # revision from Hugging Face on first start. The 600s SageMaker default is not
    # enough to download and load an 8B model onto the GPU.
    container_startup_health_check_timeout_in_seconds = var.container_startup_timeout_seconds
  }
}

resource "aws_sagemaker_endpoint" "this" {
  count = var.enable_endpoint ? 1 : 0

  name                 = "${var.prefix}-chat"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.this[0].name

  depends_on = [aws_cloudwatch_log_group.endpoint]
}
