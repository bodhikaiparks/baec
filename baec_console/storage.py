"""Which persistence a deployment mode may use. Decided once, before any store is opened.

    development  SQLite product and engine stores under the data directory (backends.SQLiteBackend). Only when
                 BAEC_DEPLOYMENT_MODE is explicitly "development"; BAEC_DATABASE_URL must not be set, because the
                 development console runs on SQLite and silently using SQLite instead of the configured database
                 would mislead.
    production   PostgreSQL only, read from BAEC_DATABASE_URL (the application role, never the migration role) and
                 BAEC_AUTH_DATABASE_URL (the sign-in lookup role, migration 0005), each as a value or as NAME_FILE
                 naming a mounted secret file (deployment.configured_value), each requiring verified TLS
                 (sslmode=verify-full; V0.4 Phase 5). The database must pass every
                 check in postgres/gate.py, and every engine store BAEC needs must have a PostgreSQL backend
                 (ENGINE_BACKENDS): production never falls back to a shared SQLite file. No SQLite store, directory,
                 or identity file is created or opened here.

Product data (identity, console, CRM), Engine 1, and Engine 2 have PostgreSQL backends with database-enforced
tenancy, and ConsoleService runs on them through backends.PostgresBackend. The production console (production.py)
opens only after this check passes, and only for a signed-in user. See docs/V04_PHASE4_PRODUCTION_AUTH_REPORT.md.
"""

from __future__ import annotations

import os
from typing import Mapping

from .deployment import DeploymentError, configured_value

DATABASE_URL_VARIABLE = "BAEC_DATABASE_URL"
AUTH_DATABASE_URL_VARIABLE = "BAEC_AUTH_DATABASE_URL"

# Each engine's production persistence. None means no PostgreSQL store exists: production must not run.
ENGINE_BACKENDS: dict[str, str | None] = {
    "Engine 1": "PostgreSQL schema baec_engine1 (migration 0004), checked by the production database gate",
    "Engine 2": "PostgreSQL schema baec_engine2 (migration 0003), checked by the production database gate",
    "Engine 3": "no persistence of its own (its records are product tables)",
}


def engine_storage_blocker(missing: list[str]) -> str:
    names = " and ".join(missing)
    return (f"Production storage is not available yet: {names} records have no PostgreSQL store "
            f"({'it' if len(missing) == 1 else 'they'} would be in a SQLite file shared by every organization, and "
            f"production never uses a shared SQLite store). BAEC Console is closed. Nothing was read or changed.")


class StorageRefused(DeploymentError):
    """This deployment mode cannot use the configured storage. Nothing was read, created, or changed."""


def require_development_storage(environ: Mapping[str, str] | None = None) -> None:
    environ = os.environ if environ is None else environ
    if environ.get(DATABASE_URL_VARIABLE):
        raise StorageRefused(f"{DATABASE_URL_VARIABLE} is set, but the development console runs only on its local "
                             f"SQLite stores in this version. Unset it to use development mode.")


def require_production_storage(environ: Mapping[str, str] | None = None, settings=None):
    """Check the production database and return its report (postgres.gate.DatabaseReport), or refuse. Order:
    configuration (including verified TLS) and connectivity, then migrations, roles, and row-level security, then the
    live schema objects (Engine 2, Engine 1, product, then the sign-in schema and lookup role; each with its own fixed
    message, e.g. ENGINE1_SCHEMA_MISMATCH), and only then an engine store with no PostgreSQL backend
    (engine_storage_blocker). `settings` (postgres.database.DatabaseSettings) gives the check's own connections their
    timeouts."""
    from .postgres import PostgresError
    from .postgres.gate import Engine2SchemaRefused, check_database

    environ = os.environ if environ is None else environ
    try:
        report = check_database(configured_value(environ, DATABASE_URL_VARIABLE),
                                configured_value(environ, AUTH_DATABASE_URL_VARIABLE), settings)
    except Engine2SchemaRefused as error:  # also Engine1SchemaRefused, ProductSchemaRefused, AuthSchemaRefused
        refused = StorageRefused(str(error))
        refused.failures = error.failures  # schema object names only, for operators and tests
        raise refused from None
    except PostgresError as error:
        raise StorageRefused(f"Production storage refused: {error}. Nothing was read or changed.") from None
    missing = [engine for engine, backend in ENGINE_BACKENDS.items() if backend is None]
    if missing:
        raise StorageRefused(engine_storage_blocker(missing))
    return report
