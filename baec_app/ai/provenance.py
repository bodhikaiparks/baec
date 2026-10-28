"""The AI-side provenance records and the store protocol the service writes through.

These are provider- and application-level values, independent of SQLite. The
concrete adapter in composition.py translates them field by field into the Phase 6B
data-layer records; the data layer re-validates everything and owns atomicity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from baec_app.ai.validation import SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION, VALIDATION_VERSION

# The service's parse-failure codes: exactly one per parse_failure (Phase 6D design §5.1).
PARSE_FAILURE_CODES = (
    "missing_stop_reason",
    "missing_text_block",
    "multiple_text_blocks",
    "invalid_json",
    "structured_output_validation_failed",
)


class RunStatus(Enum):
    """Terminal statuses the service records. interrupted is an operator action in the data layer only."""

    SUCCESS = "success"
    REFUSAL = "refusal"
    MAX_TOKENS = "max_tokens"
    UNEXPECTED_STOP = "unexpected_stop"
    API_ERROR = "api_error"
    TRANSPORT_FAILURE = "transport_failure"
    PARSE_FAILURE = "parse_failure"
    SEMANTIC_VALIDATION_FAILURE = "semantic_validation_failure"
    MODEL_MISMATCH = "model_mismatch"


class RemoteOutcome(Enum):
    RESPONSE_RECEIVED = "response_received"
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


def _aware(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")


@dataclass(frozen=True)
class RunRecord:
    """The intended remote attempt, recorded before it happens."""

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

    def __post_init__(self) -> None:
        if type(self.validation_version) is not str or not self.validation_version.strip():
            raise ValueError("RunRecord.validation_version must be a non-blank string")
        _aware(self.requested_at, "RunRecord.requested_at")


@dataclass(frozen=True)
class TerminalResult:
    """How one attempt ended. raw_output_text is the exact returned text (or the multi-block audit form)."""

    status: RunStatus
    remote_outcome: RemoteOutcome
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
    raw_output_text: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not RunStatus or type(self.remote_outcome) is not RemoteOutcome:
            raise ValueError("status and remote_outcome must be RunStatus and RemoteOutcome")
        _aware(self.completed_at, "TerminalResult.completed_at")
        if type(self.failure_codes) is not tuple:
            raise ValueError("failure_codes must be a tuple")
        codes = self.failure_codes
        if self.status is RunStatus.PARSE_FAILURE:
            if len(codes) != 1 or codes[0] not in PARSE_FAILURE_CODES:
                raise ValueError("parse_failure requires exactly one parse failure code")
        elif self.status is RunStatus.SEMANTIC_VALIDATION_FAILURE:
            legal = SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[VALIDATION_VERSION]
            if not codes or any(code not in legal for code in codes):
                raise ValueError("semantic_validation_failure requires one or more semantic failure codes")
            if list(codes) != sorted(set(codes)):
                raise ValueError("failure codes must be sorted and unique")
        elif codes:
            raise ValueError(f"{self.status.value} has no failure codes")


@dataclass(frozen=True)
class ArtifactExcerpt:
    excerpt_id: str
    interaction_id: str
    text: str
    attributed_speaker: str  # "buyer" | "seller" | "unclear": AI inference, never verified


@dataclass(frozen=True)
class ArtifactRecord:
    """A successful, semantically valid extraction. canonical_result is canonical JSON of the parsed output."""

    artifact_id: str
    task_type: str
    task_version: str
    output_schema_version: str
    account_id: str
    interaction_id: str
    canonical_result: str
    created_at: datetime
    excerpts: tuple[ArtifactExcerpt, ...]

    def __post_init__(self) -> None:
        _aware(self.created_at, "ArtifactRecord.created_at")
        if type(self.excerpts) is not tuple or any(type(e) is not ArtifactExcerpt for e in self.excerpts):
            raise ValueError("excerpts must be a tuple of ArtifactExcerpt")


@dataclass(frozen=True)
class TerminalOutcome:
    """Everything one terminal outcome persists. The store commits it all at once or not at all."""

    ai_run_id: str
    result: TerminalResult
    artifact: ArtifactRecord | None = None

    def __post_init__(self) -> None:
        if type(self.result) is not TerminalResult:
            raise ValueError("result must be a TerminalResult")
        if self.artifact is not None and type(self.artifact) is not ArtifactRecord:
            raise ValueError("artifact must be an ArtifactRecord")


class ProvenanceStore(Protocol):
    def record_run(self, run: RunRecord) -> None:
        """Persist and commit the run before any remote attempt."""

    def record_terminal_outcome(self, outcome: TerminalOutcome) -> None:
        """Persist one terminal outcome atomically."""
