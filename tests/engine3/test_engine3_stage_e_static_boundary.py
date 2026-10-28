"""Stage E static boundary: offline, no provider, no I/O, no action, no fixture coupling."""

import ast
import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "baec_app" / "engine3"
STAGE_E_FILES = (PACKAGE / "proposal_contract.py", PACKAGE / "offline_evaluation.py")
STAGE_D_MANIFEST = REPO / "docs" / "engine3" / "ENGINE3_STAGE_D_IMPLEMENTATION_MANIFEST.json"

ALLOWED_STDLIB = {"__future__", "dataclasses", "datetime", "decimal", "enum", "hashlib", "json", "re", "typing"}

FORBIDDEN_TOKENS = (
    r"\banthropic\b", r"\bopenai\b", r"\brequests\b", r"\burllib\b", r"\bhttpx\b", r"\baiohttp\b",
    r"\bhttp\.client\b", r"\bsocket\b", r"\bsmtplib\b", r"\bimaplib\b", r"\bstreamlit\b", r"\bsalesforce\b",
    r"\bsimple_salesforce\b", r"\bsqlite3\b", r"\bsqlalchemy\b", r"\bpsycopg", r"\bshelve\b", r"\bpickle\b",
    r"\bsubprocess\b", r"\bos\.", r"\benviron\b", r"\bgetenv\b", r"\bopen\(", r"\bread_text\b", r"\bwrite_text\b",
    r"\bPath\(", r"\burlopen\b", r"\bmessages\.create\b", r"\bapi_key\b", r"\beval\(", r"\bexec\(",
    r"\b__import__\b", r"\bimportlib\b", r"datetime\.now", r"\btime\.time\b", r"\brandom\b", r"\buuid4\b",
    r"\bsend_email\b", r"\bsend_message\b", r"\bplace_call\b", r"\bcreate_task\b", r"\bschedule_meeting\b",
    r"baec_app\.engine2", r"baec_app\.domain", r"baec_app\.application", r"baec_app\.data",
)
FIXTURE_TOKENS = (
    r"E3E-", r"E3B-", r"expected_", r"\bfixtures?\b", r"ORACLE", r"PRERECORDED_PROPOSALS", r"\btests\b",
    r"recording_id", r"CTX-AR", r"G-AR-", r"Alder", r"Cobalt",
)


def _violations(source: str, patterns) -> list[str]:
    return [p for p in patterns if re.search(p, source)]


def _imports(source: str) -> list[str]:
    """Absolute imported module roots; relative imports return '.'."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append("." if node.level else node.module.split(".")[0])
    return found


def _stage_e_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in STAGE_E_FILES)


def test_scanners_detect_planted_violations():
    # Positive controls: the patterns must actually match, so the audit is not vacuous.
    planted = "import anthropic\nfrom openai import OpenAI\nimport requests\nopen('x')\nimport socket\n"
    assert {r"\banthropic\b", r"\bopenai\b", r"\brequests\b", r"\bopen\(", r"\bsocket\b"} <= set(
        _violations(planted, FORBIDDEN_TOKENS)
    )
    assert _violations("CASE = 'E3E-P01'; path = 'fixtures/x.json'", FIXTURE_TOKENS)
    assert _imports("import smtplib\nfrom . import x\nfrom http.client import HTTPConnection") == [
        "smtplib", ".", "http"
    ]


def test_stage_e_production_has_no_provider_network_io_persistence_or_action_capability():
    assert _violations(_stage_e_source(), FORBIDDEN_TOKENS) == []


def test_stage_e_production_does_not_reference_corpus_oracle_or_fixtures():
    assert _violations(_stage_e_source(), FIXTURE_TOKENS) == []


def test_engine3_package_imports_only_allowed_stdlib_and_itself():
    for path in sorted(PACKAGE.glob("*.py")):
        roots = set(_imports(path.read_text(encoding="utf-8")))
        assert roots <= ALLOWED_STDLIB | {"."}, (path.name, roots - ALLOWED_STDLIB - {"."})


def test_stage_e_did_not_modify_stage_d_production_or_tests():
    manifest = json.loads(STAGE_D_MANIFEST.read_text(encoding="utf-8"))
    for group in ("production_files", "test_files"):
        for rel, expected in manifest[group].items():
            assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == expected, rel


def test_stage_e_is_not_exported_from_the_stage_d_package_entry_point():
    init = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    assert "proposal_contract" not in init and "offline_evaluation" not in init
