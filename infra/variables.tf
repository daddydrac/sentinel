variable "aws_region" {
  description = "AWS Region for the two-day demo. Confirm Bedrock Agent, AgentCore Gateway, and model availability."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment label used in names and tags."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.environment))
    error_message = "environment must contain only lowercase letters, numbers, and hyphens."
  }
}

variable "name_prefix" {
  description = "Short resource-name prefix."
  type        = string
  default     = "hpe-agentic-remediation"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,32}$", var.name_prefix))
    error_message = "name_prefix must be 3-32 lowercase letters, numbers, or hyphens."
  }
}

variable "bedrock_model_id" {
  description = "Foundation model used by the mandatory Amazon Bedrock Agent."
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "bedrock_model_arn" {
  description = "Optional exact model ARN. When blank, Terraform constructs a foundation-model ARN from region and model ID."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the disposable demo."
  type        = number
  default     = 7
}

variable "monthly_budget_usd" {
  description = "Visible AWS Budget limit for this ephemeral account stack; it is not a hard service cutoff."
  type        = number
  default     = 5000

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "force_destroy" {
  description = "Allow Terraform to empty the disposable evidence bucket during destroy. Disable outside demos."
  type        = bool
  default     = true
}

variable "point_in_time_recovery" {
  description = "Enable DynamoDB point-in-time recovery."
  type        = bool
  default     = true
}

variable "api_memory_size" {
  description = "API Lambda memory in MiB."
  type        = number
  default     = 512
}

variable "api_timeout_seconds" {
  description = "API Lambda timeout. Must fit within the API Gateway integration timeout."
  type        = number
  default     = 15
}

variable "worker_memory_size" {
  description = "Workflow worker Lambda memory in MiB."
  type        = number
  default     = 1024
}

variable "worker_timeout_seconds" {
  description = "Workflow worker Lambda timeout."
  type        = number
  default     = 120
}

variable "throttling_burst_limit" {
  description = "HTTP API burst limit. Sized above the 250-user demo's expected synchronized burst."
  type        = number
  default     = 300
}

variable "throttling_rate_limit" {
  description = "HTTP API steady-state requests per second."
  type        = number
  default     = 150
}

variable "enable_hpc_analytics" {
  description = "Provision the managed EMR Serverless Spark qualification application. Jobs are not started by terraform apply."
  type        = bool
  default     = true
}

variable "dataset_id" {
  type        = string
  description = "Public Hugging Face dataset repository acquired by managed CodeBuild."
  default     = "honicky/hdfs-logs-encoded-blocks"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.dataset_id))
    error_message = "dataset_id must use the owner/repository form."
  }
}

variable "dataset_revision" {
  type        = string
  description = "Immutable dataset commit used for acquisition and provenance."
  default     = "977c62c6c9c7ec1122e75cb92368ea7614e5b688"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.dataset_revision))
    error_message = "dataset_revision must be an immutable 40-character commit SHA."
  }
}

variable "dataset_expected_parquet_files" {
  type        = number
  description = "Fail-closed file-count contract for the pinned dataset revision."
  default     = 5

  validation {
    condition     = var.dataset_expected_parquet_files > 0
    error_message = "dataset_expected_parquet_files must be positive."
  }
}

variable "emr_release_label" {
  description = "EMR Serverless release for the 100 GiB Spark qualification path."
  type        = string
  default     = "emr-7.2.0"
}

variable "hpc_maximum_cpu" {
  description = "Maximum aggregate EMR Serverless vCPU capacity."
  type        = string
  default     = "400 vCPU"
}

# Corpus and build sizing. Defaults reproduce the documented 100 GiB benchmark.
# A development stack lowers them so a small corpus passes the same fail-closed gates.
variable "target_corpus_gib" {
  description = "Size of the generated qualification corpus."
  type        = number
  default     = 100

  validation {
    condition     = var.target_corpus_gib > 0
    error_message = "target_corpus_gib must be positive."
  }
}

variable "minimum_corpus_gib" {
  description = "Physical payload the timed build must scan before it may publish."
  type        = number
  default     = 100

  validation {
    condition     = var.minimum_corpus_gib > 0
    error_message = "minimum_corpus_gib must be positive."
  }
}

variable "build_slo_seconds" {
  description = "Timed GraphRAG build SLO. Exceeding it rolls the published aliases back."
  type        = number
  default     = 600

  validation {
    condition     = var.build_slo_seconds >= 60
    error_message = "build_slo_seconds must be at least 60."
  }
}

variable "spark_partitions" {
  description = "Spark shuffle partitions for corpus generation and the timed build."
  type        = number
  default     = 1024
}

variable "graphrag_record_shards" {
  description = "OpenSearch primary shards for the run-scoped record index."
  type        = number
  default     = 24
}

variable "hpc_maximum_memory" {
  description = "Maximum aggregate EMR Serverless memory."
  type        = string
  default     = "1600 GB"
}

variable "hpc_maximum_disk" {
  description = "Maximum aggregate EMR Serverless ephemeral disk."
  type        = string
  default     = "4000 GB"
}

variable "vpc_cidr" {
  description = "Isolated address space for OpenSearch, Neptune, EMR Serverless, and MCP retrieval."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be valid IPv4 CIDR notation."
  }
}

variable "availability_zone_count" {
  description = "Number of Availability Zones used by the GraphRAG data plane."
  type        = number
  default     = 3

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 3
    error_message = "availability_zone_count must be 2 or 3."
  }
}

variable "opensearch_engine_version" {
  type        = string
  description = "OpenSearch version; 3.1 or newer is required for managed GPU vector acceleration."
  default     = "OpenSearch_3.1"
}

variable "opensearch_instance_type" {
  type        = string
  description = "Data-node instance type sized for the formal 100 GiB benchmark."
  default     = "r7g.4xlarge.search"
}

variable "opensearch_instance_count" {
  type        = number
  description = "OpenSearch data-node count."
  default     = 6
}

variable "opensearch_master_instance_type" {
  description = "Dedicated master instance type. Only used when opensearch_master_instance_count > 0."
  type        = string
  default     = "c7g.large.search"
}

variable "opensearch_master_instance_count" {
  description = "Dedicated master nodes. Set 0 to disable them on a small development domain."
  type        = number
  default     = 3
}

variable "opensearch_volume_size" {
  type        = number
  description = "gp3 GiB per OpenSearch data node."
  default     = 2048
}

variable "opensearch_volume_iops" {
  description = "gp3 provisioned IOPS per data node. 3000 is the free baseline; above that is billed."
  type        = number
  default     = 16000
}

variable "opensearch_volume_throughput" {
  type        = number
  description = "gp3 MiB/s per OpenSearch data node."
  default     = 1000
}

variable "opensearch_enable_gpu_acceleration" {
  type        = bool
  description = "Enable managed GPU acceleration for FAISS segment builds."
  default     = true
}

variable "neptune_engine_version" {
  type        = string
  description = "Pinned Neptune engine family."
  default     = "1.4.5.1"
}

variable "neptune_instance_class" {
  type        = string
  description = "Use db.serverless for benchmark elasticity or a supported provisioned Neptune class."
  default     = "db.serverless"
}

variable "neptune_instance_count" {
  type        = number
  description = "One writer and optional read replicas."
  default     = 2
}

variable "neptune_serverless_min_capacity" {
  type        = number
  description = "Minimum Neptune capacity units while the benchmark stack is running."
  default     = 16
}

variable "neptune_serverless_max_capacity" {
  type        = number
  description = "Maximum Neptune capacity units available to the bulk loader and demo queries."
  default     = 128
}

variable "nova_embedding_model_id" {
  type        = string
  description = "Amazon Nova embedding model used for both indexing and retrieval."
  default     = "amazon.nova-2-multimodal-embeddings-v1:0"
}

variable "nova_embedding_dimension" {
  type        = number
  description = "Embedding dimension validated before OpenSearch writes."
  default     = 1024
}

variable "enable_model_endpoint" {
  type        = bool
  description = "Deploy the pinned public open-weight model after its ECR image build succeeds."
  default     = true
}

variable "sagemaker_model_id" {
  type        = string
  description = "Public, non-gated Apache-2.0 Hugging Face model repository."
  default     = "Qwen/Qwen3-8B"
}

variable "sagemaker_model_revision" {
  type        = string
  description = "Immutable Hugging Face model commit SHA validated by deploy.sh."
  default     = "b968826d9c46dd6066d109eabc6255188de91218"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.sagemaker_model_revision))
    error_message = "sagemaker_model_revision must be an immutable 40-character commit SHA."
  }
}

variable "sagemaker_image_digest" {
  type        = string
  description = "Immutable sha256 ECR digest resolved by deploy.sh after CodeBuild."
  default     = ""

  validation {
    condition     = var.sagemaker_image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.sagemaker_image_digest))
    error_message = "sagemaker_image_digest must be empty or an immutable sha256 ECR digest."
  }
}

variable "sagemaker_instance_type" {
  type        = string
  description = "GPU instance with sufficient memory for the unmodified Qwen3-8B model."
  default     = "ml.g5.2xlarge"
}

variable "sagemaker_initial_instance_count" {
  description = "Instances behind the streaming inference endpoint."
  type        = number
  default     = 1
}

variable "sagemaker_max_new_tokens" {
  description = "Upper bound the container enforces on a single generation request."
  type        = number
  default     = 768
}

variable "chat_memory_size" {
  description = "Memory for the async GraphRAG chat Lambda, in MiB."
  type        = number
  default     = 2048
}

variable "chat_timeout_seconds" {
  description = "Timeout for the async GraphRAG chat Lambda. Must exceed the model stream duration."
  type        = number
  default     = 900
}

variable "amplify_branch_name" {
  description = "Amplify branch that hosts the built GraphRAG console."
  type        = string
  default     = "main"
}

variable "tags" {
  description = "Additional AWS tags."
  type        = map(string)
  default = {
    Owner     = "Demo-Team"
    Lifecycle = "Ephemeral"
  }
}

check "bedrock_configuration" {
  assert {
    condition     = trimspace(var.bedrock_model_id) != ""
    error_message = "bedrock_model_id is mandatory because the demo always uses Amazon Bedrock Agents."
  }
}

variable "hpc_spark_execution" {
  description = "Spark sizing for the GraphRAG jobs; driver plus min_executors must fit inside hpc_maximum_*."
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

variable "hpc_initial_capacity" {
  description = "Pre-initialized EMR Serverless workers; totals must fit inside hpc_maximum_*."
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
