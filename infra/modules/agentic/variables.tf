variable "prefix" { type = string }
variable "lambda_zip_path" { type = string }
variable "lambda_source_code_hash" { type = string }
variable "kms_key_arn" { type = string }
variable "foundation_model_id" { type = string }
variable "foundation_model_arn" { type = string }
variable "log_retention_days" { type = number }
variable "subnet_ids" { type = list(string) }
variable "mcp_security_group_id" { type = string }
variable "opensearch_endpoint" { type = string }
variable "opensearch_domain_arn" { type = string }
variable "opensearch_pattern_index" { type = string }
variable "opensearch_record_index" { type = string }
variable "neptune_endpoint" { type = string }
variable "neptune_port" { type = number }
variable "neptune_database_resource_arn" { type = string }
variable "nova_embedding_model_id" { type = string }
variable "nova_embedding_dimension" { type = number }

# Bedrock Agents entered maintenance mode: CreateAgent is refused for accounts
# without prior service usage. The chat and worker paths now call the model
# directly with Converse, so these resources are off by default. Accounts that
# already have agent access can set this true to keep the legacy path.
variable "enable_bedrock_agents" {
  type        = bool
  description = "Provision the legacy Bedrock Agents action-group path."
  default     = false
}
