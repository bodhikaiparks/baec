"""Phase 6C-B: AnthropicExtractionProvider against the pinned SDK through a mock transport.

No request can reach Anthropic: every client uses httpx2.MockTransport and real
sockets are blocked. The key below is a fake, test-only string.
"""

import json
import socket

import anthropic
import httpx2
import pytest

from baec_app.ai import anthropic_provider
from baec_app.ai.anthropic_provider import ANTHROPIC_TIMEOUT_SECONDS, AnthropicExtractionProvider, AnthropicSchemaDriftError
from baec_app.ai.contracts import BaecExtractionOutput
from baec_app.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from baec_app.ai.provider import SENT_FIELDS, AiRequestSpec, ProviderApiError, ProviderTransportError
from baec_app.ai.service import canonical_input
from tests.ai_builders import THRESHOLD_TEXT, as_text, output

FAKE_KEY = "test-only-not-a-real-key"
MODEL = "claude-test-model-5"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)


class Transport:
    def __init__(self, respond):
        self.requests, self._respond = [], respond

    def __call__(self, request):
        self.requests.append(request)
        return self._respond(request)


def message(content, stop_reason="end_turn", model=MODEL):
    return {"id": "msg_01", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1500, "output_tokens": 250, "cache_creation_input_tokens": 3,
                      "cache_read_input_tokens": 4}}


def replying(content, stop_reason="end_turn", model=MODEL):
    return Transport(lambda request: httpx2.Response(200, json=message(content, stop_reason, model),
                                                     headers={"request-id": "req_01"}))


def failing(status):
    return Transport(lambda request: httpx2.Response(
        status, json={"type": "error", "error": {"type": "x", "message": "SECRET-BODY-TEXT"}},
        headers={"request-id": "req_err", "retry-after-ms": "0", "x-should-retry": "true"}))


def raising(exception):
    def respond(request):
        raise exception
    return Transport(respond)


def provider_for(transport, max_retries=0, timeout=ANTHROPIC_TIMEOUT_SECONDS):
    client = anthropic.Anthropic(api_key=FAKE_KEY, max_retries=max_retries, timeout=timeout,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
    return AnthropicExtractionProvider(client)


def spec_for(provider):
    content = canonical_input(account_id="ACC-1", interaction_id="INT-T", interaction_text=THRESHOLD_TEXT)
    return provider.prepare_request(model=MODEL, max_tokens=4096, system=SYSTEM_PROMPT, user_content=content,
                                    prompt_version=PROMPT_VERSION, input_version="baec-extraction-input/v1",
                                    output_schema_version="baec-extraction-output/v1")


# --- construction ----------------------------------------------------------------------------


def test_the_default_client_has_retries_disabled_and_no_explicit_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)  # the SDK reads it from the environment
    provider = AnthropicExtractionProvider()
    assert type(provider._client) is anthropic.Anthropic and provider._client.max_retries == 0
    assert ANTHROPIC_TIMEOUT_SECONDS == 180.0
    assert type(provider._client.timeout) is float and provider._client.timeout == 180.0
    assert (provider.provider_name, provider.sdk_name, provider.sdk_version) == ("anthropic", "anthropic", anthropic.__version__)


def test_a_client_that_retries_or_is_not_the_sdk_client_is_refused():
    with pytest.raises(ValueError):
        provider_for(replying([]), max_retries=2)
    with pytest.raises(ValueError):  # the SDK default timeout, not the locked one
        AnthropicExtractionProvider(anthropic.Anthropic(api_key=FAKE_KEY, max_retries=0))
    for other in (60.0, 600.0, httpx2.Timeout(180.0)):
        with pytest.raises(ValueError):
            provider_for(replying([]), timeout=other)
    with pytest.raises(ValueError):
        AnthropicExtractionProvider(object())


# --- request preparation ---------------------------------------------------------------------


def test_prepare_request_is_local_and_builds_the_exact_v1_spec():
    transport = replying([])
    provider = provider_for(transport)
    spec = spec_for(provider)
    assert transport.requests == []  # purely local
    assert type(spec) is AiRequestSpec
    semantic = spec.to_json_object()
    assert (semantic["provider"], semantic["api_method"], semantic["model"], semantic["max_tokens"]) == (
        "anthropic", "messages.create", MODEL, 4096)
    assert semantic["output_config"] == {"format": {"type": "json_schema",
                                                    "schema": anthropic.transform_schema(BaecExtractionOutput)}}
    assert semantic["messages"] == [{"role": "user", "content": canonical_input(
        account_id="ACC-1", interaction_id="INT-T", interaction_text=THRESHOLD_TEXT)}]


def test_an_unsupported_output_schema_version_is_refused():
    provider = provider_for(replying([]))
    with pytest.raises(ValueError):
        provider.prepare_request(model=MODEL, max_tokens=4096, system="s", user_content="{}", prompt_version="p",
                                 input_version="i", output_schema_version="baec-extraction-output/v2")


# --- the call -----------------------------------------------------------------------------------


def test_invoke_calls_messages_create_once_with_arguments_taken_field_for_field_from_the_spec(monkeypatch):
    def no_parse(*args, **kwargs):
        raise AssertionError("messages.parse must not be used")

    monkeypatch.setattr(anthropic.resources.messages.Messages, "parse", no_parse)
    transport = replying([{"type": "text", "text": as_text(output())}])
    provider = provider_for(transport)
    spec = spec_for(provider)
    provider.invoke(spec)
    assert len(transport.requests) == 1
    request = transport.requests[0]
    body = json.loads(request.content)
    assert body == spec.sdk_arguments()
    assert sorted(body) == sorted(SENT_FIELDS)
    for absent in ("tools", "tool_choice", "thinking", "temperature", "top_p", "top_k", "metadata", "stop_sequences"):
        assert absent not in body
    assert body["max_tokens"] == 4096 and request.url.path == "/v1/messages"
    assert "x-stainless-helper" not in request.headers  # the parse helper marks its requests; create does not
    assert FAKE_KEY not in request.content.decode()


def test_the_response_keeps_text_blocks_in_order_and_drops_everything_else():
    content = [{"type": "thinking", "thinking": "HIDDEN-REASONING", "signature": "sig"},
               {"type": "text", "text": "first"}, {"type": "redacted_thinking", "data": "opaque"},
               {"type": "text", "text": "  second\n"}]
    provider = provider_for(replying(content, "end_turn"))
    result = provider.invoke(spec_for(provider))
    assert result.text_blocks == ("first", "  second\n")
    assert "HIDDEN-REASONING" not in repr(result)
    assert (result.provider_message_id, result.response_model, result.stop_reason, result.provider_request_id) == (
        "msg_01", MODEL, "end_turn", "req_01")
    assert (result.input_tokens, result.output_tokens, result.cache_creation_input_tokens,
            result.cache_read_input_tokens) == (1500, 250, 3, 4)


@pytest.mark.parametrize("stop", ["refusal", "max_tokens", "model_context_window_exceeded", "pause_turn"])
def test_every_stop_reason_returns_a_response_with_its_text(stop):
    provider = provider_for(replying([{"type": "text", "text": "partial {"}], stop))
    result = provider.invoke(spec_for(provider))
    assert result.stop_reason == stop and result.text_blocks == ("partial {",)


# --- failures ---------------------------------------------------------------------------------------

STATUS_CATEGORIES = {400: "invalid_request", 401: "authentication", 403: "permission", 404: "invalid_request",
                     409: "other", 413: "invalid_request", 422: "invalid_request", 429: "rate_limited",
                     500: "server_error", 503: "server_error", 504: "server_error", 529: "overloaded"}


@pytest.mark.parametrize("status", STATUS_CATEGORIES)
def test_api_status_errors_map_to_the_locked_categories_in_one_attempt(status):
    transport = failing(status)
    provider = provider_for(transport)
    with pytest.raises(ProviderApiError) as raised:
        provider.invoke(spec_for(provider))
    error = raised.value
    assert (error.category, error.provider_request_id) == (STATUS_CATEGORIES[status], "req_err")
    assert len(transport.requests) == 1  # no retry, even when the server asks for one
    assert "SECRET-BODY-TEXT" not in str(error) and FAKE_KEY not in str(error)
    assert error.__cause__ is None


class _ConnectErrorSubclass(httpx2.ConnectError):
    pass


TRANSPORT = [
    (httpx2.ConnectError("refused"), "connection_not_established", "not_sent"),
    (_ConnectErrorSubclass("refused"), "timeout_or_disconnect", "unknown"),
    (httpx2.ConnectTimeout("t"), "timeout_or_disconnect", "unknown"),
    (httpx2.ReadTimeout("t"), "timeout_or_disconnect", "unknown"),
    (httpx2.WriteTimeout("t"), "timeout_or_disconnect", "unknown"),
    (httpx2.ReadError("r"), "timeout_or_disconnect", "unknown"),
    (httpx2.RemoteProtocolError("p"), "timeout_or_disconnect", "unknown"),
]


@pytest.mark.parametrize("cause,category,outcome", TRANSPORT,
                         ids=["ConnectError", "ConnectError-subclass", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
                              "ReadError", "RemoteProtocolError"])
def test_transport_failures_map_exactly_and_not_sent_only_for_connect_error(cause, category, outcome):
    transport = raising(cause)
    provider = provider_for(transport)
    with pytest.raises(ProviderTransportError) as raised:
        provider.invoke(spec_for(provider))
    assert (raised.value.category, raised.value.remote_outcome) == (category, outcome)
    assert len(transport.requests) == 1


def test_schema_drift_is_refused_before_any_http_attempt(monkeypatch):
    from pydantic import BaseModel, ConfigDict

    class Drifted(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        analysis_status: str

    transport = replying([])
    provider = provider_for(transport)
    spec = spec_for(provider)
    monkeypatch.setitem(anthropic_provider._OUTPUT_MODELS, "baec-extraction-output/v1", Drifted)
    with pytest.raises(AnthropicSchemaDriftError):
        provider.invoke(spec)
    assert transport.requests == []


def test_invoke_refuses_a_spec_for_another_provider_or_method():
    from dataclasses import replace

    provider = provider_for(replying([]))
    spec = spec_for(provider)
    for change in ({"provider": "openai"}, {"api_method": "messages.parse"}):
        with pytest.raises(ValueError):
            provider.invoke(replace(spec, **change))


# --- timeout is transport configuration, never request content ---------------------------------


def test_the_timeout_is_not_part_of_the_spec_its_digest_or_the_request_body():
    transport = replying([{"type": "text", "text": as_text(output())}])
    provider = provider_for(transport)
    spec = spec_for(provider)
    semantic = json.dumps(spec.to_json_object())
    assert "timeout" not in semantic and "180" not in semantic
    provider.invoke(spec)
    body = json.loads(transport.requests[0].content)
    assert "timeout" not in body
    # applied as transport configuration only: every phase of the one HTTP attempt is bounded by 180 s
    assert transport.requests[0].extensions["timeout"] == {"connect": 180.0, "read": 180.0, "write": 180.0, "pool": 180.0}


def test_a_timeout_still_maps_to_an_unknown_transport_failure():
    transport = raising(httpx2.ReadTimeout("t"))
    provider = provider_for(transport)
    with pytest.raises(ProviderTransportError) as raised:
        provider.invoke(spec_for(provider))
    assert (raised.value.category, raised.value.remote_outcome) == ("timeout_or_disconnect", "unknown")
    assert len(transport.requests) == 1
