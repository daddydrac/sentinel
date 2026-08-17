data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  use_serverless = var.instance_class == "db.serverless"
}

resource "aws_security_group" "this" {
  name_prefix = "${var.prefix}-neptune-"
  description = "Neptune access from GraphRAG ingestion and MCP clients"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8182
    to_port         = 8182
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

resource "aws_neptune_subnet_group" "this" {
  name       = "${var.prefix}-graph"
  subnet_ids = var.subnet_ids
}

resource "aws_iam_role" "loader" {
  name = "${var.prefix}-neptune-loader"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      # Neptune assumes loader roles through the RDS service principal.
      # "neptune.amazonaws.com" is not a recognised principal and IAM rejects the
      # whole policy with MalformedPolicyDocument.
      Principal = {
        Service = "rds.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "loader" {
  name = "${var.prefix}-neptune-loader"
  role = aws_iam_role.loader.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [var.bucket_arn, "${var.bucket_arn}/hpc/graphrag/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = var.kms_key_arn
      }
    ]
  })
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                  = "${var.prefix}-graph"
  engine                              = "neptune"
  engine_version                      = var.engine_version
  neptune_subnet_group_name           = aws_neptune_subnet_group.this.name
  vpc_security_group_ids              = [aws_security_group.this.id]
  storage_encrypted                   = true
  kms_key_arn                         = var.kms_key_arn
  iam_database_authentication_enabled = true
  iam_roles                           = [aws_iam_role.loader.arn]
  skip_final_snapshot                 = true
  apply_immediately                   = true
  backup_retention_period             = 1
  deletion_protection                 = false

  dynamic "serverless_v2_scaling_configuration" {
    for_each = local.use_serverless ? [1] : []
    content {
      min_capacity = var.serverless_min_capacity
      max_capacity = var.serverless_max_capacity
    }
  }
}

resource "aws_neptune_cluster_instance" "this" {
  count = var.instance_count

  identifier                 = "${var.prefix}-graph-${count.index + 1}"
  cluster_identifier         = aws_neptune_cluster.this.id
  instance_class             = var.instance_class
  engine                     = "neptune"
  engine_version             = aws_neptune_cluster.this.engine_version
  neptune_subnet_group_name  = aws_neptune_subnet_group.this.name
  publicly_accessible        = false
  apply_immediately          = true
  auto_minor_version_upgrade = true
}

locals {
  database_resource_arn = "arn:${data.aws_partition.current.partition}:neptune-db:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:${aws_neptune_cluster.this.cluster_resource_id}/*"
}

