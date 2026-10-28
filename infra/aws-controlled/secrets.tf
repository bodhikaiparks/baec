# Secret containers only: Terraform creates each secret WITHOUT a value, so no credential is ever in Terraform source,
# plan, or state. The operator writes the values (README.md, "Secrets"); the host turns them into the files of
# docs/PRODUCTION_RUNBOOK.md §2.2.
#
#   class     who reads it                                      secrets
#   web       the host role, into files only the web container  app DB URL, sign-in lookup DB URL, Streamlit [auth]
#             (uid 10001) can read                              (OIDC client secret, cookie secret)
#   backup    the host role, into a file only the backup job    backup DB URL
#             (uid 70) can read
#   operator  the operator's own short-lived session, for one   migration DB URL, provisioning DB URL
#             job, then removed; the host role is denied
# The RDS administrator credential is created and kept by RDS (database.tf); it is operator-only and used for the
# role bootstrap and for restores, never by BAEC.

locals {
  secrets = {
    "web/app-database-url"               = "web"
    "web/auth-database-url"              = "web"
    "web/streamlit-auth-toml"            = "web"
    "backup/backup-database-url"         = "backup"
    "operator/migration-database-url"    = "operator"
    "operator/provisioning-database-url" = "operator"
  }
}

resource "aws_secretsmanager_secret" "baec" {
  for_each                = local.secrets
  name                    = "${var.environment}/${each.key}"
  description             = "BAEC ${each.value} secret (value written by the operator, never by Terraform)"
  recovery_window_in_days = 7
  tags                    = { SecretClass = each.value }
}

# Operator secrets: an explicit deny for the host role, so no later policy change on the host can reach them.
resource "aws_secretsmanager_secret_policy" "operator_only" {
  for_each   = { for k, v in local.secrets : k => v if v == "operator" }
  secret_arn = aws_secretsmanager_secret.baec[each.key].arn
  policy     = data.aws_iam_policy_document.operator_secret[each.key].json
}

data "aws_iam_policy_document" "operator_secret" {
  for_each = { for k, v in local.secrets : k => v if v == "operator" }

  statement {
    sid       = "DenyTheBaecHost"
    effect    = "Deny"
    actions   = ["secretsmanager:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.host.arn]
    }
  }
}
