"""Connections, and the one way a transaction acts for an organization.

Tenant context. Every policy compares a row's organization_id with baec.current_organization(), which reads the
setting baec.organization_id. tenant() sets it with set_config(..., is_local => true) as the first statement of a
transaction: the value is a bound parameter (never interpolated into SQL), and PostgreSQL discards it when the
transaction commits or rolls back. A connection outside tenant() has no organization and sees no tenant rows.

Workspace. A transaction may also act in one workspace ('local' or 'synthetic'): baec.workspace, set the same way.
Engine 2 tables (migration 0003) are scoped to it by a restrictive policy, so Engine 2's own SQL sees one workspace,
as it saw one SQLite file. Product tables carry a workspace column but are filtered by the stores, not by policy.

Pool safety. Connections are pooled in autocommit mode, so the only transactions are the explicit ones tenant()
opens. Before handing a connection out, tenant() checks that it carries no organization; before a connection is
returned, available() runs DISCARD ALL on it in the requesting thread, which also clears a setting someone made
session-wide. A connection whose context cannot be cleared is closed, never reused. DISCARD ALL also drops
server-side prepared statements, so pooled connections never auto-prepare (prepare_threshold=None). (The reset is not
the pool's own reset callback: psycopg_pool runs that in a background task, and a pool closed before the task runs
drops it without closing the connection, which then stays open on the server. Found in V0.4 Phase 5; regression
test in tests_postgres/test_deployment_hardening.py.)

Transport (V0.4 Phase 5). Every BAEC PostgreSQL connection requires verified TLS: the URL must say
sslmode=verify-full, so libpq encrypts the connection and checks that the server's certificate names the host and
chains to the configured root (sslrootcert, a CA file or "system"). require_verified_tls() refuses any other URL
before anything connects, and connect() and the pools apply it, so there is no plaintext path. The production gate
also checks, on each live connection, that TLS is really in use (gate.transport_failures).

Bounds (V0.4 Phase 5). A pool holds at most max_size connections per process however many browser sessions there
are; a request waits at most pool_timeout for one, and at most max_waiting requests wait at once; past either bound
it fails with DatabaseUnavailable instead of opening more. Every pooled session starts with a statement timeout, a
lock timeout, and an idle-in-transaction timeout (DatabaseSettings), so a stuck query, a held lock, or an abandoned
transaction cannot hold a connection indefinitely. A request that hits one of these limits, or loses its connection,
is rolled back by PostgreSQL and reported as DatabaseUnavailable (a fixed message); its connection is reset or
discarded, never reused with state. Integrity checks (constraints, triggers, row-level security) are never
converted: they still raise their own errors.

Database URLs carry credentials. Nothing here prints, logs, or puts one in an error message.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Mapping
from urllib.parse import parse_qs, urlsplit

from . import PostgresError, require_driver

ORGANIZATION_SETTING = "baec.organization_id"
WORKSPACE_SETTING = "baec.workspace"
WORKSPACES = ("local", "synthetic")
# The transaction-local search_path for the engines' unqualified SQL. Engine 1 and Engine 2 table names are disjoint
# (tests_postgres/test_engine1_parity.py checks it), so one path resolves both; pg_temp last, so a temporary table can
# never shadow an engine table. Product stores always qualify their tables.
ENGINE_SEARCH_PATH = "pg_catalog, baec_engine1, baec_engine2, pg_temp"


class DatabaseConfigError(PostgresError):
    """A database URL is absent, malformed, or not a supported PostgreSQL URL. The URL itself is never shown."""


class TenantContextError(PostgresError):
    """A connection carried an organization it should not have, or an organization id is not usable."""


UNAVAILABLE = ("BAEC Console could not complete this request because its database is busy or unavailable. "
               "Try again shortly.")


class DatabaseUnavailable(PostgresError):
    """A request could not get a connection in time, ran past a timeout, or lost its connection. PostgreSQL rolled
    its transaction back. The message is fixed; `reason` names the limit (for tests and operators), never a URL."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(UNAVAILABLE)


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection bounds and timeouts (IMPLEMENTATION; chosen for BAEC's short interactive transactions, see
    docs/V04_PHASE5_DEPLOYMENT_HARDENING_REPORT.md section 10). Times in seconds unless named _ms."""

    max_size: int = 4  # connections per application pool, per process
    auth_max_size: int = 2  # connections per sign-in lookup pool, per process
    pool_timeout: float = 10.0  # longest wait for a free connection
    max_waiting: int = 64  # requests allowed to wait at once; more fail immediately
    connect_timeout: int = 5
    statement_timeout_ms: int = 30_000
    lock_timeout_ms: int = 5_000
    idle_in_transaction_timeout_ms: int = 60_000
    max_idle: float = 300.0  # an idle connection is closed after this (down to min_size)
    max_lifetime: float = 1800.0  # every connection is replaced after this

    @property
    def options(self) -> str:
        """The libpq startup options: the session defaults every pooled connection starts with (and returns to on
        DISCARD ALL)."""
        return (f"-c statement_timeout={self.statement_timeout_ms} -c lock_timeout={self.lock_timeout_ms} "
                f"-c idle_in_transaction_session_timeout={self.idle_in_transaction_timeout_ms}")

    def kwargs(self) -> dict:
        return {"autocommit": True, "connect_timeout": self.connect_timeout, "prepare_threshold": None,
                "options": self.options}


# Environment variable -> (field, lowest, highest). Outside the range, or not a whole number, is refused.
SETTINGS_VARIABLES = {
    "BAEC_DB_POOL_MAX_SIZE": ("max_size", 1, 32),
    "BAEC_AUTH_DB_POOL_MAX_SIZE": ("auth_max_size", 1, 8),
    "BAEC_DB_POOL_TIMEOUT_SECONDS": ("pool_timeout", 1, 60),
    "BAEC_DB_STATEMENT_TIMEOUT_MS": ("statement_timeout_ms", 1_000, 300_000),
    "BAEC_DB_LOCK_TIMEOUT_MS": ("lock_timeout_ms", 100, 60_000),
    "BAEC_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": ("idle_in_transaction_timeout_ms", 1_000, 600_000),
}


def database_settings(environ: Mapping[str, str]) -> DatabaseSettings:
    """The configured bounds, or DatabaseConfigError naming the variable. Unset means the default."""
    values = {}
    for name, (field, lowest, highest) in SETTINGS_VARIABLES.items():
        raw = environ.get(name)
        if raw is None or raw == "":
            continue
        if not raw.strip().isdigit() or not lowest <= int(raw) <= highest:
            raise DatabaseConfigError(f"{name} must be a whole number from {lowest} to {highest}")
        values[field] = int(raw)
    settings = DatabaseSettings(**values)
    if settings.lock_timeout_ms >= settings.statement_timeout_ms:
        raise DatabaseConfigError("BAEC_DB_LOCK_TIMEOUT_MS must be shorter than BAEC_DB_STATEMENT_TIMEOUT_MS")
    return settings


def require_verified_tls(url: str, *, name: str = "BAEC_DATABASE_URL") -> str:
    """The URL, if it requires verified TLS (exactly one sslmode, and it is verify-full). Never echoes the value."""
    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        raise DatabaseConfigError(f"{name} is malformed") from None
    if query.get("sslmode") != ["verify-full"]:
        raise DatabaseConfigError(f"{name} must use a verified TLS connection (sslmode=verify-full)")
    return url


def _unavailable(error: Exception) -> str | None:
    """Why `error` means the database could not serve this request, or None if it is any other error (an integrity
    or permission refusal keeps its own type)."""
    from psycopg import errors
    from psycopg_pool import PoolTimeout, TooManyRequests

    if isinstance(error, PoolTimeout):
        return "no connection became free in time (pool exhausted)"
    if isinstance(error, TooManyRequests):
        return "too many requests are already waiting for a connection"
    if isinstance(error, errors.QueryCanceled):
        return "statement timeout"
    if isinstance(error, errors.LockNotAvailable):
        return "lock timeout"
    if isinstance(error, errors.IdleInTransactionSessionTimeout):
        return "idle-in-transaction timeout"
    if isinstance(error, (errors.AdminShutdown, errors.CannotConnectNow, errors.TooManyConnections)):
        return "the server refused or ended the connection"
    if isinstance(error, errors.OperationalError) and (error.sqlstate is None or error.sqlstate.startswith("08")):
        return "the connection was lost"
    return None


def validate_database_url(url: str | None, *, name: str = "BAEC_DATABASE_URL") -> str:
    """The URL, if it is a usable PostgreSQL URL: postgresql:// (or postgres://), with a user, a host, and a database.
    Refuses anything else, including sqlite URLs, without echoing the value."""
    if url is None or not url.strip():
        raise DatabaseConfigError(f"{name} is not set")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise DatabaseConfigError(f"{name} is malformed") from None
    if parts.scheme not in ("postgresql", "postgres"):
        raise DatabaseConfigError(f"{name} must be a postgresql:// URL")
    if not parts.username or not parts.hostname or not parts.path.strip("/") or port == 0:
        raise DatabaseConfigError(f"{name} must name a user, a host, and a database")
    return url.strip()


def connect(url: str, *, autocommit: bool = True, name: str = "the database URL", options: str | None = None):
    """One connection, over verified TLS only. Connection failures are reported without the URL or the server's
    message. `options` are libpq startup options (DatabaseSettings.options)."""
    psycopg = require_driver()
    require_verified_tls(url, name=name)
    extra = {} if options is None else {"options": options}
    try:
        return psycopg.connect(url, autocommit=autocommit, connect_timeout=5, **extra)
    except psycopg.OperationalError:
        raise DatabaseConfigError("the PostgreSQL database is unreachable or refused the connection") from None


def _organization_id(organization_id: str) -> str:
    if not isinstance(organization_id, str) or not organization_id.strip():
        raise TenantContextError("an organization id is required")
    return organization_id


def _workspace(workspace: str | None) -> str | None:
    if workspace is not None and workspace not in WORKSPACES:
        raise TenantContextError(f"unknown workspace {workspace!r}")
    return workspace


def _setting(connection, name: str) -> str | None:
    return connection.execute("SELECT pg_catalog.current_setting(%s, true)", (name,)).fetchone()[0] or None


def current_organization(connection) -> str | None:
    return _setting(connection, ORGANIZATION_SETTING)


def current_workspace(connection) -> str | None:
    return _setting(connection, WORKSPACE_SETTING)


def act_for(connection, organization_id: str, workspace: str | None = None) -> None:
    """Make the current transaction act for `organization_id` (and, if given, in `workspace`), until it ends (or
    act_for is called again in it). The connection must already be inside a transaction: outside one, a
    transaction-local setting would not hold."""
    from psycopg import pq

    if connection.info.transaction_status == pq.TransactionStatus.IDLE:
        raise TenantContextError("tenant context is set only inside a transaction")
    connection.execute("SELECT pg_catalog.set_config(%s, %s, true)",
                       (ORGANIZATION_SETTING, _organization_id(organization_id)))
    if _workspace(workspace) is not None:
        connection.execute("SELECT pg_catalog.set_config(%s, %s, true)", (WORKSPACE_SETTING, workspace))


@contextmanager
def tenant_transaction(connection, organization_id: str, workspace: str | None = None) -> Iterator:
    """One transaction acting for one organization (and optionally one workspace), on a connection with no
    organization or workspace of its own."""
    organization_id = _organization_id(organization_id)
    _workspace(workspace)
    if current_organization(connection) is not None:
        raise TenantContextError("the connection already carries an organization")
    if current_workspace(connection) is not None:
        raise TenantContextError("the connection already carries a workspace")
    with connection.transaction():
        act_for(connection, organization_id, workspace)
        yield connection


def _discard(connection) -> None:
    connection.execute("DISCARD ALL")


def open_pool(url: str, *, name: str, min_size: int, max_size: int, settings: DatabaseSettings):
    """A bounded pool of verified-TLS connections, each starting with the settings' timeouts. Use it only through
    available(), which resets each connection before returning it."""
    require_driver()
    from psycopg_pool import ConnectionPool

    return ConnectionPool(require_verified_tls(validate_database_url(url, name=name), name=name),
                          min_size=min_size, max_size=max_size, kwargs=settings.kwargs(),
                          check=ConnectionPool.check_connection, timeout=settings.pool_timeout,
                          max_waiting=settings.max_waiting, max_idle=settings.max_idle,
                          max_lifetime=settings.max_lifetime, open=True)


def _reset(connection) -> None:
    """Bring a connection back to a clean session (no transaction, no setting, no prepared statement), or close it
    so the pool discards it. Runs in the requesting thread, before the connection is returned."""
    from psycopg import pq

    try:
        if connection.pgconn.transaction_status != pq.TransactionStatus.IDLE:
            connection.rollback()
        _discard(connection)
    except Exception:  # any state that cannot be cleared: never reuse the connection
        connection.close()


@contextmanager
def available(pool) -> Iterator:
    """A connection from `pool`, for one request. Pool exhaustion, a timeout, or a lost connection, whether getting
    the connection or during the request, becomes DatabaseUnavailable; every other error propagates unchanged. The
    connection is reset (or closed) and then always goes back to the pool, which discards a closed one."""
    psycopg = require_driver()
    try:
        with pool.connection() as connection:
            try:
                yield connection
            finally:
                _reset(connection)
    except psycopg.Error as error:
        reason = _unavailable(error)
        if reason is None:
            raise
        raise DatabaseUnavailable(reason) from None


_TIMEOUT_SETTINGS = ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")


def session_failures(connection, settings: DatabaseSettings, label: str) -> list[str]:
    """How a pooled session differs from what `settings` require: it must use TLS and start with exactly the
    configured timeouts. (A connection pooler that drops libpq startup options would leave a session without them.)"""
    failures = [] if connection.pgconn.ssl_in_use else [f"the {label} pool's connections do not use TLS"]
    live = dict(connection.execute("SELECT name, setting FROM pg_catalog.pg_settings WHERE name = ANY(%s)",
                                   (list(_TIMEOUT_SETTINGS),)).fetchall())
    expected = dict(zip(_TIMEOUT_SETTINGS, (settings.statement_timeout_ms, settings.lock_timeout_ms,
                                            settings.idle_in_transaction_timeout_ms)))
    for name, value in expected.items():
        if live.get(name) != str(value):
            failures.append(f"the {label} pool's connections do not have the configured {name}")
    return failures


class TenantDatabase:
    """A pool of application-role connections. Every use is one tenant transaction."""

    def __init__(self, url: str, *, min_size: int = 1, max_size: int | None = None,
                 settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or DatabaseSettings()
        self.pool = open_pool(url, name="BAEC_DATABASE_URL", min_size=min_size,
                              max_size=self.settings.max_size if max_size is None else max_size,
                              settings=self.settings)

    @contextmanager
    def tenant(self, organization_id: str, workspace: str | None = None) -> Iterator:
        organization_id = _organization_id(organization_id)
        _workspace(workspace)
        with available(self.pool) as connection:
            with tenant_transaction(connection, organization_id, workspace):
                yield connection

    def close(self) -> None:
        self.pool.close()

    def __enter__(self) -> TenantDatabase:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
