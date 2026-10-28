"""Stage F-AR governance: human track unchanged, autonomous readiness, manifest binding, offline audit, claims.

Software tests only. Passing them is not evidence about any model, annotator, or BAEC theory.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
from pathlib import Path

import pytest

from experiments.engine3_stage_f.readiness import check_pilot_readiness
from experiments.engine3_stage_f_ar import panel
from experiments.engine3_stage_f_ar.panel import (
    AutonomousRunOrder,
    CorrelationAssessment,
    QualificationRecord,
    ReviewerRole,
    check_autonomous_reference_readiness,
    family_correlations,
    panel_sha256,
)
from tests.engine3 import stage_f_ar_adapter as ar
from tests.engine3.test_engine3_stage_f_static_audit import audit

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "engine3"
MANIFEST_PATH = DOCS / "ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_MANIFEST.json"
PROTOCOL_PATH = DOCS / "ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_PROTOCOL.md"
CLAIMS_PATH = DOCS / "ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_CLAIM_BOUNDARY.md"
VERIFIER = REPO / "scripts" / "verify_engine3_stage_f_autonomous_reference.py"
PACKAGE = REPO / "experiments" / "engine3_stage_f_ar"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
HARNESS = json.loads((DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json").read_text(encoding="utf-8"))
O5 = json.loads((DOCS / "ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json").read_text(encoding="utf-8"))
GLOBS = (
    "experiments/engine3_stage_f_ar/*.py",
    "tests/engine3/stage_f_ar_adapter.py",
    "tests/engine3/test_engine3_autonomous_reference_*.py",
    "scripts/verify_engine3_stage_f_autonomous_reference.py",
    "docs/engine3/ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_*.md",
)
AR_ITEMS = {f"AR-PRE-{n}" for n in range(1, 6)}
RULES = "d" * 64  # a stand-in for a future owner-frozen qualification governance record (none exists)
PILOT_STATEMENT = ("Stage F-AR does not execute, constitute, substitute for, or satisfy the Stage F human-reference "
                   "pilot. The term pilot remains reserved for the locked human-reference track. O-3 remains "
                   "PRE_RUN_REQUIRED and human pilot_ready remains false.")
EXPOSURE_STATEMENT = ("All Stage F-AR annotations, disagreements, adjudications and comparison results are labelled "
                      "evaluation-derived material for O-3 exposure purposes.")


@pytest.fixture
def frozen_rules(monkeypatch):
    """Simulates a future owner freeze of qualification governance, to test the rest of AR-PRE-4."""
    monkeypatch.setattr(panel, "FROZEN_QUALIFICATION_RULES_SHA256", RULES)
FROZEN_PANEL = tuple(ar.configuration(c.role, c.model_family, c.model_developer, frozen=True) for c in ar.PANEL)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _items(readiness) -> set[str]:
    return {item for item, _ in readiness.unresolved}


def _complete_records(configs=FROZEN_PANEL):
    digest = panel_sha256(configs)
    return dict(
        panel=configs,
        correlation=CorrelationAssessment(digest, family_correlations(configs), True),
        qualification=QualificationRecord(digest, panel.CALIBRATION_FREEZE_MANIFEST_SHA256, RULES, 1, (), True),
        run_order=AutonomousRunOrder(digest, "a" * 64, True),
    )


# --- human track unchanged ----------------------------------------------------------------------------


def test_human_o3_remains_pre_run_required():
    assert HARNESS["pre_run_state_current"]["status"]["O-3"] == "PRE_RUN_REQUIRED"
    assert HARNESS["pre_run_state_current"]["pilot_readiness_blockers"] == {"O-3": "PRE_RUN_REQUIRED"}
    assert MANIFEST["human_track"] == {"O-3": "PRE_RUN_REQUIRED", "pilot_ready": False, "unchanged": True}
    assert "O-3" in {item for item, _ in check_pilot_readiness().unresolved}


def test_human_pilot_ready_remains_false():
    assert HARNESS["pre_run_state_current"]["pilot_readiness"] is False
    assert O5["pilot_readiness"] is False


def test_locked_stage_f_inputs_are_bound_and_unchanged():
    locked = MANIFEST["locked_inputs_sha256"]
    for rel, expected in locked.items():
        assert _sha256(REPO / rel) == expected, rel
    for rel in ("docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json", "docs/engine3/ENGINE3_STAGE_F_DESIGN_MANIFEST.json",
                "docs/engine3/ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json",
                "docs/engine3/ENGINE3_STAGE_F_O5_FREEZE_MANIFEST.json",
                "docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json",
                "docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md"):
        assert rel in locked
    # The harness manifest still binds every harness file, so the locked harness code is unchanged too.
    for rel, expected in HARNESS["harness_files"].items():
        assert _sha256(REPO / rel) == expected, rel


def test_generator_identity_constants_match_the_frozen_o5_configuration():
    model = O5["frozen_configuration"]["model"]
    assert panel.GENERATOR_MODEL_FAMILY == model["model_family"]
    assert O5["frozen_configuration"]["api"]["api"].startswith(panel.GENERATOR_MODEL_DEVELOPER)
    assert panel.GENERATOR_CONFIGURATION_SHA256 == O5["configuration_sha256"]
    freeze = DOCS / "ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json"
    assert panel.CALIBRATION_FREEZE_MANIFEST_SHA256 == _sha256(freeze)


# --- autonomous readiness ---------------------------------------------------------------------------------


def test_autonomous_readiness_is_false_with_nothing_supplied_and_never_lists_o3():
    readiness = check_autonomous_reference_readiness()
    assert not readiness.preconditions_met and _items(readiness) == AR_ITEMS
    assert readiness.owner_execution_authorization_still_required is True
    assert not any("O-3" in item or "O-3" in problem for item, problem in readiness.unresolved)
    assert MANIFEST["autonomous_reference_ready"] is False
    assert MANIFEST["autonomous_blockers"] == {f"AR-PRE-{n}": "PRE_RUN_REQUIRED" for n in range(1, 7)}
    assert not any("O-3" in key for key in MANIFEST["autonomous_blockers"])


def test_autonomous_readiness_does_not_depend_on_o3():
    assert list(inspect.signature(check_autonomous_reference_readiness).parameters) == [
        "panel", "correlation", "qualification", "run_order"]
    tree = ast.parse((PACKAGE / "panel.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "experiments.engine3_stage_f.readiness" not in imported
    for name in ("panel.py", "review.py", "analysis.py"):
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        assert "experiments.engine3_stage_f.readiness" not in {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    readiness = check_autonomous_reference_readiness(**_complete_records())
    assert _items(readiness) == {"AR-PRE-4"}  # only the unfrozen qualification governance, never O-3
    assert not any("O-3" in problem for _, problem in readiness.unresolved)
    assert "O-3" in {item for item, _ in check_pilot_readiness().unresolved}


def test_autonomous_readiness_stays_false_while_reviewer_configuration_is_not_frozen():
    readiness = check_autonomous_reference_readiness(**_complete_records(ar.PANEL))
    assert not readiness.preconditions_met
    assert {p for item, p in readiness.unresolved if item == "AR-PRE-1"} == {
        f"{c.role.value}: configuration is not frozen" for c in ar.PANEL}


def test_owner_execution_authorization_is_never_satisfied_by_code(frozen_rules):
    readiness = check_autonomous_reference_readiness(**_complete_records())
    assert readiness.preconditions_met and readiness.owner_execution_authorization_still_required is True


def test_reviewer_a_and_b_sharing_a_family_blocks_readiness():
    same = (FROZEN_PANEL[0], ar.configuration(ReviewerRole.REVIEWER_B, "SYNTHETIC FAMILY ALPHA", "Other", frozen=True),
            FROZEN_PANEL[2])
    readiness = check_autonomous_reference_readiness(**_complete_records(same))
    assert ("AR-PRE-1", "Reviewer A and Reviewer B must be different model families") in readiness.unresolved


@pytest.mark.parametrize("change,item", [
    ({"prompt_bundle_sha256": "not-a-digest"}, "AR-PRE-2"),
    ({"output_schema_version": "other"}, "AR-PRE-2"),
    ({"model_id": " "}, "AR-PRE-1"),
    ({"sampling_configuration": ()}, "AR-PRE-1"),
])
def test_each_configuration_problem_maps_to_its_blocker(change, item):
    configs = (ar.configuration(ReviewerRole.REVIEWER_A, "Synthetic Family Alpha", "Synthetic Developer One",
                                frozen=True, **change),) + FROZEN_PANEL[1:]
    readiness = check_autonomous_reference_readiness(**_complete_records(configs))
    assert item in _items(readiness)


def test_records_bound_to_another_panel_or_unfinished_do_not_count(frozen_rules):
    records = _complete_records()
    other = "b" * 64
    stale = dict(records,
                 correlation=CorrelationAssessment(other, (), True),
                 qualification=QualificationRecord(records["qualification"].panel_sha256, "c" * 64, RULES, 1, (),
                                                   True),
                 run_order=AutonomousRunOrder(records["run_order"].panel_sha256, "a" * 64, False))
    assert _items(check_autonomous_reference_readiness(**stale)) == {"AR-PRE-3", "AR-PRE-4", "AR-PRE-5"}
    undisclosed = dict(records, correlation=CorrelationAssessment(records["correlation"].panel_sha256, (), True))
    correlated = (FROZEN_PANEL[0], FROZEN_PANEL[1],
                  ar.configuration(ReviewerRole.AUTONOMOUS_ADJUDICATOR, "Claude Sonnet", "Anthropic", frozen=True))
    hidden = dict(_complete_records(correlated),
                  correlation=CorrelationAssessment(panel_sha256(correlated), (), True))
    assert _items(check_autonomous_reference_readiness(**undisclosed)) == set()  # this panel has no correlation
    assert _items(check_autonomous_reference_readiness(**hidden)) == {"AR-PRE-3"}


def test_repository_supplies_no_autonomous_readiness_record():
    records = {"ReviewerConfiguration", "CorrelationAssessment", "QualificationRecord", "AutonomousRunOrder"}
    for path in sorted((REPO / "experiments").rglob("*.py")) + sorted((REPO / "scripts").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in records, path.relative_to(REPO).as_posix()
    assert MANIFEST["reviewer_model_ids_selected"] is False


# --- qualification governance (AR-PRE-4) ---------------------------------------------------------------------


def test_ar_pre_4_stays_unresolved_while_qualification_governance_is_not_frozen():
    assert panel.FROZEN_QUALIFICATION_RULES_SHA256 is None
    readiness = check_autonomous_reference_readiness(**_complete_records())
    assert [p for item, p in readiness.unresolved if item == "AR-PRE-4"] == [
        "qualification governance is not frozen (metrics, acceptance/refusal criteria, structural failure, "
        "semantic disagreement, disqualification, rerun rules)"]
    governance = MANIFEST["qualification_governance"]
    assert governance["status"] == "NOT_FROZEN" and governance["thresholds"] is None
    assert governance["must_freeze_before_qualification"] == [
        "qualification metrics", "qualification acceptance/refusal criteria", "what constitutes structural failure",
        "what constitutes semantic disagreement for qualification",
        "whether qualification can disqualify a configuration", "rules for rerunning qualification"]


def test_qualification_must_run_under_the_frozen_rules(frozen_rules):
    records = _complete_records()
    other = QualificationRecord(records["qualification"].panel_sha256, panel.CALIBRATION_FREEZE_MANIFEST_SHA256,
                                "e" * 64, 1, (), True)
    readiness = check_autonomous_reference_readiness(**dict(records, qualification=other))
    assert ("AR-PRE-4", "reviewer qualification was not run under the frozen qualification rules") in readiness.unresolved


def test_every_prior_qualification_attempt_must_be_preserved(frozen_rules):
    records = _complete_records()
    first = records["qualification"]
    second = QualificationRecord(first.panel_sha256, first.calibration_freeze_manifest_sha256, RULES, 2,
                                 (first.attempt_sha256,), True)
    assert check_autonomous_reference_readiness(**dict(records, qualification=second)).preconditions_met
    dropped = QualificationRecord(first.panel_sha256, first.calibration_freeze_manifest_sha256, RULES, 2, (), True)
    readiness = check_autonomous_reference_readiness(**dict(records, qualification=dropped))
    assert ("AR-PRE-4", "every prior qualification attempt must be preserved and named by hash") in readiness.unresolved


@pytest.mark.parametrize("change", [
    {"model_id": "synthetic-other-model"}, {"model_version": "synthetic-v1"},
    {"prompt_bundle_sha256": hashlib.sha256(b"changed annotation instructions").hexdigest()},
    {"output_schema_version": "engine3.stage_f_ar.reviewer_output.v2"},
    {"sampling_configuration": (("temperature", "0"),)},
], ids=["model", "version", "instructions", "schema", "sampling"])
def test_material_change_is_a_new_configuration_version_and_needs_a_new_qualification(change, frozen_rules):
    records = _complete_records()
    for index in range(3):  # reviewer A, reviewer B, and adjudicator (adjudication instructions)
        changed_config = ar.configuration(FROZEN_PANEL[index].role, FROZEN_PANEL[index].model_family,
                                          FROZEN_PANEL[index].model_developer, frozen=True, **change)
        assert changed_config.configuration_sha256 != FROZEN_PANEL[index].configuration_sha256
        changed = FROZEN_PANEL[:index] + (changed_config,) + FROZEN_PANEL[index + 1:]
        readiness = check_autonomous_reference_readiness(**dict(records, panel=changed))
        assert ("AR-PRE-4", "reviewer qualification is not bound to this exact reviewer panel") in readiness.unresolved


def test_qualification_governance_is_documented():
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8")
    for text in ("reviewer qualification diagnostic, not an evaluation result and not a continuously reusable "
                 "tuning set", "Preserve every prior qualification attempt", "new reviewer-configuration version",
                 "AR-PRE-4 stays unresolved"):
        assert text in " ".join(protocol.split()), text


# --- reserved term and exposure -------------------------------------------------------------------------------


def test_required_pilot_statement_appears_verbatim():
    assert MANIFEST["human_pilot_statement"] == PILOT_STATEMENT
    for path in (PROTOCOL_PATH, CLAIMS_PATH):
        assert PILOT_STATEMENT in " ".join(path.read_text(encoding="utf-8").split())


def _strings(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_pilot_is_never_used_for_an_autonomous_run():
    """'pilot' appears only for the human track, or as the locked harness identifier RunKind.PILOT."""
    for rel in MANIFEST["ar_files_sha256"]:
        if rel.endswith("_governance.py"):
            continue  # this file names the rule it enforces
        for line in (REPO / rel).read_text(encoding="utf-8").splitlines():
            lowered = line.casefold()
            if "pilot" in lowered:
                assert "human" in lowered or "RunKind.PILOT" in line, f"{rel}: {line}"
    for key, value in _strings(MANIFEST):
        for text in (key, value if isinstance(value, str) else ""):
            if "pilot" in text.casefold():
                assert "human" in text.casefold() or key in ("pilot_ready", "human_track"), (key, text)


def test_exposure_rule_is_recorded():
    assert MANIFEST["o3_exposure"]["statement"] == EXPOSURE_STATEMENT
    assert MANIFEST["o3_exposure"]["repository_access_alone_is_exposure"] is False
    for path in (PROTOCOL_PATH, CLAIMS_PATH):
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert EXPOSURE_STATEMENT in text
        assert "must not be shown Stage F-AR labelled outputs before their eligibility is determined" in text
        assert "Repository access alone does not establish exposure" in text


def test_ar_code_never_reads_or_writes_human_track_readiness():
    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for text in ("HARNESS_MANIFEST", "pre_run_state_current", "check_pilot_readiness", "AnnotatorStaffing"):
            assert text not in source, (path.name, text)


# --- manifest -------------------------------------------------------------------------------------------------


def test_every_stage_f_ar_file_hash_matches_the_manifest():
    for rel, expected in MANIFEST["ar_files_sha256"].items():
        assert _sha256(REPO / rel) == expected, rel


def test_manifest_covers_every_stage_f_ar_file_and_not_itself():
    found = {p.relative_to(REPO).as_posix() for pattern in GLOBS for p in REPO.glob(pattern)}
    assert set(MANIFEST["ar_files_sha256"]) == found
    assert MANIFEST_PATH.relative_to(REPO).as_posix() not in MANIFEST["ar_files_sha256"]


def test_stage_f_ar_files_stay_outside_the_locked_harness_coverage():
    """New files must not match the harness manifest's coverage globs, so the locked harness is untouched."""
    harness_globs = ("experiments/engine3_stage_f/*.py", "tests/engine3/stage_f_adapter.py",
                     "tests/engine3/test_engine3_stage_f_*.py", "tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_*.json")
    harness_found = {p.relative_to(REPO).as_posix() for pattern in harness_globs for p in REPO.glob(pattern)}
    assert harness_found.isdisjoint(MANIFEST["ar_files_sha256"])
    assert set(HARNESS["harness_files"]).isdisjoint(MANIFEST["ar_files_sha256"])


def test_no_execution_record():
    assert MANIFEST["status"] == "DESIGNED_OFFLINE_NOT_EXECUTED"
    assert MANIFEST["execution_record"] == {
        "live_model_executed": False, "live_model_authorized": False, "api_key_used": False, "network_calls": 0,
        "ar_44_initial_autonomous_execution_batch_executed": False, "ar_full_run_executed": False,
        "qualification_executed": False,
        "autonomous_results_exist": False, "outreach_authorized": False}


def test_manifest_records_labels_provenance_and_namespaces():
    assert MANIFEST["reference_labels"] == ["AUTONOMOUS_CONSENSUS_REFERENCE", "AUTONOMOUS_ADJUDICATED_REFERENCE"]
    assert MANIFEST["reviewer_provenance_values"] == {
        "AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH": "AUTONOMOUS_SYNTHETIC_REFERENCE",
        "AR_FULL_RUN": "AUTONOMOUS_SYNTHETIC_REFERENCE", "OFFLINE_DRY_RUN": "OFFLINE_HANDWRITTEN_AUTONOMOUS_DRY_RUN"}
    assert MANIFEST["result_namespace"] == "engine3_stage_f_autonomous_reference"
    assert MANIFEST["decision_rules"] == ["AR-A", "AR-B", "AR-C", "AR-D", "AR-E", "AR-F", "AR-G"]
    assert MANIFEST["human_decision_rules_a_to_g_modified"] is False


# --- offline audit ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("module", sorted(p.stem for p in PACKAGE.glob("*.py")))
def test_stage_f_ar_module_passes_the_stage_f_static_audit(module):
    """The locked harness audit, allowing only imports of the locked Stage F harness itself."""
    violations = audit((PACKAGE / f"{module}.py").read_text(encoding="utf-8"), "harness")
    assert [v for v in violations
            if not (v[0] == "IMPORT_NOT_ALLOWED" and v[1].startswith("experiments.engine3_stage_f."))] == []


def test_stage_f_ar_verifier_passes_the_audit():
    assert audit(VERIFIER.read_text(encoding="utf-8"), "verifier") == []


def test_stage_f_ar_package_is_not_imported_by_production_or_the_locked_harness():
    for path in sorted((REPO / "baec_app").rglob("*.py")) + sorted((REPO / "experiments/engine3_stage_f").glob("*.py")):
        assert "engine3_stage_f_ar" not in path.read_text(encoding="utf-8"), path


# --- claims -----------------------------------------------------------------------------------------------------


def test_governing_claim_is_identical_everywhere():
    claim = MANIFEST["governing_claim"]
    assert claim.startswith("Stage F autonomous-reference execution measures the generating model's semantic")
    assert claim.endswith("are not human reference labels, human ground truth, or evidence of human semantic "
                          "reliability.")
    for path in (PROTOCOL_PATH, CLAIMS_PATH):
        assert f"> {claim}" in path.read_text(encoding="utf-8").splitlines()


def test_permitted_claims_always_qualify_reference_as_autonomous():
    for claim in MANIFEST["permitted_claims"]:
        assert "human" not in claim.casefold() and "ground truth" not in claim.casefold()
        if "reference" in claim.casefold() or "consensus" in claim.casefold():
            assert "autonomous" in claim.casefold()


def test_prohibited_claims_are_recorded_and_never_permitted():
    prohibited = MANIFEST["prohibited_claims"]
    for claim in ("The model was X% accurate relative to humans.", "The autonomous reference is ground truth.",
                  "The system achieved human-level semantic reliability.",
                  "The experiment establishes annotation accuracy.", "Zero autonomous false passes proves safety."):
        assert claim in prohibited
        assert f'"{claim}"' in CLAIMS_PATH.read_text(encoding="utf-8")
    assert set(prohibited).isdisjoint(MANIFEST["permitted_claims"])


_NEGATION = re.compile(r"(?i)\b(not|never|no|nor|prohibited|nothing)\b")


@pytest.mark.parametrize("path", [PROTOCOL_PATH, CLAIMS_PATH], ids=lambda p: p.name)
def test_human_reference_terms_appear_only_in_negated_or_separating_statements(path):
    """Every line using 'ground truth', 'gold standard', or 'inter-rater' does so to deny or separate it."""
    lines = path.read_text(encoding="utf-8").splitlines()
    in_prohibited_list = False
    for line in lines:
        if line.startswith("#"):
            in_prohibited_list = "Prohibited" in line or "Terminology" in line
            continue
        lowered = line.casefold()
        if any(term in lowered for term in ("ground truth", "gold standard", "inter-rater")):
            assert in_prohibited_list or _NEGATION.search(line), line
