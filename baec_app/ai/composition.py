"""Builds the extraction runtime from a database path. The only AI module that touches the data layer.

open_extraction_runtime(path) opens:
- the Phase 4 query-only read path for source interactions
  (open_read_connection -> build_proposal_facade(...).reads), keeping only ReadService;
- the Phase 6B provenance store (open_ai_provenance_store), which owns its own
  foreign-key-enforcing writable connection and never creates a database;
- the Anthropic provider (unless a provider is supplied, as tests do).
It closes everything it opened, deterministically, including when building fails.
No command facade, gate, approval, or authority object is ever constructed.

_DataLayerProvenanceStore translates the AI-side records field by field into the
Phase 6B data-layer records. Every status, outcome, and speaker value is mapped
through an explicit table.
"""

from __future__ import annotations

import os
import uuid
from typing import Callable

from baec_app.ai.anthropic_provider import AnthropicExtractionProvider
from baec_app.ai.provenance import (
    ArtifactRecord,
    RemoteOutcome,
    RunRecord,
    RunStatus,
    TerminalOutcome,
)
from baec_app.ai.provider import ExtractionProvider
from baec_app.ai.service import ExtractionService
from baec_app.application import Clock, SystemClock, build_proposal_facade, open_read_connection
from baec_app.data.ai_provenance import (
    AiArtifactExcerptRecord,
    AiArtifactRecord,
    AiAttributedSpeaker,
    AiProvenanceStore,
    AiRemoteOutcome,
    AiRunOutputRecord,
    AiRunRecord,
    AiRunResultRecord,
    AiRunStatus,
    AiTerminalOutcome,
    open_ai_provenance_store,
)

STATUS_MAP = {
    RunStatus.SUCCESS: AiRunStatus.SUCCESS,
    RunStatus.REFUSAL: AiRunStatus.REFUSAL,
    RunStatus.MAX_TOKENS: AiRunStatus.MAX_TOKENS,
    RunStatus.UNEXPECTED_STOP: AiRunStatus.UNEXPECTED_STOP,
    RunStatus.API_ERROR: AiRunStatus.API_ERROR,
    RunStatus.TRANSPORT_FAILURE: AiRunStatus.TRANSPORT_FAILURE,
    RunStatus.PARSE_FAILURE: AiRunStatus.PARSE_FAILURE,
    RunStatus.SEMANTIC_VALIDATION_FAILURE: AiRunStatus.SEMANTIC_VALIDATION_FAILURE,
    RunStatus.MODEL_MISMATCH: AiRunStatus.MODEL_MISMATCH,
}
OUTCOME_MAP = {
    RemoteOutcome.RESPONSE_RECEIVED: AiRemoteOutcome.RESPONSE_RECEIVED,
    RemoteOutcome.NOT_SENT: AiRemoteOutcome.NOT_SENT,
    RemoteOutcome.UNKNOWN: AiRemoteOutcome.UNKNOWN,
}
SPEAKER_MAP = {
    "buyer": AiAttributedSpeaker.BUYER,
    "seller": AiAttributedSpeaker.SELLER,
    "unclear": AiAttributedSpeaker.UNCLEAR,
}


class _DataLayerProvenanceStore:
    """The AI-side ProvenanceStore, written through the Phase 6B AiProvenanceStore."""

    def __init__(self, store: AiProvenanceStore) -> None:
        if type(store) is not AiProvenanceStore:
            raise TypeError("a Phase 6B AiProvenanceStore is required")
        self._store = store

    def record_run(self, run: RunRecord) -> None:
        if type(run) is not RunRecord:
            raise TypeError("record_run requires a RunRecord")
        self._store.record_run(AiRunRecord(
            ai_run_id=run.ai_run_id,
            provider=run.provider,
            task_type=run.task_type,
            task_version=run.task_version,
            account_id=run.account_id,
            interaction_id=run.interaction_id,
            requested_model=run.requested_model,
            sdk_name=run.sdk_name,
            sdk_version=run.sdk_version,
            prompt_version=run.prompt_version,
            prompt_digest=run.prompt_digest,
            input_version=run.input_version,
            input_digest=run.input_digest,
            output_schema_version=run.output_schema_version,
            output_schema_digest=run.output_schema_digest,
            canonicalization_version=run.canonicalization_version,
            request_spec_version=run.request_spec_version,
            request_digest=run.request_digest,
            validation_version=run.validation_version,
            requested_at=run.requested_at,
        ))

    def record_terminal_outcome(self, outcome: TerminalOutcome) -> None:
        if type(outcome) is not TerminalOutcome:
            raise TypeError("record_terminal_outcome requires a TerminalOutcome")
        result = outcome.result
        output = None
        if result.raw_output_text is not None:
            output = AiRunOutputRecord.of(outcome.ai_run_id, result.raw_output_text)
        data_result = AiRunResultRecord(
            ai_run_id=outcome.ai_run_id,
            status=STATUS_MAP[result.status],
            remote_outcome=OUTCOME_MAP[result.remote_outcome],
            completed_at=result.completed_at,
            provider_message_id=result.provider_message_id,
            response_model=result.response_model,
            stop_reason=result.stop_reason,
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_creation_input_tokens=result.cache_creation_input_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens,
            failure_category=result.failure_category,
            failure_codes=result.failure_codes,
            output_digest=None if output is None else output.output_digest,
        )
        artifact, excerpts = None, ()
        if outcome.artifact is not None:
            artifact, excerpts = self._artifact(outcome.ai_run_id, outcome.artifact)
        self._store.record_terminal_outcome(
            AiTerminalOutcome(result=data_result, output=output, artifact=artifact, excerpts=excerpts)
        )

    @staticmethod
    def _artifact(ai_run_id: str, artifact: ArtifactRecord) -> tuple[AiArtifactRecord, tuple[AiArtifactExcerptRecord, ...]]:
        record = AiArtifactRecord.of(
            artifact_id=artifact.artifact_id,
            ai_run_id=ai_run_id,
            task_type=artifact.task_type,
            task_version=artifact.task_version,
            output_schema_version=artifact.output_schema_version,
            account_id=artifact.account_id,
            interaction_id=artifact.interaction_id,
            canonical_result=artifact.canonical_result,
            created_at=artifact.created_at,
        )
        excerpts = tuple(
            AiArtifactExcerptRecord(
                artifact_id=artifact.artifact_id,
                excerpt_id=excerpt.excerpt_id,
                interaction_id=excerpt.interaction_id,
                text=excerpt.text,
                attributed_speaker=SPEAKER_MAP[excerpt.attributed_speaker],
            )
            for excerpt in artifact.excerpts
        )
        return record, excerpts


def new_run_id() -> str:
    return f"airun_{uuid.uuid4().hex}"


def new_artifact_id() -> str:
    return f"aiart_{uuid.uuid4().hex}"


class ExtractionRuntime:
    """The extraction service plus ownership of its read connection and provenance store."""

    def __init__(self, service: ExtractionService, read_connection, store: AiProvenanceStore) -> None:
        self._service = service
        self._read_connection = read_connection
        self._store = store
        self._closed = False

    @property
    def service(self) -> ExtractionService:
        if self._closed:
            raise RuntimeError("the extraction runtime is closed")
        return self._service

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._store.close()
            finally:
                self._read_connection.close()

    def __enter__(self) -> ExtractionRuntime:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def open_extraction_runtime(
    database_path: str | os.PathLike[str], *, provider: ExtractionProvider | None = None,
    clock: Clock | None = None, run_ids: Callable[[], str] = new_run_id,
    artifact_ids: Callable[[], str] = new_artifact_id,
) -> ExtractionRuntime:
    """Open the read path and the provenance store, and build the service. Closes everything on failure."""
    read_connection = open_read_connection(database_path)
    try:
        store = open_ai_provenance_store(database_path)
        try:
            if provider is None:
                provider = AnthropicExtractionProvider()
            service = ExtractionService(
                reads=build_proposal_facade(read_connection).reads,
                clock=SystemClock() if clock is None else clock,
                provider=provider,
                store=_DataLayerProvenanceStore(store),
                run_ids=run_ids,
                artifact_ids=artifact_ids,
            )
        except BaseException:
            store.close()
            raise
    except BaseException:
        read_connection.close()
        raise
    return ExtractionRuntime(service, read_connection, store)
