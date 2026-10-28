"""Stage H: the single controlled live Engine 2 correspondence-proposal experiment (harness only).

Opt-in and outside baec_app/; the gated pytest entry is tests/live_engine2/test_live_engine2_stage_h.py.
Importing or collecting this module makes no network activity:
the Anthropic client is constructed only by run_live, and only after every gate is open.

Locked experiment choices (Stage H approval):
- provider "anthropic", exact model "claude-sonnet-5-5": no alias, substitution, newer-model
  query, or model-selection logic;
- case HBR-CORR-011, whose AI input must hash to INPUT_DIGEST;
- prompt-only JSON: the locked Stage F SYSTEM_PROMPT, with OUTPUT_SCHEMA and the canonical AI input as
  user text. No Structured Outputs schema. Stage F, through Stage G, is the only validator;
- anthropic.Anthropic(api_key=<ANTHROPIC_API_KEY>, base_url="https://api.anthropic.com", max_retries=0,
  timeout=180.0): the key is passed explicitly and never recorded; no auth token, alternate endpoint, or
  environment-enabled SDK logging is permitted. Thinking {"type": "between_tools"} with no tools,
  effort "high". One messages.create call at most: no retry, fallback, repair, or continuation call.

Order of work:
1. The fixed input is built and its digest checked; a fresh BAEC_ENGINE_2_AI database is created in an
   explicitly supplied, empty directory outside the repository.
2. The AI input and the attempt (requested provider and model, producer commit) are committed.
3. One provider invocation. Any provider failure, unexpected stop reason, or response shape other than
   exactly one plain text block ends the run; the attempt stays and nothing else is written.
4. The response is recorded with ModelResponse.model = message.model, verbatim. Stage G decides whether it
   matches the attempt; a refusal ends the run with no response, validation, or proposal row.
5. Stage G validation (ACCEPTED or REJECTED; a Stage F rejection is a valid result), then
   verify_ai_database, then the SHA-256 of the closed database file.
6. A sanitized canonical run record is written next to the database. It never holds credentials, the
   raw model output (that lives only in the Stage G store), or provider error text.

These are software checks on synthetic cases. Nothing here validates BAEC theory or measures accuracy.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

import anthropic

from baec_app.engine2 import ai_persistence as store
from baec_app.engine2.ai import (INPUT_VERSION, OUTPUT_SCHEMA, OUTPUT_SCHEMA_VERSION, PROMPT_VERSION, SYSTEM_PROMPT,
                                 CorrespondenceAIInput, ModelResponse)
from tests.engine2.ai_replay import ai_input_for
from tests.engine2.corpus_adapter import load_corpus, run_case

HARNESS_VERSION = "baec-e2-stage-h-harness/v1"
RUN_RECORD_VERSION = "baec-e2-stage-h-run-record/v1"
PROVIDER = "anthropic"
MODEL = "claude-sonnet-5-5"
CASE_ID = "HBR-CORR-011"
INPUT_DIGEST = "dcb061385e0c6294735e099f058eb27503baf33a86e031c83210dea932b727f4"
TIMEOUT_SECONDS = 180.0  # the reviewed Engine 1 transport policy, unchanged
MAX_TOKENS = 16000  # Stage H implementation choice: room for 11 proposals; below the SDK non-streaming limit
THINKING = {"type": "between_tools"}
OUTPUT_CONFIG = {"effort": "high"}
ATTEMPT_ID = "STAGE-H-ATTEMPT-1"
ARTIFACT_ID = "STAGE-H-PROPOSAL-1"
ACTOR = "STAGE-H-HARNESS"
DATABASE_NAME = "stage_h_ai_audit.sqlite3"
RUN_RECORD_NAME = "stage_h_run_record.json"
REPO = Path(__file__).resolve().parents[2]

LIVE_FLAG = "BAEC_LIVE_E2_STAGE_H"
RESEARCH_DIR_VARIABLE = "BAEC_LIVE_E2_RESEARCH_DIR"
API_KEY_VARIABLE = "ANTHROPIC_API_KEY"  # the only credential source; the value is never printed or recorded
CREDENTIAL_MODE = "api_key"
ENDPOINT = "https://api.anthropic.com"  # the normal Claude API endpoint; not configurable
# Any of these, set nonblank, closes the gate: another credential, an endpoint override, SDK logging.
REFUSED_VARIABLES = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_LOG")


class LiveGateClosed(Exception):
    """A precondition for the live run is not met. Raised before any client exists."""


class ResponseShapeKilled(Exception):
    """The provider response is not exactly one plain text block ending in end_turn. Never repaired."""


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def experiment_input() -> CorrespondenceAIInput:
    """The one fixed Stage H input. Oracle fields stay in the corpus; only the neutral input is returned."""
    corpus = load_corpus()
    case = next(c for c in corpus["cases"] if c["case_id"] == CASE_ID)
    ai_input = ai_input_for(corpus, run_case(corpus, case))
    if ai_input.input_digest != INPUT_DIGEST:
        raise RuntimeError("the Stage H input no longer hashes to the locked digest")
    return ai_input


def user_text(ai_input: CorrespondenceAIInput, output_schema: dict) -> str:
    return (f"Output schema ({OUTPUT_SCHEMA_VERSION}, JSON Schema):\n{canonical(output_schema)}\n\n"
            f"Evidence snapshot ({INPUT_VERSION}, input digest {ai_input.input_digest}):\n{ai_input.canonical_json}")


def request_arguments(ai_input: CorrespondenceAIInput, *, system_prompt: str, output_schema: dict) -> dict:
    """The complete messages.create arguments. No tools, no output format, no sampling parameters."""
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system_prompt,
            "messages": [{"role": "user", "content": user_text(ai_input, output_schema)}],
            "thinking": dict(THINKING), "output_config": dict(OUTPUT_CONFIG)}


def make_client(api_key: str, http_client=None) -> anthropic.Anthropic:
    """The only client constructor: explicit key and endpoint. Offline tests inject a mock http_client."""
    if http_client is None:
        return anthropic.Anthropic(api_key=api_key, base_url=ENDPOINT, max_retries=0, timeout=TIMEOUT_SECONDS)
    return anthropic.Anthropic(api_key=api_key, base_url=ENDPOINT, max_retries=0, timeout=TIMEOUT_SECONDS,
                               http_client=http_client)


class StageHAnthropicModel:
    """A CorrespondenceProposalModel for exactly one Anthropic invocation.

    Facts observed about the call (identifiers, stop reason, usage, block types, failure category) are kept
    in `observed` for the run record. The response text is never copied there.
    """

    def __init__(self, client: anthropic.Anthropic) -> None:
        if type(client) is not anthropic.Anthropic or client.max_retries != 0 \
                or type(client.timeout) is not float or client.timeout != TIMEOUT_SECONDS:
            raise ValueError("the client must be anthropic.Anthropic with max_retries=0 and the locked timeout")
        if type(client.api_key) is not str or client.auth_token is not None or str(client.base_url) != ENDPOINT:
            raise ValueError("the client must use an explicit API key, no auth token, and the Claude API endpoint")
        self._client = client
        self.invocations = 0
        self.observed: dict = {}

    def propose(self, ai_input: CorrespondenceAIInput, *, system_prompt: str, output_schema: dict) -> ModelResponse:
        if system_prompt is not SYSTEM_PROMPT or output_schema is not OUTPUT_SCHEMA:
            raise ValueError("Stage H sends only the locked Stage F prompt and schema")
        if self.invocations:
            raise RuntimeError("Stage H allows one provider invocation")
        self.invocations += 1
        try:
            message = self._client.messages.create(
                **request_arguments(ai_input, system_prompt=system_prompt, output_schema=output_schema))
        except anthropic.APIStatusError as error:
            self.observed.update(failure_category="api_status_error", http_status=error.status_code,
                                 provider_request_id=error.request_id)
            raise
        except anthropic.APITimeoutError:
            self.observed.update(failure_category="timeout")
            raise
        except anthropic.APIConnectionError:
            self.observed.update(failure_category="connection_error")
            raise
        except anthropic.APIError as error:
            self.observed.update(failure_category=f"api_error:{type(error).__name__}")
            raise
        usage = message.usage
        self.observed.update(
            provider_request_id=message._request_id, provider_message_id=message.id, returned_model=message.model,
            stop_reason=message.stop_reason, content_block_types=[block.type for block in message.content],
            usage={"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                   "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                   "cache_read_input_tokens": usage.cache_read_input_tokens})
        if message.type != "message" or message.role != "assistant":
            raise ResponseShapeKilled("unexpected_message_shape")
        if message.stop_reason != "end_turn":
            raise ResponseShapeKilled("unexpected_stop_reason")
        if len(message.content) == 0:
            raise ResponseShapeKilled("zero_content_blocks")
        if len(message.content) > 1:
            raise ResponseShapeKilled("multiple_content_blocks")
        block = message.content[0]
        if block.type != "text" or type(block.text) is not str or block.citations:
            raise ResponseShapeKilled("non_text_content_block")
        return ModelResponse(raw_text=block.text, provider=PROVIDER, model=message.model)


@dataclass(frozen=True)
class StageHRun:
    record: dict
    record_text: str
    record_sha256: str
    database_path: Path
    record_path: Path


def _fresh_research_dir(research_dir: Path) -> Path:
    if not isinstance(research_dir, Path) or not research_dir.is_absolute():
        raise LiveGateClosed("the research directory must be an explicit absolute path")
    resolved = research_dir.resolve()
    if not resolved.is_dir():
        raise LiveGateClosed("the research directory must exist")
    if resolved == REPO or REPO in resolved.parents:
        raise LiveGateClosed("the research directory must be outside the repository")
    if any(resolved.iterdir()):
        raise LiveGateClosed("the research directory must be empty, so the AI database is new")
    return resolved


def _validate_commit(commit: str) -> str:
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise LiveGateClosed("producer_commit must be a full 40-character lowercase commit id")
    return commit


def run_stage_h(*, client: anthropic.Anthropic, research_dir: Path, producer_commit: str,
                clock: Callable[[], datetime]) -> StageHRun:
    """Run the experiment once against a fresh AI database in research_dir and write the run record."""
    directory = _fresh_research_dir(research_dir)
    commit = _validate_commit(producer_commit)
    model = StageHAnthropicModel(client)
    ai_input = experiment_input()
    arguments = request_arguments(ai_input, system_prompt=SYSTEM_PROMPT, output_schema=OUTPUT_SCHEMA)
    database_path, record_path = directory / DATABASE_NAME, directory / RUN_RECORD_NAME
    record = {
        "run_record_version": RUN_RECORD_VERSION, "harness_version": HARNESS_VERSION, "repository_commit": commit,
        "python_version": platform.python_version(), "anthropic_sdk_version": anthropic.__version__,
        "case_id": CASE_ID, "input_digest": ai_input.input_digest, "attempt_id": ATTEMPT_ID, "provider": PROVIDER,
        "provider_endpoint": ENDPOINT, "credential_mode": CREDENTIAL_MODE,
        "requested_model": MODEL, "returned_model": None, "prompt_version": PROMPT_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION, "system_prompt_sha256": sha256(SYSTEM_PROMPT),
        "output_schema_sha256": sha256(canonical(OUTPUT_SCHEMA)), "request_sha256": sha256(canonical(arguments)),
        "request_settings": {"max_retries": 0, "timeout_seconds": TIMEOUT_SECONDS, "max_tokens": MAX_TOKENS,
                             "thinking": THINKING, "output_config": OUTPUT_CONFIG, "tools": "omitted"},
        "attempt_requested_at": None, "call_started_at": None, "call_ended_at": None,
        "provider_request_id": None, "provider_message_id": None, "stop_reason": None, "content_block_types": None,
        "usage": None, "http_status": None, "failure_category": None, "outcome": None,
        "validation_status": None, "validation_error_category": None, "proposal_id": None,
        "ai_database_verification": None, "ai_database_counts": None, "ai_database_sha256": None,
    }
    connection = store.open_ai_audit_database(str(database_path))
    pending: BaseException | None = None
    try:
        requested_at = clock()
        store.record_ai_input(connection, ai_input, recorded_at=requested_at, recorded_by=ACTOR)
        store.record_ai_attempt(connection, attempt_id=ATTEMPT_ID, input_digest=ai_input.input_digest,
                                provider=PROVIDER, model=MODEL, requested_at=requested_at, requested_by=ACTOR,
                                producer_commit=commit)
        record["attempt_requested_at"] = requested_at.isoformat()
        record["call_started_at"] = clock().isoformat()
        try:
            response = model.propose(ai_input, system_prompt=SYSTEM_PROMPT, output_schema=OUTPUT_SCHEMA)
        except anthropic.APIError:
            response, record["outcome"] = None, "PROVIDER_FAILURE"
        except ResponseShapeKilled as killed:
            response, record["outcome"], record["failure_category"] = None, "RESPONSE_SHAPE_KILLED", str(killed)
        finally:
            record["call_ended_at"] = clock().isoformat()
            record.update(model.observed)
        if response is not None:
            try:
                store.record_ai_response(connection, ATTEMPT_ID, response, received_at=clock(), recorded_by=ACTOR,
                                         provider_response_ref=model.observed["provider_message_id"])
            except store.AIWriteRefused:
                record["outcome"], record["failure_category"] = "STAGE_G_IDENTITY_REFUSED", "provider_model_mismatch"
            else:
                when = clock()
                validation = store.record_ai_validation(connection, ATTEMPT_ID, artifact_id=ARTIFACT_ID,
                                                        created_at=when, validated_at=when, validated_by=ACTOR)
                record["outcome"], record["validation_status"] = "VALIDATED", validation.status
                record["validation_error_category"] = validation.error_category
                record["proposal_id"] = validation.proposal.artifact_id if validation.proposal else None
    except BaseException as error:  # recorded as a category only, then re-raised after the record is written
        pending = error
        record["outcome"] = record["outcome"] or "HARNESS_ERROR"
        record["failure_category"] = record["failure_category"] or f"unexpected:{type(error).__name__}"
    finally:
        try:
            record["ai_database_counts"] = store.verify_ai_database(connection)
            record["ai_database_verification"] = "VERIFIED"
        except store.AIStoreError as failed:
            record["ai_database_verification"] = f"FAILED:{type(failed).__name__}"
        finally:
            connection.close()
    record["ai_database_sha256"] = hashlib.sha256(database_path.read_bytes()).hexdigest()
    text = canonical(record)
    record_path.write_text(text, encoding="utf-8")
    if pending is not None:
        raise pending
    return StageHRun(record, text, sha256(text), database_path, record_path)


# --- live entry point (Stage H-B only, after separate approval) ----------------------------------------


def _clean_head() -> str:
    """The exact committed code the run uses. Local git only."""
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True)
    if status.stdout:
        raise LiveGateClosed("the working tree must be clean")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True)
    return head.stdout.removesuffix("\n")


def live_gate(environ: Mapping[str, str]) -> Path:
    """Every precondition, checked before a client exists. Returns the research directory."""
    if environ.get(LIVE_FLAG) != "1":
        raise LiveGateClosed(f"{LIVE_FLAG}=1 is not set")
    if not environ.get(RESEARCH_DIR_VARIABLE):
        raise LiveGateClosed(f"{RESEARCH_DIR_VARIABLE} is not set")
    research_dir = _fresh_research_dir(Path(environ[RESEARCH_DIR_VARIABLE]))
    if _blank(environ.get(API_KEY_VARIABLE)):
        raise LiveGateClosed(f"{API_KEY_VARIABLE} is not set")
    refused = [name for name in REFUSED_VARIABLES if not _blank(environ.get(name))]
    if refused:
        raise LiveGateClosed(f"{refused[0]} must not be set for Stage H")
    return research_dir


def _blank(value: str | None) -> bool:
    return value is None or value == "" or value.isspace()


def run_live() -> StageHRun:
    """The live run. Reads the process environment, the same one the SDK sees."""
    research_dir = live_gate(os.environ)
    commit = _clean_head()
    return run_stage_h(client=make_client(os.environ[API_KEY_VARIABLE]), research_dir=research_dir,
                       producer_commit=commit, clock=lambda: datetime.now(timezone.utc))
