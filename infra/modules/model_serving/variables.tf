variable "prefix" { type = string }
variable "bucket_name" { type = string }
variable "bucket_arn" { type = string }
variable "kms_key_arn" { type = string }
variable "model_context_path" { type = string }
variable "enable_endpoint" { type = bool }
variable "model_id" { type = string }
variable "model_revision" { type = string }
variable "image_digest" { type = string }
variable "instance_type" { type = string }
variable "initial_instance_count" { type = number }
variable "max_new_tokens" { type = number }
variable "log_retention_days" { type = number }

variable "container_startup_timeout_seconds" {
  type        = number
  description = "Grace period for the container to pull and load the pinned model weights."
  default     = 3600

  validation {
    condition     = var.container_startup_timeout_seconds >= 60 && var.container_startup_timeout_seconds <= 3600
    error_message = "container_startup_timeout_seconds must be between 60 and 3600."
  }
}
