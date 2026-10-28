"""Stage F harness manifest: complete, hash-bound, and honest that nothing has been executed."""

import hashlib
import json
from pathlib import Path

from experiments.engine3_stage_f.readiness import FrozenRunConfiguration, SupplementArtifact, check_pilot_readiness

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "engine3"
MANIFEST_PATH = DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json"
DESIGN_MANIFEST_PATH = DOCS / "ENGINE3_STAGE_F_DESIGN_MANIFEST.json"
REPORT_PATH = DOCS / "ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
DESIGN = json.loads(DESIGN_MANIFEST_PATH.read_text(encoding="utf-8"))
GLOBS = (
    "experiments/engine3_stage_f/*.py",
    "tests/engine3/stage_f_adapter.py",
    "tests/engine3/test_engine3_stage_f_*.py",
    "tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_*.json",
    "scripts/verify_engine3_stage_f_harness.py",
    "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_harness_file_hash_matches_the_manifest():
    for rel, expected in MANIFEST["harness_files"].items():
        assert _sha256(REPO / rel) == expected, rel


def test_manifest_covers_every_harness_file_and_not_itself():
    found = {p.relative_to(REPO).as_posix() for pattern in GLOBS for p in REPO.glob(pattern)}
    assert set(MANIFEST["harness_files"]) == found
    assert MANIFEST_PATH.relative_to(REPO).as_posix() not in MANIFEST["harness_files"]


def test_manifest_is_bound_to_the_unchanged_locked_design():
    assert MANIFEST["stage_f_design_manifest_sha256"] == _sha256(DESIGN_MANIFEST_PATH)
    assert MANIFEST["base_commit"] == "c81d00343d96683514dd38f43d11a6ebae6d104e"
    for rel, expected in DESIGN["design_artifacts_sha256"].items():
        assert _sha256(REPO / rel) == expected, rel
    assert _sha256(REPO / "scripts" / "verify_engine3_stage_f_design.py") == DESIGN["verifier_sha256"]
    assert DESIGN["status"] == "LOCKED_DESIGN_NO_LIVE_EXECUTION"
    assert MANIFEST["planned_size"] == DESIGN["planned_size"]
    assert MANIFEST["model_controlled_fields"] == DESIGN["model_controlled_fields"]


def test_manifest_records_no_execution_and_unresolved_limitation():
    assert MANIFEST["status"] == "LOCKED_OFFLINE_HARNESS_NOT_EXECUTED"
    assert MANIFEST["lock_date"] == "2026-10-14"
    expected = {"live_model_executed": False, "api_key_used": False, "external_model_sdk_used": False,
                "network_calls": 0, "pilot_executed": False, "main_experiment_executed": False,
                "live_model_authorized": False, "outreach_authorized": False, "pre_run_records_supplied": False}
    for key, value in expected.items():
        assert type(MANIFEST[key]) is type(value) and MANIFEST[key] == value, key
    assert MANIFEST["governing_limitation"] == {
        "id": "E3-E-SEM-001", "name": "MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE", "resolved": False}
    assert {i["id"]: i["status"] for i in MANIFEST["pre_run_required"]} == {
        "O-3": "PRE_RUN_REQUIRED", "O-5": "PRE_RUN_REQUIRED", "O-6": "PRE_RUN_REQUIRED"}


def test_harness_amendment_001_is_recorded_and_hash_bound():
    assert [a["id"] for a in MANIFEST["harness_amendments"]] == [
        "HARNESS_AMENDMENT_001", "HARNESS_AMENDMENT_002", "HARNESS_AMENDMENT_003", "HARNESS_AMENDMENT_004"]
    amendment = MANIFEST["harness_amendments"][0]
    assert amendment["applied"] is True and amendment["date"] == "2026-10-15"
    assert amendment["files_modified"] == ["experiments/engine3_stage_f/readiness.py"]
    assert _sha256(REPO / amendment["record"]) == amendment["record_sha256"]
    assert MANIFEST["status"] == "LOCKED_OFFLINE_HARNESS_NOT_EXECUTED"
    assert "HARNESS_AMENDMENT_001" in REPORT_PATH.read_text(encoding="utf-8")


def test_harness_amendment_002_is_recorded_hash_bound_and_limited_to_the_packet_guide_examples():
    amendment = MANIFEST["harness_amendments"][1]
    assert amendment["id"] == "HARNESS_AMENDMENT_002"
    assert amendment["applied"] is True and amendment["date"] == "2026-10-15" and amendment["owner_approved"] is True
    assert amendment["files_modified"] == ["experiments/engine3_stage_f/annotation.py"]
    assert amendment["example_substitutions"] == {"9.00% -> 9%": "$46.00 -> $46",
                                                  "more than 8% -> above 8%": "over $40 -> higher than $40"}
    assert amendment["experiment_rules_changed"] is False
    assert _sha256(REPO / amendment["record"]) == amendment["record_sha256"]
    # HARNESS_AMENDMENT_001 history is kept unchanged.
    assert MANIFEST["harness_amendments"][0]["record_sha256"] == (
        "a2350ca991716b545c213b41cc1f8a56710370299a7f6c55f0ba481462977536")
    assert "HARNESS_AMENDMENT_002" in REPORT_PATH.read_text(encoding="utf-8")


def test_o6_lock_time_status_is_preserved_and_current_status_is_bound_to_the_freeze():
    # 8. Historical: O-6 was PRE_RUN_REQUIRED when the harness was locked.
    assert MANIFEST["o6_status_at_harness_lock"] == "PRE_RUN_REQUIRED"
    assert {i["id"]: i["status"] for i in MANIFEST["pre_run_required"]}["O-6"] == "PRE_RUN_REQUIRED"
    # 7. Current: O-6 is RESOLVED_FROZEN, bound to the unchanged freeze manifest; O-3 stays unresolved.
    current = MANIFEST["pre_run_state_current"]
    assert MANIFEST["o6_current_status"] == "RESOLVED_FROZEN"
    assert current["status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
    assert current["pilot_readiness"] is False
    # 9. The O-6 freeze manifest and frozen corpus are byte-identical to the bound hashes.
    freeze_path = REPO / current["o6_freeze_manifest"]
    assert current["o6_freeze_manifest"] == "docs/engine3/ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json"
    assert _sha256(freeze_path) == current["o6_freeze_manifest_sha256"]
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    assert freeze["status"] == "O6_RESOLVED_FROZEN" and current["o6_frozen_file"] == freeze["frozen_file"]
    assert _sha256(REPO / freeze["frozen_file"]) == freeze["frozen_file_sha256"] == current["o6_frozen_file_sha256"]


def test_report_states_status_limitation_and_claim_boundary():
    report = REPORT_PATH.read_text(encoding="utf-8")
    for text in ("LOCKED_OFFLINE_HARNESS_NOT_EXECUTED", "E3-E-SEM-001", "NOT_EVALUABLE", "PRE_RUN_REQUIRED",
                 "LIVE_SYNTHETIC_EXPERIMENT", MANIFEST["claim_boundary"]):
        assert text in report, text
    lowered = report.lower()
    for overclaim in ("validated the model", "model is reliable", "100% accura", "resolves e3-e-sem-001",
                      "production ready", "production-ready"):
        assert overclaim not in lowered, overclaim


# --- HARNESS_AMENDMENT_003 ------------------------------------------------------------------------

O5_FREEZE_MANIFEST = "docs/engine3/ENGINE3_STAGE_F_O5_FREEZE_MANIFEST.json"
O5_FROZEN = "docs/engine3/ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json"
O5_REBINDING_001 = "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json"
O5_REBINDING_002 = "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json"
O3_ADDENDUM_001 = "docs/engine3/ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM.md"
O3_ADDENDUM_002 = "docs/engine3/ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md"
O6_FROZEN = DOCS / "ENGINE3_STAGE_F_ARM_A_ADDITIONAL_ARTIFACTS_FROZEN.json"
O6_FREEZE_MANIFEST = DOCS / "ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json"


def _load(rel: str) -> dict:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def test_harness_amendment_003_is_recorded_hash_bound_and_limited_to_governance_gaps():
    amendment = MANIFEST["harness_amendments"][2]
    assert amendment["id"] == "HARNESS_AMENDMENT_003"
    assert amendment["applied"] is True and amendment["date"] == "2026-10-15" and amendment["owner_approved"] is True
    assert MANIFEST["harness_amendment_003_applied"] is True
    assert amendment["files_modified"] == ["experiments/engine3_stage_f/annotation.py"]
    assert amendment["scope"] == [
        "annotation-record provenance completeness",
        "historical/current O-5 status separation",
        "broader annotator-exposure governance",
    ]
    assert amendment["experiment_rules_changed"] is False
    assert amendment["record"] == "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_003.md"
    assert _sha256(REPO / amendment["record"]) == amendment["record_sha256"]
    assert amendment["o5_rebinding_record"] == O5_REBINDING_002
    # HARNESS_AMENDMENT_001 and HARNESS_AMENDMENT_002 history is kept unchanged.
    assert MANIFEST["harness_amendments"][0]["record_sha256"] == (
        "a2350ca991716b545c213b41cc1f8a56710370299a7f6c55f0ba481462977536")
    assert MANIFEST["harness_amendments"][1]["record_sha256"] == (
        "76d20bce96d4ac2a1617e3a71c4cf96b278570e71da648fb1423f17dc76e2d87")
    assert "HARNESS_AMENDMENT_003" in REPORT_PATH.read_text(encoding="utf-8")
    record = (REPO / amendment["record"]).read_text(encoding="utf-8")
    for unchanged in ("Stage F research question", "Arm G or Arm A corpus", "semantic annotation definitions",
                      "decision-rule thresholds A-G", "model configuration", "prompt", "schema", "retry policy",
                      "run order", "O-6 corpus", "Stage D behavior", "E3-E-SEM-001 status"):
        assert f"- {unchanged};" in record or f"- {unchanged}." in record, unchanged


def test_o5_lock_time_status_is_preserved_as_history():
    assert MANIFEST["o5_status_at_harness_lock"] == "PRE_RUN_REQUIRED"
    assert {i["id"]: i["status"] for i in MANIFEST["pre_run_required"]}["O-5"] == "PRE_RUN_REQUIRED"
    assert MANIFEST["pre_run_required_label"].startswith("Lock-time state (2026-10-14). Historical record")
    # The superseded current-state snapshots are kept, labelled as superseded, not rewritten.
    superseded = MANIFEST["pre_run_state_current_superseded"]
    assert [s["superseded_by"] for s in superseded] == ["HARNESS_AMENDMENT_003", "HARNESS_AMENDMENT_004"]
    assert superseded[0]["status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "PRE_RUN_REQUIRED", "O-6": "RESOLVED_FROZEN"}


def test_o5_current_status_is_resolved_frozen_and_bound_to_the_exact_freeze_and_rebindings():
    current = MANIFEST["pre_run_state_current"]
    assert MANIFEST["o5_current_status"] == "RESOLVED_FROZEN" == current["status"]["O-5"]
    o5 = current["o5"]
    assert o5["freeze_manifest"] == O5_FREEZE_MANIFEST
    assert o5["freeze_manifest_sha256"] == _sha256(REPO / O5_FREEZE_MANIFEST) == (
        "4a28ed081ab4b5a9899ad3888cb695f7e366491302ff37a66134897f912355e3")
    assert o5["frozen_configuration"] == O5_FROZEN
    assert o5["frozen_configuration_sha256"] == _sha256(REPO / O5_FROZEN) == (
        "e74c4a46fd771c12e48ab78f492026f97250a2d0a90ee49158ee544fb6c86237")
    frozen, freeze = _load(O5_FROZEN), _load(O5_FREEZE_MANIFEST)
    assert o5["configuration_sha256"] == frozen["configuration_sha256"] == freeze["configuration_sha256"]
    assert freeze["status"] == frozen["status"] == "O5_RESOLVED_FROZEN"
    assert freeze["pre_run_status"]["O-5"] == frozen["pre_run_status"]["O-5"] == "RESOLVED_FROZEN"
    assert o5["postfreeze_rebinding_001"] == O5_REBINDING_001
    assert o5["postfreeze_rebinding_001_sha256"] == _sha256(REPO / O5_REBINDING_001) == (
        "76119f1bcfc0096c2abf595d2e21002ab7bcbb5aa2a1d1efee7161cb6e0f9ba4")
    assert _load(O5_REBINDING_001)["rebinding_id"] == "O5_POSTFREEZE_REBINDING_001"
    # REBINDING_002 binds this manifest's hash, so the manifest names it by path only.
    assert o5["postfreeze_rebinding_002"] == O5_REBINDING_002
    rebinding = _load(O5_REBINDING_002)
    assert rebinding["rebinding_id"] == "O5_POSTFREEZE_REBINDING_002"
    assert rebinding["amendment"] == "HARNESS_AMENDMENT_003" and rebinding["o5_status"] == "RESOLVED_FROZEN"
    assert rebinding["previous_rebinding"] == {"path": O5_REBINDING_001, "sha256": o5["postfreeze_rebinding_001_sha256"]}


def _frozen_o5_configuration() -> FrozenRunConfiguration:
    mapping = _load(O5_FROZEN)["readiness_mapping"]
    assert mapping["readiness_type"] == "experiments.engine3_stage_f.readiness.FrozenRunConfiguration"
    return FrozenRunConfiguration(
        mapping["experiment_configuration_id"], mapping["model_identifier"], mapping["model_version"],
        mapping["prompt_sha256"], tuple(tuple(p) for p in mapping["sampling_configuration"]),
        mapping["output_schema_version"], mapping["frozen"])


def _frozen_o6_supplement() -> tuple[SupplementArtifact, ...]:
    manifest = json.loads(O6_FREEZE_MANIFEST.read_text(encoding="utf-8"))
    frozen = json.loads(O6_FROZEN.read_text(encoding="utf-8"))
    return tuple(SupplementArtifact(a["artifact_id"], manifest["artifact_freeze_sha256"][a["artifact_id"]],
                                    tuple(frozen["author_role_aliases"]), a["human_approved"], a["frozen"])
                 for a in frozen["artifacts"])


def test_current_readiness_does_not_fail_because_of_o5():
    configuration = _frozen_o5_configuration()
    assert configuration.prompt_sha256 == _load(O5_FROZEN)["prompt"]["prompt_bundle_sha256"]
    readiness = check_pilot_readiness(None, configuration, _frozen_o6_supplement())
    assert "O-5" not in {item for item, _ in readiness.unresolved}
    # Remaining: O-3 itself, and the O-6 author cross-check that waits on O-3.
    assert {item for item, _ in readiness.unresolved} == {"O-3", "O-6"}
    assert all(p.endswith("author independence cannot be checked without O-3")
               for item, p in readiness.unresolved if item == "O-6")
    assert not readiness.preconditions_met and readiness.owner_approval_still_required is True


def test_pilot_readiness_stays_false_for_the_actual_remaining_blockers():
    current = MANIFEST["pre_run_state_current"]
    assert current["pilot_readiness"] is False
    # HARNESS_AMENDMENT_004 resolved the calibration blocker; O-3 remains.
    assert current["pilot_readiness_blockers"] == {"O-3": "PRE_RUN_REQUIRED"}
    assert current["annotation_record_provenance_hashes"] == "RESOLVED"
    assert current["calibration_contamination_audit"] == "RESOLVED_FROZEN"
    for key in ("live_model_executed", "pilot_executed", "main_experiment_executed"):
        assert MANIFEST[key] is False
    assert MANIFEST["governing_limitation"]["resolved"] is False


def test_broadened_o3_exposure_rule_is_recorded_additively_without_identities_or_exceptions():
    current = MANIFEST["pre_run_state_current"]
    o3 = current["o3"]
    assert current["status"]["O-3"] == "PRE_RUN_REQUIRED"
    # The first addendum is hash-bound by the contamination resolution record and is not rewritten.
    assert o3["eligibility_addendum_001"] == O3_ADDENDUM_001
    assert o3["eligibility_addendum_001_sha256"] == _sha256(REPO / O3_ADDENDUM_001) == (
        "b66e57b5c546346c031913981091cce21c6b00863b633b62143cdedfad43632f")
    assert o3["eligibility_addendum_002"] == O3_ADDENDUM_002
    assert o3["eligibility_addendum_002_sha256"] == _sha256(REPO / O3_ADDENDUM_002)
    assert o3["exposure_rule"] == "LABELLED_EVALUATION_DERIVED_EXAMPLES_FROM_ANY_PROJECT_ARTIFACT"
    assert o3["exceptions_approved"] == [] and o3["annotator_identities_supplied"] is False
    text = (REPO / O3_ADDENDUM_002).read_text(encoding="utf-8")
    plain = " ".join(text.replace("**", "").split())
    assert ("> Any Primary A, Primary B, or Adjudicator who has previously seen labelled evaluation-derived examples "
            "from any project artifact is presumptively ineligible for the blinded human-reference collection unless "
            "the owner later approves a documented exception.") in plain
    assert "**No exception is approved now.**" in text
    assert "Working on the repository is not, by itself, exposure." in text
    assert "| O-3 | `PRE_RUN_REQUIRED`" in text


# --- HARNESS_AMENDMENT_004 ------------------------------------------------------------------------

CALIBRATION_FREEZE = "docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json"
CALIBRATION_CORPUS = "docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json"
CALIBRATION_GUIDE = "docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_GUIDE.md"


def test_harness_amendment_004_is_recorded_hash_bound_and_limited_to_the_calibration_status():
    amendment = MANIFEST["harness_amendments"][3]
    assert amendment["id"] == "HARNESS_AMENDMENT_004"
    assert amendment["applied"] is True and amendment["date"] == "2026-10-15"
    assert MANIFEST["harness_amendment_004_applied"] is True
    assert amendment["files_modified"] == []
    assert amendment["scope"] == ["record CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED RESOLVED_FROZEN"]
    assert amendment["experiment_rules_changed"] is False
    assert amendment["record"] == "docs/engine3/ENGINE3_STAGE_F_HARNESS_AMENDMENT_004.md"
    assert _sha256(REPO / amendment["record"]) == amendment["record_sha256"]
    assert amendment["o5_rebinding_record"] == "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_003.json"
    assert amendment["owner_approved"] == "APPROVED"
    assert "not an independent human annotation or independent adjudication" in amendment["approval_provenance"]
    # Earlier amendment history is kept unchanged.
    assert [a["record_sha256"] for a in MANIFEST["harness_amendments"][:3]] == [
        "a2350ca991716b545c213b41cc1f8a56710370299a7f6c55f0ba481462977536",
        "76d20bce96d4ac2a1617e3a71c4cf96b278570e71da648fb1423f17dc76e2d87",
        "48060bf60e323964a61fe1d012edc562dd0b67e2c934dbef8db1864c12c4991e",
    ]
    assert "HARNESS_AMENDMENT_004" in REPORT_PATH.read_text(encoding="utf-8")


def test_calibration_current_status_is_bound_to_the_exact_freeze_records():
    current = MANIFEST["pre_run_state_current"]
    assert current["updated_by"] == "HARNESS_AMENDMENT_004"
    calibration = current["calibration"]
    for key, rel in (("freeze_manifest", CALIBRATION_FREEZE), ("corpus", CALIBRATION_CORPUS),
                     ("guide", CALIBRATION_GUIDE)):
        assert calibration[key] == rel
        assert calibration[f"{key}_sha256"] == _sha256(REPO / rel), key
    freeze = _load(CALIBRATION_FREEZE)
    assert freeze["status"] == "CALIBRATION_CORPUS_FROZEN" and freeze["blocker_status"] == "RESOLVED_FROZEN"
    assert freeze["corpus_sha256"] == calibration["corpus_sha256"]
    assert freeze["guide_sha256"] == calibration["guide_sha256"]
    assert freeze["contamination_audit"]["passed"] is True
    assert freeze["calibration_responses_collected"] is False
    assert freeze["pre_run_status"] == current["status"]
    assert freeze["pilot_readiness"] is False


def test_the_snapshot_superseded_by_amendment_004_keeps_the_calibration_blocker_open():
    snapshot = MANIFEST["pre_run_state_current_superseded"][1]
    assert snapshot["superseded_by"] == "HARNESS_AMENDMENT_004"
    assert snapshot["status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
    assert snapshot["calibration_contamination_audit"] == "OPEN"
    assert snapshot["pilot_readiness_blockers"] == {
        "O-3": "PRE_RUN_REQUIRED", "CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED": "OPEN"}


def test_readiness_still_fails_on_o3_after_the_calibration_freeze():
    readiness = check_pilot_readiness(None, _frozen_o5_configuration(), _frozen_o6_supplement())
    assert "O-3" in {item for item, _ in readiness.unresolved}
    assert not readiness.preconditions_met and readiness.owner_approval_still_required is True
