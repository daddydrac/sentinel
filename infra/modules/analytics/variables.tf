variable "prefix" { type = string }
variable "bucket_name" { type = string }
variable "bucket_arn" { type = string }
variable "kms_key_arn" { type = string }
variable "generate_script_path" { type = string }
variable "graphrag_script_path" { type = string }
variable "acquisition_script_path" { type = string }
variable "dataset_id" { type = string }
variable "dataset_revision" { type = string }
variable "dataset_expected_parquet_files" { type = number }
variable "release_label" { type = string }
variable "maximum_cpu" { type = string }
variable "maximum_memory" { type = string }
variable "maximum_disk" { type = string }
variable "log_retention_days" { type = number }
variable "subnet_ids" { type = list(string) }
variable "security_group_id" { type = string }
variable "opensearch_domain_arn" { type = string }
variable "neptune_database_resource_arn" { type = string }
variable "neptune_loader_role_arn" { type = string }
variable "nova_embedding_model_id" { type = string }

variable "spark_execution" {
  description = "Spark sizing for the GraphRAG jobs. The floor (driver + min_executors) must fit inside maximum_capacity or no executor can ever be scheduled."
  type = object({
    driver_cores        = number
    driver_memory_gb    = number
    executor_cores      = number
    executor_memory_gb  = number
    initial_executors   = number
    min_executors       = number
    max_executors       = number
    shuffle_partitions  = number
    max_partition_bytes = number
  })
  default = {
    driver_cores        = 4
    driver_memory_gb    = 16
    executor_cores      = 8
    executor_memory_gb  = 28
    initial_executors   = 16
    min_executors       = 8
    max_executors       = 48
    shuffle_partitions  = 768
    max_partition_bytes = 268435456
  }
}

variable "initial_capacity" {
  description = "Pre-initialized EMR Serverless workers. Totals must fit inside maximum_capacity."
  type = object({
    driver_workers   = number
    driver_cpu       = string
    driver_memory    = string
    driver_disk      = string
    executor_workers = number
    executor_cpu     = string
    executor_memory  = string
    executor_disk    = string
  })
  default = {
    driver_workers   = 1
    driver_cpu       = "4 vCPU"
    driver_memory    = "16 GB"
    driver_disk      = "40 GB"
    executor_workers = 24
    executor_cpu     = "8 vCPU"
    executor_memory  = "32 GB"
    executor_disk    = "80 GB"
  }
}
