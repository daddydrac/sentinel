aws_region  = "us-east-1"
environment = "dev"

bedrock_model_id  = "amazon.nova-lite-v1:0"
bedrock_model_arn = ""

enable_hpc_analytics  = true
enable_model_endpoint = true

nova_embedding_model_id  = "amazon.nova-2-multimodal-embeddings-v1:0"
nova_embedding_dimension = 1024

# Same pinned public Apache-2.0 model as the benchmark profile.
sagemaker_model_id       = "Qwen/Qwen3-8B"
sagemaker_model_revision = "b968826d9c46dd6066d109eabc6255188de91218"

# ml.g5.2xlarge carries the same A10G 24 GB card as ml.g5.xlarge but doubles system
# RAM to 32 GiB. The container streams ~16 GB of weights from Hugging Face on first
# start, so the headroom costs $0.107/hr and removes a load-time failure mode.
sagemaker_instance_type          = "ml.g5.2xlarge"
sagemaker_initial_instance_count = 1

# Neptune subnet groups require at least two Availability Zones, so this cannot go
# to 1. OpenSearch then turns zone awareness on and requires the data-node count to
# be a multiple of the AZ count — which is why the domain runs 2 nodes, not 1.
availability_zone_count = 2

# r7g.large.search gives 16 GiB RAM for FAISS/HNSW graphs. T-family instances are
# not recommended for k-NN workloads.
opensearch_instance_type           = "r7g.large.search"
opensearch_instance_count          = 2
opensearch_master_instance_count   = 0
opensearch_volume_size             = 20
opensearch_volume_iops             = 3000
opensearch_volume_throughput       = 125
opensearch_enable_gpu_acceleration = false

neptune_instance_class          = "db.serverless"
neptune_instance_count          = 1
neptune_serverless_min_capacity = 1
# The bulk load of ~2.2 GB of edge CSVs pinned the cluster at 8 NCU with CPU at
# 99.9% and 161k write IOPS for a solid hour without finishing. The loader
# already requests parallelism=OVERSUBSCRIBE, so capacity is the actual ceiling.
# Serverless bills only for NCUs in use and idles back down to min_capacity, so
# a higher ceiling costs nothing at rest and finishes the load in fewer NCU-hours.
neptune_serverless_max_capacity = 32

# Development corpus. The fail-closed gates still run — they are simply bound to a
# size this stack can actually ingest. This does NOT reproduce the 100 GiB SLO.
target_corpus_gib  = 1
minimum_corpus_gib = 1
# Also the Neptune loader's own timeout, which 3600 exceeded. The EMR execution
# timeout is derived from this value plus an hour, so the two stay consistent.
build_slo_seconds      = 7200
spark_partitions       = 16
graphrag_record_shards = 2

hpc_maximum_cpu    = "16 vCPU"
hpc_maximum_memory = "64 GB"
hpc_maximum_disk   = "200 GB"

monthly_budget_usd = 200

tags = {
  Owner      = "Demo-Team"
  CostCenter = "Agentic-Remediation-Dev"
  Lifecycle  = "Ephemeral"
}

# Development corpus needs no warm pool. Totals must stay inside hpc_maximum_*
# above, which a plan-time precondition now enforces.
hpc_initial_capacity = {
  driver_workers   = 1
  driver_cpu       = "4 vCPU"
  driver_memory    = "16 GB"
  driver_disk      = "40 GB"
  executor_workers = 1
  executor_cpu     = "4 vCPU"
  executor_memory  = "16 GB"
  executor_disk    = "40 GB"
}

# The module defaults size Spark for the full 100 GiB run (8 minimum executors
# at 8 vCPU each). That floor is 68 vCPU, which cannot fit in the 16 vCPU dev
# ceiling above, so dynamic allocation never places an executor and the job dies
# with "no executor being launched within 1200000ms". Scale the floor to the
# dev ceiling; a plan-time precondition now enforces the relationship.
hpc_spark_execution = {
  driver_cores        = 4
  driver_memory_gb    = 12
  executor_cores      = 4
  executor_memory_gb  = 12
  initial_executors   = 2
  min_executors       = 1
  max_executors       = 3
  shuffle_partitions  = 32
  max_partition_bytes = 268435456
}
