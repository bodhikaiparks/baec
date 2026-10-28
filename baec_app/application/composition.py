"""Building the command and proposal facades.

The command facade is built over the writable repository and owns the one
gate. The proposal facade is built only from a separately supplied
query-only connection, never derived from the command repository.

open_read_connection gives the proposal/read path a SQLite-enforced
connection-level write guard (read-only open mode plus PRAGMA query_only).
It is not a complete security boundary: other connections, other processes,
and code running in this process are not constrained by it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from baec_app.application.account_state import AccountStatePreviewService, AccountStateService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.context import Clock, IdFactory
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.errors import ApplicationValidationError, ReadDatabaseUnavailable, ReadOnlyConnectionRequired
from baec_app.application.facades import HumanCommandFacade, ProposalFacade, ReadService
from baec_app.data.database import check_sqlite_version, make_read_only, require_current_schema
from baec_app.data.repository import Repository


def _query_only(connection: sqlite3.Connection) -> bool:
    return connection.execute("PRAGMA query_only").fetchone()[0] == 1


def open_read_connection(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open an existing database file read-only, without ever creating one.

    Accepts a str or an os.PathLike[str] (such as pathlib.Path); bytes paths are refused.
    """
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    if type(path) is not str or not path.strip() or path == ":memory:" or path.strip().lower().startswith("file:"):
        raise ReadDatabaseUnavailable("a path to an existing database file is required")
    file = Path(path)
    if not file.is_file():
        raise ReadDatabaseUnavailable(f"{path!r} is not an existing database file")
    check_sqlite_version()
    try:
        # mode=ro never creates a file: a file that disappeared since the check is an error here.
        connection = sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    except sqlite3.Error as error:
        raise ReadDatabaseUnavailable(f"{path!r} cannot be opened read-only: {error}") from error
    try:
        try:
            require_current_schema(connection)  # DatabaseVersionError propagates unchanged
        except sqlite3.DatabaseError as error:
            raise ReadDatabaseUnavailable(f"{path!r} is not a readable database: {error}") from error
        make_read_only(connection)
        if not _query_only(connection):
            raise ReadOnlyConnectionRequired("PRAGMA query_only could not be enabled")
    except BaseException:
        connection.close()
        raise
    return connection


def build_command_facade(repository: Repository, *, clock: Clock, ids: IdFactory) -> HumanCommandFacade:
    """One gate, shared by every command service, over the writable repository."""
    if type(repository) is not Repository:
        raise ApplicationValidationError("build_command_facade requires a Repository")
    gate = HumanConfirmationGate(clock, ids)
    return HumanCommandFacade(
        gate=gate,
        reads=ReadService(repository),
        classification=ClassificationService(repository, gate, clock, ids),
        dormancy=DormancyJudgmentService(repository, gate, clock, ids),
        account_state=AccountStateService(repository, gate, clock, ids),
    )


def build_proposal_facade(read_connection: sqlite3.Connection) -> ProposalFacade:
    """A read/preview/propose facade over a supplied query-only connection. Holds no authority."""
    if type(read_connection) is not sqlite3.Connection:
        raise ReadOnlyConnectionRequired("build_proposal_facade requires a sqlite3.Connection")
    require_current_schema(read_connection)  # DatabaseVersionError propagates unchanged
    if not _query_only(read_connection):
        raise ReadOnlyConnectionRequired("the proposal facade requires a query-only connection")
    repository = Repository(read_connection)
    return ProposalFacade(reads=ReadService(repository), previews=AccountStatePreviewService(repository))
