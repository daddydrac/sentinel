data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

locals {
  selected_azs = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.prefix}-graphrag" }
}

resource "aws_subnet" "private" {
  for_each = { for index, az in local.selected_azs : az => index }

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, each.value)
  map_public_ip_on_launch = false

  tags = { Name = "${var.prefix}-private-${each.value + 1}" }
}

resource "aws_route_table" "private" {
  for_each = aws_subnet.private

  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.prefix}-private-${each.key}" }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

resource "aws_security_group" "emr" {
  name_prefix = "${var.prefix}-emr-"
  description = "EMR Serverless GraphRAG workers"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group_rule" "emr_self" {
  type              = "ingress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  self              = true
  security_group_id = aws_security_group.emr.id
  description       = "Allow Spark driver and executors to communicate"
}

resource "aws_security_group" "mcp" {
  name_prefix = "${var.prefix}-mcp-"
  description = "MCP retrieval Lambda clients"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group" "endpoints" {
  name_prefix = "${var.prefix}-vpce-"
  description = "Private AWS API endpoints used by GraphRAG jobs"
  vpc_id      = aws_vpc.this.id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.emr.id, aws_security_group.mcp.id]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for route_table in aws_route_table.private : route_table.id]
}

resource "aws_vpc_endpoint" "bedrock_runtime" {
  count = var.enable_bedrock_runtime_endpoint ? 1 : 0

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for subnet in aws_subnet.private : subnet.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
}

# The private subnets deliberately have no NAT gateway, so every AWS API these
# workloads call needs its own interface endpoint. The S3 gateway endpoint above
# only covers S3 itself: reading or writing SSE-KMS objects still calls the KMS
# API, and EMR Serverless pushes Spark driver and executor logs to CloudWatch.
# Without these two, S3 access fails on encrypted objects and every job failure
# is invisible because the logs never leave the VPC.
resource "aws_vpc_endpoint" "interface" {
  for_each = toset(["kms", "logs"])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for subnet in aws_subnet.private : subnet.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${var.prefix}-${each.value}" }
}
