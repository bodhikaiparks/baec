"""Phase 6C-A: characterization of the installed Anthropic Python SDK (anthropic==1.9.0).

These tests record how the exact pinned SDK behaves at the boundary Phase 6 will
rely on. They feed deterministic, Anthropic-shaped responses into the real SDK
through httpx2.MockTransport: no request can reach Anthropic, and real sockets are
blocked as a backstop. The API key used here is a fake, test-only string.

Nothing here is production code. The output models below are test-only mirrors of
the approved Phase 6 output, close enough to characterize Structured Outputs.

Two kinds of test live here, and their docstrings or section headings say which:
- SDK CHARACTERIZATION: what anthropic==1.9.0 does by default. These pin observed
  behavior, including behavior Phase 6 deliberately avoids (messages.parse raising,
  default retries). If an SDK upgrade changes one of them, that is a signal for
  review, not a behavior to preserve.
- DESIGN DECISION: the production choice derived from that behavior
  (messages.create with explicit output_config, the application's own strict parse,
  exact Literal machine tokens, max_retries=0). See
  docs/PHASE6C_ANTHROPIC_SDK_CHARACTERIZATION.md.
"""

import enum
import importlib.metadata
import inspect
import json
import socket
from pathlib import Path
from typing import Literal, get_args

import anthropic
import httpx2
import pydantic
import pytest
from anthropic.types import Message, StopReason
from anthropic.types.model_param import ModelParam
from pydantic import BaseModel, ConfigDict, Field

FAKE_KEY = "test-only-not-a-real-key"
CANDIDATE_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No real network and no real credentials during these tests."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)


# --- test-only mirror of the approved Phase 6 output -----------------------------------------

_CLOSED = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceExcerptSpike(BaseModel):
    model_config = _CLOSED
    excerpt_id: str
    source_interaction_id: str
    text: str
    attributed_speaker: Literal["buyer", "seller", "unclear"]


class CriterionHypothesisSpike(BaseModel):
    model_config = _CLOSED
    criterion: Literal["present_non_evaluation", "prospective_condition", "buyer_articulation", "evaluation_linkage"]
    status: Literal["supported", "not_supported", "unclear"]
    excerpt_refs: list[str]
    explanation: str = Field(min_length=1, max_length=500)


class ExtractionSpike(BaseModel):
    model_config = _CLOSED
    analysis_status: Literal["possible_baec_language", "no_clear_baec_language", "insufficient_context"]
    source_excerpts: list[SourceExcerptSpike] = Field(max_length=20)
    normalized_condition: str | None
    normalized_evaluation_link: str | None
    criterion_hypotheses: list[CriterionHypothesisSpike]
    uncertainties: list[str] = Field(max_length=10)


VALID = {
    "analysis_status": "possible_baec_language",
    "source_excerpts": [{"excerpt_id": "e1", "source_interaction_id": "INT-1",
                         "text": "If pricing rises more than 10% at renewal, we'd look again.",
                         "attributed_speaker": "buyer"}],
    "normalized_condition": "A price increase above 10% at renewal.",
    "normalized_evaluation_link": "The buyer says such an increase would lead them to look at alternatives again.",
    "criterion_hypotheses": [
        {"criterion": c, "status": "unclear", "excerpt_refs": ["e1"], "explanation": "Short reason."}
        for c in ("present_non_evaluation", "prospective_condition", "buyer_articulation", "evaluation_linkage")
    ],
    "uncertainties": ["Whether the buyer is currently evaluating is not stated."],
}
VALID_TEXT = json.dumps(VALID, separators=(",", ":"))


# --- mock transport ---------------------------------------------------------------------------


class Recorder:
    """Scripted responses through the real SDK; records every HTTP attempt."""

    def __init__(self, respond):
        self.requests = []
        self._respond = respond

    def __call__(self, request):
        self.requests.append(request)
        return self._respond(request)


def message_json(content, stop_reason="end_turn", model="claude-sonnet-5-5", **extra):
    return {
        "id": "msg_synthetic_01", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1234, "output_tokens": 321, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0},
        **extra,
    }


def replying(content, stop_reason="end_turn", **extra):
    return Recorder(lambda request: httpx2.Response(
        200, json=message_json(content, stop_reason, **extra), headers={"request-id": "req_synthetic_01"}))


def failing_with(status, error_type, headers=None):
    return Recorder(lambda request: httpx2.Response(
        status, json={"type": "error", "error": {"type": error_type, "message": "synthetic"}},
        headers={"request-id": "req_synthetic_err", **(headers or {})}))


def raising(exception):
    def respond(request):
        raise exception
    return Recorder(respond)


def client(recorder, **options):
    options.setdefault("max_retries", 0)
    return anthropic.Anthropic(api_key=FAKE_KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)),
                               **options)


SCHEMA = anthropic.transform_schema(ExtractionSpike)
OUTPUT_CONFIG = {"format": {"type": "json_schema", "schema": SCHEMA}}


def create(recorder, **overrides):
    arguments = dict(model="claude-sonnet-5-5", max_tokens=4096, system="SYSTEM PROMPT",
                     messages=[{"role": "user", "content": '{"input_version":"x"}'}], output_config=OUTPUT_CONFIG)
    arguments.update(overrides)
    return client(recorder).messages.create(**arguments)


def parse(recorder, **overrides):
    arguments = dict(model="claude-sonnet-5-5", max_tokens=4096, system="SYSTEM PROMPT",
                     messages=[{"role": "user", "content": '{"input_version":"x"}'}], output_format=ExtractionSpike)
    arguments.update(overrides)
    return client(recorder).messages.parse(**arguments)


def text_block(text):
    return {"type": "text", "text": text}


# --- versions -----------------------------------------------------------------------------------


def test_the_pinned_sdk_versions_are_installed_and_recorded():
    assert anthropic.__version__ == importlib.metadata.version("anthropic") == "1.9.0"
    assert importlib.metadata.version("pydantic") == "2.13.5"
    assert importlib.metadata.version("mcp") == "2.2.0"
    requirements = (Path(__file__).resolve().parents[1] / "requirements-dev.txt").read_text(encoding="utf-8").split()
    assert "anthropic==1.9.0" in requirements and "mcp[cli]==2.2.0" in requirements


def test_the_sdk_transports_over_httpx2_and_accepts_an_injected_http_client():
    parameters = inspect.signature(anthropic.Anthropic.__init__).parameters
    assert "http_client" in parameters and parameters["max_retries"].default == anthropic.DEFAULT_MAX_RETRIES == 2


# --- SDK CHARACTERIZATION: messages.parse, and why Phase 6 does not use it -------------------------


def test_parse_signature_and_success_path():
    parameters = inspect.signature(anthropic.resources.messages.Messages.parse).parameters
    assert {"model", "max_tokens", "messages", "system", "output_format", "output_config"} <= set(parameters)
    recorder = replying([text_block(VALID_TEXT)])
    message = parse(recorder)
    assert type(message.parsed_output) is ExtractionSpike
    assert message.parsed_output.analysis_status == "possible_baec_language"
    assert [block.type for block in message.content] == ["text"] and message.content[0].text == VALID_TEXT
    body = json.loads(recorder.requests[0].content)
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


@pytest.mark.parametrize(
    "content,stop_reason",
    [
        ([text_block("I can't help with analyzing this conversation.")], "refusal"),
        ([text_block(VALID_TEXT[:40])], "max_tokens"),
        ([text_block(VALID_TEXT[:40])], "model_context_window_exceeded"),
        ([text_block(VALID_TEXT.replace("possible_baec_language", "Possible_BAEC_Language"))], "end_turn"),
        ([text_block(VALID_TEXT[:-1] + ',"confidence":0.9}')], "end_turn"),
    ],
    ids=["refusal-text", "max-tokens-truncated", "context-window-truncated", "wrong-case-token", "extra-field"],
)
def test_parse_raises_a_bare_validation_error_and_the_response_provenance_is_lost(content, stop_reason):
    """SDK CHARACTERIZATION. The 6A design named messages.parse. In 1.9.0 its post-parser validates every
    text block, so any non-conforming text raises pydantic.ValidationError and the caller never receives
    the Message: no message id, model, stop reason, usage, request id, or raw text to record. This is
    the reason for the messages.create decision; it is not behavior Phase 6 depends on."""
    recorder = replying(content, stop_reason)
    with pytest.raises(pydantic.ValidationError):
        parse(recorder)
    assert len(recorder.requests) == 1  # the response did arrive; only the Message is lost


def test_parse_has_no_raw_response_escape_hatch():
    assert not hasattr(client(replying([])).messages.with_raw_response, "parse")


# --- DESIGN DECISION: messages.create with explicit Structured Outputs, then a local strict parse ---


def test_create_sends_exactly_the_same_structured_output_request_as_parse():
    via_parse, via_create = replying([text_block(VALID_TEXT)]), replying([text_block(VALID_TEXT)])
    parse(via_parse)
    create(via_create)
    parse_body, create_body = (json.loads(r.requests[0].content) for r in (via_parse, via_create))
    assert parse_body == create_body
    assert sorted(create_body) == ["max_tokens", "messages", "model", "output_config", "system"]
    assert "temperature" not in create_body and "tools" not in create_body and "thinking" not in create_body


def test_create_returns_full_provenance_and_the_exact_structured_text():
    message = create(replying([text_block(VALID_TEXT)]))
    assert type(message) is Message
    assert (message.id, message.model, message.stop_reason, message._request_id) == (
        "msg_synthetic_01", "claude-sonnet-5-5", "end_turn", "req_synthetic_01")
    usage = message.usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_creation_input_tokens, usage.cache_read_input_tokens) == (
        1234, 321, 0, 0)
    texts = [block.text for block in message.content if block.type == "text"]
    assert texts == [VALID_TEXT]
    parsed = ExtractionSpike.model_validate_json(texts[0])  # the application's own strict parse
    assert parsed == ExtractionSpike.model_validate(VALID)


@pytest.mark.parametrize(
    "content,stop_reason",
    [
        ([text_block("I can't help with analyzing this conversation.")], "refusal"),
        ([], "refusal"),
        ([text_block(VALID_TEXT[:40])], "max_tokens"),
        ([text_block(VALID_TEXT[:40])], "model_context_window_exceeded"),
        ([text_block(VALID_TEXT)], "stop_sequence"),
        ([], "pause_turn"),
        ([text_block(VALID_TEXT.replace("possible_baec_language", "Possible_BAEC_Language"))], "end_turn"),
    ],
    ids=["refusal-text", "refusal-empty", "max-tokens", "context-window", "stop-sequence", "pause-turn", "wrong-case"],
)
def test_create_always_returns_the_message_so_every_outcome_keeps_its_provenance(content, stop_reason):
    message = create(replying(content, stop_reason))
    assert message.stop_reason == stop_reason and message.id == "msg_synthetic_01" and message._request_id
    assert [block.text for block in message.content if block.type == "text"] == [b["text"] for b in content]


def test_refusal_text_and_stop_details_are_available():
    details = {"type": "refusal", "category": "cyber", "explanation": "synthetic"}
    message = create(replying([text_block("I can't help with that.")], "refusal", stop_details=details))
    assert message.content[0].text == "I can't help with that."
    assert message.stop_details is not None and set(type(message.stop_details).model_fields) == {"type", "category", "explanation"}


@pytest.mark.parametrize(
    "text",
    [VALID_TEXT[:40], VALID_TEXT.replace("possible_baec_language", "POSSIBLE_BAEC_LANGUAGE"),
     VALID_TEXT[:-1] + ',"confidence":0.9}', VALID_TEXT.replace('"buyer"', '"Buyer"'),
     VALID_TEXT.replace('"criterion":"present_non_evaluation"', '"criterion":"PRESENT_NON_EVALUATION"')],
    ids=["truncated", "uppercase-status", "extra-field", "speaker-case", "domain-style-criterion"],
)
def test_the_applications_strict_parse_refuses_non_exact_machine_tokens_without_normalizing(text):
    with pytest.raises(pydantic.ValidationError):
        ExtractionSpike.model_validate_json(text)


def test_thinking_blocks_are_distinguishable_and_never_part_of_the_structured_text():
    content = [{"type": "thinking", "thinking": "hidden reasoning", "signature": "sig"}, text_block(VALID_TEXT)]
    message = create(replying(content))
    assert [block.type for block in message.content] == ["thinking", "text"]
    texts = [block.text for block in message.content if block.type == "text"]
    assert texts == [VALID_TEXT] and "hidden reasoning" not in texts[0]


def test_multiple_text_blocks_are_reported_as_they_are():
    message = create(replying([text_block(VALID_TEXT), text_block(VALID_TEXT)]))
    assert len([block for block in message.content if block.type == "text"]) == 2


def test_the_stop_reason_vocabulary_of_the_installed_sdk():
    assert set(get_args(StopReason)) == {
        "end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"}


# --- machine tokens (DESIGN DECISION: strict Literal) and schema (SDK CHARACTERIZATION) ----------


class _StrEnumStatus(enum.StrEnum):
    POSSIBLE = "possible_baec_language"
    NO_CLEAR = "no_clear_baec_language"


@pytest.mark.parametrize("annotation", [Literal["possible_baec_language", "no_clear_baec_language"], _StrEnumStatus],
                         ids=["Literal", "StrEnum"])
def test_literal_and_strenum_are_both_strict_and_exact(annotation):
    class Model(BaseModel):
        model_config = _CLOSED
        status: annotation

    assert Model.model_validate_json('{"status":"no_clear_baec_language"}').status == "no_clear_baec_language"
    for wrong in ("No_Clear_BAEC_Language", "NO_CLEAR_BAEC_LANGUAGE", " no_clear_baec_language"):
        with pytest.raises(pydantic.ValidationError):
            Model.model_validate_json(json.dumps({"status": wrong}))
    schema = anthropic.transform_schema(Model)
    enum_values = schema["properties"]["status"].get("enum") or schema["$defs"]["_StrEnumStatus"]["enum"]
    assert enum_values == ["possible_baec_language", "no_clear_baec_language"]


def test_literal_tokens_are_inlined_in_the_transformed_schema():
    status = SCHEMA["properties"]["analysis_status"]
    assert status == {"type": "string", "enum": ["possible_baec_language", "no_clear_baec_language",
                                                "insufficient_context"], "title": "Analysis Status"}


def test_transform_schema_is_public_deterministic_and_closed():
    assert anthropic.transform_schema is not None
    first, second = anthropic.transform_schema(ExtractionSpike), anthropic.transform_schema(ExtractionSpike)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["additionalProperties"] is False and set(first["required"]) == set(ExtractionSpike.model_fields)
    for definition in first["$defs"].values():
        assert definition["additionalProperties"] is False


def test_transform_schema_moves_unsupported_constraints_into_descriptions_so_the_api_does_not_enforce_them():
    explanation = SCHEMA["$defs"]["CriterionHypothesisSpike"]["properties"]["explanation"]
    assert "maxLength" not in explanation and "minLength" not in explanation
    assert explanation["description"] == "{maxLength: 500, minLength: 1}"
    assert "maxItems" not in SCHEMA["properties"]["source_excerpts"]


# --- SDK CHARACTERIZATION: request surface --------------------------------------------------------


def test_the_create_surface_has_no_legacy_sampling_parameters_and_knows_the_candidate_models():
    parameters = set(inspect.signature(anthropic.resources.messages.Messages.create).parameters)
    assert not parameters & {"temperature", "top_p", "top_k"}
    assert {"model", "max_tokens", "system", "messages", "output_config"} <= parameters
    known = set(get_args(get_args(ModelParam)[0])) if get_args(ModelParam) else set()
    assert set(CANDIDATE_MODELS) <= known


def test_the_sdk_adds_its_own_headers_which_are_not_application_request_fields():
    recorder = replying([text_block(VALID_TEXT)])
    create(recorder)
    request = recorder.requests[0]
    assert request.headers["x-api-key"] == FAKE_KEY  # authentication travels only as a header
    assert FAKE_KEY not in request.content.decode()
    assert {"anthropic-version", "x-stainless-retry-count", "user-agent"} <= set(request.headers)


def test_a_large_max_tokens_with_default_timeouts_fails_locally_before_any_attempt():
    recorder = replying([text_block(VALID_TEXT)])
    with pytest.raises(ValueError, match="Streaming is required"):
        create(recorder, max_tokens=30_000)
    assert recorder.requests == []


# --- SDK CHARACTERIZATION: errors (the mapping to 6B statuses is a design decision in the doc) ------

STATUS_ERRORS = [
    (400, "invalid_request_error", anthropic.BadRequestError),
    (401, "authentication_error", anthropic.AuthenticationError),
    (403, "permission_error", anthropic.PermissionDeniedError),
    (404, "not_found_error", anthropic.NotFoundError),
    (413, "request_too_large", anthropic.RequestTooLargeError),
    (429, "rate_limit_error", anthropic.RateLimitError),
    (500, "api_error", anthropic.InternalServerError),
    (503, "api_error", anthropic.InternalServerError),
    (504, "api_error", anthropic.InternalServerError),
    (529, "overloaded_error", anthropic.OverloadedError),
]


@pytest.mark.parametrize("status,error_type,exception", STATUS_ERRORS, ids=[str(s) for s, _, _ in STATUS_ERRORS])
def test_status_responses_raise_typed_errors_with_status_request_id_and_no_key(status, error_type, exception):
    recorder = failing_with(status, error_type)
    with pytest.raises(anthropic.APIStatusError) as raised:
        create(recorder)
    error = raised.value
    assert type(error) is exception and error.status_code == status and error.request_id == "req_synthetic_err"
    assert error.body["error"]["type"] == error_type
    assert FAKE_KEY not in str(error) and FAKE_KEY not in repr(error.body)
    assert len(recorder.requests) == 1


TRANSPORT_ERRORS = [
    (httpx2.ConnectError("refused"), anthropic.APIConnectionError),
    (httpx2.ConnectTimeout("t"), anthropic.APITimeoutError),
    (httpx2.ReadTimeout("t"), anthropic.APITimeoutError),
    (httpx2.WriteTimeout("t"), anthropic.APITimeoutError),
    (httpx2.PoolTimeout("t"), anthropic.APITimeoutError),
    (httpx2.ReadError("r"), anthropic.APIConnectionError),
    (httpx2.WriteError("w"), anthropic.APIConnectionError),
    (httpx2.RemoteProtocolError("p"), anthropic.APIConnectionError),
]


@pytest.mark.parametrize("cause,exception", TRANSPORT_ERRORS, ids=[type(c).__name__ for c, _ in TRANSPORT_ERRORS])
def test_transport_errors_raise_connection_errors_with_the_httpx2_cause_kept(cause, exception):
    recorder = raising(cause)
    with pytest.raises(anthropic.APIConnectionError) as raised:
        create(recorder)
    assert type(raised.value) is exception
    assert raised.value.__cause__ is cause  # the public cause chain says which phase failed
    assert issubclass(anthropic.APITimeoutError, anthropic.APIConnectionError)
    assert len(recorder.requests) == 1


# --- DESIGN DECISION: max_retries=0; the SDK default is characterized for comparison only ---------


@pytest.mark.parametrize(
    "recorder_factory",
    [
        lambda: failing_with(500, "api_error", {"retry-after-ms": "0"}),
        lambda: failing_with(529, "overloaded_error", {"retry-after-ms": "0"}),
        lambda: failing_with(429, "rate_limit_error", {"retry-after-ms": "0"}),
        lambda: failing_with(400, "invalid_request_error", {"x-should-retry": "true", "retry-after-ms": "0"}),
        lambda: raising(httpx2.ConnectError("refused")),
        lambda: raising(httpx2.ReadTimeout("t")),
    ],
    ids=["500", "529", "429", "x-should-retry", "connect-error", "read-timeout"],
)
def test_max_retries_zero_makes_exactly_one_http_attempt(recorder_factory):
    recorder = recorder_factory()
    with pytest.raises(anthropic.APIError):
        create(recorder)
    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers["x-stainless-retry-count"] == "0"


def test_the_sdk_default_would_retry_twice():
    """SDK CHARACTERIZATION only: the default client makes three attempts on a retryable status.
    Production never uses the default; a change here after an upgrade needs review, not preservation."""
    recorder = failing_with(500, "api_error", {"retry-after-ms": "0"})
    sdk_default = anthropic.Anthropic(api_key=FAKE_KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)))
    with pytest.raises(anthropic.InternalServerError):
        sdk_default.messages.create(model="claude-sonnet-5-5", max_tokens=100, messages=[{"role": "user", "content": "x"}])
    assert [r.headers["x-stainless-retry-count"] for r in recorder.requests] == ["0", "1", "2"]
