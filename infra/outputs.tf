output "demo_url" {
  description = "Open this URL with ?token=<demo_token>."
  value       = module.api.demo_url
}

output "demo_token" {
  description = "Ephemeral shared token required by every /api request."
  value       = random_password.demo_token.result
  sensitive   = true
}

output "open_demo_command" {
  description = "Authenticated demo URL for any browser."
  value       = "${module.api.demo_url}?token=${random_password.demo_token.result}"
  sensitive   = true
}

output "state_machine_arn" {
  description = "Step Functions state machine ARN."
  value       = module.workflow.state_machine_arn
}

output "workflow_table" {
  description = "DynamoDB workflow-state table."
  value       = module.data.workflow_table_name
}

output "evidence_bucket" {
  description = "Private, versioned evidence bucket."
  value       = module.data.evidence_bucket_name
}

output "api_function_name" {
  value = module.api.function_name
}

output "worker_function_name" {
  value = module.worker.function_name
}

output "bedrock_agent_id" {
  description = "Mandatory Amazon Bedrock Agent ID."
  value       = module.agentic.agent_id
}

output "bedrock_agent_alias_id" {
  value = module.agentic.agent_alias_id
}

output "graphrag_bedrock_agent_id" {
  description = "Dedicated RETURN_CONTROL agent used by the approved GraphRAG chat path."
  value       = module.agentic.graphrag_agent_id
}

output "graphrag_bedrock_agent_alias_id" {
  value = module.agentic.graphrag_agent_alias_id
}

output "bedrock_guardrail_id" {
  description = "Bedrock Guardrail associated with the mandatory agent."
  value       = module.agentic.guardrail_id
}

output "bedrock_guardrail_version" {
  value = module.agentic.guardrail_version
}

output "mcp_gateway_url" {
  description = "IAM-authorized AgentCore Gateway MCP endpoint."
  value       = module.agentic.gateway_url
}

output "mcp_tool_function_name" {
  value = module.agentic.mcp_tool_function_name
}

output "graphrag_tool_function_name" {
  value = module.agentic.graphrag_tool_function_name
}

output "emr_serverless_application_id" {
  description = "Managed Spark application used for the optional cost-incurring 100 GiB qualification run."
  value       = try(module.analytics[0].application_id, null)
}

output "emr_job_role_arn" {
  value = try(module.analytics[0].job_role_arn, null)
}

output "hpc_generate_script_uri" {
  value = try(module.analytics[0].generate_script_uri, null)
}

output "hpc_graphrag_script_uri" {
  value = try(module.analytics[0].graphrag_script_uri, null)
}

output "dataset_acquisition_project" {
  value = try(module.analytics[0].acquisition_project_name, null)
}

output "dataset_source_prefix" {
  value = try(module.analytics[0].source_prefix, null)
}

output "dataset_revision" { value = var.dataset_revision }

output "graphrag_ingestion_state_machine_arn" {
  value = try(module.ingestion[0].state_machine_arn, null)
}

output "opensearch_endpoint" {
  description = "Private OpenSearch endpoint used by the HDFS GraphRAG pipeline."
  value       = module.opensearch.endpoint
}

output "opensearch_pattern_index" { value = local.opensearch_pattern_index }
output "opensearch_record_index" { value = local.opensearch_record_index }

output "neptune_writer_endpoint" {
  value = module.neptune.writer_endpoint
}

output "neptune_port" { value = module.neptune.port }
output "neptune_loader_role_arn" { value = module.neptune.loader_role_arn }

output "model_build_project" { value = module.model_serving.codebuild_project_name }
output "model_repository_name" { value = module.model_serving.repository_name }
output "sagemaker_endpoint_name" { value = module.model_serving.endpoint_name }

output "appsync_events_endpoint" { value = module.realtime.http_endpoint }
output "appsync_realtime_endpoint" { value = module.realtime.realtime_endpoint }
output "amplify_app_id" { value = module.amplify_ui.app_id }
output "amplify_branch_name" { value = module.amplify_ui.branch_name }
output "graphrag_ui_url" { value = module.amplify_ui.url }
output "cognito_user_pool_id" { value = module.identity.user_pool_id }
output "cognito_user_pool_client_id" { value = module.identity.user_pool_client_id }
output "amplify_waf_arn" { value = aws_wafv2_web_acl.amplify.arn }
