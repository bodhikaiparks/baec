#!/bin/sh
# Local PostgreSQL for BAEC integration tests (Docker). Test credentials only; never a production database.
#
#   scripts/postgres_test_db.sh up      start an empty cluster (generates per-machine test passwords and a test TLS
#                                       CA and server certificate on first use)
#   scripts/postgres_test_db.sh env     print the connection variables the integration tests read
#   scripts/postgres_test_db.sh test    start it if needed and run the PostgreSQL integration suite
#   scripts/postgres_test_db.sh down    stop it and discard all data (tmpfs)
#   scripts/postgres_test_db.sh reset   down, then up
#
# PYTHON selects the interpreter for `test` (default: .venv-postgres/bin/python, which has psycopg installed).
#
# V0.4 Phase 5: the cluster accepts TLS only, and every URL printed here requires sslmode=verify-full against the
# per-machine test CA in infra/postgres-test/certs/ (gitignored; test-only, never a production certificate).
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$HERE/infra/postgres-test"
ENV_FILE="$DIR/.env"
CERTS="$DIR/certs"
COMPOSE="docker compose --project-directory $DIR -f $DIR/docker-compose.yml"

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    umask 077
    {
      echo "BAEC_TEST_POSTGRES_PORT=55439"
      echo "BAEC_TEST_BOOTSTRAP_PASSWORD=$(openssl rand -hex 16)"
      echo "BAEC_TEST_MIGRATOR_PASSWORD=$(openssl rand -hex 16)"
      echo "BAEC_TEST_APP_PASSWORD=$(openssl rand -hex 16)"
    } > "$ENV_FILE"
  fi
  # V0.4 Phase 4 and 5 roles, added to an older .env without changing its existing passwords
  for name in BAEC_TEST_AUTH_PASSWORD BAEC_TEST_PROVISIONER_PASSWORD BAEC_TEST_BACKUP_PASSWORD; do
    if ! grep -q "^$name=" "$ENV_FILE"; then
      (umask 077; echo "$name=$(openssl rand -hex 16)" >> "$ENV_FILE")
    fi
  done
}

# A test CA and a server certificate for every name the test cluster is reached by: 127.0.0.1 and localhost from this
# machine, and postgres / baec-postgres-test from containers on its Docker network (deploy/tests smoke test).
ensure_certs() {
  if [ ! -f "$CERTS/server.crt" ]; then
    (
      umask 077
      mkdir -p "$CERTS"
      openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
        -subj "/CN=BAEC disposable test CA" -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" 2>/dev/null
      openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj "/CN=localhost" \
        -keyout "$CERTS/server.key" -out "$CERTS/server.csr" 2>/dev/null
      printf 'subjectAltName=DNS:localhost,DNS:postgres,DNS:baec-postgres-test,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' \
        > "$CERTS/server.ext"
      openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
        -days 825 -extfile "$CERTS/server.ext" -out "$CERTS/server.crt" 2>/dev/null
      rm -f "$CERTS/server.csr" "$CERTS/server.ext"
    )
    # The container copies these and fixes ownership itself; it only needs to read them through the mount.
    chmod 644 "$CERTS/ca.crt" "$CERTS/server.crt" "$CERTS/server.key"
  fi
}

print_env() {
  ensure_env
  ensure_certs
  . "$ENV_FILE"
  base="127.0.0.1:$BAEC_TEST_POSTGRES_PORT/baec_test?sslmode=verify-full&sslrootcert=$CERTS/ca.crt"
  echo "export BAEC_TEST_POSTGRES_ADMIN_URL='postgresql://baec_bootstrap:$BAEC_TEST_BOOTSTRAP_PASSWORD@$base'"
  echo "export BAEC_TEST_POSTGRES_MIGRATOR_URL='postgresql://baec_migrator:$BAEC_TEST_MIGRATOR_PASSWORD@$base'"
  echo "export BAEC_TEST_POSTGRES_APP_URL='postgresql://baec_app:$BAEC_TEST_APP_PASSWORD@$base'"
  echo "export BAEC_TEST_POSTGRES_AUTH_URL='postgresql://baec_auth_lookup:$BAEC_TEST_AUTH_PASSWORD@$base'"
  echo "export BAEC_TEST_POSTGRES_PROVISIONER_URL='postgresql://baec_provisioner:$BAEC_TEST_PROVISIONER_PASSWORD@$base'"
  echo "export BAEC_TEST_POSTGRES_BACKUP_URL='postgresql://baec_backup:$BAEC_TEST_BACKUP_PASSWORD@$base'"
}

up() {
  ensure_env
  ensure_certs
  $COMPOSE up -d --wait
}

case "${1:-}" in
  up) up ;;
  down) ensure_env; ensure_certs; $COMPOSE down --volumes ;;
  reset) ensure_env; ensure_certs; $COMPOSE down --volumes; up ;;
  env) print_env ;;
  test)
    up
    eval "$(print_env)"
    cd "$HERE"
    "${PYTHON:-$HERE/.venv-postgres/bin/python}" -m pytest baec_console/tests_postgres -q -p no:cacheprovider
    ;;
  *) echo "usage: $0 up|env|test|down|reset" >&2; exit 2 ;;
esac
