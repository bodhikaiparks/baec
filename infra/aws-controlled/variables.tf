variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "availability_zones" {
  description = "Two availability zones in the region: the host runs in the first; RDS needs a subnet in each."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Exactly two availability zones."
  }
}

variable "environment" {
  description = "Name of this environment; prefixes every resource name."
  type        = string
  default     = "baec-controlled"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.environment))
    error_message = "Lower-case letters, digits, and hyphens; 3 to 31 characters."
  }
}

variable "public_hostname" {
  description = "The one public host name for BAEC, e.g. baec.<operator-domain>. Supplied by the operator; never invented."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.public_hostname))
    error_message = "A lower-case DNS host name without scheme, port, or path."
  }
}

variable "https_ingress_cidrs" {
  description = <<-EOT
    Sources allowed to reach HTTPS (443). For the controlled deployment, the operator's own address(es) as /32.
    Widening it is a separate decision (README.md, "Exposure").
  EOT
  type        = list(string)

  validation {
    condition     = length(var.https_ingress_cidrs) > 0 && alltrue([for c in var.https_ingress_cidrs : can(cidrhost(c, 0))])
    error_message = "One or more IPv4 CIDR blocks."
  }
}

variable "http_ingress_cidrs" {
  description = "Sources allowed to reach HTTP (80): the 308 redirect and the ACME HTTP-01 challenge (Let's Encrypt validates from many addresses)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "host_ami_id" {
  description = <<-EOT
    Ubuntu Server 24.04 LTS arm64 AMI in var.region. Look it up with the operator's credentials:
    aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id
  EOT
  type        = string

  validation {
    condition     = can(regex("^ami-[0-9a-f]{8,17}$", var.host_ami_id))
    error_message = "An AMI ID (ami-...)."
  }
}

variable "host_instance_type" {
  description = "Graviton (arm64) instance type. The images from e626634 are linux/arm64; an x86-64 type is refused."
  type        = string
  default     = "t4g.medium"

  validation {
    # Graviton families only: t4g, m6g/m7g/m8g, c6g/c7g/c8g, r6g/r7g/r8g (with optional d/n/gd suffixes).
    condition     = can(regex("^(t4g|[mcr][678]g[dn]?|[mcr]6gd)\\.[a-z0-9]+$", var.host_instance_type))
    error_message = "Must be a Graviton (arm64) instance type; changing CPU architecture requires a rebuild, rescan, and the full Phase 6 security gate."
  }
}

variable "host_root_volume_gb" {
  description = "Encrypted root volume for the host (images, backup staging)."
  type        = number
  default     = 30
}

variable "db_instance_class" {
  description = "RDS instance class (Graviton)."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL major version. RDS picks its current 16.x minor; it is never upgraded across a major version."
  type        = string
  default     = "16"

  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.db_engine_version))
    error_message = "PostgreSQL 16 only."
  }
}

variable "db_allocated_storage_gb" {
  description = "Initial RDS storage (gp3, encrypted)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling."
  type        = number
  default     = 50
}

variable "db_backup_retention_days" {
  description = "RDS automated backups and point-in-time recovery window (runbook: at least 7)."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 7 && var.db_backup_retention_days <= 35
    error_message = "7 to 35 days."
  }
}

variable "db_ca_cert_identifier" {
  description = "RDS server certificate authority. BAEC verifies the server against it (sslmode=verify-full)."
  type        = string
  default     = "rds-ca-rsa2048-g1"
}

variable "vpc_cidr" {
  description = "VPC address range."
  type        = string
  default     = "10.40.0.0/16"
}

variable "backup_lock_days" {
  description = "S3 Object Lock (governance) default retention for logical dumps."
  type        = number
  default     = 30
}

variable "backup_daily_expire_days" {
  description = "Daily dumps are removed after this many days (runbook: 30 daily)."
  type        = number
  default     = 35
}

variable "backup_monthly_expire_days" {
  description = "Monthly dumps are removed after this many days (runbook: 12 monthly)."
  type        = number
  default     = 400
}

variable "release" {
  description = "The approved commit (short SHA) whose images run on the host; used as the image tag."
  type        = string
  default     = "e626634"

  validation {
    condition     = can(regex("^[0-9a-f]{7,40}$", var.release)) && !startswith(var.release, "86f59a1")
    error_message = "A commit SHA. 86f59a1 must never be deployed."
  }
}

variable "release_image_ids" {
  description = "Image IDs of the approved, scanned linux/arm64 images. The host refuses to start images with any other ID."
  type        = map(string)
  default = {
    web   = "sha256:75b492c82ce1b0436ad71da3e66936c1013a12861b796e4120e5e66cf313cc72"
    proxy = "sha256:ce0c5ecd49119e29c1724507c27e45b8835caf190485df7c61c2c1bba9ace263"
    ops   = "sha256:9913c85398a8ded57d0cbae5c37a49d88cac6382d77d2c2d35bdbd2402d60f3d"
  }

  validation {
    condition = (toset(keys(var.release_image_ids)) == toset(["web", "proxy", "ops"]) &&
    alltrue([for id in values(var.release_image_ids) : can(regex("^sha256:[0-9a-f]{64}$", id))]))
    error_message = "Exactly web, proxy, and ops, each a full sha256 image ID."
  }
}

variable "auth_issuer" {
  description = "The OIDC issuer, exactly as the provider's discovery document states it."
  type        = string
  default     = "https://accounts.google.com"
}
