"""Phase 5B: server factory, protocol surface, isolation, and runtime lifecycle."""

import sqlite3
import types

import pytest
from mcp import Client

from baec_app.application import (
    DatabaseVersionError,
    HumanApproval,
    HumanCommandFacade,
    ProposalFacade,
    ReadDatabaseUnavailable,
    ReadService,
    build_command_facade,
    build_proposal_facade,
    open_read_connection,
)
from baec_app.application.account_state import AccountStateService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.data.database import open_database
from baec_app.data.repository import Repository
from baec_app.domain.models import Account, HumanAuthorization
from baec_app.mcp import composition
from baec_app.mcp.composition import McpRuntime, open_mcp_runtime
from baec_app.mcp.server import build_mcp_server
from tests.application_builders import FixedClock, SequentialIds
from tests.mcp_builders import APPROVED_TOOLS, FIXED_RESOURCES, RESOURCE_TEMPLATES, Writer, connected, run, seeded_database


@pytest.fixture
def path(tmp_path):
    return seeded_database(tmp_path)


# --- protocol surface ---------------------------------------------------------------


def test_connection_capabilities_and_empty_tool_and_prompt_lists(path):
    """Prompts stay empty. Since 5C the tool list is exactly the four read-only previews (name kept for ID continuity)."""

    async def main():
        async with connected(path) as (_, client):
            return (
                client.protocol_version,
                client.server_info.name,
                client.server_capabilities,
                (await client.list_tools()).tools,
                (await client.list_prompts()).prompts,
            )

    version, name, capabilities, tools, prompts = run(main)
    assert version == "2026-07-28" and name == "baec-trigger-intelligence"
    assert capabilities.resources is not None
    assert sorted(t.name for t in tools) == sorted(APPROVED_TOOLS) and prompts == []
    assert capabilities.tools is not None


def test_fixed_resources_and_templates_are_listed_separately_and_exactly(path):
    async def main():
        async with connected(path) as (_, client):
            return (await client.list_resources()).resources, (await client.list_resource_templates()).resource_templates

    fixed, templates = run(main)
    assert {str(r.uri) for r in fixed} == FIXED_RESOURCES
    assert {t.uri_template for t in templates} == RESOURCE_TEMPLATES
    assert len(fixed) + len(templates) == 8
    assert {r.mime_type for r in fixed} == {t.mime_type for t in templates} == {"application/json"}


# --- the factory accepts only an exact ProposalFacade ---------------------------------


class _FacadeSubclass(ProposalFacade):
    pass


def _not_facades(path):
    writer = open_database(path)
    repository = Repository(writer)
    reader = open_read_connection(path)
    facade = build_proposal_facade(reader)
    values = {
        "None": None,
        "path string": path,
        "repository": repository,
        "writable connection": writer,
        "read connection": reader,
        "read service": facade.reads,
        "command facade": build_command_facade(repository, clock=FixedClock(), ids=SequentialIds()),
        "look-alike": types.SimpleNamespace(reads=facade.reads),
        "subclass": _FacadeSubclass(facade.reads, facade._previews),
    }
    return values, (writer, reader)


def test_build_mcp_server_refuses_anything_but_an_exact_proposal_facade(path):
    values, connections = _not_facades(path)
    try:
        for label, value in values.items():
            with pytest.raises(TypeError):
                build_mcp_server(value)
    finally:
        for connection in connections:
            connection.close()


def test_build_mcp_server_accepts_a_proposal_facade(path):
    reader = open_read_connection(path)
    try:
        assert type(build_mcp_server(build_proposal_facade(reader))).__name__ == "MCPServer"
    finally:
        reader.close()


# --- isolation --------------------------------------------------------------------------

FORBIDDEN = (
    HumanConfirmationGate, HumanCommandFacade, ClassificationService, DormancyJudgmentService,
    AccountStateService, HumanApproval, HumanAuthorization,
)


def _reachable(root, limit=400_000):
    """Every object reachable through attributes, containers, closures and bound methods."""
    seen, stack, found = set(), [root], []
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, (type, types.ModuleType, str, bytes, int, float, bool)) or obj is None:
            continue
        seen.add(id(obj))
        found.append(obj)
        assert len(found) < limit
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        if isinstance(obj, types.FunctionType):
            stack.extend(cell.cell_contents for cell in (obj.__closure__ or ()) if _has_contents(cell))
            stack.extend((obj.__defaults__ or ()))
        if isinstance(obj, types.MethodType):
            stack.extend((obj.__self__, obj.__func__))
        if hasattr(obj, "__dict__") and not isinstance(obj, types.FunctionType):
            stack.extend(vars(obj).values())
        for slot in getattr(type(obj), "__slots__", ()) if isinstance(getattr(type(obj), "__slots__", ()), (tuple, list)) else ():
            if hasattr(obj, slot):
                stack.append(getattr(obj, slot))
    return found


def _has_contents(cell):
    try:
        cell.cell_contents
    except ValueError:
        return False
    return True


def test_the_server_reaches_one_read_only_facade_and_no_command_side_object(path):
    with open_mcp_runtime(path) as runtime:
        reachable = _reachable(runtime.server)
        facades = [o for o in reachable if type(o) is ProposalFacade]
        reads = [o for o in reachable if type(o) is ReadService]
        connections = [o for o in reachable if type(o) is sqlite3.Connection]
        assert len(facades) == 1 and reads
        assert connections and all(c.execute("PRAGMA query_only").fetchone()[0] == 1 for c in connections)
        assert not [o for o in reachable if isinstance(o, FORBIDDEN)]
        repositories = [o for o in reachable if type(o) is Repository]
        assert repositories and all(r._db in connections for r in repositories)


def test_the_walk_would_find_command_side_objects_if_they_were_reachable(path):
    """Positive control for the isolation walk."""
    writer = open_database(path)
    try:
        facade = build_command_facade(Repository(writer), clock=FixedClock(), ids=SequentialIds())
        found = _reachable(facade)
        assert any(isinstance(o, HumanConfirmationGate) for o in found)
        assert any(isinstance(o, AccountStateService) for o in found)
    finally:
        writer.close()


def test_a_write_through_the_servers_repository_is_rejected_by_sqlite(path):
    writer = Writer(path)
    try:
        before = writer.dump()
        with open_mcp_runtime(path) as runtime:
            repository = next(o for o in _reachable(runtime.server) if type(o) is Repository)
            with pytest.raises(sqlite3.OperationalError):
                repository.add_account(Account("ACC-X", "Synthetic"))
            connection = repository._db
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-Y', 'Synthetic')")
        assert writer.dump() == before
    finally:
        writer.close()


# --- runtime lifecycle ---------------------------------------------------------------------


def _connection_of(runtime):
    return next(o for o in _reachable(runtime.server) if type(o) is sqlite3.Connection)


def _is_closed(connection):
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_close_is_explicit_deterministic_and_idempotent(path):
    runtime = open_mcp_runtime(path)
    connection = _connection_of(runtime)
    assert type(runtime) is McpRuntime and not runtime.closed and not _is_closed(connection)
    runtime.close()
    assert runtime.closed and _is_closed(connection)
    runtime.close()
    with pytest.raises(RuntimeError):
        runtime.server


def test_the_context_manager_closes_the_connection(path):
    with open_mcp_runtime(path) as runtime:
        connection = _connection_of(runtime)
    assert _is_closed(connection)


def test_a_failure_while_building_closes_the_connection(path, monkeypatch):
    opened = []
    real_open = composition.open_read_connection
    monkeypatch.setattr(composition, "open_read_connection", lambda p: opened.append(real_open(p)) or opened[-1])

    def fail(facade):
        raise RuntimeError("build failed")

    monkeypatch.setattr(composition, "build_mcp_server", fail)
    with pytest.raises(RuntimeError, match="build failed"):
        open_mcp_runtime(path)
    assert len(opened) == 1 and _is_closed(opened[0])


def test_the_runtime_does_not_expose_its_connection_publicly(path):
    with open_mcp_runtime(path) as runtime:
        public = {name for name in dir(runtime) if not name.startswith("_")}
        assert public == {"server", "closed", "close"}


def test_missing_and_wrong_schema_databases_are_refused_without_creating_anything(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ReadDatabaseUnavailable):
        open_mcp_runtime(missing)
    assert not missing.exists()
    old = tmp_path / "v3.sqlite3"
    connection = open_database(str(old))
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    with pytest.raises(DatabaseVersionError):
        open_mcp_runtime(old)


def test_the_server_works_through_a_client_after_composition(path):
    async def main():
        async with connected(path) as (runtime, client):
            assert isinstance(client, Client)
            return (await client.read_resource("baec://accounts")).contents[0].text

    assert '"accounts"' in run(main)
