"""Engine 2 on PostgreSQL: the released Engine 2 code, unchanged, over one tenant transaction.

baec_app/engine2/persistence.py, review.py, and baec_console/engine2_adapter.py take a connection and use only
connection.execute(sql, args) with the cursor's fetchone / fetchall / iteration, plus `in_transaction`. Engine2Connection
provides exactly that over a psycopg connection that is already inside a tenant transaction acting for one
organization and one workspace (database.tenant_transaction). Every Engine 2 rule therefore runs as the same Python
code on both backends: domain validation on write and on load, duplicate refusal, the outcome snapshot recomputed on
every load, and one write = one transaction with its audit event.

The SQL is passed through with a closed set of translations, and anything else SQLite-specific is refused:

    BEGIN IMMEDIATE / COMMIT / ROLLBACK   SAVEPOINT / RELEASE SAVEPOINT / ROLLBACK TO SAVEPOINT (each Engine 2 write is
                                          a savepoint of the enclosing tenant transaction, which commits it)
    rowid                                 seq (an identity column on every table; insertion order, as rowid is with no
                                          deletes)
    ? placeholders                        %s (a literal % is escaped)
    bool parameters                       int, as sqlite3 stores them
    PRAGMA user_version                   1 when migration 0003 is recorded, otherwise 0
    PRAGMA foreign_key_check              no rows: PostgreSQL checks every foreign key when the row is written, and the
                                          application role cannot defer or disable them
    any other PRAGMA                      refused (Engine2SchemaError)

Unqualified table names resolve to schema baec_engine2: the transaction's search_path is database.ENGINE_SEARCH_PATH
(set transaction-locally; it also names baec_engine1, whose table names are disjoint from Engine 2's; pg_temp last, so
a temporary table can never shadow an Engine 2 table). The application role cannot create objects in these schemas.

Errors are PostgreSQL's (psycopg), not sqlite3's: Engine 2 catches no database exception type, so its refusals are
unchanged; trigger messages are the same text as SQLite's (migration 0003).
"""

from __future__ import annotations

import re

from baec_app.engine2.persistence import Engine2SchemaError

from .database import ENGINE_SEARCH_PATH, current_organization, current_workspace

ENGINE2_MIGRATION = 3
_ROWID = re.compile(r"\browid\b", re.IGNORECASE)
_PRAGMA = re.compile(r"^\s*PRAGMA\s+(\w+)\s*;?\s*$", re.IGNORECASE)


class _Rows:
    """The cursor interface Engine 2 uses, for an answer computed here rather than by a query."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _parameter(value):
    return int(value) if isinstance(value, bool) else value


class Engine2Connection:
    """A sqlite3-compatible view of one organization's workspace of Engine 2 tables, inside its tenant transaction."""

    def __init__(self, connection) -> None:
        if current_organization(connection) is None or current_workspace(connection) is None:
            raise Engine2SchemaError("Engine 2 on PostgreSQL needs a tenant transaction with an organization and a "
                                     "workspace")
        self.db = connection
        self._savepoints: list[str] = []
        self._count = 0
        connection.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (ENGINE_SEARCH_PATH,))

    @property
    def in_transaction(self) -> bool:
        return bool(self._savepoints)

    def execute(self, sql: str, args: tuple | list = ()):
        statement = sql.strip().rstrip(";").strip().upper()
        if statement == "BEGIN IMMEDIATE":
            self._count += 1
            name = f"engine2_write_{self._count}"
            self.db.execute(f"SAVEPOINT {name}")
            self._savepoints.append(name)
            return _Rows([])
        if statement in ("COMMIT", "ROLLBACK"):
            if not self._savepoints:
                raise Engine2SchemaError(f"{statement} without an open Engine 2 write")
            name = self._savepoints.pop()
            if statement == "ROLLBACK":
                self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.db.execute(f"RELEASE SAVEPOINT {name}")
            return _Rows([])
        pragma = _PRAGMA.match(sql)
        if pragma:
            return self._pragma(pragma.group(1).lower())
        if statement.startswith("PRAGMA"):
            raise Engine2SchemaError(f"unsupported on PostgreSQL: {sql.strip()}")
        sql = _ROWID.sub("seq", sql)
        if not args:
            return self.db.execute(sql)
        return self.db.execute(sql.replace("%", "%%").replace("?", "%s"), tuple(_parameter(a) for a in args))

    def _pragma(self, name: str) -> _Rows:
        if name == "user_version":
            applied = self.db.execute("SELECT count(*) FROM baec_migrations.schema_migrations WHERE version = %s",
                                      (ENGINE2_MIGRATION,)).fetchone()[0]
            return _Rows([(1 if applied else 0,)])
        if name == "foreign_key_check":
            return _Rows([])
        raise Engine2SchemaError(f"unsupported on PostgreSQL: PRAGMA {name}")
