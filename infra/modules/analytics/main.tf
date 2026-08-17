data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  # EMR expresses capacity as "16 vCPU" / "64 GB"; parse to numbers so the
  # initial totals can be compared against the maximum before AWS rejects them.
  maximum_cpu_units    = tonumber(split(" ", var.maximum_cpu)[0])
  maximum_memory_units = tonumber(split(" ", var.maximum_memory)[0])

  initial_total_cpu = (
    var.initial_capacity.driver_workers * tonumber(split(" ", var.initial_capacity.driver_cpu)[0])
    + var.initial_capacity.executor_workers * tonumber(split(" ", var.initial_capacity.executor_cpu)[0])
  )
  initial_total_memory = (
    var.initial_capacity.driver_workers * tonumber(split(" ", var.initial_capacity.driver_memory)[0])
    + var.initial_capacity.executor_workers * tonumber(split(" ", var.initial_capacity.executor_memory)[0])
  )

  # Dynamic allocation will not start a job unless the driver plus minExecutors
  # can be placed at once, so this floor - not the initial capacity - is what
  # decides whether any executor ever launches.
  spark_floor_cpu = (
    var.spark_execution.driver_cores
    + var.spark_execution.min_executors * var.spark_execution.executor_cores
  )
  spark_floor_memory = (
    var.spark_execution.driver_memory_gb
    + var.spark_execution.min_executors * var.spark_execution.executor_memory_gb
  )
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/emr-serverless/${var.prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_s3_object" "generate" {
  bucket                 = var.bucket_name
  key                    = "hpc/scripts/generate_100g.py"
  source                 = var.generate_script_path
  source_hash            = filemd5(var.generate_script_path)
  server_side_encryption = "aws:kms"
  kms_key_id             = var.kms_key_arn
}

resource "aws_s3_object" "graphrag" {
  bucket                 = var.bucket_name
  key                    = "hpc/scripts/graphrag_build.py"
  source                 = var.graphrag_script_path
  source_hash            = filemd5(var.graphrag_script_path)
  server_side_encryption = "aws:kms"
  kms_key_id             = var.kms_key_arn
}

resource "aws_s3_object" "acquisition" {
  bucket                 = var.bucket_name
  key                    = "hpc/scripts/acquire_hf_dataset.py"
  source                 = var.acquisition_script_path
  source_hash            = filemd5(var.acquisition_script_path)
  server_side_encryption = "aws:kms"
  kms_key_id             = var.kms_key_arn
}

resource "aws_cloudwatch_log_group" "acquisition" {
  name              = "/aws/codebuild/${var.prefix}-dataset-acquisition"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "acquisition" {
  name = "${var.prefix}-dataset-acquisition"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "acquisition" {
  name = "${var.prefix}-dataset-acquisition"
  role = aws_iam_role.acquisition.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.acquisition.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.bucket_arn
        Condition = {
          StringLike = { "s3:prefix" = ["hpc/scripts/*", "hpc/raw/source/*"] }
        }
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${var.bucket_arn}/hpc/scripts/acquire_hf_dataset.py",
          "${var.bucket_arn}/hpc/raw/source/${var.dataset_revision}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arn
      }
    ]
  })
}

resource "aws_codebuild_project" "acquisition" {
  name         = "${var.prefix}-dataset-acquisition"
  service_role = aws_iam_role.acquisition.arn

  artifacts { type = "NO_ARTIFACTS" }
  source {
    type = "NO_SOURCE"
    buildspec = yamlencode({
      version = 0.2
      # CodeBuild defaults to /bin/sh, which is dash on the standard image, where
      # "set -o pipefail" fails with "Illegal option -o pipefail". The shell is
      # selected in the env section; it is not a phase-level key.
      env = {
        shell = "bash"
      }
      phases = {
        build = {
          commands = [
            "set -euo pipefail",
            "aws s3 cp s3://${var.bucket_name}/${aws_s3_object.acquisition.key} /tmp/acquire_hf_dataset.py --region ${data.aws_region.current.region} --only-show-errors",
            "python3 /tmp/acquire_hf_dataset.py --bucket ${var.bucket_name} --prefix hpc/raw/source/${var.dataset_revision} --dataset ${var.dataset_id} --revision ${var.dataset_revision} --expected-parquet-files ${var.dataset_expected_parquet_files} --region ${data.aws_region.current.region}"
          ]
        }
      }
    })
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "KMS_KEY_ARN"
      value = var.kms_key_arn
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.acquisition.name
      stream_name = "acquire"
    }
  }

  build_timeout = 30
}

resource "aws_iam_role" "job" {
  name = "${var.prefix}-emr-job"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "emr-serverless.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike = {
          "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:emr-serverless:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:/applications/*"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "job" {
  name = "${var.prefix}-emr-job"
  role = aws_iam_role.job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Deliberately unconditioned. Spark's write(mode="overwrite") enumerates
        # the bucket to clear an existing output prefix, and that listing does
        # not carry an s3:prefix matching "hpc/*", so a prefix condition here
        # lets the very first run into an empty prefix succeed and then fails
        # every re-run with AccessDenied on s3:ListBucket. Listing the bucket
        # reveals only key names; object access stays scoped to hpc/* below.
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = var.bucket_arn
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${var.bucket_arn}/hpc/*",
          # EMRFS writes a legacy directory marker for each parent directory as
          # "<dir>_$folder$". For the hpc/ tree the top-level marker lands at the
          # bucket root as "hpc_$folder$", which has no slash and so is not
          # matched by hpc/*. Markers for nested directories do start with hpc/
          # and are already covered above.
          "${var.bucket_arn}/hpc_$folder$",
        ]
      },
      {
        # EMR Serverless probes for the configured log group before it starts
        # streaming. DescribeLogGroups is a list operation that does not accept a
        # resource scope, so it has to be granted on "*"; the write actions below
        # stay scoped to this stack's own log group.
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = "${aws_cloudwatch_log_group.this.arn}:*"
      },
      {
        # EMR Serverless managed persistence encrypts logs with this customer
        # managed key and refuses to launch the job without DescribeKey and the
        # envelope-key actions, not just Decrypt/GenerateDataKey.
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey",
          "kms:GenerateDataKeyWithoutPlaintext",
          "kms:DescribeKey",
          "kms:ReEncryptFrom",
          "kms:ReEncryptTo",
        ]
        Resource = var.kms_key_arn
      },
      {
        # Scoped to grants the service creates for itself, per AWS guidance.
        Effect   = "Allow"
        Action   = ["kms:CreateGrant"]
        Resource = var.kms_key_arn
        Condition = {
          Bool = { "kms:GrantIsForAWSResource" = "true" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:${data.aws_partition.current.partition}:bedrock:${data.aws_region.current.region}::foundation-model/${var.nova_embedding_model_id}"
      },
      {
        Effect   = "Allow"
        Action   = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPost", "es:ESHttpPut"]
        Resource = "${var.opensearch_domain_arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["neptune-db:StartLoaderJob", "neptune-db:GetLoaderJobStatus"]
        Resource = var.neptune_database_resource_arn
      },
      {
        Effect   = "Allow"
        Action   = ["neptune-db:ReadDataViaQuery"]
        Resource = var.neptune_database_resource_arn
        Condition = {
          StringEquals = { "neptune-db:QueryLanguage" = "OpenCypher" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = var.neptune_loader_role_arn
        Condition = {
          StringEquals = { "iam:PassedToService" = "rds.amazonaws.com" }
        }
      }
    ]
  })
}

resource "aws_emrserverless_application" "this" {
  name          = "${var.prefix}-hpc"
  release_label = var.release_label
  type          = "spark"
  architecture  = "ARM64"

  network_configuration {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.security_group_id]
  }

  auto_start_configuration {
    enabled = true
  }

  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 5
  }

  maximum_capacity {
    cpu    = var.maximum_cpu
    memory = var.maximum_memory
    disk   = var.maximum_disk
  }

  # Pre-initialized capacity removes Spark cold start for the timed benchmark.
  # It is sized here rather than hardcoded because EMR rejects the application
  # outright when the initial total exceeds maximum_capacity, and a development
  # profile necessarily lowers the maximum.
  initial_capacity {
    initial_capacity_type = "Driver"
    initial_capacity_config {
      worker_count = var.initial_capacity.driver_workers
      worker_configuration {
        cpu    = var.initial_capacity.driver_cpu
        memory = var.initial_capacity.driver_memory
        disk   = var.initial_capacity.driver_disk
      }
    }
  }

  initial_capacity {
    initial_capacity_type = "Executor"
    initial_capacity_config {
      worker_count = var.initial_capacity.executor_workers
      worker_configuration {
        cpu    = var.initial_capacity.executor_cpu
        memory = var.initial_capacity.executor_memory
        disk   = var.initial_capacity.executor_disk
      }
    }
  }

  lifecycle {
    precondition {
      condition     = local.initial_total_cpu <= local.maximum_cpu_units
      error_message = "EMR initial capacity requests ${local.initial_total_cpu} vCPU but maximum_capacity allows ${local.maximum_cpu_units}. Lower initial_capacity or raise hpc_maximum_cpu."
    }
    precondition {
      condition     = local.initial_total_memory <= local.maximum_memory_units
      error_message = "EMR initial capacity requests ${local.initial_total_memory} GB but maximum_capacity allows ${local.maximum_memory_units}. Lower initial_capacity or raise hpc_maximum_memory."
    }
    precondition {
      condition     = local.spark_floor_cpu <= local.maximum_cpu_units
      error_message = "Spark needs ${local.spark_floor_cpu} vCPU for the driver plus ${var.spark_execution.min_executors} minimum executors but maximum_capacity allows ${local.maximum_cpu_units}. The job would start and then fail with 'no executor being launched'. Lower spark_execution.min_executors or executor_cores, or raise hpc_maximum_cpu."
    }
    precondition {
      condition     = local.spark_floor_memory <= local.maximum_memory_units
      error_message = "Spark needs ${local.spark_floor_memory} GB for the driver plus ${var.spark_execution.min_executors} minimum executors but maximum_capacity allows ${local.maximum_memory_units}. Lower spark_execution.min_executors or executor_memory_gb, or raise hpc_maximum_memory."
    }
  }

  scheduler_configuration {
    max_concurrent_runs   = 4
    queue_timeout_minutes = 30
  }

  monitoring_configuration {
    cloudwatch_logging_configuration {
      enabled                = true
      log_group_name         = aws_cloudwatch_log_group.this.name
      log_stream_name_prefix = "spark"

      log_types {
        name   = "SPARK_DRIVER"
        values = ["STDOUT", "STDERR"]
      }

      log_types {
        name   = "SPARK_EXECUTOR"
        values = ["STDERR"]
      }
    }

    managed_persistence_monitoring_configuration {
      enabled            = true
      encryption_key_arn = var.kms_key_arn
    }
  }

  runtime_configuration {
    classification = "spark-defaults"
    properties = {
      "spark.dynamicAllocation.enabled"          = "true"
      "spark.dynamicAllocation.initialExecutors" = tostring(var.spark_execution.initial_executors)
      "spark.dynamicAllocation.minExecutors"     = tostring(var.spark_execution.min_executors)
      "spark.dynamicAllocation.maxExecutors"     = tostring(var.spark_execution.max_executors)
      "spark.executor.cores"                     = tostring(var.spark_execution.executor_cores)
      "spark.executor.memory"                    = "${var.spark_execution.executor_memory_gb}g"
      "spark.driver.cores"                       = tostring(var.spark_execution.driver_cores)
      "spark.driver.memory"                      = "${var.spark_execution.driver_memory_gb}g"
      "spark.sql.adaptive.enabled"               = "true"
      "spark.sql.files.maxPartitionBytes"        = tostring(var.spark_execution.max_partition_bytes)
      "spark.sql.shuffle.partitions"             = tostring(var.spark_execution.shuffle_partitions)
    }
  }
}
