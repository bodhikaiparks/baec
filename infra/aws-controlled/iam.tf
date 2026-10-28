# The host's runtime role: the minimum the host needs, nothing to administer infrastructure.
#   * Session Manager (no SSH, no inbound port 22)
#   * read the web and backup secrets (never the operator secrets; secrets.tf adds an explicit deny)
#   * add objects to the backups bucket under daily/ and monthly/ (no read, list, or delete)
#   * read release artifacts
# Containers cannot use this role: IMDSv2 with a hop limit of 1 (compute.tf) keeps the instance credentials away from
# every container, so the web process holds only its own mounted files. Infrastructure deployment permissions are
# separate: iam/deployer-policy.json, used by the operator's own session, never by the host.

resource "aws_iam_role" "host" {
  name                 = "${var.environment}-host"
  path                 = "/baec-controlled/"
  assume_role_policy   = data.aws_iam_policy_document.host_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "host_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_instance_profile" "host" {
  name = "${var.environment}-host"
  path = "/baec-controlled/"
  role = aws_iam_role.host.name
}

resource "aws_iam_role_policy_attachment" "host_ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "host" {
  name   = "baec-host-runtime"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

data "aws_iam_policy_document" "host" {
  statement {
    sid     = "ReadWebAndBackupSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      for name, class in local.secrets : aws_secretsmanager_secret.baec[name].arn if class != "operator"
    ]
  }

  statement {
    sid     = "AddBackupObjectsOnly"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.backups.arn}/daily/*",
      "${aws_s3_bucket.backups.arn}/monthly/*",
    ]
  }

  statement {
    sid       = "EncryptBackupUploads"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"] # Decrypt: S3 needs it to complete a multipart upload
    resources = [aws_kms_key.backups.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadReleaseArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
  }
}
