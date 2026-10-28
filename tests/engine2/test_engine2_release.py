"""Stage L: the Engine 2 release candidate. Deterministic demonstration, verifier, record, brief, and final audit.

Sockets are blocked and no credential is present. These are software tests on synthetic data, not evidence for the
theory.
"""

import ast
import json
import re
import socket
from pathlib import Path

import pytest

import scripts.engine2_release_demo as demo_script
import scripts.verify_engine2_release as verifier
from baec_app.engine2 import review
from baec_app.engine2.integration import engine1_handoff

REPO = Path(__file__).resolve().parents[2]
ENGINE2 = REPO / "baec_app" / "engine2"
RECORD = REPO / "docs" / "engine2" / "ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json"
BRIEF = REPO / "docs" / "engine2" / "ENGINE2_TECHNICAL_RESEARCH_BRIEF.md"
RELEASE_DOC = REPO / "docs" / "engine2" / "ENGINE2_RELEASE_CANDIDATE_RECORD.md"
DEMO = REPO / "scripts" / "engine2_release_demo.py"
VERIFY = REPO / "scripts" / "verify_engine2_release.py"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(scope="module")
def runs():
    return demo_script.run_release_demo(), demo_script.run_release_demo()


def _code(path):
    return re.sub(r'"""[\s\S]*?"""', "", path.read_text(encoding="utf-8"))


# --- the deterministic demonstration -----------------------------------------------------------------------


def test_the_demonstration_is_deterministic_and_matches_the_record(runs):
    first, second = runs
    record = json.loads(RECORD.read_text(encoding="utf-8"))["release_demo"]
    assert first == second
    assert first["release_demo_digest"] == record["release_demo_digest"]
    assert first["handoff"]["digest"] == record["handoff_digest"]
    assert first["engine2"]["context_digest"] == record["context_digest"]
    assert first["engine1"]["baec_id"] == record["engine1_baec_id"]


def test_the_demonstration_runs_engine1_confirmation_through_engine2_review(runs):
    summary = runs[0]
    e1, e2 = summary["engine1"], summary["engine2"]
    assert (e1["classification"], e1["staleness_status"], e1["confirmed_by"]) == (
        "CONFIRMED_BAEC", "CURRENT", demo_script.REVIEWER_E1)
    assert summary["handoff"]["engine1_reference"].startswith(f"engine1:baec/{e1['baec_id']}/confirmation/")
    assert e2["condition_reference"] == demo_script.stage_j.BUYER_STATEMENT
    assert (e2["threshold"], e2["timing_context"]) == (["GREATER_THAN", "8", "%"], "at the July renewal")
    assert e2["observations"] == [["OBS-DEMO-01", False], ["OBS-DEMO-02", True], ["OBS-DEMO-03", True],
                                  ["OBS-DEMO-04", False], ["OBS-DEMO-05", True]]
    assert e2["measurements"] == [["DM-DEMO-01", "12.00", False], ["DM-DEMO-02", "9.00", True]]
    assert e2["refusals"] == ["ITEM-DEMO-FORUM-01"]
    assert e2["outcome"] == "HUMAN_VERIFIED_CORRESPONDENCE"
    assert e2["review_history"] == [[e2["review_id"], "HUMAN_VERIFIED_CORRESPONDENCE"]]


def test_the_demonstration_changes_no_engine1_account_state(runs):
    e1 = runs[0]["engine1"]
    assert e1["account_state_transitions"] == 0 and e1["account_states"] == [[demo_script.ACCOUNT_ID, None]]


def test_the_demonstration_uses_only_production_boundaries():
    code = _code(DEMO)
    for boundary in ("build_command_facade(", "facade.gate.open_session(", "facade.request_baec_confirmation(",
                     "facade.gate.approve(", "facade.confirm_baec(", "make_read_only(",
                     "handoff_boundary.read_confirmed_baec(", "handoff_boundary.monitoring_plan_from_handoff(",
                     "handoff_boundary.currentness_reading(", "e2.record_plan_activation(", "capture_observation(",
                     "compute_derived_measurement(", "review.build_review_packet(", "review.submit_human_review("):
        assert boundary in code, boundary
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", code)
    assert re.findall(r'execute\("([^"]*)"', code) == ["SELECT account_id, state FROM accounts",
                                                       "SELECT COUNT(*) FROM account_state_transitions"]
    for forbidden in ("uuid", "random", "datetime.now", "utcnow", "time.time", "os.environ", "getenv", "API_KEY",
                      "anthropic", "socket", "requests", "urllib", "http", "subprocess", "open(", "write_text",
                      "write_bytes", "HBR-CORR", "expected_", "case_id", "corpus", "tests.", "classify_correspondence",
                      "record_review", "AccountState", "persist_transition", "outreach", "opportunit", "latest",
                      "build_correspondence_ai_input", "propose_correspondence"):
        assert forbidden not in code, forbidden


def test_the_stage_j_scenario_is_reused_not_altered():
    from baec_app.engine2.operator_ui import demo as stage_j
    assert demo_script.stage_j is stage_j
    assert demo_script.CHOICES.required_dimensions == stage_j.PLAN.required_dimensions
    assert stage_j.PLAN.baec_id == "BAEC-DEMO-001"  # the Stage J page keeps its own fixed demo store


# --- the verifier ------------------------------------------------------------------------------------------


def test_the_verifier_identity_checks_pass_apart_from_the_working_tree(monkeypatch):
    for name in verifier.LIVE_GATES:
        monkeypatch.delenv(name, raising=False)
    expected = json.loads(RECORD.read_text(encoding="utf-8"))
    results = verifier.identity_checks(expected)
    failed = [(name, detail) for name, passed, detail in results if not passed and name != "working tree clean"]
    assert failed == []
    assert {name for name, _, _ in results} >= {"no live gate variable set", "BAEC_ENGINE_2 schema",
                                                "BAEC_ENGINE_2_AI schema", "Engine 1 schema version",
                                                "Stage A domain contract hash", "Research Contract hash"}


def test_the_verifier_refuses_when_a_live_gate_is_set(monkeypatch):
    monkeypatch.setenv("BAEC_LIVE_E2_STAGE_H", "1")
    results = dict((name, passed) for name, passed, _ in verifier.identity_checks(json.loads(RECORD.read_text())))
    assert results["no live gate variable set"] is False


def test_the_verifier_demo_checks_pass():
    results = verifier.demo_checks(json.loads(RECORD.read_text(encoding="utf-8")))
    assert all(passed for _, passed, _ in results), results


def test_the_verifier_composes_existing_suites_and_stays_offline():
    for arguments in verifier.SUITES.values():
        for item in arguments:
            if item.startswith("tests/"):
                assert (REPO / item).exists(), item
    code = _code(VERIFY)
    for forbidden in ("ANTHROPIC", "API_KEY", "getenv(", "environ.get(", "environ[", "fetch", "push", "requests",
                      "urllib", "socket", "anthropic", "http", "latest"):
        assert forbidden not in code, forbidden
    assert re.findall(r'(?<!def )_git\(([^)]*)\)', code) == ['"rev-parse", "HEAD"', '"status", "--porcelain"']


# --- record and brief ----------------------------------------------------------------------------------------


def test_the_record_holds_no_secret_or_machine_path_and_claims_no_tag():
    text = RECORD.read_text(encoding="utf-8")
    record = json.loads(text)
    assert not re.search(r"sk-ant|/Users/|/home/|C:\\\\|api[_-]?key\"\s*:", text, re.I)
    assert record["baseline"]["starting_commit"] == "69175621d49a7c194a50810fcbe2c3a28867ecfc"
    assert "Not tagged" in record["status"] and record["stage_h_b"]["status"] == "not executed (paused)"
    assert record["corpus_distribution"] == {"cases": 64, "NO_CORRESPONDENCE": 20, "POSSIBLE_CORRESPONDENCE": 32,
                                             "HUMAN_VERIFIED_CORRESPONDENCE": 9, "blocked_before_classification": 3}
    for path in record["reproduction_files"]:
        assert (REPO / path).exists(), path


def test_the_brief_states_the_boundaries_and_claims_nothing_empirical():
    text = BRIEF.read_text(encoding="utf-8")
    for required in ("Correspondence narrows human attention. It does not establish that the buyer is currently "
                     "evaluating, intends to purchase, or that an opportunity exists.",
                     "AI output is advisory and cannot create a human finding or authoritative correspondence outcome.",
                     "A confirmed BAEC does not automatically create an active opportunity.",
                     "A `HUMAN_VERIFIED_CORRESPONDENCE` outcome does not automatically create an active opportunity.",
                     "Stage H-B has not been executed.", "`NO_CORRESPONDENCE`", "`POSSIBLE_CORRESPONDENCE`",
                     "has not been peer reviewed", "No claim of production readiness"):
        assert required in text, required
    for doc in (BRIEF, RELEASE_DOC):
        body = doc.read_text(encoding="utf-8")
        assert "\u2014" not in body
        for claim in ("validated the", "proves that", "predicts purchase", "production-ready", "production ready",
                      "lead score", "buying intent", "accepted for publication", "peer-reviewed"):
            assert claim not in body.lower(), (doc.name, claim)


# --- final authority and boundary audit (all Engine 2 production code) -----------------------------------------


def _engine2_sources():
    return {path.relative_to(REPO).as_posix(): path.read_text(encoding="utf-8") for path in sorted(ENGINE2.rglob("*.py"))}


def _callers(name):
    return {path for path, text in _engine2_sources().items() if re.search(rf"\b{name}\(", _code(REPO / path))}


def test_only_stage_i_records_reviews_and_only_the_core_classifies():
    assert _callers(r"store\.record_review") | _callers(r"p\.record_review") == {"baec_app/engine2/review.py"}
    assert _callers("classify_correspondence") == {"baec_app/engine2/classification.py",
                                                    "baec_app/engine2/persistence.py"}
    assert _callers("HumanCorrespondenceReview") == {"baec_app/engine2/persistence.py", "baec_app/engine2/review.py"}


def test_ai_modules_have_no_path_to_human_findings_or_outcomes():
    for module in ("baec_app/engine2/ai.py", "baec_app/engine2/ai_persistence.py"):
        code = _code(REPO / module)
        for forbidden in ("HumanCorrespondenceReview", "DimensionAssessment(", "classify_correspondence",
                          "record_review", "CorrespondenceOutcome.", "from .persistence", "from .review"):
            assert forbidden not in code, (module, forbidden)


def test_no_engine2_code_changes_account_state_writes_engine1_or_takes_seller_action():
    for path, text in _engine2_sources().items():
        code = _code(REPO / path)
        for forbidden in ("persist_transition", "AccountState", "account_state_transitions", "move_to_",
                          "ConfirmationExecutionService", "authorization_grants", "proposal_bridge", "save_confirmed",
                          "save_classification", "insert_confirmed_record", "UPDATE baec_records", "staleness_status =",
                          "send_email", "crm", "outreach(", "create_opportunity"):
            assert forbidden not in code, (path, forbidden)
        imports = set(re.findall(r"^from (baec_app\.[\w.]+) import", code, re.M))
        engine1 = {i for i in imports if not i.startswith("baec_app.engine2")}
        allowed = {"baec_app.domain.enums"}
        if path == "baec_app/engine2/integration/engine1_handoff.py":
            allowed |= {"baec_app.data.database", "baec_app.data.repository", "baec_app.domain.models"}
        assert engine1 <= allowed, (path, engine1 - allowed)


def test_no_engine2_production_code_selects_a_model_or_uses_the_latest_token():
    for path, text in _engine2_sources().items():
        assert not re.search(r"\blatest\b", text, re.I), path
        assert "anthropic" not in _code(REPO / path), path
        assert "claude-" not in _code(REPO / path), path


def test_the_live_harness_stays_gated_and_outside_production():
    harness = (REPO / "tests" / "live" / "engine2_stage_h.py").read_text(encoding="utf-8")
    assert 'LIVE_FLAG = "BAEC_LIVE_E2_STAGE_H"' in harness and "def live_gate(" in harness
    assert not any("engine2_stage_h" in text for text in _engine2_sources().values())
    config = (REPO / "pytest.ini").read_text(encoding="utf-8")
    assert '-m "not live_claude"' in config


def test_public_operations_stay_exactly_as_locked():
    assert sorted(n for n in dir(review) if not n.startswith("_") and callable(getattr(review, n))
                  and getattr(getattr(review, n), "__module__", None) == review.__name__
                  and type(getattr(review, n)).__name__ == "function") == [
        "attach_advisory_proposal", "build_review_packet", "submit_human_review"]
    functions = sorted(n for n, v in vars(engine1_handoff).items() if callable(v) and not n.startswith("_")
                       and getattr(v, "__module__", None) == engine1_handoff.__name__ and type(v).__name__ == "function")
    assert functions == ["condition_input", "currentness_reading", "monitoring_plan_from_handoff", "read_confirmed_baec",
                         "require_plan_matches"]
    tree = ast.parse((REPO / "baec_app" / "engine2" / "review.py").read_text(encoding="utf-8"))
    submit = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "submit_human_review")
    assert not [a.arg for a in submit.args.kwonlyargs if re.search(r"outcome|classif", a.arg)]
