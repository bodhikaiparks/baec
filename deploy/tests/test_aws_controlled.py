"""V0.4 Phase 6: the AWS controlled-deployment definition (infra/aws-controlled), checked without any AWS access.

The host script (baecctl) runs in Ubuntu 24.04, the host's OS, with fake `docker` and `aws` commands that only record
their arguments: the watchdog's restart budget and hold, the backup's failure reporting and staging cleanup, and the
image-ID gate. The Terraform is checked as text for the properties the controlled deployment depends on, and its
cloud-init is rendered and parsed by Terraform itself. Nothing is applied, and no AWS endpoint is contacted.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

from deploy_support import ROOT, docker, run

INFRA = ROOT / "infra" / "aws-controlled"
UBUNTU = "ubuntu:24.04@sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c"
TERRAFORM = "hashicorp/terraform:1.9.8"
APPROVED = {"web": "sha256:75b492c82ce1b0436ad71da3e66936c1013a12861b796e4120e5e66cf313cc72",
            "proxy": "sha256:ce0c5ecd49119e29c1724507c27e45b8835caf190485df7c61c2c1bba9ace263",
            "ops": "sha256:9913c85398a8ded57d0cbae5c37a49d88cac6382d77d2c2d35bdbd2402d60f3d"}

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Records every call; answers from files in $FAKE.
echo "docker $*" >> "$FAKE/calls"
case "$1" in
  inspect) [[ -f $FAKE/inspect ]] || exit 1; cat "$FAKE/inspect" ;;
  image) name=${!#}; name=${name#baec-}; name=${name%%:*}; [[ -f $FAKE/image-$name ]] || exit 1; cat "$FAKE/image-$name" ;;
  restart|stop) ;;
  compose)
    if [[ "$*" == *"run --rm backup"* ]]; then
      [[ -f $FAKE/dump-fails ]] && exit 1
      dump=$BAEC_STAGING_DIR/baec-20261028T060000Z.dump
      echo "synthetic dump" > "$dump"
      sum=$(sha256sum "$dump" | cut -c1-64)
      [[ -f $FAKE/corrupt ]] && sum=0000000000000000000000000000000000000000000000000000000000000000
      echo "$sum  baec-20261028T060000Z.dump" > "$dump.sha256"
    fi ;;
esac
"""

FAKE_AWS = r"""#!/usr/bin/env bash
echo "aws $*" >> "$FAKE/calls"
[[ -f $FAKE/aws-fails ]] && { echo "upload failed (synthetic)" >&2; exit 1; }
exit 0
"""

HOST_CONF = f"""AWS_REGION_NAME=us-east-1
BAEC_ENVIRONMENT=baec-controlled
BAEC_RELEASE=e626634
BAEC_BACKUPS_BUCKET=baec-controlled-backups-test
BAEC_BACKUPS_KEY_ARN=arn:aws:kms:us-east-1:000000000000:key/test
BAEC_RELEASES_BUCKET=baec-controlled-releases-test
BAEC_IMAGE_WEB={APPROVED['web']}
BAEC_IMAGE_PROXY={APPROVED['proxy']}
BAEC_IMAGE_OPS={APPROVED['ops']}
BAEC_DB_HOST=baec-controlled.example.invalid
"""


@pytest.fixture
def host(tmp_path):
    """A scratch 'host': baecctl, fake docker/aws, a state directory, a staging directory."""
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "calls").write_text("")
    for name, body in (("docker", FAKE_DOCKER), ("aws", FAKE_AWS)):
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    (tmp_path / "host.conf").write_text(HOST_CONF)
    (tmp_path / "staging").mkdir()
    return tmp_path


def baecctl(host: Path, *arguments: str, **environment: str):
    variables = {"FAKE": "/h/fake", "BAEC_DOCKER": "/h/fake/docker", "BAEC_AWS": "/h/fake/aws",
                 "BAEC_HOST_CONF": "/h/host.conf", "BAEC_STAGING_DIR": "/h/staging",
                 "BAEC_WATCHDOG_STATE": "/h/watchdog", **environment}
    command = [docker(), "run", "--rm", "--network", "none", "-v", f"{host}:/h",
               "-v", f"{INFRA / 'host' / 'baecctl.sh'}:/usr/local/sbin/baecctl:ro"]
    for key, value in variables.items():
        command += ["-e", f"{key}={value}"]
    return run(*command, UBUNTU, "bash", "/usr/local/sbin/baecctl", *arguments, check=False)


def calls(host: Path) -> list[str]:
    return (host / "fake" / "calls").read_text().splitlines()


def actions(host: Path) -> list[str]:
    return [c for c in calls(host) if c.split()[1] in ("restart", "stop")]


# --- watchdog -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["healthy true", "starting true", "none true", "unhealthy false"])
def test_the_watchdog_leaves_a_healthy_starting_or_stopped_container_alone(host, state):
    (host / "fake" / "inspect").write_text(state + "\n")
    result = baecctl(host, "watchdog")
    assert result.returncode == 0 and result.stdout == "" and actions(host) == []


def test_the_watchdog_ignores_a_missing_container(host):
    result = baecctl(host, "watchdog")
    assert result.returncode == 0 and result.stdout == "" and actions(host) == []


def test_the_watchdog_restarts_an_unhealthy_container_then_stops_and_holds_it(host):
    (host / "fake" / "inspect").write_text("unhealthy true\n")
    outputs = [baecctl(host, "watchdog", BAEC_WATCHDOG_NOW=str(1000 + i * 60)).stdout for i in range(5)]
    assert outputs[0] == "<3>WATCHDOG_RESTART container=baec-web-1: unhealthy; restarted (1/3 in 1800s)\n"
    assert outputs[2].startswith("<3>WATCHDOG_RESTART") and "(3/3 in 1800s)" in outputs[2]
    assert outputs[3] == ("<2>WATCHDOG_HELD container=baec-web-1: still unhealthy after 3 restarts in 1800s; "
                          "stopped, not serving; operator action required\n")
    assert outputs[4] == ""  # held: no further action, no restart loop
    assert actions(host) == ["docker restart baec-web-1"] * 3 + ["docker stop baec-web-1"]
    # it only ever inspects, restarts, or stops: never a database command, never compose
    assert {c.split()[1] for c in calls(host)} == {"inspect", "restart", "stop"}


def test_restarts_outside_the_window_do_not_count(host):
    (host / "fake" / "inspect").write_text("unhealthy true\n")
    for now in (1000, 1060, 1120):
        baecctl(host, "watchdog", BAEC_WATCHDOG_NOW=str(now))
    later = baecctl(host, "watchdog", BAEC_WATCHDOG_NOW=str(1000 + 1800 + 1))
    assert later.stdout.startswith("<3>WATCHDOG_RESTART") and "(3/3" in later.stdout
    assert actions(host) == ["docker restart baec-web-1"] * 4


def test_a_held_container_is_released_only_when_healthy_again(host):
    (host / "fake" / "inspect").write_text("unhealthy true\n")
    for now in range(4):
        baecctl(host, "watchdog", BAEC_WATCHDOG_NOW=str(1000 + now))
    assert (host / "watchdog" / "held").exists()
    (host / "fake" / "inspect").write_text("healthy true\n")  # an operator fixed it and started it again
    recovered = baecctl(host, "watchdog", BAEC_WATCHDOG_NOW="2000")
    assert recovered.stdout == "<5>WATCHDOG_RECOVERED container=baec-web-1: healthy again; hold cleared\n"
    assert not (host / "watchdog" / "held").exists()
    (host / "fake" / "inspect").write_text("unhealthy true\n")
    assert "(1/3" in baecctl(host, "watchdog", BAEC_WATCHDOG_NOW="2060").stdout  # a fresh budget


# --- backup --------------------------------------------------------------------------------------------------------------


def test_a_successful_backup_uploads_encrypted_and_leaves_no_staging_copy(host):
    result = baecctl(host, "backup", BAEC_DAY_OF_MONTH="15")
    assert result.returncode == 0
    assert re.fullmatch(r"<5>BACKUP_OK file=baec-20261028T060000Z\.dump sha256=[0-9a-f]{64} bytes=15 "
                        r"destination=s3-kms\n", result.stdout)
    uploads = [c for c in calls(host) if c.startswith("aws s3 cp")]
    assert [u.split()[-1] for u in uploads] == [
        "s3://baec-controlled-backups-test/daily/baec-20261028T060000Z.dump",
        "s3://baec-controlled-backups-test/daily/baec-20261028T060000Z.dump.sha256"]
    assert all("--sse aws:kms --sse-kms-key-id arn:aws:kms:us-east-1:000000000000:key/test" in u and
               "--checksum-algorithm SHA256" in u for u in uploads)
    assert list((host / "staging").iterdir()) == []


def test_the_first_of_the_month_also_keeps_a_monthly_copy(host):
    baecctl(host, "backup", BAEC_DAY_OF_MONTH="01")
    prefixes = [c.split()[-1].split("/")[3] for c in calls(host) if c.startswith("aws s3 cp")]
    assert prefixes == ["daily", "daily", "monthly", "monthly"]


@pytest.mark.parametrize("failure,step,uploaded", [
    ("dump-fails", "<3>BACKUP_FAILED step=dump\n", 0),
    ("corrupt", "<3>BACKUP_FAILED step=checksum file=baec-20261028T060000Z.dump\n", 0),
    ("aws-fails", "<3>BACKUP_FAILED step=upload object=daily/baec-20261028T060000Z.dump\n", 1),
])
def test_a_failed_backup_reports_failure_never_success_and_removes_the_staging_copy(host, failure, step, uploaded):
    (host / "fake" / failure).write_text("")
    result = baecctl(host, "backup", BAEC_DAY_OF_MONTH="15")
    assert result.returncode == 1 and result.stdout == step and "BACKUP_OK" not in result.stdout
    assert len([c for c in calls(host) if c.startswith("aws s3 cp")]) == uploaded
    assert list((host / "staging").iterdir()) == []  # the plaintext dump never outlives a failed run
    assert "postgresql://" not in result.stdout + result.stderr


# --- the image gate -------------------------------------------------------------------------------------------------------


def _images(host: Path, **overrides: str) -> None:
    for name, image_id in APPROVED.items():
        (host / "fake" / f"image-{name}").write_text(overrides.get(name, f"{image_id} linux/arm64") + "\n")


def test_only_the_approved_arm64_images_pass_the_image_gate(host):
    _images(host)
    assert baecctl(host, "verify-images").stdout == (
        "<6>images verified: release e626634, web proxy ops are the approved linux/arm64 image IDs\n")
    _images(host, web="sha256:" + "1" * 64 + " linux/arm64")
    assert baecctl(host, "verify-images").stdout == "<3>baec-web:e626634 is not the approved linux/arm64 image\n"
    _images(host, proxy=f"{APPROVED['proxy']} linux/amd64")
    assert baecctl(host, "verify-images").stdout == "<3>baec-proxy:e626634 is not the approved linux/arm64 image\n"


def test_the_host_refuses_release_86f59a1(host):
    (host / "host.conf").write_text(HOST_CONF.replace("BAEC_RELEASE=e626634", "BAEC_RELEASE=86f59a1"))
    result = baecctl(host, "verify-images")
    assert result.returncode == 1 and result.stdout == "<3>release 86f59a1 must never be deployed\n"


# --- the Terraform definition (text) ---------------------------------------------------------------------------------------


def _tf(name: str) -> str:
    return (INFRA / name).read_text(encoding="utf-8")


def test_the_database_is_private_encrypted_tls_only_and_reachable_only_from_the_host():
    database, network = _tf("database.tf"), _tf("network.tf")
    for setting in ("publicly_accessible    = false", "storage_encrypted     = true",
                    "manage_master_user_password = true", "deletion_protection       = true",
                    "allow_major_version_upgrade = false"):
        assert setting in database
    assert re.search(r'name\s*=\s*"rds\.force_ssl"\s*\n\s*value\s*=\s*"1"', database)
    assert re.search(r'name\s*=\s*"ssl_min_protocol_version"\s*\n\s*value\s*=\s*"TLSv1\.2"', database)
    assert not re.search(r"^\s*password\s*=", database, re.M)  # no password in source
    rule = network.split('resource "aws_vpc_security_group_ingress_rule" "database_from_host"')[1].split("}")[0]
    assert "referenced_security_group_id = aws_security_group.host.id" in rule and "cidr" not in rule
    assert re.findall(r"from_port\s*=\s*5432", network) and \
        not re.search(r'5432[\s\S]{0,200}cidr_ipv4\s*=\s*"0\.0\.0\.0/0"', network)


def test_only_nginx_ports_are_open_and_the_host_has_no_ssh_and_imdsv2_hop_limit_one():
    network, compute = _tf("network.tf"), _tf("compute.tf")
    ingress = re.findall(r'resource "aws_vpc_security_group_ingress_rule" "(\w+)"[\s\S]*?from_port\s*=\s*(\d+)', network)
    assert sorted(ingress) == [("database_from_host", "5432"), ("host_http", "80"), ("host_https", "443")]
    assert "22" not in [port for _, port in ingress] and "key_name" not in compute
    assert 'http_tokens                 = "required"' in compute and "http_put_response_hop_limit = 1" in compute
    assert "encrypted             = true" in compute


def test_the_host_is_arm64_and_runs_only_the_approved_release():
    variables = _tf("variables.tf")
    assert 'default     = "t4g.medium"' in variables and 'default     = "e626634"' in variables
    assert '!startswith(var.release, "86f59a1")' in variables
    for image_id in APPROVED.values():
        assert image_id in variables


def test_iam_grants_no_administrator_or_wildcard_actions_and_keeps_operator_secrets_from_the_host():
    iam, secrets = _tf("iam.tf"), _tf("secrets.tf")
    assert "AdministratorAccess" not in iam and '"*"' not in iam.split("host_trust")[0]
    assert re.search(r'actions\s*=\s*\["s3:PutObject"\]', iam)  # backups: add only, never read or delete
    assert "s3:GetObject" in iam and iam.count("s3:GetObject") == 1  # releases only
    assert 'if class != "operator"' in iam
    assert 'effect    = "Deny"' in secrets and "aws_iam_role.host.arn" in secrets
    assert "aws_secretsmanager_secret_version" not in secrets  # Terraform never holds a secret value
    for name in ("deployer-policy.json", "operator-policy.json"):
        policy = json.loads((INFRA / "iam" / name).read_text())
        for statement in policy["Statement"]:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            assert "*" not in actions and not any(a.endswith(":*") for a in actions)
            assert "AdministratorAccess" not in json.dumps(statement)


def test_backups_are_versioned_locked_kms_encrypted_tls_only_and_private():
    storage = _tf("storage.tf")
    assert "object_lock_enabled = true" in storage and 'mode = "GOVERNANCE"' in storage
    assert 'sse_algorithm     = "aws:kms"' in storage and "enable_key_rotation     = true" in storage
    assert storage.count('status = "Enabled"') >= 2 + 3  # versioning on both buckets; three lifecycle rules
    assert storage.count('variable = "aws:SecureTransport"') == 2
    assert storage.count("restrict_public_buckets = true") == 2


def test_terraform_never_tracks_state_variables_or_plans():
    ignore = (ROOT / ".gitignore").read_text()
    for rule in ("infra/aws-controlled/.terraform/", "infra/aws-controlled/*.tfstate", "infra/aws-controlled/*.tfvars",
                 "infra/aws-controlled/*.tfplan"):
        assert rule in ignore
    tracked = run("git", "-C", str(ROOT), "ls-files", "infra/aws-controlled").stdout.split()
    assert not [f for f in tracked if f.endswith((".tfstate", ".tfvars", ".tfplan")) or "/.terraform/" in f]


def test_cloud_init_renders_as_yaml_and_installs_baecctl_unchanged(tmp_path):
    expression = textwrap.dedent("""\
        jsonencode(yamldecode(templatefile("host/cloud-init.yaml.tftpl", {
          compose_env = templatefile("host/compose.env.tftpl", {release = "e626634", public_hostname = "baec.example.invalid",
                                                               auth_issuer = "https://accounts.google.com"}),
          host_conf = "AWS_REGION_NAME=us-east-1\\n", baecctl = file("host/baecctl.sh"),
          watchdog_service = file("host/baec-watchdog.service"), watchdog_timer = file("host/baec-watchdog.timer"),
          backup_service = file("host/baec-backup.service"), backup_timer = file("host/baec-backup.timer"),
        })))
        """).replace("\n", " ")
    # Only the host templates, in an empty working directory: no state, providers, or init are involved.
    (tmp_path / "host").mkdir()
    for path in (INFRA / "host").iterdir():
        (tmp_path / "host" / path.name).write_bytes(path.read_bytes())
    result = run(docker(), "run", "--rm", "-i", "--network", "none", "-v", f"{tmp_path}:/w", "-w", "/w", TERRAFORM,
                 "console", input=expression + "\n")
    config = json.loads(json.loads(result.stdout.strip()))
    files = {f["path"]: f["content"] for f in config["write_files"]}
    assert files["/usr/local/sbin/baecctl"] == (INFRA / "host" / "baecctl.sh").read_text()
    assert "BAEC_AUTH_ISSUER=https://accounts.google.com\n" in files["/etc/baec/compose.env"]
    assert "BAEC_RELEASE=e626634\n" in files["/etc/baec/compose.env"]
    settings = re.findall(r"^([A-Z_]+)=", files["/etc/baec/compose.env"], re.M)
    assert settings == ["BAEC_RELEASE", "BAEC_PUBLIC_HOST", "BAEC_PUBLIC_ORIGIN", "BAEC_AUTH_ISSUER", "BAEC_HSTS_MAX_AGE",
                        "BAEC_TLS_DIR", "BAEC_SECRETS_DIR", "BAEC_BACKUP_STAGING_DIR", "BAEC_DB_POOL_MAX_SIZE",
                        "BAEC_AUTH_DB_POOL_MAX_SIZE"]  # names and settings only; every secret is a mounted file
    host_conf = (INFRA / "host" / "host.conf.tftpl").read_text()
    assert re.findall(r"^([A-Z_]+)=", host_conf, re.M) == [
        "AWS_REGION_NAME", "BAEC_ENVIRONMENT", "BAEC_RELEASE", "BAEC_BACKUPS_BUCKET", "BAEC_BACKUPS_KEY_ARN",
        "BAEC_RELEASES_BUCKET", "BAEC_IMAGE_WEB", "BAEC_IMAGE_PROXY", "BAEC_IMAGE_OPS", "BAEC_DB_HOST"]
    assert set(config["packages"]) == {"docker.io", "docker-compose-v2", "certbot", "openssl", "curl"}
