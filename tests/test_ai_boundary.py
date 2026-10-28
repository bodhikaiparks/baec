"""Phase 6C-B: static architecture rules A1-A10 for production baec_app/ai/** (tests exempt).

Each rule runs over the real package and against synthetic snippets it must report
and must allow. Call-target checks use exact names (or anchored prefixes), never
substring matching.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_application_boundary import imports_package, scan
from tests.test_mcp_boundary import _call_name, m2_application_package_api_only

REPO_ROOT = Path(__file__).resolve().parents[1]
AI_ROOT = REPO_ROOT / "baec_app" / "ai"
AI_MODULES = {"baec_app.ai", "baec_app.ai.contracts", "baec_app.ai.canonical", "baec_app.ai.prompts", "baec_app.ai.grounding",
              "baec_app.ai.provider", "baec_app.ai.provenance", "baec_app.ai.validation", "baec_app.ai.service",
              "baec_app.ai.anthropic_provider", "baec_app.ai.composition",
              "baec_app.ai.verification"}  # Phase 7D: the pure verification façade
COMPOSITION = "baec_app.ai.composition"
PROVIDER = "baec_app.ai.anthropic_provider"
PURE = {"baec_app.ai.service", "baec_app.ai.provider", "baec_app.ai.validation", "baec_app.ai.contracts",
        "baec_app.ai.prompts", "baec_app.ai.canonical", "baec_app.ai.provenance", "baec_app.ai.grounding",
        "baec_app.ai.verification"}


def _modules(root: Path) -> list[tuple[str, str]]:
    found = []
    for path in sorted(root.rglob("*.py")):
        parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.append((".".join(parts), path.read_text(encoding="utf-8")))
    return found


def ai_modules():
    return _modules(AI_ROOT)


def _imported(module: str, source: str) -> list[tuple[str, int]]:
    return scan(source, module).imports + scan(source, module).dynamic_imports


def _keywords(tree):
    return [(k.arg, node.lineno) for node in ast.walk(tree) if isinstance(node, ast.Call) for k in node.keywords]


# --- rules -----------------------------------------------------------------------------------


def a1_application_package_api_only(module, source):
    return m2_application_package_api_only(module, source)


A2_NAMES = {"HumanCommandFacade", "HumanConfirmationGate", "HumanApproval", "HumanAuthorization", "ApprovalRequest",
            "build_command_facade", "ProposalFacade", "ProposalOrigin", "ClassificationService",
            "DormancyJudgmentService", "AccountStateService", "_GATE_KEY", "ConfirmationProposal",
            "DormancyJudgmentProposal", "MoveToDormantProposal", "MoveToActiveProposal", "MoveToNoPlausiblePathProposal"}
A2_CALLS = {"request_from_proposal", "confirm_baec", "record_dormancy_judgment", "move_to_conditionally_dormant",
            "move_to_active_opportunity", "move_to_no_plausible_path", "approve", "redeem", "authorize", "register"}


def a2_no_command_side(module, source):
    facts = scan(source, module)
    tokens = facts.names + facts.attributes + [(name.rsplit(".", 1)[-1], line) for name, line in facts.imports]
    found = [f"{module}:{line} references {name}" for name, line in tokens if name in A2_NAMES]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node) or ""
            if name in A2_CALLS or name.startswith(("propose_", "request_")):
                found.append(f"{module}:{node.lineno} calls {name}")
    return found


A3_CONSTRUCTORS = {"BaecCandidate", "CriterionAssessment", "EvidenceExcerpt", "BaecRecord", "AiDerivedText",
                   "EvaluationEvidence", "NonEvaluationEvidence", "StringencyExpression", "DormancyJudgment", "Account"}


def a3_no_domain(module, source):
    found = imports_package(scan(source, module), "baec_app.domain")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) in A3_CONSTRUCTORS:
            found.append(f"{module}:{node.lineno} constructs {_call_name(node)}")
    return found


def a4_data_only_from_composition(module, source):
    found = []
    for name, line in _imported(module, source):
        if name == "sqlite3" or name.startswith("sqlite3."):
            found.append(f"{module}:{line} imports sqlite3")
        if name == "baec_app.data" or name.startswith("baec_app.data."):
            if module != COMPOSITION or not (name == "baec_app.data.ai_provenance" or name.startswith("baec_app.data.ai_provenance.")):
                found.append(f"{module}:{line} imports {name}")
    facts = scan(source, module)
    for name, line in facts.names + facts.attributes:
        if name in {"Repository", "open_database", "connect"}:
            found.append(f"{module}:{line} references {name}")
    return found


def a5_anthropic_only_in_the_provider(module, source):
    found = []
    for name, line in _imported(module, source):
        if (name == "anthropic" or name.startswith("anthropic.")) and module != PROVIDER:
            found.append(f"{module}:{line} imports {name}")
        if name.startswith(PROVIDER) and module != COMPOSITION:
            found.append(f"{module}:{line} imports the Anthropic provider")
    return found


def a6_pure_modules_import_no_concrete_adapter(module, source):
    if module not in PURE:
        return []
    found = []
    for name, line in _imported(module, source):
        if name.startswith((COMPOSITION, PROVIDER, "baec_app.data", "anthropic", "httpx2", "sqlite3")):
            found.append(f"{module}:{line} imports {name}")
    return found


def a7_no_model_tools(module, source):
    tree = ast.parse(source)
    found = [f"{module}:{line} passes {arg}=" for arg, line in _keywords(tree) if arg in {"tools", "tool_choice", "mcp_servers"}]
    facts = scan(source, module)
    found += [f"{module}:{line} references {n}" for n, line in facts.names + facts.attributes
              if n in {"tool_choice", "MCPServer", "ToolParam", "tools"}]
    return found


def a8_no_secrets_or_logging(module, source):
    tree = ast.parse(source)
    found = [f"{module}:{line} passes api_key=" for arg, line in _keywords(tree) if arg in {"api_key", "auth_token"}]
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "sk-ant" in node.value:
            found.append(f"{module}:{node.lineno} contains a key-like literal")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            found.append(f"{module}:{node.lineno} prints")
    found += imports_package(scan(source, module), "logging")
    return found


def a9_no_mcp_link(module, source):
    found = []
    for name, line in _imported(module, source):
        if name in ("mcp", "baec_app.mcp") or name.startswith(("mcp.", "baec_app.mcp.")):
            found.append(f"{module}:{line} imports {name}")
    return found


# The named exceptions to A9, each one application module to exactly one pure AI module (and names from it).
# Phase 7D: the mapper may use the verification façade. Phase 7F-A: the human-normalization contract may use
# the grounding primitives. Every other lower-layer import of baec_app.ai stays forbidden.
A9_VERIFICATION_EXCEPTION = ("baec_app.application.ai_proposal_mapping", "baec_app.ai.verification")
A9_GROUNDING_EXCEPTION = ("baec_app.application.human_normalization", "baec_app.ai.grounding")
A9_EXCEPTIONS = dict((A9_VERIFICATION_EXCEPTION, A9_GROUNDING_EXCEPTION))
# Public demo release: the one manual, paid, one-shot recording utility runs the unchanged Phase 6 extraction through
# its composition and the production provider. Exactly this script and exactly these two modules.
A9_RECORDER_EXCEPTION = ("scripts.record_public_demo_artifact",
                         ("baec_app.ai.composition", "baec_app.ai.anthropic_provider"))


def _a9_excepted(module, name):
    if module == A9_RECORDER_EXCEPTION[0]:
        return any(name == m or name.startswith(m + ".") for m in A9_RECORDER_EXCEPTION[1])  # these two only
    allowed = A9_EXCEPTIONS.get(module)
    return allowed is not None and (name == allowed or name.startswith(allowed + "."))


def lower_layers_never_import_ai(module, source):
    return [f"{module}:{line} imports {name}" for name, line in _imported(module, source)
            if (name == "baec_app.ai" or name.startswith("baec_app.ai.")) and not _a9_excepted(module, name)]


A10_PACKAGES = ("importlib", "runpy", "subprocess", "pty", "socket", "requests", "urllib", "http", "aiohttp", "httpx",
                "websockets", "multiprocessing", "threading")
A10_CALLS = {"__import__", "import_module", "system", "popen", "Popen", "fork", "create_subprocess_exec",
             "create_subprocess_shell", "run_process", "open_process"}
HTTPX2_ALLOWED_ATTRIBUTES = {"ConnectError"}


def a10_no_dynamic_code_processes_or_raw_network(module, source):
    tree = ast.parse(source)
    facts = scan(source, module)
    found = []
    for package in A10_PACKAGES:
        found += imports_package(facts, package)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in A10_CALLS or (isinstance(node.func, ast.Name) and name in {"eval", "exec", "compile"}):
                found.append(f"{module}:{node.lineno} calls {name}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "httpx2" \
                and node.attr not in HTTPX2_ALLOWED_ATTRIBUTES:
            found.append(f"{module}:{node.lineno} uses httpx2.{node.attr}")
    for name, line in _imported(module, source):
        if (name == "httpx2" or name.startswith("httpx2.")) and module != PROVIDER:
            found.append(f"{module}:{line} imports httpx2")
        if name.startswith("httpx2."):
            found.append(f"{module}:{line} imports from httpx2 directly")
    return found


RULES = {
    "A1 application package API only": a1_application_package_api_only,
    "A2 no command side": a2_no_command_side,
    "A3 no domain": a3_no_domain,
    "A4 data only from composition": a4_data_only_from_composition,
    "A5 anthropic only in the provider": a5_anthropic_only_in_the_provider,
    "A6 pure modules import no concrete adapter": a6_pure_modules_import_no_concrete_adapter,
    "A7 no model tools": a7_no_model_tools,
    "A8 no secrets or logging": a8_no_secrets_or_logging,
    "A9 no MCP link": a9_no_mcp_link,
    "A10 no dynamic code, processes, or raw network": a10_no_dynamic_code_processes_or_raw_network,
}


# --- production -----------------------------------------------------------------------------------


def test_the_ai_package_is_exactly_the_approved_modules():
    assert {module for module, _ in ai_modules()} == AI_MODULES


@pytest.mark.parametrize("rule", RULES.values(), ids=RULES.keys())
def test_production_ai_code_obeys_the_rule(rule):
    assert [message for module, source in ai_modules() for message in rule(module, source)] == []


def test_no_lower_layer_mcp_or_script_imports_the_ai_package():
    """A9, with exactly two named exceptions: application.ai_proposal_mapping -> ai.verification (Phase 7D)
    and application.human_normalization -> ai.grounding (Phase 7F-A); plus the one manual public-demo recording
    utility, scripts.record_public_demo_artifact -> ai.composition and ai.anthropic_provider only."""
    others = (_modules(REPO_ROOT / "baec_app" / "domain") + _modules(REPO_ROOT / "baec_app" / "data")
              + _modules(REPO_ROOT / "baec_app" / "application") + _modules(REPO_ROOT / "baec_app" / "mcp")
              + _modules(REPO_ROOT / "scripts"))
    assert others and [m for module, source in others for m in lower_layers_never_import_ai(module, source)] == []
    importers = {module for module, source in others
                 for name, _ in _imported(module, source) if name.startswith("baec_app.ai")}
    assert importers == set(A9_EXCEPTIONS) | {A9_RECORDER_EXCEPTION[0]}  # exactly these reach the AI package


def test_the_package_root_does_not_re_export_the_provider():
    assert ast.parse((AI_ROOT / "__init__.py").read_text(encoding="utf-8")).body[1:] == []


# --- scanner self-tests ------------------------------------------------------------------------------

S, C, P = "baec_app.ai.service", COMPOSITION, PROVIDER
VIOLATIONS = {
    "A1 application submodule": (S, "from baec_app.application.facades import ReadService", a1_application_package_api_only),
    "A1 non-public name": (S, "from baec_app.application import facades", a1_application_package_api_only),
    "A2 command facade": (S, "from baec_app.application import HumanCommandFacade", a2_no_command_side),
    "A2 gate": (C, "gate = HumanConfirmationGate(clock, ids)", a2_no_command_side),
    "A2 approval": (S, "x = HumanApproval", a2_no_command_side),
    "A2 authorization": (S, "from baec_app.domain.models import HumanAuthorization", a2_no_command_side),
    "A2 approval request": (S, "r = ApprovalRequest", a2_no_command_side),
    "A2 build command facade": (C, "build_command_facade(repo, clock=c, ids=i)", a2_no_command_side),
    "A2 request_from_proposal": (S, "facade.request_from_proposal(session, proposal)", a2_no_command_side),
    "A2 confirm": (S, "facade.confirm_baec(approval)", a2_no_command_side),
    "A2 judgment": (S, "facade.record_dormancy_judgment(approval)", a2_no_command_side),
    "A2 move to dormant": (S, "facade.move_to_conditionally_dormant(approval)", a2_no_command_side),
    "A2 move to active": (S, "facade.move_to_active_opportunity(approval)", a2_no_command_side),
    "A2 move to no path": (S, "facade.move_to_no_plausible_path(approval)", a2_no_command_side),
    "A2 approve": (S, "gate.approve(s, r, displayed_digest=d)", a2_no_command_side),
    "A2 redeem": (S, "gate.redeem(a, k)", a2_no_command_side),
    "A2 authorize": (S, "authorize(request, approval)", a2_no_command_side),
    "A2 propose": (S, "facade.propose_confirmation(c, captured_at=t)", a2_no_command_side),
    "A2 proposal origin": (S, "origin = ProposalOrigin.DETERMINISTIC", a2_no_command_side),
    "A3 domain import": (S, "from baec_app.domain.models import BaecCandidate", a3_no_domain),
    "A3 domain constructor": (S, "c = BaecCandidate(**fields)", a3_no_domain),
    "A3 evidence constructor": (S, "e = EvidenceExcerpt(text, p, s)", a3_no_domain),
    "A4 service imports data": (S, "from baec_app.data.ai_provenance import AiProvenanceStore", a4_data_only_from_composition),
    "A4 composition imports the repository": (C, "from baec_app.data.repository import Repository", a4_data_only_from_composition),
    "A4 composition imports database": (C, "from baec_app.data.database import connect", a4_data_only_from_composition),
    "A4 sqlite3": (C, "import sqlite3", a4_data_only_from_composition),
    "A4 dynamic data import": (S, "import importlib\nimportlib.import_module('baec_app.data.repository')", a4_data_only_from_composition),
    "A5 anthropic in service": (S, "import anthropic", a5_anthropic_only_in_the_provider),
    "A5 anthropic in composition": (C, "from anthropic import Anthropic", a5_anthropic_only_in_the_provider),
    "A5 provider imported by service": (S, "from baec_app.ai.anthropic_provider import AnthropicExtractionProvider", a5_anthropic_only_in_the_provider),
    "A5 provider imported by package root": ("baec_app.ai", "from baec_app.ai.anthropic_provider import AnthropicExtractionProvider", a5_anthropic_only_in_the_provider),
    "A6 service imports composition": (S, "from baec_app.ai.composition import open_extraction_runtime", a6_pure_modules_import_no_concrete_adapter),
    "A6 validation imports data": ("baec_app.ai.validation", "import baec_app.data.ai_provenance", a6_pure_modules_import_no_concrete_adapter),
    "A6 provider protocol imports httpx2": ("baec_app.ai.provider", "import httpx2", a6_pure_modules_import_no_concrete_adapter),
    "A7 tools keyword": (P, "client.messages.create(model=m, tools=[t])", a7_no_model_tools),
    "A7 tool_choice keyword": (P, "client.messages.create(model=m, tool_choice={'type': 'auto'})", a7_no_model_tools),
    "A7 mcp servers": (P, "client.messages.create(model=m, mcp_servers=[s])", a7_no_model_tools),
    "A7 MCP server object": (C, "server = MCPServer(name='x')", a7_no_model_tools),
    "A8 explicit key": (P, "anthropic.Anthropic(api_key=key, max_retries=0)", a8_no_secrets_or_logging),
    "A8 key literal": (P, "KEY = 'sk-ant-api03-abc'", a8_no_secrets_or_logging),
    "A8 logging": (S, "import logging\nlogging.getLogger(__name__).info('request %s', spec)", a8_no_secrets_or_logging),
    "A8 print": (S, "print(response)", a8_no_secrets_or_logging),
    "A9 imports MCP package": (S, "from baec_app.mcp.server import build_mcp_server", a9_no_mcp_link),
    "A9 imports MCP SDK": (C, "from mcp.server import MCPServer", a9_no_mcp_link),
    "A9 lower layer imports ai": ("baec_app.application.facades", "from baec_app.ai.service import ExtractionService", lower_layers_never_import_ai),
    "A9 MCP imports ai": ("baec_app.mcp.tools", "import baec_app.ai.composition", lower_layers_never_import_ai),
    "A9 mapping imports service": ("baec_app.application.ai_proposal_mapping",
                                   "from baec_app.ai.service import ExtractionService", lower_layers_never_import_ai),
    "A9 mapping imports validation directly": ("baec_app.application.ai_proposal_mapping",
                                               "from baec_app.ai.validation import validate_extraction",
                                               lower_layers_never_import_ai),
    "A9 mapping imports the provider": ("baec_app.application.ai_proposal_mapping",
                                        "import baec_app.ai.anthropic_provider", lower_layers_never_import_ai),
    "A9 mapping imports contracts": ("baec_app.application.ai_proposal_mapping",
                                     "from baec_app.ai.contracts import BaecExtractionOutput", lower_layers_never_import_ai),
    "A9 mapping imports the package root": ("baec_app.application.ai_proposal_mapping",
                                            "from baec_app.ai import verification", lower_layers_never_import_ai),
    "A9 look-alike verification module": ("baec_app.application.ai_proposal_mapping",
                                          "import baec_app.ai.verification_extra", lower_layers_never_import_ai),
    "A9 second application module imports verification": ("baec_app.application.facades",
                                                           "from baec_app.ai.verification import verify_persisted_extraction",
                                                           lower_layers_never_import_ai),
    "A9 human normalization imports validation": ("baec_app.application.human_normalization",
                                                  "from baec_app.ai.validation import validate_extraction",
                                                  lower_layers_never_import_ai),
    "A9 human normalization imports verification": ("baec_app.application.human_normalization",
                                                    "from baec_app.ai.verification import verify_persisted_extraction",
                                                    lower_layers_never_import_ai),
    "A9 human normalization imports the service": ("baec_app.application.human_normalization",
                                                   "import baec_app.ai.service", lower_layers_never_import_ai),
    "A9 human normalization imports a grounding look-alike": ("baec_app.application.human_normalization",
                                                              "import baec_app.ai.grounding_extra",
                                                              lower_layers_never_import_ai),
    "A9 another application module imports grounding": ("baec_app.application.facades",
                                                        "from baec_app.ai.grounding import tokenize",
                                                        lower_layers_never_import_ai),
    "A9 the mapper imports grounding": ("baec_app.application.ai_proposal_mapping",
                                        "from baec_app.ai.grounding import tokenize", lower_layers_never_import_ai),
    "A9 data layer imports verification": ("baec_app.data.proposal_bridge",
                                           "from baec_app.ai.verification import verify_persisted_extraction",
                                           lower_layers_never_import_ai),
    "A10 importlib": (S, "import importlib", a10_no_dynamic_code_processes_or_raw_network),
    "A10 dunder import": (S, "__import__(name)", a10_no_dynamic_code_processes_or_raw_network),
    "A10 eval": (S, "eval(text)", a10_no_dynamic_code_processes_or_raw_network),
    "A10 subprocess": (C, "import subprocess", a10_no_dynamic_code_processes_or_raw_network),
    "A10 os.system": (C, "os.system(command)", a10_no_dynamic_code_processes_or_raw_network),
    "A10 socket": (P, "import socket", a10_no_dynamic_code_processes_or_raw_network),
    "A10 requests": (P, "import requests", a10_no_dynamic_code_processes_or_raw_network),
    "A10 urllib": (P, "from urllib.request import urlopen", a10_no_dynamic_code_processes_or_raw_network),
    "A10 httpx2 client": (P, "import httpx2\nclient = httpx2.Client()", a10_no_dynamic_code_processes_or_raw_network),
    "A10 httpx2 transport": (P, "import httpx2\nt = httpx2.HTTPTransport()", a10_no_dynamic_code_processes_or_raw_network),
    "A10 httpx2 request": (P, "import httpx2\nhttpx2.post(url)", a10_no_dynamic_code_processes_or_raw_network),
    "A10 httpx2 from-import": (P, "from httpx2 import Client", a10_no_dynamic_code_processes_or_raw_network),
    "A10 httpx2 outside the provider": (S, "import httpx2", a10_no_dynamic_code_processes_or_raw_network),
}


@pytest.mark.parametrize("case", VIOLATIONS.values(), ids=VIOLATIONS.keys())
def test_rules_report_synthetic_violations(case):
    module, source, rule = case
    assert rule(module, source) != []


ALLOWED = {
    "A1 package API": (S, "from baec_app.application import Clock, ReadService", a1_application_package_api_only),
    "A2 store writes are not commands": (S, "self._store.record_run(run)\nself._store.record_terminal_outcome(o)", a2_no_command_side),
    "A2 docstring mentions the gate": (S, '"""Never reaches HumanConfirmationGate."""', a2_no_command_side),
    "A4 composition imports the narrow opener": (C, "from baec_app.data.ai_provenance import open_ai_provenance_store", a4_data_only_from_composition),
    "A5 provider imports anthropic": (P, "import anthropic", a5_anthropic_only_in_the_provider),
    "A5 composition imports the provider": (C, "from baec_app.ai.anthropic_provider import AnthropicExtractionProvider", a5_anthropic_only_in_the_provider),
    "A6 service imports protocols": (S, "from baec_app.ai.provider import AiRequestSpec\nfrom baec_app.ai.provenance import RunRecord", a6_pure_modules_import_no_concrete_adapter),
    "A7 create without tools": (P, "client.messages.create(model=m, max_tokens=n, system=s, messages=x, output_config=o)", a7_no_model_tools),
    "A8 max_retries only": (P, "anthropic.Anthropic(max_retries=0)", a8_no_secrets_or_logging),
    "A10 httpx2 ConnectError check": (P, "import httpx2\nok = type(e.__cause__) is httpx2.ConnectError", a10_no_dynamic_code_processes_or_raw_network),
    "A10 re.compile": ("baec_app.ai.service", "import re\nP = re.compile('x')", a10_no_dynamic_code_processes_or_raw_network),
    "A9 the named verification exception": ("baec_app.application.ai_proposal_mapping",
                                            "from baec_app.ai.verification import verify_persisted_extraction",
                                            lower_layers_never_import_ai),
    "A9 the named grounding exception": ("baec_app.application.human_normalization",
                                         "from baec_app.ai.grounding import Magnitude, tokenize",
                                         lower_layers_never_import_ai),
}


@pytest.mark.parametrize("case", ALLOWED.values(), ids=ALLOWED.keys())
def test_rules_allow_legitimate_code(case):
    module, source, rule = case
    assert rule(module, source) == []
