"""Phase 5D: the stdio entry point's composition and lifecycle, exercised in-process.

main() is called directly, with the SDK's stdio transport (MCPServer.run_stdio_async)
replaced by a stand-in, so each exit path can be observed: the read connection
must be closed on every one of them, stdout must stay empty, and the server main()
serves must reach only the read-only facade.
"""

import sqlite3
import threading

import pytest
from mcp.server import MCPServer

from baec_app.application import (
    DatabaseVersionError,
    HumanApproval,
    HumanCommandFacade,
    ProposalFacade,
    ReadService,
)
from baec_app.application.account_state import AccountStateService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.data.database import open_database
from baec_app.data.repository import Repository
from baec_app.domain.models import HumanAuthorization
from baec_app.mcp import __main__ as entry_point
from baec_app.mcp import composition
from tests.mcp_builders import Writer, seeded_database
from tests.test_mcp_server import _is_closed, _reachable

COMMAND_SIDE = (
    HumanCommandFacade, HumanConfirmationGate, HumanApproval, HumanAuthorization,
    ClassificationService, DormancyJudgmentService, AccountStateService,
)


@pytest.fixture
def path(tmp_path):
    return seeded_database(tmp_path)


@pytest.fixture
def opened(monkeypatch):
    """Every read connection the entry point opens, in order, with the thread that opened it."""
    connections = []
    real_open = composition.open_read_connection

    def recording(database_path):
        connection = real_open(database_path)
        connections.append((connection, threading.get_ident()))
        return connection

    monkeypatch.setattr(composition, "open_read_connection", recording)
    return connections


def serve_with(monkeypatch, transport):
    """Replace the SDK stdio transport with `transport(server)`."""

    async def run_stdio_async(self):
        return await transport(self)

    monkeypatch.setattr(MCPServer, "run_stdio_async", run_stdio_async)


def test_normal_shutdown_closes_the_read_connection(path, opened, monkeypatch, capsys):
    served = []

    async def transport(server):
        served.append((server, threading.get_ident()))

    serve_with(monkeypatch, transport)
    assert entry_point.main(["--database", path]) == 0
    (connection, opener), = opened
    assert _is_closed(connection)
    assert served[0][1] == opener  # served on the thread that opened the thread-affine connection
    assert capsys.readouterr().out == ""


def test_a_transport_failure_is_logged_and_closes_the_read_connection(path, opened, monkeypatch, capsys, caplog):
    async def transport(server):
        raise OSError("SECRET-TRANSPORT-DETAIL broken pipe")

    serve_with(monkeypatch, transport)
    assert entry_point.main(["--database", path]) == 1
    (connection, _), = opened
    assert _is_closed(connection)
    assert "the stdio transport failed" in caplog.text
    assert capsys.readouterr().out == ""


def test_an_interrupt_that_escapes_the_transport_still_closes_the_read_connection(path, opened, monkeypatch, capsys):
    """A KeyboardInterrupt the signal receiver did not catch propagates (anyio wraps it in a group); the
    runtime context still closes the connection."""

    async def transport(server):
        raise KeyboardInterrupt

    serve_with(monkeypatch, transport)
    with pytest.raises((KeyboardInterrupt, BaseExceptionGroup)):
        entry_point.main(["--database", path])
    (connection, _), = opened
    assert _is_closed(connection)
    assert capsys.readouterr().out == ""


def test_a_failure_after_the_connection_opens_closes_it_before_the_server_exists(path, opened, monkeypatch, capsys):
    def fail(facade):
        raise RuntimeError("server construction failed")

    monkeypatch.setattr(composition, "build_mcp_server", fail)
    serve_with(monkeypatch, pytest.fail)
    with pytest.raises(RuntimeError, match="server construction failed"):
        entry_point.main(["--database", path])
    (connection, _), = opened
    assert _is_closed(connection)
    assert capsys.readouterr().out == ""


def test_a_composition_failure_before_any_connection_is_reported_on_stderr(tmp_path, opened, monkeypatch, capsys, caplog):
    old = tmp_path / "v3.sqlite3"
    connection = open_database(str(old))
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    serve_with(monkeypatch, pytest.fail)
    for database in (str(tmp_path / "absent.sqlite3"), ":memory:", "file:x.sqlite3", str(old)):
        assert entry_point.main(["--database", database]) == 1
    assert opened == []
    assert caplog.text.count("cannot start:") == 4
    assert capsys.readouterr().out == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["v3.sqlite3"]
    with pytest.raises(DatabaseVersionError):
        composition.open_mcp_runtime(old)


def test_the_entry_point_opens_sqlite_only_read_only(path, monkeypatch):
    targets = []
    real_connect = sqlite3.connect

    def recording(database, *args, **kwargs):
        targets.append((database, kwargs.get("uri")))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording)

    async def transport(server):
        pass

    serve_with(monkeypatch, transport)
    assert entry_point.main(["--database", path]) == 0
    assert len(targets) == 1
    database, uri = targets[0]
    assert uri is True and database.startswith("file:") and database.endswith("?mode=ro")


def test_the_served_server_reaches_only_the_read_only_facade(path, monkeypatch):
    """The object-graph walk, repeated on the exact server main() hands to the stdio transport."""
    findings = {}

    async def transport(server):
        reachable = _reachable(server)
        findings["facades"] = [o for o in reachable if type(o) is ProposalFacade]
        findings["reads"] = [o for o in reachable if type(o) is ReadService]
        findings["command side"] = [o for o in reachable if isinstance(o, COMMAND_SIDE)]
        connections = [o for o in reachable if type(o) is sqlite3.Connection]
        findings["query_only"] = [c.execute("PRAGMA query_only").fetchone()[0] for c in connections]
        findings["repositories on writable connections"] = [
            o for o in reachable if type(o) is Repository and o._db.execute("PRAGMA query_only").fetchone()[0] != 1
        ]
        with pytest.raises(sqlite3.OperationalError):
            connections[0].execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-Z', 'Synthetic')")

    serve_with(monkeypatch, transport)
    writer = Writer(path)
    try:
        before = writer.dump()
        assert entry_point.main(["--database", path]) == 0
        assert writer.dump() == before
    finally:
        writer.close()
    assert len(findings["facades"]) == 1 and findings["reads"]
    assert findings["command side"] == []
    assert findings["query_only"] and set(findings["query_only"]) == {1}
    assert findings["repositories on writable connections"] == []


def test_help_and_usage_errors_write_nothing_to_stdout(capsys):
    with pytest.raises(SystemExit) as exited:
        entry_point.main(["--help"])
    assert exited.value.code == 0
    out, err = capsys.readouterr()
    assert out == "" and "--database PATH" in err
    for arguments in ([], ["--database"], ["--database", "x", "--extra"], ["positional.sqlite3"]):
        with pytest.raises(SystemExit) as exited:
            entry_point.main(arguments)
        assert exited.value.code == 2
        out, err = capsys.readouterr()
        assert out == "" and "usage:" in err
