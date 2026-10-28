"""Orchestration of one structured extraction: request → recorded run → one attempt → terminal outcome.

Order (design §10.5, 6C-B):
1-9. Validate the request locally, read the stored interaction through the Phase 4
     read path, build the canonical input, ask the provider to prepare the exact
     request, check it against the v1 constants, and compute every digest. A failure
     here records nothing and calls no provider.
10.  Record the run, which the store commits before returning.
11.  Hand that same request object to the provider for exactly one attempt.
Then decide the terminal outcome with the locked precedence and record it in one
atomic call. Expected provider failures become recorded outcomes. Any other
exception after the run is recorded propagates and leaves the run incomplete.

The service depends only on protocols and pure modules: it never imports the data
layer, the Anthropic SDK, the domain layer, or MCP, and it never creates a BAEC, a
proposal, an approval, an authorization, or an account-state change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pydantic

from baec_app.ai.canonical import CANONICALIZATION_VERSION, canonical_digest, canonical_json, sha256_text
from baec_app.ai.contracts import (
    API_METHOD,
    INPUT_VERSION,
    MAX_TOKENS,
    OUTPUT_SCHEMA_VERSION,
    PROVIDER,
    REQUEST_SPEC_VERSION,
    TASK_TYPE,
    TASK_VERSION,
    BaecExtractionOutput,
)
from baec_app.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from baec_app.ai.provenance import (
    ArtifactExcerpt,
    ArtifactRecord,
    ProvenanceStore,
    RemoteOutcome,
    RunRecord,
    RunStatus,
    TerminalOutcome,
    TerminalResult,
)
from baec_app.ai.provider import (
    AiRequestSpec,
    ExtractionProvider,
    ProviderApiError,
    ProviderResponse,
    ProviderTransportError,
)
from baec_app.ai.validation import VALIDATION_VERSION, validate_extraction
from baec_app.application import Clock, ReadService

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")


class AiRequestError(ValueError):
    """The extraction request is invalid locally. Nothing was recorded and no provider was called."""


@dataclass(frozen=True)
class ExtractionExecutionResult:
    """What one extraction produced. structured_output exists only for success."""

    ai_run_id: str
    status: RunStatus
    remote_outcome: RemoteOutcome
    artifact_id: str | None
    structured_output: BaecExtractionOutput | None


def canonical_input(*, account_id: str, interaction_id: str, interaction_text: str) -> str:
    """The exact user-message content for baec-extraction-input/v1: canonical JSON, nothing else."""
    return canonical_json({
        "input_version": INPUT_VERSION,
        "account_id": account_id,
        "interaction_id": interaction_id,
        "interaction_text": interaction_text,
    })


def text_audit(text_blocks: tuple[str, ...]) -> str | None:
    """Returned text as stored: none, the single block exactly, or a canonical JSON array of the blocks.

    The array form is an audit representation of several returned text blocks, not a
    claim that the model produced one literal text block.
    """
    if not text_blocks:
        return None
    if len(text_blocks) == 1:
        return text_blocks[0]
    return canonical_json(list(text_blocks))


class ExtractionService:
    def __init__(
        self, *, reads: ReadService, clock: Clock, provider: ExtractionProvider, store: ProvenanceStore,
        run_ids: Callable[[], str], artifact_ids: Callable[[], str],
    ) -> None:
        if type(reads) is not ReadService:
            raise TypeError("ExtractionService requires the Phase 4 ReadService")
        self._reads = reads
        self._clock = clock
        self._provider = provider
        self._store = store
        self._run_ids = run_ids
        self._artifact_ids = artifact_ids

    def extract_interaction(self, *, account_id: str, interaction_id: str, model: str) -> ExtractionExecutionResult:
        # 1. local identifier checks
        for value, field, pattern in ((account_id, "account_id", _IDENTIFIER),
                                      (interaction_id, "interaction_id", _IDENTIFIER), (model, "model", _MODEL)):
            if type(value) is not str or not pattern.fullmatch(value) or ".." in value:
                raise AiRequestError(f"{field} is not a valid identifier")
        # 2-3. the stored interaction, through the Phase 4 read path, and its ownership
        interaction = self._reads.get_interaction(interaction_id)
        if interaction.account_id != account_id:
            raise AiRequestError("the interaction does not belong to the account")
        # 4-6. canonical input, versioned prompt, and the provider's exact request
        user_content = canonical_input(account_id=account_id, interaction_id=interaction_id,
                                       interaction_text=interaction.text)
        spec = self._provider.prepare_request(
            model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, user_content=user_content,
            prompt_version=PROMPT_VERSION, input_version=INPUT_VERSION, output_schema_version=OUTPUT_SCHEMA_VERSION,
        )
        # 7. the prepared request must be exactly the v1 request
        self._require_v1_spec(spec, model=model, user_content=user_content)
        # 8-9. digests and the run record
        run = RunRecord(
            ai_run_id=self._run_ids(),
            provider=spec.provider,
            task_type=TASK_TYPE,
            task_version=TASK_VERSION,
            account_id=account_id,
            interaction_id=interaction_id,
            requested_model=model,
            sdk_name=self._provider.sdk_name,
            sdk_version=self._provider.sdk_version,
            prompt_version=spec.prompt_version,
            prompt_digest=sha256_text(spec.system),
            input_version=spec.input_version,
            input_digest=sha256_text(user_content),
            output_schema_version=spec.output_schema_version,
            output_schema_digest=canonical_digest(spec.output_config["format"]["schema"]),
            canonicalization_version=CANONICALIZATION_VERSION,
            request_spec_version=spec.request_spec_version,
            request_digest=spec.digest(),
            validation_version=VALIDATION_VERSION,
            requested_at=self._clock.now(),
        )
        # 10. durable before the attempt
        self._store.record_run(run)
        # 11. exactly one attempt with the same request object
        try:
            response = self._provider.invoke(spec)
        except ProviderApiError as error:
            result = TerminalResult(
                status=RunStatus.API_ERROR, remote_outcome=RemoteOutcome.RESPONSE_RECEIVED,
                completed_at=self._clock.now(), failure_category=error.category,
                provider_request_id=error.provider_request_id,
            )
            return self._finish(run, result)
        except ProviderTransportError as error:
            result = TerminalResult(
                status=RunStatus.TRANSPORT_FAILURE, remote_outcome=RemoteOutcome(error.remote_outcome),
                completed_at=self._clock.now(), failure_category=error.category,
            )
            return self._finish(run, result)
        if type(response) is not ProviderResponse:
            raise TypeError("the provider returned something other than a ProviderResponse")
        return self._conclude(run, interaction.text, response)

    # --- the terminal decision ------------------------------------------------------------

    def _conclude(self, run: RunRecord, source_text: str, response: ProviderResponse) -> ExtractionExecutionResult:
        raw = text_audit(response.text_blocks)

        def result(status: RunStatus, codes: tuple[str, ...] = (), raw_output: str | None = raw) -> TerminalResult:
            return TerminalResult(
                status=status, remote_outcome=RemoteOutcome.RESPONSE_RECEIVED, completed_at=self._clock.now(),
                provider_message_id=response.provider_message_id, response_model=response.response_model,
                stop_reason=response.stop_reason, provider_request_id=response.provider_request_id,
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                cache_creation_input_tokens=response.cache_creation_input_tokens,
                cache_read_input_tokens=response.cache_read_input_tokens,
                failure_codes=codes, raw_output_text=raw_output,
            )

        # A. a different model answered: nothing else is interpreted
        if response.response_model != run.requested_model:
            return self._finish(run, result(RunStatus.MODEL_MISMATCH))
        # C. provider terminal causes, kept as reported
        stop = response.stop_reason
        if stop is None:
            return self._finish(run, result(RunStatus.PARSE_FAILURE, ("missing_stop_reason",)))
        if stop == "refusal":
            return self._finish(run, result(RunStatus.REFUSAL))
        if stop == "max_tokens":
            return self._finish(run, result(RunStatus.MAX_TOKENS))
        if stop != "end_turn":
            return self._finish(run, result(RunStatus.UNEXPECTED_STOP))
        # D. end_turn: exactly one text block, parsed strictly with no repair
        if not response.text_blocks:
            return self._finish(run, result(RunStatus.PARSE_FAILURE, ("missing_text_block",)))
        if len(response.text_blocks) > 1:
            return self._finish(run, result(RunStatus.PARSE_FAILURE, ("multiple_text_blocks",)))
        try:
            parsed = BaecExtractionOutput.model_validate_json(response.text_blocks[0])
        except pydantic.ValidationError as error:
            invalid_json = any(detail["type"] == "json_invalid" for detail in error.errors(include_input=False))
            code = "invalid_json" if invalid_json else "structured_output_validation_failed"
            return self._finish(run, result(RunStatus.PARSE_FAILURE, (code,)))
        codes = validate_extraction(parsed, source_interaction_id=run.interaction_id, source_text=source_text)
        if codes:
            return self._finish(run, result(RunStatus.SEMANTIC_VALIDATION_FAILURE, codes))
        artifact = ArtifactRecord(
            artifact_id=self._artifact_ids(),
            task_type=TASK_TYPE,
            task_version=TASK_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            account_id=run.account_id,
            interaction_id=run.interaction_id,
            canonical_result=canonical_json(parsed.model_dump(mode="json")),
            created_at=self._clock.now(),
            excerpts=tuple(
                ArtifactExcerpt(excerpt.excerpt_id, excerpt.source_interaction_id, excerpt.text, excerpt.attributed_speaker)
                for excerpt in parsed.source_excerpts
            ),
        )
        return self._finish(run, result(RunStatus.SUCCESS), artifact, parsed)

    def _finish(
        self, run: RunRecord, result: TerminalResult, artifact: ArtifactRecord | None = None,
        parsed: BaecExtractionOutput | None = None,
    ) -> ExtractionExecutionResult:
        self._store.record_terminal_outcome(TerminalOutcome(run.ai_run_id, result, artifact))
        return ExtractionExecutionResult(
            ai_run_id=run.ai_run_id, status=result.status, remote_outcome=result.remote_outcome,
            artifact_id=None if artifact is None else artifact.artifact_id, structured_output=parsed,
        )

    @staticmethod
    def _require_v1_spec(spec: AiRequestSpec, *, model: str, user_content: str) -> None:
        if type(spec) is not AiRequestSpec:
            raise AiRequestError("the provider prepared something other than an AiRequestSpec")
        expected = {
            "request_spec_version": REQUEST_SPEC_VERSION, "provider": PROVIDER, "api_method": API_METHOD,
            "model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT, "prompt_version": PROMPT_VERSION,
            "input_version": INPUT_VERSION, "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "messages": [{"role": "user", "content": user_content}],
        }
        actual = spec.to_json_object()
        for field, value in expected.items():
            if actual[field] != value:
                raise AiRequestError(f"the prepared request has an unexpected {field}")
        output_format = actual["output_config"].get("format")
        if set(actual["output_config"]) != {"format"} or type(output_format) is not dict \
                or set(output_format) != {"type", "schema"} or output_format["type"] != "json_schema":
            raise AiRequestError("the prepared request has an unexpected output_config")
