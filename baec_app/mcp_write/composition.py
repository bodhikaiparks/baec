"""Builds the write server from a database path and a clock.

open_write_runtime(path, clock=...): one writable connection to an existing current-schema database ->
ConfirmationExecutionService(connection, clock=clock) -> build_write_server(executor).

This composition is physically separate from the Phase 5 read composition (mode=ro, PRAGMA query_only), which is
unchanged. It never creates a database, never seeds one, and never migrates one: a missing file, ":memory:", a
file: URI, a non-database, and a database at another schema version are all refused. The connection is opened in
the calling thread, which must run the server's event loop (the connection is thread-affine).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from mcp.server import MCPServer

from baec_app.application import Clock
from baec_app.application.proposal_authorization import ConfirmationExecutionService
from baec_app.data.database import check_sqlite_version, require_current_schema
from baec_app.mcp_write.server import build_write_server


class WriteDatabaseUnavailable(Exception):
    """The given path is not an existing, openable database file."""


def _open_write_connection(path: str | os.PathLike[str]) -> sqlite3.Connection:
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    if type(path) is not str or not path.strip() or path == ":memory:" or path.strip().lower().startswith("file:"):
        raise WriteDatabaseUnavailable("a path to an existing database file is required")
    file = Path(path)
    if not file.is_file():
        raise WriteDatabaseUnavailable(f"{path!r} is not an existing database file")
    check_sqlite_version()
    try:
        # mode=rw never creates a file: a file that disappeared since the check is an error here.
        connection = sqlite3.connect(file.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None)
    except sqlite3.Error as error:
        raise WriteDatabaseUnavailable(f"{path!r} cannot be opened for writing: {error}") from error
    try:
        try:
            require_current_schema(connection)  # DatabaseVersionError propagates unchanged; nothing is migrated
            connection.execute("PRAGMA foreign_keys = ON")
            enforced = connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        except sqlite3.DatabaseError as error:
            raise WriteDatabaseUnavailable(f"{path!r} is not a usable database: {error}") from error
        if not enforced:
            raise WriteDatabaseUnavailable("foreign key enforcement could not be enabled")
    except BaseException:
        connection.close()
        raise
    return connection


class WriteRuntime:
    """The write server plus ownership of its connection. Use as a context manager or call close()."""

    def __init__(self, server: MCPServer, connection: sqlite3.Connection) -> None:
        self._server = server
        self._connection = connection
        self._closed = False

    @property
    def server(self) -> MCPServer:
        if self._closed:
            raise RuntimeError("the write runtime is closed")
        return self._server

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._connection.close()

    def __enter__(self) -> WriteRuntime:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def open_write_runtime(database_path: str | os.PathLike[str], *, clock: Clock) -> WriteRuntime:
    """Open the write connection and build the server; on any failure the connection is closed."""
    connection = _open_write_connection(database_path)
    try:
        server = build_write_server(ConfirmationExecutionService(connection, clock=clock))
    except BaseException:
        connection.close()
        raise
    return WriteRuntime(server, connection)
