"""Verify the locked BAEC Engine 3 Stage F experimental harness (LOCKED_OFFLINE_HARNESS_NOT_EXECUTED).

Offline only: no model, no network call, no key. Confirms the locked Stage F design
artifacts and the locked Stage A-E artifacts are unchanged, checks the harness manifest
hashes and coverage, checks that dry-run inputs and the oracle stay separate, checks the
no-execution record, that O-3, O-5, and O-6 are recorded as PRE_RUN_REQUIRED at harness lock,
and the current pre-run state (O-3 unresolved; O-5 and O-6 RESOLVED_FROZEN, each bound to its
exact freeze records; the calibration contamination blocker RESOLVED_FROZEN, bound to the exact
calibration freeze records), then runs the harness tests, which include the static audit. Separate
from scripts/verify_engine3_stage_f_design.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "engine3"
HARNESS_MANIFEST = DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json"
DESIGN_MANIFEST = DOCS / "ENGINE3_STAGE_F_DESIGN_MANIFEST.json"
SPEC = DOCS / "ENGINE3_STAGE_F_EXPERIMENT_SPEC.json"
STAGE_E_MANIFEST = DOCS / "ENGINE3_STAGE_E_IMPLEMENTATION_MANIFEST.json"
STAGE_D_MANIFEST = DOCS / "ENGINE3_STAGE_D_IMPLEMENTATION_MANIFEST.json"
DESIGN_VERIFIER = REPO / "scripts" / "verify_engine3_stage_f_design.py"
HARNESS_DIR = REPO / "experiments" / "engine3_stage_f"
INPUTS = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_INPUTS.json"
O6_FREEZE_MANIFEST = "docs/engine3/ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json"
ORACLE = REPO / "tests" / "engine3" / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_ORACLE.json"

STATUS = "LOCKED_OFFLINE_HARNESS_NOT_EXECUTED"
LOCK_DATE = "2026-10-14"
NO_EXECUTION = {
    "live_model_executed": False,
    "api_key_used": False,
    "external_model_sdk_used": False,
    "network_calls": 0,
    "pilot_executed": False,
    "main_experiment_executed": False,
}
PRE_RUN_REQUIRED = ("O-3", "O-5", "O-6")  # state at harness lock (2026-10-14)
# Current state after later preregistration work. O-6 was resolved by the Arm A freeze, O-5 by the O-5 freeze
# (recorded here by HARNESS_AMENDMENT_003).
PRE_RUN_CURRENT = {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
# Superseded current-state snapshot, kept as history (O-5 had already been frozen when it was last written).
PRE_RUN_SUPERSEDED = {"O-3": "PRE_RUN_REQUIRED", "O-5": "PRE_RUN_REQUIRED", "O-6": "RESOLVED_FROZEN"}
# State recorded by the O-6 freeze manifest on its own date. Historical; not the current state.
PRE_RUN_AT_O6_FREEZE = PRE_RUN_SUPERSEDED
# O-5 current status is bound to these exact records (path -> SHA-256).
O5_BOUND = {
    "freeze_manifest": ("docs/engine3/ENGINE3_STAGE_F_O5_FREEZE_MANIFEST.json",
                        "4a28ed081ab4b5a9899ad3888cb695f7e366491302ff37a66134897f912355e3"),
    "frozen_configuration": ("docs/engine3/ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json",
                             "e74c4a46fd771c12e48ab78f492026f97250a2d0a90ee49158ee544fb6c86237"),
    "postfreeze_rebinding_001": ("docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json",
                                 "76119f1bcfc0096c2abf595d2e21002ab7bcbb5aa2a1d1efee7161cb6e0f9ba4"),
}
O5_CONFIGURATION_SHA256 = "5ec4f582d2a05e8fe2768e9030e8eadf1315c1cced89d5acc0de1916e28e7f21"
# Binds this manifest's hash, so the manifest can name it only by path. The O-5 verifier checks it exactly.
O5_REBINDING_002 = "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json"
O3_ADDENDA = {
    "eligibility_addendum_001": ("docs/engine3/ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM.md",
                                 "b66e57b5c546346c031913981091cce21c6b00863b633b62143cdedfad43632f"),
    "eligibility_addendum_002": ("docs/engine3/ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md",
                                 "9490a7213625a3e2e13cae1b98b57c47223ac60e56e69ff8f51af4f1ce72f5bd"),
}
PILOT_BLOCKERS = {"O-3": "PRE_RUN_REQUIRED"}
# Blockers in the snapshot HARNESS_AMENDMENT_004 superseded. Historical.
PILOT_BLOCKERS_SUPERSEDED = {"O-3": "PRE_RUN_REQUIRED", "CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED": "OPEN"}
# HARNESS_AMENDMENT_004: the calibration blocker is RESOLVED_FROZEN, bound to these exact records (path -> SHA-256).
CALIBRATION_BOUND = {
    "freeze_manifest": ("docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json",
                        "a88600bb392f9d853b07c8948ee41ce8af597f5a3025697c56dc5e6e0fefef97"),
    "corpus": ("docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json",
               "83e5843d10a575792379fc4f1e6b3f0570ff8f951d272889478380b03a317c46"),
    "guide": ("docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_GUIDE.md",
              "5a1d83cbc0082b0a29c2f042854135efb6a8e0d0d2e8877f2d1a5d9a8a591584"),
}
# Post-lock amendments: id -> (date, record path). Each must be recorded in the manifest with the record's hash.
AMENDMENTS = {
    "HARNESS_AMENDMENT_001": ("2026-10-15", "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_001.md"),
    "HARNESS_AMENDMENT_002": ("2026-10-15", "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_002.md"),
    "HARNESS_AMENDMENT_003": ("2026-10-15", "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_003.md"),
    "HARNESS_AMENDMENT_004": ("2026-10-15", "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_004.md"),
}
HARNESS_GLOBS = (
    "experiments/engine3_stage_f/*.py",
    "tests/engine3/stage_f_adapter.py",
    "tests/engine3/test_engine3_stage_f_*.py",
    "tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_*.json",
    "scripts/verify_engine3_stage_f_harness.py",
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md",
)

# Independent cross-check of the static audit: no model SDK or network client import.
_NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:anthropic|openai|mcp|requests|httpx|aiohttp|urllib|http\.client|socket|smtplib)\b",
    re.MULTILINE,
)
_TEST_IMPORT = re.compile(r"^\s*(?:import|from)\s+tests\b", re.MULTILINE)


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


def _check_locked(harness: dict, failures: list[str]) -> int:
    if _sha256(DESIGN_MANIFEST) != harness.get("stage_f_design_manifest_sha256"):
        failures.append("Stage F design manifest changed")
        return 0
    design = _load(DESIGN_MANIFEST)
    _check_hashes(design["design_artifacts_sha256"], "Stage F design", failures)
    if _sha256(DESIGN_VERIFIER) != design["verifier_sha256"]:
        failures.append("Stage F design verifier changed")
    if _sha256(STAGE_E_MANIFEST) != design["stage_e_manifest_sha256"]:
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
    spec_inputs = _load(SPEC)["locked_inputs_sha256"]
    _check_hashes(spec_inputs, "Stage A-E input", failures)
    return len(design["design_artifacts_sha256"]) + 1 + len(stage_e["stage_e_files"]) + len(locked) + len(spec_inputs)


def _check_manifest(harness: dict, failures: list[str]) -> None:
    if harness.get("status") != STATUS or harness.get("lock_date") != LOCK_DATE:
        failures.append(f"harness manifest: status must be {STATUS} locked {LOCK_DATE}")
    for key, value in NO_EXECUTION.items():
        if key not in harness or type(harness[key]) is not type(value) or harness[key] != value:
            failures.append(f"harness manifest: {key} must be {value!r}")
    limitation = harness.get("governing_limitation", {})
    if limitation.get("id") != "E3-E-SEM-001" or limitation.get("resolved") is not False:
        failures.append("harness manifest: E3-E-SEM-001 must remain unresolved")
    items = {i.get("id"): i.get("status") for i in harness.get("pre_run_required", [])}
    if (items != {i: "PRE_RUN_REQUIRED" for i in PRE_RUN_REQUIRED}
            or harness.get("o6_status_at_harness_lock") != "PRE_RUN_REQUIRED"
            or harness.get("o5_status_at_harness_lock") != "PRE_RUN_REQUIRED"):
        failures.append("harness manifest: lock-time O-3, O-5, O-6 must remain recorded as PRE_RUN_REQUIRED")
    superseded = harness.get("pre_run_state_current_superseded", [])
    if ([(x.get("superseded_by"), x.get("status")) for x in superseded]
            != [("HARNESS_AMENDMENT_003", PRE_RUN_SUPERSEDED), ("HARNESS_AMENDMENT_004", PRE_RUN_CURRENT)]
            or superseded[1].get("calibration_contamination_audit") != "OPEN"
            or superseded[1].get("pilot_readiness_blockers") != PILOT_BLOCKERS_SUPERSEDED):
        failures.append("harness manifest: the superseded current-state snapshots must be kept as history")
    _check_current_pre_run(harness, failures)
    if harness.get("pre_run_records_supplied") is not False:
        failures.append("harness manifest: no pre-run record may be recorded as supplied")
    recorded = {a.get("id"): a for a in harness.get("harness_amendments", [])}
    if set(recorded) != set(AMENDMENTS):
        failures.append(f"harness manifest: amendments must be exactly {sorted(AMENDMENTS)}")
    for amendment_id, (date, record) in AMENDMENTS.items():
        entry = recorded.get(amendment_id, {})
        if entry.get("applied") is not True or entry.get("date") != date or entry.get("record") != record:
            failures.append(f"harness manifest: {amendment_id} must be applied on {date} with record {record}")
        _check_hashes({record: entry.get("record_sha256")}, f"amendment {amendment_id}", failures)
    files = harness.get("harness_files", {})
    _check_hashes(files, "harness", failures)
    found = {p.relative_to(REPO).as_posix() for pattern in HARNESS_GLOBS for p in REPO.glob(pattern)}
    if set(files) != found:
        failures.append(f"harness manifest coverage: unlisted={sorted(found - set(files))} "
                        f"missing={sorted(set(files) - found)}")
    if HARNESS_MANIFEST.relative_to(REPO).as_posix() in files:
        failures.append("harness manifest must not hash itself")


def _check_current_pre_run(harness: dict, failures: list[str]) -> None:
    current = harness.get("pre_run_state_current", {})
    if (current.get("status") != PRE_RUN_CURRENT or harness.get("o6_current_status") != "RESOLVED_FROZEN"
            or harness.get("o5_current_status") != "RESOLVED_FROZEN"):
        failures.append(f"harness manifest: current pre-run state must be {PRE_RUN_CURRENT}")
    if (current.get("pilot_readiness") is not False or current.get("pilot_readiness_blockers") != PILOT_BLOCKERS
            or current.get("annotation_record_provenance_hashes") != "RESOLVED"
            or current.get("calibration_contamination_audit") != "RESOLVED_FROZEN"
            or current.get("updated_by") != "HARNESS_AMENDMENT_004"):
        failures.append(f"harness manifest: pilot readiness must be false with blockers {PILOT_BLOCKERS}")
    _check_current_calibration(current.get("calibration", {}), failures)
    _check_current_o5(current.get("o5", {}), failures)
    _check_current_o3(current.get("o3", {}), failures)
    if current.get("o6_freeze_manifest") != O6_FREEZE_MANIFEST:
        failures.append(f"harness manifest: O-6 current status must reference {O6_FREEZE_MANIFEST}")
        return
    _check_hashes({O6_FREEZE_MANIFEST: current.get("o6_freeze_manifest_sha256")}, "O-6 freeze manifest", failures)
    freeze = _load(REPO / O6_FREEZE_MANIFEST)
    if freeze.get("status") != "O6_RESOLVED_FROZEN" or {
            i.get("id"): i.get("status") for i in freeze.get("pre_run_required", [])} != PRE_RUN_AT_O6_FREEZE:
        failures.append("O-6 freeze manifest does not record O-6 RESOLVED_FROZEN with O-3 and O-5 unresolved at its date")
    if current.get("o6_frozen_file") != freeze.get("frozen_file"):
        failures.append("harness manifest: O-6 frozen file does not match the freeze manifest")
    _check_hashes({freeze.get("frozen_file", ""): freeze.get("frozen_file_sha256")}, "O-6 frozen corpus", failures)


def _check_current_calibration(calibration: dict, failures: list[str]) -> None:
    """HARNESS_AMENDMENT_004: calibration blocker RESOLVED_FROZEN, bound to the exact calibration freeze records."""
    for key, (rel, digest) in CALIBRATION_BOUND.items():
        if calibration.get(key) != rel or calibration.get(f"{key}_sha256") != digest:
            failures.append(f"harness manifest: calibration {key} must be {rel} at {digest}")
        _check_hashes({rel: digest}, f"calibration {key}", failures)
    freeze_rel = CALIBRATION_BOUND["freeze_manifest"][0]
    if not (REPO / freeze_rel).exists():
        return
    freeze = _load(REPO / freeze_rel)
    if (freeze.get("status") != "CALIBRATION_CORPUS_FROZEN"
            or freeze.get("blocker") != "CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED"
            or freeze.get("blocker_status") != "RESOLVED_FROZEN"
            or freeze.get("corpus_sha256") != CALIBRATION_BOUND["corpus"][1]
            or freeze.get("guide_sha256") != CALIBRATION_BOUND["guide"][1]
            or freeze.get("pre_run_status") != PRE_RUN_CURRENT or freeze.get("pilot_readiness") is not False
            or freeze.get("calibration_responses_collected") is not False
            or freeze.get("contamination_audit", {}).get("passed") is not True):
        failures.append("calibration freeze manifest does not show a passed audit, RESOLVED_FROZEN, O-3 unresolved, "
                        "pilot readiness false, and no responses collected")


def _check_current_o5(o5: dict, failures: list[str]) -> None:
    """HARNESS_AMENDMENT_003: O-5 RESOLVED_FROZEN, bound to the exact freeze records."""
    for key, (rel, digest) in O5_BOUND.items():
        if o5.get(key) != rel or o5.get(f"{key}_sha256") != digest:
            failures.append(f"harness manifest: O-5 {key} must be {rel} at {digest}")
        _check_hashes({rel: digest}, f"O-5 {key}", failures)
    frozen_rel, freeze_rel = O5_BOUND["frozen_configuration"][0], O5_BOUND["freeze_manifest"][0]
    if not (REPO / frozen_rel).exists() or not (REPO / freeze_rel).exists():
        return
    frozen, freeze = _load(REPO / frozen_rel), _load(REPO / freeze_rel)
    if (frozen.get("status") != "O5_RESOLVED_FROZEN" or freeze.get("status") != "O5_RESOLVED_FROZEN"
            or frozen.get("pre_run_status", {}).get("O-5") != "RESOLVED_FROZEN"
            or o5.get("configuration_sha256") != O5_CONFIGURATION_SHA256
            or frozen.get("configuration_sha256") != O5_CONFIGURATION_SHA256):
        failures.append("O-5 freeze records do not show the exact frozen configuration RESOLVED_FROZEN")
    rebinding = _load(REPO / O5_REBINDING_002) if (REPO / O5_REBINDING_002).exists() else {}
    if (o5.get("postfreeze_rebinding_002") != O5_REBINDING_002
            or rebinding.get("rebinding_id") != "O5_POSTFREEZE_REBINDING_002"
            or rebinding.get("amendment") != "HARNESS_AMENDMENT_003"
            or rebinding.get("previous_rebinding") != {"path": O5_BOUND["postfreeze_rebinding_001"][0],
                                                       "sha256": O5_BOUND["postfreeze_rebinding_001"][1]}):
        failures.append(f"harness manifest: O-5 must reference {O5_REBINDING_002} for HARNESS_AMENDMENT_003")


def _check_current_o3(o3: dict, failures: list[str]) -> None:
    """HARNESS_AMENDMENT_003: broadened exposure rule recorded additively; no identity, no exception."""
    for key, (rel, digest) in O3_ADDENDA.items():
        if o3.get(key) != rel or o3.get(f"{key}_sha256") != digest:
            failures.append(f"harness manifest: O-3 {key} must be {rel}")
        _check_hashes({rel: o3.get(f"{key}_sha256")}, f"O-3 {key}", failures)
    if (o3.get("exposure_rule") != "LABELLED_EVALUATION_DERIVED_EXAMPLES_FROM_ANY_PROJECT_ARTIFACT"
            or o3.get("exceptions_approved") != [] or o3.get("annotator_identities_supplied") is not False):
        failures.append("harness manifest: O-3 broadened exposure rule, no exceptions, no identities")


def _keys(value):
    if isinstance(value, dict):
        yield from value
        for v in value.values():
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def _check_separation_and_boundaries(failures: list[str]) -> None:
    inputs = _load(INPUTS)
    if [k for k in _keys(inputs) if k.startswith("expected")]:
        failures.append("dry-run inputs contain expected results")
    if inputs.get("all_data_synthetic") is not True or inputs.get("dry_run_only") is not True:
        failures.append("dry-run inputs must be marked synthetic and dry-run only")
    oracle = _load(ORACLE)
    if [c["case_id"] for c in inputs["cases"]] != [c["case_id"] for c in oracle["cases"]]:
        failures.append("dry-run oracle does not align with the inputs")
    for path in sorted(HARNESS_DIR.glob("*.py")) + [Path(__file__).resolve()]:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        if _NETWORK_IMPORT.search(text):
            failures.append(f"model SDK or network import: {rel}")
        if path.parent == HARNESS_DIR and (_TEST_IMPORT.search(text) or INPUTS.name in text or ORACLE.name in text):
            failures.append(f"harness code references dry-run inputs or the oracle: {rel}")


def main() -> int:
    failures: list[str] = []
    harness = _load(HARNESS_MANIFEST)
    print(f"Stage F harness status: {harness.get('status')} (manifest sha256 {_sha256(HARNESS_MANIFEST)})")
    locked = _check_locked(harness, failures)
    _check_manifest(harness, failures)
    _check_separation_and_boundaries(failures)
    if failures:
        for failure in failures:
            print("FAIL ", failure)
        return 1
    print(f"PASS  Locked Stage F design and Stage A-E artifacts unchanged (hash checks): {locked}")
    print(f"PASS  Harness file hashes and coverage: {len(harness['harness_files'])}")
    print("PASS  Dry-run inputs and oracle are separate; harness code references neither")
    print("PASS  No-execution record: " + ", ".join(f"{k}={v}" for k, v in NO_EXECUTION.items()))
    print(f"PASS  Pre-run items at harness lock were PRE_RUN_REQUIRED: {', '.join(PRE_RUN_REQUIRED)}")
    print("PASS  Current pre-run state: " + ", ".join(f"{k}={v}" for k, v in PRE_RUN_CURRENT.items())
          + "; O-6 freeze manifest hash and frozen corpus match; O-5 freeze manifest, frozen configuration, and "
          + "O5_POSTFREEZE_REBINDING_001 hashes match; O5_POSTFREEZE_REBINDING_002 referenced; calibration "
          + "freeze manifest, corpus, and guide hashes match (CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED "
          + "RESOLVED_FROZEN)")
    print("PASS  Pilot readiness false; blockers: " + ", ".join(f"{k}={v}" for k, v in PILOT_BLOCKERS.items())
          + "; annotation record provenance hashes RESOLVED; O-3 addenda hash-bound, no exception, no identity")
    print(f"PASS  Post-lock amendments recorded with record hashes: {', '.join(AMENDMENTS)}")
    print("PASS  No model SDK or network import in harness code or this verifier")

    tests = sorted(rel for rel in harness["harness_files"] if Path(rel).name.startswith("test_"))
    sys.stdout.flush()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=REPO)
    if result.returncode:
        print("FAIL  Stage F harness tests (including the static audit)")
        return result.returncode
    print(f"PASS  Stage F harness tests, including the static audit: {len(tests)} files")
    print("LIMIT E3-E-SEM-001 MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE remains NOT RESOLVED. The harness is")
    print("      locked offline and has not been executed; it validates no model and authorizes no run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
