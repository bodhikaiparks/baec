#!/usr/bin/env bash
# baecctl: host operations for the BAEC controlled deployment (V0.4 Phase 6; IMPLEMENTATION). Installed by cloud-init
# as /usr/local/sbin/baecctl, run as root. See infra/aws-controlled/README.md.
#
#   baecctl fetch-secrets          web + backup secrets from Secrets Manager into files (runbook §2.2); RDS CA bundle
#   baecctl install-release        download, checksum, and load the approved images and source; refuse other IDs
#   baecctl install-certificate    certbot deploy hook: copy the issued certificate to the proxy's TLS directory
#   baecctl up                     check image IDs, then start proxy + web (docker compose up --wait)
#   baecctl operator-job <job> ... migrate | provision <args>, with the operator's own short-lived AWS session
#   baecctl backup                 logical dump -> verify checksum -> encrypted upload to S3 -> remove staging copy
#   baecctl verify-db-tls          RDS refuses plaintext, a wrong CA, and a wrong host name; accepts verify-full
#   baecctl watchdog               restart an unhealthy web container, within a budget; then stop it and hold
#
# Logging: one line per event on stdout with a syslog priority prefix (<3> error ... <6> info), which journald
# records under the unit's identifier. No command prints a secret value, a database URL, or a credential.
set -euo pipefail

HOST_CONF=${BAEC_HOST_CONF:-/etc/baec/host.conf}
COMPOSE_ENV=${BAEC_COMPOSE_ENV:-/etc/baec/compose.env}
SECRETS_DIR=/etc/baec/secrets
STAGING_DIR=${BAEC_STAGING_DIR:-/var/lib/baec/backup-staging}
INSTALL_ROOT=/opt/baec
DOCKER=${BAEC_DOCKER:-docker}
AWS=${BAEC_AWS:-aws}
WEB_UID=10001
OPS_UID=70

log() { printf '<%s>%s\n' "$1" "$2"; }  # 3 error, 4 warning, 5 notice, 6 info
die() { log 3 "$1"; exit 1; }

load_conf() {
  [[ -r $HOST_CONF ]] || die "missing $HOST_CONF"
  # shellcheck disable=SC1090
  source "$HOST_CONF"
  [[ $BAEC_RELEASE != 86f59a1* ]] || die "release 86f59a1 must never be deployed"
}

compose() {
  "$DOCKER" compose --project-directory "$INSTALL_ROOT/current/deploy" --env-file "$COMPOSE_ENV" "$@"
}

# Write one secret value from stdin to a file readable only by one uid. The value never touches argv or the log.
write_secret_file() {
  local target=$1 owner=$2 temporary
  temporary=$(mktemp "$(dirname "$target")/.incoming.XXXXXX")
  cat > "$temporary"
  [[ -s $temporary ]] || { rm -f "$temporary"; die "secret for $(basename "$target") is empty"; }
  chown "$owner:$owner" "$temporary"
  chmod 0400 "$temporary"
  mv -f "$temporary" "$target"
}

secret_value() {  # secret name -> value on stdout (to a pipe only)
  "$AWS" secretsmanager get-secret-value --region "$AWS_REGION_NAME" --secret-id "$1" --query SecretString --output text
}

fetch_ca_bundle() {
  local target=$1 temporary
  temporary=$(mktemp)
  curl --fail --silent --show-error --proto '=https' --tlsv1.2 --max-time 30 \
    "https://truststore.pki.rds.amazonaws.com/$AWS_REGION_NAME/$AWS_REGION_NAME-bundle.pem" -o "$temporary"
  grep -q 'BEGIN CERTIFICATE' "$temporary" && openssl crl2pkcs7 -nocrl -certfile "$temporary" >/dev/null \
    || { rm -f "$temporary"; die "RDS CA bundle did not parse as certificates"; }
  chmod 0444 "$temporary"
  mv -f "$temporary" "$target"
  log 6 "RDS CA bundle installed: sha256 $(sha256sum "$target" | cut -c1-64)"
}

cmd_fetch_secrets() {
  load_conf
  install -d -m 0700 -o root -g root "$SECRETS_DIR"
  secret_value "$BAEC_ENVIRONMENT/web/app-database-url" | write_secret_file "$SECRETS_DIR/baec_app_database_url" "$WEB_UID"
  secret_value "$BAEC_ENVIRONMENT/web/auth-database-url" | write_secret_file "$SECRETS_DIR/baec_auth_database_url" "$WEB_UID"
  secret_value "$BAEC_ENVIRONMENT/web/streamlit-auth-toml" | write_secret_file "$SECRETS_DIR/baec_streamlit_auth.toml" "$WEB_UID"
  secret_value "$BAEC_ENVIRONMENT/backup/backup-database-url" | write_secret_file "$SECRETS_DIR/baec_backup_database_url" "$OPS_UID"
  fetch_ca_bundle "$SECRETS_DIR/baec_postgres_ca.crt"
  log 6 "web and backup secrets installed (names only): baec_app_database_url baec_auth_database_url baec_streamlit_auth.toml baec_backup_database_url"
}

expected_image_id() {
  case $1 in web) echo "$BAEC_IMAGE_WEB" ;; proxy) echo "$BAEC_IMAGE_PROXY" ;; ops) echo "$BAEC_IMAGE_OPS" ;; esac
}

cmd_verify_images() {
  load_conf
  local name actual
  for name in web proxy ops; do
    actual=$("$DOCKER" image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}}' "baec-$name:$BAEC_RELEASE") \
      || die "image baec-$name:$BAEC_RELEASE is not loaded"
    [[ $actual == "$(expected_image_id "$name") linux/arm64" ]] || die "baec-$name:$BAEC_RELEASE is not the approved linux/arm64 image"
  done
  log 6 "images verified: release $BAEC_RELEASE, web proxy ops are the approved linux/arm64 image IDs"
}

cmd_install_release() {
  load_conf
  local work name
  work=$(mktemp -d)
  trap 'rm -rf "$work"' RETURN
  for name in SHA256SUMS baec-web.tar baec-proxy.tar baec-ops.tar source.tar; do
    "$AWS" s3 cp --region "$AWS_REGION_NAME" --only-show-errors \
      "s3://$BAEC_RELEASES_BUCKET/releases/$BAEC_RELEASE/$name" "$work/$name"
  done
  (cd "$work" && sha256sum --check --strict --quiet SHA256SUMS >/dev/null 2>&1) || die "release artifacts do not match SHA256SUMS"
  for name in web proxy ops; do
    "$DOCKER" load --quiet --input "$work/baec-$name.tar" >/dev/null
  done
  cmd_verify_images
  install -d -m 0755 "$INSTALL_ROOT/$BAEC_RELEASE"
  tar -x -f "$work/source.tar" -C "$INSTALL_ROOT/$BAEC_RELEASE" deploy
  ln -sfn "$INSTALL_ROOT/$BAEC_RELEASE" "$INSTALL_ROOT/current"
  log 5 "release $BAEC_RELEASE installed"
}

# certbot --deploy-hook: RENEWED_LINEAGE is /etc/letsencrypt/live/<host>. The key stays on this host (0400, root).
cmd_install_certificate() {
  local lineage=${RENEWED_LINEAGE:-} tls=/etc/baec/tls
  [[ -n $lineage && -r $lineage/fullchain.pem && -r $lineage/privkey.pem ]] || die "no certificate lineage from certbot"
  install -d -m 0700 -o root -g root "$tls"
  install -m 0444 -o root -g root "$lineage/fullchain.pem" "$tls/fullchain.pem"
  install -m 0400 -o root -g root "$lineage/privkey.pem" "$tls/privkey.pem"
  log 5 "certificate installed: $(openssl x509 -in "$tls/fullchain.pem" -noout -subject -enddate | tr '\n' ' ')"
}

cmd_up() {
  cmd_verify_images
  compose up -d --wait
  log 5 "proxy and web started; web is healthy (readiness and liveness)"
}

# Operator jobs: the credential is read with the OPERATOR's own short-lived AWS session (exported into this shell
# only), never with the host role, which is denied these secrets. It exists for the job's duration only, on tmpfs.
cmd_operator_job() {
  load_conf
  local job=${1:-} secret file caller scratch
  shift || true
  case $job in
    migrate) secret=operator/migration-database-url; file=baec_migration_database_url ;;
    provision) secret=operator/provisioning-database-url; file=baec_provisioning_database_url ;;
    *) die "usage: baecctl operator-job migrate | provision <provision arguments>" ;;
  esac
  caller=$("$AWS" sts get-caller-identity --query Arn --output text) || die "no operator AWS session in this shell"
  [[ $caller != *":assumed-role/$BAEC_ENVIRONMENT-host/"* ]] \
    || die "operator jobs must use the operator's session, not the host role"
  scratch=$(mktemp -d /run/baec-operator.XXXXXX)
  trap 'rm -rf "$scratch"' EXIT
  chmod 0700 "$scratch"
  cp "$SECRETS_DIR/baec_postgres_ca.crt" "$scratch/"
  secret_value "$BAEC_ENVIRONMENT/$secret" | write_secret_file "$scratch/$file" "$WEB_UID"
  if [[ $job == migrate ]]; then
    BAEC_SECRETS_DIR=$scratch compose --profile release run --rm migrate
  else
    BAEC_SECRETS_DIR=$scratch compose --profile ops run --rm provision "$@"
  fi
  log 5 "operator job $job finished; its credential was removed"
}

cmd_backup() {
  load_conf
  local before dump name failed=""
  install -d -m 0700 "$STAGING_DIR"
  if [[ $(id -u) == 0 ]]; then chown "$OPS_UID:$OPS_UID" "$STAGING_DIR"; fi  # the backup container writes as uid 70
  before=$(ls "$STAGING_DIR")
  [[ -z $before ]] || log 4 "staging directory was not empty; leftover files are removed without upload"
  rm -f "$STAGING_DIR"/baec-*
  trap 'rm -f "$STAGING_DIR"/baec-*' EXIT  # the plaintext staging copy never outlives this run
  if ! compose --profile ops run --rm backup >/dev/null; then
    log 3 "BACKUP_FAILED step=dump"
    exit 1
  fi
  dump=$(find "$STAGING_DIR" -maxdepth 1 -name 'baec-*.dump' -print -quit)
  [[ -n $dump ]] || { log 3 "BACKUP_FAILED step=dump reason=no-dump-written"; exit 1; }
  name=$(basename "$dump")
  (cd "$STAGING_DIR" && sha256sum --check --strict --quiet "$name.sha256" >/dev/null 2>&1) \
    || { log 3 "BACKUP_FAILED step=checksum file=$name"; exit 1; }
  for prefix in daily $([[ ${BAEC_DAY_OF_MONTH:-$(date -u +%d)} == 01 ]] && echo monthly); do
    for upload in "$name" "$name.sha256"; do
      if ! "$AWS" s3 cp --region "$AWS_REGION_NAME" --only-show-errors --sse aws:kms --sse-kms-key-id "$BAEC_BACKUPS_KEY_ARN" \
          --checksum-algorithm SHA256 "$STAGING_DIR/$upload" "s3://$BAEC_BACKUPS_BUCKET/$prefix/$upload"; then
        failed="$prefix/$upload"
        break 2
      fi
    done
  done
  if [[ -n $failed ]]; then
    log 3 "BACKUP_FAILED step=upload object=$failed"
    exit 1
  fi
  log 5 "BACKUP_OK file=$name sha256=$(cut -c1-64 "$STAGING_DIR/$name.sha256") bytes=$(stat -c %s "$dump") destination=s3-kms"
}

cmd_verify_db_tls() {
  load_conf
  local work result
  work=$(mktemp -d)
  trap 'rm -rf "$work"' RETURN
  cp "$SECRETS_DIR/baec_postgres_ca.crt" "$work/rds-ca.pem"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 -subj "/CN=wrong CA (probe)" \
    -keyout "$work/wrong.key" -out "$work/wrong-ca.pem" 2>/dev/null
  chmod 0644 "$work"/*.pem
  probe() {  # a role that does not exist and no password: nothing secret is ever sent
    "$DOCKER" run --rm --network bridge -v "$work:/probe:ro" -e PGPASSWORD= --entrypoint psql \
      "baec-ops:$BAEC_RELEASE" "host=$1 port=5432 dbname=postgres user=baec_tls_probe connect_timeout=10 $2" \
      -c 'select 1' 2>&1 || true
  }
  result=$(probe "$BAEC_DB_HOST" "sslmode=disable")
  [[ $result == *"no encryption"* ]] || die "plaintext was not refused by the server"
  log 6 "plaintext connection refused by the server (rds.force_ssl)"
  result=$(probe "$BAEC_DB_HOST" "sslmode=verify-full sslrootcert=/probe/wrong-ca.pem")
  [[ $result == *"certificate verify failed"* ]] || die "a server certificate from an unexpected CA was not refused"
  log 6 "wrong CA refused"
  local address
  address=$(getent ahostsv4 "$BAEC_DB_HOST" | awk 'NR==1{print $1}')
  result=$(probe "$address" "sslmode=verify-full sslrootcert=/probe/rds-ca.pem")
  [[ $result == *"does not match host name"* ]] || die "a connection by another name was not refused"
  log 6 "wrong host name refused"
  result=$(probe "$BAEC_DB_HOST" "sslmode=verify-full sslrootcert=/probe/rds-ca.pem")
  [[ $result == *"password"* && $result != *"certificate"* && $result != *"SSL"* ]] \
    || die "verify-full did not complete the TLS handshake with the RDS CA and host name"
  log 5 "DB TLS verified: plaintext, wrong CA, and wrong host name refused; verify-full handshake succeeds"
}

# --- watchdog ----------------------------------------------------------------------------------------------------------
# Acts only on a running web container that Docker reports unhealthy (its health check is readiness + liveness, two
# failures in a row). Restarts it at most BAEC_WATCHDOG_MAX_RESTARTS times per BAEC_WATCHDOG_WINDOW_SECONDS; after
# that it stops the container, so nginx answers 502 rather than serving a known-unsafe instance, and holds until the
# web container is healthy again (an operator fixed and restarted it). It only runs docker inspect/restart/stop:
# it never touches the database. A missing or deliberately stopped container is left alone.
cmd_watchdog() {
  local container=${BAEC_WEB_CONTAINER:-baec-web-1}
  local state=${BAEC_WATCHDOG_STATE:-/var/lib/baec-watchdog}
  local max=${BAEC_WATCHDOG_MAX_RESTARTS:-3} window=${BAEC_WATCHDOG_WINDOW_SECONDS:-1800}
  local now=${BAEC_WATCHDOG_NOW:-$(date +%s)}
  local status running recent=() stamp
  mkdir -p "$state"
  touch "$state/restarts"
  read -r status running < <("$DOCKER" inspect --format \
    '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.State.Running}}' "$container" 2>/dev/null) \
    || return 0
  if [[ -e $state/held ]]; then
    if [[ $status == healthy ]]; then
      rm -f "$state/held"
      : > "$state/restarts"
      log 5 "WATCHDOG_RECOVERED container=$container: healthy again; hold cleared"
    fi
    return 0
  fi
  [[ $status == unhealthy && $running == true ]] || return 0
  while read -r stamp; do
    [[ -n $stamp ]] && (( now - stamp < window )) && recent+=("$stamp")
  done < "$state/restarts"
  if (( ${#recent[@]} < max )); then
    "$DOCKER" restart "$container" >/dev/null
    printf '%s\n' ${recent[@]+"${recent[@]}"} "$now" > "$state/restarts"
    log 3 "WATCHDOG_RESTART container=$container: unhealthy; restarted ($(( ${#recent[@]} + 1 ))/$max in ${window}s)"
  else
    "$DOCKER" stop "$container" >/dev/null
    touch "$state/held"
    log 2 "WATCHDOG_HELD container=$container: still unhealthy after $max restarts in ${window}s; stopped, not serving; operator action required"
  fi
}

main() {
  local command=${1:-}
  shift || true
  case $command in
    fetch-secrets) cmd_fetch_secrets ;;
    install-release) cmd_install_release ;;
    install-certificate) cmd_install_certificate ;;
    verify-images) cmd_verify_images ;;
    up) cmd_up ;;
    operator-job) cmd_operator_job "$@" ;;
    backup) cmd_backup ;;
    verify-db-tls) cmd_verify_db_tls ;;
    watchdog) cmd_watchdog ;;
    *) die "usage: baecctl fetch-secrets | install-release | install-certificate | verify-images | up | operator-job | backup | verify-db-tls | watchdog" ;;
  esac
}

main "$@"
