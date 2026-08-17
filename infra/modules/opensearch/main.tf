data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  # OpenSearch caps domain names at 28 characters. Truncating "<prefix>-hdfs" can
  # land exactly on the hyphen, leaving a trailing "-"; trim it so the name stays
  # well-formed for any prefix length.
  domain_name = trimsuffix(substr("${var.prefix}-hdfs", 0, 28), "-")
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/aws/opensearch/${local.domain_name}/application"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_resource_policy" "this" {
  policy_name = "${local.domain_name}-logs"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "es.amazonaws.com" }
      Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource  = "${aws_cloudwatch_log_group.application.arn}:*"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_security_group" "this" {
  name_prefix = "${var.prefix}-opensearch-"
  description = "OpenSearch access from GraphRAG ingestion and MCP clients"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_opensearch_domain" "this" {
  domain_name    = local.domain_name
  engine_version = var.engine_version

  cluster_config {
    instance_type            = var.instance_type
    instance_count           = var.instance_count
    dedicated_master_enabled = var.dedicated_master_count > 0
    dedicated_master_type    = var.dedicated_master_count > 0 ? var.dedicated_master_type : null
    dedicated_master_count   = var.dedicated_master_count > 0 ? var.dedicated_master_count : null
    zone_awareness_enabled   = length(var.subnet_ids) > 1

    dynamic "zone_awareness_config" {
      for_each = length(var.subnet_ids) > 1 ? [1] : []
      content {
        availability_zone_count = length(var.subnet_ids)
      }
    }
  }

  aiml_options {
    serverless_vector_acceleration {
      enabled = var.enable_gpu_acceleration
    }
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.volume_size
    iops        = var.volume_iops
    throughput  = var.volume_throughput
  }

  vpc_options {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.this.id]
  }

  encrypt_at_rest {
    enabled    = true
    kms_key_id = var.kms_key_arn
  }

  node_to_node_encryption { enabled = true }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_security_options {
    # IAM identity policies plus the private VPC boundary authorize EMR and MCP.
    # Fine-grained access control would also require a role-mapping bootstrap,
    # creating a circular dependency during first deployment.
    enabled                        = false
    internal_user_database_enabled = false
    anonymous_auth_enabled         = false
  }

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "es:ESHttp*"
      Resource  = "arn:${data.aws_partition.current.partition}:es:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:domain/${local.domain_name}/*"
    }]
  })

  advanced_options = {
    "rest.action.multi.allow_explicit_index" = "false"
    "indices.fielddata.cache.size"           = "20"
  }

  log_publishing_options {
    cloudwatch_log_group_arn = aws_cloudwatch_log_group.application.arn
    log_type                 = "ES_APPLICATION_LOGS"
    enabled                  = true
  }

  depends_on = [aws_cloudwatch_log_resource_policy.this]
}
