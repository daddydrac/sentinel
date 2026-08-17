terraform {
  # Kept in step with infra/versions.tf; the state backend it provisions
  # requires Terraform 1.10 or newer.
  required_version = ">= 1.10.0"

  required_providers {
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
    tags = {
      Project   = "HPE-Agentic-Remediation-Demo"
      ManagedBy = "Terraform"
      Purpose   = "Terraform-State"
    }
  }
}
