"""Phase 7G: the one-tool write server, end to end through the SDK's in-memory MCP client.

Setup issues grants through the human-authorization application service (ProposalAuthorizationService), as the
review page would. The write server itself only executes them. Every database is ephemeral, test-only fixture
data (tests/review_builders.py), and every clock is injected.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from datetime import timedelta

import anyio
import pytest
from mcp import Client

from baec_app.application.proposal_authorization import (
    EXECUTION_FAILURE_CODES,
    ConfirmationExecutionService,
    ProposalAuthorizationService,
)
from baec_app.data.database import DatabaseVersionError
from baec_app.mcp_write import server as write_server
from baec_app.mcp_write.composition import WriteDatabaseUnavailable, open_write_runtime
from baec_app.mcp_write.server import build_write_server
from tests.application_builders import FixedClock
from tests.persistence_builders import dump
from tests.review_builders import REVIEWER, SUGGESTED, decisions
from tests.test_proposal_authorization import EFFECTS, ISSUED, Authorized

ERROR_PREFIX = "Error executing tool confirm_baec: "
GENERIC_ERROR = "Error executing tool confirm_baec"

INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"grant_id": {"pattern": "^grant_[0-9a-f]{64}$", "title": "Grant Id", "type": "string"}},
    "required": ["grant_id"],
    "title": "ConfirmBaecArguments",
}
OUTPUT_SCHEMA = {
    "additionalProperties": False,
    "description": "Confirmation metadata only: no interaction text, evidence, review content, or authorization "
                   "internals.",
    "properties": {
        "status": {"const": "confirmed", "title": "Status", "type": "string"},
        "baec_id": {"title": "Baec Id", "type": "string"},
        "proposal_id": {"title": "Proposal Id", "type": "string"},
        "review_revision_id": {"title": "Review Revision Id", "type": "string"},
        "grant_id": {"title": "Grant Id", "type": "string"},
    },
    "required": ["status", "baec_id", "proposal_id", "review_revision_id", "grant_id"],
    "title": "ConfirmationView",
    "type": "object",
}
DESCRIPTION = (
    "Execute one previously issued human authorization grant for BAEC confirmation. The grant must already exist "
    "and be valid: issued by a human through the review surface, unexpired, unused, and not superseded. This tool "
    "cannot issue, extend, or alter a grant and supplies no BAEC content; it records the confirmed BAEC the human "
    "authorized, or refuses with the executor's code. It changes no account state."
)


@pytest.fixture
def world(tmp_path):
    w = Authorized(str(tmp_path / "write.sqlite3"))
    w.path = str(tmp_path / "write.sqlite3")
    yield w
    w.connection.close()


@contextlib.asynccontextmanager
async def write_client(path, clock):
    """Open the write runtime in the event-loop thread and connect an in-process client."""
    with open_write_runtime(path, clock=clock) as runtime:
        async with Client(runtime.server) as client:
            yield client


def call(world, *argument_sets):
    """Call confirm_baec once per argument set, in order, over one MCP session."""

    async def main():
        async with write_client(world.path, world.clock) as client:
            return [await client.call_tool("confirm_baec", arguments) for arguments in argument_sets]

    return anyio.run(main)


def refusal(result):
    assert result.is_error and result.structured_content is None and len(result.content) == 1
    text = result.content[0].text
    assert text.startswith(ERROR_PREFIX)
    body = json.loads(text[len(ERROR_PREFIX):])
    assert set(body) == {"status", "code", "grant_id"} and body["status"] == "refused"
    assert body["code"] in EXECUTION_FAILURE_CODES
    return body


def schema_rejection(result):
    """An argument refused by the input schema, before the executor runs."""
    assert result.is_error and result.structured_content is None
    text = result.content[0].text
    assert re.match(re.escape(ERROR_PREFIX) + r"\d+ validation errors? for ConfirmBaecArguments\n", text)
    assert '"status"' not in text  # not an executor refusal, and certainly not a confirmation
    return text


def issued(world):
    """A grant explicitly issued through the human-authorization service, one minute before the call."""
    grant = world.grant()
    world.clock.advance(minutes=1)
    return grant.grant_id


# --- the exact surface --------------------------------------------------------------------------------------------


def test_exactly_one_tool_and_no_resources_templates_or_prompts_are_listed(world):
    async def main():
        async with write_client(world.path, world.clock) as client:
            return (client.server_info.name, (await client.list_tools()).tools,
                    (await client.list_resources()).resources,
                    (await client.list_resource_templates()).resource_templates,
                    (await client.list_prompts()).prompts)

    name, tools, resources, templates, prompts = anyio.run(main)
    assert name == "baec-trigger-intelligence-confirmation"
    assert [t.name for t in tools] == ["confirm_baec"]
    assert resources == [] and templates == [] and prompts == []


def test_the_advertised_input_and_output_schemas_are_pinned(world):
    async def main():
        async with write_client(world.path, world.clock) as client:
            return (await client.list_tools()).tools[0]

    tool = anyio.run(main)
    assert tool.input_schema == INPUT_SCHEMA  # exactly grant_id: str, and additionalProperties false
    assert tool.output_schema == OUTPUT_SCHEMA


def test_the_annotations_are_pinned_and_the_tool_is_not_marked_read_only(world):
    async def main():
        async with write_client(world.path, world.clock) as client:
            return (await client.list_tools()).tools[0]

    annotations = anyio.run(main).annotations.model_dump(by_alias=True)
    assert annotations == {"title": None, "readOnlyHint": False, "destructiveHint": False, "idempotentHint": None,
                           "openWorldHint": False}


def test_the_description_is_narrow_and_makes_no_buyer_claim(world):
    async def main():
        async with write_client(world.path, world.clock) as client:
            return (await client.list_tools()).tools[0].description

    description = anyio.run(main)
    assert description == DESCRIPTION
    lowered = description.lower()
    for claim in ("opportunity", "purchase", "intent", "buyer ready", "buyer-ready", "predict", "decides",
                  "authorizes itself", "buyer is"):
        assert claim not in lowered, claim


# --- success, end to end ------------------------------------------------------------------------------------------


def test_a_valid_grant_confirms_exactly_one_baec_through_mcp(world):
    grant_id = issued(world)
    grant = world.grants.get_grant(grant_id)
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": grant_id})
    assert not result.is_error
    assert result.structured_content == {
        "status": "confirmed", "baec_id": grant.baec_id, "proposal_id": world.pid,
        "review_revision_id": grant.review_revision_id, "grant_id": grant_id}
    assert world.effects() == {t: 1 for t in EFFECTS}  # one BAEC, one HumanAuthorization, one link, one consumption
    after = dump(world.connection)
    assert after["accounts"] == before["accounts"]  # account state unchanged
    for table in ("account_state_transitions", "dormancy_judgments", "evaluation_evidence",
                  "non_evaluation_evidence"):
        assert world.count(table) == 0, table
    assert world.connection.execute(
        "SELECT normalized_text, normalized_generated_at, normalized_model, classification FROM baec_records"
    ).fetchone() == (None, None, None, "CONFIRMED_BAEC")
    assert world.connection.execute(
        "SELECT authorized_by, authorized_at, action, subject_id, target_state FROM human_authorizations"
    ).fetchone() == (REVIEWER, "2026-05-01T12:05:00.000000+00:00", "CONFIRM_BAEC", grant.baec_id, None)
    assert world.grants.lifecycle(grant_id).consumed_baec_id == grant.baec_id
    assert world.count("human_authorization_grants") == 1  # the server issued nothing


def test_the_success_result_carries_metadata_only(world):
    grant_id = issued(world)
    (result,) = call(world, {"grant_id": grant_id})
    wire = result.model_dump_json(by_alias=True)
    interaction = world.connection.execute("SELECT text FROM interactions").fetchone()[0]
    for content in (SUGGESTED, interaction, REVIEWER, "authorization_id", "authorized_by", "digest", "opportunity"):
        assert content not in wire, content


# --- refusals -----------------------------------------------------------------------------------------------------


def test_replaying_a_consumed_grant_through_mcp_is_refused_and_writes_nothing(world):
    grant_id = issued(world)
    first, = call(world, {"grant_id": grant_id})
    assert not first.is_error
    before = dump(world.connection)
    second, third = call(world, {"grant_id": grant_id}, {"grant_id": grant_id})
    for result in (second, third):
        assert refusal(result) == {"status": "refused", "code": "grant_already_consumed", "grant_id": grant_id}
    assert dump(world.connection) == before and world.effects() == {t: 1 for t in EFFECTS}


@pytest.mark.parametrize("minutes", [15, 16, 60 * 24])
def test_an_expired_grant_is_refused_through_mcp_with_no_write_and_no_reauthorization(world, minutes):
    grant_id = world.grant().grant_id
    world.clock.advance(minutes=minutes)  # now >= expires_at
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": grant_id})
    assert refusal(result) == {"status": "refused", "code": "grant_expired", "grant_id": grant_id}
    assert dump(world.connection) == before  # zero authoritative writes, and no new grant
    assert world.grants.lifecycle(grant_id).consumed_baec_id is None


def test_a_grant_just_before_expiry_still_confirms(world):
    grant_id = world.grant().grant_id
    world.clock.advance(minutes=15, microseconds=-1)
    (result,) = call(world, {"grant_id": grant_id})
    assert not result.is_error and result.structured_content["status"] == "confirmed"


def test_a_superseded_unexpired_grant_is_refused_through_mcp(world):
    old = world.grant().grant_id
    world.service.accept_review(world.pid, decisions(final_normalized_condition="More than 10% at renewal."),
                                actor_label=REVIEWER)  # revision 2
    world.clock.advance(minutes=1)
    assert world.clock.now() < world.grants.get_grant(old).expires_at  # chronologically unexpired
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": old})
    assert refusal(result) == {"status": "refused", "code": "grant_superseded", "grant_id": old}
    assert dump(world.connection) == before and world.effects() == {t: 0 for t in EFFECTS}


def test_a_nonexistent_grant_is_refused(world):
    missing = "grant_" + "f" * 64
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": missing})
    assert refusal(result) == {"status": "refused", "code": "grant_not_found", "grant_id": missing}
    assert dump(world.connection) == before


@pytest.mark.parametrize("value", ["", "grant_", "grant_" + "0" * 63, "grant_" + "0" * 65, "grant_" + "A" * 64,
                                   "GRANT_" + "0" * 64, " grant_" + "0" * 64, "x" * 10_000, 7, None, ["grant_"],
                                   {"grant_id": "grant_" + "0" * 64}],
                         ids=["empty", "prefix", "short", "long", "uppercase-hex", "uppercase-prefix", "padded",
                              "huge", "int", "null", "list", "object"])
def test_a_malformed_grant_id_is_rejected_by_the_input_schema_without_echo(world, value):
    world.grant()
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": value})
    text = schema_rejection(result)
    assert "grant_id" in text and "xxxxxxxx" not in text and "0" * 63 not in text and "A" * 64 not in text
    assert dump(world.connection) == before


def test_a_missing_grant_id_is_rejected(world):
    before = dump(world.connection)
    (result,) = call(world, {})
    schema_rejection(result)
    assert dump(world.connection) == before


AUTHORITATIVE_FIELDS = {
    "actor": "attacker", "authorized_by": "attacker", "account_id": "FIXTURE-ACC-2", "proposal_id": "aiprop_x",
    "review_revision_id": "aireview_x", "baec_id": "BAEC-attacker", "evidence": ["forged"],
    "buyer_exact_statement": "forged statement", "provenance": "BUYER_FACT", "criteria": {"all": "MET"},
    "stringency": "more than 0%", "normalization": {"condition": "forged"},
    "reviewed_content": {"buyer_exact_statement": "forged"}, "action": "CHANGE_ACCOUNT_STATE",
    "target_state": "ACTIVE_OPPORTUNITY", "expiry": "2099-01-01T00:00:00+00:00",
    "expires_at": "2099-01-01T00:00:00+00:00", "digest": "0" * 64,
}


@pytest.mark.parametrize("field", sorted(AUTHORITATIVE_FIELDS))
def test_caller_supplied_authority_is_rejected_at_the_schema_not_ignored(world, field):
    grant_id = issued(world)
    before = dump(world.connection)
    (refused,) = call(world, {"grant_id": grant_id, field: AUTHORITATIVE_FIELDS[field]})
    text = schema_rejection(refused)
    assert field in text and "Extra inputs are not permitted" in text
    assert "attacker" not in text and "forged" not in text  # the refused value is not echoed
    assert dump(world.connection) == before  # rejected, not executed with the extra field dropped
    assert world.grants.lifecycle(grant_id).consumed_baec_id is None
    (clean,) = call(world, {"grant_id": grant_id})  # the grant is untouched: the human's authorization still works
    assert clean.structured_content["status"] == "confirmed"
    assert world.connection.execute("SELECT authorized_by FROM human_authorizations").fetchone() == (REVIEWER,)


def test_many_extra_fields_together_are_rejected(world):
    grant_id = issued(world)
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": grant_id, **AUTHORITATIVE_FIELDS})
    schema_rejection(result)
    assert dump(world.connection) == before


# --- unexpected failures ------------------------------------------------------------------------------------------


def test_an_unexpected_failure_is_generic_never_confirmed_and_not_retried(world, monkeypatch):
    grant_id = issued(world)
    calls = []

    def broken(self, grant_id):
        calls.append(grant_id)
        raise RuntimeError("SELECT * FROM baec_records WHERE path = '/private/secret.sqlite3'")

    monkeypatch.setattr(ConfirmationExecutionService, "execute", broken)
    before = dump(world.connection)
    (result,) = call(world, {"grant_id": grant_id})
    assert result.is_error and result.structured_content is None
    assert [c.text for c in result.content] == [GENERIC_ERROR]  # no SQL, path, repr, or refusal code
    assert calls == [grant_id]  # executed once, never retried
    assert dump(world.connection) == before


def test_a_locked_database_is_an_unexpected_failure_with_no_retry_and_no_write(world, monkeypatch):
    """SQLite busy/locked is not translated into a business refusal, and the adapter does not retry it."""
    grant_id = issued(world)
    calls = []
    original = ConfirmationExecutionService.execute

    def counted(self, grant_id):
        calls.append(grant_id)
        return original(self, grant_id)

    monkeypatch.setattr(ConfirmationExecutionService, "execute", counted)
    holder = sqlite3.connect(world.path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # another writer holds the database

    async def main():
        no_wait = sqlite3.connect(world.path, isolation_level=None, timeout=0)
        no_wait.execute("PRAGMA foreign_keys = ON")
        try:
            async with Client(build_write_server(ConfirmationExecutionService(no_wait, clock=world.clock))) as client:
                return await client.call_tool("confirm_baec", {"grant_id": grant_id})
        finally:
            no_wait.close()

    try:
        result = anyio.run(main)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert result.is_error and [c.text for c in result.content] == [GENERIC_ERROR]
    assert calls == [grant_id]
    assert world.effects() == {t: 0 for t in EFFECTS} and world.grants.lifecycle(grant_id).consumed_baec_id is None
    (again,) = call(world, {"grant_id": grant_id})  # the caller may invoke again; the locked semantics decide
    assert again.structured_content["status"] == "confirmed"


# --- concurrency ----------------------------------------------------------------------------------------------------


def test_two_concurrent_calls_on_separate_servers_confirm_exactly_once(world):
    grant_id = issued(world)
    barrier = threading.Barrier(2)
    results = []

    def one_server():
        clock = FixedClock(world.clock.now())

        async def main():
            async with write_client(world.path, clock) as client:
                barrier.wait()
                return await client.call_tool("confirm_baec", {"grant_id": grant_id})

        results.append(anyio.run(main))

    threads = [threading.Thread(target=one_server) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(results) == 2
    confirmed = [r for r in results if not r.is_error]
    refused = [refusal(r) for r in results if r.is_error]
    assert len(confirmed) == 1 and refused == [{"status": "refused", "code": "grant_already_consumed",
                                                 "grant_id": grant_id}]
    assert world.effects() == {t: 1 for t in EFFECTS}


# --- construction ---------------------------------------------------------------------------------------------------


class _ExecutorSubclass(ConfirmationExecutionService):
    pass


def test_the_server_accepts_only_an_exact_execution_service(world):
    clock = FixedClock(ISSUED)
    issuance = ProposalAuthorizationService(world.connection, clock=clock, new_grant_id=lambda: "grant_" + "0" * 64)
    for wrong in (None, world.connection, world.path, issuance, _ExecutorSubclass(world.connection, clock=clock),
                  world.grants):
        with pytest.raises(TypeError):
            build_write_server(wrong)


def test_the_write_runtime_opens_a_writable_foreign_key_enforcing_connection_and_closes_it(world):
    runtime = open_write_runtime(world.path, clock=world.clock)
    connection = runtime._connection
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 0
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    runtime.close()
    assert runtime.closed
    with pytest.raises(RuntimeError):
        runtime.server
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


@pytest.mark.parametrize("path", [":memory:", "", "   ", "file:x.sqlite3?mode=rwc", "FILE:/tmp/x.sqlite3", b"x", 3],
                         ids=["memory", "empty", "blank", "file-uri", "file-uri-uppercase", "bytes", "int"])
def test_the_write_runtime_refuses_anything_but_a_database_path(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WriteDatabaseUnavailable):
        open_write_runtime(path, clock=FixedClock(ISSUED))
    assert list(tmp_path.iterdir()) == []


def test_the_write_runtime_never_creates_seeds_or_migrates(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(WriteDatabaseUnavailable):
        open_write_runtime(missing, clock=FixedClock(ISSUED))
    assert not missing.exists()
    with pytest.raises(WriteDatabaseUnavailable):
        open_write_runtime(tmp_path, clock=FixedClock(ISSUED))  # a directory
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"not a database" * 100)
    old = tmp_path / "v6.sqlite3"
    Authorized(str(old)).connection.close()
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 6")
    raw.close()
    snapshot = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(DatabaseVersionError):
        open_write_runtime(empty, clock=FixedClock(ISSUED))  # an empty file is not initialized
    with pytest.raises(WriteDatabaseUnavailable):
        open_write_runtime(garbage, clock=FixedClock(ISSUED))
    with pytest.raises(DatabaseVersionError):
        open_write_runtime(old, clock=FixedClock(ISSUED))  # an older version is not migrated
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == snapshot


def test_the_refusal_text_is_canonical_json():
    assert write_server._refusal_text("grant_expired", "grant_" + "a" * 64) == (
        '{"code":"grant_expired","grant_id":"grant_' + "a" * 64 + '","status":"refused"}')


def test_every_refusal_code_passes_through_unchanged(world, monkeypatch):
    """The adapter uses the executor's closed vocabulary exactly; it has no codes of its own."""
    from baec_app.application.proposal_authorization import ExecutionRefused
    grant_id = issued(world)
    for code in EXECUTION_FAILURE_CODES:
        def refuse(self, grant_id, code=code):
            raise ExecutionRefused(code)

        monkeypatch.setattr(ConfirmationExecutionService, "execute", refuse)
        (result,) = call(world, {"grant_id": grant_id})
        assert refusal(result) == {"status": "refused", "code": code, "grant_id": grant_id}
    assert world.effects() == {t: 0 for t in EFFECTS}


def test_a_new_revision_after_expiry_still_needs_a_new_human_authorization(world):
    """The server never re-issues: after an expired grant, only the human surface can authorize again."""
    grant_id = world.grant().grant_id
    world.clock.advance(minutes=20)
    (result,) = call(world, {"grant_id": grant_id})
    assert refusal(result)["code"] == "grant_expired"
    assert world.count("human_authorization_grants") == 1
    replacement = world.grant().grant_id  # a second explicit human action
    world.clock.advance(seconds=30)
    (result,) = call(world, {"grant_id": replacement})
    assert result.structured_content["status"] == "confirmed" and result.structured_content["grant_id"] == replacement
    assert world.clock.now() - world.grants.get_grant(replacement).issued_at == timedelta(seconds=30)
