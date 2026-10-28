"""Phase 5B–5D: static architecture rules for baec_app/mcp/** (design §12, M1–M10, plus the 5D stdio rules S1–S6).

Production MCP code only, including the stdio entry point __main__.py; tests are exempt. Every rule also runs against
synthetic violating and allowed snippets to prove it has teeth. Call-target
rules use exact names (or anchored prefixes for the forbidden families), never
substring matching, so preview_move_to_* is never mistaken for move_to_*.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import baec_app.application as application
from tests.test_application_boundary import _in_package, _resolve_relative, imports_package, scan

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "baec_app" / "mcp"

APPROVED_RESOURCE_URIS = {
    "baec://accounts",
    "baec://accounts/{account_id}",
    "baec://accounts/{account_id}/interactions",
    "baec://accounts/{account_id}/transition-history",
    "baec://interactions/{interaction_id}",
    "baec://baecs",
    "baec://baecs/{baec_id}",
    "baec://baecs/{baec_id}/dormancy-judgments",
}


def mcp_modules() -> list[tuple[str, str]]:
    modules = []
    for path in sorted(MCP_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.append((".".join(parts), path.read_text(encoding="utf-8")))
    return modules


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


# --- rules ------------------------------------------------------------------------------


def m1_no_data_layer(module: str, source: str) -> list[str]:
    return imports_package(scan(source, module), "baec_app.data")


def m2_application_package_api_only(module: str, source: str) -> list[str]:
    """Only `baec_app.application` itself, and only names in its __all__; never a submodule."""
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = _resolve_relative(module, False, node.level, node.module) if node.level else (node.module or "")
            if _in_package(target, "baec_app.application"):
                if target != "baec_app.application":
                    found.append(f"{module}:{node.lineno} imports submodule {target}")
                for alias in node.names:
                    if alias.name not in application.__all__:
                        found.append(f"{module}:{node.lineno} imports non-public {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("baec_app.application."):
                    found.append(f"{module}:{node.lineno} imports submodule {alias.name}")
    facts = scan(source, module)
    found += [
        f"{module}:{line} dynamically imports {name}"
        for name, line in facts.dynamic_imports
        if name.startswith("baec_app.application.")
    ]
    return found


M3_FORBIDDEN_NAMES = {
    "HumanCommandFacade", "HumanConfirmationGate", "HumanApproval", "HumanAuthorization", "ApprovalRequest",
    "ClassificationService", "DormancyJudgmentService", "AccountStateService", "build_command_facade",
    "Repository", "authorize", "_GATE_KEY", "ConfirmationProposal", "DormancyJudgmentProposal",
    "MoveToDormantProposal", "MoveToActiveProposal", "MoveToNoPlausiblePathProposal",
}


# Stored records returned by the reads (BAEC confirmations, judgment and transition
# authorizations) carry HumanAuthorization values. adapters.py may name the type
# only to serialize those stored values by exact type; it may never call or
# construct it (also enforced across production by the Phase 4 rule R1).
M3_TYPE_ONLY_EXCEPTIONS = {"baec_app.mcp.adapters": {"HumanAuthorization"}}


def m3_no_command_side_names(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    tokens = [(n, line) for n, line in facts.names] + [(a, line) for a, line in facts.attributes]
    tokens += [(name.rsplit(".", 1)[-1], line) for name, line in facts.imports]
    allowed = M3_TYPE_ONLY_EXCEPTIONS.get(module, set())
    found = [f"{module}:{line} references {name}" for name, line in tokens if name in M3_FORBIDDEN_NAMES - allowed]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            target = _call_name(node)
            arguments = [a.id for a in node.args if isinstance(a, ast.Name)]
            if target in allowed or (target == "__new__" and set(arguments) & allowed):
                found.append(f"{module}:{node.lineno} constructs {target}")
    return found


def m4_no_sqlite(module: str, source: str) -> list[str]:
    return imports_package(scan(source, module), "sqlite3")


MODEL_SDKS = ("anthropic", "openai", "claude_agent_sdk", "claude_code_sdk", "litellm", "langchain")


def m5_no_model_sdk(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = []
    for package in MODEL_SDKS:
        found += imports_package(facts, package)
    return found


M6_EXACT = {
    "request_from_proposal", "confirm_baec", "record_dormancy_judgment", "move_to_conditionally_dormant",
    "move_to_active_opportunity", "move_to_no_plausible_path", "approve", "redeem", "register",
}
M6_PREFIXES = ("propose_", "request_", "save_", "persist_", "add_")
M6_ALLOWED = {"preview_move_to_conditionally_dormant", "preview_move_to_active_opportunity", "preview_move_to_no_plausible_path"}
# argparse's ArgumentParser.add_argument matches the anchored add_ prefix but is not a repository write.
# It is allowed only in the entry point, and only on a name bound directly to argparse.ArgumentParser(...).
ARGPARSE_MODULE = "baec_app.mcp.__main__"


def _argument_parsers(tree: ast.AST) -> set[str]:
    """Names assigned directly from an argparse.ArgumentParser(...) call."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser" and isinstance(func.value, ast.Name) \
                    and func.value.id == "argparse":
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def m6_no_forbidden_calls(module: str, source: str) -> list[str]:
    found = []
    tree = ast.parse(source)
    parsers = _argument_parsers(tree) if module == ARGPARSE_MODULE else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name is None or name in M6_ALLOWED:
                continue
            if name == "add_argument" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id in parsers:
                continue
            if name in M6_EXACT or name.startswith(M6_PREFIXES):
                found.append(f"{module}:{node.lineno} calls {name}")
    return found


def _registration_decorators(function: ast.AST) -> list[ast.Call]:
    return [
        d for d in getattr(function, "decorator_list", [])
        if isinstance(d, ast.Call) and _call_name(d) in {"resource", "tool", "prompt"}
    ]


def m7_async_handlers_and_no_offloading(module: str, source: str) -> list[str]:
    found = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registration_decorators(node):
            if not isinstance(node, ast.AsyncFunctionDef):
                found.append(f"{module}:{node.lineno} handler {node.name} is not async")
        if isinstance(node, ast.Call) and _call_name(node) in {
            "to_thread", "run_in_executor", "run_sync", "ThreadPoolExecutor", "ProcessPoolExecutor", "Thread",
        }:
            found.append(f"{module}:{node.lineno} offloads with {_call_name(node)}")
    facts = scan(source, module)
    for package in ("concurrent.futures", "threading", "multiprocessing"):
        found += imports_package(facts, package)
    return found


def m8_literal_static_registration(module: str, source: str) -> list[str]:
    found = []
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in _registration_decorators(node):
                if not decorator.args or not (isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str)):
                    found.append(f"{module}:{decorator.lineno} registers a non-literal name")
                ancestor = parents.get(node)
                while ancestor is not None:
                    if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                        found.append(f"{module}:{decorator.lineno} registers inside a loop")
                        break
                    ancestor = parents.get(ancestor)
        if isinstance(node, ast.Call) and _call_name(node) in {"getattr", "setattr", "__import__", "eval", "exec", "import_module"}:
            found.append(f"{module}:{node.lineno} uses dynamic dispatch via {_call_name(node)}")
    return found


def registered(source: str, kind: str) -> list[str]:
    values = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in _registration_decorators(node):
                if _call_name(decorator) == kind and decorator.args and isinstance(decorator.args[0], ast.Constant):
                    values.append(decorator.args[0].value)
    return values


APPROVED_TOOL_NAMES = {
    "preview_baec_classification",
    "preview_move_to_conditionally_dormant",
    "preview_move_to_active_opportunity",
    "preview_move_to_no_plausible_path",
}


def _from_function_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "from_function"]


def tool_names(source: str) -> list[str]:
    """Literal names of tools built with Tool.from_function."""
    names = []
    for call in _from_function_calls(ast.parse(source)):
        for keyword in call.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                names.append(keyword.value.value)
    return names


def m7_tool_functions_are_async(module: str, source: str) -> list[str]:
    """Every function given to Tool.from_function is an async def named directly (no lambda or computed callable)."""
    tree = ast.parse(source)
    async_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    found = []
    for call in _from_function_calls(tree):
        target = call.args[0] if call.args else None
        if not isinstance(target, ast.Name) or target.id not in async_names:
            found.append(f"{module}:{call.lineno} tool function is not a directly named async def")
    return found


def m8_literal_tool_registration(module: str, source: str) -> list[str]:
    """Tool names are literal, and tools are not built inside loops or comprehensions."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for call in _from_function_calls(tree):
        names = [k for k in call.keywords if k.arg == "name"]
        if len(names) != 1 or not (isinstance(names[0].value, ast.Constant) and isinstance(names[0].value.value, str)):
            found.append(f"{module}:{call.lineno} tool name is not a literal")
        ancestor = parents.get(call)
        while ancestor is not None:
            if isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                found.append(f"{module}:{call.lineno} builds a tool inside a loop")
                break
            ancestor = parents.get(ancestor)
    return found


def m10_exact_facade_check_first(module: str, source: str) -> list[str]:
    """build_mcp_server's first statement (after its docstring) refuses anything but an exact ProposalFacade."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "build_mcp_server":
            body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            first = body[0] if body else None
            expected = "if type(proposal_facade) is not ProposalFacade:\n    raise TypeError"
            if not (isinstance(first, ast.If) and ast.unparse(first).startswith(expected)):
                found.append(f"{module}:{node.lineno} build_mcp_server does not begin with the exact ProposalFacade check")
    return found


# --- stdio rules (5D) ------------------------------------------------------------------------


def s1_stdout_is_reserved_for_the_protocol(module: str, source: str) -> list[str]:
    """No print, no reference to stdout, no argparse output that defaults to stdout."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if isinstance(node.func, ast.Name) and name in {"print", "pprint", "breakpoint"}:
                found.append(f"{module}:{node.lineno} calls {name}")
            if name in {"print_help", "print_usage"} and not node.args and not any(k.arg == "file" for k in node.keywords):
                found.append(f"{module}:{node.lineno} {name} writes to stdout by default")
            if name == "ArgumentParser" and not any(
                k.arg == "add_help" and isinstance(k.value, ast.Constant) and k.value.value is False for k in node.keywords
            ):
                found.append(f"{module}:{node.lineno} ArgumentParser's built-in help writes to stdout")
            for keyword in node.keywords:
                if keyword.arg == "action" and isinstance(keyword.value, ast.Constant) and keyword.value.value in {"help", "version"}:
                    found.append(f"{module}:{node.lineno} argparse action {keyword.value.value!r} writes to stdout")
        if isinstance(node, ast.Attribute) and node.attr in {"stdout", "__stdout__"}:
            found.append(f"{module}:{node.lineno} references {node.attr}")
        if isinstance(node, ast.Name) and node.id in {"stdout", "__stdout__"}:
            found.append(f"{module}:{node.lineno} references {node.id}")
        if isinstance(node, ast.ImportFrom) and node.module == "sys" and {a.name for a in node.names} & {"stdout", "__stdout__"}:
            found.append(f"{module}:{node.lineno} imports sys.stdout")
    found += imports_package(scan(source, module), "pprint")
    return found


S2_TRANSPORT_PACKAGES = ("mcp.server.sse", "mcp.server.streamable_http", "mcp.server.streamable_http_manager",
                         "starlette", "uvicorn", "fastapi", "sse_starlette")
S2_TRANSPORT_CALLS = {"run_sse_async", "run_streamable_http_async", "sse_app", "streamable_http_app",
                      "SseServerTransport", "StreamableHTTPSessionManager"}


def s2_stdio_is_the_only_transport(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = []
    for package in S2_TRANSPORT_PACKAGES:
        found += imports_package(facts, package)
    for node in ast.walk(ast.parse(source)):
        referenced = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
        if referenced in S2_TRANSPORT_CALLS:  # called or passed by reference
            found.append(f"{module}:{node.lineno} uses a non-stdio transport via {referenced}")
        if isinstance(node, ast.Call):
            if any(k.arg == "transport" for k in node.keywords):
                found.append(f"{module}:{node.lineno} selects a transport by argument")
        if isinstance(node, ast.Constant) and node.value in {"sse", "streamable-http"}:
            found.append(f"{module}:{node.lineno} names the {node.value!r} transport")
    return found


S3_NETWORK_PACKAGES = ("socket", "socketserver", "http.server", "http.client", "urllib.request", "ssl",
                       "requests", "httpx", "aiohttp", "websockets")
S3_NETWORK_CALLS = {"create_tcp_listener", "create_unix_listener", "create_udp_socket", "connect_tcp", "connect_unix",
                    "start_server", "start_unix_server", "create_server", "create_connection", "serve_forever",
                    "bind", "listen"}


def s3_no_network(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = []
    for package in S3_NETWORK_PACKAGES:
        found += imports_package(facts, package)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) in S3_NETWORK_CALLS:
            found.append(f"{module}:{node.lineno} opens a network endpoint with {_call_name(node)}")
    return found


def s4_no_dynamic_import_or_code_execution(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = []
    for package in ("importlib", "runpy", "imp", "pickle", "marshal"):
        found += imports_package(facts, package)
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        builtin = isinstance(node.func, ast.Name) and name in {"exec", "eval", "compile"}  # re.compile is not code execution
        if builtin or name in {"__import__", "import_module", "run_module", "run_path", "reload"}:
            found.append(f"{module}:{node.lineno} executes or imports dynamically via {name}")
    return found


S5_PROCESS_PACKAGES = ("subprocess", "pty", "pexpect", "sh", "shlex")
S5_PROCESS_CALLS = {"system", "popen", "Popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "startfile",
                    "create_subprocess_exec", "create_subprocess_shell", "open_process", "run_process",
                    "check_output", "check_call", "getoutput", "getstatusoutput"}


def s5_no_subprocess_or_shell(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = []
    for package in S5_PROCESS_PACKAGES:
        found += imports_package(facts, package)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in S5_PROCESS_CALLS or (name or "").startswith(("spawn", "execv", "execl")):
                found.append(f"{module}:{node.lineno} starts a process with {name}")
    return found


S6_WRITE_CALLS = {"write_text", "write_bytes", "touch", "mkdir", "makedirs", "rmdir", "removedirs", "unlink", "remove",
                  "rename", "renames", "truncate", "symlink", "symlink_to", "hardlink_to", "link", "chmod", "chown",
                  "copy", "copy2", "copyfile", "copytree", "move", "rmtree", "mkstemp", "mkdtemp",
                  "NamedTemporaryFile", "TemporaryFile", "TemporaryDirectory", "SpooledTemporaryFile"}


def _open_mode(node: ast.Call) -> object:
    if len(node.args) >= 2:
        return node.args[1].value if isinstance(node.args[1], ast.Constant) else None
    if isinstance(node.func, ast.Attribute) and node.args:  # Path.open(mode)
        return node.args[0].value if isinstance(node.args[0], ast.Constant) else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return keyword.value.value if isinstance(keyword.value, ast.Constant) else None
    return "r"


def s6_no_filesystem_writes(module: str, source: str) -> list[str]:
    facts = scan(source, module)
    found = imports_package(facts, "shutil") + imports_package(facts, "tempfile")
    found += [f"{module}:{line} writes a descriptor with {name}" for name, line in facts.calls if name in {"os.write", "os.writev"}]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in S6_WRITE_CALLS:
                found.append(f"{module}:{node.lineno} changes the filesystem with {name}")
            if name == "open":
                mode = _open_mode(node)
                if not isinstance(mode, str) or set(mode) & set("wax+"):
                    found.append(f"{module}:{node.lineno} opens a file for writing")
    return found


def _is_os_exit(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_exit" \
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"


def _statement_calls(statement: ast.stmt, test) -> bool:
    return isinstance(statement, ast.Expr) and test(statement.value)


def _is_runtime_close(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and not node.args and not node.keywords and isinstance(node.func, ast.Attribute) \
        and node.func.attr == "close" and isinstance(node.func.value, ast.Name) and node.func.value.id == "runtime"


def l1_signal_exit_closes_the_runtime_first(module: str, source: str) -> list[str]:
    """os._exit appears only on the signal path: inside `async for` over an open_signal_receiver(SIGTERM, SIGINT),
    as an unconditional statement of that loop body, preceded in the same body by an unconditional runtime.close()."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for call in [n for n in ast.walk(tree) if _is_os_exit(n)]:
        statement = parents.get(call)
        loop = parents.get(statement)
        receiver = parents.get(loop)
        on_signal_path = (
            isinstance(statement, ast.Expr) and isinstance(loop, ast.AsyncFor) and statement in loop.body
            and isinstance(receiver, ast.With)
            and any(isinstance(item.context_expr, ast.Call) and _call_name(item.context_expr) == "open_signal_receiver"
                    and isinstance(item.optional_vars, ast.Name) and isinstance(loop.iter, ast.Name)
                    and loop.iter.id == item.optional_vars.id
                    for item in receiver.items)
        )
        if not on_signal_path:
            found.append(f"{module}:{call.lineno} os._exit outside the signal receiver loop")
            continue
        index = loop.body.index(statement)
        if not any(_statement_calls(earlier, _is_runtime_close) for earlier in loop.body[:index]):
            found.append(f"{module}:{call.lineno} os._exit is not preceded by runtime.close() on the signal path")
    return found


RULES = {
    "M1 no data layer": m1_no_data_layer,
    "M2 application package API only": m2_application_package_api_only,
    "M3 no command-side names": m3_no_command_side_names,
    "M4 no sqlite3": m4_no_sqlite,
    "M5 no model SDK": m5_no_model_sdk,
    "M6 no forbidden calls": m6_no_forbidden_calls,
    "M7 async handlers, no offloading": m7_async_handlers_and_no_offloading,
    "M8 literal static registration": m8_literal_static_registration,
    "M7 tool functions are async": m7_tool_functions_are_async,
    "M8 literal tool registration": m8_literal_tool_registration,
    "M10 exact facade check first": m10_exact_facade_check_first,
    "S1 stdout reserved for the protocol": s1_stdout_is_reserved_for_the_protocol,
    "S2 stdio is the only transport": s2_stdio_is_the_only_transport,
    "S3 no network": s3_no_network,
    "S4 no dynamic import or code execution": s4_no_dynamic_import_or_code_execution,
    "S5 no subprocess or shell": s5_no_subprocess_or_shell,
    "S6 no filesystem writes": s6_no_filesystem_writes,
    "L1 signal exit closes the runtime first": l1_signal_exit_closes_the_runtime_first,
}


# --- production checks --------------------------------------------------------------------


def test_the_mcp_package_is_scanned():
    names = {module for module, _ in mcp_modules()}
    assert names == {"baec_app.mcp", "baec_app.mcp.contracts", "baec_app.mcp.adapters", "baec_app.mcp.resources",
                     "baec_app.mcp.server", "baec_app.mcp.composition", "baec_app.mcp.tools", "baec_app.mcp.__main__"}


def _calls_by_module(name: str) -> dict[str, int]:
    counts = {}
    for module, source in mcp_modules():
        count = sum(1 for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _call_name(n) == name)
        if count:
            counts[module] = count
    return counts


def test_composition_runs_only_through_open_mcp_runtime_and_the_sdk_stdio_transport():
    """open_read_connection -> build_proposal_facade -> build_mcp_server happens once, in composition.py;
    the entry point only opens the runtime and serves it over the SDK's stdio transport."""
    assert _calls_by_module("open_read_connection") == {"baec_app.mcp.composition": 1}
    assert _calls_by_module("build_proposal_facade") == {"baec_app.mcp.composition": 1}
    assert _calls_by_module("build_mcp_server") == {"baec_app.mcp.composition": 1}
    assert _calls_by_module("open_mcp_runtime") == {"baec_app.mcp.__main__": 1}
    assert _calls_by_module("run_stdio_async") == {"baec_app.mcp.__main__": 1}
    assert _calls_by_module("MCPServer") == {"baec_app.mcp.server": 1}
    assert _calls_by_module("stdio_server") == {}  # the SDK's own run_stdio_async wraps it


def test_the_signal_path_is_the_only_immediate_exit_and_it_closes_the_runtime_first():
    """Architecture contract (5D): explicit, deterministic cleanup before the signal exit. Exactly one os._exit
    exists in production MCP code, in the entry point's SIGTERM/SIGINT receiver, after runtime.close()."""
    exits = {module: sum(1 for n in ast.walk(ast.parse(source)) if _is_os_exit(n)) for module, source in mcp_modules()}
    assert {module: count for module, count in exits.items() if count} == {"baec_app.mcp.__main__": 1}
    source = (MCP_ROOT / "__main__.py").read_text(encoding="utf-8")
    assert l1_signal_exit_closes_the_runtime_first("baec_app.mcp.__main__", source) == []
    receivers = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _call_name(n) == "open_signal_receiver"]
    assert [sorted(ast.unparse(a) for a in r.args) for r in receivers] == [["signal.SIGINT", "signal.SIGTERM"]]


def test_the_one_server_is_built_with_the_tool_list_and_nothing_else_registers_capabilities():
    tree = ast.parse((MCP_ROOT / "server.py").read_text(encoding="utf-8"))
    (construction,) = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "MCPServer"]
    assert {k.arg for k in construction.keywords} == {"name", "title", "version", "tools"}
    assert ast.unparse(next(k.value for k in construction.keywords if k.arg == "tools")) == "build_tools(proposal_facade)"
    for module, source in mcp_modules():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                assert _call_name(node) not in {"add_tool", "add_resource", "add_prompt", "add_template", "prompt"}, module


@pytest.mark.parametrize("rule", RULES.values(), ids=RULES.keys())
def test_production_mcp_code_obeys_the_rule(rule):
    violations = [message for module, source in mcp_modules() for message in rule(module, source)]
    assert violations == []


def test_m9_exactly_the_eight_resources_and_no_tools_or_prompts_are_registered():
    resources, tools, prompts = [], [], []
    for _, source in mcp_modules():
        resources += registered(source, "resource")
        tools += registered(source, "tool")
        prompts += registered(source, "prompt")
    assert sorted(resources) == sorted(APPROVED_RESOURCE_URIS) and len(resources) == 8
    assert tools == [] and prompts == []


def test_m9_exactly_the_four_approved_tools_are_built_and_nothing_else_registers_tools():
    names = []
    for module, source in mcp_modules():
        names += tool_names(source)
        assert registered(source, "tool") == [], module  # no decorator-registered tools
    assert sorted(names) == sorted(APPROVED_TOOL_NAMES) and len(names) == 4
    tools_source = (MCP_ROOT / "tools.py").read_text(encoding="utf-8")
    assert len(_from_function_calls(ast.parse(tools_source))) == 4


def test_every_registered_handler_is_async_and_holds_no_thread_offloading():
    handlers = 0
    for module, source in mcp_modules():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registration_decorators(node):
                handlers += 1
                assert isinstance(node, ast.AsyncFunctionDef), (module, node.name)
    assert handlers == 8


def test_mcp_imports_the_six_read_types_only_from_the_application_package():
    tree = ast.parse((MCP_ROOT / "adapters.py").read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "baec_app.application" for alias in node.names
    }
    assert {"PersistedDormancyJudgment", "SourceInteraction", "TransitionHistoryEntry"} <= imported


# --- scanner self-tests ------------------------------------------------------------------

VIOLATIONS = {
    "M1 direct": ("baec_app.mcp.adapters", "from baec_app.data.records import SourceInteraction", m1_no_data_layer),
    "M1 dynamic": ("baec_app.mcp.adapters", "import importlib\nimportlib.import_module('baec_app.data.repository')", m1_no_data_layer),
    "M2 submodule": ("baec_app.mcp.resources", "from baec_app.application.facades import ReadService", m2_application_package_api_only),
    "M2 relative submodule": ("baec_app.mcp.resources", "from ..application.composition import build_proposal_facade", m2_application_package_api_only),
    "M2 non-public name": ("baec_app.mcp.server", "from baec_app.application import facades", m2_application_package_api_only),
    "M2 import submodule": ("baec_app.mcp.server", "import baec_app.application.approval", m2_application_package_api_only),
    "M3 command facade": ("baec_app.mcp.server", "from baec_app.application import HumanCommandFacade", m3_no_command_side_names),
    "M3 gate attribute": ("baec_app.mcp.resources", "x = runtime.gate.HumanConfirmationGate", m3_no_command_side_names),
    "M3 repository": ("baec_app.mcp.composition", "repo = Repository(conn)", m3_no_command_side_names),
    "M3 proposal type": ("baec_app.mcp.adapters", "p = MoveToActiveProposal(a, e, o)", m3_no_command_side_names),
    "M3 authorize": ("baec_app.mcp.adapters", "authority.authorize(r, a)", m3_no_command_side_names),
    "M3 adapters constructs HumanAuthorization": (
        "baec_app.mcp.adapters",
        "from baec_app.domain.models import HumanAuthorization\nHumanAuthorization('u', t, a, 's')",
        m3_no_command_side_names,
    ),
    "M3 adapters HumanAuthorization.__new__": (
        "baec_app.mcp.adapters",
        "from baec_app.domain.models import HumanAuthorization\nobject.__new__(HumanAuthorization)",
        m3_no_command_side_names,
    ),
    "M3 resources names HumanAuthorization": (
        "baec_app.mcp.resources",
        "from baec_app.domain.models import HumanAuthorization",
        m3_no_command_side_names,
    ),
    "M3 adapters names HumanApproval": ("baec_app.mcp.adapters", "from baec_app.application import HumanApproval", m3_no_command_side_names),
    "M4 sqlite3": ("baec_app.mcp.composition", "import sqlite3", m4_no_sqlite),
    "M5 anthropic": ("baec_app.mcp.server", "import anthropic", m5_no_model_sdk),
    "M5 openai dynamic": ("baec_app.mcp.server", "import importlib\nimportlib.import_module('openai')", m5_no_model_sdk),
    "M6 propose": ("baec_app.mcp.resources", "facade.propose_confirmation(c, captured_at=t)", m6_no_forbidden_calls),
    "M6 request_from_proposal": ("baec_app.mcp.resources", "f.request_from_proposal(s, p)", m6_no_forbidden_calls),
    "M6 move_to command": ("baec_app.mcp.resources", "f.move_to_active_opportunity(approval)", m6_no_forbidden_calls),
    "M6 approve": ("baec_app.mcp.resources", "gate.approve(s, r, displayed_digest=d)", m6_no_forbidden_calls),
    "M6 register": ("baec_app.mcp.resources", "gate.register(s, r)", m6_no_forbidden_calls),
    "M6 save": ("baec_app.mcp.resources", "repo.save_confirmed_baec(r)", m6_no_forbidden_calls),
    "M6 add": ("baec_app.mcp.server", "server.add_tool(fn)", m6_no_forbidden_calls),
    "M7 sync handler": ("baec_app.mcp.resources", "@server.resource('baec://x')\ndef x():\n    return ''", m7_async_handlers_and_no_offloading),
    "M7 to_thread": ("baec_app.mcp.resources", "import asyncio\nawait_ = asyncio.to_thread(f)", m7_async_handlers_and_no_offloading),
    "M7 run_in_executor": ("baec_app.mcp.resources", "loop.run_in_executor(None, f)", m7_async_handlers_and_no_offloading),
    "M7 anyio run_sync": ("baec_app.mcp.resources", "anyio.to_thread.run_sync(f)", m7_async_handlers_and_no_offloading),
    "M7 thread pool import": ("baec_app.mcp.resources", "from concurrent.futures import ThreadPoolExecutor", m7_async_handlers_and_no_offloading),
    "M8 computed name": ("baec_app.mcp.resources", "@server.resource(uri)\nasync def x():\n    return ''", m8_literal_static_registration),
    "M8 registration in loop": (
        "baec_app.mcp.resources",
        "for uri in uris:\n    @server.resource('baec://x')\n    async def x():\n        return ''",
        m8_literal_static_registration,
    ),
    "M8 getattr dispatch": ("baec_app.mcp.resources", "handler = getattr(facade.reads, name)", m8_literal_static_registration),
    "M7 sync tool function": (
        "baec_app.mcp.tools",
        "def preview(x):\n    return x\nTool.from_function(preview, name='preview_x')",
        m7_tool_functions_are_async,
    ),
    "M7 lambda tool function": ("baec_app.mcp.tools", "Tool.from_function(lambda x: x, name='preview_x')", m7_tool_functions_are_async),
    "M7 computed tool function": ("baec_app.mcp.tools", "Tool.from_function(handlers[name], name='preview_x')", m7_tool_functions_are_async),
    "M8 computed tool name": (
        "baec_app.mcp.tools",
        "async def p(x):\n    return x\nTool.from_function(p, name=stored_text)",
        m8_literal_tool_registration,
    ),
    "M8 tool built in a loop": (
        "baec_app.mcp.tools",
        "async def p(x):\n    return x\ntools = [Tool.from_function(p, name='preview_x') for _ in range(2)]",
        m8_literal_tool_registration,
    ),
    "M8 tool name missing": ("baec_app.mcp.tools", "async def p(x):\n    return x\nTool.from_function(p)", m8_literal_tool_registration),
    "M6 propose from a tool": ("baec_app.mcp.tools", "facade.propose_move_to_active_opportunity(a, evaluation_evidence=e)", m6_no_forbidden_calls),
    "M3 proposal type in tools": ("baec_app.mcp.tools", "from baec_app.application import MoveToActiveProposal", m3_no_command_side_names),
    # 5D: the entry point and the stdio rules
    "M1 entry point opens the data layer": ("baec_app.mcp.__main__", "from baec_app.data.database import open_database", m1_no_data_layer),
    "M2 entry point application submodule": ("baec_app.mcp.__main__", "from baec_app.application.composition import open_read_connection", m2_application_package_api_only),
    "M3 entry point builds a command facade": ("baec_app.mcp.__main__", "from baec_app.application import build_command_facade", m3_no_command_side_names),
    "M3 entry point wraps a Repository": ("baec_app.mcp.__main__", "runtime = Repository(open_database(path))", m3_no_command_side_names),
    "M4 entry point opens sqlite": ("baec_app.mcp.__main__", "import sqlite3\nsqlite3.connect(path, check_same_thread=False)", m4_no_sqlite),
    "M5 entry point model SDK": ("baec_app.mcp.__main__", "from anthropic import Anthropic", m5_no_model_sdk),
    "M6 add_argument outside the entry point": ("baec_app.mcp.resources", "parser.add_argument('--x')", m6_no_forbidden_calls),
    "M6 entry point other add_": ("baec_app.mcp.__main__", "repository.add_account(a)", m6_no_forbidden_calls),
    "M6 add_argument on a non-parser in the entry point": ("baec_app.mcp.__main__", "repository.add_argument(a)", m6_no_forbidden_calls),
    "M6 add_argument on a parser from elsewhere": (
        "baec_app.mcp.__main__", "parser = factory.ArgumentParser()\nparser.add_argument('--x')", m6_no_forbidden_calls,
    ),
    "M6 bare add_argument": ("baec_app.mcp.__main__", "add_argument('--x')", m6_no_forbidden_calls),
    "M6 entry point request": ("baec_app.mcp.__main__", "facade.request_move_to_active_opportunity(s, a, evaluation_evidence=e)", m6_no_forbidden_calls),
    "M7 entry point thread": ("baec_app.mcp.__main__", "import threading\nthreading.Thread(target=serve).start()", m7_async_handlers_and_no_offloading),
    "M7 entry point to_thread": ("baec_app.mcp.__main__", "await anyio.to_thread.run_sync(runtime.server.run)", m7_async_handlers_and_no_offloading),
    "M8 entry point getattr dispatch": ("baec_app.mcp.__main__", "handler = getattr(server, stored_text)", m8_literal_static_registration),
    "M10 check removed": ("baec_app.mcp.server", "def build_mcp_server(proposal_facade):\n    return MCPServer(name='x')", m10_exact_facade_check_first),
    "M10 isinstance instead of exact type": (
        "baec_app.mcp.server",
        "def build_mcp_server(proposal_facade):\n    if not isinstance(proposal_facade, ProposalFacade):\n        raise TypeError('x')",
        m10_exact_facade_check_first,
    ),
    "M10 check not first": (
        "baec_app.mcp.server",
        "def build_mcp_server(proposal_facade):\n    server = MCPServer(name='x')\n"
        "    if type(proposal_facade) is not ProposalFacade:\n        raise TypeError('x')",
        m10_exact_facade_check_first,
    ),
    "L1 close removed": ("baec_app.mcp.__main__", "with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:\n    async for signum in signals:\n        _logger.info('x')\n        os._exit(128 + signum)",
                         l1_signal_exit_closes_the_runtime_first),
    "L1 close after exit": ("baec_app.mcp.__main__", "with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:\n    async for signum in signals:\n        os._exit(128 + signum)\n        runtime.close()",
                            l1_signal_exit_closes_the_runtime_first),
    "L1 close only conditionally": (
        "baec_app.mcp.__main__", "with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:\n    async for signum in signals:\n        if signum == 15:\n            runtime.close()\n        os._exit(128 + signum)",
        l1_signal_exit_closes_the_runtime_first,
    ),
    "L1 exit on normal shutdown": ("baec_app.mcp.__main__", "runtime.close()\nos._exit(0)", l1_signal_exit_closes_the_runtime_first),
    "L1 exit on startup failure": (
        "baec_app.mcp.__main__", "try:\n    runtime = open_mcp_runtime(p)\nexcept ApplicationError:\n    os._exit(1)",
        l1_signal_exit_closes_the_runtime_first,
    ),
    "L1 exit in a loop that is not the signal receiver": (
        "baec_app.mcp.__main__", "async for message in stream:\n    runtime.close()\n    os._exit(1)",
        l1_signal_exit_closes_the_runtime_first,
    ),
    "S1 print": ("baec_app.mcp.__main__", "print('serving')", s1_stdout_is_reserved_for_the_protocol),
    "S1 print to stderr is still print": ("baec_app.mcp.__main__", "print('serving', file=sys.stderr)", s1_stdout_is_reserved_for_the_protocol),
    "S1 logging to stdout": ("baec_app.mcp.__main__", "logging.basicConfig(stream=sys.stdout)", s1_stdout_is_reserved_for_the_protocol),
    "S1 stream handler on stdout": ("baec_app.mcp.tools", "logging.StreamHandler(sys.__stdout__)", s1_stdout_is_reserved_for_the_protocol),
    "S1 sys.stdout.write": ("baec_app.mcp.resources", "sys.stdout.write('debug')", s1_stdout_is_reserved_for_the_protocol),
    "S1 from sys import stdout": ("baec_app.mcp.__main__", "from sys import stdout", s1_stdout_is_reserved_for_the_protocol),
    "S1 default argparse help": ("baec_app.mcp.__main__", "argparse.ArgumentParser(prog='x')", s1_stdout_is_reserved_for_the_protocol),
    "S1 print_help to stdout": ("baec_app.mcp.__main__", "parser.print_help()", s1_stdout_is_reserved_for_the_protocol),
    "S1 version action": ("baec_app.mcp.__main__", "parser.add_argument('--version', action='version', version='1')", s1_stdout_is_reserved_for_the_protocol),
    "S1 pprint": ("baec_app.mcp.adapters", "from pprint import pprint", s1_stdout_is_reserved_for_the_protocol),
    "S1 breakpoint": ("baec_app.mcp.tools", "breakpoint()", s1_stdout_is_reserved_for_the_protocol),
    "S2 run with transport": ("baec_app.mcp.__main__", "runtime.server.run(transport='streamable-http')", s2_stdio_is_the_only_transport),
    "S2 sse app": ("baec_app.mcp.__main__", "app = server.sse_app()", s2_stdio_is_the_only_transport),
    "S2 streamable http async": ("baec_app.mcp.__main__", "anyio.run(server.run_streamable_http_async)", s2_stdio_is_the_only_transport),
    "S2 starlette import": ("baec_app.mcp.__main__", "from starlette.applications import Starlette", s2_stdio_is_the_only_transport),
    "S2 sdk sse module": ("baec_app.mcp.server", "from mcp.server.sse import SseServerTransport", s2_stdio_is_the_only_transport),
    "S2 uvicorn": ("baec_app.mcp.__main__", "import uvicorn\nuvicorn.run(app)", s2_stdio_is_the_only_transport),
    "S3 socket": ("baec_app.mcp.__main__", "import socket", s3_no_network),
    "S3 tcp listener": ("baec_app.mcp.__main__", "listener = await anyio.create_tcp_listener(local_port=8000)", s3_no_network),
    "S3 asyncio server": ("baec_app.mcp.__main__", "await asyncio.start_server(handle, '0.0.0.0', 8000)", s3_no_network),
    "S3 http server": ("baec_app.mcp.__main__", "from http.server import HTTPServer", s3_no_network),
    "S3 outbound http": ("baec_app.mcp.tools", "import httpx", s3_no_network),
    "S4 importlib": ("baec_app.mcp.__main__", "import importlib\nimportlib.import_module(name)", s4_no_dynamic_import_or_code_execution),
    "S4 dunder import": ("baec_app.mcp.tools", "__import__(stored_text)", s4_no_dynamic_import_or_code_execution),
    "S4 eval": ("baec_app.mcp.tools", "eval(buyer_text)", s4_no_dynamic_import_or_code_execution),
    "S4 exec": ("baec_app.mcp.resources", "exec(code)", s4_no_dynamic_import_or_code_execution),
    "S4 runpy": ("baec_app.mcp.__main__", "import runpy\nrunpy.run_module('x')", s4_no_dynamic_import_or_code_execution),
    "S5 subprocess": ("baec_app.mcp.__main__", "import subprocess\nsubprocess.run(['sh'])", s5_no_subprocess_or_shell),
    "S5 os.system": ("baec_app.mcp.tools", "os.system(command)", s5_no_subprocess_or_shell),
    "S5 os.popen": ("baec_app.mcp.tools", "os.popen(command)", s5_no_subprocess_or_shell),
    "S5 exec family": ("baec_app.mcp.__main__", "os.execvp('sh', ['sh'])", s5_no_subprocess_or_shell),
    "S5 spawn family": ("baec_app.mcp.__main__", "os.spawnlp(os.P_NOWAIT, 'sh', 'sh')", s5_no_subprocess_or_shell),
    "S5 anyio process": ("baec_app.mcp.__main__", "await anyio.run_process(['sh'])", s5_no_subprocess_or_shell),
    "S5 asyncio shell": ("baec_app.mcp.__main__", "await asyncio.create_subprocess_shell(command)", s5_no_subprocess_or_shell),
    "S5 fork": ("baec_app.mcp.__main__", "pid = os.fork()", s5_no_subprocess_or_shell),
    "S6 open for writing": ("baec_app.mcp.__main__", "open(path, 'w')", s6_no_filesystem_writes),
    "S6 open for appending by keyword": ("baec_app.mcp.__main__", "open(path, mode='a')", s6_no_filesystem_writes),
    "S6 open with computed mode": ("baec_app.mcp.__main__", "open(path, mode)", s6_no_filesystem_writes),
    "S6 path open for writing": ("baec_app.mcp.__main__", "Path(p).open('wb')", s6_no_filesystem_writes),
    "S6 write_text": ("baec_app.mcp.__main__", "Path(p).write_text(x)", s6_no_filesystem_writes),
    "S6 touch": ("baec_app.mcp.composition", "Path(p).touch()", s6_no_filesystem_writes),
    "S6 os.write": ("baec_app.mcp.__main__", "import os\nos.write(1, b'x')", s6_no_filesystem_writes),
    "S6 unlink": ("baec_app.mcp.__main__", "os.unlink(path)", s6_no_filesystem_writes),
    "S6 tempfile": ("baec_app.mcp.composition", "import tempfile", s6_no_filesystem_writes),
    "S6 shutil": ("baec_app.mcp.composition", "import shutil\nshutil.copy(a, b)", s6_no_filesystem_writes),
}


@pytest.mark.parametrize("case", VIOLATIONS.values(), ids=VIOLATIONS.keys())
def test_rules_report_synthetic_violations(case):
    module, source, rule = case
    assert rule(module, source) != []


ALLOWED = {
    "M2 package API": ("baec_app.mcp.adapters", "from baec_app.application import SourceInteraction, ProposalFacade", m2_application_package_api_only),
    "M3 docstring mentions command facade": ("baec_app.mcp.server", '"""Never holds a HumanCommandFacade or Repository."""', m3_no_command_side_names),
    "M3 adapters serializes stored authorizations by type": (
        "baec_app.mcp.adapters",
        "from baec_app.domain.models import HumanAuthorization\n_require(value, HumanAuthorization)",
        m3_no_command_side_names,
    ),
    "M6 preview_move_to calls": (
        "baec_app.mcp.tools",
        "facade.preview_move_to_conditionally_dormant(a, baec_id=b, judgment_id=1)\n"
        "facade.preview_move_to_active_opportunity(a, evaluation_evidence=e)\n"
        "facade.preview_move_to_no_plausible_path(a, ground=g, reason=r)",
        m6_no_forbidden_calls,
    ),
    "M6 register_resources is not register": ("baec_app.mcp.server", "register_resources(server, facade)", m6_no_forbidden_calls),
    "M6 reads": ("baec_app.mcp.resources", "facade.reads.list_accounts()", m6_no_forbidden_calls),
    "M7 async handler": ("baec_app.mcp.resources", "@server.resource('baec://x')\nasync def x():\n    return ''", m7_async_handlers_and_no_offloading),
    "M8 literal registration": ("baec_app.mcp.resources", "@server.resource('baec://x')\nasync def x():\n    return ''", m8_literal_static_registration),
    "M7 async tool function": (
        "baec_app.mcp.tools",
        "async def preview(x):\n    return x\nTool.from_function(preview, name='preview_x')",
        m7_tool_functions_are_async,
    ),
    "M8 literal tool name": (
        "baec_app.mcp.tools",
        "async def p(x):\n    return x\nt = Tool.from_function(p, name='preview_x')",
        m8_literal_tool_registration,
    ),
    # 5D
    "M6 entry point ArgumentParser.add_argument": (
        "baec_app.mcp.__main__",
        "parser = argparse.ArgumentParser(add_help=False)\nparser.add_argument('--database', required=True)",
        m6_no_forbidden_calls,
    ),
    "M10 exact check after docstring": (
        "baec_app.mcp.server",
        'def build_mcp_server(proposal_facade):\n    """Doc."""\n    if type(proposal_facade) is not ProposalFacade:\n'
        "        raise TypeError('requires a ProposalFacade')\n    return MCPServer(name='x')",
        m10_exact_facade_check_first,
    ),
    "L1 close then exit on the signal path": (
        "baec_app.mcp.__main__", "with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:\n    async for signum in signals:\n        runtime.close()\n        _logger.info('x')\n        os._exit(128 + signum)",
        l1_signal_exit_closes_the_runtime_first,
    ),
    "S1 help on stderr": (
        "baec_app.mcp.__main__",
        "parser = argparse.ArgumentParser(prog='x', add_help=False)\nparser.print_help(sys.stderr)",
        s1_stdout_is_reserved_for_the_protocol,
    ),
    "S1 logging to stderr": ("baec_app.mcp.__main__", "logging.basicConfig(stream=sys.stderr, level=logging.INFO)", s1_stdout_is_reserved_for_the_protocol),
    "S1 docstring mentions stdout": ("baec_app.mcp.__main__", '"""stdout carries only protocol messages; print nothing."""', s1_stdout_is_reserved_for_the_protocol),
    "S2 sdk stdio transport": ("baec_app.mcp.__main__", "await runtime.server.run_stdio_async()\nanyio.run(_serve, runtime)", s2_stdio_is_the_only_transport),
    "S3 signal receiver": ("baec_app.mcp.__main__", "with anyio.open_signal_receiver(signal.SIGTERM) as signals:\n    pass", s3_no_network),
    "S4 regex compile": ("baec_app.mcp.contracts", "import re\nPATTERN = re.compile(r'^x$')", s4_no_dynamic_import_or_code_execution),
    "S5 immediate exit": ("baec_app.mcp.__main__", "os._exit(143)", s5_no_subprocess_or_shell),
    "S6 read-only open": ("baec_app.mcp.__main__", "open(path)\nopen(path, 'rb')\nopen(path, mode='r')", s6_no_filesystem_writes),
    "S6 str.replace and model_copy": ("baec_app.mcp.tools", "x.replace('a', 'b')\ntool.model_copy(update={})", s6_no_filesystem_writes),
}


@pytest.mark.parametrize("case", ALLOWED.values(), ids=ALLOWED.keys())
def test_rules_allow_legitimate_code(case):
    module, source, rule = case
    assert rule(module, source) == []
