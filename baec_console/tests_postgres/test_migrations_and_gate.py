"""V0.4 Phase 3: ordered migrations, and the production database checks that refuse an unsafe configuration."""

from __future__ import annotations

import os
import secrets
import uuid
from contextlib import closing
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

import pytest

from baec_console import deployment, production, storage as storage_selection
from baec_console.postgres import migrate
from baec_console.postgres.database import DatabaseConfigError
from baec_console.postgres.gate import (
    ENGINE1_TABLES,
    ENGINE2_SCHEMA_MISMATCH,
    ENGINE2_TABLES,
    TENANT_TABLES,
    DatabaseRefused,
    check_database,
)

from .conftest import auth_section, production_environ, psycopg


def _with(url: str, *, user: str | None = None, password: str | None = None, database: str | None = None,
          port: int | None = None) -> str:
    parts = urlsplit(url)
    user = user or parts.username
    password = password if password is not None else parts.password
    netloc = f"{user}:{password}@{parts.hostname}:{port or parts.port}"
    # The query (sslmode=verify-full and the test CA, V0.4 Phase 5) is kept: every connection uses verified TLS.
    return urlunsplit((parts.scheme, netloc, f"/{database}" if database else parts.path, parts.query, ""))


# --- migrations -------------------------------------------------------------------------------------------------------


def test_migrations_are_recorded_in_order_and_rerunning_applies_nothing(migrated):
    assert migrate.apply_migrations(migrated["migrator"]) == []
    with psycopg.connect(migrated["migrator"]) as db:
        assert migrate.recorded(db) == migrate.expected_versions()
        assert [v for v, _ in migrate.recorded(db)] == [1, 2, 3, 4, 5, 6]


def test_a_changed_or_unknown_migration_stops_the_run(migrated, monkeypatch):
    original = migrate.migrations()
    monkeypatch.setattr(migrate, "migrations", lambda: [replace(original[0], sql=original[0].sql + "\n-- edited"),
                                                       *original[1:]])
    with pytest.raises(migrate.MigrationError, match="migration 1 has changed"):
        migrate.apply_migrations(migrated["migrator"])
    monkeypatch.setattr(migrate, "migrations", lambda: original[:1])
    with pytest.raises(migrate.MigrationError, match="records migration 2, which this code does not have"):
        migrate.apply_migrations(migrated["migrator"])


def test_the_application_role_cannot_run_migrations_or_create_objects(migrated):
    with pytest.raises(migrate.MigrationError, match="never as the application role"):
        migrate.apply_migrations(migrated["app"])
    with psycopg.connect(migrated["app"], autocommit=True) as app:
        for sql in ("CREATE TABLE baec.x (a int)", "CREATE TABLE public.x (a int)", "CREATE SCHEMA rogue",
                    "ALTER TABLE baec.events DISABLE ROW LEVEL SECURITY", "DROP POLICY tenant_isolation ON baec.events",
                    "INSERT INTO baec_migrations.schema_migrations VALUES (99, 'x', 'x', now())",
                    "SELECT * FROM baec.operator_log"):
            with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedObject)):
                app.execute(sql)


def test_the_schema_enforces_its_constraints_in_the_database(migrated):
    """CHECK, NOT NULL, and JSON constraints hold without any Python validation (no organization needed: each
    statement is refused before row-level security would matter, or is checked under the test superuser)."""
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute("BEGIN")
        admin.execute("INSERT INTO baec.organizations (organization_id, name, created_at) VALUES ('org-c', 'C', now())")
        admin.execute("INSERT INTO baec.users (user_id, organization_id, display_name, created_at, active, kind) "
                      "VALUES ('user-c', 'org-c', 'C', now(), true, 'HUMAN')")
        admin.execute("INSERT INTO baec.account_ownership (organization_id, workspace, account_id, recorded_at, "
                      "recorded_by_user_id, basis) VALUES ('org-c', 'local', 'ACC-C', now(), 'user-c', 'CREATED')")
        refused = {
            "unknown user kind": "INSERT INTO baec.users VALUES ('u2', 'org-c', 'D', now(), true, 'ADMIN')",
            "blank organization id": "INSERT INTO baec.organizations (organization_id, name, created_at) "
                                     "VALUES ('  ', 'x', now())",
            "unknown workspace": "INSERT INTO baec.account_ownership (organization_id, workspace, account_id, "
                                 "recorded_at, recorded_by_user_id, basis) VALUES ('org-c', 'shared', 'A2', now(), "
                                 "'user-c', 'CREATED')",
            "unknown ownership basis": "INSERT INTO baec.account_ownership (organization_id, workspace, account_id, "
                                       "recorded_at, recorded_by_user_id, basis) VALUES ('org-c', 'local', 'A3', "
                                       "now(), 'user-c', 'ADOPTED')",
            "missing buyer statement": "INSERT INTO baec.interaction_meta (organization_id, workspace, "
                                       "interaction_id, account_id, recorded_at, recorded_by) VALUES ('org-c', "
                                       "'local', 'I', 'ACC-C', now(), 'x')",
            "draft form that is not JSON": "INSERT INTO baec.condition_drafts (organization_id, workspace, draft_id, "
                                           "account_id, interaction_id, form_json, captured_at, captured_by) VALUES "
                                           "('org-c', 'local', 'D', 'ACC-C', 'I', 'not json', now(), 'x')",
            "provenance claiming a model": "INSERT INTO baec.preparation_provenance VALUES ('org-c', 'local', 'P', "
                                           "'R', 'C', 'DETERMINISTIC_CONSOLE_TEMPLATE_V1', 'AI_INFERENCE', "
                                           "'E3-ORIGIN-COMPAT-V1', true)",
            "done task without completion": "INSERT INTO baec.crm_tasks (organization_id, workspace, task_id, "
                                            "account_id, title, status, created_at, created_by) VALUES ('org-c', "
                                            "'local', 'T', 'ACC-C', 'x', 'DONE', now(), 'x')",
            "unknown task priority": "INSERT INTO baec.crm_tasks (organization_id, workspace, task_id, account_id, "
                                     "title, priority, created_at, created_by) VALUES ('org-c', 'local', 'T', "
                                     "'ACC-C', 'x', 'URGENT', now(), 'x')",
            "duplicate display name": "INSERT INTO baec.users VALUES ('u3', 'org-c', 'C', now(), true, 'HUMAN')",
            "truncate (even for a superuser)": "TRUNCATE baec.events",
        }
        for label, sql in refused.items():
            admin.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.IntegrityError):
                admin.execute(sql)
            admin.execute("ROLLBACK TO SAVEPOINT s")
        admin.execute("ROLLBACK")


# --- production database checks ---------------------------------------------------------------------------------------


def test_the_application_role_passes_every_check(migrated):
    report = check_database(migrated["app"], migrated["auth"])
    assert report.role == "baec_app" and report.schema_versions == (1, 2, 3, 4, 5, 6)
    assert report.tenant_tables == (TENANT_TABLES + tuple(f"baec_engine1.{t}" for t in ENGINE1_TABLES)
                                    + tuple(f"baec_engine2.{t}" for t in ENGINE2_TABLES))


@pytest.mark.parametrize("role,expected", [
    ("admin", ["is a superuser", "can bypass row-level security"]),
    ("migrator", ["owns BAEC schema objects", "can create objects in schema baec"]),
])
def test_a_privileged_role_is_refused(migrated, role, expected):
    with pytest.raises(DatabaseRefused) as refused:
        check_database(migrated[role], migrated["auth"])
    for text in expected:
        assert any(text in f for f in refused.value.failures), (text, refused.value.failures)


@pytest.fixture
def temporary_role(migrated):
    """Login roles created for one test by the test superuser, with random passwords, dropped afterwards."""
    created = []

    def make(*options: str, member_of: str | None = None) -> str:
        name, password = f"baec_gate_{uuid.uuid4().hex[:8]}", secrets.token_hex(12)
        with psycopg.connect(migrated["admin"], autocommit=True) as admin:
            admin.execute(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' {' '.join(options)}")
            admin.execute(f"GRANT CONNECT ON DATABASE baec_test TO {name}")
            admin.execute(f"GRANT baec_app TO {name}")  # the application's privileges, plus whatever else
            if member_of:
                admin.execute(f"GRANT {member_of} TO {name}")
        created.append(name)
        return _with(migrated["app"], user=name, password=password)

    yield make
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        for name in created:
            admin.execute(f"DROP OWNED BY {name}")
            admin.execute(f"DROP ROLE {name}")


def test_a_role_that_can_bypass_rls_directly_or_through_membership_is_refused(temporary_role, migrated):
    with pytest.raises(DatabaseRefused, match="can bypass row-level security"):
        check_database(temporary_role("NOSUPERUSER", "BYPASSRLS"), migrated["auth"])
    with pytest.raises(DatabaseRefused, match="member of a superuser or RLS-bypassing role"):
        check_database(temporary_role("NOSUPERUSER", "NOBYPASSRLS", member_of="baec_bootstrap"), migrated["auth"])


@pytest.fixture
def scratch_database(migrated):
    """A separate, freshly migrated database the test may make unsafe. Dropped afterwards."""
    name = f"baec_gate_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
        admin.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        admin.execute(f"GRANT CONNECT, CREATE ON DATABASE {name} TO baec_migrator")
        admin.execute(f"GRANT CONNECT ON DATABASE {name} TO baec_app, baec_auth_lookup, baec_provisioner")
    with psycopg.connect(_with(migrated["admin"], database=name), autocommit=True) as admin:
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    urls = {k: _with(v, database=name) for k, v in migrated.items()}
    yield urls
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute(f"DROP DATABASE {name} WITH (FORCE)")


def test_an_unmigrated_database_is_refused(scratch_database):
    with pytest.raises(DatabaseRefused, match="not at the expected migration version"):
        check_database(scratch_database["app"], scratch_database["auth"])


@pytest.mark.parametrize("sabotage,expected", [
    ("ALTER TABLE baec.crm_tasks NO FORCE ROW LEVEL SECURITY", "not enabled and forced on crm_tasks"),
    ("ALTER TABLE baec.events DISABLE ROW LEVEL SECURITY", "not enabled and forced on events"),
    ("DROP POLICY tenant_boundary ON baec.attributions", "policies on attributions"),
    ("CREATE POLICY open_read ON baec.users FOR SELECT USING (true)", "policies on users"),
    ("GRANT DELETE ON baec.crm_contacts TO baec_app", "has DELETE on crm_contacts"),
    ("GRANT TRUNCATE ON baec.events TO baec_app", "has TRUNCATE on events"),
    ("GRANT CREATE ON SCHEMA baec TO baec_app", "can create objects in schema baec"),
    ("GRANT SELECT ON baec.operator_log TO baec_app", "can read the operator log"),
    ("CREATE TABLE baec.shadow (organization_id text)", "tenant tables in schema baec differ"),
    # Engine 2 (migration 0003)
    ("ALTER TABLE baec_engine2.observations NO FORCE ROW LEVEL SECURITY", "not enabled and forced on "
                                                                          "baec_engine2.observations"),
    ("DROP POLICY workspace_scope ON baec_engine2.reviews", "policies on baec_engine2.reviews"),
    ("CREATE POLICY open_read ON baec_engine2.audit_events FOR SELECT USING (true)",
     "policies on baec_engine2.audit_events"),
    ("GRANT DELETE ON baec_engine2.supersessions TO baec_app", "has DELETE on baec_engine2.supersessions"),
    ("GRANT TRUNCATE ON baec_engine2.audit_events TO baec_app", "has TRUNCATE on baec_engine2.audit_events"),
    ("GRANT INSERT ON baec_engine2.engine2_meta TO baec_app", "has INSERT on baec_engine2.engine2_meta"),
    ("GRANT CREATE ON SCHEMA baec_engine2 TO baec_app", "can create objects in schema baec_engine2"),
    ("CREATE TABLE baec_engine2.shadow (organization_id text)", "tenant tables in schema baec_engine2 differ"),
    # Engine 1 (migration 0004)
    ("ALTER TABLE baec_engine1.baec_records NO FORCE ROW LEVEL SECURITY", "not enabled and forced on "
                                                                          "baec_engine1.baec_records"),
    ("ALTER TABLE baec_engine1.accounts DISABLE ROW LEVEL SECURITY", "not enabled and forced on baec_engine1.accounts"),
    ("DROP POLICY workspace_scope ON baec_engine1.interactions", "policies on baec_engine1.interactions"),
    ("CREATE POLICY open_read ON baec_engine1.human_authorizations FOR SELECT USING (true)",
     "policies on baec_engine1.human_authorizations"),
    ("GRANT DELETE ON baec_engine1.account_state_transitions TO baec_app",
     "has DELETE on baec_engine1.account_state_transitions"),
    ("GRANT TRUNCATE ON baec_engine1.interaction_evidence TO baec_app",
     "has TRUNCATE on baec_engine1.interaction_evidence"),
    ("GRANT CREATE ON SCHEMA baec_engine1 TO baec_app", "can create objects in schema baec_engine1"),
    ("CREATE TABLE baec_engine1.shadow (organization_id text)", "tenant tables in schema baec_engine1 differ"),
])
def test_an_unsafe_schema_or_grant_is_refused_and_never_repaired(scratch_database, sabotage, expected):
    assert migrate.apply_migrations(scratch_database["migrator"]) == [1, 2, 3, 4, 5, 6]
    check_database(scratch_database["app"], scratch_database["auth"])
    with psycopg.connect(scratch_database["migrator"], autocommit=True) as migrator:
        migrator.execute(sabotage)
    for _ in range(2):  # refused every time: the check changes nothing
        with pytest.raises(DatabaseRefused) as refused:
            check_database(scratch_database["app"], scratch_database["auth"])
        assert any(expected in f for f in refused.value.failures), refused.value.failures


def test_connection_problems_are_refused_without_revealing_the_url(migrated):
    password = urlsplit(migrated["app"]).password
    for url in (_with(migrated["app"], port=1), _with(migrated["app"], password="wrong-password"),
                _with(migrated["app"], database="no_such_database")):
        with pytest.raises(DatabaseConfigError, match="unreachable or refused") as refused:
            check_database(url, migrated["auth"])
        assert password not in str(refused.value) and "wrong-password" not in str(refused.value)


# --- production startup ---------------------------------------------------------------------------------------------


def _open(urls: dict, environ: dict | None = None):
    return production.open_runtime(auth_section(), environ or production_environ(urls))


def test_production_with_a_safe_database_and_sign_in_configuration_opens_its_runtime(migrated, tmp_path):
    """Phase 4 replaces PRODUCTION_CONSOLE_NOT_BUILT: with every check passing, production opens its connection pools.
    It holds no user: a console is opened only for a verified sign-in (test_production_auth.py)."""
    runtime = _open(migrated)
    try:
        assert runtime.issuer == "https://issuer.baec-test.invalid"
        assert not hasattr(storage_selection, "PRODUCTION_CONSOLE_NOT_BUILT")
    finally:
        runtime.close()
    with pytest.raises(deployment.ProductionAccessRefused):  # no way to ask for a production console by user id
        deployment.runtime_console(tmp_path / "data", mode=deployment.DeploymentMode.PRODUCTION)
    assert not (tmp_path / "data").exists()  # no SQLite identity, workspace, or engine file


def test_no_engine_store_lacks_a_postgresql_backend():
    assert [e for e, b in storage_selection.ENGINE_BACKENDS.items() if b is None] == []
    assert "baec_engine1 (migration 0004)" in storage_selection.ENGINE_BACKENDS["Engine 1"]


@pytest.mark.parametrize("unsafe", [["Engine 1"], ["Engine 2"], ["Engine 1", "Engine 2"], ["Engine 3"]])
def test_production_stays_closed_while_any_engine_store_lacks_a_postgresql_backend(migrated, monkeypatch, unsafe):
    monkeypatch.setattr(storage_selection, "ENGINE_BACKENDS",
                        {e: (None if e in unsafe else "safe") for e in storage_selection.ENGINE_BACKENDS})
    with pytest.raises(storage_selection.StorageRefused) as refused:
        _open(migrated)
    assert str(refused.value) == storage_selection.engine_storage_blocker(unsafe)


def test_an_unsafe_engine2_schema_closes_production_before_anything_opens(scratch_database):
    migrate.apply_migrations(scratch_database["migrator"])
    with psycopg.connect(scratch_database["migrator"], autocommit=True) as migrator:
        migrator.execute("ALTER TABLE baec_engine2.audit_events DISABLE ROW LEVEL SECURITY")
    with pytest.raises(storage_selection.StorageRefused, match="Production storage refused: .*baec_engine2.audit_events"):
        _open(scratch_database)


@pytest.mark.parametrize("role", ["admin", "migrator", "auth", "provisioner"])
def test_production_refuses_any_role_but_the_application_role(migrated, role):
    with pytest.raises(storage_selection.StorageRefused, match="Production storage refused") as refused:
        _open(migrated, {**production_environ(migrated), "BAEC_DATABASE_URL": migrated[role]})
    assert urlsplit(migrated[role]).password not in str(refused.value)


@pytest.mark.parametrize("role", ["admin", "migrator", "app", "provisioner"])
def test_production_refuses_any_sign_in_lookup_role_but_the_narrow_one(migrated, role):
    with pytest.raises(storage_selection.StorageRefused) as refused:
        _open(migrated, {**production_environ(migrated), "BAEC_AUTH_DATABASE_URL": migrated[role]})
    assert "does not connect as baec_auth_lookup" in "".join(refused.value.failures)
    assert urlsplit(migrated[role]).password not in str(refused.value) + "".join(refused.value.failures)


def test_the_test_database_urls_are_local_only():
    for name in ("BAEC_TEST_POSTGRES_ADMIN_URL", "BAEC_TEST_POSTGRES_MIGRATOR_URL", "BAEC_TEST_POSTGRES_APP_URL",
                 "BAEC_TEST_POSTGRES_AUTH_URL", "BAEC_TEST_POSTGRES_PROVISIONER_URL", "BAEC_TEST_POSTGRES_BACKUP_URL"):
        assert urlsplit(os.environ[name]).hostname in ("127.0.0.1", "localhost")
        assert "sslmode=verify-full" in os.environ[name]  # V0.4 Phase 5: the test cluster is TLS-only


# --- Engine 2 schema integrity (Phase 3B gate) ---------------------------------------------------------------------


def _production_refusal(urls: dict, root, monkeypatch) -> storage_selection.StorageRefused:
    """Production startup against `urls` (a migrated or scratch database), with a valid sign-in configuration, must
    refuse. `root` and `monkeypatch` are kept from Phase 3: production never creates anything under a data root."""
    with pytest.raises(storage_selection.StorageRefused) as refused:
        _open(urls)
    assert not root.exists()
    return refused.value


def test_the_expected_engine2_triggers_are_derived_from_engine2s_own_schema(migrated):
    """The gate's trigger set is Engine 2's SQLite trigger set (read from persistence._schema_statements) plus the
    PostgreSQL-only additions, and a freshly migrated database has exactly it."""
    import sqlite3

    from baec_app.engine2 import persistence

    from baec_console.postgres import gate
    with closing(sqlite3.connect(":memory:")) as sqlite:  # closed: a leaked connection warns in a later test
        for statement in persistence._schema_statements():
            sqlite.execute(statement)
        sqlite_triggers = {r[0] for r in sqlite.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    expected = gate.expected_engine2_triggers()
    assert len(sqlite_triggers) == 92 and sqlite_triggers <= set(expected)
    assert set(expected) - sqlite_triggers == {f"{t}_no_truncate" for t in persistence.TABLES} | {
        "audit_events_assign_event_id"}
    with psycopg.connect(migrated["app"]) as db:
        assert gate.engine2_failures(db) == []
        live = {r[0] for r in db.execute("SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                         "JOIN pg_namespace n ON n.oid = c.relnamespace "
                                         "WHERE n.nspname = 'baec_engine2' AND NOT t.tgisinternal")}
    assert live == set(expected) and len(live) == 123


ENGINE2_SABOTAGE = {
    "missing trigger": ("migrator", "DROP TRIGGER observations_needs_activation ON baec_engine2.observations",
                        "trigger observations_needs_activation is missing"),
    "disabled trigger": ("migrator", "ALTER TABLE baec_engine2.audit_events DISABLE TRIGGER audit_events_no_update",
                         "trigger audit_events_no_update is not enabled"),
    "replica-only trigger": ("migrator", "ALTER TABLE baec_engine2.reviews ENABLE REPLICA TRIGGER reviews_no_delete",
                             "trigger reviews_no_delete is not enabled"),
    "renamed trigger": ("migrator", "ALTER TRIGGER supersessions_no_replace ON baec_engine2.supersessions "
                                    "RENAME TO supersessions_guard", "trigger supersessions_no_replace is missing"),
    "narrowed trigger": ("migrator", ["DROP TRIGGER plan_dimensions_no_replace ON baec_engine2.plan_dimensions",
                                      "CREATE TRIGGER plan_dimensions_no_replace BEFORE INSERT ON "
                                      "baec_engine2.plan_dimensions FOR EACH ROW EXECUTE FUNCTION "
                                      "baec_engine2.refuse_replace('monitoring_plan_id')"],
                         "trigger plan_dimensions_no_replace differs from its validated definition"),
    "extra trigger": ("migrator", "CREATE TRIGGER observations_extra BEFORE INSERT ON baec_engine2.observations "
                                  "FOR EACH ROW EXECUTE FUNCTION baec_engine2.refuse_change()",
                      "unexpected Engine 2 trigger observations_extra"),
    "weakened trigger function": ("migrator", "CREATE OR REPLACE FUNCTION baec_engine2.refuse_change() RETURNS trigger "
                                              "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$",
                                  "function baec_engine2.refuse_change differs from its migration"),
    "weakened tenant function": ("migrator", "CREATE OR REPLACE FUNCTION baec.current_workspace() RETURNS text "
                                             "LANGUAGE sql STABLE AS $$ SELECT 'local'::text $$",
                                 "function baec.current_workspace differs from its migration"),
    "security definer": ("migrator", "ALTER FUNCTION baec_engine2.require_activation() SECURITY DEFINER",
                         "function baec_engine2.require_activation is SECURITY DEFINER"),
    "missing store metadata": ("migrator", "DROP TABLE baec_engine2.engine2_meta",
                               "Engine 2 table baec_engine2.engine2_meta is missing"),
    "altered store metadata": ("admin", ["SET session_replication_role = replica",
                                         "UPDATE baec_engine2.engine2_meta SET value = '2' WHERE key = 'schema_version'"],
                               "engine2_meta) differs from the validated rows"),
}


@pytest.mark.parametrize("case", sorted(ENGINE2_SABOTAGE))
def test_a_missing_or_altered_engine2_schema_object_closes_production(scratch_database, tmp_path, monkeypatch, case):
    role, sabotage, expected = ENGINE2_SABOTAGE[case]
    assert migrate.apply_migrations(scratch_database["migrator"]) == [1, 2, 3, 4, 5, 6]
    with psycopg.connect(scratch_database[role], autocommit=True) as db:
        for statement in [sabotage] if isinstance(sabotage, str) else sabotage:
            db.execute(statement)
    for attempt in range(2):  # refused every time: the gate never repairs the schema
        refused = _production_refusal(scratch_database, tmp_path / f"data-{attempt}", monkeypatch)
        assert str(refused) == ENGINE2_SCHEMA_MISMATCH  # one safe message, not the Engine 1 blocker
        assert any(expected in f for f in refused.failures), refused.failures
        assert urlsplit(scratch_database["app"]).password not in str(refused) + "".join(refused.failures)


def test_a_missing_engine2_table_closes_production(scratch_database, tmp_path, monkeypatch):
    migrate.apply_migrations(scratch_database["migrator"])
    with psycopg.connect(scratch_database["migrator"], autocommit=True) as db:
        db.execute("DROP TABLE baec_engine2.review_check_dimensions")
    refused = _production_refusal(scratch_database, tmp_path / "data", monkeypatch)
    assert str(refused).startswith("Production storage refused:")
    assert "missing: ['review_check_dimensions']" in str(refused)  # caught by the tenant-table check first
    with psycopg.connect(scratch_database["app"]) as db:  # and by the Engine 2 schema check on its own
        assert "Engine 2 table baec_engine2.review_check_dimensions is missing" in gate_failures(db)


def gate_failures(db) -> list[str]:
    from baec_console.postgres.gate import engine2_failures
    return engine2_failures(db)


@pytest.mark.parametrize("tamper", [
    "UPDATE baec_migrations.schema_migrations SET checksum = repeat('0', 64) WHERE version = 3",
    "DELETE FROM baec_migrations.schema_migrations WHERE version = 3",
    "UPDATE baec_migrations.schema_migrations SET name = 'other' WHERE version = 3",
])
def test_altered_engine2_migration_metadata_closes_production(scratch_database, tmp_path, monkeypatch, tamper):
    migrate.apply_migrations(scratch_database["migrator"])
    with psycopg.connect(scratch_database["admin"], autocommit=True) as db:
        db.execute(tamper)
    refused = _production_refusal(scratch_database, tmp_path / "data", monkeypatch)
    assert "not at the expected migration version" in str(refused)  # version, name, and checksum are all pinned


def test_an_application_role_that_can_disable_triggers_closes_production(migrated, tmp_path, monkeypatch):
    """session_replication_role = replica stops every ordinary trigger; the role must not be able to set it.
    Parameter privileges are cluster-wide, so the grant is revoked whatever happens."""
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute("GRANT SET ON PARAMETER session_replication_role TO baec_app")
        try:
            refused = _production_refusal(migrated, tmp_path / "data", monkeypatch)
        finally:
            admin.execute("REVOKE SET ON PARAMETER session_replication_role FROM baec_app")
    assert "can set session_replication_role" in str(refused)
    _open(migrated).close()  # with the grant revoked, production opens again


def test_the_startup_order_is_mode_database_then_sign_in_configuration(migrated, tmp_path):
    """mode, then database configuration, schema, RLS, roles, live-schema integrity (Engine 2, Engine 1, product,
    sign-in schema and lookup role), then the sign-in configuration; only then does production open."""
    from baec_console import auth
    environ = production_environ(migrated)
    with pytest.raises(deployment.DeploymentError, match="only in production mode"):
        production.open_runtime(auth_section(), {**environ, "BAEC_DEPLOYMENT_MODE": "development"})
    with pytest.raises(deployment.DeploymentModeNotConfigured):
        production.open_runtime(auth_section(), {k: v for k, v in environ.items() if k != "BAEC_DEPLOYMENT_MODE"})
    with pytest.raises(storage_selection.StorageRefused, match="BAEC_DATABASE_URL is not set"):
        production.open_runtime(None, {k: v for k, v in environ.items() if k != "BAEC_DATABASE_URL"})
    with pytest.raises(storage_selection.StorageRefused, match="BAEC_AUTH_DATABASE_URL is not set"):
        production.open_runtime(None, {k: v for k, v in environ.items() if k != "BAEC_AUTH_DATABASE_URL"})
    with pytest.raises(auth.AuthConfigurationError) as refused:  # the database is safe; sign-in is not configured
        production.open_runtime(None, environ)
    assert str(refused.value) == auth.AUTH_NOT_CONFIGURED
    with pytest.raises(auth.AuthConfigurationError):
        production.open_runtime(auth_section(), {k: v for k, v in environ.items() if k != "BAEC_AUTH_ISSUER"})
    production.open_runtime(auth_section(), environ).close()
    assert not (tmp_path / "data").exists()
