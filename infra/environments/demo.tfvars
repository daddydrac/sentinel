aws_region  = "us-east-1"
environment = "demo"

bedrock_model_id  = "amazon.nova-lite-v1:0"
bedrock_model_arn = ""

enable_hpc_analytics  = true
enable_model_endpoint = true

nova_embedding_model_id  = "amazon.nova-2-multimodal-embeddings-v1:0"
nova_embedding_dimension = 1024

# Public, non-gated Apache-2.0 model pinned to an immutable Hugging Face revision.
sagemaker_model_id       = "Qwen/Qwen3-8B"
sagemaker_model_revision = "b968826d9c46dd6066d109eabc6255188de91218"

# Benchmark profile. Reduce these values only for a non-SLO development stack.
opensearch_instance_type           = "r7g.4xlarge.search"
opensearch_instance_count          = 6
opensearch_enable_gpu_acceleration = true
neptune_instance_class             = "db.serverless"
neptune_serverless_min_capacity    = 16
neptune_serverless_max_capacity    = 128

tags = {
  Owner      = "Demo-Team"
  CostCenter = "Agentic-Remediation-Demo"
  Lifecycle  = "Ephemeral"
}
