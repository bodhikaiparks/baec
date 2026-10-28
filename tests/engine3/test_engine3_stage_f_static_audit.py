"""Stage F static audit: offline, no model or network, no credentials, no action, no fixture coupling.

AST-based and independent of the legacy Stage D regex test (whose double-escaped patterns cannot fail).
Every rule is proven non-vacuous by planted-violation self-tests.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "experiments" / "engine3_stage_f"
VERIFIER = REPO / "scripts" / "verify_engine3_stage_f_harness.py"
HARNESS_MODULES = ("__init__", "annotation", "comparison", "contexts", "decision_rules", "envelope", "readiness")

HARNESS_STDLIB = {"__future__", "collections", "dataclasses", "enum", "fractions", "hashlib", "json", "re", "typing"}
VERIFIER_STDLIB = {"__future__", "hashlib", "json", "pathlib", "re", "subprocess", "sys"}
MODEL_SDKS = {"anthropic", "openai", "google", "vertexai", "mistralai", "cohere", "ollama", "langchain",
              "langchain_core", "langchain_openai", "langchain_anthropic", "litellm", "transformers", "boto3",
              "botocore", "mcp", "claude_agent_sdk", "claude_code_sdk"}
NETWORK = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "httpx2", "aiohttp", "websockets",
           "websocket", "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "xmlrpc", "asyncio", "selectors",
           "email"}
PROCESS_OR_DYNAMIC = {"importlib", "runpy", "pty", "ctypes", "multiprocessing", "threading", "os", "subprocess",
                      "base64", "codecs", "pickle", "marshal", "shelve", "sqlite3", "builtins", "shutil", "tempfile"}
ENGINE12 = ("baec_app.domain", "baec_app.application", "baec_app.data", "baec_app.engine2", "baec_app.ai",
            "baec_app.mcp", "baec_app.mcp_write", "baec_app.interfaces", "streamlit")
DYNAMIC_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "breakpoint"}
CREDENTIAL_NAMES = {"environ", "environb", "getenv", "putenv", "unsetenv"}
CREDENTIAL_TEXT = re.compile(r"(?i)api[_-]?key(?![_a-z])|sk-ant-|anthropic_|openai_|x-api-key|auth[_-]?token|\bbearer\b")
CALL_SURFACE = {"send", "sendall", "sendmail", "send_message", "send_email", "post", "put", "patch", "request",
                "urlopen", "connect", "create_connection", "place_call", "schedule_meeting", "create_task"}
MODEL_CALL = re.compile(r"\b(messages|completions|responses|chat)\.create$")
ACTION_DEFINITION = re.compile(
    r"(?i)^(send|email|mail|outreach|contact|notify|call_|dial|phone|schedule|book_|crm|post_|publish|dispatch|"
    r"execute|deliver|transmit|upload|place_call|draft_email)")
ENGINE12_WRITE = re.compile(r"^(save_|insert_|record_|apply_transition|add_proposal|confirm_baec)")
FILE_WRITE = {"write_text", "write_bytes", "mkdir", "unlink", "rename", "replace_file", "touch", "rmdir"}
FILE_READ = {"read_text", "read_bytes"}
FIXTURE_TOKENS = (r"F-DRY-", r"F-G\d{2}", r"\bE3E-", r"\bE3B-", r"CTX-(?:AR|F)-", r"\bG-AR-", r"\bG-F-", r"(?i)\balder\b",
                  r"(?i)\bcobalt\b", r"(?i)oracle", r"(?i)fixture", r"\bexpected_", r"DRY_RUN_INPUTS",
                  r"PRERECORDED_PROPOSALS", r"tests/")


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _imports(tree: ast.AST) -> list[tuple[str, bool]]:
    """(absolute target, is_relative) for every import."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, False) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.append((base, bool(node.level)))
            found += [(f"{base}.{a.name}" if base else a.name, bool(node.level)) for a in node.names]
    return found


def _import_category(name: str, kind: str) -> str | None:
    root = name.split(".")[0]
    if root in MODEL_SDKS:
        return "MODEL_SDK_IMPORT"
    if root in NETWORK:
        return "NETWORK_IMPORT"
    if root == "tests":
        return "TEST_FIXTURE_IMPORT"
    if any(name == p or name.startswith(p + ".") for p in ENGINE12):
        return "ENGINE12_COUPLING"
    if root in PROCESS_OR_DYNAMIC and not (kind == "verifier" and root == "subprocess"):
        return "PROCESS_OR_DYNAMIC_IMPORT"
    if kind == "harness":
        allowed = root in HARNESS_STDLIB or name == "baec_app.engine3" or name.startswith("baec_app.engine3.")
    else:
        allowed = root in VERIFIER_STDLIB
    return None if allowed else "IMPORT_NOT_ALLOWED"


def _verifier_subprocess_ok(call: ast.Call) -> bool:
    """The verifier may start exactly one kind of process: `sys.executable -m pytest`, without a shell."""
    if _dotted(call.func) != "subprocess.run" or not call.args or not isinstance(call.args[0], ast.List):
        return False
    head = call.args[0].elts[:3]
    if len(head) < 3 or _dotted(head[0]) != "sys.executable":
        return False
    if [getattr(e, "value", None) for e in head[1:]] != ["-m", "pytest"]:
        return False
    return not any(k.arg == "shell" for k in call.keywords)


def audit(source: str, kind: str) -> list[tuple[str, str]]:
    """Return (category, detail) violations. kind is 'harness' or 'verifier'."""
    tree = ast.parse(source)
    found = []
    for name, relative in _imports(tree):
        if relative:
            if kind != "harness":
                found.append(("IMPORT_NOT_ALLOWED", f"relative import {name}"))
            continue
        category = _import_category(name, kind)
        if category:
            found.append((category, name))
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            leaf = node.id if isinstance(node, ast.Name) else node.attr
            if leaf in CREDENTIAL_NAMES:
                found.append(("CREDENTIAL_ACCESS", leaf))
            dotted = _dotted(node) or ""
            if dotted.startswith("ActionAttempt.") and dotted != "ActionAttempt.NONE":
                found.append(("OUTBOUND_ACTION", dotted))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if CREDENTIAL_TEXT.search(node.value):
                found.append(("CREDENTIAL_ACCESS", node.value[:40]))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ACTION_DEFINITION.match(node.name):
            found.append(("OUTBOUND_ACTION", node.name))
        if isinstance(node, ast.Call):
            name = _dotted(node.func) or ""
            leaf = name.split(".")[-1]
            if name in DYNAMIC_CALLS or name.endswith("import_module"):
                found.append(("DYNAMIC_CODE", name))
            if MODEL_CALL.search(name) or (isinstance(node.func, ast.Attribute) and leaf in CALL_SURFACE):
                found.append(("MODEL_OR_NETWORK_CALL", name))
            if isinstance(node.func, ast.Attribute) and ENGINE12_WRITE.match(leaf):
                found.append(("ENGINE12_COUPLING", name))
            if name == "open" or leaf in FILE_WRITE or (kind == "harness" and leaf in FILE_READ):
                found.append(("FILE_IO", name))
            if name.startswith("subprocess.") and not (kind == "verifier" and _verifier_subprocess_ok(node)):
                found.append(("PROCESS_OR_DYNAMIC_IMPORT", name))
    if kind == "harness":
        found += [("FIXTURE_TOKEN", p) for p in FIXTURE_TOKENS if re.search(p, source)]
    return found


def _categories(source: str, kind: str = "harness") -> set[str]:
    return {category for category, _ in audit(source, kind)}


# --- planted-violation self-tests ---------------------------------------------------------------------

PLANTED = {
    "anthropic sdk": ("import anthropic", "MODEL_SDK_IMPORT"),
    "openai sdk": ("from openai import OpenAI", "MODEL_SDK_IMPORT"),
    "google sdk": ("import google.generativeai as genai", "MODEL_SDK_IMPORT"),
    "mcp client": ("from mcp import ClientSession", "MODEL_SDK_IMPORT"),
    "agent sdk": ("from claude_agent_sdk import query", "MODEL_SDK_IMPORT"),
    "requests": ("import requests", "NETWORK_IMPORT"),
    "urllib": ("import urllib.request", "NETWORK_IMPORT"),
    "http client": ("from http.client import HTTPSConnection", "NETWORK_IMPORT"),
    "socket": ("import socket", "NETWORK_IMPORT"),
    "httpx": ("import httpx", "NETWORK_IMPORT"),
    "smtp": ("import smtplib", "NETWORK_IMPORT"),
    "environ key": ("import os\nKEY = os.environ['ANTHROPIC_API_KEY']", "CREDENTIAL_ACCESS"),
    "getenv": ("from os import getenv\nk = getenv('X')", "CREDENTIAL_ACCESS"),
    "key literal": ("K = 'sk-ant-CANARYPLANTEDKEY01'", "CREDENTIAL_ACCESS"),
    "key header": ("H = {'x-api-key': k}", "CREDENTIAL_ACCESS"),
    "model call": ("client.messages.create(model='m', messages=[])", "MODEL_OR_NETWORK_CALL"),
    "http post": ("session.post('https://example.invalid', json={})", "MODEL_OR_NETWORK_CALL"),
    "send email def": ("def send_email(to):\n    return to", "OUTBOUND_ACTION"),
    "schedule def": ("def schedule_meeting():\n    pass", "OUTBOUND_ACTION"),
    "crm def": ("class CrmTaskWriter:\n    pass", "OUTBOUND_ACTION"),
    "outreach action": ("a = ActionAttempt.OUTREACH_EXECUTION", "OUTBOUND_ACTION"),
    "smtp send": ("server.sendmail(a, b, c)", "MODEL_OR_NETWORK_CALL"),
    "engine1 repository": ("from baec_app.data.repository import Repository", "ENGINE12_COUPLING"),
    "engine2 import": ("from baec_app.engine2 import review", "ENGINE12_COUPLING"),
    "engine1 domain": ("from baec_app import domain", "ENGINE12_COUPLING"),
    "engine1 write call": ("repo.save_confirmed_baec(record)", "ENGINE12_COUPLING"),
    "account state call": ("service.apply_transition(x)", "ENGINE12_COUPLING"),
    "fixture adapter import": ("from tests.engine3 import stage_f_adapter", "TEST_FIXTURE_IMPORT"),
    "fixture module import": ("import tests.engine3.stage_f_adapter", "TEST_FIXTURE_IMPORT"),
    "oracle path": ("P = 'tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_ORACLE.json'", "FIXTURE_TOKEN"),
    "fixture case id": ("CASE = 'F-DRY-04'", "FIXTURE_TOKEN"),
    "scenario id": ("if scenario == 'F-G16':\n    pass", "FIXTURE_TOKEN"),
    "context id": ("CONTEXT = 'CTX-AR-CURRENT'", "FIXTURE_TOKEN"),
    "stage e recording id": ("R = 'E3E-P01'", "FIXTURE_TOKEN"),
    "expected outcome": ("expected_allowed = True", "FIXTURE_TOKEN"),
    "subprocess model": ("import subprocess\nsubprocess.run(['claude', '-p', 'hi'])", "PROCESS_OR_DYNAMIC_IMPORT"),
    "dynamic import": ("m = __import__('anthro' + 'pic')", "DYNAMIC_CODE"),
    "importlib": ("import importlib\nm = importlib.import_module('open' + 'ai')", "DYNAMIC_CODE"),
    "eval": ("eval('1')", "DYNAMIC_CODE"),
    "encoded name": ("import base64\nbase64.b64decode('YW50aHJvcGlj')", "PROCESS_OR_DYNAMIC_IMPORT"),
    "file write": ("open('x', 'w').write('y')", "FILE_IO"),
    "path write": ("p.write_text('y')", "FILE_IO"),
    "unlisted import": ("import yaml", "IMPORT_NOT_ALLOWED"),
}


@pytest.mark.parametrize("source,category", PLANTED.values(), ids=PLANTED.keys())
def test_audit_detects_planted_violation(source, category):
    assert category in _categories(source), audit(source, "harness")


VERIFIER_PLANTED = {
    "curl": ("import subprocess\nsubprocess.run(['curl', 'https://example.invalid'])", "PROCESS_OR_DYNAMIC_IMPORT"),
    "shell": ("import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'pytest'], shell=True)",
              "PROCESS_OR_DYNAMIC_IMPORT"),
    "popen": ("import subprocess, sys\nsubprocess.Popen([sys.executable, '-m', 'pytest'])", "PROCESS_OR_DYNAMIC_IMPORT"),
    "model sdk": ("import anthropic", "MODEL_SDK_IMPORT"),
    "network": ("import urllib.request", "NETWORK_IMPORT"),
    "harness import": ("from experiments.engine3_stage_f import envelope", "IMPORT_NOT_ALLOWED"),
    "file write": ("from pathlib import Path\nPath('x').write_text('y')", "FILE_IO"),
    "environ": ("import os\nos.environ.get('K')", "CREDENTIAL_ACCESS"),
}


@pytest.mark.parametrize("source,category", VERIFIER_PLANTED.values(), ids=VERIFIER_PLANTED.keys())
def test_verifier_audit_detects_planted_violation(source, category):
    assert category in _categories(source, "verifier"), audit(source, "verifier")


def test_audit_allows_benign_code():
    benign = ("from __future__ import annotations\nimport json\nfrom dataclasses import dataclass\n"
              "from baec_app.engine3.validator import validate_case\nfrom .envelope import RunBinding\n"
              "def compare_values(a, b):\n    '''No credential read and no api_key access.'''\n    return json.dumps([a, b])\n")
    assert audit(benign, "harness") == []
    pytest_only = ("import subprocess\nimport sys\n"
                   "subprocess.run([sys.executable, '-m', 'pytest', '-q', 't.py'], cwd='.')\n")
    assert audit(pytest_only, "verifier") == []


# --- the real Stage F harness code ------------------------------------------------------------------


def test_audit_scope_is_every_stage_f_harness_module():
    assert sorted(p.stem for p in HARNESS.glob("*.py")) == sorted(HARNESS_MODULES)
    assert not [p for p in HARNESS.rglob("*") if p.is_file() and p.suffix not in (".py", ".pyc")]
    assert VERIFIER.exists()


@pytest.mark.parametrize("module", HARNESS_MODULES)
def test_stage_f_harness_module_passes_the_audit(module):
    assert audit((HARNESS / f"{module}.py").read_text(encoding="utf-8"), "harness") == []


def test_stage_f_harness_verifier_passes_the_audit():
    assert audit(VERIFIER.read_text(encoding="utf-8"), "verifier") == []


def test_harness_never_uses_the_locked_stage_e_loader_or_its_prerecorded_provenance():
    source = "\n".join(p.read_text(encoding="utf-8") for p in sorted(HARNESS.glob("*.py")))
    assert "load_prerecorded_preparation_proposal" not in source
    assert "PRERECORDED_PROVENANCE" not in source and "AIPreparationProposal" not in source
    used = {name for name, _ in _imports(ast.parse(source)) if name.startswith("baec_app.engine3.proposal_contract.")}
    assert used == {"baec_app.engine3.proposal_contract.ProposedArtifact",
                    "baec_app.engine3.proposal_contract.parse_artifact"}


def test_harness_is_outside_the_production_package_and_not_imported_by_it():
    assert not (REPO / "baec_app" / "engine3" / "stage_f").exists()
    for path in sorted((REPO / "baec_app").rglob("*.py")):
        assert "experiments" not in {n.split(".")[0] for n, _ in _imports(ast.parse(path.read_text(encoding="utf-8")))}
