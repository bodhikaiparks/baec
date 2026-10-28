"""Verify the locked BAEC Engine 3 Stage F design (LOCKED_DESIGN_NO_LIVE_EXECUTION).

Offline only: no network call, no API key, no model. Checks the Stage F design artifact
hashes, validates the experiment spec, checks the locked fields, thresholds, arm scopes,
and pre-run items against values fixed in this script, confirms no live execution is
recorded, and independently confirms the locked Stage D and Stage E artifacts are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "engine3"
MANIFEST = DOCS / "ENGINE3_STAGE_F_DESIGN_MANIFEST.json"
SPEC = DOCS / "ENGINE3_STAGE_F_EXPERIMENT_SPEC.json"
STAGE_E_MANIFEST = DOCS / "ENGINE3_STAGE_E_IMPLEMENTATION_MANIFEST.json"
STAGE_D_MANIFEST = DOCS / "ENGINE3_STAGE_D_IMPLEMENTATION_MANIFEST.json"

STATUS = "LOCKED_DESIGN_NO_LIVE_EXECUTION"
LOCK_DATE = "2026-10-14"
BASE_COMMIT = "d00355f439967dcab8044497a8165d360f81fb61"

MODEL_CONTROLLED_FIELDS = (
    "claim_type",
    "semantic_class",
    "grounding_refs",
    "reference_use_mode",
    "source_identity_refs",
    "claim_field",
    "declared_value",
    "specificity",
    "question_has_premise",
    "source_instruction_ref",
    "prescribes_buyer_choice",
)
EXCLUDED_FIELDS = ("origin", "audience")

PLANNED_SIZE = {"arm_g_proposals": 220, "pilot_proposals": 44, "arm_a_annotation_runs": 240}

THRESHOLDS = {
    "A": {"confirmed_artifact_false_pass_min": 1},
    "B": {"confirmed_false_pass_count": 0, "min_reference_refused_artifacts": 60,
          "min_represented_scenario_families": 8},
    "B_prime": {"confirmed_false_pass_count": 0, "reference_refused_artifacts_below": 60,
                "or_represented_scenario_families_below": 8},
    "C": {"model_vs_adjudicated_exact_agreement_below": 0.95, "min_scorable_instances": 30,
          "or_field_contributes_to_any_confirmed_false_pass": True},
    "D": {"share_of_human_safe_reference_complete_artifacts_with_any_field_disagreement_at_least": 0.10},
    "E": {"share_of_structurally_valid_arm_g_artifacts_d_ref_refused_due_to_prose_at_least": 0.05},
    "F": {"confirmed_cases_h_unacceptable_with_final_labels_and_d_ref_allowed_min": 1, "numeric_threshold": None},
    "G": {"pre_adjudication_inter_annotator_exact_agreement_below": 0.90, "min_instances_both_scorable": 30},
}

ARM_SCOPE = {
    "A": ["G"], "B": ["G"], "B_prime": ["G"], "E": ["G"],
    "C": ["G", "A"], "D": ["G", "A"], "F": ["G", "A"], "G": ["G", "A"],
}

PRE_RUN_REQUIRED = ("O-3", "O-5", "O-6")

# No-execution invariants. Each must hold in both the spec (authorization) and the manifest.
NO_EXECUTION = {
    "live_model_executed": False,
    "api_key_used": False,
    "external_model_sdk_used": False,
    "network_calls": 0,
    "pilot_executed": False,
    "main_experiment_executed": False,
}

# Source text of this verifier and of the Engine 3 package must not import a model SDK or network client.
_NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:anthropic|openai|requests|httpx|aiohttp|urllib|http\.client|socket)\b",
    re.MULTILINE,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_hashes(expected: dict[str, str], label: str, failures: list[str]) -> None:
    for rel, digest in expected.items():
        path = REPO / rel
        if not path.exists():
            failures.append(f"{label}: missing {rel}")
        elif _sha256(path) != digest:
            failures.append(f"{label}: hash mismatch {rel}")


def _check_no_execution(record: dict, label: str, failures: list[str]) -> None:
    for key, value in NO_EXECUTION.items():
        if key not in record or type(record[key]) is not type(value) or record[key] != value:
            failures.append(f"{label}: {key} must be {value!r}")


def _check_manifest(manifest: dict, failures: list[str]) -> None:
    expected = {
        "status": STATUS,
        "lock_date": LOCK_DATE,
        "stage_e_base_commit": BASE_COMMIT,
        "live_model_executed": False,
        "live_model_authorized": False,
        "pilot_executed": False,
        "main_experiment_executed": False,
        "planned_size": PLANNED_SIZE,
        "model_controlled_fields": list(MODEL_CONTROLLED_FIELDS),
        "deterministic_exclusions": list(EXCLUDED_FIELDS),
        "decision_thresholds": THRESHOLDS,
        "decision_rule_arm_scope": ARM_SCOPE,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            failures.append(f"manifest: {key} is not the locked value")
    _check_no_execution(manifest, "manifest", failures)
    items = {item.get("id"): item.get("status") for item in manifest.get("pre_run_required", [])}
    if items != {item_id: "PRE_RUN_REQUIRED" for item_id in PRE_RUN_REQUIRED}:
        failures.append("manifest: pre_run_required must be exactly O-3, O-5, O-6 marked PRE_RUN_REQUIRED")


def _check_spec(spec: dict, failures: list[str]) -> None:
    if spec.get("status") != STATUS or spec.get("lock_date") != LOCK_DATE:
        failures.append("spec: status or lock date is not the locked value")
    if spec.get("base_commit") != BASE_COMMIT:
        failures.append("spec: base commit is not the Stage E base commit")

    fields = tuple(f.get("field") for f in spec.get("evaluated_annotation_fields", []))
    if fields != MODEL_CONTROLLED_FIELDS:
        failures.append("spec: evaluated fields are not exactly the 11 model-controlled fields")
    excluded = spec.get("excluded_deterministic_fields", {})
    if tuple(excluded) != EXCLUDED_FIELDS:
        failures.append("spec: origin and audience must be the deterministic exclusions")
    if set(fields) & set(excluded):
        failures.append("spec: an excluded field is also evaluated")

    rules = spec.get("decision_rules", {})
    by_id = {rule.get("id"): rule.get("condition", {}) for rule in rules.get("rules", [])}
    if set(by_id) != set(THRESHOLDS):
        failures.append("spec: decision rules are not exactly A, B, B_prime, C, D, E, F, G")
    for rule_id, thresholds in THRESHOLDS.items():
        condition = by_id.get(rule_id, {})
        for key, value in thresholds.items():
            if key not in condition or condition[key] != value:
                failures.append(f"spec: rule {rule_id} {key} is not the locked value")
    scope = rules.get("arm_scope", {})
    for rule_id, arms in ARM_SCOPE.items():
        if scope.get(rule_id, {}).get("arms") != arms:
            failures.append(f"spec: rule {rule_id} arm scope is not the locked value")
    for rule_id in ("C", "D"):
        if scope.get(rule_id, {}).get("pooled") is not False:
            failures.append(f"spec: rule {rule_id} must not pool arms")
    if scope.get("F", {}).get("either_arm_sufficient") is not True or scope.get("F", {}).get("arm_g_override") is not False:
        failures.append("spec: rule F must be satisfied by either arm with no Arm G override")
    if scope.get("G", {}).get("pooled_to_reach_minimum") is not False:
        failures.append("spec: rule G must not pool arms to reach the minimum")
    insufficient = rules.get("insufficient_instances_rule", {})
    if insufficient.get("label") != "INSUFFICIENT_INSTANCES" or insufficient.get("min_instances_per_arm") != 30:
        failures.append("spec: INSUFFICIENT_INSTANCES rule is not the locked value")
    if rules.get("composite_score") is not False or rules.get("may_change_after_results") is not False:
        failures.append("spec: decision rules must forbid composite scores and post-result changes")

    items = {item.get("id"): item.get("status") for item in spec.get("pre_run_required", [])}
    if items != {item_id: "PRE_RUN_REQUIRED" for item_id in PRE_RUN_REQUIRED}:
        failures.append("spec: pre_run_required must be exactly O-3, O-5, O-6 marked PRE_RUN_REQUIRED")
    if spec.get("pre_run_required_may_be_selected_after_viewing_main_results") is not False:
        failures.append("spec: pre-run items must not be selectable after main results")

    sizes = {
        "arm_g_proposals": spec["arms"][0].get("proposals_full_run"),
        "pilot_proposals": spec["arms"][0].get("pilot", {}).get("proposals"),
        "arm_a_annotation_runs": spec["arms"][1].get("annotation_runs"),
    }
    if sizes != PLANNED_SIZE:
        failures.append("spec: planned sizes are not the locked values")

    auth = spec.get("authorization", {})
    _check_no_execution(auth, "spec: authorization", failures)
    for key in ("live_model_authorized", "live_results_exist", "stage_a_to_e_files_modified"):
        if auth.get(key) is not False:
            failures.append(f"spec: authorization.{key} must be false")


def _check_locked_stages(manifest: dict, spec: dict, failures: list[str]) -> int:
    if _sha256(STAGE_E_MANIFEST) != manifest.get("stage_e_manifest_sha256"):
        failures.append("Stage E manifest changed")
        return 0
    stage_e = _load(STAGE_E_MANIFEST)
    _check_hashes(stage_e["stage_e_files"], "Stage E", failures)
    if _sha256(STAGE_D_MANIFEST) != stage_e["stage_d_manifest_sha256"]:
        failures.append("Stage D manifest changed")
        return 0
    stage_d = _load(STAGE_D_MANIFEST)
    locked: dict[str, str] = {}
    for group in ("locked_artifact_hashes", "production_files", "test_files"):
        locked.update(stage_d[group])
    _check_hashes(locked, "Stage D", failures)
    _check_hashes(spec["locked_inputs_sha256"], "Stage A-E input", failures)
    return len(stage_e["stage_e_files"]) + len(locked) + len(spec["locked_inputs_sha256"])


def _check_no_network_code(failures: list[str]) -> None:
    paths = sorted((REPO / "baec_app" / "engine3").glob("*.py")) + [Path(__file__).resolve()]
    for path in paths:
        if _NETWORK_IMPORT.search(path.read_text(encoding="utf-8")):
            failures.append(f"model SDK or network import found: {path.relative_to(REPO)}")


def main() -> int:
    failures: list[str] = []
    manifest = _load(MANIFEST)
    print(f"Stage F status: {manifest.get('status')} (manifest sha256 {_sha256(MANIFEST)})")

    _check_hashes(manifest.get("design_artifacts_sha256", {}), "Stage F design", failures)
    if len(manifest.get("design_artifacts_sha256", {})) != 3:
        failures.append("Stage F design: manifest must hash exactly three design artifacts")
    if _sha256(Path(__file__).resolve()) != manifest.get("verifier_sha256"):
        failures.append("Stage F design: verifier hash mismatch")
    _check_manifest(manifest, failures)

    try:
        spec = _load(SPEC)
    except json.JSONDecodeError as exc:
        print(f"FAIL  spec is not valid JSON: {exc}")
        return 1
    _check_spec(spec, failures)

    locked_count = _check_locked_stages(manifest, spec, failures)
    _check_no_network_code(failures)

    if failures:
        for failure in failures:
            print("FAIL ", failure)
        return 1
    print("PASS  Stage F design artifact hashes: 3 (and verifier hash)")
    print("PASS  Experiment spec is valid JSON")
    print(f"PASS  Model-controlled fields: {len(MODEL_CONTROLLED_FIELDS)}; excluded: {', '.join(EXCLUDED_FIELDS)}")
    print("PASS  Decision thresholds A-G and arm scopes match locked values")
    print(f"PASS  PRE_RUN_REQUIRED: {', '.join(PRE_RUN_REQUIRED)}")
    print("PASS  No-execution invariants hold in spec and manifest: " + ", ".join(NO_EXECUTION))
    print(f"PASS  Locked Stage A-E artifacts unchanged (hash checks): {locked_count}")
    print("PASS  No model SDK or network imports in Engine 3 package or this verifier")
    print("LIMIT E3-E-SEM-001 MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE remains NOT RESOLVED. Stage F is a")
    print("      locked experiment design only; it reports no results and authorizes no live execution or outreach.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
