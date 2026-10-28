"""Product identity: organizations, users, and the principal every console operation runs as.

An organization owns data; a user records who performed an action. No passwords or credentials are stored here.
Authentication is not implemented: the only principal the product creates for itself is the deterministic
local-development principal below. A future authentication provider must produce a Principal that
Console verifies against this store; nothing in the domain treats a principal as proof of human approval.

A user's identity is its user_id. The display name is a mutable label: authorization never compares it, and each
recorded action keeps the display name it was recorded under as a historical snapshot (console_store.attributions).

Every user has a fixed kind (UserKind). Only HUMAN users are eligible for interactive authentication; the
local-development, synthetic-seed, and public-demo identities never are, and a user's kind can never change.

The identity store is one SQLite file per data root (identity.sqlite3), shared by the local and synthetic
workspaces. Engine 1, Engine 2, and Engine 3 records carry no organization; ownership is mapped on the product side
(console_store.account_ownership) and checked before any engine record is read or written.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2

# The deterministic local-development principal. These literals appear nowhere else.
LOCAL_ORGANIZATION_ID = "local"
LOCAL_ORGANIZATION_NAME = "Local development"
LOCAL_USER_ID = "local-user"
LOCAL_USER_DEFAULT_NAME = "Local user"

# Each organization's synthetic demo seed acts as its own user, so seeded actions are never attributed to the
# person who loaded the demo. The label names the seed, not a real person.
SEED_DISPLAY_NAME = "Synthetic demo seed (not a real person)"


def seed_user_id(organization_id: str) -> str:
    return f"synthetic-seed@{organization_id}"


class IdentityError(Exception):
    """The identity store refused an operation, or a principal does not match an active user."""


class UserKind(Enum):
    """What a user record stands for. Stored explicitly; never inferred from a name."""

    HUMAN = "HUMAN"  # a person; the only kind a future authentication provider may sign in
    LOCAL_DEVELOPMENT = "LOCAL_DEVELOPMENT"  # the deterministic local-development principal
    SYSTEM_SEED = "SYSTEM_SEED"  # an organization's synthetic demo seed
    PUBLIC_DEMO_SESSION = "PUBLIC_DEMO_SESSION"  # the visitor of one temporary public-demo session
    # A user that existed before kinds were recorded and is not one of the known Phase 1 identities. Never eligible.
    LEGACY_UNCLASSIFIED = "LEGACY_UNCLASSIFIED"


@dataclass(frozen=True)
class Organization:
    organization_id: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class User:
    user_id: str
    organization_id: str
    display_name: str
    created_at: datetime
    active: bool
    kind: UserKind

    @property
    def authentication_eligible(self) -> bool:
        """Whether a future authentication provider may sign this user in. Only active HUMAN users."""
        return self.active and self.kind is UserKind.HUMAN


@dataclass(frozen=True)
class Principal:
    """Who the current request or session runs as. user_id and organization_id are the identity; display_name is the
    user's label when the principal was read, used only as the snapshot recorded on actions."""

    user_id: str
    organization_id: str
    display_name: str


_KINDS = ", ".join(f"'{k.value}'" for k in UserKind)

_SCHEMA = (
    "CREATE TABLE identity_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE organizations (organization_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)",
    # Display names are unique within an organization, so a recorded actor label names one user at a time.
    "CREATE TABLE users (user_id TEXT PRIMARY KEY, "
    "organization_id TEXT NOT NULL REFERENCES organizations (organization_id), display_name TEXT NOT NULL, "
    "created_at TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0, 1)), "
    f"kind TEXT NOT NULL CHECK (kind IN ({_KINDS})), "
    "UNIQUE (organization_id, display_name))",
    # A user never moves between organizations.
    "CREATE TRIGGER users_fixed_organization BEFORE UPDATE OF organization_id ON users "
    "BEGIN SELECT RAISE(ABORT, 'a user cannot change organization'); END",
    "CREATE TRIGGER users_no_delete BEFORE DELETE ON users BEGIN SELECT RAISE(ABORT, 'users are never deleted'); END",
    "CREATE TRIGGER organizations_no_delete BEFORE DELETE ON organizations "
    "BEGIN SELECT RAISE(ABORT, 'organizations are never deleted'); END",
)
_V2_STATEMENTS = (
    # V2. A user's kind is fixed when the user is created.
    "CREATE TRIGGER users_fixed_kind BEFORE UPDATE OF kind ON users "
    "BEGIN SELECT RAISE(ABORT, 'a user kind never changes'); END",
    # V2. Append-only history of identity changes: renames, activation changes, and migration classifications.
    "CREATE TABLE identity_audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, "
    "event_type TEXT NOT NULL CHECK (event_type IN ('USER_CREATED', 'USER_RENAMED', 'USER_DEACTIVATED', "
    "'USER_REACTIVATED', 'KIND_CLASSIFIED_BY_MIGRATION')), user_id TEXT NOT NULL REFERENCES users (user_id), "
    "detail TEXT NOT NULL)",
    "CREATE TRIGGER identity_audit_no_update BEFORE UPDATE ON identity_audit "
    "BEGIN SELECT RAISE(ABORT, 'identity_audit is append-only'); END",
    "CREATE TRIGGER identity_audit_no_delete BEFORE DELETE ON identity_audit "
    "BEGIN SELECT RAISE(ABORT, 'identity_audit is append-only'); END",
)
_SCHEMA += _V2_STATEMENTS


def _ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IdentityError("timestamps must be timezone-aware")
    return value.isoformat()


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Additive: record every existing user's kind and start the identity audit. No user, name, or id changes.

    Kinds are assigned only to the exact identities Phase 1 code created: LOCAL_USER_ID, and each organization's
    seed_user_id. Matching is on the stored user_id, never on a display name. Any other existing user is
    LEGACY_UNCLASSIFIED, which is never eligible for authentication (fail closed). Each classification is audited.
    """
    now = _ts(datetime.now(timezone.utc))
    with _transaction(connection):
        connection.execute("ALTER TABLE users ADD COLUMN kind TEXT NOT NULL DEFAULT 'LEGACY_UNCLASSIFIED' "
                           f"CHECK (kind IN ({_KINDS}))")
        for user_id, organization_id in connection.execute(
                "SELECT user_id, organization_id FROM users ORDER BY rowid").fetchall():
            if user_id == LOCAL_USER_ID and organization_id == LOCAL_ORGANIZATION_ID:
                kind = UserKind.LOCAL_DEVELOPMENT
            elif user_id == seed_user_id(organization_id):
                kind = UserKind.SYSTEM_SEED
            else:
                kind = UserKind.LEGACY_UNCLASSIFIED
            connection.execute("UPDATE users SET kind = ? WHERE user_id = ?", (kind.value, user_id))
        for statement in _V2_STATEMENTS:
            connection.execute(statement)
        for user_id, kind in connection.execute("SELECT user_id, kind FROM users ORDER BY rowid").fetchall():
            connection.execute("INSERT INTO identity_audit (occurred_at, event_type, user_id, detail) "
                               "VALUES (?, 'KIND_CLASSIFIED_BY_MIGRATION', ?, ?)",
                               (now, user_id, f"V1 -> V2 identity migration classified this user {kind}"))
        connection.execute("UPDATE identity_meta SET value = '2' WHERE key = 'schema_version'")


def open_identity_store(path: str | Path) -> sqlite3.Connection:
    """Open (creating if new) an identity store, migrating a V1 store. The caller closes it."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0:
            with _transaction(connection):
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("INSERT INTO identity_meta VALUES ('engine', 'BAEC_IDENTITY'), ('schema_version', ?)",
                                   (str(SCHEMA_VERSION),))
        meta = dict(connection.execute("SELECT key, value FROM identity_meta").fetchall())
        if meta == {"engine": "BAEC_IDENTITY", "schema_version": "1"}:
            _migrate_v1_to_v2(connection)
            meta = dict(connection.execute("SELECT key, value FROM identity_meta").fetchall())
        if meta != {"engine": "BAEC_IDENTITY", "schema_version": str(SCHEMA_VERSION)}:
            raise IdentityError(f"unsupported identity store: {meta}")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


@dataclass(frozen=True)
class IdentityAuditEvent:
    seq: int
    occurred_at: datetime
    event_type: str
    user_id: str
    detail: str


class IdentityStore:
    """Reads and writes over one open identity-store connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection

    def organization(self, organization_id: str) -> Organization | None:
        row = self.db.execute("SELECT organization_id, name, created_at FROM organizations WHERE organization_id = ?",
                              (organization_id,)).fetchone()
        return None if row is None else Organization(row[0], row[1], datetime.fromisoformat(row[2]))

    def user(self, user_id: str) -> User | None:
        row = self.db.execute("SELECT user_id, organization_id, display_name, created_at, active, kind FROM users "
                              "WHERE user_id = ?", (user_id,)).fetchone()
        return None if row is None else User(row[0], row[1], row[2], datetime.fromisoformat(row[3]), bool(row[4]),
                                             UserKind(row[5]))

    def users(self, organization_id: str) -> list[User]:
        """Every user the organization has ever had (users are never deleted), in creation order."""
        ids = self.db.execute("SELECT user_id FROM users WHERE organization_id = ? ORDER BY rowid",
                              (organization_id,)).fetchall()
        return [self.user(r[0]) for r in ids]

    def audit(self, user_id: str | None = None) -> list[IdentityAuditEvent]:
        sql, args = "SELECT seq, occurred_at, event_type, user_id, detail FROM identity_audit", ()
        if user_id is not None:
            sql, args = sql + " WHERE user_id = ?", (user_id,)
        return [IdentityAuditEvent(r[0], datetime.fromisoformat(r[1]), r[2], r[3], r[4])
                for r in self.db.execute(sql + " ORDER BY seq", args).fetchall()]

    def _audit(self, at: datetime, event_type: str, user_id: str, detail: str) -> None:
        self.db.execute("INSERT INTO identity_audit (occurred_at, event_type, user_id, detail) VALUES (?, ?, ?, ?)",
                        (_ts(at), event_type, user_id, detail))

    def ensure_organization(self, organization_id: str, name: str, created_at: datetime) -> Organization:
        """Create the organization if it does not exist. Idempotent; an existing organization is never changed."""
        if not organization_id.strip() or not name.strip():
            raise IdentityError("an organization needs an id and a name")
        self.db.execute("INSERT OR IGNORE INTO organizations VALUES (?, ?, ?)",
                        (organization_id, name.strip(), _ts(created_at)))
        return self.organization(organization_id)

    def ensure_user(self, user_id: str, organization_id: str, display_name: str, created_at: datetime, *,
                    kind: UserKind) -> User:
        """Create the user if it does not exist. Idempotent; refuses if the id belongs to another organization or
        was created with a different kind."""
        if not isinstance(kind, UserKind):
            raise IdentityError("a user needs an explicit kind")
        if self.organization(organization_id) is None:
            raise IdentityError(f"unknown organization {organization_id!r}")
        existing = self.user(user_id)
        if existing is not None:
            if existing.organization_id != organization_id:
                raise IdentityError(f"user {user_id!r} belongs to a different organization")
            if existing.kind is not kind:
                raise IdentityError(f"user {user_id!r} is a {existing.kind.value} user, not {kind.value}")
            return existing
        try:
            self.db.execute("INSERT INTO users VALUES (?, ?, ?, ?, 1, ?)",
                            (user_id, organization_id, _display_name(display_name), _ts(created_at), kind.value))
        except sqlite3.IntegrityError as error:
            raise IdentityError("That name is already used by someone else in this organization.") from error
        self._audit(created_at, "USER_CREATED", user_id, f"{kind.value} user created as {display_name.strip()!r}")
        return self.user(user_id)

    def rename_user(self, user_id: str, display_name: str, *, at: datetime) -> User:
        """Change a user's display name. The user_id and organization never change, and records made under the old
        name keep it: each stores its own display-name snapshot. The rename itself is audited."""
        before = self.user(user_id)
        if before is None:
            raise IdentityError(f"unknown user {user_id!r}")
        name = _display_name(display_name)
        if name == before.display_name:
            return before
        try:
            self.db.execute("UPDATE users SET display_name = ? WHERE user_id = ?", (name, user_id))
        except sqlite3.IntegrityError as error:
            raise IdentityError("That name is already used by someone else in this organization.") from error
        self._audit(at, "USER_RENAMED", user_id, f"{before.display_name!r} -> {name!r}")
        return self.user(user_id)

    def set_active(self, user_id: str, active: bool, *, at: datetime) -> None:
        before = self.user(user_id)
        if before is None:
            raise IdentityError(f"unknown user {user_id!r}")
        if before.active == active:
            return
        self.db.execute("UPDATE users SET active = ? WHERE user_id = ?", (int(active), user_id))
        self._audit(at, "USER_REACTIVATED" if active else "USER_DEACTIVATED", user_id,
                    "reactivated" if active else "deactivated")

    def principal(self, user_id: str) -> Principal:
        """The principal for an active user, read from stored records. Fails closed."""
        user = self.user(user_id)
        if user is None or not user.active or self.organization(user.organization_id) is None:
            raise IdentityError("no active user for this principal")
        return Principal(user.user_id, user.organization_id, user.display_name)

    def verify(self, principal: Principal, *, require_authentication_eligible: bool = False) -> Principal:
        """The current principal for `principal`'s identity, or IdentityError. Fails closed.

        The identity is the user_id and organization_id: the user must exist, be active, and belong to that
        organization, which must exist. The display name is not compared, so a rename never invalidates a
        legitimate user; the returned principal carries the user's current display name. With
        `require_authentication_eligible`, the user must also be a HUMAN user.
        """
        if not isinstance(principal, Principal):
            raise IdentityError("the principal does not match an active user")
        user = self.user(principal.user_id)
        if user is None or user.organization_id != principal.organization_id:
            raise IdentityError("the principal does not match an active user")
        current = self.principal(principal.user_id)
        if require_authentication_eligible and not user.authentication_eligible:
            raise IdentityError("this user is not eligible for interactive authentication")
        return current

    def authenticated_principal(self, user_id: str) -> Principal:
        """The principal for a user an authentication provider has signed in. Only an active HUMAN user qualifies;
        local-development, seed, public-demo, and unclassified identities are refused whatever the caller claims."""
        user = self.user(user_id)
        if user is None or not user.authentication_eligible:
            raise IdentityError("this user is not eligible for interactive authentication")
        return self.principal(user_id)


def _display_name(name: str) -> str:
    if not name or not name.strip():
        raise IdentityError("Enter a name.")
    return name.strip()


@contextmanager
def identity_store(path: str | Path) -> Iterator[IdentityStore]:
    connection = open_identity_store(path)
    try:
        yield IdentityStore(connection)
    finally:
        connection.close()


def provision(path: str | Path, *, organization_id: str, organization_name: str, user_id: str, display_name: str,
              kind: UserKind, now: datetime) -> Principal:
    """Create (if needed) one organization and one user of `kind` in it, and return that user's principal.
    Idempotent."""
    with identity_store(path) as store, _transaction(store.db):
        store.ensure_organization(organization_id, organization_name, now)
        store.ensure_user(user_id, organization_id, display_name, now, kind=kind)
        return store.principal(user_id)


def local_development_principal(path: str | Path, *, now: datetime, legacy_display_name: str | None = None) -> Principal:
    """The deterministic local-development principal. `legacy_display_name` names the user on first creation only
    (the V0.3 "Your name" preference), so existing history keeps the same actor label."""
    return provision(path, organization_id=LOCAL_ORGANIZATION_ID, organization_name=LOCAL_ORGANIZATION_NAME,
                     user_id=LOCAL_USER_ID, display_name=legacy_display_name or LOCAL_USER_DEFAULT_NAME,
                     kind=UserKind.LOCAL_DEVELOPMENT, now=now)


def seed_principal(path: str | Path, organization_id: str, *, now: datetime) -> Principal:
    """The synthetic-seed user of one existing organization."""
    with identity_store(path) as store, _transaction(store.db):
        return ensure_seed_principal(store, organization_id, now=now)


def ensure_seed_principal(store: IdentityStore, organization_id: str, *, now: datetime) -> Principal:
    """seed_principal on an open identity store (SQLite inside its transaction, or a PostgreSQL tenant session)."""
    if store.organization(organization_id) is None:
        raise IdentityError(f"unknown organization {organization_id!r}")
    store.ensure_user(seed_user_id(organization_id), organization_id, SEED_DISPLAY_NAME, now,
                      kind=UserKind.SYSTEM_SEED)
    return store.principal(seed_user_id(organization_id))
