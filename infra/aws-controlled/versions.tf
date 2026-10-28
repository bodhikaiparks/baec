# BAEC controlled deployment on AWS (V0.4 Phase 6; IMPLEMENTATION). See README.md before running anything.
# Validated and planned offline only; nothing here has been applied.

terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100"
    }
  }

  # Local state, never committed (.gitignore). It holds resource identifiers, never a secret value: no resource here
  # is given a password, the RDS administrator password is generated and kept by RDS in Secrets Manager, and every
  # BAEC secret is created without a value (README.md, "Secrets").
}

provider "aws" {
  region = var.region

  # Credentials come only from the operator's short-lived session (IAM Identity Center or an assumed role), never
  # from this directory.
  default_tags {
    tags = {
      Project     = "baec"
      Environment = var.environment
      Phase       = "v0.4-phase6-controlled"
      ManagedBy   = "terraform"
      DataClass   = "synthetic-only"
    }
  }
}
