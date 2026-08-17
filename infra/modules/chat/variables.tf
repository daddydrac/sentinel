variable "prefix" { type = string }
variable "lambda_zip_path" { type = string }
variable "lambda_source_code_hash" { type = string }
variable "workflow_table_name" { type = string }
variable "workflow_table_arn" { type = string }
# Converse target. The chat Lambda calls this model directly; the retired
# Bedrock Agents action group is no longer part of this path.
variable "foundation_model_id" { type = string }
variable "foundation_model_arn" { type = string }
variable "guardrail_id" { type = string }
variable "guardrail_version" { type = string }
variable "guardrail_arn" { type = string }
variable "sagemaker_endpoint_name" { type = string }
variable "sagemaker_endpoint_arn" { type = string }
variable "mcp_gateway_url" { type = string }
variable "mcp_gateway_arn" { type = string }
variable "appsync_api_arn" { type = string }
variable "appsync_http_endpoint" { type = string }
variable "evidence_bucket_name" { type = string }
variable "evidence_bucket_arn" { type = string }
variable "kms_key_arn" { type = string }
variable "max_new_tokens" {
  type        = number
  description = "Generation ceiling; must match the SageMaker endpoint contract."
}

variable "memory_size" { type = number }
variable "timeout_seconds" { type = number }
variable "log_retention_days" { type = number }
