"""V0.4 Phase 5: the production database boundary under operational stress, on the real test cluster (TLS only).

Verified TLS (client policy, server identity, server policy), role separation and dangerous-privilege detection,
bounded pools under repeated, concurrent, and exhausting load, timeouts that free a stuck request without weakening
a transaction, and readiness. Roles are the real ones (baec_app, baec_auth_lookup, baec_provisioner, baec_migrator,
baec_backup); the test superuser only stages faults no BAEC role can cause. All data synthetic; software tests on a
disposable cluster, not a security assessment or a load test.
"""

from __future__ import annotations

import subprocess
import threading
import time
from urllib.parse import urlsplit

import pytest

from baec_console import deployment, production, readiness
from baec_console import storage as storage_selection
from baec_console.postgres import provision
from baec_console.postgres.database import (
    UNAVAILABLE,
    DatabaseConfigError,
    DatabaseSettings,
    DatabaseUnavailable,
    TenantDatabase,
    available,
    connect,
    current_organization,
    session_failures,
)
from baec_console.postgres.gate import DatabaseRefused, check_database, transport_failures

from .conftest import auth_section, fresh, production_environ, provision as provision_user, psycopg
from .test_production_auth import clock, provisioner, sign_in, world  # noqa: F401


def _query(url: str, **changes) -> str:
    """`url` with its TLS parameters replaced (the credentials and host stay)."""
    parts = urlsplit(url)
    params = dict(p.split("=", 1) for p in parts.query.split("&"))
    params.update(changes)
    return parts._replace(query="&".join(f"{k}={v}" for k, v in params.items() if v is not None)).geturl()


def _secret(url: str) -> str:
    return urlsplit(url).password


def _open(urls: dict, **environ):
    return production.open_runtime(auth_section(), {**production_environ(urls), **environ})


# --- verified TLS -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("variable,role", [("BAEC_DATABASE_URL", "app"), ("BAEC_AUTH_DATABASE_URL", "auth")])
@pytest.mark.parametrize("sslmode", ["disable", "prefer", "require", "verify-ca", None])
def test_production_refuses_any_database_url_that_does_not_require_verified_tls(migrated, variable, role, sslmode):
    unsafe = _query(migrated[role], sslmode=sslmode)
    with pytest.raises(storage_selection.StorageRefused, match=f"{variable} must use a verified TLS") as refused:
        _open(migrated, **{variable: unsafe})
    assert _secret(unsafe) not in str(refused.value)


def test_the_server_itself_refuses_a_plaintext_connection(migrated):
    """Defense in depth (the test cluster's pg_hba.conf; a production server must be configured the same way): even a
    client that asks for no TLS is refused by the server, for every role."""
    for role in ("app", "auth", "admin"):
        with pytest.raises(psycopg.OperationalError, match="pg_hba.conf rejects connection"):
            psycopg.connect(_query(migrated[role], sslmode="disable", sslrootcert=None), connect_timeout=5)


def test_verify_full_checks_the_server_certificate_and_its_name(migrated, tmp_path):
    other_ca = tmp_path / "other-ca.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-days", "1", "-subj", "/CN=another CA (synthetic)", "-keyout", str(tmp_path / "k"), "-out",
                    str(other_ca)], check=True, capture_output=True)
    with pytest.raises(DatabaseConfigError, match="unreachable or refused") as refused:  # a CA that did not sign it
        connect(_query(migrated["app"], sslrootcert=str(other_ca)))
    assert _secret(migrated["app"]) not in str(refused.value)
    # The right address, but a name the certificate does not carry: libpq verifies the name, not just the chain.
    port = urlsplit(migrated["app"]).port
    wrong_name = _query(migrated["app"], hostaddr="127.0.0.1").replace(f"@127.0.0.1:{port}",
                                                                        f"@db.baec-test.invalid:{port}")
    with pytest.raises(DatabaseConfigError, match="unreachable or refused"):
        connect(wrong_name)
    with connect(migrated["app"]) as db:  # the configured CA and name: verified, encrypted
        assert db.pgconn.ssl_in_use and transport_failures(db, "application") == []
        assert db.execute("SELECT version FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()[0] in (
            "TLSv1.2", "TLSv1.3")


class _Plaintext:
    """What a live connection without TLS reports (client and server), for the gate's transport check."""

    class pgconn:
        ssl_in_use = False

    def execute(self, *args):
        class Row:
            def fetchone(self):
                return (False,)
        return Row()


def test_the_gate_refuses_a_live_connection_that_is_not_encrypted():
    assert transport_failures(_Plaintext(), "application") == [
        "the application connection does not use TLS", "the server does not report TLS for the application connection"]


def test_every_pooled_session_uses_tls_and_carries_the_configured_timeouts(migrated):
    settings = DatabaseSettings(statement_timeout_ms=12_000, lock_timeout_ms=1_500, idle_in_transaction_timeout_ms=9_000)
    runtime = _open(migrated, BAEC_DB_STATEMENT_TIMEOUT_MS="12000", BAEC_DB_LOCK_TIMEOUT_MS="1500",
                    BAEC_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS="9000")
    try:
        for pool in (runtime.database.pool, runtime.lookup.pool):
            with available(pool) as connection:
                assert session_failures(connection, settings, "x") == []
                connection.execute("SET statement_timeout = 0")  # a request changing it for the session...
            with available(pool) as connection:  # ...is undone by DISCARD ALL before the next request
                assert session_failures(connection, settings, "x") == []
    finally:
        runtime.close()


def test_a_pool_whose_sessions_lack_the_timeouts_is_detected(migrated):
    """What a connection pooler that drops libpq startup options would produce."""
    with psycopg.connect(migrated["app"]) as bare:
        assert session_failures(bare, DatabaseSettings(), "application") == [
            f"the application pool's connections do not have the configured {name}"
            for name in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")]


# --- roles: the web runtime cannot act as any other role ----------------------------------------------------------------


@pytest.mark.parametrize("role,expected", [
    ("backup", "can bypass row-level security"),
    ("admin", "is a superuser"),
    ("migrator", "owns BAEC schema objects"),
])
def test_production_names_the_dangerous_privilege_of_a_wrong_application_credential(migrated, role, expected):
    with pytest.raises(storage_selection.StorageRefused) as refused:
        _open(migrated, BAEC_DATABASE_URL=migrated[role])
    assert expected in str(refused.value)
    assert _secret(migrated[role]) not in str(refused.value)


@pytest.mark.parametrize("role", ["backup", "provisioner"])
def test_production_refuses_the_backup_or_provisioning_role_as_either_runtime_credential(migrated, role):
    for variable in ("BAEC_DATABASE_URL", "BAEC_AUTH_DATABASE_URL"):
        with pytest.raises(storage_selection.StorageRefused) as refused:
            _open(migrated, **{variable: migrated[role]})
        assert _secret(migrated[role]) not in str(refused.value) + "".join(getattr(refused.value, "failures", []))


@pytest.fixture
def alter_role(migrated):
    """Stage a privilege change on a real role (cluster-wide), and always undo it."""
    undo = []

    def change(statement: str, revert: str) -> None:
        with psycopg.connect(migrated["admin"], autocommit=True) as admin:
            admin.execute(statement)
        undo.append(revert)
    yield change
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        for statement in reversed(undo):
            admin.execute(statement)


@pytest.mark.parametrize("grant,revert,expected", [
    ("ALTER ROLE baec_app BYPASSRLS", "ALTER ROLE baec_app NOBYPASSRLS", "can bypass row-level security"),
    ("ALTER ROLE baec_app REPLICATION", "ALTER ROLE baec_app NOREPLICATION", "can replicate"),
    ("ALTER ROLE baec_app CREATEROLE", "ALTER ROLE baec_app NOCREATEROLE", "can create roles"),
    ("ALTER ROLE baec_app CONNECTION LIMIT -1", "ALTER ROLE baec_app CONNECTION LIMIT 64", "has no connection limit"),
    ("GRANT pg_read_all_data TO baec_app", "REVOKE pg_read_all_data FROM baec_app", "member of another role"),
    ("GRANT baec_provisioner TO baec_app", "REVOKE baec_provisioner FROM baec_app", "member of another role"),
    ("GRANT baec_migrator TO baec_app", "REVOKE baec_migrator FROM baec_app", "member of another role"),
])
def test_startup_detects_a_dangerous_privilege_added_to_the_application_role(migrated, alter_role, grant, revert,
                                                                             expected):
    alter_role(grant, revert)
    with pytest.raises(storage_selection.StorageRefused) as refused:
        _open(migrated)
    assert expected in str(refused.value)


def test_startup_detects_a_sign_in_lookup_role_without_a_connection_limit(migrated, alter_role):
    alter_role("ALTER ROLE baec_auth_lookup CONNECTION LIMIT -1", "ALTER ROLE baec_auth_lookup CONNECTION LIMIT 24")
    with pytest.raises(storage_selection.StorageRefused) as refused:
        _open(migrated)
    assert "the sign-in lookup role has no connection limit" in refused.value.failures


def test_startup_refuses_a_pool_larger_than_the_roles_connection_limit(migrated, alter_role):
    _eventually(lambda: _server_connections(migrated, "baec_auth_lookup", None) == 0)  # earlier tests' sessions
    alter_role("ALTER ROLE baec_auth_lookup CONNECTION LIMIT 1", "ALTER ROLE baec_auth_lookup CONNECTION LIMIT 24")
    with pytest.raises(storage_selection.StorageRefused, match=r"sign-in lookup role's connection limit \(1\) is below "
                                                               r"BAEC_AUTH_DB_POOL_MAX_SIZE \(2\)"):
        _open(migrated)
    runtime = _open(migrated, BAEC_AUTH_DB_POOL_MAX_SIZE="1")
    runtime.close()


def test_the_application_role_cannot_take_any_other_roles_powers(migrated):
    """Migrate, provision, change RLS or the schema, bypass RLS, touch sign-in links, or raise its own privileges."""
    with pytest.raises(provision.ProvisioningError, match="only as baec_provisioner"):
        provision.Provisioner(migrated["app"])
    with psycopg.connect(migrated["app"], autocommit=True) as app:
        for sql in ("SET ROLE baec_migrator", "SET ROLE baec_provisioner", "SET ROLE baec_backup",
                    "SET session_replication_role = replica", "ALTER TABLE baec.users NO FORCE ROW LEVEL SECURITY",
                    "ALTER TABLE baec_engine1.baec_records DISABLE TRIGGER ALL", "DROP SCHEMA baec CASCADE",
                    "CREATE SCHEMA baec_rogue", "ALTER ROLE baec_app BYPASSRLS",
                    "ALTER ROLE baec_app CONNECTION LIMIT -1", "CREATE ROLE rogue",
                    "GRANT pg_read_all_data TO baec_app", "SELECT * FROM baec_auth.auth_identities",
                    "INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, "
                    "linked_by) VALUES ('https://x', 's', 'o', 'u', now(), 'x')",
                    "UPDATE baec_auth.auth_identities SET revoked_at = now(), revoked_by = 'x'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(sql)
        app.execute("SET row_security = off")  # allowed to ask; then any query RLS would filter is refused
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            app.execute("SELECT 1 FROM baec.users")
        app.execute("RESET row_security")
        assert app.execute("SELECT count(*) FROM baec.users").fetchone()[0] == 0  # no tenant: RLS shows nothing


def test_the_sign_in_lookup_role_cannot_read_any_business_table(migrated):
    with psycopg.connect(migrated["admin"]) as admin:
        tables = [r[0] for r in admin.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname IN ('baec', 'baec_engine1', 'baec_engine2', 'baec_migrations')")]
    assert len(tables) > 50
    with psycopg.connect(migrated["auth"], autocommit=True) as lookup:
        for table in tables:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                lookup.execute(f"SELECT 1 FROM {table} LIMIT 1")


def test_the_provisioning_credential_never_reaches_the_web_runtime(migrated):
    """Every other check would pass; the provisioning (or migration) credential alone keeps production closed."""
    for variable, role in (("BAEC_PROVISIONING_DATABASE_URL", "provisioner"),
                           ("BAEC_MIGRATION_DATABASE_URL", "migrator"), ("BAEC_BACKUP_DATABASE_URL", "backup")):
        with pytest.raises(deployment.SecretBoundaryRefused) as refused:
            _open(migrated, **{variable: migrated[role]})
        assert refused.value.names == [variable] and _secret(migrated[role]) not in str(refused.value)
    _open(migrated).close()  # without them, it opens


# --- bounded pools ------------------------------------------------------------------------------------------------------


def _server_connections(migrated, role: str = "baec_app", application: str | None = "baec-pool-test") -> int:
    """Server sessions of `role` (only this file's pools, unless `application` is None)."""
    with psycopg.connect(migrated["admin"]) as admin:
        return admin.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = %s "
                             "AND (%s::text IS NULL OR application_name = %s)",
                             (role, application, application)).fetchone()[0]


def _released(pool) -> bool:
    """Every connection is back in the pool (a returned connection is reset before it is counted as available)."""
    stats = pool.get_stats()
    return stats.get("pool_available", 0) == stats.get("pool_size", 0) and not stats.get("requests_waiting", 0)


def _eventually(condition, seconds: float = 5) -> None:
    """A closed connection leaves pg_stat_activity once its server process has exited, a moment later."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.05)


def _pool_url(url: str) -> str:
    return _query(url, application_name="baec-pool-test")


def _org(storage, label: str) -> str:
    org = fresh(f"org-{label}")
    provision_user(storage, org, fresh(f"user-{label}"), f"Seller {label} (synthetic)")
    return org


@pytest.mark.parametrize("pool_class", ["TenantDatabase", "AuthLookup"])
def test_closing_a_pool_right_after_use_leaves_no_session_open_on_the_server(migrated, pool_class):
    """Regression (found in this phase): psycopg_pool returned connections through a background task when a reset
    callback was configured, and a pool closed before that task ran left the connection open on the server. A runtime
    that fails its post-open checks closes its pools at once, so each refused request leaked a server session."""
    from baec_console.postgres.auth_lookup import AuthLookup

    role = "baec_app" if pool_class == "TenantDatabase" else "baec_auth_lookup"
    url = _pool_url(migrated["app" if role == "baec_app" else "auth"])
    for _ in range(3):
        opened = TenantDatabase(url) if role == "baec_app" else AuthLookup(url)
        for statement in ("SELECT 1", "SET statement_timeout = 0", "SELECT 2"):
            with available(opened.pool) as connection:
                connection.execute(statement)
        opened.close()
    _eventually(lambda: _server_connections(migrated, role) == 0)


def test_repeated_sign_ins_never_grow_the_pool(migrated, world, clock):  # noqa: F811
    runtime = production.open_runtime(auth_section(), {**production_environ(migrated),
                                                       "BAEC_DATABASE_URL": _pool_url(migrated["app"]),
                                                       "BAEC_AUTH_DATABASE_URL": _pool_url(migrated["auth"])})
    runtime.clock = clock
    try:
        for i in range(40):  # forty browser sessions, each with its own console
            console = sign_in(runtime, clock, world.subject_a if i % 2 else world.subject_b)
            console.local.account_ids()
        assert runtime.database.pool.get_stats()["pool_size"] <= DatabaseSettings().max_size
        assert _server_connections(migrated, "baec_app") <= DatabaseSettings().max_size
        assert _server_connections(migrated, "baec_auth_lookup") <= DatabaseSettings().auth_max_size
    finally:
        runtime.close()
    _eventually(lambda: _server_connections(migrated) == 0)


def test_concurrent_sessions_share_a_bounded_pool_and_each_sees_only_its_organization(storage, migrated):
    organizations = [_org(storage, f"c{i}") for i in range(6)]
    seen, errors, peak = {}, [], []
    with TenantDatabase(_pool_url(migrated["app"]), max_size=3) as db:
        def session(index: int) -> None:
            org = organizations[index % len(organizations)]
            try:
                for _ in range(5):
                    with db.tenant(org) as connection:
                        rows = {r[0] for r in connection.execute("SELECT organization_id FROM baec.organizations")}
                        seen.setdefault(index, set()).update(rows)
                        connection.execute("SELECT pg_sleep(0.01)")
                        peak.append(db.pool.get_stats()["pool_size"])
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(error)
        threads = [threading.Thread(target=session, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert max(peak) <= 3 and _server_connections(migrated) <= 3
        with db.pool.connection() as raw:
            assert current_organization(raw) is None
    assert all(seen[i] == {organizations[i % len(organizations)]} for i in range(24))


def test_pool_exhaustion_is_a_controlled_refusal_and_the_pool_recovers(storage, migrated):
    org = _org(storage, "x")
    settings = DatabaseSettings(max_size=2, pool_timeout=1)
    with TenantDatabase(_pool_url(migrated["app"]), settings=settings) as db:
        held, release = threading.Event(), threading.Event()
        holders = []

        def hold():
            with db.tenant(org):
                held.set()
                release.wait(10)
        for _ in range(2):
            holders.append(threading.Thread(target=hold))
            holders[-1].start()
            assert held.wait(5)
            held.clear()
        started = time.monotonic()
        with pytest.raises(DatabaseUnavailable) as refused:
            with db.tenant(org):
                pytest.fail("a third connection was handed out")
        assert 0.8 < time.monotonic() - started < 5
        assert str(refused.value) == UNAVAILABLE and refused.value.reason.endswith("(pool exhausted)")
        assert _server_connections(migrated) <= 2  # it never opened a third
        release.set()
        for thread in holders:
            thread.join()
        with db.tenant(org) as connection:  # recovered, and clean
            assert current_organization(connection) == org
        with db.pool.connection() as raw:
            assert current_organization(raw) is None


def test_too_many_waiting_requests_are_refused_at_once(storage, migrated):
    org = _org(storage, "w")
    settings = DatabaseSettings(max_size=1, pool_timeout=5, max_waiting=1)
    with TenantDatabase(_pool_url(migrated["app"]), settings=settings) as db:
        held, release, results = threading.Event(), threading.Event(), []

        def hold():
            with db.tenant(org):
                held.set()
                release.wait(10)

        def wait():
            try:
                with db.tenant(org):
                    results.append("served")
            except DatabaseUnavailable as error:
                results.append(error.reason)
        holder = threading.Thread(target=hold)
        holder.start()
        assert held.wait(5)
        waiter = threading.Thread(target=wait)
        waiter.start()
        deadline = time.monotonic() + 5
        while db.pool.get_stats().get("requests_waiting", 0) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(DatabaseUnavailable, match="busy or unavailable") as refused:
            with db.tenant(org):
                pass
        assert refused.value.reason == "too many requests are already waiting for a connection"
        release.set()
        holder.join()
        waiter.join()
        assert results == ["served"]


def test_a_failed_request_returns_its_connection_and_leaves_no_tenant(storage, migrated):
    org = _org(storage, "f")
    with TenantDatabase(_pool_url(migrated["app"]), max_size=2) as db:
        for failure in (ValueError("application refusal"), None):
            for _ in range(5):
                with pytest.raises((ValueError, psycopg.errors.DivisionByZero)):  # not "unavailable": its own type
                    with db.tenant(org) as connection:
                        if failure is not None:
                            raise failure
                        connection.execute("SELECT 1 / 0")
        assert db.pool.get_stats()["pool_size"] <= 2
        _eventually(lambda: _released(db.pool))
        with db.pool.connection() as raw:
            assert current_organization(raw) is None


# --- timeouts -----------------------------------------------------------------------------------------------------------


FAST = DatabaseSettings(max_size=2, pool_timeout=2, statement_timeout_ms=1_000, lock_timeout_ms=500,
                        idle_in_transaction_timeout_ms=1_000)


def test_a_stuck_statement_is_cancelled_and_its_transaction_rolled_back(storage, migrated):
    org = _org(storage, "s")
    marker = fresh("org-never-committed")
    with TenantDatabase(_pool_url(migrated["app"]), settings=FAST) as db:
        started = time.monotonic()
        with pytest.raises(DatabaseUnavailable) as refused:
            with db.tenant(marker) as connection:
                connection.execute("INSERT INTO baec.organizations (organization_id, name, created_at) "
                                   "VALUES (%s, 'written before the stuck statement', now())", (marker,))
                connection.execute("SELECT pg_sleep(30)")
        assert refused.value.reason == "statement timeout" and time.monotonic() - started < 5
        with db.tenant(marker) as connection:  # the earlier write in that transaction did not survive
            assert connection.execute("SELECT count(*) FROM baec.organizations").fetchone()[0] == 0
        with db.tenant(org) as connection:
            assert current_organization(connection) == org


def test_a_held_lock_cannot_hold_a_request_indefinitely(storage, migrated):
    org = _org(storage, "l")
    with TenantDatabase(_pool_url(migrated["app"]), settings=FAST) as db, \
            psycopg.connect(migrated["admin"]) as blocker:
        blocker.execute("LOCK TABLE baec.organizations IN ACCESS EXCLUSIVE MODE")  # held until rollback
        started = time.monotonic()
        with pytest.raises(DatabaseUnavailable) as refused:
            with db.tenant(org) as connection:
                connection.execute("SELECT count(*) FROM baec.organizations").fetchone()
        assert refused.value.reason == "lock timeout" and time.monotonic() - started < 3
        blocker.rollback()
        with db.tenant(org) as connection:
            assert connection.execute("SELECT count(*) FROM baec.organizations").fetchone()[0] == 1
        _eventually(lambda: _released(db.pool))


def test_an_abandoned_transaction_is_ended_by_the_server_and_its_connection_discarded(storage, migrated):
    org = _org(storage, "i")
    with TenantDatabase(_pool_url(migrated["app"]), settings=FAST) as db:
        with pytest.raises(DatabaseUnavailable) as refused:
            with db.tenant(org) as connection:
                pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                time.sleep(2.5)  # idle inside the transaction, past the 1 s limit
                connection.execute("SELECT 1")
        assert refused.value.reason in ("idle-in-transaction timeout", "the connection was lost")
        with db.tenant(org) as connection:  # a different, healthy session
            assert connection.execute("SELECT pg_backend_pid()").fetchone()[0] != pid
            assert current_organization(connection) == org


def test_a_console_operation_blocked_by_a_lock_fails_safely_and_writes_nothing(migrated, world, clock):  # noqa: F811
    runtime = production.open_runtime(auth_section(), {**production_environ(migrated),
                                                       "BAEC_DB_LOCK_TIMEOUT_MS": "500",
                                                       "BAEC_DB_STATEMENT_TIMEOUT_MS": "2000"})
    runtime.clock = clock
    try:
        console = sign_in(runtime, clock, world.subject_a)
        account = console.local.create_account("Locked-out account (synthetic)")
        with psycopg.connect(migrated["admin"]) as blocker:
            blocker.execute("LOCK TABLE baec.crm_contacts IN ACCESS EXCLUSIVE MODE")
            with pytest.raises(DatabaseUnavailable, match="busy or unavailable"):
                console.local.crm_add_contact(account, name="Alex Rivera (synthetic)")
            blocker.rollback()
        assert console.local.crm_contacts(account) == []  # nothing was written
        console.local.crm_add_contact(account, name="Alex Rivera (synthetic)")
        assert [c["name"] for c in console.local.crm_contacts(account)] == ["Alex Rivera (synthetic)"]
    finally:
        runtime.close()


# --- readiness ----------------------------------------------------------------------------------------------------------


def _fingerprint(migrated) -> tuple:
    """Every BAEC row, as the superuser: what readiness must never change."""
    with psycopg.connect(migrated["admin"]) as admin:
        tables = [r[0] for r in admin.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname LIKE 'baec%' ORDER BY 1")]
        return tuple(admin.execute(f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                                   f"FROM {table} t").fetchone()[0] for table in tables)


def test_readiness_passes_on_a_safe_deployment_and_changes_nothing(migrated, world):
    before = _fingerprint(migrated)
    assert readiness.check(auth_section(), production_environ(migrated)) == (None, None)
    assert _fingerprint(migrated) == before


@pytest.mark.parametrize("change,code", [
    ({"BAEC_PROVISIONING_DATABASE_URL": "x"}, "SECRET_BOUNDARY"),
    ({"BAEC_DB_POOL_MAX_SIZE": "0"}, "DATABASE"),
    ({"BAEC_AUTH_ISSUER": "http://issuer.baec-test.invalid"}, "SIGN_IN_CONFIGURATION"),
    ({"BAEC_DEPLOYMENT_MODE": "development"}, "CONFIGURATION"),
])
def test_readiness_reports_an_unsafe_configuration(migrated, change, code):
    assert readiness.check(auth_section(), {**production_environ(migrated), **change})[0] == code


def test_readiness_reports_an_unsafe_role_and_a_schema_mismatch(migrated):
    assert readiness.check(auth_section(), {**production_environ(migrated),
                                            "BAEC_DATABASE_URL": migrated["backup"]})[0] == "DATABASE"
    assert readiness.check(auth_section(), {**production_environ(migrated),
                                            "BAEC_AUTH_DATABASE_URL": migrated["app"]})[0] == "DATABASE_SCHEMA"
