"""Ordered, forward-only schema migrations, applied by the migration role.

    python -m baec_console.postgres.migrate      (reads BAEC_MIGRATION_DATABASE_URL, or BAEC_MIGRATION_DATABASE_URL_FILE
                                                  naming a mounted secret file; verified TLS only)

In production this runs as the release job, a separate process that alone holds the migration credential, before the
web service starts (deploy/compose.yaml, docs/PRODUCTION_RUNBOOK.md); the web service never receives it and refuses to
start if it does (deployment.require_web_secret_boundary). After applying, it re-reads the record and requires it to
equal exactly the migrations this code has.

Each file in migrations/ is NNNN_name.sql and is applied once, in order, in its own transaction, and recorded in
baec_migrations.schema_migrations with its SHA-256. Re-running applies nothing new. A recorded migration whose file
has changed, or an unknown recorded version, stops the run: migrations are never edited in place or reapplied, and
nothing is ever dropped or reset. RLS policies and grants are migrations too (0002), so they are versioned with the
schema. The application role cannot run this: it has no CREATE privilege, and the runner refuses it by name.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import PostgresError
from .database import connect, validate_database_url

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
APPLICATION_ROLE = "baec_app"
_LOCK = 0x42AEC0003  # pg_advisory_xact_lock key: one migration run at a time


class MigrationError(PostgresError):
    """The database's recorded migrations disagree with the migration files, or the runner was refused."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def migrations() -> list[Migration]:
    found = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        match = re.fullmatch(r"(\d{4})_([a-z0-9_]+)\.sql", path.name)
        if match is None:
            raise MigrationError(f"unexpected migration file name {path.name!r}")
        found.append(Migration(int(match[1]), match[2], path.read_text(encoding="utf-8")))
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrationError("migration versions must be 1, 2, 3, ... with no gaps")
    return found


def expected_versions() -> list[tuple[int, str]]:
    return [(m.version, m.checksum) for m in migrations()]


def recorded(connection) -> list[tuple[int, str]]:
    """(version, checksum) of every applied migration, or [] when nothing has been applied."""
    exists = connection.execute("SELECT to_regclass('baec_migrations.schema_migrations') IS NOT NULL").fetchone()[0]
    if not exists:
        return []
    return [tuple(r) for r in connection.execute(
        "SELECT version, checksum FROM baec_migrations.schema_migrations ORDER BY version").fetchall()]


def apply_migrations(url: str) -> list[int]:
    """Apply every pending migration as the connecting (migration) role. Returns the versions applied."""
    available = migrations()
    name = "BAEC_MIGRATION_DATABASE_URL"
    connection = connect(validate_database_url(url, name=name), name=name)
    try:
        if connection.execute("SELECT current_user").fetchone()[0] == APPLICATION_ROLE:
            raise MigrationError("migrations run as the migration role, never as the application role")
        applied = []
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK,))
            connection.execute("CREATE SCHEMA IF NOT EXISTS baec_migrations")
            connection.execute("CREATE TABLE IF NOT EXISTS baec_migrations.schema_migrations (version integer "
                               "PRIMARY KEY, name text NOT NULL, checksum text NOT NULL, "
                               "applied_at timestamptz NOT NULL DEFAULT now())")
            done = dict(recorded(connection))
            known = {m.version: m for m in available}
            for version, checksum in done.items():
                if version not in known:
                    raise MigrationError(f"the database records migration {version}, which this code does not have")
                if known[version].checksum != checksum:
                    raise MigrationError(f"migration {version} has changed since it was applied")
            for migration in available:
                if migration.version in done:
                    continue
                connection.execute(migration.sql)
                connection.execute("INSERT INTO baec_migrations.schema_migrations (version, name, checksum) "
                                   "VALUES (%s, %s, %s)", (migration.version, migration.name, migration.checksum))
                applied.append(migration.version)
        return applied
    finally:
        connection.close()


def verify_recorded(url: str) -> None:
    """The database records exactly this code's migrations, in order, with their checksums; else MigrationError."""
    name = "BAEC_MIGRATION_DATABASE_URL"
    connection = connect(validate_database_url(url, name=name), name=name)
    try:
        if recorded(connection) != expected_versions():
            raise MigrationError("the recorded migrations differ from this release's migrations after applying")
    finally:
        connection.close()


def main(environ=None) -> int:
    from ..deployment import DeploymentError, configured_value

    environ = os.environ if environ is None else environ
    try:
        url = configured_value(environ, "BAEC_MIGRATION_DATABASE_URL")
        applied = apply_migrations(url)
        verify_recorded(url)
    except (PostgresError, DeploymentError) as error:
        print(f"migration refused: {error}", file=sys.stderr)
        return 1
    print(f"applied migrations: {applied}; schema verified" if applied else "schema is current; nothing applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
