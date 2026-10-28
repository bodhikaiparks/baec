"""Phase 7G boundaries: the write package consumes authority and cannot manufacture it.

Static rules over baec_app/mcp_write/** (imports, names, SQL, registration, the handler itself), an object-graph walk
from the built server, import coupling in fresh interpreters, Phase 5 preservation, and the Streamlit execution
prohibition. These are conventions checked by tests, not a sandbox: Python code with access to the local database
or process could bypass them.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import types
from pathlib import Path

import anyio
import pytest

from baec_app.application import (
    HumanApproval,
    HumanCommandFacade,
    ProposalFacade,
    ReadService,
    SystemClock,
)
from baec_app.application.account_state import AccountStatePreviewService, AccountStateService
from baec_app.application.ai_proposal_mapping import AiProposalMappingService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.proposal_authorization import ConfirmationExecutionService, ProposalAuthorizationService
from baec_app.application.proposal_review import ProposalReviewService
from baec_app.data.ai_provenance import AiProvenanceStore
from baec_app.data.authorization_grants import AuthorizationGrantStore, GrantExecutionStore, GrantRecord
from baec_app.data.database import DATA_TABLES
from baec_app.data.proposal_bridge import ProposalBridgeStore
from baec_app.data.repository import Repository
from baec_app.domain.models import HumanAuthorization
from baec_app.mcp_write import __main__ as write_main
from baec_app.mcp_write import server as write_server
from baec_app.mcp_write.composition import open_write_runtime
from tests.mcp_builders import APPROVED_TOOLS, connected, seeded_database
from tests.persistence_builders import dump
from tests.test_mcp_server import _reachable
from tests.test_proposal_authorization import Authorized

REPO = Path(__file__).resolve().parents[1]
WRITE_ROOT = REPO / "baec_app" / "mcp_write"
MODULES = ("__init__", "__main__", "composition", "server")


def _tree(module: str) -> ast.Module:
    return ast.parse((WRITE_ROOT / f"{module}.py").read_text(encoding="utf-8"))


def _docstrings(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _strings(tree: ast.Module) -> list[str]:
    """Every string literal that is not a docstring."""
    skip = _docstrings(tree)
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


def _identifiers(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names |= {node.name, node.asname or node.name}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def _from_imports(tree: ast.Module) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.setdefault(node.module, set()).update(alias.name for alias in node.names)
    return found


def _imported_modules(tree: ast.Module) -> set[str]:
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
    return modules


def _call_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")


# --- the package and its imports ---------------------------------------------------------------------------------


def test_the_write_package_is_exactly_four_modules():
    assert sorted(p.stem for p in WRITE_ROOT.rglob("*.py")) == sorted(MODULES)


PROJECT_IMPORTS = {
    "__init__": {},
    "server": {"baec_app.application.proposal_authorization": {"ConfirmationExecutionService", "ExecutionRefused"}},
    "composition": {
        "baec_app.application": {"Clock"},
        "baec_app.application.proposal_authorization": {"ConfirmationExecutionService"},
        "baec_app.data.database": {"check_sqlite_version", "require_current_schema"},  # server construction only
        "baec_app.mcp_write.server": {"build_write_server"},
    },
    "__main__": {
        "baec_app.application": {"DatabaseVersionError", "SystemClock"},
        "baec_app.mcp_write.composition": {"WriteDatabaseUnavailable", "WriteRuntime", "open_write_runtime"},
    },
}
THIRD_PARTY_AND_STDLIB = {
    "__init__": set(),
    "server": {"__future__", "json", "typing", "mcp.server", "mcp.server.mcpserver.exceptions",
               "mcp.server.mcpserver.tools", "mcp.server.mcpserver.utilities.func_metadata", "mcp.types", "pydantic"},
    "composition": {"__future__", "os", "sqlite3", "pathlib", "mcp.server"},
    "__main__": {"__future__", "argparse", "logging", "os", "signal", "sys", "anyio"},
}


@pytest.mark.parametrize("module", MODULES)
def test_each_module_imports_exactly_the_approved_names(module):
    tree = _tree(module)
    project = {m: names for m, names in _from_imports(tree).items() if m.startswith("baec_app")}
    assert project == PROJECT_IMPORTS[module]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import) and any(a.name.startswith("baec_app")
                                                                               for a in n.names)]
    assert {m for m in _imported_modules(tree) if not m.startswith("baec_app")} == THIRD_PARTY_AND_STDLIB[module]
    assert not {"importlib", "__import__", "import_module", "getattr", "exec", "eval"} & _identifiers(tree)


FORBIDDEN_MODULE_PREFIXES = (
    "baec_app.ai", "anthropic", "openai", "claude_agent_sdk", "streamlit", "baec_app.interfaces", "baec_app.domain",
    "baec_app.application.account_state", "baec_app.application.dormancy", "baec_app.application.classification",
    "baec_app.application.approval", "baec_app.application.authority", "baec_app.application.proposal_review",
    "baec_app.application.ai_proposal_mapping", "baec_app.application.human_normalization",
    "baec_app.application.facades", "baec_app.data.repository", "baec_app.data.authorization_grants",
    "baec_app.data.proposal_bridge", "baec_app.data.ai_provenance", "baec_app.data.seed",
)


def _is_or_under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


@pytest.mark.parametrize("module", MODULES)
def test_no_ai_model_state_machine_review_streamlit_or_phase5_import(module):
    for name in _imported_modules(_tree(module)):
        assert not any(_is_or_under(name, p) for p in FORBIDDEN_MODULE_PREFIXES), name
        assert not _is_or_under(name, "baec_app.mcp"), name  # no coupling to the Phase 5 package


def test_only_the_composition_touches_sqlite_and_the_data_layer():
    for module in MODULES:
        imported = _imported_modules(_tree(module))
        touches = "sqlite3" in imported or any(_is_or_under(m, "baec_app.data") for m in imported)
        assert touches == (module == "composition"), module


FORBIDDEN_NAMES = {
    # grant issuance, alteration, or direct authority construction
    "ProposalAuthorizationService", "authorize_confirmation", "authorization_status", "add_grant", "GrantRecord",
    "AuthorizationGrantStore", "issue", "authorize_grant", "authority", "HumanAuthorization", "GrantLifecycle",
    "insert_confirmed_record", "record_confirmation", "create_confirmed_baec_record", "save_confirmed_baec",
    # the executor's own work, which the adapter must not redo
    "rebuild_reviewed_candidate", "classify_candidate", "grant_status", "get_grant", "lifecycle",
    # review, mapping, normalization
    "ProposalReviewService", "accept_review", "reject_review", "add_revision", "add_decision", "add_accepted_revision",
    "ProposalBridgeStore", "AiProposalMappingService", "map_artifact", "ExtractionService", "open_extraction_runtime",
    # command side, state machine, dormancy, staleness, outreach
    "Repository", "HumanConfirmationGate", "HumanCommandFacade", "build_command_facade", "ProposalFacade",
    "build_proposal_facade", "open_read_connection", "AccountStateService", "DormancyJudgmentService",
    "record_dormancy_judgment", "ClassificationService", "staleness", "outreach", "send",
    # database creation or migration
    "open_database", "initialize_schema", "migrate_v6_to_v7", "build_seed_database",
    # interfaces, models, retries
    "review_page", "streamlit", "anthropic", "retry", "sleep", "to_thread", "run_sync",
}


@pytest.mark.parametrize("module", MODULES)
def test_the_package_names_no_issuance_review_state_ai_or_retry_symbol(module):
    names = _identifiers(_tree(module))
    assert not names & FORBIDDEN_NAMES, sorted(names & FORBIDDEN_NAMES)
    assert not [n for n in names if n.startswith(("move_to_", "persist_transition", "issue_", "save_"))]


SQL_WORDS = ("INSERT", "UPDATE", "DELETE", "SELECT", "CREATE", "DROP", "ALTER", "BEGIN", "COMMIT", "ROLLBACK",
             "ATTACH", "REPLACE")


def test_no_sql_except_the_composition_foreign_key_pragma():
    for module in MODULES:
        for text in _strings(_tree(module)):
            assert not any(word in text.split() for word in SQL_WORDS), (module, text)
            assert "human_authorization" not in text and "baec_records" not in text, (module, text)
            if "PRAGMA" in text:
                assert module == "composition" and text in {"PRAGMA foreign_keys = ON", "PRAGMA foreign_keys"}, text


# --- registration and the handler --------------------------------------------------------------------------------

HANDLER = '''async def confirm_baec(grant_id: str) -> ConfirmationView:
    try:
        result = executor.execute(grant_id)
    except ExecutionRefused as refusal:
        raise ToolError(_refusal_text(refusal.code, grant_id)) from None
    return ConfirmationView(status='confirmed', baec_id=result.baec_id, proposal_id=result.proposal_id, review_revision_id=result.review_revision_id, grant_id=result.grant_id)'''


def _handler() -> ast.AsyncFunctionDef:
    (builder,) = [n for n in _tree("server").body if isinstance(n, ast.FunctionDef) and n.name == "_confirm_baec_tool"]
    (handler,) = [n for n in builder.body if isinstance(n, ast.AsyncFunctionDef)]
    return handler


def test_the_handler_is_only_execute_plus_deterministic_translation():
    """One execute call, no loop, no retry, one expected refusal; the refusal never becomes a confirmation."""
    handler = _handler()
    assert ast.unparse(handler) == HANDLER
    calls = [n for n in ast.walk(handler) if isinstance(n, ast.Call)]
    assert [ast.unparse(c) for c in calls if _call_name(c) == "execute"] == ["executor.execute(grant_id)"]
    assert not [n for n in ast.walk(handler) if isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.comprehension))]


def test_confirmed_is_produced_in_exactly_one_place():
    tree = _tree("server")
    confirmed = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "confirmed"]
    assert len(confirmed) == 2  # the Literal type of ConfirmationView.status, and the one successful return
    assert "confirmed" not in ast.unparse([n for n in tree.body if isinstance(n, ast.FunctionDef)
                                          and n.name == "_refusal_text"][0])


def test_the_one_server_is_built_with_one_tool_and_nothing_else_registers_capabilities():
    tree = _tree("server")
    (construction,) = [n for module in MODULES for n in ast.walk(_tree(module))
                       if isinstance(n, ast.Call) and _call_name(n) == "MCPServer"]
    assert {k.arg for k in construction.keywords} == {"name", "title", "version", "tools"}
    assert ast.unparse(next(k.value for k in construction.keywords if k.arg == "tools")) == \
        "[_confirm_baec_tool(executor)]"
    assert len([n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "from_function"]) == 1
    for module in MODULES:
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Call):
                assert _call_name(node) not in {"add_tool", "add_resource", "add_prompt", "add_template", "tool",
                                                "resource", "prompt"}, module
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert [ast.unparse(d) for d in node.decorator_list] in ([], ["property"]), (module, node.name)


def test_the_argument_model_is_closed_and_carries_only_the_grant_id():
    arguments = write_server.ConfirmBaecArguments
    assert set(arguments.model_fields) == {"grant_id"}
    config = arguments.model_config
    assert (config["extra"], config["strict"], config["frozen"], config["hide_input_in_errors"]) == (
        "forbid", True, True, True)
    assert set(write_server.ConfirmationView.model_fields) == {"status", "baec_id", "proposal_id",
                                                               "review_revision_id", "grant_id"}
    assert write_server.TOOL_ANNOTATIONS.model_dump(by_alias=True, exclude_none=True) == {
        "readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}


def _public(module: str) -> set[str]:
    names = set()
    for node in _tree(module).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {n for n in names if not n.startswith("_")}


def test_the_public_symbols_are_minimal():
    assert {module: _public(module) for module in MODULES} == {
        "__init__": set(),
        "server": {"SERVER_NAME", "TOOL_NAME", "TOOL_DESCRIPTION", "TOOL_ANNOTATIONS", "ConfirmBaecArguments",
                   "ConfirmationView", "build_write_server"},
        "composition": {"WriteDatabaseUnavailable", "WriteRuntime", "open_write_runtime"},
        "__main__": {"main"},
    }
    import baec_app.mcp_write as package
    assert not hasattr(package, "__all__")
    assert {n for n in vars(package) if not n.startswith("_")} <= set(MODULES)  # only its own submodules


# --- object graph ---------------------------------------------------------------------------------------------------

FORBIDDEN_TYPES = (
    ProposalAuthorizationService, ProposalReviewService, AiProposalMappingService, Repository, ProposalBridgeStore,
    AiProvenanceStore, HumanConfirmationGate, HumanCommandFacade, ProposalFacade, ReadService, AccountStateService,
    AccountStatePreviewService, DormancyJudgmentService, ClassificationService, HumanApproval, HumanAuthorization,
    GrantRecord,
)
FORBIDDEN_CALLABLES = {
    "authorize_confirmation", "add_grant", "accept_review", "reject_review", "add_revision", "add_decision",
    "add_accepted_revision", "map_artifact", "record_dormancy_judgment", "save_confirmed_baec",
    "insert_confirmed_record", "authorize_grant", "issue",
}


@pytest.fixture
def world(tmp_path):
    w = Authorized(str(tmp_path / "graph.sqlite3"))
    w.path = str(tmp_path / "graph.sqlite3")
    yield w
    w.connection.close()


ISSUANCE_ATTRIBUTES = ("add_grant", "authorize_confirmation", "issue", "reauthorize", "supersede")
ISSUANCE_CLASSES = (ProposalAuthorizationService, AuthorizationGrantStore, GrantRecord)


def _held(root):
    """Every object and every class held as a value, reachable through attributes, containers, closures, defaults,
    and bound methods. Classes are recorded but not descended into, and module globals are not followed: arbitrary
    Python code with import access is outside this prototype's claim. The claim is about what the composed server
    itself holds."""
    seen, stack, objects, classes = set(), [root], [], []
    while stack:
        obj = stack.pop()
        if id(obj) in seen or obj is None or isinstance(obj, (types.ModuleType, str, bytes, int, float, bool)):
            continue
        seen.add(id(obj))
        if isinstance(obj, type):
            classes.append(obj)
            continue
        objects.append(obj)
        assert len(objects) < 400_000
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        if isinstance(obj, types.FunctionType):
            stack.extend(cell.cell_contents for cell in (obj.__closure__ or ()) if _has_contents(cell))
            stack.extend(obj.__defaults__ or ())
            stack.extend((obj.__kwdefaults__ or {}).values())
        if isinstance(obj, types.MethodType):
            stack.extend((obj.__self__, obj.__func__))
        if hasattr(obj, "__dict__") and not isinstance(obj, types.FunctionType):
            stack.extend(vars(obj).values())
        slots = getattr(type(obj), "__slots__", ())
        for slot in slots if isinstance(slots, (tuple, list)) else ():
            if hasattr(obj, slot):
                stack.append(getattr(obj, slot))
    return objects, classes


def _has_contents(cell):
    try:
        cell.cell_contents
    except ValueError:
        return False
    return True


def issuance_capabilities(root) -> list[str]:
    """Every grant-issuance capability the composed object graph holds: an issuance-capable instance or class, an
    application object or class exposing an issuance attribute, or a bound or plain callable with an issuance name."""
    objects, classes = _held(root)
    found = []
    for obj in objects:
        kind = type(obj)
        if isinstance(obj, ISSUANCE_CLASSES):
            found.append(f"instance of {kind.__qualname__}")
        if kind.__module__.startswith("baec_app"):
            found += [f"{kind.__qualname__}.{name}" for name in ISSUANCE_ATTRIBUTES if hasattr(obj, name)]
        if callable(obj) and getattr(obj, "__name__", None) in ISSUANCE_ATTRIBUTES + ("authorize_grant",):
            found.append(f"callable {obj.__name__}")
    for cls in classes:
        if issubclass(cls, ISSUANCE_CLASSES):
            found.append(f"class {cls.__qualname__}")
        if cls.__module__.startswith("baec_app"):
            found += [f"class {cls.__qualname__}.{name}" for name in ISSUANCE_ATTRIBUTES if hasattr(cls, name)]
    return found


def test_the_server_reaches_one_executor_and_no_issuance_review_state_or_ai_object(world):
    with open_write_runtime(world.path, clock=world.clock) as runtime:
        assert issuance_capabilities(runtime) == [] and issuance_capabilities(runtime.server) == []
        objects, classes = _held(runtime)
        assert not [o for o in objects if isinstance(o, FORBIDDEN_TYPES)]
        assert not [c for c in classes if issubclass(c, FORBIDDEN_TYPES + ISSUANCE_CLASSES)]
        assert {f"{type(o).__module__}.{type(o).__qualname__}" for o in objects
                if type(o).__module__.startswith("baec_app")} == {
            "baec_app.mcp_write.composition.WriteRuntime",
            "baec_app.application.proposal_authorization.ConfirmationExecutionService",
            "baec_app.data.authorization_grants.GrantExecutionStore"}
        for module in ("baec_app.ai", "baec_app.interfaces", "baec_app.mcp.", "streamlit", "anthropic"):
            assert not [o for o in objects if type(o).__module__.startswith(module)], module
            assert not [c for c in classes if c.__module__.startswith(module)], module
        names = {getattr(o, "__name__", None) for o in objects if callable(o)}
        assert not names & FORBIDDEN_CALLABLES
        (executor,) = [o for o in objects if type(o) is ConfirmationExecutionService]
        # The executor's whole persistence surface: the execution-only grant capability on its writable connection.
        assert type(executor._grants) is GrantExecutionStore and not hasattr(executor._grants, "add_grant")
        assert {n for n in dir(executor._grants) if not n.startswith("_")} == {
            "get_grant", "lifecycle", "record_confirmation"}
        assert executor._clock is world.clock
        assert executor._connection.execute("PRAGMA query_only").fetchone()[0] == 0


def test_the_capability_walk_would_fail_if_the_executor_held_the_issuance_capable_store(world):
    """Negative control: restoring the 7F-B composition (executor holds AuthorizationGrantStore) is detected."""
    with open_write_runtime(world.path, clock=world.clock) as runtime:
        (executor,) = [o for o in _held(runtime)[0] if type(o) is ConfirmationExecutionService]
        assert issuance_capabilities(runtime) == []
        executor._grants = AuthorizationGrantStore(executor._connection)
        found = issuance_capabilities(runtime)
        assert "instance of AuthorizationGrantStore" in found and "AuthorizationGrantStore.add_grant" in found


@pytest.mark.parametrize("held", ["issuance service", "bound authorize_confirmation", "GrantRecord class",
                                  "a stored grant", "bound add_grant"])
def test_the_capability_walk_detects_every_other_issuance_path(world, held):
    """Negative controls for paths the type check alone would miss (a class, a bound method, a stored record)."""
    values = {
        "issuance service": lambda: world.auth,
        "bound authorize_confirmation": lambda: world.auth.authorize_confirmation,
        "GrantRecord class": lambda: GrantRecord,
        "a stored grant": lambda: world.grants.get_grant(world.grant().grant_id),
        "bound add_grant": lambda: world.grants.add_grant,
    }
    with open_write_runtime(world.path, clock=world.clock) as runtime:
        (executor,) = [o for o in _held(runtime)[0] if type(o) is ConfirmationExecutionService]
        executor.extra = values[held]()
        assert issuance_capabilities(runtime) != []


def test_the_walk_would_find_an_issuance_service_if_it_were_reachable(world):
    """Positive control for the isolation walk."""
    found = _reachable({"held": world.auth, "review": world.service})
    assert any(isinstance(o, ProposalAuthorizationService) for o in found)
    assert any(isinstance(o, ProposalReviewService) for o in found)


def test_main_composes_the_real_utc_clock_and_the_same_isolated_graph(world, monkeypatch):
    """The object-graph walk, repeated on the exact runtime main() hands to the stdio transport."""
    captured = []

    def fake_run(serve, runtime):
        captured.append(runtime)
        assert issuance_capabilities(runtime) == []
        reachable = _reachable(runtime.server)
        assert not [o for o in reachable if isinstance(o, FORBIDDEN_TYPES)]
        (executor,) = [o for o in reachable if type(o) is ConfirmationExecutionService]
        assert type(executor._clock) is SystemClock
        assert serve is write_main._serve

    monkeypatch.setattr(write_main.anyio, "run", fake_run)
    before = dump(world.connection)
    assert write_main.main(["--database", world.path]) == 0
    assert len(captured) == 1 and captured[0].closed
    assert dump(world.connection) == before


# --- import coupling, in fresh interpreters ---------------------------------------------------------------------------


def _loaded_after(statement: str) -> set[str]:
    code = f"import json, sys\n{statement}\nprint(json.dumps(sorted(sys.modules)))"
    completed = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60,
                               check=True)
    return set(json.loads(completed.stdout))


def test_importing_the_write_server_loads_no_model_provider_streamlit_interface_or_phase5_module():
    loaded = _loaded_after("import baec_app.mcp_write.__main__")
    executor_only = _loaded_after("import baec_app.application.proposal_authorization")
    assert not [m for m in loaded if _is_or_under(m, "baec_app.mcp")]
    for prefix in ("anthropic", "openai", "streamlit", "baec_app.interfaces", "baec_app.ai.anthropic_provider",
                   "baec_app.ai.provider", "baec_app.ai.service", "baec_app.ai.composition", "baec_app.ai.prompts"):
        assert not [m for m in loaded if _is_or_under(m, prefix)], prefix
    # The locked executor module loads deterministic verification helpers (the approved A9 exception); the write
    # package adds no AI module of its own.
    assert {m for m in loaded if _is_or_under(m, "baec_app.ai")} == {
        m for m in executor_only if _is_or_under(m, "baec_app.ai")}


def test_importing_the_phase5_server_loads_nothing_from_the_write_package():
    loaded = _loaded_after("import baec_app.mcp.__main__")
    assert not [m for m in loaded if _is_or_under(m, "baec_app.mcp_write")]


# --- Phase 5 preservation, side by side ---------------------------------------------------------------------------------


def test_phase5_stays_read_only_with_eight_resources_four_tools_and_no_prompts(tmp_path):
    path = seeded_database(tmp_path)

    async def main():
        async with connected(path) as (runtime, client):
            tools = (await client.list_tools()).tools
            resources = (await client.list_resources()).resources
            templates = (await client.list_resource_templates()).resource_templates
            prompts = (await client.list_prompts()).prompts
            connections = [o for o in _reachable(runtime.server) if type(o).__name__ == "Connection"]
            query_only = {c.execute("PRAGMA query_only").fetchone()[0] for c in connections}
            return client.server_info.name, tools, len(resources) + len(templates), prompts, query_only

    name, tools, resources, prompts, query_only = anyio.run(main)
    assert name == "baec-trigger-intelligence" != write_server.SERVER_NAME
    assert sorted(t.name for t in tools) == sorted(APPROVED_TOOLS) and resources == 8 and prompts == []
    assert all(t.annotations.read_only_hint is True for t in tools) and query_only == {1}
    assert "confirm_baec" not in {t.name for t in tools}


def test_the_phase5_package_names_nothing_from_phase7g():
    for path in sorted((REPO / "baec_app" / "mcp").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in ("mcp_write", "proposal_authorization", "ConfirmationExecutionService", "confirm_baec",
                     "grant_id", "query_only = OFF"):
            assert name not in text, (path.name, name)


# --- Streamlit cannot execute -------------------------------------------------------------------------------------------


def test_no_interface_module_imports_or_names_the_write_server_or_the_executor():
    for path in sorted((REPO / "baec_app" / "interfaces").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not [m for m in _imported_modules(tree) if _is_or_under(m, "baec_app.mcp_write")], path.name
        names = _identifiers(tree)
        assert not names & {"ConfirmationExecutionService", "build_write_server", "open_write_runtime", "execute",
                            "confirm_baec"}, path.name


# --- what one execution through MCP may write -------------------------------------------------------------------------


def test_an_execution_through_mcp_writes_only_the_confirmation_tables(world):
    from mcp import Client

    grant_id = world.grant().grant_id
    world.clock.advance(minutes=1)
    before = dump(world.connection)

    async def main():
        with open_write_runtime(world.path, clock=world.clock) as runtime:
            async with Client(runtime.server) as client:
                return await client.call_tool("confirm_baec", {"grant_id": grant_id})

    assert not anyio.run(main).is_error
    after = dump(world.connection)
    assert {t for t in DATA_TABLES if after[t] != before[t]} == {
        "baec_records", "human_authorizations", "interaction_evidence", "criterion_assessments", "criterion_evidence",
        "stringency_expressions", "ai_proposal_confirmations", "human_authorization_grant_consumptions"}
