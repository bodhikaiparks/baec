#!/bin/sh
# Bootstrap (runs once, as the container superuser, when the empty test cluster is created).
#
# This is the DBA step a real deployment performs by hand: create the BAEC roles and the database grants.
# No role is a superuser, and only the backup role can bypass row-level security. The application never runs this.
#
#   baec_migrator     owns the baec schema; runs ordered migrations (tables, constraints, triggers, RLS policies, grants)
#   baec_app          the runtime role; DML only on tenant tables, subject to RLS, owns nothing
#   baec_auth_lookup  the runtime sign-in lookup (V0.4 Phase 4); reads one exact sign-in link, nothing else
#   baec_provisioner  the operator's provisioning tool (V0.4 Phase 4); never used by the running application
#   baec_backup       the backup job (V0.4 Phase 5): reads every row of every organization (BYPASSRLS plus
#                     pg_read_all_data) so a dump is complete; writes nothing; never used by the running application
#
# Every login role has a connection limit (V0.4 Phase 5), so no process, however configured, can exhaust the server.
# The production gate refuses an application or sign-in lookup role without one. These limits suit the test suite,
# which opens many short-lived pools; production values are in docs/PRODUCTION_RUNBOOK.md.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v migrator_password="$BAEC_TEST_MIGRATOR_PASSWORD" -v app_password="$BAEC_TEST_APP_PASSWORD" \
  -v auth_password="$BAEC_TEST_AUTH_PASSWORD" -v provisioner_password="$BAEC_TEST_PROVISIONER_PASSWORD" \
  -v backup_password="$BAEC_TEST_BACKUP_PASSWORD" <<'SQL'
CREATE ROLE baec_migrator LOGIN PASSWORD :'migrator_password' CONNECTION LIMIT 8
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
CREATE ROLE baec_app LOGIN PASSWORD :'app_password' CONNECTION LIMIT 64
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
CREATE ROLE baec_auth_lookup LOGIN PASSWORD :'auth_password' CONNECTION LIMIT 24
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
CREATE ROLE baec_provisioner LOGIN PASSWORD :'provisioner_password' CONNECTION LIMIT 8
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
CREATE ROLE baec_backup LOGIN PASSWORD :'backup_password' CONNECTION LIMIT 4
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS INHERIT;
GRANT pg_read_all_data TO baec_backup;

REVOKE ALL ON DATABASE baec_test FROM PUBLIC;
GRANT CONNECT, CREATE ON DATABASE baec_test TO baec_migrator;
GRANT CONNECT ON DATABASE baec_test TO baec_app, baec_auth_lookup, baec_provisioner, baec_backup;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
