"""Stores and reloads persisted human authorization grants (Phase 7F-B, schema version 7).

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §12-§14. Persistence only. A grant is the persisted
record that the human-interaction path authorized exactly one action, confirm_baec, for one immutable,
ACCEPTED review revision. It expires 15 minutes after issuance (an IMPLEMENTATION setting) and is single-use.

* Grant rows are append-only. Status is never a column: it is derived from the rows that reference a grant
  (a consumption, a supersession, or a successor grant) and, for expiry, from a clock the caller supplies.
  Nothing here reads the clock, and no trigger or index evaluates the current time.
* Two capabilities. GrantExecutionStore (get_grant, lifecycle, record_confirmation) is all the executor holds;
  it cannot insert a grant. AuthorizationGrantStore extends it with add_grant and the per-revision and
  per-proposal reads, for the human-authorization path only.
* add_grant inserts one grant in its own transaction. record_confirmation inserts the confirmation link and the
  consumption inside the caller's open transaction, which must also hold the confirmed BAEC and its
  authorization (design §14); it never commits.
* The actor label is self-asserted in this prototype; nothing here authenticates anyone.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

from baec_app.data.database import (
    BRIDGE_GRANT_ACTION,
    BRIDGE_GRANT_EXECUTOR_SURFACE,
    BRIDGE_GRANT_FORMAT,
    BRIDGE_GRANT_ISSUING_SURFACE,
    BRIDGE_GRANT_TTL_SECONDS,
    PersistenceError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    require_current_schema,
    transaction,
)
from baec_app.data.records import RecordValidationError
from baec_app.data.repository import decode_datetime, encode_datetime

GRANT_ID_PATTERN = re.compile(r"grant_[0-9a-f]{64}")
GRANT_TTL = timedelta(seconds=BRIDGE_GRANT_TTL_SECONDS)
# The binding fields the grant digest covers, in the design's §12.1 order: every binding field except the digest.
GRANT_DIGEST_FIELDS = (
    "grant_id", "action", "account_id", "interaction_id", "artifact_id", "proposal_id", "proposal_digest",
    "review_revision_id", "review_content_digest", "decision_id", "issue_sequence", "supersedes_grant_id", "baec_id",
    "actor_label", "issued_at", "expires_at", "ttl_seconds", "grant_format", "issuing_surface",
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _text(value: object, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise RecordValidationError(f"{field} must be a non-blank string")


def _digest(value: object, field: str) -> None:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RecordValidationError(f"{field} must be a lowercase hexadecimal SHA-256 digest")


@dataclass(frozen=True)
class GrantRecord:
    """One persisted human authorization grant. Use GrantRecord.issue to build one with its digest."""

    grant_id: str
    account_id: str
    interaction_id: str
    artifact_id: str
    proposal_id: str
    proposal_digest: str
    review_revision_id: str
    review_content_digest: str
    decision_id: str
    issue_sequence: int
    supersedes_grant_id: str | None
    baec_id: str
    actor_label: str
    issued_at: datetime
    expires_at: datetime
    grant_digest: str
    action: str = BRIDGE_GRANT_ACTION
    ttl_seconds: int = BRIDGE_GRANT_TTL_SECONDS
    grant_format: str = BRIDGE_GRANT_FORMAT
    issuing_surface: str = BRIDGE_GRANT_ISSUING_SURFACE

    def __post_init__(self) -> None:
        if type(self.grant_id) is not str or not GRANT_ID_PATTERN.fullmatch(self.grant_id):
            raise RecordValidationError("grant_id must be 'grant_' followed by 64 lowercase hexadecimal digits")
        for field in ("account_id", "interaction_id", "artifact_id", "proposal_id", "review_revision_id",
                      "decision_id", "baec_id", "actor_label"):
            _text(getattr(self, field), f"GrantRecord.{field}")
        for field in ("proposal_digest", "review_content_digest", "grant_digest"):
            _digest(getattr(self, field), f"GrantRecord.{field}")
        if type(self.issue_sequence) is not int or self.issue_sequence < 1:
            raise RecordValidationError("issue_sequence must be a positive integer")
        if (self.issue_sequence == 1) != (self.supersedes_grant_id is None):
            raise RecordValidationError("only a re-issued grant names the grant it supersedes")
        for field in ("issued_at", "expires_at"):
            value = getattr(self, field)
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise RecordValidationError(f"GrantRecord.{field} must be a timezone-aware datetime")
        if (self.action, self.ttl_seconds, self.grant_format, self.issuing_surface) != (
                BRIDGE_GRANT_ACTION, BRIDGE_GRANT_TTL_SECONDS, BRIDGE_GRANT_FORMAT, BRIDGE_GRANT_ISSUING_SURFACE):
            raise RecordValidationError("a grant authorizes exactly confirm_baec, for 900 seconds, in the v1 format")

    def binding(self) -> dict:
        """The exact object the grant digest covers (design §12.1)."""
        values = {field: getattr(self, field) for field in GRANT_DIGEST_FIELDS}
        values["issued_at"], values["expires_at"] = encode_datetime(self.issued_at), encode_datetime(self.expires_at)
        return values

    def computed_digest(self) -> str:
        return hashlib.sha256(_canonical(self.binding()).encode("utf-8")).hexdigest()

    def binding_is_intact(self) -> bool:
        """The stored digest equals the digest of the stored binding, and the expiry is exactly 15 minutes."""
        return self.grant_digest == self.computed_digest() and self.expires_at - self.issued_at == GRANT_TTL

    @classmethod
    def issue(cls, **fields) -> GrantRecord:
        """A new grant: expires_at = issued_at + 15 minutes, and the digest computed over the binding."""
        provisional = cls(**fields, expires_at=fields["issued_at"] + GRANT_TTL, grant_digest="0" * 64)
        return cls(**fields, expires_at=provisional.expires_at, grant_digest=provisional.computed_digest())


@dataclass(frozen=True)
class GrantLifecycle:
    """What has happened to a grant, from append-only rows. Expiry is not here: it needs a clock."""

    consumed_baec_id: str | None
    supersession_reason: str | None
    successor_grant_id: str | None

    @property
    def superseded(self) -> bool:
        return self.supersession_reason is not None or self.successor_grant_id is not None


_COLUMNS = ("grant_id", "account_id", "interaction_id", "artifact_id", "proposal_id", "proposal_digest",
            "review_revision_id", "review_content_digest", "decision_id", "issue_sequence", "supersedes_grant_id",
            "baec_id", "actor_label", "issued_at", "expires_at", "grant_digest", "action", "ttl_seconds",
            "grant_format", "issuing_surface")


class GrantExecutionStore:
    """The execution-only grant capability: read one grant and its lifecycle, and record its confirmation.

    It has no way to insert, re-issue, or supersede a grant. ConfirmationExecutionService, and so the Phase 7G
    MCP write server, holds only this.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        if type(connection) is not sqlite3.Connection:
            raise PersistenceError(f"{type(self).__name__} requires a sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise PersistenceError(f"{type(self).__name__} requires foreign key enforcement")
        require_current_schema(connection)
        self._db = connection

    # --- the one write: inside the caller's transaction ------------------------------------------

    def record_confirmation(self, grant: GrantRecord, *, authorization_id: int, consumed_at: datetime) -> None:
        """Insert the confirmation link and the consumption inside the caller's open transaction. Never commits."""
        if not self._db.in_transaction:
            raise PersistenceError("record_confirmation must run inside the caller's open transaction")
        self._db.execute(
            "INSERT INTO ai_proposal_confirmations (baec_id, artifact_id, proposal_id, review_revision_id, grant_id, "
            "authorization_id, account_id, interaction_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (grant.baec_id, grant.artifact_id, grant.proposal_id, grant.review_revision_id, grant.grant_id,
             authorization_id, grant.account_id, grant.interaction_id))
        self._db.execute(
            "INSERT INTO human_authorization_grant_consumptions (grant_id, baec_id, executor_surface, consumed_at) "
            "VALUES (?, ?, ?, ?)",
            (grant.grant_id, grant.baec_id, BRIDGE_GRANT_EXECUTOR_SURFACE, encode_datetime(consumed_at)))

    # --- reads --------------------------------------------------------------------------------

    def get_grant(self, grant_id: str) -> GrantRecord:
        """The stored grant as stored. Whether its digest still covers its binding is checked by the caller, in order."""
        row = self._db.execute(f"SELECT {', '.join(_COLUMNS)} FROM human_authorization_grants WHERE grant_id = ?",
                               (grant_id,)).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"grant {grant_id!r} does not exist")
        values = dict(zip(_COLUMNS, row))
        values["issued_at"], values["expires_at"] = decode_datetime(values["issued_at"]), decode_datetime(values["expires_at"])
        return GrantRecord(**values)

    def lifecycle(self, grant_id: str) -> GrantLifecycle:
        consumed = self._db.execute("SELECT baec_id FROM human_authorization_grant_consumptions WHERE grant_id = ?",
                                    (grant_id,)).fetchone()
        superseded = self._db.execute("SELECT reason FROM human_authorization_grant_supersessions WHERE grant_id = ?",
                                      (grant_id,)).fetchone()
        successor = self._db.execute("SELECT grant_id FROM human_authorization_grants WHERE supersedes_grant_id = ?",
                                     (grant_id,)).fetchone()
        return GrantLifecycle(consumed[0] if consumed else None, superseded[0] if superseded else None,
                              successor[0] if successor else None)


class AuthorizationGrantStore(GrantExecutionStore):
    """The issuance-capable grant store: everything the execution store has, plus inserting a grant and the
    per-revision and per-proposal reads that issuance and status need. Held only by the human-authorization
    path (ProposalAuthorizationService), never by the executor."""

    @contextmanager
    def _write(self) -> Iterator[None]:
        try:
            with transaction(self._db):
                yield
        except sqlite3.IntegrityError as error:
            raise RepositoryConflictError(f"the database refused the write: {error}") from error

    # --- writes -------------------------------------------------------------------------------

    def add_grant(self, grant: GrantRecord) -> None:
        """Insert one grant. The schema refuses a grant without an ACCEPTED latest revision, or a second live one."""
        if type(grant) is not GrantRecord:
            raise RecordValidationError("add_grant requires a GrantRecord")
        if not grant.binding_is_intact():
            raise RecordValidationError("the grant digest does not cover its binding")
        values = tuple(encode_datetime(getattr(grant, c)) if c in ("issued_at", "expires_at") else getattr(grant, c)
                       for c in _COLUMNS)
        with self._write():
            self._db.execute(f"INSERT INTO human_authorization_grants ({', '.join(_COLUMNS)}) "
                             f"VALUES ({', '.join('?' for _ in _COLUMNS)})", values)

    # --- reads --------------------------------------------------------------------------------

    def grants_for_revision(self, review_revision_id: str) -> tuple[GrantRecord, ...]:
        ids = self._db.execute("SELECT grant_id FROM human_authorization_grants WHERE review_revision_id = ? "
                               "ORDER BY issue_sequence", (review_revision_id,)).fetchall()
        return tuple(self.get_grant(row[0]) for row in ids)

    def grants_for_proposal(self, proposal_id: str) -> tuple[GrantRecord, ...]:
        ids = self._db.execute("SELECT grant_id FROM human_authorization_grants WHERE proposal_id = ? ORDER BY rowid",
                               (proposal_id,)).fetchall()
        return tuple(self.get_grant(row[0]) for row in ids)
