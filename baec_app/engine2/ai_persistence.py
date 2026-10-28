"""Engine 2 AI audit store (Stage G): a SEPARATE, append-only SQLite database for AI_INFERENCE history.

Authority isolation. The authoritative BAEC_ENGINE_2 database holds deterministic and human
authority. This BAEC_ENGINE_2_AI database holds only AI audit history, in four categories:

    INPUT_SNAPSHOT            the exact Stage F CorrespondenceAIInput sent to a model
    UNTRUSTED_MODEL_RESPONSE  the exact text a model returned, before validation
    VALIDATION_RESULT         ACCEPTED or REJECTED, decided only by the Stage F validator
    AI_INFERENCE_PROPOSAL     a CorrespondenceAIProposal that Stage F validation produced

Nothing stored here is a finding, a review, or a correspondence outcome, and persisting it
grants no authority. There are no foreign keys into BAEC_ENGINE_2 or Engine 1; plan, BAEC,
and candidate references are stored as immutable values checked against the Stage F objects
at write time. This module never reads another database to reinterpret an AI input.

Provider/model identity (Stage G v1): an attempt is bound to exactly one (provider, model)
pair, recorded on the attempt. Its response and any accepted proposal must carry the same
pair by exact string equality; there is no alias, equivalence, or newest-model selection.

Stage F stays the only authority on AI proposal semantics. This module verifies storage
integrity and reruns the locked Stage F validator; it never reimplements its rules. It
calls no model, provider, or network; reads no clock (every time is supplied); holds no
credentials; and has no update or delete path. Rows are append-only, enforced by triggers.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from .ai import (
    INPUT_VERSION,
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    CorrespondenceAIInput,
    CorrespondenceAIProposal,
    ModelResponse,
    ProposalRejected,
    canonical_json,
    validate_proposal_output,
)
from .errors import Engine2Error

ENGINE = "BAEC_ENGINE_2_AI"
SCHEMA_VERSION = 1
MINIMUM_SQLITE_VERSION = (3, 38, 0)
VALIDATION_STATUSES = ("ACCEPTED", "REJECTED")
AUDIT_EVENT_TYPES = ("AI_INPUT_RECORDED", "AI_ATTEMPT_RECORDED", "AI_RESPONSE_RECORDED",
                     "AI_VALIDATION_ACCEPTED", "AI_VALIDATION_REJECTED", "AI_PROPOSAL_RECORDED")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")


class AIStoreError(Exception):
    """Base class for AI audit store failures."""


class AIStoreSchemaError(AIStoreError):
    """Not a BAEC_ENGINE_2_AI schema-version-1 database. Fail closed."""


class AIStoreIntegrityError(AIStoreError):
    """Stored AI history is tampered or inconsistent. Nothing is returned."""


class AIDuplicateError(AIStoreError):
    """An identifier already exists. AI history is never replaced; a retry is a new attempt."""


class AINotFoundError(AIStoreError):
    """A referenced AI audit record does not exist."""


class AIWriteRefused(AIStoreError):
    """A write disagrees with the records already stored or with the supplied objects."""


# --- schema ---------------------------------------------------------------------------------

_PV = "plan_version INTEGER NOT NULL CHECK (plan_version >= 1)"
_SHA = "CHECK (length({c}) = 64 AND {c} NOT GLOB '*[^0-9a-f]*')"

_TABLES = {
    "engine2_ai_meta": """(key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    "ai_inputs": f"""(
        input_digest TEXT PRIMARY KEY {_SHA.format(c="input_digest")},
        data_authority TEXT NOT NULL CHECK (data_authority = 'INPUT_SNAPSHOT'),
        input_version TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        baec_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
        canonical_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (input_digest, monitoring_plan_id, plan_version))""",
    "ai_attempts": f"""(
        attempt_id TEXT PRIMARY KEY,
        input_digest TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        prompt_version TEXT NOT NULL, output_schema_version TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL,
        producer_commit TEXT, provider_request_ref TEXT,
        requested_at TEXT NOT NULL, requested_by TEXT NOT NULL,
        FOREIGN KEY (input_digest, monitoring_plan_id, plan_version)
            REFERENCES ai_inputs (input_digest, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "ai_responses": f"""(
        attempt_id TEXT PRIMARY KEY REFERENCES ai_attempts (attempt_id) ON DELETE RESTRICT,
        data_authority TEXT NOT NULL CHECK (data_authority = 'UNTRUSTED_MODEL_RESPONSE'),
        raw_text TEXT NOT NULL,
        response_sha256 TEXT NOT NULL {_SHA.format(c="response_sha256")},
        canonical_json TEXT,
        provider TEXT NOT NULL, model TEXT NOT NULL, provider_response_ref TEXT,
        received_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (attempt_id, response_sha256))""",
    "ai_validations": f"""(
        attempt_id TEXT PRIMARY KEY,
        data_authority TEXT NOT NULL CHECK (data_authority = 'VALIDATION_RESULT'),
        response_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACCEPTED', 'REJECTED')),
        error_category TEXT, error_summary TEXT,
        validated_at TEXT NOT NULL, validated_by TEXT NOT NULL,
        CHECK ((status = 'ACCEPTED') = (error_category IS NULL AND error_summary IS NULL)),
        UNIQUE (attempt_id, status),
        FOREIGN KEY (attempt_id, response_sha256)
            REFERENCES ai_responses (attempt_id, response_sha256) ON DELETE RESTRICT)""",
    "ai_proposals": f"""(
        artifact_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL UNIQUE,
        validation_status TEXT NOT NULL CHECK (validation_status = 'ACCEPTED'),
        data_authority TEXT NOT NULL CHECK (data_authority = 'AI_INFERENCE_PROPOSAL'),
        origin TEXT NOT NULL CHECK (origin = 'AI_INFERENCE'),
        input_digest TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        baec_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
        prompt_version TEXT NOT NULL, output_schema_version TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        canonical_json TEXT NOT NULL,
        proposal_sha256 TEXT NOT NULL {_SHA.format(c="proposal_sha256")},
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        FOREIGN KEY (attempt_id, validation_status)
            REFERENCES ai_validations (attempt_id, status) ON DELETE RESTRICT,
        FOREIGN KEY (input_digest, monitoring_plan_id, plan_version)
            REFERENCES ai_inputs (input_digest, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "ai_audit_events": f"""(
        event_id INTEGER PRIMARY KEY,
        event_type TEXT NOT NULL CHECK (event_type IN ({", ".join(repr(e) for e in AUDIT_EVENT_TYPES)})),
        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL, actor TEXT NOT NULL,
        details_json TEXT NOT NULL CHECK (json_valid(details_json)))""",
}
_KEYS = {"engine2_ai_meta": ("key",), "ai_inputs": ("input_digest",), "ai_attempts": ("attempt_id",),
         "ai_responses": ("attempt_id",), "ai_validations": ("attempt_id",), "ai_proposals": ("artifact_id",),
         "ai_audit_events": ("event_id",)}
TABLES = tuple(_TABLES)


def _schema_statements() -> list[str]:
    statements = [f"CREATE TABLE {name} {body} STRICT" for name, body in _TABLES.items()]
    for table, key in _KEYS.items():
        for operation in ("UPDATE", "DELETE"):
            statements.append(f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                              f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")
        match = " AND ".join(f"{c} = NEW.{c}" for c in key)
        statements.append(f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
                          f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {match}) "
                          f"BEGIN SELECT RAISE(ABORT, '{table} rows cannot be replaced'); END")
    return statements


def _connect(path: str) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
        raise AIStoreSchemaError("SQLite 3.38.0 or newer is required")
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise AIStoreSchemaError("foreign key enforcement could not be enabled")
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
    except BaseException as error:
        if connection.in_transaction:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error as rollback_error:
                error.add_note(f"rolling back also failed: {rollback_error}")
        raise


def require_ai_schema(connection: sqlite3.Connection) -> None:
    """Fail closed unless this is a BAEC_ENGINE_2_AI schema-version-1 database."""
    try:
        meta = dict(connection.execute("SELECT key, value FROM engine2_ai_meta").fetchall())
    except sqlite3.Error as error:
        raise AIStoreSchemaError("not an Engine 2 AI audit database (no engine2_ai_meta table)") from error
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if meta != {"engine": ENGINE, "schema_version": str(SCHEMA_VERSION)} or version != SCHEMA_VERSION:
        raise AIStoreSchemaError(f"unsupported AI audit database: meta={meta}, user_version={version}")


def open_ai_audit_database(path: str = ":memory:") -> sqlite3.Connection:
    """Open (creating if new and empty) the separate AI audit database. Never the authoritative store."""
    connection = _connect(path)
    try:
        empty = (connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0] == 0
                 and connection.execute("PRAGMA user_version").fetchone()[0] == 0)
        if empty:
            with _transaction(connection):
                for statement in _schema_statements():
                    connection.execute(statement)
                connection.execute("INSERT INTO engine2_ai_meta (key, value) VALUES ('engine', ?), ('schema_version', ?)",
                                   (ENGINE, str(SCHEMA_VERSION)))
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        require_ai_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


# --- helpers -----------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _when(value: object, field: str) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AIWriteRefused(f"{field} must be a timezone-aware datetime supplied by the caller")
    return value.isoformat()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AIWriteRefused(f"{field} must be a non-empty string")
    return value


def _optional(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _insert(connection, table: str, **values) -> None:
    columns, marks = ", ".join(values), ", ".join("?" for _ in values)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))


def _refuse_duplicate(connection, table: str, label: str, **key) -> None:
    where = " AND ".join(f"{c} = ?" for c in key)
    if connection.execute(f"SELECT 1 FROM {table} WHERE {where}", tuple(key.values())).fetchone():
        raise AIDuplicateError(f"{label} {'/'.join(map(str, key.values()))} already exists; AI history is never replaced")


def _audit(connection, event_type: str, entity_type: str, entity_id: str, recorded_at: str, actor: str, **details) -> None:
    _insert_audit_event(connection, event_type, entity_type, entity_id, recorded_at, actor,
                        json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def _insert_audit_event(connection, event_type, entity_type, entity_id, recorded_at, actor, details_json) -> None:
    _insert(connection, "ai_audit_events", event_type=event_type, entity_type=entity_type, entity_id=entity_id,
            recorded_at=recorded_at, actor=actor, details_json=details_json)


def _one(connection, sql: str, args: tuple, label: str):
    row = connection.execute(sql, args).fetchone()
    if row is None:
        raise AINotFoundError(f"{label} not found")
    return row


# --- input snapshots -------------------------------------------------------------------------------


def record_ai_input(connection, ai_input: CorrespondenceAIInput, *, recorded_at: datetime, recorded_by: str) -> None:
    """Persist the exact canonical Stage F input. Duplicate digests are refused."""
    if not isinstance(ai_input, CorrespondenceAIInput):
        raise AIWriteRefused("record_ai_input requires a CorrespondenceAIInput")
    when, actor = _when(recorded_at, "recorded_at"), _text(recorded_by, "recorded_by")
    with _transaction(connection):
        _refuse_duplicate(connection, "ai_inputs", "AI input", input_digest=ai_input.input_digest)
        _insert(connection, "ai_inputs", input_digest=ai_input.input_digest, data_authority="INPUT_SNAPSHOT",
                input_version=ai_input.input_version, monitoring_plan_id=ai_input.monitoring_plan_id,
                plan_version=ai_input.plan_version, baec_id=ai_input.baec_id, candidate_id=ai_input.candidate_id,
                canonical_json=ai_input.canonical_json, recorded_at=when, recorded_by=actor)
        _audit(connection, "AI_INPUT_RECORDED", "AIInput", ai_input.input_digest, when, actor,
               monitoring_plan_id=ai_input.monitoring_plan_id, plan_version=ai_input.plan_version)


def load_ai_input(connection, input_digest: str) -> CorrespondenceAIInput:
    """Rebuild through the Stage F input contract; the digest and every column must agree."""
    row = _one(connection, "SELECT input_version, monitoring_plan_id, plan_version, baec_id, candidate_id, canonical_json "
               "FROM ai_inputs WHERE input_digest = ?", (input_digest,), f"AI input {input_digest}")
    if _sha(row[5]) != input_digest:
        raise AIStoreIntegrityError(f"AI input {input_digest}: stored canonical JSON does not hash to its digest")
    try:
        ai_input = CorrespondenceAIInput(row[1], row[2], row[3], row[4], row[5], input_digest, row[0])
    except (Engine2Error, ValueError, KeyError, TypeError) as error:
        raise AIStoreIntegrityError(f"AI input {input_digest} fails the Stage F input contract: {error}") from error
    return ai_input


# --- attempts and responses ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIAttempt:
    """One AI request initiated against one exact input. Not a review, outcome, or action."""

    attempt_id: str
    input_digest: str
    monitoring_plan_id: str
    plan_version: int
    prompt_version: str
    output_schema_version: str
    provider: str
    model: str
    requested_at: datetime
    requested_by: str
    producer_commit: str | None
    provider_request_ref: str | None


def record_ai_attempt(connection, *, attempt_id: str, input_digest: str, provider: str, model: str,
                      requested_at: datetime, requested_by: str, producer_commit: str | None = None,
                      provider_request_ref: str | None = None) -> None:
    """Record that a request was initiated. Prompt and schema versions are the locked Stage F constants."""
    when, actor = _when(requested_at, "requested_at"), _text(requested_by, "requested_by")
    if producer_commit is not None and (not isinstance(producer_commit, str) or not _COMMIT.fullmatch(producer_commit)):
        raise AIWriteRefused("producer_commit must be a 7 to 40 character lowercase hex commit id")
    ai_input = load_ai_input(connection, input_digest)
    with _transaction(connection):
        _refuse_duplicate(connection, "ai_attempts", "AI attempt", attempt_id=_text(attempt_id, "attempt_id"))
        _insert(connection, "ai_attempts", attempt_id=attempt_id, input_digest=input_digest,
                monitoring_plan_id=ai_input.monitoring_plan_id, plan_version=ai_input.plan_version,
                prompt_version=PROMPT_VERSION, output_schema_version=OUTPUT_SCHEMA_VERSION,
                provider=_text(provider, "provider"), model=_text(model, "model"), producer_commit=producer_commit,
                provider_request_ref=_optional(provider_request_ref, "provider_request_ref"),
                requested_at=when, requested_by=actor)
        _audit(connection, "AI_ATTEMPT_RECORDED", "AIAttempt", attempt_id, when, actor, input_digest=input_digest,
               provider=provider, model=model, prompt_version=PROMPT_VERSION)


def load_ai_attempt(connection, attempt_id: str) -> AIAttempt:
    row = _one(connection, "SELECT input_digest, monitoring_plan_id, plan_version, prompt_version, output_schema_version, "
               "provider, model, requested_at, requested_by, producer_commit, provider_request_ref FROM ai_attempts "
               "WHERE attempt_id = ?", (attempt_id,), f"AI attempt {attempt_id}")
    ai_input = load_ai_input(connection, row[0])
    if (ai_input.monitoring_plan_id, ai_input.plan_version) != (row[1], row[2]):
        raise AIStoreIntegrityError(f"AI attempt {attempt_id} disagrees with its input's plan identity")
    return AIAttempt(attempt_id, row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                     datetime.fromisoformat(row[7]), row[8], row[9], row[10])


def _canonical_or_none(raw_text: str) -> str | None:
    """The canonical JSON form of a strictly parseable response, or None. Never replaces the exact text."""
    def reject_duplicates(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate key")
        return dict(pairs)

    def reject_constant(name):
        raise ValueError(name)

    try:
        return canonical_json(json.loads(raw_text, object_pairs_hook=reject_duplicates, parse_constant=reject_constant))
    except (ValueError, Engine2Error):
        return None


def record_ai_response(connection, attempt_id: str, response: ModelResponse, *, received_at: datetime,
                       recorded_by: str, provider_response_ref: str | None = None) -> None:
    """Persist the exact text Stage F validation will consume. Untrusted; never repaired.

    The response must name exactly the attempt's provider and model, or nothing is written.
    """
    if not isinstance(response, ModelResponse) or type(response.raw_text) is not str:
        raise AIWriteRefused("record_ai_response requires a ModelResponse carrying raw text")
    when, actor = _when(received_at, "received_at"), _text(recorded_by, "recorded_by")
    attempt = load_ai_attempt(connection, attempt_id)
    if (response.provider, response.model) != (attempt.provider, attempt.model):
        raise AIWriteRefused(f"response names ({response.provider!r}, {response.model!r}) but attempt {attempt_id} is "
                             f"bound to ({attempt.provider!r}, {attempt.model!r}); exact equality is required")
    with _transaction(connection):
        _refuse_duplicate(connection, "ai_responses", "AI response for attempt", attempt_id=attempt_id)
        digest = _sha(response.raw_text)
        _insert(connection, "ai_responses", attempt_id=attempt_id, data_authority="UNTRUSTED_MODEL_RESPONSE",
                raw_text=response.raw_text, response_sha256=digest, canonical_json=_canonical_or_none(response.raw_text),
                provider=_text(response.provider, "provider"), model=_text(response.model, "model"),
                provider_response_ref=_optional(provider_response_ref, "provider_response_ref"),
                received_at=when, recorded_by=actor)
        _audit(connection, "AI_RESPONSE_RECORDED", "AIResponse", attempt_id, when, actor, response_sha256=digest)


def load_ai_response(connection, attempt_id: str) -> ModelResponse:
    row = _one(connection, "SELECT raw_text, response_sha256, canonical_json, provider, model FROM ai_responses "
               "WHERE attempt_id = ?", (attempt_id,), f"AI response {attempt_id}")
    if _sha(row[0]) != row[1]:
        raise AIStoreIntegrityError(f"AI response {attempt_id}: stored text does not hash to its digest")
    if row[2] != _canonical_or_none(row[0]):
        raise AIStoreIntegrityError(f"AI response {attempt_id}: canonical form disagrees with the exact text")
    attempt = load_ai_attempt(connection, attempt_id)
    if (row[3], row[4]) != (attempt.provider, attempt.model):
        raise AIStoreIntegrityError(f"AI response {attempt_id}: provider/model disagree with the attempt")
    return ModelResponse(row[0], row[3], row[4])


# --- validation and proposals --------------------------------------------------------------------------


def proposal_payload(proposal: CorrespondenceAIProposal) -> dict:
    """The complete validated proposal as a canonical-JSON-ready dict."""
    def citation(c):
        return {"kind": c.kind, "reference_id": c.reference_id, "excerpt": c.excerpt, "observation_ids": list(c.observation_ids)}
    return {
        "artifact_id": proposal.artifact_id, "origin": proposal.origin.value,
        "monitoring_plan_id": proposal.monitoring_plan_id, "plan_version": proposal.plan_version,
        "baec_id": proposal.baec_id, "candidate_id": proposal.candidate_id, "input_digest": proposal.input_digest,
        "prompt_version": proposal.prompt_version, "output_schema_version": proposal.output_schema_version,
        "provider": proposal.provider, "model": proposal.model, "created_at": proposal.created_at.isoformat(),
        "dimension_proposals": [
            {"dimension": p.dimension.label, "proposal": p.proposal.value,
             "unresolved_basis": None if p.unresolved_basis is None else p.unresolved_basis.value,
             "evidence": [citation(c) for c in p.evidence], "rationale": p.rationale}
            for p in proposal.dimension_proposals],
        "cross_cutting_observations": [
            {"check": c.check.label, "concern": c.concern, "evidence": [citation(e) for e in c.evidence],
             "affected_dimensions": sorted(d.label for d in c.affected_dimensions)}
            for c in proposal.cross_cutting_observations],
        "unresolved_questions": list(proposal.unresolved_questions),
        "prohibited_inference_acknowledgement": proposal.prohibited_inference_acknowledgement,
    }


def proposal_digest(proposal: CorrespondenceAIProposal) -> tuple[str, str]:
    """(canonical JSON, lowercase hex SHA-256 of its UTF-8 bytes)."""
    text = canonical_json(proposal_payload(proposal))
    return text, _sha(text)


@dataclass(frozen=True)
class AIValidationRecord:
    """The immutable validation result for one attempt. Not a finding and not an outcome."""

    attempt_id: str
    status: str
    response_sha256: str
    error_category: str | None
    error_summary: str | None
    proposal: CorrespondenceAIProposal | None


def record_ai_validation(connection, attempt_id: str, *, artifact_id: str, created_at: datetime,
                         validated_at: datetime, validated_by: str) -> AIValidationRecord:
    """Run the locked Stage F validator on the persisted input and response, and record its verdict.

    The caller cannot choose the status. ACCEPTED is written together with the proposal Stage F
    produced, in one transaction; REJECTED is written with no proposal.
    """
    when, actor = _when(validated_at, "validated_at"), _text(validated_by, "validated_by")
    attempt = load_ai_attempt(connection, attempt_id)
    ai_input = load_ai_input(connection, attempt.input_digest)
    response = load_ai_response(connection, attempt_id)  # integrity failure, not a rejection, on identity mismatch
    response_sha = _sha(response.raw_text)
    try:
        proposal = validate_proposal_output(response, ai_input, artifact_id=artifact_id, created_at=created_at)
        error = None
    except ProposalRejected as rejected:
        proposal, error = None, rejected
    if proposal is not None and (proposal.provider, proposal.model) != (attempt.provider, attempt.model):
        raise AIStoreIntegrityError(f"validated proposal for attempt {attempt_id} names a different provider/model")
    with _transaction(connection):
        _refuse_duplicate(connection, "ai_validations", "AI validation for attempt", attempt_id=attempt_id)
        if proposal is None:
            summary = str(error)[:500]
            _insert(connection, "ai_validations", attempt_id=attempt_id, data_authority="VALIDATION_RESULT",
                    response_sha256=response_sha, status="REJECTED", error_category=type(error).__name__,
                    error_summary=summary, validated_at=when, validated_by=actor)
            _audit(connection, "AI_VALIDATION_REJECTED", "AIValidation", attempt_id, when, actor,
                   error_category=type(error).__name__)
            return AIValidationRecord(attempt_id, "REJECTED", response_sha, type(error).__name__, summary, None)
        _refuse_duplicate(connection, "ai_proposals", "AI proposal artifact", artifact_id=artifact_id)
        _insert(connection, "ai_validations", attempt_id=attempt_id, data_authority="VALIDATION_RESULT",
                response_sha256=response_sha, status="ACCEPTED", error_category=None, error_summary=None,
                validated_at=when, validated_by=actor)
        _audit(connection, "AI_VALIDATION_ACCEPTED", "AIValidation", attempt_id, when, actor)
        text, digest = proposal_digest(proposal)
        _insert(connection, "ai_proposals", artifact_id=proposal.artifact_id, attempt_id=attempt_id,
                validation_status="ACCEPTED", data_authority="AI_INFERENCE_PROPOSAL", origin=proposal.origin.value,
                input_digest=proposal.input_digest, monitoring_plan_id=proposal.monitoring_plan_id,
                plan_version=proposal.plan_version, baec_id=proposal.baec_id, candidate_id=proposal.candidate_id,
                prompt_version=proposal.prompt_version, output_schema_version=proposal.output_schema_version,
                provider=proposal.provider, model=proposal.model, created_at=proposal.created_at.isoformat(),
                canonical_json=text, proposal_sha256=digest, recorded_at=when, recorded_by=actor)
        _audit(connection, "AI_PROPOSAL_RECORDED", "AIProposal", proposal.artifact_id, when, actor,
               attempt_id=attempt_id, proposal_sha256=digest)
    return AIValidationRecord(attempt_id, "ACCEPTED", response_sha, None, None, proposal)


def load_ai_proposal(connection, artifact_id: str) -> CorrespondenceAIProposal:
    """Rebuild by rerunning Stage F validation on the persisted input and response; everything must agree."""
    row = _one(connection, "SELECT attempt_id, input_digest, monitoring_plan_id, plan_version, baec_id, candidate_id, "
               "prompt_version, output_schema_version, provider, model, created_at, canonical_json, proposal_sha256, origin "
               "FROM ai_proposals WHERE artifact_id = ?", (artifact_id,), f"AI proposal {artifact_id}")
    if _sha(row[11]) != row[12]:
        raise AIStoreIntegrityError(f"AI proposal {artifact_id}: stored JSON does not hash to its digest")
    status = connection.execute("SELECT status, response_sha256 FROM ai_validations WHERE attempt_id = ?", (row[0],)).fetchone()
    if status is None or status[0] != "ACCEPTED":
        raise AIStoreIntegrityError(f"AI proposal {artifact_id} has no ACCEPTED validation")
    attempt = load_ai_attempt(connection, row[0])
    response = load_ai_response(connection, row[0])
    if _sha(response.raw_text) != status[1]:
        raise AIStoreIntegrityError(f"AI proposal {artifact_id}: validation refers to a different response")
    ai_input = load_ai_input(connection, attempt.input_digest)
    try:
        proposal = validate_proposal_output(response, ai_input, artifact_id=artifact_id,
                                            created_at=datetime.fromisoformat(row[10]))
    except (ProposalRejected, ValueError) as error:
        raise AIStoreIntegrityError(f"AI proposal {artifact_id} no longer passes Stage F validation: {error}") from error
    text, digest = proposal_digest(proposal)
    stored = (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[13])
    rebuilt = (proposal.input_digest, proposal.monitoring_plan_id, proposal.plan_version, proposal.baec_id,
               proposal.candidate_id, proposal.prompt_version, proposal.output_schema_version, proposal.provider,
               proposal.model, proposal.origin.value)
    if (text, digest) != (row[11], row[12]) or stored != rebuilt or attempt.input_digest != row[1]:
        raise AIStoreIntegrityError(f"AI proposal {artifact_id} disagrees with its revalidated artifact")
    if not (attempt.provider == response.provider == proposal.provider
            and attempt.model == response.model == proposal.model):
        raise AIStoreIntegrityError(f"AI proposal {artifact_id}: provider/model disagree across attempt, response, and proposal")
    return proposal


def load_ai_validation(connection, attempt_id: str) -> AIValidationRecord:
    """The validation result, with its proposal (revalidated) when ACCEPTED. Cardinality is checked."""
    row = _one(connection, "SELECT status, response_sha256, error_category, error_summary FROM ai_validations "
               "WHERE attempt_id = ?", (attempt_id,), f"AI validation {attempt_id}")
    if _sha(load_ai_response(connection, attempt_id).raw_text) != row[1]:
        raise AIStoreIntegrityError(f"AI validation {attempt_id} refers to a different response")
    proposals = [r[0] for r in connection.execute("SELECT artifact_id FROM ai_proposals WHERE attempt_id = ?", (attempt_id,))]
    if row[0] == "REJECTED":
        if proposals:
            raise AIStoreIntegrityError(f"REJECTED validation {attempt_id} has a proposal")
        return AIValidationRecord(attempt_id, "REJECTED", row[1], row[2], row[3], None)
    if row[0] != "ACCEPTED" or len(proposals) != 1:
        raise AIStoreIntegrityError(f"ACCEPTED validation {attempt_id} must have exactly one proposal")
    return AIValidationRecord(attempt_id, "ACCEPTED", row[1], None, None, load_ai_proposal(connection, proposals[0]))


def attempt_ids_for_input(connection, input_digest: str) -> list[str]:
    """Every attempt against an input, in recording order. Retries never replace earlier attempts."""
    return [r[0] for r in connection.execute("SELECT attempt_id FROM ai_attempts WHERE input_digest = ? ORDER BY rowid",
                                             (input_digest,))]


@dataclass(frozen=True)
class AIAuditEvent:
    event_id: int
    event_type: str
    entity_type: str
    entity_id: str
    recorded_at: datetime
    actor: str
    details: dict


def ai_audit_events(connection) -> list[AIAuditEvent]:
    out = []
    for event_id, event_type, entity_type, entity_id, recorded_at, actor, details in connection.execute(
            "SELECT event_id, event_type, entity_type, entity_id, recorded_at, actor, details_json FROM ai_audit_events "
            "ORDER BY event_id"):
        if event_type not in AUDIT_EVENT_TYPES:
            raise AIStoreIntegrityError(f"unknown AI audit event type {event_type}")
        out.append(AIAuditEvent(event_id, event_type, entity_type, entity_id, datetime.fromisoformat(recorded_at), actor,
                                json.loads(details)))
    return out


def verify_ai_database(connection) -> dict[str, int]:
    """Re-verify every stored AI record and the cardinality rules. Raises on the first problem."""
    require_ai_schema(connection)
    orphans = connection.execute("PRAGMA foreign_key_check").fetchall()
    if orphans:
        raise AIStoreIntegrityError(f"orphaned AI references: {orphans[:5]}")
    for (digest,) in connection.execute("SELECT input_digest FROM ai_inputs").fetchall():
        load_ai_input(connection, digest)
    for (attempt_id,) in connection.execute("SELECT attempt_id FROM ai_attempts").fetchall():
        load_ai_attempt(connection, attempt_id)
    for (attempt_id,) in connection.execute("SELECT attempt_id FROM ai_responses").fetchall():
        load_ai_response(connection, attempt_id)
    for (attempt_id,) in connection.execute("SELECT attempt_id FROM ai_validations").fetchall():
        load_ai_validation(connection, attempt_id)
    accepted = {r[0] for r in connection.execute("SELECT attempt_id FROM ai_validations WHERE status = 'ACCEPTED'")}
    for (artifact_id, attempt_id) in connection.execute("SELECT artifact_id, attempt_id FROM ai_proposals").fetchall():
        if attempt_id not in accepted:
            raise AIStoreIntegrityError(f"AI proposal {artifact_id} has no ACCEPTED validation")
    events = ai_audit_events(connection)
    for kind, table, column in (("AI_INPUT_RECORDED", "ai_inputs", "input_digest"),
                                ("AI_ATTEMPT_RECORDED", "ai_attempts", "attempt_id"),
                                ("AI_RESPONSE_RECORDED", "ai_responses", "attempt_id"),
                                ("AI_PROPOSAL_RECORDED", "ai_proposals", "artifact_id")):
        stored = {r[0] for r in connection.execute(f"SELECT {column} FROM {table}")}
        logged = [e.entity_id for e in events if e.event_type == kind]
        if sorted(logged) != sorted(stored):
            raise AIStoreIntegrityError(f"{kind} events disagree with {table}")
    return {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
