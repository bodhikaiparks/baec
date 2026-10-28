# One Graviton (arm64) host running Docker Compose exactly as deploy/compose.yaml defines: nginx publishes 80/443;
# the web container has no published port. No SSH key and no port 22: operators use Session Manager.

resource "aws_eip" "host" {
  domain = "vpc"
  tags   = { Name = var.environment }
}

resource "aws_eip_association" "host" {
  allocation_id = aws_eip.host.id
  instance_id   = aws_instance.host.id
}

resource "aws_instance" "host" {
  ami                    = var.host_ami_id
  instance_type          = var.host_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.host.id]
  iam_instance_profile   = aws_iam_instance_profile.host.name
  monitoring             = false

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # containers (one network hop further) cannot obtain the host role
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.host_root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/host/cloud-init.yaml.tftpl", {
    compose_env = templatefile("${path.module}/host/compose.env.tftpl", {
      release         = var.release
      public_hostname = var.public_hostname
      auth_issuer     = var.auth_issuer
    })
    host_conf = templatefile("${path.module}/host/host.conf.tftpl", {
      region          = var.region
      environment     = var.environment
      release         = var.release
      backups_bucket  = aws_s3_bucket.backups.bucket
      backups_key_arn = aws_kms_key.backups.arn
      releases_bucket = aws_s3_bucket.releases.bucket
      image_web       = var.release_image_ids["web"]
      image_proxy     = var.release_image_ids["proxy"]
      image_ops       = var.release_image_ids["ops"]
      db_host         = aws_db_instance.main.address
    })
    baecctl          = file("${path.module}/host/baecctl.sh")
    watchdog_service = file("${path.module}/host/baec-watchdog.service")
    watchdog_timer   = file("${path.module}/host/baec-watchdog.timer")
    backup_service   = file("${path.module}/host/baec-backup.service")
    backup_timer     = file("${path.module}/host/baec-backup.timer")
  })

  tags = { Name = var.environment }

  lifecycle {
    precondition {
      condition     = can(regex("g[dn]?\\.", var.host_instance_type))
      error_message = "The host must be Graviton (arm64)."
    }
  }
}

# The AMI must be arm64. Checked against AWS at plan time when credentials exist; a failed check is reported, and the
# instance type precondition above already refuses x86-64 instance types.
check "host_ami_is_arm64" {
  data "aws_ami" "host" {
    filter {
      name   = "image-id"
      values = [var.host_ami_id]
    }
    filter {
      name   = "architecture"
      values = ["arm64"]
    }
  }

  assert {
    condition     = data.aws_ami.host.architecture == "arm64"
    error_message = "host_ami_id is not an arm64 image."
  }
}
