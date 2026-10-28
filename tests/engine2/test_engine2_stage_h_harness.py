"""Stage H-A: offline proof of the Stage H live harness (tests/live/engine2_stage_h.py).

Deterministic Anthropic-shaped responses go through the real anthropic SDK via httpx2.MockTransport, so the
real client, request serialization, and response parsing are exercised. Real sockets are blocked and
credentials removed. Nothing here contacts Anthropic. These are software tests, not model evaluation.
"""

import ast
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import httpx2
import pytest

from baec_app.engine2 import ai_persistence as store
from baec_app.engine2.ai import OUTPUT_SCHEMA, SYSTEM_PROMPT
from tests.engine2.ai_replay import ai_case, faithful_output
from tests.engine2.corpus_adapter import load_corpus, run_case
from tests.engine2.test_engine2_ai_evaluation import CATEGORIES, LEAK_TOKENS
from tests.live import engine2_stage_h as h

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "tests" / "live" / "engine2_stage_h.py"
COMMIT = "b3a9bd937d647cc23a54747c45e47c7cc5ae71b8"
FAKE_KEY = "test-only-not-a-real-key"
T0 = datetime.fromisoformat("2027-06-02T00:00:00+00:00")
CORPUS = load_corpus()
CASE = next(c for c in CORPUS["cases"] if c["case_id"] == h.CASE_ID)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_LOG", h.LIVE_FLAG,
                 h.RESEARCH_DIR_VARIABLE):
        monkeypatch.delenv(name, raising=False)  # the fake key below is always passed explicitly, never via the env

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class Recorder:
    """Scripted HTTP responses; records every HTTP attempt. `during` runs inside the provider call."""

    def __init__(self, respond, during=None):
        self.requests, self._respond, self._during = [], respond, during

    def __call__(self, request):
        self.requests.append(request)
        if self._during:
            self._during()
        return self._respond(request)


def faithful_text():
    ai_input, ids = ai_case(CORPUS, run_case(CORPUS, CASE))
    return faithful_output(ai_input, CASE, ids)


def text_block(text):
    return {"type": "text", "text": text}


def message_json(content, *, stop_reason="end_turn", model=h.MODEL):
    return {"id": "msg_synthetic_01", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1234, "output_tokens": 321, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0}}


def replying(content, during=None, **options):
    return Recorder(lambda request: httpx2.Response(200, json=message_json(content, **options),
                                                    headers={"request-id": "req_synthetic_01"}), during)


def failing_with(status, error_type, headers=None):
    return Recorder(lambda request: httpx2.Response(
        status, json={"type": "error", "error": {"type": error_type, "message": "synthetic provider error text"}},
        headers={"request-id": "req_synthetic_err", **(headers or {})}))


def raising(exception):
    def respond(request):
        raise exception
    return Recorder(respond)


def client_for(recorder):
    return h.make_client(FAKE_KEY, httpx2.Client(transport=httpx2.MockTransport(recorder)))


def run(tmp_path, recorder, name="research"):
    research = tmp_path / name
    research.mkdir()
    return h.run_stage_h(client=client_for(recorder), research_dir=research, producer_commit=COMMIT, clock=Clock())


def counts(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        result = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in store.TABLES}
    conn.close()
    return result


def reopen(path):
    return store.open_ai_audit_database(str(path))


def assert_attempt_only(result, recorder):
    assert len(recorder.requests) == 1
    c = counts(result.database_path)
    assert (c["ai_inputs"], c["ai_attempts"], c["ai_responses"], c["ai_validations"], c["ai_proposals"]) == (1, 1, 0, 0, 0)
    assert result.record["ai_database_verification"] == "VERIFIED"
    assert result.record["validation_status"] is None and result.record["proposal_id"] is None


# --- 1. client configuration --------------------------------------------------------------------------------


def test_the_live_client_is_built_with_no_retries_and_the_locked_timeout():
    client = h.make_client(FAKE_KEY)
    assert type(client) is anthropic.Anthropic and client.max_retries == 0
    assert type(client.timeout) is float and client.timeout == h.TIMEOUT_SECONDS == 180.0
    assert client.api_key == FAKE_KEY and client.auth_token is None and str(client.base_url) == "https://api.anthropic.com"
    client.close()


def test_the_api_key_is_passed_explicitly_and_is_the_only_credential_sent(tmp_path):
    assert "ANTHROPIC_API_KEY" not in os.environ  # so the SDK cannot have discovered it implicitly
    recorder = replying([text_block(faithful_text())])
    run(tmp_path, recorder)
    (request,) = recorder.requests
    assert request.headers["x-api-key"] == FAKE_KEY and "authorization" not in request.headers
    assert str(request.url) == "https://api.anthropic.com/v1/messages"


@pytest.mark.parametrize("variable, value", [("ANTHROPIC_BASE_URL", "https://proxy.invalid"),
                                             ("ANTHROPIC_AUTH_TOKEN", "test-only-token"), ("ANTHROPIC_AUTH_TOKEN", "")])
def test_the_explicit_client_ignores_environment_overrides_behind_the_gate(tmp_path, monkeypatch, variable, value):
    # Defense in depth only: the live gate already refuses these variables when nonblank.
    monkeypatch.setenv(variable, value)
    recorder = replying([text_block(faithful_text())])
    client = client_for(recorder)
    assert str(client.base_url) == "https://api.anthropic.com" and client.auth_token is None
    research = tmp_path / "research"
    research.mkdir()
    h.run_stage_h(client=client, research_dir=research, producer_commit=COMMIT, clock=Clock())
    (request,) = recorder.requests
    assert str(request.url) == "https://api.anthropic.com/v1/messages" and "authorization" not in request.headers


@pytest.mark.parametrize("options", [
    {"max_retries": 2, "timeout": 180.0}, {"max_retries": 0, "timeout": 600.0}, {},
    {"max_retries": 0, "timeout": 180.0, "base_url": "https://proxy.invalid"},
    {"max_retries": 0, "timeout": 180.0, "base_url": "https://api.anthropic.com", "auth_token": "test-only-token"}])
def test_the_adapter_refuses_any_other_client(options):
    client = anthropic.Anthropic(api_key=FAKE_KEY, **options)
    with pytest.raises(ValueError, match="max_retries=0|explicit API key"):
        h.StageHAnthropicModel(client)
    client.close()
    with pytest.raises(ValueError):
        h.StageHAnthropicModel(object())


# --- 2. the exact request -------------------------------------------------------------------------------------


def test_the_request_is_exactly_the_locked_prompt_only_json_request(tmp_path):
    recorder = replying([text_block(faithful_text())])
    run(tmp_path, recorder)
    (request,) = recorder.requests
    assert request.method == "POST" and request.url.host == "api.anthropic.com" and request.url.path == "/v1/messages"
    body = json.loads(request.content)
    ai_input = h.experiment_input()
    assert body == {
        "model": "claude-sonnet-5-5", "max_tokens": h.MAX_TOKENS, "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": h.user_text(ai_input, OUTPUT_SCHEMA)}],
        "thinking": {"type": "between_tools"}, "output_config": {"effort": "high"},
    }
    content = body["messages"][0]["content"]
    assert h.canonical(OUTPUT_SCHEMA) in content and ai_input.canonical_json in content
    assert "tools" not in body and "tool_choice" not in body and "format" not in body["output_config"]
    assert "anthropic-beta" not in request.headers


def test_the_request_carries_no_case_identity_or_oracle(tmp_path):
    recorder = replying([text_block(faithful_text())])
    run(tmp_path, recorder)
    body = json.loads(recorder.requests[0].content)
    assert body["system"] == SYSTEM_PROMPT  # locked Stage F material, checked exactly elsewhere
    schema = h.canonical(OUTPUT_SCHEMA)
    content = body["messages"][0]["content"]
    assert content.count(schema) == 1
    case_material = content.replace(schema, "")  # the evidence snapshot and its framing
    for token in LEAK_TOKENS + CATEGORIES + (h.CASE_ID, "expected_", "case_id"):
        assert token not in case_material, token
    assert "HBR-CORR" not in recorder.requests[0].content.decode("utf-8")


def test_the_fixed_input_is_case_011_with_the_locked_digest():
    assert h.CASE_ID == "HBR-CORR-011" and h.experiment_input().input_digest == h.INPUT_DIGEST


# --- 3, 4, 5. one invocation, persisted first, exact attempt -------------------------------------------------


def test_the_input_and_attempt_are_committed_before_the_provider_call(tmp_path):
    seen = []
    research = tmp_path / "research"
    recorder = replying([text_block(faithful_text())], during=lambda: seen.append(counts(research / h.DATABASE_NAME)))
    run(tmp_path, recorder)
    assert len(seen) == 1
    assert (seen[0]["ai_inputs"], seen[0]["ai_attempts"], seen[0]["ai_responses"]) == (1, 1, 0)


def test_the_attempt_records_exactly_the_requested_identity(tmp_path):
    result = run(tmp_path, replying([text_block(faithful_text())], model="claude-opus-5-5"))
    conn = reopen(result.database_path)
    attempt = store.load_ai_attempt(conn, h.ATTEMPT_ID)
    conn.close()
    assert (attempt.provider, attempt.model, attempt.producer_commit, attempt.input_digest) == \
        ("anthropic", "claude-sonnet-5-5", COMMIT, h.INPUT_DIGEST)


def test_the_adapter_allows_one_invocation_and_only_the_locked_prompt_and_schema():
    recorder = replying([text_block(faithful_text())])
    model = h.StageHAnthropicModel(client_for(recorder))
    ai_input = h.experiment_input()
    with pytest.raises(ValueError):
        model.propose(ai_input, system_prompt=SYSTEM_PROMPT + " ", output_schema=OUTPUT_SCHEMA)
    with pytest.raises(ValueError):
        model.propose(ai_input, system_prompt=SYSTEM_PROMPT, output_schema=dict(OUTPUT_SCHEMA))
    assert recorder.requests == []
    model.propose(ai_input, system_prompt=SYSTEM_PROMPT, output_schema=OUTPUT_SCHEMA)
    with pytest.raises(RuntimeError, match="one provider invocation"):
        model.propose(ai_input, system_prompt=SYSTEM_PROMPT, output_schema=OUTPUT_SCHEMA)
    assert len(recorder.requests) == 1


# --- 6, 14. matching response: accepted, exact identity, verified --------------------------------------------


def test_a_matching_response_maps_message_model_and_is_accepted(tmp_path):
    raw = faithful_text()
    recorder = replying([text_block(raw)])
    result = run(tmp_path, recorder)
    assert len(recorder.requests) == 1
    record = result.record
    assert (record["outcome"], record["validation_status"], record["proposal_id"]) == ("VALIDATED", "ACCEPTED", h.ARTIFACT_ID)
    assert record["requested_model"] == record["returned_model"] == "claude-sonnet-5-5"
    assert record["provider_request_id"] == "req_synthetic_01" and record["provider_message_id"] == "msg_synthetic_01"
    assert record["stop_reason"] == "end_turn" and record["content_block_types"] == ["text"]
    assert record["usage"]["input_tokens"] == 1234 and record["usage"]["output_tokens"] == 321
    conn = reopen(result.database_path)
    response = store.load_ai_response(conn, h.ATTEMPT_ID)
    proposal = store.load_ai_proposal(conn, h.ARTIFACT_ID)
    verified = store.verify_ai_database(conn)
    conn.close()
    assert response.raw_text == raw and (response.provider, response.model) == ("anthropic", "claude-sonnet-5-5")
    assert (proposal.provider, proposal.model, proposal.input_digest) == ("anthropic", "claude-sonnet-5-5", h.INPUT_DIGEST)
    assert proposal.origin.value == "AI_INFERENCE"
    assert verified == record["ai_database_counts"] and verified["ai_proposals"] == 1


def test_the_raw_text_is_stored_exactly_without_trimming(tmp_path):
    raw = "\n  " + faithful_text() + "\n\n"
    result = run(tmp_path, replying([text_block(raw)]))
    conn = reopen(result.database_path)
    assert store.load_ai_response(conn, h.ATTEMPT_ID).raw_text == raw
    conn.close()


# --- 7, 8. returned-model mismatch is never rewritten -------------------------------------------------------


@pytest.mark.parametrize("returned", ["claude-opus-5-5", "Claude-Sonnet-5-5", "CLAUDE-SONNET-5-5", "claude-sonnet-5-5 ",
                                      " claude-sonnet-5-5", "claude-sonnet-5-5\n", "claude-sonnet-5-5-20260101",
                                      "claude-sonnet-5", "claude_sonnet_5_5", "claude-sonnet-5.5"])
def test_a_returned_model_mismatch_is_refused_by_stage_g_and_recorded(tmp_path, returned):
    recorder = replying([text_block(faithful_text())], model=returned)
    result = run(tmp_path, recorder)
    assert_attempt_only(result, recorder)
    record = result.record
    assert (record["outcome"], record["failure_category"]) == ("STAGE_G_IDENTITY_REFUSED", "provider_model_mismatch")
    assert record["requested_model"] == "claude-sonnet-5-5" and record["returned_model"] == returned


# --- 9, 10, 11. response shape kills ---------------------------------------------------------------------------


THINKING = {"type": "thinking", "thinking": "SYNTHETIC-THINKING-TEXT", "signature": "sig"}
SHAPES = {
    "zero_content_blocks": ([], {}),
    "multiple_content_blocks": ([text_block(faithful_text()), text_block("{}")], {}),
    "multiple_with_thinking": ([THINKING, text_block(faithful_text())], {}),
    "thinking_only": ([THINKING], {}),
    "redacted_thinking_only": ([{"type": "redacted_thinking", "data": "OPAQUE-REDACTED-DATA"}], {}),
    "tool_use_only": ([{"type": "tool_use", "id": "toolu_1", "name": "x", "input": {}}], {"stop_reason": "end_turn"}),
    "text_with_citations": ([{**text_block(faithful_text()), "citations": [
        {"type": "char_location", "cited_text": "abc", "document_index": 0, "document_title": None,
         "start_char_index": 0, "end_char_index": 3}]}], {}),
    "max_tokens": ([text_block(faithful_text())], {"stop_reason": "max_tokens"}),
    "refusal": ([text_block(faithful_text())], {"stop_reason": "refusal"}),
    "tool_use_stop": ([text_block(faithful_text())], {"stop_reason": "tool_use"}),
    "pause_turn": ([text_block(faithful_text())], {"stop_reason": "pause_turn"}),
}
EXPECTED_KILL = {"zero_content_blocks": "zero_content_blocks", "multiple_content_blocks": "multiple_content_blocks",
                 "multiple_with_thinking": "multiple_content_blocks", "thinking_only": "non_text_content_block",
                 "redacted_thinking_only": "non_text_content_block", "tool_use_only": "non_text_content_block",
                 "text_with_citations": "non_text_content_block", "max_tokens": "unexpected_stop_reason",
                 "refusal": "unexpected_stop_reason", "tool_use_stop": "unexpected_stop_reason",
                 "pause_turn": "unexpected_stop_reason"}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_an_unexpected_response_shape_kills_the_run_without_retry_or_repair(tmp_path, shape):
    content, options = SHAPES[shape]
    recorder = replying(content, **options)
    result = run(tmp_path, recorder)
    assert_attempt_only(result, recorder)
    assert (result.record["outcome"], result.record["failure_category"]) == ("RESPONSE_SHAPE_KILLED", EXPECTED_KILL[shape])
    assert result.record["content_block_types"] == [b["type"] for b in content]
    assert "SYNTHETIC-THINKING-TEXT" not in result.record_text and "OPAQUE-REDACTED-DATA" not in result.record_text


# --- 12. provider failures -------------------------------------------------------------------------------------


FAILURES = {
    "server_error": (lambda: failing_with(500, "api_error"), "api_status_error", 500),
    "overloaded": (lambda: failing_with(529, "overloaded_error"), "api_status_error", 529),
    "rate_limited": (lambda: failing_with(429, "rate_limit_error", {"retry-after": "0"}), "api_status_error", 429),
    "should_retry": (lambda: failing_with(500, "api_error", {"x-should-retry": "true"}), "api_status_error", 500),
    "authentication": (lambda: failing_with(401, "authentication_error"), "api_status_error", 401),
    "invalid_request": (lambda: failing_with(400, "invalid_request_error"), "api_status_error", 400),
    "timeout": (lambda: raising(httpx2.ReadTimeout("synthetic")), "timeout", None),
    "connect": (lambda: raising(httpx2.ConnectError("synthetic")), "connection_error", None),
}


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_a_provider_failure_leaves_only_the_attempt_and_is_recorded_sanitized(tmp_path, failure):
    make, category, status = FAILURES[failure]
    recorder = make()
    result = run(tmp_path, recorder)
    assert_attempt_only(result, recorder)
    record = result.record
    assert (record["outcome"], record["failure_category"], record.get("http_status")) == ("PROVIDER_FAILURE", category, status)
    assert record["returned_model"] is None and "synthetic provider error text" not in result.record_text
    if status is not None:
        assert record["provider_request_id"] == "req_synthetic_err"


# --- 13. Stage F rejection is a valid result ----------------------------------------------------------------


@pytest.mark.parametrize("raw", ["{}", "not json", '{"schema_version": "baec-e2-correspondence-proposal/v1"}',
                                 "```json\n{}\n```"])
def test_a_stage_f_rejection_is_recorded_as_rejected_with_the_response_kept(tmp_path, raw):
    recorder = replying([text_block(raw)])
    result = run(tmp_path, recorder)
    assert len(recorder.requests) == 1
    record = result.record
    assert (record["outcome"], record["validation_status"], record["validation_error_category"], record["proposal_id"]) \
        == ("VALIDATED", "REJECTED", "ProposalRejected", None)
    c = counts(result.database_path)
    assert (c["ai_responses"], c["ai_validations"], c["ai_proposals"]) == (1, 1, 0)
    conn = reopen(result.database_path)
    assert store.load_ai_response(conn, h.ATTEMPT_ID).raw_text == raw
    conn.close()
    assert record["ai_database_verification"] == "VERIFIED"


# --- run record ---------------------------------------------------------------------------------------------


def test_the_run_record_is_canonical_sanitized_and_complete(tmp_path):
    raw = faithful_text()
    result = run(tmp_path, replying([text_block(raw)]))
    on_disk = result.record_path.read_text(encoding="utf-8")
    assert on_disk == result.record_text == h.canonical(json.loads(on_disk))
    assert result.record_sha256 == h.sha256(on_disk)
    assert FAKE_KEY not in on_disk and FAKE_KEY.encode() not in result.database_path.read_bytes()
    assert raw not in on_disk and "rationale" not in on_disk
    record = result.record
    assert record["credential_mode"] == "api_key" and record["provider_endpoint"] == "https://api.anthropic.com"
    assert not any("key" in k and k != "credential_mode" for k in record)
    for key in ("repository_commit", "python_version", "anthropic_sdk_version", "harness_version", "case_id",
                "input_digest", "attempt_id", "provider", "requested_model", "returned_model", "prompt_version",
                "output_schema_version", "system_prompt_sha256", "output_schema_sha256", "request_sha256",
                "attempt_requested_at", "call_started_at", "call_ended_at", "provider_request_id",
                "provider_message_id", "stop_reason", "usage", "validation_status", "proposal_id",
                "ai_database_verification", "ai_database_sha256"):
        assert record[key] is not None, key
    assert record["repository_commit"] == COMMIT and record["anthropic_sdk_version"] == anthropic.__version__
    assert record["ai_database_sha256"] == h.hashlib.sha256(result.database_path.read_bytes()).hexdigest()
    assert sorted(p.name for p in result.database_path.parent.iterdir()) == sorted([h.DATABASE_NAME, h.RUN_RECORD_NAME])


def test_the_run_record_is_deterministic_for_identical_runs(tmp_path):
    first = run(tmp_path, replying([text_block(faithful_text())]), "one")
    second = run(tmp_path, replying([text_block(faithful_text())]), "two")
    assert first.record_text == second.record_text


# --- isolation ------------------------------------------------------------------------------------------------


def test_the_research_directory_must_be_explicit_empty_and_outside_the_repository(tmp_path):
    recorder = replying([text_block(faithful_text())])
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing.sqlite3").write_bytes(b"")
    for directory in (Path("relative/dir"), tmp_path / "missing", occupied, REPO, REPO / "tests"):
        with pytest.raises(h.LiveGateClosed):
            h.run_stage_h(client=client_for(recorder), research_dir=directory, producer_commit=COMMIT, clock=Clock())
    assert recorder.requests == [] and [p.name for p in occupied.iterdir()] == ["existing.sqlite3"]


@pytest.mark.parametrize("commit", ["", "b3a9bd9", COMMIT.upper(), COMMIT + "0", None])
def test_the_producer_commit_must_be_a_full_commit_id(tmp_path, commit):
    recorder = replying([text_block(faithful_text())])
    research = tmp_path / "research"
    research.mkdir()
    with pytest.raises(h.LiveGateClosed):
        h.run_stage_h(client=client_for(recorder), research_dir=research, producer_commit=commit, clock=Clock())
    assert recorder.requests == [] and list(research.iterdir()) == []


# --- 15, 16. the live gate --------------------------------------------------------------------------------------


class ClientConstructed(Exception):
    pass


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """The live entry with git and client construction replaced by recorders. Nothing can reach the network."""
    constructed = []

    def construct(*args, **kwargs):
        constructed.append((args, kwargs))
        raise ClientConstructed()
    monkeypatch.setattr(h, "make_client", construct)
    monkeypatch.setattr(h, "_clean_head", lambda: COMMIT)
    research = tmp_path / "research"
    research.mkdir()
    approved = {h.LIVE_FLAG: "1", h.RESEARCH_DIR_VARIABLE: str(research), "ANTHROPIC_API_KEY": FAKE_KEY}
    return constructed, research, approved, monkeypatch


def open_with(monkeypatch, environ):
    for name, value in environ.items():
        monkeypatch.setenv(name, value)


def test_the_approved_environment_reaches_client_construction_with_the_explicit_key(gate):
    constructed, research, approved, monkeypatch = gate
    open_with(monkeypatch, approved)
    with pytest.raises(ClientConstructed):
        h.run_live()
    assert constructed == [((FAKE_KEY,), {})]
    assert list(research.iterdir()) == []  # the gate and client come before any database


CLOSED = {
    "missing key": ({}, "ANTHROPIC_API_KEY"),
    "blank key": ({"ANTHROPIC_API_KEY": ""}, None),
    "whitespace key": ({"ANTHROPIC_API_KEY": "  \t"}, None),
    "auth token with key": ({"ANTHROPIC_AUTH_TOKEN": "test-only-token"}, None),
    "base url": ({"ANTHROPIC_BASE_URL": "https://proxy.invalid"}, None),
    "base url same host": ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, None),
    "sdk logging": ({"ANTHROPIC_LOG": "debug"}, None),
    "sdk logging info": ({"ANTHROPIC_LOG": "info"}, None),
    "flag not exactly 1": ({h.LIVE_FLAG: "true"}, None),
    "flag missing": ({}, h.LIVE_FLAG),
    "research dir missing": ({}, h.RESEARCH_DIR_VARIABLE),
    "research dir relative": ({h.RESEARCH_DIR_VARIABLE: "relative"}, None),
    "research dir in repo": ({h.RESEARCH_DIR_VARIABLE: str(REPO)}, None),
}


@pytest.mark.parametrize("case", sorted(CLOSED))
def test_the_live_gate_closes_before_any_client_or_database(gate, case):
    constructed, research, approved, monkeypatch = gate
    changes, removed = CLOSED[case]
    environ = {**approved, **changes}
    environ.pop(removed, None)
    open_with(monkeypatch, environ)
    with pytest.raises(h.LiveGateClosed):
        h.run_live()
    assert constructed == [] and list(research.iterdir()) == []


@pytest.mark.parametrize("variable", ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_LOG"])
def test_blank_refused_variables_do_not_close_the_gate(gate, variable):
    constructed, research, approved, monkeypatch = gate
    open_with(monkeypatch, {**approved, variable: ""})
    with pytest.raises(ClientConstructed):
        h.run_live()
    assert list(research.iterdir()) == []


def test_the_live_gate_reports_no_credential_value(gate):
    constructed, research, approved, monkeypatch = gate
    open_with(monkeypatch, {**approved, "ANTHROPIC_AUTH_TOKEN": "test-only-token"})
    with pytest.raises(h.LiveGateClosed) as closed:
        h.run_live()
    assert FAKE_KEY not in str(closed.value) and "test-only-token" not in str(closed.value)


def test_the_default_suite_never_selects_or_opens_the_live_run():
    assert h.LIVE_FLAG not in os.environ
    config = (REPO / "pytest.ini").read_text(encoding="utf-8")
    assert '-m "not live_claude"' in config
    live = ast.parse((REPO / "tests" / "live_engine2" / "test_live_engine2_stage_h.py").read_text(encoding="utf-8"))
    marks = [n for n in live.body if isinstance(n, ast.Assign) and n.targets[0].id == "pytestmark"]
    assert len(marks) == 1 and ast.unparse(marks[0].value) == "pytest.mark.live_claude"


def test_importing_the_harness_constructs_nothing_and_calls_nothing():
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    allowed = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Expr)
    assert all(isinstance(node, allowed) for node in tree.body)
    module_calls = {ast.unparse(c.func) for node in tree.body if isinstance(node, (ast.Assign, ast.Expr))
                    for c in ast.walk(node) if isinstance(c, ast.Call)}
    assert module_calls == {"Path", "Path(__file__).resolve"}


# --- static review of the harness -------------------------------------------------------------------------


def _code_words():
    """Identifiers, attributes, keywords, and non-docstring string constants of the harness."""
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    words = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            words.append(n.id)
        elif isinstance(n, ast.Attribute):
            words.append(n.attr)
        elif isinstance(n, ast.keyword) and n.arg:
            words.append(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            words.append(n.name)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            words.append(n.value)
    return tree, " ".join(words).lower()


def test_the_harness_has_no_retry_fallback_selection_normalization_repair_or_joining():
    tree, words = _code_words()
    for banned in ("retry", "fallback", "alias", "latest", "newest", "select", "normal", "strip", "lower(", "upper",
                   "casefold", "repair", "join", "stream", "messages.parse", "with_options", "models", "beta", "print",
                   "logging", "sleep", "backoff", "requests", "urllib", "socket", "http.client"):
        assert banned not in words.replace("max_retries", ""), banned
    assert not any(isinstance(n, (ast.For, ast.While, ast.AsyncFor)) for n in ast.walk(tree))
    source = HARNESS.read_text(encoding="utf-8")
    assert source.count("messages.create(") == 1
    retries = [k for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords if k.arg == "max_retries"]
    assert len(retries) == 2 and all(ast.unparse(k.value) == "0" for k in retries)  # the live and injected clients
    models = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "claude-sonnet-5-5"]
    assert len(models) == 1 and "claude-opus" not in source  # one pinned literal: MODEL


def test_the_harness_reads_the_api_key_only_to_pass_it_to_the_client():
    source = HARNESS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    uses = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Subscript) and "API_KEY_VARIABLE" in ast.unparse(n)]
    assert uses == ["os.environ[API_KEY_VARIABLE]"]
    assert "client=make_client(os.environ[API_KEY_VARIABLE])" in source
    assert "_blank(environ.get(API_KEY_VARIABLE))" in source
    assert h.REFUSED_VARIABLES == ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_LOG")
    assert h.ENDPOINT == "https://api.anthropic.com" and h.CREDENTIAL_MODE == "api_key"


def test_the_harness_lives_outside_the_production_package():
    assert not (REPO / "baec_app" / "engine2" / "engine2_stage_h.py").exists()
    for path in (REPO / "baec_app").rglob("*.py"):
        assert "engine2_stage_h" not in path.read_text(encoding="utf-8"), path


def test_an_unexpected_harness_error_is_recorded_then_raised_with_the_attempt_kept(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic harness fault")
    monkeypatch.setattr(store, "record_ai_response", broken)
    recorder = replying([text_block(faithful_text())])
    research = tmp_path / "research"
    research.mkdir()
    with pytest.raises(RuntimeError):
        h.run_stage_h(client=client_for(recorder), research_dir=research, producer_commit=COMMIT, clock=Clock())
    assert len(recorder.requests) == 1
    record = json.loads((research / h.RUN_RECORD_NAME).read_text(encoding="utf-8"))
    assert (record["outcome"], record["failure_category"]) == ("HARNESS_ERROR", "unexpected:RuntimeError")
    assert record["ai_database_verification"] == "VERIFIED" and record["ai_database_counts"]["ai_attempts"] == 1
    assert record["ai_database_counts"]["ai_responses"] == 0


def test_a_default_pytest_run_collects_no_stage_h_live_test_even_with_the_gate_variables_set(tmp_path):
    env = dict(os.environ, **{h.LIVE_FLAG: "1", h.RESEARCH_DIR_VARIABLE: str(tmp_path), "ANTHROPIC_API_KEY": FAKE_KEY})
    completed = subprocess.run([sys.executable, "-m", "pytest", "tests/live_engine2", "--collect-only", "-q",
                                "-p", "no:cacheprovider"], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert "no tests collected (1 deselected)" in completed.stdout
    assert FAKE_KEY not in completed.stdout + completed.stderr and list(tmp_path.iterdir()) == []
