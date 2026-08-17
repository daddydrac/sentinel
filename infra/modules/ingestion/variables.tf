variable "prefix" { type = string }
variable "lambda_zip_path" { type = string }
variable "lambda_source_code_hash" { type = string }
variable "log_retention_days" { type = number }
variable "bucket_name" { type = string }
variable "bucket_arn" { type = string }
variable "workflow_table_name" { type = string }
variable "workflow_table_arn" { type = string }
variable "acquisition_project_name" { type = string }
variable "acquisition_project_arn" { type = string }
variable "application_id" { type = string }
variable "job_role_arn" { type = string }
variable "generate_script_uri" { type = string }
variable "graphrag_script_uri" { type = string }
variable "dataset_revision" { type = string }
variable "opensearch_endpoint" { type = string }
variable "pattern_index_alias" { type = string }
variable "record_index_alias" { type = string }
variable "neptune_endpoint" { type = string }
variable "neptune_port" { type = number }
variable "neptune_loader_role_arn" { type = string }
variable "embedding_model_id" { type = string }
variable "embedding_dimension" { type = number }
variable "aws_region" { type = string }
variable "graphrag_tool_function_name" { type = string }
variable "graphrag_tool_function_arn" { type = string }
variable "kms_key_arn" { type = string }

# Corpus and build sizing. Defaults reproduce the documented 100 GiB benchmark;
# a development stack lowers them so a small corpus can pass the same gates.
variable "target_corpus_gib" {
  type        = number
  description = "Size of the generated qualification corpus."
  default     = 100
}

variable "minimum_corpus_gib" {
  type        = number
  description = "Physical payload the timed build must scan before it may publish."
  default     = 100
}

variable "build_slo_seconds" {
  type        = number
  description = "Timed build SLO. Exceeding it rolls the published aliases back."
  default     = 600
}

variable "spark_partitions" {
  type        = number
  description = "Spark shuffle partitions for generation and the timed build."
  default     = 1024
}

variable "record_shards" {
  type        = number
  description = "OpenSearch primary shards for the run-scoped record index."
  default     = 24
}

variable "remote_index_build" {
  type        = bool
  description = "GPU-accelerated FAISS segment builds; needs serverless vector acceleration."
  default     = true
}

