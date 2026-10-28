"""Stores and reloads Phase 6 AI provenance (tables introduced in schema versions 5 and 6).

Concrete persistence for docs/PHASE6_STRUCTURED_CLAUDE_PROVENANCE_DESIGN.md §10.
It records AI runs, each run's single terminal result, the raw returned model
text, and successful structured artifacts with their source excerpts.

Rules this module follows:

* AI output is interpretation. Nothing here is buyer evidence, a BAEC
  decision, human judgment, or approval, and nothing here creates one.
* It enforces structure and provenance only. Whether an extraction is
  semantically correct is decided by the AI layer before it asks for an
  artifact to be stored. Raw model text is opaque: never parsed or interpreted.
* Every table is append-only. Rows are inserted with plain INSERT; a duplicate
  identity fails and never replaces anything.
* record_run commits on its own before returning, so a run is durable before
  any remote attempt. Everything belonging to one terminal outcome is
  committed by record_terminal_outcome in a single transaction, or not at all.
* Every load re-verifies digests, bindings, excerpt fidelity, and failure codes,
  and raises PersistenceIntegrityError rather than returning anything corrupt.
* Failure codes are a closed, status-specific vocabulary (Phase 6D design §5.1).
  A semantic code is legal only under the validator version its run recorded.
* The store never reads the clock; timestamps are supplied by callers.
* open_ai_provenance_store(path) is the only opener: it opens an existing
  current-schema file with foreign keys enforced, never creates one, and returns a
  store that owns (and closes) its connection. A store built directly from a
  connection does not own it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Iterator

from baec_app.data.database import (
    AI_API_ERROR_CATEGORIES,
    AI_OUTPUT_REQUIRED_STATUSES,
    AI_OUTPUT_STATUSES,
    AI_PARSE_FAILURE_CODES,
    AI_RESPONSE_STATUSES,
    AI_SEMANTIC_FAILURE_CODES,
    AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION,
    AI_TRANSPORT_CATEGORIES,
    PersistenceError,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
    check_sqlite_version,
    require_current_schema,
    transaction,
)
from baec_app.data.records import RecordValidationError
from baec_app.data.repository import decode_datetime, encode_datetime


class AiRunStatus(Enum):
    """The terminal status of one AI run (design §11)."""

    SUCCESS = "success"
    REFUSAL = "refusal"
    MAX_TOKENS = "max_tokens"
    UNEXPECTED_STOP = "unexpected_stop"
    API_ERROR = "api_error"
    TRANSPORT_FAILURE = "transport_failure"
    PARSE_FAILURE = "parse_failure"
    SEMANTIC_VALIDATION_FAILURE = "semantic_validation_failure"
    MODEL_MISMATCH = "model_mismatch"
    INTERRUPTED = "interrupted"


class AiAttributedSpeaker(Enum):
    """Who the AI says spoke an excerpt. AI inference: never verified, never buyer evidence."""

    BUYER = "buyer"
    SELLER = "seller"
    UNCLEAR = "unclear"


class AiRemoteOutcome(Enum):
    """Whether the request reached the provider. Separate from the failure cause."""

    RESPONSE_RECEIVED = "response_received"
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


_SHA256 = re.compile(r"[0-9a-f]{64}")
_CATEGORY = re.compile(r"[a-z][a-z0-9_]*")


def sha256_text(text: str) -> str:
    """Lowercase hexadecimal SHA-256 of the exact UTF-8 bytes of text."""
    if not isinstance(text, str):
        raise RecordValidationError("only text can be digested")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RecordValidationError(f"{field} must be a non-blank string")


def _optional_text(value: object, field: str) -> None:
    if value is not None:
        _text(value, field)


def _digest(value: object, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RecordValidationError(f"{field} must be a lowercase hexadecimal SHA-256 digest")


def _aware(value: object, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RecordValidationError(f"{field} must be a timezone-aware datetime")


def _encode_failure_codes(codes: tuple[str, ...]) -> str | None:
    """The one stored spelling of failure codes: NULL for none, else a compact JSON array."""
    return json.dumps(list(codes), separators=(",", ":")) if codes else None


def _decode_failure_codes(stored: object) -> tuple[str, ...]:
    """Read stored failure codes back, refusing anything but the exact stored spelling."""
    if stored is None:
        return ()
    if not isinstance(stored, str):
        raise ValueError("stored failure codes are not text")
    codes = json.loads(stored)
    if type(codes) is not list or not codes or any(type(code) is not str for code in codes):
        raise ValueError("stored failure codes are not a non-empty JSON array of text")
    codes = tuple(codes)
    if _encode_failure_codes(codes) != stored:
        raise ValueError("stored failure codes are not in their canonical spelling")
    return codes


def _count(value: object, field: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise RecordValidationError(f"{field} must be a non-negative integer or None")


# --- records --------------------------------------------------------------------


@dataclass(frozen=True)
class AiRunRecord:
    """The immutable record of one intended remote inference attempt."""

    ai_run_id: str
    provider: str
    task_type: str
    task_version: str
    account_id: str
    interaction_id: str
    requested_model: str
    sdk_name: str
    sdk_version: str
    prompt_version: str
    prompt_digest: str
    input_version: str
    input_digest: str
    output_schema_version: str
    output_schema_digest: str
    canonicalization_version: str
    request_spec_version: str
    request_digest: str
    validation_version: str
    requested_at: datetime
    retry_of_ai_run_id: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "ai_run_id", "task_type", "task_version", "account_id", "interaction_id", "requested_model",
            "sdk_name", "sdk_version", "prompt_version", "input_version", "output_schema_version",
            "canonicalization_version", "request_spec_version", "validation_version",
        ):
            _text(getattr(self, field), f"AiRunRecord.{field}")
        if self.provider != "anthropic":
            raise RecordValidationError("AiRunRecord.provider must be 'anthropic'")
        for field in ("prompt_digest", "input_digest", "output_schema_digest", "request_digest"):
            _digest(getattr(self, field), f"AiRunRecord.{field}")
        _aware(self.requested_at, "AiRunRecord.requested_at")
        _optional_text(self.retry_of_ai_run_id, "AiRunRecord.retry_of_ai_run_id")
        if self.retry_of_ai_run_id == self.ai_run_id:
            raise RecordValidationError("a run cannot be a retry of itself")


@dataclass(frozen=True)
class AiRunResultRecord:
    """The single terminal result of a run. Mirrors the ai_run_results CHECK constraints."""

    ai_run_id: str
    status: AiRunStatus
    remote_outcome: AiRemoteOutcome
    completed_at: datetime
    provider_message_id: str | None = None
    response_model: str | None = None
    stop_reason: str | None = None
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    failure_category: str | None = None
    failure_codes: tuple[str, ...] = ()
    output_digest: str | None = None

    def __post_init__(self) -> None:
        _text(self.ai_run_id, "AiRunResultRecord.ai_run_id")
        if type(self.status) is not AiRunStatus or type(self.remote_outcome) is not AiRemoteOutcome:
            raise RecordValidationError("status and remote_outcome must be AiRunStatus and AiRemoteOutcome")
        _aware(self.completed_at, "AiRunResultRecord.completed_at")
        for field in ("provider_message_id", "response_model", "stop_reason", "provider_request_id"):
            _optional_text(getattr(self, field), f"AiRunResultRecord.{field}")
        usage = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        for field in usage:
            _count(getattr(self, field), f"AiRunResultRecord.{field}")
        if self.output_digest is not None:
            _digest(self.output_digest, "AiRunResultRecord.output_digest")
        if self.failure_category is not None and (
            not isinstance(self.failure_category, str) or not _CATEGORY.fullmatch(self.failure_category)
        ):
            raise RecordValidationError("failure_category must be a machine token")
        self._check_shape(usage)

    def _check_shape(self, usage: tuple[str, ...]) -> None:
        status, outcome = self.status.value, self.remote_outcome
        if status in AI_RESPONSE_STATUSES:
            if outcome is not AiRemoteOutcome.RESPONSE_RECEIVED:
                raise RecordValidationError(f"{status} requires remote_outcome response_received")
        elif status == "transport_failure":
            if outcome is AiRemoteOutcome.RESPONSE_RECEIVED:
                raise RecordValidationError("transport_failure permits only not_sent or unknown")
        elif outcome is not AiRemoteOutcome.UNKNOWN:  # interrupted
            raise RecordValidationError("interrupted requires remote_outcome unknown")
        if self.output_digest is not None and status not in AI_OUTPUT_STATUSES:
            raise RecordValidationError(f"{status} carries no returned model output")
        if self.output_digest is None and status in AI_OUTPUT_REQUIRED_STATUSES:
            raise RecordValidationError(f"{status} requires the returned model output")
        if status == "success" and not (
            self.provider_message_id and self.response_model and self.stop_reason == "end_turn"
        ):
            raise RecordValidationError("success requires a message id, a response model and stop_reason end_turn")
        if status in ("refusal", "max_tokens") and self.stop_reason != status:
            raise RecordValidationError(f"{status} requires stop_reason {status}")
        if status == "unexpected_stop" and self.stop_reason in (None, "end_turn", "refusal", "max_tokens"):
            raise RecordValidationError("unexpected_stop requires some other stop_reason")
        if status == "model_mismatch" and self.response_model is None:
            raise RecordValidationError("model_mismatch requires the returned model id")
        if status in ("transport_failure", "interrupted") and (
            self.provider_message_id or self.response_model or self.stop_reason
            or any(getattr(self, field) is not None for field in usage)
        ):
            raise RecordValidationError(f"{status} has no response, so it has no response metadata")
        if status == "api_error":
            if self.failure_category not in AI_API_ERROR_CATEGORIES:
                raise RecordValidationError("api_error requires an approved failure category")
        elif status == "transport_failure":
            if AI_TRANSPORT_CATEGORIES.get(self.failure_category) != outcome.value:
                raise RecordValidationError("transport_failure requires the category matching its delivery state")
        elif self.failure_category is not None:
            raise RecordValidationError(f"{status} has no failure category")
        _check_failure_codes(status, self.failure_codes)


def _check_failure_codes(status: str, codes: tuple[str, ...], validation_version: str | None = None) -> None:
    """The closed, status-specific failure-code vocabulary and cardinality (Phase 6D design §5.1).

    Without a validator version, a semantic code must be legal under some known
    version (record construction). With one, it must be legal under that version:
    the store checks every result against the version its own run recorded.
    """
    if type(codes) is not tuple or any(type(code) is not str for code in codes):
        raise RecordValidationError("failure_codes must be a tuple of machine codes")
    if status == "parse_failure":
        if len(codes) != 1 or codes[0] not in AI_PARSE_FAILURE_CODES:
            raise RecordValidationError("parse_failure requires exactly one parse failure code")
    elif status == "semantic_validation_failure":
        if not codes:
            raise RecordValidationError("semantic_validation_failure requires its failure codes")
        if validation_version is None:
            legal = AI_SEMANTIC_FAILURE_CODES
        else:
            legal = AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION.get(validation_version, ())
        if any(code not in legal for code in codes):
            raise RecordValidationError("a failure code is outside the validator version's closed vocabulary")
        if list(codes) != sorted(set(codes)):
            raise RecordValidationError("failure codes must be sorted and unique")
    elif codes:
        raise RecordValidationError(f"{status} has no failure codes")


@dataclass(frozen=True)
class AiRunOutputRecord:
    """The exact raw text a model returned for a run, with its digest. Opaque: never parsed here."""

    ai_run_id: str
    raw_output_text: str
    output_digest: str

    @classmethod
    def of(cls, ai_run_id: str, raw_output_text: str) -> AiRunOutputRecord:
        return cls(ai_run_id, raw_output_text, sha256_text(raw_output_text))

    def __post_init__(self) -> None:
        _text(self.ai_run_id, "AiRunOutputRecord.ai_run_id")
        _digest(self.output_digest, "AiRunOutputRecord.output_digest")
        if sha256_text(self.raw_output_text) != self.output_digest:
            raise RecordValidationError("raw output does not match its digest")


@dataclass(frozen=True)
class AiArtifactRecord:
    """A successful, schema-valid and semantically valid structured AI result."""

    artifact_id: str
    ai_run_id: str
    task_type: str
    task_version: str
    output_schema_version: str
    account_id: str
    interaction_id: str
    canonical_result: str
    artifact_digest: str
    created_at: datetime

    @classmethod
    def of(
        cls, *, artifact_id: str, ai_run_id: str, task_type: str, task_version: str, output_schema_version: str,
        account_id: str, interaction_id: str, canonical_result: str, created_at: datetime,
    ) -> AiArtifactRecord:
        return cls(
            artifact_id, ai_run_id, task_type, task_version, output_schema_version, account_id, interaction_id,
            canonical_result, sha256_text(canonical_result), created_at,
        )

    def __post_init__(self) -> None:
        for field in ("artifact_id", "ai_run_id", "task_type", "task_version", "output_schema_version",
                      "account_id", "interaction_id"):
            _text(getattr(self, field), f"AiArtifactRecord.{field}")
        _digest(self.artifact_digest, "AiArtifactRecord.artifact_digest")
        if sha256_text(self.canonical_result) != self.artifact_digest:
            raise RecordValidationError("canonical result does not match its digest")
        try:
            parsed = json.loads(self.canonical_result)
        except json.JSONDecodeError as error:
            raise RecordValidationError("canonical result is not JSON") from error
        if not isinstance(parsed, dict):
            raise RecordValidationError("canonical result must be a JSON object")
        _aware(self.created_at, "AiArtifactRecord.created_at")


@dataclass(frozen=True)
class AiArtifactExcerptRecord:
    """One exact source excerpt of an artifact. AI-selected text, not domain evidence.

    attributed_speaker is the AI's inference about who spoke. Nothing here claims it is correct.
    """

    artifact_id: str
    excerpt_id: str
    interaction_id: str
    text: str
    attributed_speaker: AiAttributedSpeaker

    def __post_init__(self) -> None:
        for field in ("artifact_id", "excerpt_id", "interaction_id"):
            _text(getattr(self, field), f"AiArtifactExcerptRecord.{field}")
        _text(self.text, "AiArtifactExcerptRecord.text")  # stored exactly; must not be whitespace-only
        if type(self.attributed_speaker) is not AiAttributedSpeaker:
            raise RecordValidationError("attributed_speaker must be an AiAttributedSpeaker")


@dataclass(frozen=True)
class AiTerminalOutcome:
    """Everything one terminal outcome persists, validated as a whole before any write (design §10.5)."""

    result: AiRunResultRecord
    output: AiRunOutputRecord | None = None
    artifact: AiArtifactRecord | None = None
    excerpts: tuple[AiArtifactExcerptRecord, ...] = ()

    def __post_init__(self) -> None:
        if type(self.result) is not AiRunResultRecord:
            raise RecordValidationError("result must be an AiRunResultRecord")
        if self.output is not None and type(self.output) is not AiRunOutputRecord:
            raise RecordValidationError("output must be an AiRunOutputRecord")
        if self.artifact is not None and type(self.artifact) is not AiArtifactRecord:
            raise RecordValidationError("artifact must be an AiArtifactRecord")
        if type(self.excerpts) is not tuple or any(type(e) is not AiArtifactExcerptRecord for e in self.excerpts):
            raise RecordValidationError("excerpts must be a tuple of AiArtifactExcerptRecord")
        run_id, status = self.result.ai_run_id, self.result.status
        if status is AiRunStatus.INTERRUPTED:
            raise RecordValidationError("interrupted is recorded only through mark_interrupted")
        if (self.output is None) != (self.result.output_digest is None):
            raise RecordValidationError("the returned output and the result's output digest must come together")
        if self.output is not None:
            if self.output.ai_run_id != run_id:
                raise RecordValidationError("the output belongs to a different run")
            if self.output.output_digest != self.result.output_digest:
                raise RecordValidationError("the output digest differs from the result's")
        if (status is AiRunStatus.SUCCESS) != (self.artifact is not None):
            raise RecordValidationError("an artifact is stored exactly when the run succeeded")
        if self.artifact is None:
            if self.excerpts:
                raise RecordValidationError("excerpts exist only with an artifact")
            return
        if self.artifact.ai_run_id != run_id:
            raise RecordValidationError("the artifact belongs to a different run")
        ids, texts = set(), set()
        for excerpt in self.excerpts:
            if excerpt.artifact_id != self.artifact.artifact_id:
                raise RecordValidationError("an excerpt belongs to a different artifact")
            if excerpt.interaction_id != self.artifact.interaction_id:
                raise RecordValidationError("an excerpt cites a different interaction than its artifact")
            if excerpt.excerpt_id in ids:
                raise RecordValidationError(f"duplicate excerpt id {excerpt.excerpt_id!r}")
            if excerpt.text in texts:
                raise RecordValidationError(f"excerpt {excerpt.excerpt_id!r} duplicates another excerpt's text")
            ids.add(excerpt.excerpt_id)
            texts.add(excerpt.text)


# --- store ----------------------------------------------------------------------

_RUN_COLUMNS = (
    "ai_run_id", "provider", "task_type", "task_version", "account_id", "interaction_id", "requested_model",
    "sdk_name", "sdk_version", "prompt_version", "prompt_digest", "input_version", "input_digest",
    "output_schema_version", "output_schema_digest", "canonicalization_version", "request_spec_version",
    "request_digest", "validation_version", "requested_at", "retry_of_ai_run_id",
)
_RESULT_COLUMNS = (
    "ai_run_id", "status", "remote_outcome", "provider_message_id", "response_model", "stop_reason",
    "provider_request_id", "input_tokens", "output_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "failure_category", "failure_codes", "output_digest", "completed_at",
)
_ARTIFACT_COLUMNS = (
    "artifact_id", "ai_run_id", "task_type", "task_version", "output_schema_version", "account_id",
    "interaction_id", "canonical_result", "artifact_digest", "created_at",
)
_EXCERPT_COLUMNS = ("artifact_id", "excerpt_id", "interaction_id", "text", "attributed_speaker")


def _insert(table: str, columns: tuple[str, ...]) -> str:
    """A plain INSERT. Never REPLACE, OR REPLACE, OR IGNORE, or an upsert."""
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"


@contextmanager
def _fail_closed(what: str) -> Iterator[None]:
    """Turn any failure while rebuilding a record into PersistenceIntegrityError."""
    try:
        yield
    except (PersistenceIntegrityError, RepositoryNotFoundError):
        raise
    except (RecordValidationError, ValueError, TypeError, KeyError) as error:
        raise PersistenceIntegrityError(f"stored {what} is corrupt or contradictory: {error}") from error


class AiProvenanceStoreUnavailable(PersistenceError):
    """open_ai_provenance_store could not open an existing database file for AI provenance."""


def open_ai_provenance_store(path: str | os.PathLike[str]) -> AiProvenanceStore:
    """Open an existing current-schema database for AI provenance writes, never creating one.

    Accepts a str or an os.PathLike[str]. Refuses blank paths, ":memory:", file: URIs,
    missing files, directories, and non-databases; DatabaseVersionError propagates for
    another schema version. The returned store owns its connection; close() closes it.
    """
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    if type(path) is not str or not path.strip() or path == ":memory:" or path.strip().lower().startswith("file:"):
        raise AiProvenanceStoreUnavailable("a path to an existing database file is required")
    file = Path(path)
    if not file.is_file():
        raise AiProvenanceStoreUnavailable(f"{path!r} is not an existing database file")
    check_sqlite_version()
    try:
        # mode=rw opens an existing file for writing and never creates one.
        connection = sqlite3.connect(file.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None)
    except sqlite3.Error as error:
        raise AiProvenanceStoreUnavailable(f"{path!r} cannot be opened: {error}") from error
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            store = AiProvenanceStore(connection)  # verifies foreign keys and the schema version
        except sqlite3.DatabaseError as error:
            raise AiProvenanceStoreUnavailable(f"{path!r} is not a readable database: {error}") from error
    except BaseException:
        connection.close()
        raise
    store._owns_connection = True
    return store


class AiProvenanceStore:
    """SQL for the five AI provenance tables, and nothing else: no domain writes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if type(connection) is not sqlite3.Connection:
            raise PersistenceError("AiProvenanceStore requires a sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise PersistenceError("AiProvenanceStore requires foreign key enforcement")
        require_current_schema(connection)
        self._db = connection
        self._owns_connection = False
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Close an owned connection. Idempotent; a store that does not own its connection leaves it open."""
        if not self._closed:
            self._closed = True
            if self._owns_connection:
                self._db.close()

    def __enter__(self) -> AiProvenanceStore:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[None]:
        try:
            with transaction(self._db):
                yield
        except sqlite3.IntegrityError as error:
            raise RepositoryConflictError(f"the database refused the write: {error}") from error

    # --- writes -------------------------------------------------------------------

    def record_run(self, run: AiRunRecord) -> None:
        """Insert a run and commit it before returning, so it is durable before any remote attempt."""
        if type(run) is not AiRunRecord:
            raise RecordValidationError("record_run requires an AiRunRecord")
        if self._db.in_transaction:
            raise PersistenceError("record_run commits independently and cannot join an open transaction")
        with self._write():
            interaction = self._db.execute(
                "SELECT account_id FROM interactions WHERE interaction_id = ?", (run.interaction_id,)
            ).fetchone()
            if interaction is None:
                raise RepositoryNotFoundError(f"interaction {run.interaction_id!r} does not exist")
            if interaction[0] != run.account_id:
                raise RepositoryVerificationError("the run's interaction belongs to a different account")
            if self._run_row(run.ai_run_id) is not None:
                raise RepositoryConflictError(f"AI run {run.ai_run_id!r} already exists")
            if run.retry_of_ai_run_id is not None and self._run_row(run.retry_of_ai_run_id) is None:
                raise RepositoryNotFoundError(f"AI run {run.retry_of_ai_run_id!r} does not exist")
            values = [getattr(run, column) for column in _RUN_COLUMNS]
            values[_RUN_COLUMNS.index("requested_at")] = encode_datetime(run.requested_at)
            self._db.execute(_insert("ai_runs", _RUN_COLUMNS), values)

    def record_terminal_outcome(self, outcome: AiTerminalOutcome) -> None:
        """Persist one terminal outcome in a single transaction: all of it, or none of it."""
        if type(outcome) is not AiTerminalOutcome:
            raise RecordValidationError("record_terminal_outcome requires an AiTerminalOutcome")
        result = outcome.result
        with self._write():
            run = self._require_run(result.ai_run_id)
            self._require_no_result(result.ai_run_id)
            self._require_after_request(run, result.completed_at, "completed_at")
            if result.status is AiRunStatus.SUCCESS and result.response_model != run.requested_model:
                raise RepositoryVerificationError("success requires the response model to equal the requested model")
            if result.status is AiRunStatus.MODEL_MISMATCH and result.response_model == run.requested_model:
                raise RepositoryVerificationError("model_mismatch requires a response model that differs")
            try:
                _check_failure_codes(result.status.value, result.failure_codes, run.validation_version)
            except RecordValidationError as error:
                raise RepositoryVerificationError(str(error)) from error
            if outcome.artifact is not None:
                self._verify_artifact_against_run(outcome.artifact, run)
                self._require_after_request(run, outcome.artifact.created_at, "created_at")
                source = self._interaction_text(run.interaction_id)
                for excerpt in outcome.excerpts:
                    if excerpt.text not in source:
                        raise RepositoryVerificationError(
                            f"excerpt {excerpt.excerpt_id!r} does not occur verbatim in interaction {run.interaction_id!r}"
                        )
            self._insert_result(result)
            if outcome.output is not None:
                self._insert_output(outcome.output)
            if outcome.artifact is not None:
                self._insert_artifact(outcome.artifact)
                for excerpt in outcome.excerpts:
                    self._insert_excerpt(excerpt)

    def mark_interrupted(self, ai_run_id: str, completed_at: datetime, *, minimum_age: timedelta) -> None:
        """Explicit operator action: close an incomplete run older than minimum_age as interrupted.

        Operational provenance cleanup only. It is not a human authorization of anything.
        """
        if not isinstance(minimum_age, timedelta) or minimum_age < timedelta(0):
            raise RecordValidationError("minimum_age must be a non-negative timedelta")
        result = AiRunResultRecord(
            ai_run_id, AiRunStatus.INTERRUPTED, AiRemoteOutcome.UNKNOWN, completed_at
        )
        with self._write():
            run = self._require_run(ai_run_id)
            self._require_no_result(ai_run_id)
            self._require_after_request(run, completed_at, "completed_at")
            if completed_at - run.requested_at < minimum_age:
                raise RepositoryConflictError(f"AI run {ai_run_id!r} is younger than the minimum age")
            self._insert_result(result)

    def _insert_result(self, result: AiRunResultRecord) -> None:
        values = [getattr(result, column) for column in _RESULT_COLUMNS]
        values[_RESULT_COLUMNS.index("status")] = result.status.value
        values[_RESULT_COLUMNS.index("remote_outcome")] = result.remote_outcome.value
        values[_RESULT_COLUMNS.index("failure_codes")] = _encode_failure_codes(result.failure_codes)
        values[_RESULT_COLUMNS.index("completed_at")] = encode_datetime(result.completed_at)
        self._db.execute(_insert("ai_run_results", _RESULT_COLUMNS), values)

    def _insert_output(self, output: AiRunOutputRecord) -> None:
        self._db.execute(
            _insert("ai_run_outputs", ("ai_run_id", "raw_output_text", "output_digest")),
            (output.ai_run_id, output.raw_output_text, output.output_digest),
        )

    def _insert_artifact(self, artifact: AiArtifactRecord) -> None:
        values = [getattr(artifact, column) for column in _ARTIFACT_COLUMNS]
        values[_ARTIFACT_COLUMNS.index("created_at")] = encode_datetime(artifact.created_at)
        self._db.execute(_insert("ai_artifacts", _ARTIFACT_COLUMNS), values)

    def _insert_excerpt(self, excerpt: AiArtifactExcerptRecord) -> None:
        self._db.execute(
            _insert("ai_artifact_excerpts", _EXCERPT_COLUMNS),
            (excerpt.artifact_id, excerpt.excerpt_id, excerpt.interaction_id, excerpt.text,
             excerpt.attributed_speaker.value),
        )

    # --- reads --------------------------------------------------------------------

    def get_run(self, ai_run_id: str) -> AiRunRecord:
        row = self._run_row(ai_run_id)
        if row is None:
            raise RepositoryNotFoundError(f"AI run {ai_run_id!r} does not exist")
        with _fail_closed(f"AI run {ai_run_id!r}"):
            values = dict(zip(_RUN_COLUMNS, row))
            values["requested_at"] = decode_datetime(values["requested_at"])
            run = AiRunRecord(**values)
            owner = self._db.execute(
                "SELECT account_id FROM interactions WHERE interaction_id = ?", (run.interaction_id,)
            ).fetchone()
            if owner is None or owner[0] != run.account_id:
                raise ValueError("the run's interaction is missing or belongs to a different account")
            return run

    def get_result(self, ai_run_id: str) -> AiRunResultRecord:
        run = self.get_run(ai_run_id)
        row = self._db.execute(
            f"SELECT {', '.join(_RESULT_COLUMNS)} FROM ai_run_results WHERE ai_run_id = ?", (ai_run_id,)
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"AI run {ai_run_id!r} has no terminal result")
        with _fail_closed(f"result of AI run {ai_run_id!r}"):
            values = dict(zip(_RESULT_COLUMNS, row))
            values["status"] = AiRunStatus(values["status"])
            values["remote_outcome"] = AiRemoteOutcome(values["remote_outcome"])
            values["completed_at"] = decode_datetime(values["completed_at"])
            values["failure_codes"] = _decode_failure_codes(values["failure_codes"])
            result = AiRunResultRecord(**values)
            _check_failure_codes(result.status.value, result.failure_codes, run.validation_version)
            if result.completed_at < run.requested_at:
                raise ValueError("the result completed before its run was requested")
            if result.status is AiRunStatus.SUCCESS and result.response_model != run.requested_model:
                raise ValueError("a successful result names a different model than was requested")
            if result.status is AiRunStatus.MODEL_MISMATCH and result.response_model == run.requested_model:
                raise ValueError("a model_mismatch result names the requested model")
        if result.output_digest is not None:
            self._load_output(ai_run_id, result.output_digest)
        elif self._output_row(ai_run_id) is not None:
            raise PersistenceIntegrityError(f"AI run {ai_run_id!r} has output its result does not record")
        artifact_row = self._artifact_row_for_run(ai_run_id)
        if (result.status is AiRunStatus.SUCCESS) != (artifact_row is not None):
            raise PersistenceIntegrityError(
                f"AI run {ai_run_id!r}: an artifact must exist exactly when the run succeeded"
            )
        return result

    def get_output(self, ai_run_id: str) -> AiRunOutputRecord:
        result = self.get_result(ai_run_id)
        if result.output_digest is None:
            raise RepositoryNotFoundError(f"AI run {ai_run_id!r} has no returned output")
        return self._load_output(ai_run_id, result.output_digest)

    def get_artifact(self, artifact_id: str) -> AiArtifactRecord:
        row = self._db.execute(
            f"SELECT {', '.join(_ARTIFACT_COLUMNS)} FROM ai_artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"AI artifact {artifact_id!r} does not exist")
        return self._load_artifact(row)

    def get_artifact_for_run(self, ai_run_id: str) -> AiArtifactRecord:
        self.get_run(ai_run_id)
        row = self._artifact_row_for_run(ai_run_id)
        if row is None:
            raise RepositoryNotFoundError(f"AI run {ai_run_id!r} has no artifact")
        return self._load_artifact(row)

    def list_artifact_excerpts(self, artifact_id: str) -> tuple[AiArtifactExcerptRecord, ...]:
        artifact = self.get_artifact(artifact_id)
        return self._load_excerpts(artifact)

    def list_incomplete_runs(self) -> tuple[AiRunRecord, ...]:
        """Runs with no terminal result, oldest request first, then by run id."""
        rows = self._db.execute(
            "SELECT ai_run_id FROM ai_runs WHERE ai_run_id NOT IN (SELECT ai_run_id FROM ai_run_results) "
            "ORDER BY requested_at ASC, ai_run_id ASC"
        ).fetchall()
        return tuple(self.get_run(row[0]) for row in rows)

    # --- load helpers -------------------------------------------------------------

    def _load_output(self, ai_run_id: str, expected_digest: str) -> AiRunOutputRecord:
        row = self._output_row(ai_run_id)
        if row is None:
            raise PersistenceIntegrityError(f"AI run {ai_run_id!r} records output that is missing")
        with _fail_closed(f"output of AI run {ai_run_id!r}"):
            output = AiRunOutputRecord(*row)  # recomputes and checks the digest
            if output.output_digest != expected_digest:
                raise ValueError("the stored output digest differs from the result's")
            return output

    def _load_artifact(self, row) -> AiArtifactRecord:
        values = dict(zip(_ARTIFACT_COLUMNS, row))
        with _fail_closed(f"AI artifact {values['artifact_id']!r}"):
            values["created_at"] = decode_datetime(values["created_at"])
            artifact = AiArtifactRecord(**values)  # recomputes and checks the digest
        run = self.get_run(artifact.ai_run_id)
        with _fail_closed(f"AI artifact {artifact.artifact_id!r}"):
            self._verify_artifact_against_run(artifact, run, error=ValueError)
        result = self.get_result(artifact.ai_run_id)  # verifies output and the success/artifact pairing
        if result.status is not AiRunStatus.SUCCESS:
            raise PersistenceIntegrityError(f"AI artifact {artifact.artifact_id!r} belongs to an unsuccessful run")
        self._load_excerpts(artifact)
        return artifact

    def _load_excerpts(self, artifact: AiArtifactRecord) -> tuple[AiArtifactExcerptRecord, ...]:
        rows = self._db.execute(
            f"SELECT {', '.join(_EXCERPT_COLUMNS)} FROM ai_artifact_excerpts WHERE artifact_id = ? ORDER BY rowid",
            (artifact.artifact_id,),
        ).fetchall()
        source = self._interaction_text(artifact.interaction_id)
        with _fail_closed(f"excerpts of AI artifact {artifact.artifact_id!r}"):
            excerpts = tuple(AiArtifactExcerptRecord(*row[:4], AiAttributedSpeaker(row[4])) for row in rows)
            for excerpt in excerpts:
                if excerpt.interaction_id != artifact.interaction_id:
                    raise ValueError(f"excerpt {excerpt.excerpt_id!r} cites a different interaction")
                if source is None or excerpt.text not in source:
                    raise ValueError(f"excerpt {excerpt.excerpt_id!r} does not occur verbatim in its interaction")
            if len({e.excerpt_id for e in excerpts}) != len(excerpts) or len({e.text for e in excerpts}) != len(excerpts):
                raise ValueError("duplicate excerpts")
            return excerpts

    @staticmethod
    def _verify_artifact_against_run(artifact: AiArtifactRecord, run: AiRunRecord, error=RepositoryVerificationError) -> None:
        for field in ("account_id", "interaction_id", "task_type", "task_version", "output_schema_version"):
            if getattr(artifact, field) != getattr(run, field):
                raise error(f"the artifact's {field} differs from its run's")

    # --- row helpers --------------------------------------------------------------

    def _run_row(self, ai_run_id: str):
        return self._db.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM ai_runs WHERE ai_run_id = ?", (ai_run_id,)
        ).fetchone()

    def _require_run(self, ai_run_id: str) -> AiRunRecord:
        return self.get_run(ai_run_id)

    def _require_no_result(self, ai_run_id: str) -> None:
        if self._db.execute("SELECT 1 FROM ai_run_results WHERE ai_run_id = ?", (ai_run_id,)).fetchone():
            raise RepositoryConflictError(f"AI run {ai_run_id!r} already has a terminal result")

    @staticmethod
    def _require_after_request(run: AiRunRecord, moment: datetime, field: str) -> None:
        if moment < run.requested_at:
            raise RepositoryVerificationError(f"{field} precedes the run's requested_at")

    def _output_row(self, ai_run_id: str):
        return self._db.execute(
            "SELECT ai_run_id, raw_output_text, output_digest FROM ai_run_outputs WHERE ai_run_id = ?",
            (ai_run_id,),
        ).fetchone()

    def _artifact_row_for_run(self, ai_run_id: str):
        return self._db.execute(
            f"SELECT {', '.join(_ARTIFACT_COLUMNS)} FROM ai_artifacts WHERE ai_run_id = ?", (ai_run_id,)
        ).fetchone()

    def _interaction_text(self, interaction_id: str) -> str | None:
        row = self._db.execute("SELECT text FROM interactions WHERE interaction_id = ?", (interaction_id,)).fetchone()
        return None if row is None else row[0]
