"""Stores and reloads Phase 7 AI_DRAFT proposals and human review history (schema version 7).

Persistence substrate for docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §13.

Rules this module follows:

* It is persistence only. A stored proposal is an immutable AI_DRAFT snapshot:
  model-derived draft content awaiting human review, never authority. A stored
  review revision is an immutable persistence representation of human review
  content. It is not a validated review (the application review object belongs
  to a later increment) and nothing here treats it as executable.
* Nothing here creates a BAEC, an authorization, a grant, a confirmation, or a
  consumption, or changes any account state. The grant, supersession,
  confirmation-link, and consumption tables exist in the schema, guarded by
  their triggers, but this module writes none of them: issuing and executing
  grants belong to later increments.
* Every table is append-only. Rows are inserted with plain INSERT; a duplicate
  identity fails and never replaces anything.
* Content is stored exactly as given: canonical JSON (baec-canonical-json/v1
  rules, no floats) whose SHA-256 is the stored digest, and whose binding
  fields agree with the row. Every load re-verifies all of it and raises
  PersistenceIntegrityError rather than returning anything corrupt.
* The store never reads the clock; timestamps are supplied by callers.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterator

from baec_app.data.database import (
    BRIDGE_MAPPING_VERSION,
    BRIDGE_NORMALIZATION_VALIDATION_VERSION,
    BRIDGE_PROPOSAL_CONTENT_VERSION,
    BRIDGE_PROPOSAL_ORIGIN,
    BRIDGE_REVIEW_CONTENT_VERSION,
    PersistenceError,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    require_current_schema,
    transaction,
)
from baec_app.data.records import RecordValidationError
from baec_app.data.repository import decode_datetime, encode_datetime

class ReviewDecision(Enum):
    """A human review decision: acceptance of one revision, or terminal rejection of the proposal."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


# --- validation helpers ------------------------------------------------------------------


def sha256_text(text: str) -> str:
    """Lowercase hexadecimal SHA-256 of the exact UTF-8 bytes of text."""
    if type(text) is not str:
        raise RecordValidationError("only text can be digested")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise RecordValidationError(f"{field} must be a non-blank string")


def _prefixed(value: object, prefix: str, field: str) -> None:
    _text(value, field)
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise RecordValidationError(f"{field} must start with {prefix!r}")


def _digest(value: object, field: str) -> None:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RecordValidationError(f"{field} must be a lowercase hexadecimal SHA-256 digest")


def _aware(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise RecordValidationError(f"{field} must be a timezone-aware datetime")


def _refuse_float(text: str) -> object:
    raise RecordValidationError("canonical content holds no floating-point numbers")


def _refuse_constant(text: str) -> object:
    raise RecordValidationError("canonical content holds only finite numbers")


def _canonical_object(content: object, digest: object, field: str) -> dict:
    """The parsed content, which must be the canonical JSON text of an object whose SHA-256 is digest."""
    if type(content) is not str:
        raise RecordValidationError(f"{field} must be text")
    _digest(digest, f"{field} digest")
    try:
        value = json.loads(content, parse_float=_refuse_float, parse_constant=_refuse_constant)
    except RecordValidationError:
        raise
    except ValueError as error:
        raise RecordValidationError(f"{field} is not valid JSON") from error
    if type(value) is not dict:
        raise RecordValidationError(f"{field} must be a JSON object")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if canonical != content:
        raise RecordValidationError(f"{field} is not in canonical form")
    if sha256_text(content) != digest:
        raise RecordValidationError(f"{field} does not match its digest")
    return value


def _require_equal(content: dict, key: str, expected: object, field: str) -> None:
    if key not in content or content[key] != expected or type(content[key]) is not type(expected):
        raise RecordValidationError(f"{field}.{key} disagrees with the record")


# --- records -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AiProposalRecord:
    """One immutable AI_DRAFT proposal snapshot of one stored artifact.

    artifact_id is the durable provenance anchor; the run, model, prompt, and
    validator are reconstructed through it, not copied here. The content
    snapshot is stored because re-running a mapper later is not audit evidence.
    """

    proposal_id: str
    artifact_id: str
    artifact_digest: str
    account_id: str
    interaction_id: str
    content: str
    proposal_digest: str
    created_by: str
    created_at: datetime
    origin: str = BRIDGE_PROPOSAL_ORIGIN
    mapping_version: str = BRIDGE_MAPPING_VERSION
    content_version: str = BRIDGE_PROPOSAL_CONTENT_VERSION

    def __post_init__(self) -> None:
        _prefixed(self.proposal_id, "aiprop_", "AiProposalRecord.proposal_id")
        _text(self.artifact_id, "AiProposalRecord.artifact_id")
        _digest(self.artifact_digest, "AiProposalRecord.artifact_digest")
        _text(self.account_id, "AiProposalRecord.account_id")
        _text(self.interaction_id, "AiProposalRecord.interaction_id")
        _text(self.created_by, "AiProposalRecord.created_by")
        _aware(self.created_at, "AiProposalRecord.created_at")
        if self.origin != BRIDGE_PROPOSAL_ORIGIN:
            raise RecordValidationError(f"a stored proposal's origin is always {BRIDGE_PROPOSAL_ORIGIN}")
        if self.mapping_version != BRIDGE_MAPPING_VERSION:
            raise RecordValidationError(f"mapping_version must be {BRIDGE_MAPPING_VERSION}")
        if self.content_version != BRIDGE_PROPOSAL_CONTENT_VERSION:
            raise RecordValidationError(f"content_version must be {BRIDGE_PROPOSAL_CONTENT_VERSION}")
        content = _canonical_object(self.content, self.proposal_digest, "AiProposalRecord.content")
        for key, expected in (("content_version", self.content_version), ("mapping_version", self.mapping_version),
                              ("origin", self.origin), ("account_id", self.account_id),
                              ("interaction_id", self.interaction_id)):
            _require_equal(content, key, expected, "AiProposalRecord.content")
        artifact = content.get("artifact")
        if type(artifact) is not dict:
            raise RecordValidationError("AiProposalRecord.content.artifact must be an object")
        _require_equal(artifact, "artifact_id", self.artifact_id, "AiProposalRecord.content.artifact")
        _require_equal(artifact, "artifact_digest", self.artifact_digest, "AiProposalRecord.content.artifact")


@dataclass(frozen=True)
class ReviewRevisionRecord:
    """One immutable human review revision: a persistence representation, not a validated review.

    The content records later human decisions (selected verbatim evidence, the
    human-asserted provenance of each selection, four explicit criterion
    findings, an explicit stringency decision, buyer_role None, and the final
    normalizations). This record checks only that the content is canonical,
    matches its digest, and is bound to its proposal, revision chain, and
    versions; validating the review itself belongs to the application layer.
    """

    review_revision_id: str
    proposal_id: str
    proposal_digest: str
    account_id: str
    interaction_id: str
    artifact_id: str
    revision_number: int
    previous_revision_id: str | None
    content: str
    review_content_digest: str
    actor_label: str
    created_at: datetime
    review_content_version: str = BRIDGE_REVIEW_CONTENT_VERSION
    normalization_validation_version: str = BRIDGE_NORMALIZATION_VALIDATION_VERSION

    def __post_init__(self) -> None:
        _prefixed(self.review_revision_id, "aireview_", "ReviewRevisionRecord.review_revision_id")
        _prefixed(self.proposal_id, "aiprop_", "ReviewRevisionRecord.proposal_id")
        _digest(self.proposal_digest, "ReviewRevisionRecord.proposal_digest")
        _text(self.account_id, "ReviewRevisionRecord.account_id")
        _text(self.interaction_id, "ReviewRevisionRecord.interaction_id")
        _text(self.artifact_id, "ReviewRevisionRecord.artifact_id")
        if type(self.revision_number) is not int or self.revision_number < 1:
            raise RecordValidationError("ReviewRevisionRecord.revision_number must be a positive integer")
        if self.revision_number == 1:
            if self.previous_revision_id is not None:
                raise RecordValidationError("revision 1 has no previous revision")
        else:
            _prefixed(self.previous_revision_id, "aireview_", "ReviewRevisionRecord.previous_revision_id")
        _text(self.actor_label, "ReviewRevisionRecord.actor_label")
        _aware(self.created_at, "ReviewRevisionRecord.created_at")
        if self.review_content_version != BRIDGE_REVIEW_CONTENT_VERSION:
            raise RecordValidationError(f"review_content_version must be {BRIDGE_REVIEW_CONTENT_VERSION}")
        if self.normalization_validation_version != BRIDGE_NORMALIZATION_VALIDATION_VERSION:
            raise RecordValidationError(
                f"normalization_validation_version must be {BRIDGE_NORMALIZATION_VALIDATION_VERSION}")
        content = _canonical_object(self.content, self.review_content_digest, "ReviewRevisionRecord.content")
        for key, expected in (
            ("review_content_version", self.review_content_version),
            ("normalization_validation_version", self.normalization_validation_version),
            ("proposal_id", self.proposal_id), ("proposal_digest", self.proposal_digest),
            ("account_id", self.account_id), ("interaction_id", self.interaction_id),
            ("artifact_id", self.artifact_id), ("revision_number", self.revision_number),
        ):
            _require_equal(content, key, expected, "ReviewRevisionRecord.content")
        if content.get("previous_revision_id", ...) != self.previous_revision_id:
            raise RecordValidationError("ReviewRevisionRecord.content.previous_revision_id disagrees with the record")
        # Representational authority is deferred: a Phase 7 review never establishes a buyer role (RC-19).
        if "buyer_role" not in content or content["buyer_role"] is not None:
            raise RecordValidationError("ReviewRevisionRecord.content.buyer_role must be null in Phase 7")


@dataclass(frozen=True)
class ReviewDecisionRecord:
    """One immutable human review decision. A rejection is audit history and authorizes nothing."""

    decision_id: str
    proposal_id: str
    review_revision_id: str | None
    account_id: str
    decision: ReviewDecision
    actor_label: str
    decided_at: datetime

    def __post_init__(self) -> None:
        _prefixed(self.decision_id, "aidecision_", "ReviewDecisionRecord.decision_id")
        _prefixed(self.proposal_id, "aiprop_", "ReviewDecisionRecord.proposal_id")
        if type(self.decision) is not ReviewDecision:
            raise RecordValidationError("ReviewDecisionRecord.decision must be a ReviewDecision")
        if self.review_revision_id is None:
            if self.decision is ReviewDecision.ACCEPTED:
                raise RecordValidationError("an acceptance names the revision it accepts")
        else:
            _prefixed(self.review_revision_id, "aireview_", "ReviewDecisionRecord.review_revision_id")
        _text(self.account_id, "ReviewDecisionRecord.account_id")
        _text(self.actor_label, "ReviewDecisionRecord.actor_label")
        _aware(self.decided_at, "ReviewDecisionRecord.decided_at")


_PROPOSAL_COLUMNS = ("proposal_id", "artifact_id", "artifact_digest", "account_id", "interaction_id", "content",
                     "proposal_digest", "created_by", "created_at", "origin", "mapping_version", "content_version")
_REVISION_COLUMNS = ("review_revision_id", "proposal_id", "proposal_digest", "account_id", "interaction_id",
                     "artifact_id", "revision_number", "previous_revision_id", "content", "review_content_digest",
                     "actor_label", "created_at", "review_content_version", "normalization_validation_version")
_DECISION_COLUMNS = ("decision_id", "proposal_id", "review_revision_id", "account_id", "decision", "actor_label",
                     "decided_at")


@contextmanager
def _fail_closed(what: str) -> Iterator[None]:
    """Turn any failure while rebuilding a record into PersistenceIntegrityError."""
    try:
        yield
    except (PersistenceIntegrityError, RepositoryNotFoundError):
        raise
    except (RecordValidationError, ValueError, TypeError, KeyError) as error:
        raise PersistenceIntegrityError(f"stored {what} is corrupt or contradictory: {error}") from error


class ProposalBridgeStore:
    """SQL for AI_DRAFT proposals, review revisions, and review decisions. No domain or grant writes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if type(connection) is not sqlite3.Connection:
            raise PersistenceError("ProposalBridgeStore requires a sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise PersistenceError("ProposalBridgeStore requires foreign key enforcement")
        require_current_schema(connection)
        self._db = connection

    @contextmanager
    def _write(self) -> Iterator[None]:
        try:
            with transaction(self._db):
                yield
        except sqlite3.IntegrityError as error:
            raise RepositoryConflictError(f"the database refused the write: {error}") from error

    def _insert(self, table: str, columns: tuple[str, ...], values: tuple) -> None:
        placeholders = ", ".join("?" for _ in columns)
        with self._write():
            self._db.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", values)

    # --- writes -------------------------------------------------------------------

    def add_proposal(self, record: AiProposalRecord) -> None:
        """Insert one immutable AI_DRAFT snapshot. The database binds it to an eligible artifact of its account."""
        if type(record) is not AiProposalRecord:
            raise RecordValidationError("add_proposal requires an AiProposalRecord")
        self._insert("ai_proposals", _PROPOSAL_COLUMNS, tuple(
            encode_datetime(record.created_at) if column == "created_at" else getattr(record, column)
            for column in _PROPOSAL_COLUMNS
        ))

    def add_revision(self, record: ReviewRevisionRecord) -> None:
        """Insert the next immutable review revision. Any unretired grant of the proposal is superseded."""
        if type(record) is not ReviewRevisionRecord:
            raise RecordValidationError("add_revision requires a ReviewRevisionRecord")
        self._insert("ai_proposal_review_revisions", _REVISION_COLUMNS, tuple(
            encode_datetime(record.created_at) if column == "created_at" else getattr(record, column)
            for column in _REVISION_COLUMNS
        ))

    def add_decision(self, record: ReviewDecisionRecord) -> None:
        """Insert one immutable review decision. A rejection is terminal and changes nothing else."""
        if type(record) is not ReviewDecisionRecord:
            raise RecordValidationError("add_decision requires a ReviewDecisionRecord")
        values = []
        for column in _DECISION_COLUMNS:
            value = getattr(record, column)
            values.append(encode_datetime(value) if column == "decided_at"
                          else value.value if column == "decision" else value)
        self._insert("ai_proposal_review_decisions", _DECISION_COLUMNS, tuple(values))

    def add_accepted_revision(self, revision: ReviewRevisionRecord, decision: ReviewDecisionRecord) -> None:
        """Insert one review revision and its ACCEPTED decision in one transaction: both, or neither (Phase 7E).

        ACCEPTED records only that a human finalized this revision. It confirms no BAEC and authorizes nothing.
        """
        if type(revision) is not ReviewRevisionRecord or type(decision) is not ReviewDecisionRecord:
            raise RecordValidationError("add_accepted_revision requires a ReviewRevisionRecord and a ReviewDecisionRecord")
        if decision.decision is not ReviewDecision.ACCEPTED or decision.review_revision_id != revision.review_revision_id:
            raise RecordValidationError("the decision must accept exactly this revision")
        if (decision.proposal_id, decision.account_id) != (revision.proposal_id, revision.account_id):
            raise RecordValidationError("the decision must belong to the revision's proposal and account")
        revision_values = tuple(encode_datetime(revision.created_at) if column == "created_at"
                                else getattr(revision, column) for column in _REVISION_COLUMNS)
        decision_values = tuple(encode_datetime(decision.decided_at) if column == "decided_at"
                                else decision.decision.value if column == "decision" else getattr(decision, column)
                                for column in _DECISION_COLUMNS)
        with self._write():
            for table, columns, values in (("ai_proposal_review_revisions", _REVISION_COLUMNS, revision_values),
                                           ("ai_proposal_review_decisions", _DECISION_COLUMNS, decision_values)):
                self._db.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                                 values)

    # --- reads --------------------------------------------------------------------

    def list_proposals(self) -> tuple[AiProposalRecord, ...]:
        """Every stored AI_DRAFT proposal, oldest first, each re-verified on load (Phase 7E)."""
        ids = self._db.execute("SELECT proposal_id FROM ai_proposals ORDER BY rowid").fetchall()
        return tuple(self.get_proposal(row[0]) for row in ids)

    def get_proposal(self, proposal_id: str) -> AiProposalRecord:
        row = self._db.execute(
            f"SELECT {', '.join(_PROPOSAL_COLUMNS)} FROM ai_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"AI proposal {proposal_id!r} does not exist")
        with _fail_closed(f"AI proposal {proposal_id!r}"):
            values = dict(zip(_PROPOSAL_COLUMNS, row))
            values["created_at"] = decode_datetime(values["created_at"])
            return AiProposalRecord(**values)

    def get_revision(self, review_revision_id: str) -> ReviewRevisionRecord:
        row = self._db.execute(
            f"SELECT {', '.join(_REVISION_COLUMNS)} FROM ai_proposal_review_revisions WHERE review_revision_id = ?",
            (review_revision_id,),
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"review revision {review_revision_id!r} does not exist")
        with _fail_closed(f"review revision {review_revision_id!r}"):
            values = dict(zip(_REVISION_COLUMNS, row))
            values["created_at"] = decode_datetime(values["created_at"])
            record = ReviewRevisionRecord(**values)
            proposal = self.get_proposal(record.proposal_id)
            if (proposal.proposal_digest, proposal.account_id, proposal.interaction_id, proposal.artifact_id) != (
                    record.proposal_digest, record.account_id, record.interaction_id, record.artifact_id):
                raise ValueError("the revision is not bound to its proposal")
            return record

    def list_revisions(self, proposal_id: str) -> tuple[ReviewRevisionRecord, ...]:
        """Every revision of the proposal, in revision order; the chain is re-verified."""
        self.get_proposal(proposal_id)
        ids = self._db.execute(
            "SELECT review_revision_id FROM ai_proposal_review_revisions WHERE proposal_id = ? "
            "ORDER BY revision_number", (proposal_id,),
        ).fetchall()
        revisions = tuple(self.get_revision(row[0]) for row in ids)
        with _fail_closed(f"revision chain of {proposal_id!r}"):
            previous = None
            for number, revision in enumerate(revisions, start=1):
                if revision.revision_number != number or revision.previous_revision_id != previous:
                    raise ValueError("the revision chain is broken")
                previous = revision.review_revision_id
        return revisions

    def list_decisions(self, proposal_id: str) -> tuple[ReviewDecisionRecord, ...]:
        self.get_proposal(proposal_id)
        rows = self._db.execute(
            f"SELECT {', '.join(_DECISION_COLUMNS)} FROM ai_proposal_review_decisions WHERE proposal_id = ? "
            "ORDER BY rowid", (proposal_id,),
        ).fetchall()
        decisions = []
        for row in rows:
            with _fail_closed(f"review decision {row[0]!r}"):
                values = dict(zip(_DECISION_COLUMNS, row))
                values["decision"] = ReviewDecision(values["decision"])
                values["decided_at"] = decode_datetime(values["decided_at"])
                decisions.append(ReviewDecisionRecord(**values))
        return tuple(decisions)

    def confirmed_baec_for_artifact(self, artifact_id: str) -> str | None:
        """The BAEC confirmed from this artifact's lineage, or None. A read only (Phase 7D, design E8)."""
        row = self._db.execute(
            "SELECT baec_id FROM ai_proposal_confirmations WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return None if row is None else row[0]

    # --- integrity ----------------------------------------------------------------

    def verify_bridge_integrity(self) -> None:
        """Re-verify every stored bridge row; raise PersistenceIntegrityError on any contradiction.

        Proposals and revisions reload through their records (digests, bindings,
        chains). Grant history must show: each confirmation link paired with
        exactly one consumption and vice versa; each retired grant retired by
        exactly one mechanism; at most one unretired grant per revision and action.
        """
        for (proposal_id,) in self._db.execute("SELECT proposal_id FROM ai_proposals ORDER BY rowid").fetchall():
            self.list_revisions(proposal_id)
            self.list_decisions(proposal_id)
        checks = {
            "a confirmation link without its consumption, or a consumption without its link": """
                SELECT (SELECT COUNT(*) FROM ai_proposal_confirmations AS link WHERE NOT EXISTS (
                            SELECT 1 FROM human_authorization_grant_consumptions
                            WHERE grant_id = link.grant_id AND baec_id = link.baec_id))
                     + (SELECT COUNT(*) FROM human_authorization_grant_consumptions AS used WHERE NOT EXISTS (
                            SELECT 1 FROM ai_proposal_confirmations
                            WHERE grant_id = used.grant_id AND baec_id = used.baec_id))""",
            "a grant retired by more than one mechanism": """
                SELECT COUNT(*) FROM human_authorization_grants AS grant_row
                WHERE (EXISTS (SELECT 1 FROM human_authorization_grant_consumptions WHERE grant_id = grant_row.grant_id)
                       + EXISTS (SELECT 1 FROM human_authorization_grant_supersessions WHERE grant_id = grant_row.grant_id)
                       + EXISTS (SELECT 1 FROM human_authorization_grants WHERE supersedes_grant_id = grant_row.grant_id)
                      ) > 1""",
            "more than one unretired grant for one revision and action": """
                SELECT COUNT(*) FROM (
                    SELECT review_revision_id, action FROM human_authorization_grants AS grant_row
                    WHERE NOT EXISTS (SELECT 1 FROM human_authorization_grant_consumptions WHERE grant_id = grant_row.grant_id)
                        AND NOT EXISTS (SELECT 1 FROM human_authorization_grant_supersessions WHERE grant_id = grant_row.grant_id)
                        AND NOT EXISTS (SELECT 1 FROM human_authorization_grants WHERE supersedes_grant_id = grant_row.grant_id)
                    GROUP BY review_revision_id, action HAVING COUNT(*) > 1)""",
        }
        for problem, sql in checks.items():
            if self._db.execute(sql).fetchone()[0] != 0:
                raise PersistenceIntegrityError(f"stored bridge history is contradictory: {problem}")
