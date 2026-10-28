# BAEC controlled deployment on AWS (V0.4 Phase 6)

Infrastructure as code, permissions model, and deployment plan for **one controlled BAEC environment** on AWS.
**Status: written and validated offline. Nothing has been applied, and no AWS resource exists.** Every claim about the
live environment below is a requirement to verify on it, not a result. Synthetic data only. No real customer, pricing,
PHI, buyer statement, or customer document may enter this environment.

Release: **`e626634`** (`e626634976722801625d768843e28a9314f2a4bd`), images `linux/arm64`, scanned
(`docs/V04_PHASE6_SECURITY_REMEDIATION_REPORT.md`). **`86f59a1` must never be deployed**: Terraform's variable
validation refuses it, and so does `baecctl`.

The architecture is the one Phase 5 proved locally (`deploy/compose.yaml`, `docs/PRODUCTION_RUNBOOK.md`), placed on
AWS without changing it: one Docker host running nginx and the BAEC web container, with a managed PostgreSQL 16.

```
                       internet
                          │ 443 (operator /32 only)   80 (308 redirect, ACME)
                 ┌────────▼───────────────────────────────────────────┐ VPC 10.40.0.0/16
  public subnet  │ EC2 t4g.medium (arm64), Elastic IP, no SSH         │
  10.40.1.0/24   │   proxy  (nginx 1.30, publishes 80/443)            │
                 │   web    (Streamlit; no published port)            │
                 │   migrate / provision / backup (operator jobs)     │──── S3 gateway endpoint ──▶ S3 backups, releases
                 └────────┬───────────────────────────────────────────┘
                          │ 5432, TLS verify-full, from the host security group only
  private subnets┌────────▼──────────────────┐
  10.40.11-12/24 │ RDS PostgreSQL 16         │  not publicly accessible; no internet route
                 │ db.t4g.micro, encrypted   │
                 └───────────────────────────┘
```

## 1. Resources (`terraform plan`: 54 to add)

| Area | Resources |
|---|---|
| Network | VPC; internet gateway; 1 public subnet (no auto public IP); 2 private database subnets (two AZs, required by RDS); public and database route tables (the database one has no internet route); S3 gateway endpoint; default security group with no rules |
| Security groups | **host**: in 443 from `https_ingress_cidrs`, in 80 from `http_ingress_cidrs`; out 443 and 80 to anywhere, out 5432 to the database group only. **database**: in 5432 from the host group only; no egress |
| Host | EC2 `t4g.medium` (Graviton, arm64), Ubuntu 24.04 arm64 AMI, 30 GB encrypted gp3 root, IMDSv2 required with hop limit 1, no key pair, Elastic IP, cloud-init (`host/`) |
| Database | RDS PostgreSQL 16 `db.t4g.micro`, 20 GB gp3 (autoscaling to 50), encrypted, `publicly_accessible = false`, single AZ, CA `rds-ca-rsa2048-g1`, parameter group `rds.force_ssl=1` and `ssl_min_protocol_version=TLSv1.2`, automated backups 7 days with PITR, deletion protection, final snapshot, administrator password managed by RDS in Secrets Manager |
| Storage | **backups** bucket: versioned, Object Lock (governance, 30 days), SSE-KMS with a dedicated rotating key, TLS-only policy, public access blocked, lifecycle (daily/ 35 days, monthly/ 400 days, noncurrent 1 day, abandoned uploads 1 day). **releases** bucket: versioned, SSE-S3, TLS-only, public access blocked |
| Secrets | 6 Secrets Manager secrets **without values** (web ×3, backup ×1, operator ×2); the operator secrets deny the host role explicitly |
| IAM | host role and instance profile (path `/baec-controlled/`); `AmazonSSMManagedInstanceCore`; one inline runtime policy |

**Approximate monthly cost** (us-east-1 on-demand list prices from memory; confirm in the AWS Pricing Calculator
before approval): EC2 t4g.medium ≈ $25, RDS db.t4g.micro ≈ $12 plus 20 GB gp3 ≈ $2.5, public IPv4 ≈ $3.6, EBS 30 GB
≈ $2.4, KMS key $1, Secrets Manager 7 × $0.40 ≈ $2.8, S3 and requests under $1. **About $50 per month**, excluding
data transfer.

## 2. Choices and why

* **Region `us-east-1`** (variable): Graviton EC2 and RDS available; any region with both works. Changing it needs
  the AZ list and the AMI ID for that region.
* **`t4g.medium` (2 vCPU, 4 GiB)**: Streamlit, nginx, and an occasional `pg_dump` fit with headroom; `t4g.small`
  (2 GiB) would be tight. Any Graviton type is accepted. **An x86-64 type is refused** by variable validation and a
  resource precondition, and an x86-64 AMI is flagged by a `check` block. A CPU architecture change requires a clean
  rebuild from `e626634`, a rescan, and the full Phase 6 security gate again.
* **Ubuntu 24.04 arm64**: `docker.io`, `docker-compose-v2`, and `certbot` come from the distribution's signed
  repositories; the AWS CLI from the `aws-cli` snap. The AMI is a variable, never looked up silently.
* **No SSH**: AWS Systems Manager Session Manager only. No key pair, no port 22.
* **No NAT gateway**: the database subnets need no internet; the host has a public address.
* **Single-AZ RDS**: a controlled environment. RDS PITR and the logical dumps are the recovery layers. Multi-AZ is a
  later decision.
* **HTTPS restricted to the operator** (`https_ingress_cidrs`, required, no default): during the controlled phase
  only the operator's address reaches the Console. Port 80 stays open to everyone because Let's Encrypt validates
  from many addresses; nginx answers it with a 308 redirect only (no query string forwarded). Widening 443 is a
  separate, explicit decision.

## 3. IAM (three separate identities; none is the root user; nothing gets `AdministratorAccess`)

| Identity | Used by | Permissions |
|---|---|---|
| **deployer** (`iam/deployer-policy.json`) | the operator's own short-lived session (IAM Identity Center or an assumed role with MFA) for `terraform plan/apply/destroy` | create/manage only this design's resources: EC2 network and instance actions limited to the chosen region; RDS, S3, Secrets Manager, KMS alias, and IAM role/profile actions limited to `baec-controlled` names and the `/baec-controlled/` path; `iam:PassRole` only for that path and only to EC2; attach only `AmazonSSMManagedInstanceCore` |
| **operator** (`iam/operator-policy.json`) | the operator's session for running BAEC | write secret values; read the operator secrets and the RDS administrator secret; Session Manager into instances tagged `Project=baec`; upload releases; read backups for restore drills; **explicitly denied** deleting backups, bypassing governance retention, or changing Object Lock |
| **host role** (`iam.tf`) | the EC2 instance | Session Manager core; `GetSecretValue` on the 3 web and 1 backup secrets only; `PutObject` to `backups/daily/*` and `backups/monthly/*` only (no read, list, or delete); KMS `GenerateDataKey`/`Decrypt` for that key **only via S3**; `GetObject` on `releases/releases/*` |

Replace `ACCOUNT_ID` and `REGION` in the two JSON files before creating the policies. **Broader than ideal (documented,
temporary to initial creation):** EC2 `Create*`/`Run*` actions cannot be scoped to resources that do not exist yet, so
they are limited by region; `kms:CreateKey` cannot be resource-scoped; `iam:CreateServiceLinkedRole` is allowed for
`rds.amazonaws.com` only (first RDS use in an account). After the first apply, the deployer policy can be detached
until the next infrastructure change. If an apply stops on `AccessDenied`, add the one action it names. Never widen to
a wildcard or to `AdministratorAccess`.

**Containers cannot use the host role:** IMDSv2 with a hop limit of 1 keeps the instance credentials one network hop
away from every container. The web process holds only its mounted files.

## 4. Secrets

No credential is in git, an image, Terraform source, plan, or state, or a log.

| Secret (Secrets Manager `baec-controlled/…`) | Class | On the host | Received by |
|---|---|---|---|
| `web/app-database-url` | web | `/etc/baec/secrets/baec_app_database_url` (uid 10001, 0400) | web |
| `web/auth-database-url` | web | `…/baec_auth_database_url` (10001, 0400) | web |
| `web/streamlit-auth-toml` (OIDC client secret, cookie secret) | web | `…/baec_streamlit_auth.toml` (10001, 0400) | web |
| `backup/backup-database-url` | backup | `…/baec_backup_database_url` (uid 70, 0400) | backup job |
| `operator/migration-database-url` | operator | tmpfs, for the job only, then removed | migrate job |
| `operator/provisioning-database-url` | operator | tmpfs, for the job only, then removed | provision job |
| RDS administrator (RDS-managed `rds!db-…`) | operator | never stored on the host | role bootstrap and restore only; **never BAEC** |
| TLS private key | host | `/etc/baec/tls/privkey.pem` (root, 0400), from certbot | proxy |
| RDS CA bundle (public) | — | `…/baec_postgres_ca.crt` (0444) | every DB client |
| Claude API key | — | **none**: browser session only (the web process refuses to start with any `ANTHROPIC_*`) | — |

* `baecctl fetch-secrets` (host role) writes the web and backup files. Values travel only through a pipe, never argv
  or a log line.
* `baecctl operator-job migrate|provision …` requires the **operator's own** short-lived AWS credentials, exported
  into that Session Manager shell only. It refuses the host role. It reads the one operator secret into a 0700 tmpfs
  directory with the CA, runs the compose job against that directory, and removes it. The web service never sees it.
* Writing a value (operator workstation, never echoed or stored in shell history):
  `read -rs V && printf %s "$V" | aws secretsmanager put-secret-value --secret-id baec-controlled/web/app-database-url --secret-string file:///dev/stdin; unset V`

Database URL form (runbook §2.2):
`postgresql://<role>:<password>@<database_endpoint>:5432/baec?sslmode=verify-full&sslrootcert=/run/secrets/baec_postgres_ca.crt`

## 5. Database TLS

* **Server:** `rds.force_ssl=1` refuses unencrypted connections; `ssl_min_protocol_version=TLSv1.2`; certificate
  authority `rds-ca-rsa2048-g1`.
* **Client:** every BAEC connection uses `sslmode=verify-full` with the RDS CA bundle supplied explicitly as a file.
  `baecctl fetch-secrets` downloads it over HTTPS from `truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem`,
  checks that it parses as certificates, and logs its SHA-256 (compare it with AWS's published bundle). Never `require`
  or `verify-ca`.
* **Proof on the live environment** (`baecctl verify-db-tls`, using a nonexistent probe role and no password, so no
  credential is ever sent):

  | Check | Expected |
  |---|---|
  | plaintext connection | refused by the server ("no encryption") |
  | wrong CA | refused ("certificate verify failed") |
  | wrong host name (the endpoint's IP address) | refused ("does not match host name") |
  | correct endpoint with `verify-full` | handshake succeeds and authentication is reached |

  Then `python -m baec_console.readiness` proves verified TLS with the real roles.

## 6. Database roles

Exactly runbook §4: `baec_migrator`, `baec_app`, `baec_auth_lookup`, `baec_provisioner`, `baec_backup`, and database
`baec`. The operator runs the SQL in an interactive `psql` as the RDS administrator, over `verify-full`, from the ops
image in a Session Manager shell. Passwords are set with `\password` (never on a command line or in a statement). Then
the operator writes the five URLs to Secrets Manager. BAEC never runs as the administrator.

* **To verify on RDS:** `CREATE ROLE … BYPASSRLS` for `baec_backup` requires the creating role to hold `BYPASSRLS`. If
  RDS's administrator cannot grant it, follow runbook §4's fallback (RDS PITR as the backup; logical dumps as the
  administrator, operator-run) and record the deviation. Do not weaken RLS or role separation to make it work.
* **Connection limits:** role limits sum to 28 (`baec_app` 12 ≥ 4 + 2; `baec_auth_lookup` 8 ≥ 2 + 2), below
  `db.t4g.micro`'s default `max_connections`. The web startup gate refuses missing or too-small limits.

## 7. DNS, TLS certificate, HTTPS

* **DNS (operator; not managed here; do not change until approved):** one record, printed by `terraform output
  dns_record_required`: `<public_hostname>. 300 IN A <host_public_ip>`. No wildcard.
* **Certificate:** Let's Encrypt, HTTP-01, certbot in standalone mode while the proxy is stopped, so the tested nginx
  configuration is unchanged:
  ```sh
  certbot certonly --standalone -d <public_hostname> -m <operator-email> --agree-tos --non-interactive \
    --pre-hook "docker stop baec-proxy-1 || true" --post-hook "docker start baec-proxy-1 || true" \
    --deploy-hook "/usr/local/sbin/baecctl install-certificate"
  ```
  The hooks are stored for renewal. Ubuntu's `certbot.timer` renews before expiry with a few seconds of proxy
  downtime.
* **nginx:** unchanged from Phase 5/6: TLS 1.2/1.3, HTTP → 308 HTTPS with the query string dropped (an OAuth `code`
  sent over HTTP is never forwarded), unknown host names refused, HSTS (`86400` to start), `Secure` added to every
  cookie, and `HttpOnly`/`SameSite=Lax` as Streamlit sets them. The web container has no published port.
* **Must be verified on the real host before acceptance** (not claimed here): every item above, from an external
  client, including `_streamlit_user` being `Secure; HttpOnly; SameSite=Lax`, the XSRF cookie behavior, and direct
  backend access (`:8501`) being unreachable.

## 8. Google OpenID Connect

| Item | Value |
|---|---|
| Issuer (`BAEC_AUTH_ISSUER`) | `https://accounts.google.com` (exact) |
| Discovery (`server_metadata_url`) | `https://accounts.google.com/.well-known/openid-configuration` |
| Client type | OAuth client ID, type **Web application**, in a Google Cloud project the operator controls |
| **Authorized redirect URI** | **`https://<public_hostname>/oauth2callback`**, exactly (`terraform output oidc_redirect_uri`). No other URI, origin, or wildcard |
| Consent screen | External, publishing status **Testing**, with the one test user only (or Internal for a Workspace). This is Google's gate; BAEC's own gate is the provisioned `(issuer, subject)` link |
| Scopes | `openid` (plus `email` only for display; never for authorization) |
| Subject | Google `sub`: stable, public. Obtain it from the person's first (refused) sign-in, then `provision link` |
| `cookie_secret` | at least 32 random characters, unique to this deployment |

BAEC never infers access from email, email domain, Workspace domain, name, or any browser-supplied claim. Only the
provisioned `(https://accounts.google.com, sub)` mapping grants access. There is no signup.

**Check on the first live sign-in:** Google documents ID-token `iss` as either `https://accounts.google.com` or
`accounts.google.com`. Authlib requires an exact match with the discovery issuer, so a token with the scheme-less form
would be **refused** (fail closed), not accepted. Record which form arrives. Google publishes no `end_session_endpoint`,
so sign-out clears BAEC's cookie but does not end the Google session. Record that as expected behavior.

**Trust store (unchanged):** the sign-in client verifies Google's certificate against the host image's Debian system
store (`httpx2` + `truststore`). That store lacks the compromised `emSign ECC Root CA - G3` but still contains emSign
G1, C1, and C3, which certifi 2026.10.23 dropped. This difference is carried as a residual item and is not changed
here. During the live test, record only non-secret metadata: the certificate chain subjects and issuers, the validated
host name, the issuer value, and a successful verification.

## 9. Health and unhealthy-instance handling

Docker marks `web` unhealthy after two failed readiness+liveness checks (60 s apart). Compose does not act on that, so
a systemd timer runs **`baecctl watchdog` every 60 s**:

* **Healthy, starting, missing, or deliberately stopped:** no action.
* **Unhealthy and running:** `docker restart`, at most **3 times per 30 minutes**, each logged at error level
  (`WATCHDOG_RESTART`).
* **Still unhealthy after the budget:** `docker stop` and **hold**, logged at critical level (`WATCHDOG_HELD`). nginx
  then answers 502 instead of serving a known-unsafe instance; there is no restart loop. `restart: unless-stopped`
  keeps it stopped across reboots. The hold clears automatically once an operator has fixed the cause and the
  container is healthy again (`WATCHDOG_RECOVERED`).
* It runs only `docker inspect`, `docker restart`, and `docker stop`, and **never touches the database**.
* Logs: `journalctl -t baec-watchdog`. Alerting beyond the journal (for example the CloudWatch agent to SNS) is not
  part of this plan. The operator checks the journal and `systemctl --failed` during the controlled phase.
* **Must be exercised deliberately on the host** (for example, revoke the app role's `CONNECT` on a disposable
  database copy, or point readiness at a stopped database) before acceptance.

Backups: `baec-backup.timer` runs daily at 06:00 UTC, before the RDS backup window. The sequence is dump, checksum
verification, then upload to S3 with SSE-KMS and a SHA-256 checksum on each object (`daily/`, plus `monthly/` on the
1st). The plaintext staging copy is always removed. The job logs `BACKUP_OK` or `BACKUP_FAILED step=…` with no
credential, and a failure fails the unit. The timer is enabled only after the backup credential exists.

## 10. Validation performed (offline, no AWS access)

| Check | Result |
|---|---|
| `terraform fmt -check` (1.9.8) | clean |
| `terraform init -backend=false`, `providers lock` (linux_arm64, linux_amd64, darwin_arm64) | hashicorp/aws 5.100.0, signed |
| `terraform validate` | valid |
| `terraform plan` with dummy credentials, in a container with **no network**, against `terraform.tfvars.example` | **54 to add, 0 to change, 0 to destroy**. One expected warning: the arm64 AMI `check` could not reach AWS |
| Plan review | RDS `publicly_accessible=false`, encrypted, `force_ssl=1`, TLS 1.2+, RDS-managed password (`password` null), deletion protection. Ingress: 443 from the /32 only, 80 from anywhere, 5432 from the host security group only, no 22. Host t4g.medium, IMDSv2 hop limit 1, encrypted root, no key pair. Both buckets versioned with public access blocked; Object Lock governance 30 days. No secret value or secret version anywhere in the plan |
| Trivy 0.58.1 `config` (misconfiguration) | 2 critical, 1 high, 2 medium, 9 low (8 distinct). Each assessed below; none applied blindly |
| ShellCheck 0.10.0 (`baecctl.sh`, warning level) | clean |
| `deploy/tests/test_aws_controlled.py` (Ubuntu 24.04 container, fake `docker` and `aws`; Terraform renders and parses cloud-init) | see the gate in the Phase 6 report |

Trivy configuration findings and their dispositions:

| Finding | Disposition |
|---|---|
| CRITICAL `aws-vpc-no-public-egress-sgr` ×2 (host egress 443, 80 to `0.0.0.0/0`) | **Accepted.** Outbound only. 443 reaches Google OIDC, Let's Encrypt, Session Manager, Secrets Manager, S3, and the RDS CA truststore, whose addresses are not fixed. 80 reaches Ubuntu's signed package mirrors. Tightening it would mean interface endpoints (cost) and a proxy; recorded as a later hardening option |
| HIGH `AVD-AWS-0132` (releases bucket not on a customer-managed key) | **Accepted.** It holds images and source that are no secret; integrity is SHA-256 plus the pinned image IDs. The backups bucket uses a customer-managed key |
| MEDIUM `AVD-AWS-0176` (RDS IAM authentication disabled) | **Accepted.** BAEC's verified role model uses passwords with `verify-full` (Phase 5); IAM authentication would be a design change |
| MEDIUM VPC flow logs not enabled | **Accepted for the controlled phase.** It is low-traffic with ingress restricted. A later hardening option |
| LOW Secrets Manager default key; S3 access logging; RDS Performance Insights | **Accepted.** AWS-managed encryption at rest; access logging and Performance Insights add cost and data capture without a current use |

## 11. What the operator must supply

1. An AWS account and an IAM Identity Center user or role with `iam/deployer-policy.json` and
   `iam/operator-policy.json` (account ID and region filled in), plus MFA. **Never the root user.**
2. Explicit authorization to create the billable resources in §1.
3. `public_hostname` (a domain the operator owns) and the ability to add one A record.
4. `https_ingress_cidrs`: the operator's public IPv4 address(es) as /32.
5. `host_ami_id`: Ubuntu 24.04 arm64 in the region (the lookup command is in `variables.tf`).
6. A Google Cloud project for the OAuth client, and the one test user's Google account.
7. An email address for Let's Encrypt.
8. Confirmation of the region (default `us-east-1`).

## 12. What happens on approval (in order; each step stops on failure)

1. `terraform init` and `terraform plan -out=baec.tfplan` with the operator's deployer session. Review: 54 resources,
   no public RDS, ingress as in §1. Then `terraform apply baec.tfplan`.
2. Operator: create the DNS A record; register the Google client with the exact redirect URI.
3. Upload release artifacts to `s3://<releases_bucket>/releases/e626634/`: `baec-{web,proxy,ops}.tar` (`docker save`
   of the scanned images), `source.tar` (`git archive e626634`), and `SHA256SUMS`.
4. Session Manager into the host. Confirm `uname -m` = `aarch64`, then `baecctl install-release` (checksums, then the
   approved image IDs and `linux/arm64`; anything else is refused).
5. **Rescan** the three image tarballs with Trivy 0.58.1 and a fresh database. Record the database date. Stop on any
   applicable critical.
6. Bootstrap the roles as the RDS administrator (§6); write all six secrets.
7. `baecctl fetch-secrets`, then `baecctl verify-db-tls`.
8. `baecctl operator-job migrate` (twice: the second run must report "nothing applied").
9. `certbot certonly …` (§7).
10. `baecctl up`, then the live verification of Phase 6 §4 onward: HTTPS, cookies, HSTS, OIDC, provisioning of one
    HUMAN user, the synthetic workflow, revocation and deactivation, the watchdog, connection limits, the secret
    boundary, a backup, and a restore drill into a separate database. No real data at any point.
11. `systemctl enable --now baec-backup.timer`.

Teardown (when the controlled phase ends): disable deletion protection deliberately, then `terraform destroy`. A final
RDS snapshot is kept. Object-locked backups remain until their retention ends.
