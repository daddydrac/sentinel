data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  state_machine_name = "${var.prefix}-graphrag-ingestion"

  # The EMR execution timeout has to be at least the SLO the job is given, plus
  # headroom for executor warmup and the Neptune bulk load that runs after the
  # Spark work completes.
  build_execution_timeout_minutes = ceil(var.build_slo_seconds / 60) + 60
  failure_catch = [{
    ErrorEquals = ["States.ALL"]
    ResultPath  = "$.failure"
    Next        = "UnlockAfterFailure"
  }]
}

resource "aws_cloudwatch_log_group" "gate" {
  name              = "/aws/lambda/${var.prefix}-pipeline-gate"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "state_machine" {
  name              = "/aws/vendedlogs/states/${local.state_machine_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "gate" {
  name = "${var.prefix}-pipeline-gate"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "gate_logs" {
  role       = aws_iam_role.gate.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "gate" {
  name = "${var.prefix}-pipeline-gate"
  role = aws_iam_role.gate.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.bucket_arn
        Condition = {
          StringLike = { "s3:prefix" = ["hpc/raw/source/*", "hpc/staged-100g/*", "hpc/graphrag/runs/*"] }
        }
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${var.bucket_arn}/hpc/raw/source/*",
          "${var.bucket_arn}/hpc/graphrag/runs/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = var.workflow_table_arn
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = var.graphrag_tool_function_arn
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.kms_key_arn
      }
    ]
  })
}

resource "aws_lambda_function" "gate" {
  function_name    = "${var.prefix}-pipeline-gate"
  role             = aws_iam_role.gate.arn
  runtime          = "python3.12"
  handler          = "handlers.pipeline_gate_handler.lambda_handler"
  filename         = var.lambda_zip_path
  source_code_hash = var.lambda_source_code_hash
  memory_size      = 512
  timeout          = 90

  environment {
    variables = {
      EVIDENCE_BUCKET        = var.bucket_name
      DATASET_REVISION       = var.dataset_revision
      TABLE_NAME             = var.workflow_table_name
      GRAPHRAG_TOOL_FUNCTION = var.graphrag_tool_function_name
      MINIMUM_BYTES          = tostring(var.minimum_corpus_gib * 1024 * 1024 * 1024)
    }
  }

  depends_on = [aws_cloudwatch_log_group.gate, aws_iam_role_policy_attachment.gate_logs]
}

resource "aws_iam_role" "state_machine" {
  name = "${var.prefix}-graphrag-ingestion"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "state_machine" {
  name = "${var.prefix}-graphrag-ingestion"
  role = aws_iam_role.state_machine.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.gate.arn
      },
      {
        Effect   = "Allow"
        Action   = ["codebuild:StartBuild"]
        Resource = var.acquisition_project_arn
      },
      {
        Effect   = "Allow"
        Action   = ["codebuild:BatchGetBuilds"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["emr-serverless:StartJobRun", "emr-serverless:GetJobRun", "emr-serverless:CancelJobRun"]
        Resource = [
          "arn:${data.aws_partition.current.partition}:emr-serverless:${var.aws_region}:${data.aws_caller_identity.current.account_id}:/applications/${var.application_id}",
          "arn:${data.aws_partition.current.partition}:emr-serverless:${var.aws_region}:${data.aws_caller_identity.current.account_id}:/applications/${var.application_id}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = var.job_role_arn
        Condition = {
          StringEquals = { "iam:PassedToService" = "emr-serverless.amazonaws.com" }
        }
      },
      {
        # StartJobRun is called by this role, and EMR Serverless validates the
        # *caller* against the key that encrypts the application's managed
        # persistence log storage before the job launches. Granting the job
        # execution role alone is not enough: the call is rejected with
        # "the IAM user does not have permissions to use the KMS Key ... used
        # for log storage" while the job role never gets a chance to run.
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "kms:GenerateDataKeyWithoutPlaintext",
          "kms:DescribeKey",
        ]
        Resource = var.kms_key_arn
      },
      {
        Effect   = "Allow"
        Action   = ["kms:CreateGrant"]
        Resource = var.kms_key_arn
        Condition = {
          Bool = { "kms:GrantIsForAWSResource" = "true" }
        }
      },
      {
        Effect = "Allow"
        # Step Functions creates EventBridge managed rules to track .sync
        # integrations. Rules on a named bus carry the bus in their ARN
        # (rule/<bus>/<name>) while default-bus rules do not, so both shapes are
        # granted; the name prefix keeps this scoped to Step Functions' own
        # managed rules rather than any rule in the account.
        # Step Functions creates EventBridge managed rules to track .sync
        # integrations, and validates this permission at CreateStateMachine
        # time. Scoping by the documented StepFunctionsGetEventsFor* name prefix
        # is rejected -- the validation does not resolve that wildcard -- so the
        # grant is scoped to EventBridge rules in this account and Region only.
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:${data.aws_partition.current.partition}:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_sfn_state_machine" "this" {
  name     = local.state_machine_name
  role_arn = aws_iam_role.state_machine.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.state_machine.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Managed, single-flight 100 GiB HDFS GraphRAG ingestion"
    StartAt = "AcquireLease"
    States = {
      AcquireLease = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.gate.arn
          Payload      = { phase = "LOCK", "run_id.$" = "$.run_id" }
        }
        ResultPath = null
        Catch      = [{ ErrorEquals = ["States.ALL"], Next = "LeaseFailed" }]
        Next       = "AcquireDataset"
      }
      AcquireDataset = {
        Type       = "Task"
        Resource   = "arn:${data.aws_partition.current.partition}:states:::codebuild:startBuild.sync"
        Parameters = { ProjectName = var.acquisition_project_name }
        ResultPath = null
        Catch      = local.failure_catch
        Next       = "ValidateAcquisition"
      }
      ValidateAcquisition = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.gate.arn
          Payload      = { phase = "VALIDATE_ACQUISITION", "run_id.$" = "$.run_id" }
        }
        ResultPath = null
        Catch      = local.failure_catch
        Next       = "Generate100GiB"
      }
      Generate100GiB = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::emr-serverless:startJobRun.sync"
        Parameters = {
          ApplicationId           = var.application_id
          ExecutionRoleArn        = var.job_role_arn
          Name                    = "hdfs-stage-100g"
          ExecutionTimeoutMinutes = 120
          JobDriver = {
            SparkSubmit = {
              EntryPoint              = var.generate_script_uri
              "EntryPointArguments.$" = "States.Array('--source', 's3://${var.bucket_name}/hpc/raw/source/${var.dataset_revision}/data/', '--output', 's3://${var.bucket_name}/hpc/staged-100g/', '--target-gib', '${var.target_corpus_gib}', '--partitions', '${var.spark_partitions}')"
            }
          }
        }
        ResultPath = null
        Catch      = local.failure_catch
        Next       = "ValidateGeneratedCorpus"
      }
      ValidateGeneratedCorpus = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.gate.arn
          Payload      = { phase = "VALIDATE_GENERATED", "run_id.$" = "$.run_id" }
        }
        ResultPath = null
        Catch      = local.failure_catch
        Next       = "BuildGraphRAG"
      }
      BuildGraphRAG = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::emr-serverless:startJobRun.sync"
        Parameters = {
          ApplicationId    = var.application_id
          ExecutionRoleArn = var.job_role_arn
          "Name.$"         = "States.Format('hdfs-graphrag-{}', $.run_id)"
          # Derived, never hardcoded: EMR must not kill the job before the SLO
          # the job itself was handed via --slo-seconds. A fixed 30 minutes
          # against build_slo_seconds=3600 cancelled the run mid-write after it
          # had already produced the OpenSearch documents and most of the
          # Neptune CSVs. The headroom covers cluster warmup and the bulk load.
          ExecutionTimeoutMinutes = local.build_execution_timeout_minutes
          JobDriver = {
            SparkSubmit = {
              EntryPoint              = var.graphrag_script_uri
              "EntryPointArguments.$" = "States.Array('--input', 's3://${var.bucket_name}/hpc/staged-100g/', '--output', 's3://${var.bucket_name}/hpc/graphrag/', '--run-id', $.run_id, '--dataset-revision', '${var.dataset_revision}', '--opensearch-endpoint', '${var.opensearch_endpoint}', '--record-index-alias', '${var.record_index_alias}', '--pattern-index-alias', '${var.pattern_index_alias}', '--neptune-endpoint', '${var.neptune_endpoint}', '--neptune-port', '${var.neptune_port}', '--neptune-loader-role-arn', '${var.neptune_loader_role_arn}', '--embedding-model-id', '${var.embedding_model_id}', '--embedding-dimension', '${var.embedding_dimension}', '--region', '${var.aws_region}', '--partitions', '${var.spark_partitions}', '--pattern-shards', '1', '--record-shards', '${var.record_shards}', '--slo-seconds', '${var.build_slo_seconds}', '--minimum-gib', '${var.minimum_corpus_gib}', '--remote-index-build', '${tostring(var.remote_index_build)}')"
            }
          }
        }
        ResultPath = null
        Catch      = local.failure_catch
        Next       = "ValidatePublishedGraphRAG"
      }
      ValidatePublishedGraphRAG = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.gate.arn
          Payload      = { phase = "VALIDATE_PUBLISHED", "run_id.$" = "$.run_id" }
        }
        ResultSelector = { "result.$" = "$.Payload" }
        ResultPath     = "$.published"
        Catch          = local.failure_catch
        Next           = "ReleaseLease"
      }
      ReleaseLease = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.gate.arn
          Payload      = { phase = "UNLOCK", "run_id.$" = "$.run_id" }
        }
        ResultPath = null
        Next       = "IngestionSucceeded"
      }
      UnlockAfterFailure = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.gate.arn
          Payload      = { phase = "UNLOCK", "run_id.$" = "$.run_id" }
        }
        ResultPath = null
        Next       = "IngestionFailed"
      }
      LeaseFailed        = { Type = "Fail", Error = "SingleFlightLeaseUnavailable" }
      IngestionFailed    = { Type = "Fail", Error = "GraphRAGIngestionFailed" }
      IngestionSucceeded = { Type = "Succeed" }
    }
  })
}
