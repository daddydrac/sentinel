data "aws_partition" "current" {}

locals {
  prefix                   = "${var.name_prefix}-${var.environment}"
  opensearch_pattern_index = "hdfs-patterns"
  opensearch_record_index  = "hdfs-records"
  foundation_model_arn = var.bedrock_model_arn != "" ? var.bedrock_model_arn : format(
    "arn:%s:bedrock:%s::foundation-model/%s",
    data.aws_partition.current.partition,
    var.aws_region,
    var.bedrock_model_id,
  )
}

resource "random_password" "demo_token" {
  length  = 32
  special = false
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/app"
  output_path = "${path.module}/lambda.zip"
  excludes    = ["__pycache__", "*.pyc"]
}

module "data" {
  source = "./modules/data"

  prefix                 = local.prefix
  force_destroy          = var.force_destroy
  point_in_time_recovery = var.point_in_time_recovery
}

module "identity" {
  source = "./modules/identity"
  prefix = local.prefix
}

module "network" {
  source = "./modules/network"

  prefix                          = local.prefix
  vpc_cidr                        = var.vpc_cidr
  availability_zone_count         = var.availability_zone_count
  enable_bedrock_runtime_endpoint = var.enable_hpc_analytics
}

module "opensearch" {
  source = "./modules/opensearch"

  prefix                    = local.prefix
  vpc_id                    = module.network.vpc_id
  subnet_ids                = module.network.private_subnet_ids
  client_security_group_ids = [module.network.emr_security_group_id, module.network.mcp_security_group_id]
  kms_key_arn               = module.data.kms_key_arn
  engine_version            = var.opensearch_engine_version
  instance_type             = var.opensearch_instance_type
  instance_count            = var.opensearch_instance_count
  dedicated_master_type     = var.opensearch_master_instance_type
  dedicated_master_count    = var.opensearch_master_instance_count
  volume_size               = var.opensearch_volume_size
  volume_iops               = var.opensearch_volume_iops
  volume_throughput         = var.opensearch_volume_throughput
  enable_gpu_acceleration   = var.opensearch_enable_gpu_acceleration
  log_retention_days        = var.log_retention_days
}

module "neptune" {
  source = "./modules/neptune"

  prefix                    = local.prefix
  vpc_id                    = module.network.vpc_id
  subnet_ids                = module.network.private_subnet_ids
  client_security_group_ids = [module.network.emr_security_group_id, module.network.mcp_security_group_id]
  bucket_arn                = module.data.evidence_bucket_arn
  kms_key_arn               = module.data.kms_key_arn
  engine_version            = var.neptune_engine_version
  instance_class            = var.neptune_instance_class
  instance_count            = var.neptune_instance_count
  serverless_min_capacity   = var.neptune_serverless_min_capacity
  serverless_max_capacity   = var.neptune_serverless_max_capacity
}

module "analytics" {
  count  = var.enable_hpc_analytics ? 1 : 0
  source = "./modules/analytics"

  prefix                         = local.prefix
  bucket_name                    = module.data.evidence_bucket_name
  bucket_arn                     = module.data.evidence_bucket_arn
  kms_key_arn                    = module.data.kms_key_arn
  generate_script_path           = "${path.module}/../hpc/generate_100g.py"
  graphrag_script_path           = "${path.module}/../hpc/graphrag_build.py"
  acquisition_script_path        = "${path.module}/../scripts/acquire_hf_dataset.py"
  dataset_id                     = var.dataset_id
  dataset_revision               = var.dataset_revision
  dataset_expected_parquet_files = var.dataset_expected_parquet_files
  release_label                  = var.emr_release_label
  maximum_cpu                    = var.hpc_maximum_cpu
  maximum_memory                 = var.hpc_maximum_memory
  maximum_disk                   = var.hpc_maximum_disk
  log_retention_days             = var.log_retention_days
  subnet_ids                     = module.network.private_subnet_ids
  security_group_id              = module.network.emr_security_group_id
  opensearch_domain_arn          = module.opensearch.domain_arn
  neptune_database_resource_arn  = module.neptune.database_resource_arn
  neptune_loader_role_arn        = module.neptune.loader_role_arn
  nova_embedding_model_id        = var.nova_embedding_model_id
  initial_capacity               = var.hpc_initial_capacity
  spark_execution                = var.hpc_spark_execution
}

module "agentic" {
  source = "./modules/agentic"

  prefix                        = local.prefix
  lambda_zip_path               = data.archive_file.lambda.output_path
  lambda_source_code_hash       = data.archive_file.lambda.output_base64sha256
  kms_key_arn                   = module.data.kms_key_arn
  foundation_model_id           = var.bedrock_model_id
  foundation_model_arn          = local.foundation_model_arn
  log_retention_days            = var.log_retention_days
  subnet_ids                    = module.network.private_subnet_ids
  mcp_security_group_id         = module.network.mcp_security_group_id
  opensearch_endpoint           = module.opensearch.endpoint
  opensearch_domain_arn         = module.opensearch.domain_arn
  opensearch_pattern_index      = local.opensearch_pattern_index
  opensearch_record_index       = local.opensearch_record_index
  neptune_endpoint              = module.neptune.writer_endpoint
  neptune_port                  = module.neptune.port
  neptune_database_resource_arn = module.neptune.database_resource_arn
  nova_embedding_model_id       = var.nova_embedding_model_id
  nova_embedding_dimension      = var.nova_embedding_dimension
}

module "ingestion" {
  count  = var.enable_hpc_analytics ? 1 : 0
  source = "./modules/ingestion"

  prefix                      = local.prefix
  lambda_zip_path             = data.archive_file.lambda.output_path
  lambda_source_code_hash     = data.archive_file.lambda.output_base64sha256
  log_retention_days          = var.log_retention_days
  bucket_name                 = module.data.evidence_bucket_name
  bucket_arn                  = module.data.evidence_bucket_arn
  workflow_table_name         = module.data.workflow_table_name
  workflow_table_arn          = module.data.workflow_table_arn
  acquisition_project_name    = module.analytics[0].acquisition_project_name
  acquisition_project_arn     = module.analytics[0].acquisition_project_arn
  application_id              = module.analytics[0].application_id
  job_role_arn                = module.analytics[0].job_role_arn
  generate_script_uri         = module.analytics[0].generate_script_uri
  graphrag_script_uri         = module.analytics[0].graphrag_script_uri
  dataset_revision            = var.dataset_revision
  opensearch_endpoint         = module.opensearch.endpoint
  pattern_index_alias         = local.opensearch_pattern_index
  record_index_alias          = local.opensearch_record_index
  neptune_endpoint            = module.neptune.writer_endpoint
  neptune_port                = module.neptune.port
  neptune_loader_role_arn     = module.neptune.loader_role_arn
  embedding_model_id          = var.nova_embedding_model_id
  embedding_dimension         = var.nova_embedding_dimension
  aws_region                  = var.aws_region
  graphrag_tool_function_name = module.agentic.graphrag_tool_function_name
  graphrag_tool_function_arn  = module.agentic.graphrag_tool_function_arn
  kms_key_arn                 = module.data.kms_key_arn

  target_corpus_gib  = var.target_corpus_gib
  minimum_corpus_gib = var.minimum_corpus_gib
  build_slo_seconds  = var.build_slo_seconds
  spark_partitions   = var.spark_partitions
  record_shards      = var.graphrag_record_shards
  # Bound to the domain capability so the index mapping can never request GPU
  # segment builds a smaller domain would reject.
  remote_index_build = var.opensearch_enable_gpu_acceleration
}

module "model_serving" {
  source = "./modules/model_serving"

  prefix                 = local.prefix
  bucket_name            = module.data.evidence_bucket_name
  bucket_arn             = module.data.evidence_bucket_arn
  kms_key_arn            = module.data.kms_key_arn
  model_context_path     = "${path.module}/../model"
  enable_endpoint        = var.enable_model_endpoint
  model_id               = var.sagemaker_model_id
  model_revision         = var.sagemaker_model_revision
  image_digest           = var.sagemaker_image_digest
  instance_type          = var.sagemaker_instance_type
  initial_instance_count = var.sagemaker_initial_instance_count
  max_new_tokens         = var.sagemaker_max_new_tokens
  log_retention_days     = var.log_retention_days
}

module "realtime" {
  source              = "./modules/realtime"
  prefix              = local.prefix
  aws_region          = var.aws_region
  user_pool_id        = module.identity.user_pool_id
  user_pool_client_id = module.identity.user_pool_client_id
}

module "chat" {
  count  = var.enable_model_endpoint ? 1 : 0
  source = "./modules/chat"

  prefix                  = local.prefix
  lambda_zip_path         = data.archive_file.lambda.output_path
  lambda_source_code_hash = data.archive_file.lambda.output_base64sha256
  workflow_table_name     = module.data.workflow_table_name
  workflow_table_arn      = module.data.workflow_table_arn
  foundation_model_id     = var.bedrock_model_id
  foundation_model_arn    = local.foundation_model_arn
  guardrail_id            = module.agentic.guardrail_id
  guardrail_version       = module.agentic.guardrail_version
  guardrail_arn           = module.agentic.guardrail_arn
  sagemaker_endpoint_name = module.model_serving.endpoint_name
  sagemaker_endpoint_arn  = module.model_serving.endpoint_arn
  # Same ceiling the endpoint enforces, so the two cannot drift apart.
  max_new_tokens        = var.sagemaker_max_new_tokens
  mcp_gateway_url       = module.agentic.gateway_url
  mcp_gateway_arn       = module.agentic.gateway_arn
  appsync_api_arn       = module.realtime.api_arn
  appsync_http_endpoint = module.realtime.http_endpoint
  evidence_bucket_name  = module.data.evidence_bucket_name
  evidence_bucket_arn   = module.data.evidence_bucket_arn
  kms_key_arn           = module.data.kms_key_arn
  memory_size           = var.chat_memory_size
  timeout_seconds       = var.chat_timeout_seconds
  log_retention_days    = var.log_retention_days
}

module "worker" {
  source = "./modules/worker"

  prefix                  = local.prefix
  lambda_zip_path         = data.archive_file.lambda.output_path
  lambda_source_code_hash = data.archive_file.lambda.output_base64sha256
  workflow_table_name     = module.data.workflow_table_name
  workflow_table_arn      = module.data.workflow_table_arn
  evidence_bucket_name    = module.data.evidence_bucket_name
  evidence_bucket_arn     = module.data.evidence_bucket_arn
  kms_key_arn             = module.data.kms_key_arn
  foundation_model_id     = var.bedrock_model_id
  foundation_model_arn    = local.foundation_model_arn
  mcp_gateway_url         = module.agentic.gateway_url
  mcp_gateway_arn         = module.agentic.gateway_arn
  memory_size             = var.worker_memory_size
  timeout_seconds         = var.worker_timeout_seconds
  log_retention_days      = var.log_retention_days
}

module "workflow" {
  source = "./modules/workflow"

  prefix              = local.prefix
  worker_function_arn = module.worker.function_arn
  log_retention_days  = var.log_retention_days
}

module "api" {
  source = "./modules/api"

  prefix                  = local.prefix
  lambda_zip_path         = data.archive_file.lambda.output_path
  lambda_source_code_hash = data.archive_file.lambda.output_base64sha256
  workflow_table_name     = module.data.workflow_table_name
  workflow_table_arn      = module.data.workflow_table_arn
  kms_key_arn             = module.data.kms_key_arn
  state_machine_arn       = module.workflow.state_machine_arn
  demo_token              = random_password.demo_token.result
  memory_size             = var.api_memory_size
  timeout_seconds         = var.api_timeout_seconds
  log_retention_days      = var.log_retention_days
  throttling_burst_limit  = var.throttling_burst_limit
  throttling_rate_limit   = var.throttling_rate_limit
  chat_enabled            = var.enable_model_endpoint
  chat_function_name      = one(module.chat[*].function_name)
  chat_function_arn       = one(module.chat[*].function_arn)
  cognito_issuer          = module.identity.issuer
  cognito_client_id       = module.identity.user_pool_client_id
}

module "amplify_ui" {
  source = "./modules/amplify_ui"

  prefix      = local.prefix
  branch_name = var.amplify_branch_name
}

resource "aws_wafv2_web_acl" "amplify" {
  provider = aws.global
  name     = "${local.prefix}-amplify"
  scope    = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 10

    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "PerIpRateLimit"
    priority = 20
    action {
      block {}
    }
    statement {
      rate_based_statement {
        aggregate_key_type = "IP"
        limit              = 1200
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.prefix}-amplify"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "amplify" {
  provider     = aws.global
  resource_arn = module.amplify_ui.arn
  web_acl_arn  = aws_wafv2_web_acl.amplify.arn
}

resource "aws_budgets_budget" "demo" {
  name         = "${local.prefix}-monthly-cap"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
}
