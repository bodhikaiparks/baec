"""BAEC PostgreSQL backup and restore. (V0.4 Phase 5; IMPLEMENTATION.)

Runs in the ops image (deploy/ops/Dockerfile: the PostgreSQL 16 client tools and Python, standard library only), never
in the web image, and only with its own credential:

    baec_backup.py backup --out DIR     as baec_backup, from BAEC_BACKUP_DATABASE_URL or BAEC_BACKUP_DATABASE_URL_FILE
    baec_backup.py restore FILE         as a cluster administrator, from BAEC_RESTORE_DATABASE_URL(_FILE), into an
                                        empty database whose roles and grants the DBA bootstrap already created
    baec_backup.py verify FILE          the file's checksum only

Backup: one pg_dump of the whole database in custom format (compressed, consistent: one repeatable-read snapshot),
as the backup role, which reads every organization's rows (BYPASSRLS and pg_read_all_data) and can change nothing.
The dump is written next to a SHA-256 file, never over an existing file, readable by its owner only. It holds every
organization's records, so it is as sensitive as the database: store it encrypted, off this host
(docs/PRODUCTION_RUNBOOK.md). Roles, role passwords, and database-level grants are cluster objects and are not in it.

Restore: the checksum must match; the target must contain no BAEC schema; then one pg_restore in a single
transaction, stopping at the first error (so a failed restore leaves the target empty, never half-restored).
Triggers are disabled while rows are loaded (--disable-triggers, which needs a superuser), because BAEC's append-only
and sign-in guard triggers would refuse the copies; each is re-enabled with its table, and the production startup
gate then verifies every trigger, policy, function, grant, and migration checksum before BAEC serves anything.

Every database connection must use verified TLS (sslmode=verify-full), as everywhere in BAEC. Connection settings
reach pg_dump, pg_restore, and psql through libpq environment variables, never on a command line, and no URL,
password, or server message containing one is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

BACKUP_URL = "BAEC_BACKUP_DATABASE_URL"
RESTORE_URL = "BAEC_RESTORE_DATABASE_URL"
BAEC_SCHEMAS = ("baec", "baec_auth", "baec_engine1", "baec_engine2", "baec_migrations")
# libpq parameters a BAEC URL may carry in its query, and the environment variable each becomes.
_QUERY_ENV = {"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "connect_timeout": "PGCONNECT_TIMEOUT",
              "application_name": "PGAPPNAME"}


class Refused(Exception):
    """The operation was refused. The message never contains a URL, password, or server message."""


def _secret(name: str, environ) -> str:
    value, path = environ.get(name), environ.get(name + "_FILE")
    if value and path:
        raise Refused(f"set {name} or {name}_FILE, not both")
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            raise Refused(f"{name}_FILE cannot be read") from None
    if not value:
        raise Refused(f"{name} is not set")
    return value


def libpq_environment(url: str, name: str) -> dict[str, str]:
    """The libpq environment for a postgresql:// URL that requires verified TLS. Refuses any other URL."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
        query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=bool(parts.query))
    except ValueError:
        raise Refused(f"{name} is malformed") from None
    if parts.scheme not in ("postgresql", "postgres"):
        raise Refused(f"{name} must be a postgresql:// URL")
    if not parts.username or not parts.hostname or not parts.path.strip("/") or port == 0:
        raise Refused(f"{name} must name a user, a host, and a database")
    if query.get("sslmode") != ["verify-full"]:
        raise Refused(f"{name} must use a verified TLS connection (sslmode=verify-full)")
    unknown = sorted(set(query) - set(_QUERY_ENV))
    if unknown or any(len(v) != 1 for v in query.values()):
        raise Refused(f"{name} has unsupported or repeated parameters: {', '.join(unknown) or 'repeated'}")
    environment = {"PGHOST": parts.hostname, "PGPORT": str(port or 5432), "PGUSER": unquote(parts.username),
                   "PGDATABASE": unquote(parts.path.strip("/"))}
    if parts.password is not None:
        environment["PGPASSWORD"] = unquote(parts.password)
    for key, values in query.items():
        environment[_QUERY_ENV[key]] = values[0]
    return environment


def _run(command: list[str], environment: dict[str, str], what: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a PostgreSQL client tool with only these connection settings. Its own output is not shown: libpq and the
    server may name hosts, roles, or paths; the exit status decides."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and not k.startswith("BAEC_")}
    env.update(environment)
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode != 0:
        raise Refused(f"{what} failed (exit status {result.returncode}); nothing was changed")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(dump: Path) -> None:
    record = dump.with_name(dump.name + ".sha256")
    try:
        expected = record.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        raise Refused(f"no checksum file for {dump.name}") from None
    if not dump.is_file() or sha256(dump) != expected:
        raise Refused(f"{dump.name} does not match its checksum; it was not used")


def backup(out: Path, environ) -> Path:
    environment = libpq_environment(_secret(BACKUP_URL, environ), BACKUP_URL)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = out / f"baec-{stamp}.dump"
    try:
        os.close(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))  # never overwrite a backup
    except FileExistsError:
        raise Refused(f"{dump.name} already exists") from None
    try:
        _run(["pg_dump", "--format=custom", "--compress=6", "--file", str(dump), "--no-password"], environment,
             "pg_dump")
        checksum = sha256(dump)
    except BaseException:
        dump.unlink(missing_ok=True)
        raise
    record = dump.with_name(dump.name + ".sha256")
    descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{checksum}  {dump.name}\n")
    return dump


def restore(dump: Path, environ) -> None:
    verify(dump)
    environment = libpq_environment(_secret(RESTORE_URL, environ), RESTORE_URL)
    found = _run(["psql", "--no-psqlrc", "--no-password", "--tuples-only", "--no-align", "--command",
                  "SELECT count(*) FROM pg_namespace WHERE nspname = ANY ('{" + ",".join(BAEC_SCHEMAS) + "}')"],
                 environment, "checking the target database", text=True)
    if found.stdout.strip() != "0":
        raise Refused("the target database already contains BAEC schemas; restore only into an empty database")
    _run(["pg_restore", "--exit-on-error", "--single-transaction", "--disable-triggers", "--no-password",
          "--dbname", environment["PGDATABASE"], str(dump)], environment, "pg_restore")


def main(argv: list[str] | None = None, environ=None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="BAEC PostgreSQL backup and restore")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup").add_argument("--out", required=True, type=Path)
    commands.add_parser("restore").add_argument("file", type=Path)
    commands.add_parser("verify").add_argument("file", type=Path)
    arguments = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if arguments.command == "backup":
            print(f"backup written: {backup(arguments.out, environ).name}")
        elif arguments.command == "restore":
            restore(arguments.file, environ)
            print("restored; run the release verification (python -m baec_console.readiness) before serving")
        else:
            verify(arguments.file)
            print(f"{arguments.file.name}: checksum matches")
    except Refused as error:
        print(f"{arguments.command} refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
