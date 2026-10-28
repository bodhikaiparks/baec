"""Where ConsoleService's records live. One interface, two implementations; the service's code is the same for both.

    SQLiteBackend    development (and every existing test): identity.sqlite3 under the data root, and per workspace
                     directory engine1.sqlite3, engine2.sqlite3, console.sqlite3, crm.sqlite3. Exactly V0.4 Phase 2
                     behavior: each store is its own file, each write block its own SQLite transaction.
    PostgresBackend  PostgreSQL test mode: identity, console, CRM, Engine 1, and Engine 2 in PostgreSQL
                     (postgres/stores.py, postgres/engine1.py, postgres/engine2.py). No SQLite file is opened.
                     Production runs on it, for a signed-in user only (production.py).

What the service asks of a workspace backend:

    unit()          one logical service action. SQLite: nothing (each store opens its own file). PostgreSQL: one tenant
                    transaction for the principal's organization and this workspace; every store opened inside it
                    (identity, console, CRM, Engine 1, Engine 2) uses it. It commits when the action returns, or when
                    the action raises an exception marked commits_recorded_refusal (a refusal the action itself
                    recorded, such as Engine 3's PREPARATION_REFUSED event); any other exception rolls the whole action
                    back, so no partial product, Engine 1, or Engine 2 record remains. Re-entrant: a nested action
                    joins the outer one.
    console(), crm(), engine1(), engine2()   the stores. engine1() is the connection the Engine 1 repository runs
                    on: a sqlite3 connection to engine1.sqlite3, or postgres.engine1.Engine1Connection.
    engine1_reader()  a read-only Engine 1 connection (Engine 2's handoff requires PRAGMA query_only = 1). SQLite:
                    open_read_connection. PostgreSQL: Engine1Connection.read_only(), enforced by the database.
    writing(store)  a block of writes that commit or roll back together. SQLite: BEGIN IMMEDIATE on that store's
                    file. PostgreSQL: a savepoint of the unit.
    identity(org)   the identity store; on PostgreSQL inside the current unit when it acts for `org`.
    create()        create or verify the workspace's stores.
    destroy()       synthetic reset. SQLite deletes the workspace files; PostgreSQL refuses (RESET_UNAVAILABLE): its
                    tables are append-only and a demo dataset reset is designed, not built (Phase 3B report, Part 5).

On SQLite, writes that span Engine 1 and a product store are not atomic (separate files; unchanged V0.3 behavior).
On PostgreSQL they are one transaction (docs/V04_PHASE3C_ENGINE1_POSTGRES_REPORT.md, transaction integration).
"""

from __future__ import annotations

import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from baec_app.application import open_read_connection
from baec_app.data.database import open_database
from baec_app.engine2 import persistence as e2store

from . import console_store, crm_store, identity

RESET_UNAVAILABLE = ("Resetting synthetic data is not available on PostgreSQL storage: its records are append-only, "
                     "and a reset that replaces a logical demo dataset is designed but not built. Nothing was changed.")


class CommitsRecordedRefusal:
    """Marker for an exception an action raises after recording the refusal itself: the unit keeps that record."""

    commits_recorded_refusal = True


def _keeps(error: BaseException) -> bool:
    return bool(getattr(error, "commits_recorded_refusal", False))


# --- SQLite --------------------------------------------------------------------------------------------------------


class SQLiteBackend:
    """The development stores under one data root."""

    resettable = True

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.identity_path = self.root / "identity.sqlite3"
        self.key = f"sqlite:{self.identity_path}"

    @contextmanager
    def identity(self, organization_id: str) -> Iterator[identity.IdentityStore]:
        with identity.identity_store(self.identity_path) as store:
            yield store

    def seed_principal(self, organization_id: str, *, now) -> identity.Principal:
        return identity.seed_principal(self.identity_path, organization_id, now=now)

    def workspace(self, directory: Path, organization_id: str, workspace: str) -> SQLiteWorkspace:
        return SQLiteWorkspace(Path(directory))


class SQLiteWorkspace:
    resettable = True

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, name: str) -> str:
        return str(self.directory / name)

    @contextmanager
    def unit(self) -> Iterator[None]:
        yield

    @contextmanager
    def console(self) -> Iterator[console_store.ConsoleStore]:
        connection = console_store.open_console_store(self.path("console.sqlite3"))
        try:
            yield console_store.ConsoleStore(connection)
        finally:
            connection.close()

    @contextmanager
    def crm(self) -> Iterator[crm_store.CRMStore]:
        connection = crm_store.open_crm_store(self.path("crm.sqlite3"))
        try:
            yield crm_store.CRMStore(connection)
        finally:
            connection.close()

    @contextmanager
    def engine1(self):
        connection = open_database(self.path("engine1.sqlite3"))
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def engine1_reader(self):
        connection = open_read_connection(self.path("engine1.sqlite3"))
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def engine2(self):
        connection = e2store.open_engine2_database(self.path("engine2.sqlite3"))
        try:
            yield connection
        finally:
            connection.close()

    def writing(self, store):
        return console_store.transaction(store.db)

    def create(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        open_database(self.path("engine1.sqlite3")).close()
        e2store.open_engine2_database(self.path("engine2.sqlite3")).close()
        console_store.open_console_store(self.path("console.sqlite3")).close()
        crm_store.open_crm_store(self.path("crm.sqlite3")).close()

    def destroy(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=False)


# --- PostgreSQL ------------------------------------------------------------------------------------------------------


class PostgresBackend:
    """Product stores, Engine 1, and Engine 2 in PostgreSQL. Test mode only."""

    resettable = False

    def __init__(self, storage) -> None:
        self.storage = storage  # postgres.stores.PostgresProductStorage
        self.key = f"postgres:{id(storage)}"
        self._local = threading.local()

    def _open(self) -> list:
        return self._local.__dict__.setdefault("sessions", [])

    @contextmanager
    def session(self, organization_id: str, workspace: str):
        """The current thread's open session for (organization, workspace), or a new one: one tenant transaction."""
        for open_session in reversed(self._open()):
            if (open_session.organization_id, open_session.workspace) == (organization_id, workspace):
                yield open_session
                return
        with self.storage.database.tenant(organization_id, workspace) as connection:
            from .postgres.stores import ProductSession

            opened = ProductSession(connection, organization_id, workspace)
            self._open().append(opened)
            try:
                try:
                    yield opened
                except BaseException as error:
                    if not _keeps(error):
                        raise
                    keep = error
                else:
                    keep = None
            finally:
                self._open().remove(opened)
        if keep is not None:  # the tenant transaction committed the recorded refusal; now report it
            raise keep

    @contextmanager
    def identity(self, organization_id: str) -> Iterator[identity.IdentityStore]:
        current = next((s for s in reversed(self._open()) if s.organization_id == organization_id), None)
        if current is not None:
            yield current.identity
            return
        with self.session(organization_id, "local") as opened:
            yield opened.identity

    def seed_principal(self, organization_id: str, *, now) -> identity.Principal:
        with self.identity(organization_id) as store:
            return identity.ensure_seed_principal(store, organization_id, now=now)

    def workspace(self, directory: Path, organization_id: str, workspace: str) -> PostgresWorkspace:
        return PostgresWorkspace(self, Path(directory), organization_id, workspace)


class PostgresWorkspace:
    resettable = False

    def __init__(self, backend: PostgresBackend, directory: Path, organization_id: str, workspace: str) -> None:
        self.backend = backend
        self.directory = directory  # names the workspace in the service; no file is created under it
        self.organization_id = organization_id
        self.workspace = workspace

    def unit(self):
        return self.backend.session(self.organization_id, self.workspace)

    @contextmanager
    def _current(self):
        with self.unit() as opened:
            yield opened

    @contextmanager
    def console(self):
        with self._current() as opened:
            yield opened.console

    @contextmanager
    def crm(self):
        with self._current() as opened:
            yield opened.crm

    @contextmanager
    def engine1(self):
        with self._current() as opened:
            yield opened.engine1

    @contextmanager
    def engine1_reader(self):
        with self._current() as opened, opened.engine1.read_only() as reader:
            yield reader

    @contextmanager
    def engine2(self):
        with self._current() as opened:
            yield opened.engine2

    def writing(self, store):
        return store.db.transaction()

    def create(self) -> None:
        from .postgres.engine1 import require_engine1_schema

        with self._current() as opened:
            require_engine1_schema(opened.connection)
            e2store.require_engine2_schema(opened.engine2)

    def destroy(self) -> None:
        from .console_service import ConsoleError

        raise ConsoleError(RESET_UNAVAILABLE)
