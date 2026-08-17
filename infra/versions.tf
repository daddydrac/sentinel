terraform {
  # 1.10 is the real floor: backend.hcl.example configures S3 native state
  # locking via use_lockfile, which earlier versions reject outright.
  required_version = ">= 1.10.0"

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.59"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(var.tags, {
      Project     = "HPE-Agentic-Remediation-Demo"
      Environment = var.environment
      ManagedBy   = "Terraform"
    })
  }
}

provider "aws" {
  alias  = "global"
  region = "us-east-1"

  default_tags {
    tags = merge(var.tags, {
      Project     = "HPE-Agentic-Remediation-Demo"
      Environment = var.environment
      ManagedBy   = "Terraform"
    })
  }
}
