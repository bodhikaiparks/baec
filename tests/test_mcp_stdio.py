"""Phase 5D: the stdio entry point, run as a real child process.

Most tests use the SDK's own stdio client transport (Client over stdio_client)
against `python -m baec_app.mcp --database PATH`. The stdout-discipline and
lifecycle tests drive the child directly through pipes, so they can see every
byte it writes. Everything is local: no sockets and no external services.

The lifecycle tests record what mcp 2.2.0 actually does; see the docstrings.
"""

import ast
import json
import os
import signal
import subprocess
import sys
import threading
import time

import mcp_types
import pytest
from mcp import MCPError

from baec_app.data.database import open_database
from baec_app.mcp import adapters, contracts
from tests.mcp_builders import (
    APPROVED_TOOLS,
    FIXED_RESOURCES,
    REPO_ROOT,
    RESOURCE_TEMPLATES,
    Writer,
    connected,
    run,
    seeded_database,
    server_command,
    stdio_connected,
)
from tests.mcp_stdio_fault_server import SECRET as FAULT_SECRET
from tests.persistence_builders import tamper
from tests.test_mcp_tools import oracle

PROTOCOL_VERSION = "2026-07-28"
SPAWN_TIMEOUT = 30


@pytest.fixture
def db(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    path = seeded_database(directory)
    writer = Writer(path)
    yield path, writer
    writer.close()


@pytest.fixture
def errlog(tmp_path):
    with open(tmp_path / "server-stderr.txt", "w+", encoding="utf-8") as file:
        yield file


def stderr_text(errlog):
    errlog.flush()
    errlog.seek(0)
    return errlog.read()


def files_in(path):
    return sorted(os.listdir(os.path.dirname(path)))


def server_children():
    """Live or unreaped child processes of this test process that run the MCP server."""
    listing = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,stat=,command="], capture_output=True, text=True, check=True)
    found = []
    for line in listing.stdout.splitlines():
        pid, ppid, stat, command = line.split(None, 3)
        if int(ppid) == os.getpid() and ("baec_app.mcp" in command or "mcp_stdio_fault_server" in command or "Z" in stat):
            found.append(line)
    return found


# --- representative operations, run identically over both transports ----------------------

HARBOR_STATEMENT = "If our supplier raises pricing by more than 10% when our agreement renews, we'd evaluate other options."


def seed_candidate():
    excerpt = {"text": HARBOR_STATEMENT, "provenance": "BUYER_FACT", "source_id": "INT-HARBOR-001"}
    criteria = ["PRESENT_NON_EVALUATION", "PROSPECTIVE_CONDITION", "BUYER_ARTICULATION", "EVALUATION_LINKAGE"]
    return {
        "account_id": "ACC-HARBOR",
        "source_interaction_id": "INT-HARBOR-001",
        "source_excerpt": excerpt,
        "assessments": [{"criterion": c, "finding": "MET", "evidence": [excerpt], "rationale": None} for c in criteria],
        "articulation_origin": "BUYER_GENERATED",
        "elicitation_mode": "CEE_ELICITED",
        "buyer_exact_statement": HARBOR_STATEMENT,
        "buyer_role": "Materials Manager",
        "stringency": None,
    }


SUMMIT_EVIDENCE = {
    "account_id": "ACC-SUMMIT",
    "evidence": {"text": "We're under a long-term agreement and it's working.", "provenance": "BUYER_FACT", "source_id": "INT-SUMMIT-001"},
    "observed_at": "2026-03-20T09:00:00+00:00",
}

OPERATIONS = {
    "fixed resource": ("resource", "baec://accounts"),
    "account resource": ("resource", "baec://accounts/ACC-HARBOR"),
    "templated interactions resource": ("resource", "baec://accounts/ACC-SUMMIT/interactions"),
    "baec resource": ("resource", "baec://baecs/BAEC-HARBOR-001"),
    "classification preview": ("tool", "preview_baec_classification", {"candidate": seed_candidate()}),
    "dormant preview": ("tool", "preview_move_to_conditionally_dormant",
                        {"account_id": "ACC-HARBOR", "baec_id": "BAEC-HARBOR-001", "judgment_id": 1}),
    "active preview": ("tool", "preview_move_to_active_opportunity", {"account_id": "ACC-SUMMIT", "evaluation_evidence": SUMMIT_EVIDENCE}),
    "no-plausible-path preview": ("tool", "preview_move_to_no_plausible_path",
                                  {"account_id": "ACC-HARBOR", "ground": "NO_PLAUSIBLE_BAEC", "reason": "CEE produced no condition."}),
    "tool not found": ("tool", "preview_move_to_active_opportunity", {"account_id": "ACC-404", "evaluation_evidence": None}),
    "resource not found": ("resource", "baec://accounts/ACC-404"),
    "tool schema failure": ("tool", "preview_move_to_active_opportunity",
                            {"account_id": "ACC-SUMMIT", "evaluation_evidence": None, "approved": True}),
    "resource identifier failure": ("resource", "baec://baecs/B..1"),
}


async def perform(client, operation):
    """Run one operation and decode it into transport-independent values."""
    if operation[0] == "resource":
        try:
            result = await client.read_resource(operation[1])
        except MCPError as error:
            return {"error": error.error.code, "message": str(error), "data": error.error.data}
        (content,) = result.contents
        return {"uri": str(content.uri), "mime_type": content.mime_type, "json": json.loads(content.text)}
    result = await client.call_tool(operation[1], operation[2])
    text = " ".join(getattr(block, "text", "") for block in result.content)
    if result.is_error:
        return {"is_error": True, "text": text}
    return {"is_error": False, "structured": result.structured_content, "json": json.loads(text)}


def run_over_stdio(path, writer, errlog, operations):
    before = writer.dump()

    async def main():
        outcomes = []
        async with stdio_connected(path, errlog) as client:
            for operation in operations:
                outcomes.append(await perform(client, operation))
                assert writer.dump() == before
        return outcomes

    outcomes = run(main)
    assert writer.dump() == before
    return outcomes


def run_in_process(path, writer, operations):
    before = writer.dump()

    async def main():
        async with connected(path) as (_, client):
            return [await perform(client, operation) for operation in operations]

    outcomes = run(main)
    assert writer.dump() == before
    return outcomes


# --- smoke ------------------------------------------------------------------------------------


def test_the_stdio_server_starts_handshakes_and_lists_exactly_the_approved_surface(db, errlog):
    path, writer = db
    before, files = writer.dump(), files_in(path)

    async def main():
        async with stdio_connected(path, errlog) as client:
            return (
                client.protocol_version,
                client.server_info.name,
                (await client.list_resources()).resources,
                (await client.list_resource_templates()).resource_templates,
                (await client.list_tools()).tools,
                (await client.list_prompts()).prompts,
            )

    version, name, fixed, templates, tools, prompts = run(main)
    assert version == PROTOCOL_VERSION and name == "baec-trigger-intelligence"
    assert {str(r.uri) for r in fixed} == FIXED_RESOURCES
    assert {t.uri_template for t in templates} == RESOURCE_TEMPLATES and len(fixed) + len(templates) == 8
    assert sorted(t.name for t in tools) == sorted(APPROVED_TOOLS) and prompts == []
    for tool in tools:
        hints = tool.annotations
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint, hints.open_world_hint) == (True, False, True, False)
    assert writer.dump() == before and files_in(path) == files
    log = stderr_text(errlog)
    assert "serving the read-only MCP Core over stdio" in log and "stopped; read connection closed" in log
    assert "Traceback" not in log
    assert server_children() == []


def test_every_representative_operation_succeeds_or_fails_as_expected_over_stdio(db, errlog):
    path, writer = db
    outcomes = dict(zip(OPERATIONS, run_over_stdio(path, writer, errlog, list(OPERATIONS.values()))))
    for label in ("classification preview", "dormant preview", "active preview", "no-plausible-path preview"):
        assert outcomes[label]["is_error"] is False, label
    assert outcomes["classification preview"]["structured"]["classification"] == "CONFIRMED_BAEC"
    assert outcomes["dormant preview"]["structured"]["rejections"] == ["SAME_STATE"]
    assert outcomes["active preview"]["structured"]["rejections"] == ["AUTHORIZATION_MISSING"]
    assert outcomes["no-plausible-path preview"]["structured"]["rejections"] == ["AUTHORIZATION_MISSING"]
    assert [a["account_id"] for a in outcomes["fixed resource"]["json"]["accounts"]] == ["ACC-HARBOR", "ACC-SUMMIT", "ACC-MERIDIAN"]
    assert outcomes["account resource"]["json"]["state"] == "CONDITIONALLY_DORMANT"
    assert outcomes["templated interactions resource"]["json"]["interactions"][0]["interaction_id"] == "INT-SUMMIT-001"
    assert outcomes["baec resource"]["json"]["candidate"]["buyer_exact_statement"] == HARBOR_STATEMENT
    assert outcomes["tool not found"] == {
        "is_error": True,
        "text": "Error executing tool preview_move_to_active_opportunity: not_found: "
                "a referenced account, interaction, BAEC record, or judgment does not exist",
    }
    assert outcomes["resource not found"] == {
        "error": -32602, "message": "resource not found: baec://accounts/ACC-404", "data": {"uri": "baec://accounts/ACC-404"},
    }
    assert outcomes["tool schema failure"]["is_error"] and "[type=extra_forbidden]" in outcomes["tool schema failure"]["text"]
    assert outcomes["resource identifier failure"] == {"error": -32602, "message": "invalid baec_id", "data": None}


def test_stdio_preview_results_match_the_phase_4_oracle(db, errlog):
    path, writer = db
    labels = ["classification preview", "dormant preview", "active preview", "no-plausible-path preview"]
    outcomes = dict(zip(labels, run_over_stdio(path, writer, errlog, [OPERATIONS[label] for label in labels])))
    candidate = adapters.candidate_from_wire(contracts.BaecCandidateIn.model_validate(seed_candidate()))
    evidence = adapters.evaluation_evidence_from_wire(contracts.EvaluationEvidenceIn.model_validate(SUMMIT_EVIDENCE))
    expected = {
        "classification preview": oracle(path, lambda f: adapters.classification_preview_view(f.preview_classification(candidate))),
        "dormant preview": oracle(path, lambda f: adapters.transition_preview_view(f.preview_move_to_conditionally_dormant(
            "ACC-HARBOR", baec_id="BAEC-HARBOR-001", judgment_id=1))),
        "active preview": oracle(path, lambda f: adapters.transition_preview_view(
            f.preview_move_to_active_opportunity("ACC-SUMMIT", evaluation_evidence=evidence))),
        "no-plausible-path preview": oracle(path, lambda f: adapters.transition_preview_view(f.preview_move_to_no_plausible_path(
            "ACC-HARBOR", ground=adapters.ground_from_wire("NO_PLAUSIBLE_BAEC"), reason="CEE produced no condition."))),
    }
    for label in labels:
        assert outcomes[label]["structured"] == outcomes[label]["json"] == expected[label], label


def test_in_process_and_stdio_results_are_semantically_identical(db, errlog):
    path, writer = db
    operations = list(OPERATIONS.values())
    assert run_in_process(path, writer, operations) == run_over_stdio(path, writer, errlog, operations)


# --- stdout discipline -------------------------------------------------------------------------


class RawServer:
    """The child process driven directly through pipes, one request and one response line at a time."""

    def __init__(self, path, module="baec_app.mcp"):
        self.process = subprocess.Popen(
            server_command(path, module), cwd=REPO_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.lines = []
        self._stderr_head = b""
        self._watchdog = threading.Timer(SPAWN_TIMEOUT, self.process.kill)
        self._watchdog.start()

    def wait_until_stop_signals_armed(self):
        """Read stderr until the child logs that its SIGTERM/SIGINT receiver is installed. Sends nothing.

        The child logs this line inside the signal-receiver context, so it can appear only after the handlers
        exist. The watchdog bounds the wait: a child that never arms its handlers is killed and this fails.
        """
        while b"stop signals armed" not in self._stderr_head:
            line = self.process.stderr.readline()
            assert line, "the child exited or closed stderr before arming its stop signals"
            self._stderr_head += line

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def send_raw(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def request(self, request_id, method, params=None):
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method} | ({"params": params} if params is not None else {}))
        line = self.process.stdout.readline()
        self.lines.append(line)
        return json.loads(line)

    def initialize(self):
        """The legacy initialize handshake, which the server also accepts (it negotiates 2025-11-25).

        The SDK client instead negotiates 2026-07-28 through server/discover; these pipe-level tests
        only need a live session to observe every byte the child writes.
        """
        response = self.request(0, "initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "raw-test", "version": "0"},
        })
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def _collect(self):
        """Wait for exit, read the remaining output, and close every pipe."""
        returncode = self.process.wait(SPAWN_TIMEOUT)
        self._watchdog.cancel()
        if not self.process.stdout.closed:
            self.lines += self.process.stdout.readlines()
        stderr = (self._stderr_head + self.process.stderr.read()).decode()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()
        return returncode, stderr

    def finish(self):
        """Close stdin (the normal stdio shutdown) and collect everything the child wrote."""
        self.process.stdin.close()
        return self._collect()

    def stop(self, signum):
        started = time.monotonic()
        self.process.send_signal(signum)
        self.process.wait(SPAWN_TIMEOUT)
        elapsed = time.monotonic() - started
        returncode, stderr = self._collect()
        return returncode, elapsed, stderr


def assert_only_protocol_messages(lines):
    for line in lines:
        assert line.endswith(b"\n"), line
        mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)


def test_stdout_carries_only_protocol_messages_while_diagnostics_go_to_stderr(db):
    path, writer = db
    tamper(writer.connection, "UPDATE baec_records SET articulation_origin = 'SELLER_SEEDED' WHERE baec_id = 'BAEC-HARBOR-001'")
    before = writer.dump()
    server = RawServer(path)
    assert server.initialize()["result"]["serverInfo"]["name"] == "baec-trigger-intelligence"
    assert server.request(1, "tools/list")["result"]["tools"]
    integrity = server.request(2, "resources/read", {"uri": "baec://baecs/BAEC-HARBOR-001"})
    assert integrity["error"]["message"] == "the requested resource is unavailable"
    missing = server.request(3, "tools/call", {"name": "preview_move_to_active_opportunity",
                                               "arguments": {"account_id": "ACC-404", "evaluation_evidence": None}})
    assert missing["result"]["isError"] is True
    invalid = server.request(4, "tools/call", {"name": "preview_move_to_active_opportunity",
                                               "arguments": {"account_id": 7, "evaluation_evidence": None}})
    assert invalid["result"]["isError"] is True
    server.send_raw(b"this line is not JSON-RPC\n")
    unknown = server.request(5, "tools/call", {"name": "confirm_baec", "arguments": {}})
    assert unknown["result"]["isError"] is True
    returncode, stderr = server.finish()
    assert returncode == 0
    assert_only_protocol_messages(server.lines)
    stdout = b"".join(server.lines).decode()
    for internal in ("Traceback", "sqlite3", path, "SELECT", "serving the read-only", "INFO"):
        assert internal not in stdout
    assert "integrity" in stderr.lower() and "Traceback" in stderr  # the details stay with the operator
    assert "serving the read-only MCP Core over stdio" in stderr and "stopped; read connection closed" in stderr
    assert writer.dump() == before


def test_an_unexpected_handler_failure_is_generic_on_stdout_and_detailed_only_on_stderr(db):
    path, writer = db
    before = writer.dump()
    server = RawServer(path, module="tests.mcp_stdio_fault_server")
    server.initialize()
    failed = server.request(1, "tools/call", {"name": "preview_move_to_active_opportunity",
                                              "arguments": {"account_id": "ACC-SUMMIT", "evaluation_evidence": None}})
    assert failed["result"] == {"content": [{"type": "text", "text": "Error executing tool preview_move_to_active_opportunity"}],
                                "isError": True}
    still_serving = server.request(2, "resources/read", {"uri": "baec://accounts/ACC-SUMMIT"})
    assert json.loads(still_serving["result"]["contents"][0]["text"])["account_id"] == "ACC-SUMMIT"
    returncode, stderr = server.finish()
    assert returncode == 0
    assert_only_protocol_messages(server.lines)
    assert FAULT_SECRET not in b"".join(server.lines).decode()
    assert FAULT_SECRET in stderr and "Traceback" in stderr
    assert writer.dump() == before


# --- start-up failures ---------------------------------------------------------------------------


def start_and_fail(arguments):
    completed = subprocess.run(
        [sys.executable, "-m", "baec_app.mcp", *arguments], cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=SPAWN_TIMEOUT,
    )
    return completed.returncode, completed.stdout, completed.stderr.decode()


def test_a_missing_database_is_refused_on_stderr_and_never_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    returncode, stdout, stderr = start_and_fail(["--database", str(missing)])
    assert returncode == 1 and stdout == b""
    assert "cannot start:" in stderr and "Traceback" not in stderr
    assert not missing.exists() and os.listdir(tmp_path) == []


@pytest.mark.parametrize(
    "database",
    [":memory:", "file:baec.sqlite3?mode=rwc", "FILE:/tmp/x.sqlite3", "", "   "],
    ids=["memory", "file-uri", "file-uri-uppercase", "empty", "blank"],
)
def test_memory_uri_and_blank_database_arguments_are_refused(tmp_path, database):
    returncode, stdout, stderr = start_and_fail(["--database", database])
    assert returncode == 1 and stdout == b"" and "cannot start: a path to an existing database file is required" in stderr
    assert not (REPO_ROOT / "baec.sqlite3").exists()


def test_a_directory_a_non_database_and_an_old_schema_are_refused(tmp_path):
    not_a_database = tmp_path / "notes.sqlite3"
    not_a_database.write_bytes(b"this is not a SQLite database" * 10)
    old = tmp_path / "v3.sqlite3"
    connection = open_database(str(old))
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    snapshot = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    for target in (tmp_path, not_a_database, old):
        returncode, stdout, stderr = start_and_fail(["--database", str(target)])
        assert returncode == 1 and stdout == b"" and "cannot start:" in stderr, target
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == snapshot


def test_usage_errors_and_help_never_write_to_stdout():
    returncode, stdout, stderr = start_and_fail([])
    assert returncode == 2 and stdout == b"" and "--database" in stderr
    returncode, stdout, stderr = start_and_fail(["--help"])
    assert returncode == 0 and stdout == b"" and "usage: python -m baec_app.mcp" in stderr
    returncode, stdout, stderr = start_and_fail(["--database"])
    assert returncode == 2 and stdout == b""


# --- lifecycle ----------------------------------------------------------------------------------


def test_closing_stdin_before_initialize_stops_the_server_cleanly(db):
    path, writer = db
    before, files = writer.dump(), files_in(path)
    server = RawServer(path)
    returncode, stderr = server.finish()
    assert returncode == 0 and server.lines == []
    assert "stopped; read connection closed" in stderr
    assert writer.dump() == before and files_in(path) == files


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
@pytest.mark.parametrize("initialized", [False, True], ids=["before-initialize", "after-initialize"])
def test_a_stop_signal_while_idle_closes_the_connection_and_exits_promptly(db, signum, initialized):
    """mcp 2.2.0 reads stdin on a worker thread that cancellation cannot interrupt, so the entry point
    closes the read connection and exits directly on SIGTERM or SIGINT instead of cancelling the transport."""
    path, writer = db
    before, files = writer.dump(), files_in(path)
    server = RawServer(path)
    if initialized:
        server.initialize()
    server.wait_until_stop_signals_armed()  # a readiness condition, not a delay
    assert initialized or server.lines == []  # before-initialize: nothing was sent and nothing was answered
    returncode, elapsed, stderr = server.stop(signum)
    assert returncode == 128 + signum and elapsed < 5
    assert f"{signal.Signals(signum).name} received; read connection closed" in stderr
    assert_only_protocol_messages(server.lines)
    assert writer.dump() == before and files_in(path) == files


def test_a_broken_stdout_pipe_is_logged_and_the_connection_closes_once_stdin_ends(db):
    """Characterizes mcp 2.2.0: after a failed write the transport cannot finish unwinding until its stdin
    reader returns, so the process stays alive until the client closes stdin, then exits with status 1."""
    path, writer = db
    before = writer.dump()
    server = RawServer(path)
    server.initialize()
    server.process.stdout.close()
    server.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    time.sleep(0.5)
    assert server.process.poll() is None
    returncode, stderr = server.finish()
    assert returncode == 1
    assert "the stdio transport failed" in stderr and "BrokenPipeError" in stderr
    assert "stopped; read connection closed" in stderr
    assert writer.dump() == before


def test_no_server_process_outlives_the_client(db, errlog):
    path, writer = db

    async def main():
        async with stdio_connected(path, errlog) as client:
            await client.read_resource("baec://accounts")
            return server_children()

    during = run(main)
    assert len(during) == 1  # the child existed while connected
    assert server_children() == []


def test_the_stop_signal_readiness_line_is_logged_only_inside_the_signal_receiver():
    """Regression guard for the readiness condition: the line must come after the receiver is installed.

    If it were logged before anyio.open_signal_receiver, the test above could signal a child whose handlers
    do not exist yet, and SIGTERM or SIGINT would end it with -15 or -2 instead of 128 + signum.
    """
    tree = ast.parse((REPO_ROOT / "baec_app" / "mcp" / "__main__.py").read_text(encoding="utf-8"))
    receivers = [node for node in ast.walk(tree) if isinstance(node, (ast.With, ast.AsyncWith))
                 and any("open_signal_receiver" in ast.unparse(item.context_expr) for item in node.items)]
    assert len(receivers) == 1
    first = receivers[0].body[0]
    assert isinstance(first, ast.Expr) and ast.unparse(first.value) == "_logger.info('stop signals armed')"
    mentions = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == "stop signals armed"]
    assert len(mentions) == 1  # logged exactly once, in exactly that place
