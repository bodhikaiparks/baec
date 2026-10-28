"""Phase 7G: the write server's stdio entry point, run as a real child process.

    python -m baec_app.mcp_write --database PATH

The child uses the real UTC clock (SystemClock). A grant meant to succeed is issued, through the human-authorization
service, at the real current time; a grant meant to be expired is issued at the fixed 2026-05-01 fixture time,
which is long past. Everything is local: no sockets and no external services.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from baec_app.application.proposal_authorization import ProposalAuthorizationService
from tests.application_builders import FixedClock
from tests.mcp_builders import REPO_ROOT, run, stdio_connected
from tests.persistence_builders import dump
from tests.review_builders import REVIEWER
from tests.test_mcp_stdio import RawServer, assert_only_protocol_messages, files_in
from tests.test_proposal_authorization import EFFECTS, Authorized

MODULE = "baec_app.mcp_write"
ERROR_PREFIX = "Error executing tool confirm_baec: "


@pytest.fixture
def world(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    w = Authorized(str(directory / "stdio-write.sqlite3"))
    w.path = str(directory / "stdio-write.sqlite3")
    yield w
    w.connection.close()


@pytest.fixture
def errlog(tmp_path):
    with open(tmp_path / "server-stderr.txt", "w+", encoding="utf-8") as file:
        yield file


def stderr_text(errlog):
    errlog.flush()
    errlog.seek(0)
    return errlog.read()


def issued_now(world):
    """A grant issued by the human-authorization service at the real current time."""
    service = ProposalAuthorizationService(world.connection, clock=FixedClock(datetime.now(timezone.utc)),
                                           new_grant_id=lambda: "grant_" + "e" * 64)
    return service.authorize_confirmation(world.pid, actor_label=REVIEWER).grant_id


def calls(world, errlog, *argument_sets):
    async def main():
        async with stdio_connected(world.path, errlog, module=MODULE) as client:
            listing = ((await client.list_tools()).tools, (await client.list_resources()).resources,
                       (await client.list_resource_templates()).resource_templates,
                       (await client.list_prompts()).prompts, client.server_info.name)
            return listing, [await client.call_tool("confirm_baec", arguments) for arguments in argument_sets]

    return run(main)


def refusal(result):
    assert result.is_error and result.structured_content is None
    return json.loads(result.content[0].text.removeprefix(ERROR_PREFIX))


def start_and_fail(arguments, cwd=REPO_ROOT):
    environment = dict(os.environ, PYTHONPATH=str(REPO_ROOT))  # importable from any working directory
    completed = subprocess.run([sys.executable, "-m", MODULE, *arguments], cwd=cwd, env=environment,
                               capture_output=True, timeout=30)
    return completed.returncode, completed.stdout, completed.stderr.decode()


def test_the_stdio_server_lists_exactly_one_tool_and_confirms_then_refuses_a_replay(world, errlog):
    grant_id = issued_now(world)
    grant = world.grants.get_grant(grant_id)
    accounts = dump(world.connection)["accounts"]
    (tools, resources, templates, prompts, name), (first, second) = calls(
        world, errlog, {"grant_id": grant_id}, {"grant_id": grant_id})
    assert name == "baec-trigger-intelligence-confirmation"
    assert [t.name for t in tools] == ["confirm_baec"] and resources == templates == prompts == []
    assert first.structured_content == {"status": "confirmed", "baec_id": grant.baec_id, "proposal_id": world.pid,
                                        "review_revision_id": grant.review_revision_id, "grant_id": grant_id}
    assert refusal(second) == {"status": "refused", "code": "grant_already_consumed", "grant_id": grant_id}
    assert world.effects() == {t: 1 for t in EFFECTS}
    assert dump(world.connection)["accounts"] == accounts
    assert world.count("account_state_transitions") == world.count("dormancy_judgments") == 0
    assert world.connection.execute("SELECT normalized_text, normalized_generated_at, normalized_model "
                                    "FROM baec_records").fetchone() == (None, None, None)
    assert "Traceback" not in stderr_text(errlog)


def test_an_expired_grant_is_refused_over_stdio_with_no_write(world, errlog):
    grant_id = world.grant().grant_id  # issued 2026-05-01T12:05Z: expired on the real clock
    before = dump(world.connection)
    _, (result,) = calls(world, errlog, {"grant_id": grant_id})
    assert refusal(result) == {"status": "refused", "code": "grant_expired", "grant_id": grant_id}
    assert dump(world.connection) == before


def test_extra_authoritative_fields_are_rejected_over_stdio(world, errlog):
    grant_id = issued_now(world)
    before = dump(world.connection)
    _, results = calls(world, errlog, {"grant_id": grant_id, "actor": "attacker"},
                       {"grant_id": grant_id, "baec_id": "BAEC-attacker"},
                       {"grant_id": grant_id, "reviewed_content": {"buyer_exact_statement": "forged"}})
    for result in results:
        text = result.content[0].text
        assert result.is_error and "Extra inputs are not permitted" in text and "status" not in text
        assert "attacker" not in text and "forged" not in text
    assert dump(world.connection) == before


def test_stdout_carries_only_protocol_messages_and_an_unexpected_state_is_generic(world):
    grant_id = issued_now(world)
    server = RawServer(world.path, module=MODULE)
    server.initialize()
    listed = server.request(1, "tools/list")
    called = server.request(2, "tools/call", {"name": "confirm_baec", "arguments": {"grant_id": grant_id}})
    replay = server.request(3, "tools/call", {"name": "confirm_baec", "arguments": {"grant_id": grant_id}})
    returncode, stderr = server.finish()
    assert returncode == 0 and "stopped; write connection closed" in stderr
    assert_only_protocol_messages(server.lines)
    assert [t["name"] for t in listed["result"]["tools"]] == ["confirm_baec"]
    assert called["result"]["structuredContent"]["status"] == "confirmed" and not called["result"].get("isError")
    assert replay["result"]["isError"] is True
    assert "grant_already_consumed" in replay["result"]["content"][0]["text"]


def test_a_missing_database_is_refused_on_stderr_and_never_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    returncode, stdout, stderr = start_and_fail(["--database", str(missing)])
    assert returncode == 1 and stdout == b"" and "cannot start:" in stderr and "Traceback" not in stderr
    assert not missing.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("database", [":memory:", "file:baec.sqlite3?mode=rwc", "FILE:/tmp/x.sqlite3", "", "   "],
                         ids=["memory", "file-uri", "file-uri-uppercase", "empty", "blank"])
def test_memory_uri_and_blank_database_arguments_are_refused(tmp_path, database):
    returncode, stdout, stderr = start_and_fail(["--database", database], cwd=tmp_path)
    assert returncode == 1 and stdout == b""
    assert "cannot start: a path to an existing database file is required" in stderr
    assert list(tmp_path.iterdir()) == []


def test_a_directory_a_non_database_an_empty_file_and_an_old_schema_are_refused_unchanged(tmp_path):
    import sqlite3
    garbage = tmp_path / "notes.sqlite3"
    garbage.write_bytes(b"this is not a SQLite database" * 10)
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    old = tmp_path / "v6.sqlite3"
    Authorized(str(old)).connection.close()
    raw = sqlite3.connect(old)
    raw.execute("PRAGMA user_version = 6")
    raw.close()
    snapshot = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    for target in (tmp_path, garbage, empty, old):
        returncode, stdout, stderr = start_and_fail(["--database", str(target)])
        assert returncode == 1 and stdout == b"" and "cannot start:" in stderr, target
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == snapshot  # nothing created, seeded, or migrated


def test_usage_errors_and_help_never_write_to_stdout():
    returncode, stdout, stderr = start_and_fail([])
    assert returncode == 2 and stdout == b"" and "--database" in stderr
    returncode, stdout, stderr = start_and_fail(["--help"])
    assert returncode == 0 and stdout == b"" and "usage: python -m baec_app.mcp_write" in stderr
    returncode, stdout, stderr = start_and_fail(["--grant-id", "grant_" + "0" * 64, "--database", "x"])
    assert returncode == 2 and stdout == b""  # no other option exists


def test_closing_stdin_before_initialize_stops_cleanly(world):
    before, files = dump(world.connection), files_in(world.path)
    server = RawServer(world.path, module=MODULE)
    returncode, stderr = server.finish()
    assert returncode == 0 and server.lines == [] and "stopped; write connection closed" in stderr
    assert dump(world.connection) == before and files_in(world.path) == files


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
@pytest.mark.parametrize("initialized", [False, True], ids=["before-initialize", "after-initialize"])
def test_a_stop_signal_while_idle_closes_the_connection_and_exits_promptly(world, signum, initialized):
    before, files = dump(world.connection), files_in(world.path)
    server = RawServer(world.path, module=MODULE)
    if initialized:
        server.initialize()
    server.wait_until_stop_signals_armed()  # a readiness condition, not a delay
    returncode, elapsed, stderr = server.stop(signum)
    assert returncode == 128 + signum and elapsed < 5
    assert f"{signal.Signals(signum).name} received; write connection closed" in stderr
    assert_only_protocol_messages(server.lines)
    assert dump(world.connection) == before and files_in(world.path) == files


def test_the_stop_signal_readiness_line_is_logged_only_inside_the_signal_receiver():
    import ast
    tree = ast.parse((REPO_ROOT / "baec_app" / "mcp_write" / "__main__.py").read_text(encoding="utf-8"))
    (receiver,) = [n for n in ast.walk(tree) if isinstance(n, ast.With)
                   and "open_signal_receiver" in ast.unparse(n.items[0].context_expr)]
    first = receiver.body[0]
    assert isinstance(first, ast.Expr) and ast.unparse(first.value) == "_logger.info('stop signals armed')"
    mentions = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "stop signals armed"]
    assert len(mentions) == 1
