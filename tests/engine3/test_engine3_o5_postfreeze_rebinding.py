"""O5_POSTFREEZE_REBINDING_001, _002, and _003: the O-5 verifier accepts exactly the approved rebindings and nothing
else."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "verify_engine3_stage_f_o5_freeze", REPO / "scripts" / "verify_engine3_stage_f_o5_freeze.py")
o5 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o5)

FROZEN = json.loads(o5.FROZEN.read_text(encoding="utf-8"))
MANIFEST = json.loads(o5.MANIFEST.read_text(encoding="utf-8"))
REBINDING = json.loads(o5.REBINDING.read_text(encoding="utf-8"))
REBINDING_002 = json.loads(o5.REBINDING_002.read_text(encoding="utf-8"))
REBINDING_003 = json.loads(o5.REBINDING_003.read_text(encoding="utf-8"))
VERIFIER_SHA = o5.file_sha(Path(o5.__file__).resolve())


def _findings(manifest=MANIFEST, frozen=FROZEN, verifier_sha=VERIFIER_SHA):
    return o5.rebinding_findings(manifest, frozen, verifier_sha)


def _with_record(tmp_path, monkeypatch, record, name="REBINDING"):
    path = tmp_path / "rebinding.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(o5, name, path)


def test_verifier_passes():
    assert o5.main() == 0


def test_original_o5_records_are_byte_identical():
    assert o5.file_sha(o5.MANIFEST) == o5.ORIGINAL_MANIFEST_SHA256
    assert o5.file_sha(o5.FROZEN) == o5.ORIGINAL_FROZEN_SHA256 == MANIFEST["frozen_record_sha256"]


def test_approved_rebindings_are_accepted_and_the_original_hashes_are_still_checked():
    problems, counts = _findings()
    assert problems == []
    rebound = set(o5.REBOUND_LOCKED_INPUTS) | set(o5.REBOUND_LOCKED_INPUTS_002) | set(o5.REBOUND_LOCKED_INPUTS_003)
    assert len(rebound) == 7
    assert counts == {"unchanged": len(MANIFEST["locked_inputs_sha256"]) - 7, "rebound": 7}
    for rel, (original, _) in o5.REBOUND_LOCKED_INPUTS.items():
        assert MANIFEST["locked_inputs_sha256"][rel] == original


def test_any_other_locked_input_divergence_fails():
    manifest = copy.deepcopy(MANIFEST)
    manifest["locked_inputs_sha256"]["experiments/engine3_stage_f/readiness.py"] = "0" * 64
    problems, _ = _findings(manifest=manifest)
    assert "locked input changed since freeze: experiments/engine3_stage_f/readiness.py" in problems


def test_a_permitted_file_at_an_unapproved_hash_fails(monkeypatch):
    rebound = dict(o5.REBOUND_LOCKED_INPUTS_002)
    rel = "experiments/engine3_stage_f/annotation.py"
    rebound[rel] = (rebound[rel][0], "f" * 64)
    monkeypatch.setattr(o5, "REBOUND_LOCKED_INPUTS_002", rebound)
    problems, _ = _findings()
    assert f"locked input changed since freeze: {rel}" in problems


def test_the_second_rebinding_must_start_where_the_first_ended(monkeypatch):
    rebound = dict(o5.REBOUND_LOCKED_INPUTS)
    rel = "experiments/engine3_stage_f/annotation.py"
    rebound[rel] = (rebound[rel][0], "f" * 64)
    monkeypatch.setattr(o5, "REBOUND_LOCKED_INPUTS", rebound)
    problems, _ = _findings()
    assert f"rebinding chain broken for {rel}" in problems


def test_a_file_outside_both_rebindings_may_not_change(monkeypatch):
    rebound = dict(o5.REBOUND_LOCKED_INPUTS_002)
    rel = "experiments/engine3_stage_f/readiness.py"
    rebound[rel] = (MANIFEST["locked_inputs_sha256"][rel], "f" * 64)
    monkeypatch.setattr(o5, "REBOUND_LOCKED_INPUTS_002", rebound)
    problems, _ = _findings()
    assert f"rebound locked input still at its freeze hash (amendment not applied): {rel}" in problems
    assert "O5_POSTFREEZE_REBINDING_002 locked-input list or hashes differ from the approved set" in problems


def test_a_rebinding_record_that_adds_a_file_fails(tmp_path, monkeypatch):
    record = copy.deepcopy(REBINDING)
    record["rebound_locked_inputs"]["experiments/engine3_stage_f/readiness.py"] = {
        "original_sha256": MANIFEST["locked_inputs_sha256"]["experiments/engine3_stage_f/readiness.py"],
        "post_sha256": "0" * 64}
    _with_record(tmp_path, monkeypatch, record)
    problems, _ = _findings()
    assert "rebinding locked-input list or hashes differ from the approved set" in problems


@pytest.mark.parametrize("key, value", [
    ("rebinding_id", "O5_POSTFREEZE_REBINDING_002"),
    ("amendment", "HARNESS_AMENDMENT_003"),
    ("original_o5_records_modified", True),
])
def test_a_different_rebinding_or_amendment_id_fails(tmp_path, monkeypatch, key, value):
    record = copy.deepcopy(REBINDING)
    record[key] = value
    _with_record(tmp_path, monkeypatch, record)
    problems, _ = _findings()
    assert problems


@pytest.mark.parametrize("name", ["REBINDING", "REBINDING_002", "REBINDING_003"])
def test_a_missing_rebinding_record_fails(tmp_path, monkeypatch, name):
    monkeypatch.setattr(o5, name, tmp_path / "absent.json")
    problems, _ = _findings()
    assert problems


def test_an_unrecorded_verifier_change_fails():
    problems, _ = _findings(verifier_sha="0" * 64)
    assert "O-5 verifier hash is neither the original nor the rebound hash" in problems


def test_unchanged_configuration_attestation_must_match_the_frozen_record():
    frozen = copy.deepcopy(FROZEN)
    frozen["frozen_configuration"]["generation"]["max_tokens"] = 1
    problems, _ = _findings(frozen=frozen)
    assert "rebinding's unchanged O-5 configuration does not match the original frozen record" in problems
    assert REBINDING["unchanged_o5_configuration"]["pilot_run_order_size"] == 44
    assert REBINDING["unchanged_o5_configuration"]["e3_e_sem_001_resolved"] is False


# --- O5_POSTFREEZE_REBINDING_002 (HARNESS_AMENDMENT_003) -------------------------------------------

def test_rebinding_001_is_kept_byte_identical_and_bound_by_rebinding_002():
    assert o5.file_sha(o5.REBINDING) == o5.REBINDING_SHA256 == (
        "76119f1bcfc0096c2abf595d2e21002ab7bcbb5aa2a1d1efee7161cb6e0f9ba4")
    assert REBINDING["rebinding_id"] == "O5_POSTFREEZE_REBINDING_001"
    assert REBINDING["o5_verifier"]["post_sha256"] == o5.REBINDING_VERIFIER[1]
    assert REBINDING_002["previous_rebinding"] == {
        "path": "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json", "sha256": o5.REBINDING_SHA256}
    assert REBINDING_002["previous_rebinding_modified"] is False
    # The verifier hash REBINDING_002 recorded is fixed; REBINDING_003 continues the chain from it.
    assert REBINDING_002["o5_verifier"] == {"path": "scripts/verify_engine3_stage_f_o5_freeze.py",
                                            "previous_sha256": o5.REBINDING_VERIFIER[1],
                                            "post_sha256": o5.REBINDING_002_VERIFIER[1]}


def test_rebinding_002_binds_the_amendment_003_records_and_the_new_harness_hashes():
    assert REBINDING_002["rebinding_id"] == "O5_POSTFREEZE_REBINDING_002"
    assert REBINDING_002["amendment"] == "HARNESS_AMENDMENT_003"
    for name, (rel, digest) in o5.REBINDING_002_RECORDS.items():
        assert REBINDING_002["bound_records"][name] == {"path": rel, "sha256": digest}
        assert o5.file_sha(REPO / rel) == digest, rel
    for rel, (_, post) in o5.REBOUND_LOCKED_INPUTS_002.items():
        # Files REBINDING_003 rebinds later are checked at their REBINDING_003 hash instead.
        if rel not in o5.REBOUND_LOCKED_INPUTS_003:
            assert o5.file_sha(REPO / rel) == post, rel
        assert REBINDING_002["rebound_locked_inputs"][rel]["post_sha256"] == post
    # Every step of REBINDING_002 starts from the hash REBINDING_001 ended on, or from the freeze hash.
    for rel, (previous, _) in o5.REBOUND_LOCKED_INPUTS_002.items():
        assert previous == o5.REBOUND_LOCKED_INPUTS.get(rel, (None, MANIFEST["locked_inputs_sha256"][rel]))[1], rel


def test_a_rebinding_002_record_that_adds_a_file_fails(tmp_path, monkeypatch):
    record = copy.deepcopy(REBINDING_002)
    rel = "experiments/engine3_stage_f/readiness.py"
    digest = MANIFEST["locked_inputs_sha256"][rel]
    record["rebound_locked_inputs"][rel] = {"freeze_sha256": digest, "previous_sha256": digest, "post_sha256": "0" * 64}
    _with_record(tmp_path, monkeypatch, record, "REBINDING_002")
    problems, _ = _findings()
    assert "O5_POSTFREEZE_REBINDING_002 locked-input list or hashes differ from the approved set" in problems


@pytest.mark.parametrize("key, value", [
    ("rebinding_id", "O5_POSTFREEZE_REBINDING_003"),
    ("amendment", "HARNESS_AMENDMENT_004"),
    ("original_o5_records_modified", True),
    ("previous_rebinding_modified", True),
    ("previous_rebinding", {"path": "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json", "sha256": "0" * 64}),
])
def test_a_different_rebinding_002_identity_fails(tmp_path, monkeypatch, key, value):
    record = copy.deepcopy(REBINDING_002)
    record[key] = value
    _with_record(tmp_path, monkeypatch, record, "REBINDING_002")
    problems, _ = _findings()
    assert problems


def test_rebinding_002_attests_the_unchanged_o5_configuration():
    assert REBINDING_002["unchanged_o5_configuration"] == o5.unchanged_configuration(FROZEN)
    assert REBINDING_002["unchanged_o5_configuration"] == REBINDING["unchanged_o5_configuration"]
    assert REBINDING_002["pre_run_status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN",
                                               "O-6": "RESOLVED_FROZEN"}
    assert REBINDING_002["pilot_readiness"] is False
    assert not any(REBINDING_002["execution_record"].values())


# --- O5_POSTFREEZE_REBINDING_003 (HARNESS_AMENDMENT_004) -------------------------------------------

def test_rebinding_002_is_kept_byte_identical_and_bound_by_rebinding_003():
    assert o5.file_sha(o5.REBINDING_002) == o5.REBINDING_002_SHA256
    assert REBINDING_003["previous_rebinding"] == {
        "path": "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json", "sha256": o5.REBINDING_002_SHA256}
    assert REBINDING_003["previous_rebindings_modified"] is False
    assert REBINDING_003["o5_verifier"] == {"path": "scripts/verify_engine3_stage_f_o5_freeze.py",
                                            "previous_sha256": o5.REBINDING_002_VERIFIER[1],
                                            "post_sha256": VERIFIER_SHA}


def test_rebinding_003_binds_the_amendment_004_records_and_the_new_harness_hashes():
    assert REBINDING_003["rebinding_id"] == "O5_POSTFREEZE_REBINDING_003"
    assert REBINDING_003["amendment"] == "HARNESS_AMENDMENT_004"
    assert set(o5.REBINDING_003_RECORDS) == {"harness_amendment_004", "calibration_freeze_manifest"}
    for name, (rel, digest) in o5.REBINDING_003_RECORDS.items():
        assert REBINDING_003["bound_records"][name] == {"path": rel, "sha256": digest}
        assert o5.file_sha(REPO / rel) == digest, rel
    # Only governance records: no harness code, prompt, schema, or configuration file is rebound.
    assert set(o5.REBOUND_LOCKED_INPUTS_003) == {
        "tests/engine3/test_engine3_stage_f_harness_manifest.py", "scripts/verify_engine3_stage_f_harness.py",
        "docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md",
        "docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json"}
    for rel, (previous, post) in o5.REBOUND_LOCKED_INPUTS_003.items():
        assert o5.file_sha(REPO / rel) == post, rel
        assert REBINDING_003["rebound_locked_inputs"][rel]["post_sha256"] == post
        # Every step of REBINDING_003 starts from the hash REBINDING_002 ended on.
        assert previous == o5.REBOUND_LOCKED_INPUTS_002[rel][1], rel


def test_the_third_rebinding_must_start_where_the_second_ended(monkeypatch):
    rebound = dict(o5.REBOUND_LOCKED_INPUTS_002)
    rel = "docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json"
    rebound[rel] = (rebound[rel][0], "f" * 64)
    monkeypatch.setattr(o5, "REBOUND_LOCKED_INPUTS_002", rebound)
    problems, _ = _findings()
    assert f"rebinding chain broken for {rel}" in problems


def test_a_changed_rebinding_002_record_fails(tmp_path, monkeypatch):
    record = copy.deepcopy(REBINDING_002)
    record["purpose"] += " "
    _with_record(tmp_path, monkeypatch, record, "REBINDING_002")
    problems, _ = _findings()
    assert "O5_POSTFREEZE_REBINDING_002 record changed after O5_POSTFREEZE_REBINDING_003 bound it" in problems


def test_a_rebinding_003_record_that_adds_a_file_fails(tmp_path, monkeypatch):
    record = copy.deepcopy(REBINDING_003)
    rel = "experiments/engine3_stage_f/annotation.py"
    digest = o5.REBOUND_LOCKED_INPUTS_002[rel][1]
    record["rebound_locked_inputs"][rel] = {"freeze_sha256": MANIFEST["locked_inputs_sha256"][rel],
                                            "previous_sha256": digest, "post_sha256": "0" * 64}
    _with_record(tmp_path, monkeypatch, record, "REBINDING_003")
    problems, _ = _findings()
    assert "O5_POSTFREEZE_REBINDING_003 locked-input list or hashes differ from the approved set" in problems


@pytest.mark.parametrize("key, value", [
    ("rebinding_id", "O5_POSTFREEZE_REBINDING_004"),
    ("amendment", "HARNESS_AMENDMENT_005"),
    ("original_o5_records_modified", True),
    ("previous_rebindings_modified", True),
    ("previous_rebinding", {"path": "docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json", "sha256": "0" * 64}),
    ("bound_records", {}),
])
def test_a_different_rebinding_003_identity_fails(tmp_path, monkeypatch, key, value):
    record = copy.deepcopy(REBINDING_003)
    record[key] = value
    _with_record(tmp_path, monkeypatch, record, "REBINDING_003")
    problems, _ = _findings()
    assert problems


def test_rebinding_003_attests_the_unchanged_o5_configuration_and_keeps_o3_and_pilot_blocked():
    assert REBINDING_003["unchanged_o5_configuration"] == o5.unchanged_configuration(FROZEN)
    assert REBINDING_003["unchanged_o5_configuration"] == REBINDING_002["unchanged_o5_configuration"]
    assert REBINDING_003["pre_run_status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN",
                                               "O-6": "RESOLVED_FROZEN"}
    assert REBINDING_003["pilot_readiness"] is False
    assert REBINDING_003["owner_approved"] == "APPROVED"
    assert "not an independent human annotation or independent adjudication" in REBINDING_003["approval_provenance"]
    assert not any(REBINDING_003["execution_record"].values())
