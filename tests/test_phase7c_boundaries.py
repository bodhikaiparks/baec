"""Phase 7C boundaries: AI_DRAFT never enters the Phase 4 in-memory path, the bridge substrate reaches no
domain write, Phase 5 MCP is untouched, and fixture AI provenance stays test-only (design §6.4, §6.5, §16).
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path

import pytest

import baec_app.application as application
from baec_app.application.errors import AiDraftNotPermitted, ApplicationValidationError
from baec_app.application.proposals import PROPOSAL_TYPES, ProposalOrigin
from baec_app.application import requests
from baec_app.application.requests import ApprovalRequest, RequestKind, build_request
from baec_app.data.database import AI_PROVENANCE_TABLES, BRIDGE_TABLES, table_counts
from baec_app.data.seed import build_canonical_seed_database
from tests import bridge_builders
from tests.application_builders import PAYLOAD_BUILDERS, request_for
from tests.builders import NOW, candidate, evaluation_evidence
from tests.persistence_builders import dump
from tests.test_application_proposals import REASON, db, human_proposals  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
# Looked up, not attribute-accessed, so a missing member fails these tests instead of breaking collection.
AI = ProposalOrigin.__members__.get("AI_DRAFT")


# --- the origin vocabulary ------------------------------------------------------------------


def test_the_origins_are_exactly_human_draft_deterministic_and_ai_draft():
    assert [o.value for o in ProposalOrigin] == ["HUMAN_DRAFT", "DETERMINISTIC", "AI_DRAFT"]
    for absent in ("AI_MODEL", "MODEL", "CLAUDE", "LLM"):
        assert absent not in ProposalOrigin.__members__
        with pytest.raises(ValueError):
            ProposalOrigin(absent)
    assert "AiDraftNotPermitted" in application.__all__ and not issubclass(AiDraftNotPermitted,
                                                                          ApplicationValidationError)


# --- AI_DRAFT never reaches the Phase 4 in-memory gate ------------------------------------------


@pytest.mark.parametrize("cls", PROPOSAL_TYPES, ids=lambda c: c.__name__)
def test_no_phase4_proposal_can_carry_ai_draft(cls):
    with pytest.raises(AiDraftNotPermitted):
        dataclasses.replace(human_proposals()[cls], origin=AI)


@pytest.mark.parametrize("cls", PROPOSAL_TYPES, ids=lambda c: c.__name__)
def test_request_from_proposal_refuses_a_forged_ai_draft_before_any_dispatch(db, cls, monkeypatch):
    proposal = human_proposals(db.judgment_id)[cls]
    object.__setattr__(proposal, "origin", AI)  # bypasses the constructor check on purpose
    for name in ("request_baec_confirmation", "request_dormancy_judgment", "request_move_to_conditionally_dormant",
                 "request_move_to_active_opportunity", "request_move_to_no_plausible_path"):
        monkeypatch.setattr(db.command, name, lambda *a, **k: pytest.fail("an AI_DRAFT proposal was dispatched"))
    before = dump(db.writer)
    with pytest.raises(AiDraftNotPermitted):
        db.command.request_from_proposal(db.session, proposal)
    assert db.command.gate._requests == {}  # nothing was registered with the gate
    assert dump(db.writer) == before


def test_every_phase4_request_entry_point_refuses_ai_draft(db):
    command, session = db.command, db.session
    calls = {
        "confirm": lambda: command.request_baec_confirmation(session, candidate(), captured_at=NOW, origin=AI),
        "judgment": lambda: command.request_dormancy_judgment(session, "B-1", plausibility=human_proposals()[
            PROPOSAL_TYPES[1]].plausibility, addressability=human_proposals()[PROPOSAL_TYPES[1]].addressability,
            notes=None, origin=AI),
        "dormant": lambda: command.request_move_to_conditionally_dormant(
            session, "ACC-1", baec_id="B-1", judgment_id=db.judgment_id, non_evaluation_evidence=None, origin=AI),
        "active": lambda: command.request_move_to_active_opportunity(
            session, "ACC-1", evaluation_evidence=evaluation_evidence(), origin=AI),
        "no path": lambda: command.request_move_to_no_plausible_path(
            session, "ACC-1", ground=human_proposals()[PROPOSAL_TYPES[4]].ground, reason=REASON,
            basis_interaction_id="INT-1", origin=AI),
    }
    before = dump(db.writer)
    for name, call in calls.items():
        with pytest.raises(AiDraftNotPermitted):
            call()
    assert db.command.gate._requests == {} and dump(db.writer) == before


@pytest.mark.parametrize("kind", list(RequestKind), ids=lambda k: k.value)
def test_no_approval_request_can_carry_ai_draft(kind, monkeypatch):
    with pytest.raises(AiDraftNotPermitted):
        request_for(kind, origin=AI)
    with monkeypatch.context() as patched:  # build_request refuses before it digests anything
        patched.setattr(requests, "digest_request", lambda **_: pytest.fail("an AI_DRAFT request was digested"))
        with pytest.raises(AiDraftNotPermitted):
            build_request(request_id="R", session_id="S", opened_at=NOW, kind=kind, origin=AI,
                          payload=PAYLOAD_BUILDERS[kind]())
    legitimate = request_for(kind)
    with pytest.raises(AiDraftNotPermitted):
        ApprovalRequest(**dict(dataclasses.asdict(legitimate) | {"payload": legitimate.payload,
                                                                  "preview": legitimate.preview}, origin=AI))


@pytest.mark.parametrize("kind", list(RequestKind), ids=lambda k: k.value)
def test_human_draft_and_deterministic_requests_are_unchanged(kind):
    assert request_for(kind, origin=ProposalOrigin.HUMAN_DRAFT).origin is ProposalOrigin.HUMAN_DRAFT
    assert request_for(kind, origin=ProposalOrigin.DETERMINISTIC).origin is ProposalOrigin.DETERMINISTIC


def test_only_the_proposals_module_uses_ai_draft_in_the_application_layer():
    """No Phase 4 service, facade, or request path ever produces or branches on AI_DRAFT itself."""
    for path in sorted((REPO / "baec_app" / "application").glob("*.py")):
        if path.name == "proposals.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        uses = [node.lineno for node in ast.walk(tree)
                if (isinstance(node, ast.Attribute) and node.attr == "AI_DRAFT")
                or (isinstance(node, ast.Constant) and node.value == "AI_DRAFT")]
        assert uses == [], path.name


# --- the bridge substrate reaches no domain write, MCP, AI, or application path -------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# Phase 7D: the only production module that may use the bridge store is the artifact -> AI_DRAFT mapper.
BRIDGE_STORE_USERS = {"baec_app/application/ai_proposal_mapping.py",
                      "baec_app/application/proposal_review.py",  # Phase 7E: the human-review layer
                      "baec_app/application/proposal_authorization.py"}  # Phase 7F-B: grants and execution


def test_no_production_module_uses_the_bridge_store_yet():
    """Since Phase 7D, exactly the approved mapper uses it (name kept for ID continuity)."""
    users = {path.relative_to(REPO).as_posix() for path in sorted((REPO / "baec_app").rglob("*.py"))
             if path.name != "proposal_bridge.py" and _imports(path) & {"baec_app.data.proposal_bridge"}}
    assert users == BRIDGE_STORE_USERS


def test_no_other_production_module_names_the_bridge_store_at_all():
    """Text-level backstop: no import alias, dynamic import, or string reference outside the approved mapper."""
    named = {path.relative_to(REPO).as_posix() for path in sorted((REPO / "baec_app").rglob("*.py"))
             if path.name != "proposal_bridge.py"
             and ("proposal_bridge" in path.read_text(encoding="utf-8")
                  or "ProposalBridgeStore" in path.read_text(encoding="utf-8"))}
    assert named == BRIDGE_STORE_USERS


def test_the_bridge_store_imports_only_the_data_layer_and_the_standard_library():
    imported = _imports(REPO / "baec_app" / "data" / "proposal_bridge.py")
    project = {name for name in imported if name.startswith("baec_app")}
    assert project == {"baec_app.data.database", "baec_app.data.records", "baec_app.data.repository"}


def test_phase5_mcp_is_untouched_and_no_write_package_exists():
    """Since 7G the separate write package exists (name kept for ID continuity); neither MCP package names the bridge."""
    assert (REPO / "baec_app" / "mcp_write").is_dir()
    for path in sorted((REPO / "baec_app" / "mcp").glob("*.py")) + sorted((REPO / "baec_app" / "mcp_write").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in ("proposal_bridge", "AI_DRAFT", "AiDraftNotPermitted") + BRIDGE_TABLES:
            assert name not in text, (path.name, name)


def test_the_ai_layer_never_names_the_bridge():
    for path in sorted((REPO / "baec_app" / "ai").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in ("proposal_bridge", "AI_DRAFT") + BRIDGE_TABLES:
            assert name not in text, (path.name, name)


# --- test-only fixture provenance (design D15) -----------------------------------------------------


def _repository_files() -> list[Path]:
    if (REPO / ".git").exists():
        listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO,
                                capture_output=True, text=True, check=True).stdout.splitlines()
        return [REPO / name for name in listed]
    return [path for path in REPO.rglob("*") if path.is_file()
            and not {"__pycache__", ".pytest_cache"} & set(path.parts)]


def test_no_database_file_is_part_of_the_repository():
    databases = [p.relative_to(REPO).as_posix() for p in _repository_files()
                 if p.suffix in (".db", ".sqlite", ".sqlite3")]
    assert databases == []
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").split()
    assert {"*.db", "*.sqlite", "*.sqlite3"} <= set(ignore)


def test_no_production_or_seed_source_can_introduce_ai_provenance():
    seed_sources = [REPO / "baec_app" / "data" / "seed.py", *sorted((REPO / "scripts").glob("*.py")),
                    *sorted((REPO / "data").rglob("*.json"))]
    # Public demo release: the one manual recording utility runs a real Phase 6 extraction, so it may name the
    # provider and read its run back through AiProvenanceStore. Nothing else on this list, and no other script.
    recorder = REPO / "scripts" / "record_public_demo_artifact.py"
    for path in seed_sources:
        text = path.read_text(encoding="utf-8")
        allowed = {"provider", "AiProvenanceStore"} if path == recorder else set()
        for name in ("ai_runs", "ai_artifacts", "AiProvenanceStore", "record_terminal_outcome", "ProposalBridgeStore",
                     "add_proposal", "FakeProvider", "provider", "FIXTURE-") + BRIDGE_TABLES:
            assert name in allowed or name not in text, (path.relative_to(REPO).as_posix(), name)
    # The public-demo loader names the fixture markers only to refuse them: every "FIXTURE-" literal it holds must
    # sit inside its FIXTURE_MARKERS refusal tuple.
    loader = REPO / "baec_app" / "application" / "public_demo_recording.py"
    for path in sorted((REPO / "baec_app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "class FakeProvider" not in text, path.name
        if path != loader:
            assert "FIXTURE-" not in text, path.name
            continue
        tree = ast.parse(text)
        [markers] = [n.value for n in tree.body if isinstance(n, ast.Assign)
                     and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["FIXTURE_MARKERS"]]
        inside = {id(c) for c in ast.walk(markers) if isinstance(c, ast.Constant)}
        assert not [c for c in ast.walk(tree) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    and "FIXTURE-" in c.value and id(c) not in inside], path.name


def test_the_canonical_seed_has_no_ai_provenance_and_no_bridge_rows():
    canonical = build_canonical_seed_database()
    try:
        counts = table_counts(canonical)
        assert all(counts[table] == 0 for table in AI_PROVENANCE_TABLES + BRIDGE_TABLES)
    finally:
        canonical.close()


def test_bridge_fixtures_are_visibly_test_only():
    for name in ("ACC", "OTHER_ACC", "INT", "INT_SAME_ACCOUNT", "INT_OTHER", "MODEL", "ACTOR"):
        assert getattr(bridge_builders, name).startswith(bridge_builders.FIXTURE_PREFIX), name
    assert "TEST-ONLY FIXTURES" in bridge_builders.__doc__
    assert "never be presented as" in bridge_builders.__doc__
