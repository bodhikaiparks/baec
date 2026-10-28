# No output is a secret value. The database endpoint and the RDS-managed administrator secret ARN are marked
# sensitive only to keep them out of routine terminal output and CI logs.

output "host_public_ip" {
  description = "The Elastic IP. DNS: an A record for var.public_hostname pointing here."
  value       = aws_eip.host.public_ip
}

output "dns_record_required" {
  description = "The one DNS record the operator must create (not managed here)."
  value       = "${var.public_hostname}. 300 IN A ${aws_eip.host.public_ip}"
}

output "oidc_redirect_uri" {
  description = "Register exactly this as the Google OAuth client's authorized redirect URI."
  value       = "https://${var.public_hostname}/oauth2callback"
}

output "host_instance_id" {
  description = "For Session Manager: aws ssm start-session --target <id>"
  value       = aws_instance.host.id
}

output "database_endpoint" {
  description = "RDS host name; the name BAEC verifies in the server certificate (sslmode=verify-full)."
  value       = aws_db_instance.main.address
  sensitive   = true
}

output "database_admin_secret_arn" {
  description = "RDS-managed administrator credential (operator-only: role bootstrap and restores)."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
  sensitive   = true
}

output "secret_names" {
  description = "Secrets Manager secrets to fill (README.md, Secrets). Names only."
  value       = { for name, class in local.secrets : name => aws_secretsmanager_secret.baec[name].name }
}

output "backups_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "releases_bucket" {
  value = aws_s3_bucket.releases.bucket
}
