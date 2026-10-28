"""Engine 1 on PostgreSQL: the released Engine 1 repository over one tenant transaction.

baec_app/data/repository.py (and the command facade built on it) takes a connection and uses only
connection.execute(sql, args), the cursor's fetchone / fetchall / rowcount, `in_transaction`, and
baec_app.data.database.transaction(), which issues BEGIN IMMEDIATE / COMMIT / ROLLBACK through execute. Engine1Connection
provides exactly that over a psycopg connection already inside a tenant transaction acting for one organization and
one workspace (database.tenant_transaction). Every Engine 1 rule therefore runs as the same Python code on both
backends: domain verification before every write, the locked state machine re-run on stored data, compare-and-swap
state updates, and fail-closed rehydration on every load.

Why an adapter and not a second repository: the repository holds the Engine 1 storage rules (what may be persisted and
how stored rows are re-verified on load); a second implementation would duplicate them. Unlike Engine 2, Engine 1
catches a database exception type (sqlite3.IntegrityError in Repository._write), reads database-assigned ids, and
uses one NULL-safe comparison, so three narrow source changes were made instead of impersonating sqlite3: the
repository also catches database.StorageRefusal, and reads ids with INSERT ... RETURNING (valid on SQLite too). The
AI provenance, proposal bridge, and grant stores stay SQLite-only: they require an exact sqlite3.Connection, and no
ConsoleService path uses them (their schema and every trigger are in PostgreSQL, migration 0004).

The SQL is passed through with a closed set of translations; any other statement kind is refused, not emulated:

    BEGIN IMMEDIATE / COMMIT / ROLLBACK   lock_workspace (the per-workspace writer lock BEGIN IMMEDIATE takes on one
                                          file), then SAVEPOINT / RELEASE / ROLLBACK TO: each Engine 1 write block is a
                                          savepoint of the enclosing tenant transaction, which commits it
    INSERT / UPDATE                       run inside their own savepoint, so a refused statement leaves the block
                                          usable, exactly as SQLite's statement rollback does
    a refusal (SQLSTATE class 23)         database.StorageRefusal with the server's primary message: for a trigger,
                                          the trigger's message, byte for byte SQLite's
    rowid                                 seq (insertion order; there are no deletes)
    ? placeholders                        %s (a literal % is escaped)
    IS ? / IS NOT ?                       IS NOT DISTINCT FROM %s / IS DISTINCT FROM %s (SQLite's NULL-safe IS)
    PRAGMA user_version                   7 (Engine 1 schema version) when migration 0004 is recorded, otherwise 0
    PRAGMA foreign_keys                   1: PostgreSQL always enforces them; the application role cannot disable them
    PRAGMA query_only                     1 exactly when the transaction is read-only (read_only(), below)
    any other PRAGMA, DELETE, DDL, ...    Engine1StatementRefused

Text containing U+0000 is refused before it reaches PostgreSQL (StorageRefusal): PostgreSQL text cannot hold it,
SQLite TEXT can. Synthetic data never contains it; this is a documented narrowing of the input domain.

read_only() is the PostgreSQL form of open_read_connection (mode=ro plus PRAGMA query_only): a savepoint with
transaction_read_only on, always rolled back. The database itself refuses every write inside it (SQLSTATE 25006), so
the read-only proof Engine 2's handoff requires (PRAGMA query_only = 1) is enforced by PostgreSQL, not by this module.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

from baec_app.data.database import SCHEMA_VERSION, PersistenceError, StorageRefusal

from .database import ENGINE_SEARCH_PATH, current_organization, current_workspace

ENGINE1_MIGRATION = 4
_ROWID = re.compile(r"\browid\b", re.IGNORECASE)
_IS_NOT_PARAMETER = re.compile(r"\bIS\s+NOT\s+\?", re.IGNORECASE)
_IS_PARAMETER = re.compile(r"\bIS\s+\?", re.IGNORECASE)
_PRAGMA = re.compile(r"^\s*PRAGMA\s+(\w+)\s*;?\s*$", re.IGNORECASE)
_VERB = re.compile(r"^\s*(\w+)")
_WRITES = ("INSERT", "UPDATE")


class Engine1StatementRefused(PersistenceError):
    """A statement Engine 1 on PostgreSQL does not support. Nothing was sent to the database."""


class Engine1SchemaMissing(PersistenceError):
    """The database does not have the Engine 1 schema (migration 0004)."""


class _Rows:
    """The cursor interface the repository uses, for an answer computed here rather than by a query."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.rowcount = -1

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _parameter(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and "\x00" in value:
        raise StorageRefusal("PostgreSQL text cannot contain U+0000")
    return value


def _translate(sql: str) -> str:
    sql = _ROWID.sub("seq", sql).replace("%", "%%")
    sql = _IS_NOT_PARAMETER.sub("IS DISTINCT FROM ?", sql)
    sql = _IS_PARAMETER.sub("IS NOT DISTINCT FROM ?", sql)
    return sql.replace("?", "%s")


def require_engine1_schema(connection) -> None:
    applied = connection.execute("SELECT count(*) FROM baec_migrations.schema_migrations WHERE version = %s",
                                 (ENGINE1_MIGRATION,)).fetchone()[0]
    if not applied:
        raise Engine1SchemaMissing("the Engine 1 PostgreSQL schema (migration 0004) is not applied")


class Engine1Connection:
    """A sqlite3-style view of one organization's workspace of Engine 1 tables, inside its tenant transaction."""

    def __init__(self, connection) -> None:
        if current_organization(connection) is None or current_workspace(connection) is None:
            raise Engine1StatementRefused("Engine 1 on PostgreSQL needs a tenant transaction with an organization "
                                          "and a workspace")
        self.db = connection
        self.organization_id = current_organization(connection)
        self.workspace = current_workspace(connection)
        self._blocks: list[str] = []
        self._count = 0
        connection.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (ENGINE_SEARCH_PATH,))

    @property
    def in_transaction(self) -> bool:
        return bool(self._blocks)

    def _savepoint(self, prefix: str) -> str:
        self._count += 1
        name = f"engine1_{prefix}_{self._count}"
        self.db.execute(f"SAVEPOINT {name}")
        return name

    def _lock(self) -> None:
        self.db.execute("SELECT baec_engine1.lock_workspace(%s, %s)", (self.organization_id, self.workspace))

    def close(self) -> None:
        """The tenant transaction owns the PostgreSQL connection; an Engine 1 session never closes it."""

    def execute(self, sql: str, args: tuple | list = ()):
        statement = sql.strip().rstrip(";").strip().upper()
        if statement == "BEGIN IMMEDIATE":
            self._lock()
            self._blocks.append(self._savepoint("write"))
            return _Rows([])
        if statement in ("COMMIT", "ROLLBACK"):
            if not self._blocks:
                raise Engine1StatementRefused(f"{statement} without an open Engine 1 write")
            name = self._blocks.pop()
            if statement == "ROLLBACK":
                self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.db.execute(f"RELEASE SAVEPOINT {name}")
            return _Rows([])
        pragma = _PRAGMA.match(sql)
        if pragma:
            return self._pragma(pragma.group(1).lower())
        verb = _VERB.match(statement)
        verb = verb.group(1) if verb else ""
        if verb not in ("SELECT", *_WRITES):
            raise Engine1StatementRefused(f"unsupported on PostgreSQL: {verb or 'an empty statement'}")
        parameters = tuple(_parameter(a) for a in args)
        if verb == "SELECT":
            return self.db.execute(_translate(sql), parameters)
        return self._write(_translate(sql), parameters)

    def _write(self, sql: str, parameters: tuple):
        from psycopg import errors

        name = self._savepoint("statement")
        try:
            cursor = self.db.execute(sql, parameters)
            rows = cursor.fetchall() if cursor.description else []
            rowcount = cursor.rowcount
        except errors.IntegrityError as error:
            self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.db.execute(f"RELEASE SAVEPOINT {name}")
            raise StorageRefusal(error.diag.message_primary or "the database refused the write") from None
        except BaseException:
            self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.db.execute(f"RELEASE SAVEPOINT {name}")
            raise
        self.db.execute(f"RELEASE SAVEPOINT {name}")
        result = _Rows([tuple(r) for r in rows])
        result.rowcount = rowcount
        return result

    def _pragma(self, name: str) -> _Rows:
        if name == "user_version":
            applied = self.db.execute("SELECT count(*) FROM baec_migrations.schema_migrations WHERE version = %s",
                                      (ENGINE1_MIGRATION,)).fetchone()[0]
            return _Rows([(SCHEMA_VERSION if applied else 0,)])
        if name == "foreign_keys":
            return _Rows([(1,)])
        if name == "query_only":
            read_only = self.db.execute("SELECT pg_catalog.current_setting('transaction_read_only')").fetchone()[0]
            return _Rows([(1 if read_only == "on" else 0,)])
        raise Engine1StatementRefused(f"unsupported on PostgreSQL: PRAGMA {name}")

    @contextmanager
    def read_only(self) -> Iterator[Engine1Connection]:
        """A block in which PostgreSQL refuses every write; nothing done inside it is kept."""
        if self._blocks:
            raise Engine1StatementRefused("a read-only Engine 1 block cannot be opened inside a write")
        name = self._savepoint("read")
        try:
            self.db.execute("SET LOCAL transaction_read_only = on")
            yield self
        finally:
            self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.db.execute(f"RELEASE SAVEPOINT {name}")
