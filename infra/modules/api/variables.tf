variable "prefix" { type = string }
variable "lambda_zip_path" { type = string }
variable "lambda_source_code_hash" { type = string }
variable "workflow_table_name" { type = string }
variable "workflow_table_arn" { type = string }
variable "kms_key_arn" { type = string }
variable "state_machine_arn" { type = string }
variable "demo_token" {
  type      = string
  sensitive = true
}
variable "memory_size" { type = number }
variable "timeout_seconds" { type = number }
variable "log_retention_days" { type = number }
variable "throttling_burst_limit" { type = number }
variable "throttling_rate_limit" { type = number }
# The root passes one(module.chat[*].x), which yields null when the chat module
# is disabled. nullable = false converts that null to the empty-string default.
variable "chat_function_name" {
  type     = string
  default  = ""
  nullable = false
}

variable "chat_function_arn" {
  type     = string
  default  = ""
  nullable = false
}

# Whether the GraphRAG chat Lambda is deployed. This is a separate input from
# chat_function_arn on purpose: count must depend on a value known at plan time,
# and the ARN is a computed attribute of a resource created in the same apply.
variable "chat_enabled" { type = bool }
variable "cognito_issuer" { type = string }
variable "cognito_client_id" { type = string }
