# Private RDS PostgreSQL 16: not publicly accessible, encrypted, TLS 1.2+ required for every connection, automated
# backups with point-in-time recovery. The administrator password is generated and stored by RDS in Secrets Manager
# (manage_master_user_password), so it never appears in Terraform source, plan, or state. BAEC never connects as the
# administrator: the roles of docs/PRODUCTION_RUNBOOK.md §4 are created by the operator (README.md, "Database").

resource "aws_db_subnet_group" "main" {
  name       = var.environment
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_db_parameter_group" "postgres16" {
  name        = "${var.environment}-postgres16"
  family      = "postgres16"
  description = "BAEC: TLS required, TLS 1.2 minimum"

  parameter {
    name  = "rds.force_ssl"
    value = "1" # the server refuses a connection without TLS
  }

  parameter {
    name  = "ssl_min_protocol_version"
    value = "TLSv1.2"
  }

  parameter {
    name  = "password_encryption"
    value = "scram-sha-256"
  }

  parameter {
    name  = "log_connections"
    value = "1" # role and database names only; no statement text
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier     = var.environment
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  username                    = "baec_admin"
  manage_master_user_password = true
  # No initial database: the operator creates it with the exact grants of runbook §4.

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  port                   = 5432
  multi_az               = false
  availability_zone      = var.availability_zones[0]

  parameter_group_name = aws_db_parameter_group.postgres16.name
  ca_cert_identifier   = var.db_ca_cert_identifier

  backup_retention_period  = var.db_backup_retention_days
  backup_window            = "07:00-07:30"
  maintenance_window       = "sun:08:00-sun:08:30"
  copy_tags_to_snapshot    = true
  delete_automated_backups = false

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
  apply_immediately           = false

  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.environment}-final"

  iam_database_authentication_enabled = false
  performance_insights_enabled        = false
}
