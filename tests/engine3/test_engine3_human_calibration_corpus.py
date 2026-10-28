"""Stage F human calibration corpus: frozen, label-valid, fully covering, and disjoint from evaluation material."""

import contextlib
import copy
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import verify_engine3_stage_f_calibration as cal  # noqa: E402

CORPUS = json.loads(cal.CORPUS.read_text(encoding="utf-8"))
GUIDE_TEXT = cal.GUIDE.read_text(encoding="utf-8")
FREEZE = json.loads(cal.FREEZE_MANIFEST.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_quietly():
    with contextlib.redirect_stdout(io.StringIO()):
        return cal.run_checks()


def _segment(corpus: dict, segment_id: str) -> dict:
    for item in corpus["items"]:
        for key, shown in zip(item["training_key"]["segments"], item["packet"]["segments"]):
            if key["packet_segment_id"] == segment_id:
                return {"key": key, "shown": shown, "item": item}
    raise KeyError(segment_id)


def _replace_text(corpus: dict, segment_id: str, text: str) -> dict:
    corpus = copy.deepcopy(corpus)
    seg = _segment(corpus, segment_id)
    seg["shown"]["text"] = text
    packet = seg["item"]["packet"]
    packet["rendered_text"] = " ".join(s["text"] for s in packet["segments"])
    return corpus


# --- verifier and freeze --------------------------------------------------------------------------


def test_verifier_passes():
    failures, results = _run_quietly()
    assert failures == []
    assert results["contamination_audit"]["passed"] is True
    assert results["coverage"]["passed"] is True


def test_freeze_manifest_binds_the_exact_files_and_records_this_run():
    assert FREEZE["status"] == "CALIBRATION_CORPUS_FROZEN"
    assert FREEZE["corpus_sha256"] == _sha256(cal.CORPUS)
    assert FREEZE["guide_sha256"] == _sha256(cal.GUIDE)
    assert FREEZE["verifier_sha256"] == _sha256(Path(cal.__file__).resolve())
    assert FREEZE["operational_guide_sha256"] == _sha256(cal.og.GUIDE) == (
        "fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011")
    assert FREEZE["locked_protocol_sha256"] == _sha256(cal.o5.PROTOCOL) == (
        "cba979c91cdcc96f419b0f3758cadd651f3427064a7f66ebebd23929c623b5a4")
    _, results = _run_quietly()
    for key in ("label_validation", "coverage", "contamination_audit"):
        assert FREEZE[key] == results[key], key


def test_blocker_resolved_frozen_while_o3_and_pilot_readiness_stay_blocked():
    assert FREEZE["blocker"] == "CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED"
    assert (FREEZE["blocker_status_before"], FREEZE["blocker_status"]) == ("OPEN", "RESOLVED_FROZEN")
    assert FREEZE["pre_run_status"] == {"O-3": "PRE_RUN_REQUIRED", "O-5": "RESOLVED_FROZEN", "O-6": "RESOLVED_FROZEN"}
    assert FREEZE["pilot_readiness"] is False
    assert FREEZE["pilot_readiness_blockers"] == {"O-3": "PRE_RUN_REQUIRED"}
    assert FREEZE["governing_limitation"]["resolved"] is False
    assert all(v in (False, 0) for v in FREEZE["execution_record"].values())


def test_records_named_unchanged_by_the_freeze_are_unchanged():
    for rel, digest in FREEZE["records_unchanged_by_this_freeze_sha256"].items():
        assert _sha256(REPO / rel) == digest, rel


def test_resolution_record_still_shows_the_blocker_open_as_of_its_date():
    # The resolution record is history; the freeze manifest carries the new status.
    text = (REPO / "docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_RESOLUTION_001.md").read_text("utf-8")
    assert "| `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` | `OPEN` |" in text


def test_a_changed_corpus_or_guide_breaks_the_freeze(tmp_path, monkeypatch):
    for attr, path in (("CORPUS", cal.CORPUS), ("GUIDE", cal.GUIDE)):
        copy_path = tmp_path / path.name
        copy_path.write_bytes(path.read_bytes() + b" ")
        monkeypatch.setattr(cal, attr, copy_path)
        failures, _ = _run_quietly()
        assert any("freeze manifest binding" in f for f in failures), attr
        monkeypatch.setattr(cal, attr, path)


# --- labels -----------------------------------------------------------------------------------------


def test_corpus_is_training_only_with_no_responses_and_no_model_labels_as_authority():
    assert CORPUS["all_data_synthetic"] is True and CORPUS["evaluation_material"] is False
    assert CORPUS["label_authority"] == "HUMAN_REFERENCE_TRAINING_LABELS_ONLY"
    assert CORPUS["model_generated_labels_used_as_authority"] is False
    assert CORPUS["calibration_responses_collected"] is False
    assert FREEZE["calibration_responses_collected"] is False
    assert "response" not in json.dumps([i["training_key"] for i in CORPUS["items"]]).casefold()


def test_a_forced_label_on_an_unscorable_segment_fails():
    corpus = copy.deepcopy(CORPUS)
    _segment(corpus, "CAL-07-S1")["key"]["label"]["fields"] = _segment(corpus, "CAL-03-S3")["key"]["label"]["fields"]
    problems, _ = cal.label_findings(corpus)
    assert any("CAL-07-S1" in p for p in problems)


def test_citing_an_id_not_shown_for_the_account_fails():
    corpus = copy.deepcopy(CORPUS)
    _segment(corpus, "CAL-08-S2")["key"]["label"]["fields"]["grounding_refs"] = ["M-LEADTIME", "R-LIMIT"]
    problems, _ = cal.label_findings(corpus)
    assert any("not shown" in p for p in problems)


def test_internal_mode_outside_an_internal_artifact_fails():
    corpus = copy.deepcopy(CORPUS)
    _segment(corpus, "CAL-08-S2")["key"]["label"]["fields"]["reference_use_mode"] = "INTERNAL"
    problems, _ = cal.label_findings(corpus)
    assert any("INTERNAL" in p for p in problems)


def test_a_score_like_specificity_value_fails():
    corpus = copy.deepcopy(CORPUS)
    _segment(corpus, "CAL-08-S2")["key"]["label"]["fields"]["specificity"] = 50
    problems, _ = cal.label_findings(corpus)
    assert any("CAL-08-S2" in p for p in problems)


# --- coverage ---------------------------------------------------------------------------------------


def test_every_value_of_the_eleven_fields_is_practised():
    coverage = FREEZE["coverage"]["field_coverage"]
    assert len(coverage["claim_type"]) == 4 and len(coverage["semantic_class"]) == 12
    assert len(coverage["claim_field"]) == 8 and len(coverage["reference_use_mode"]) == 3
    assert coverage["specificity"] == [0, 10, 90]
    for key in ("question_has_premise", "prescribes_buyer_choice", "formatting_variant", "mixed_reference_modes"):
        assert coverage[key] == [False, True], key
    assert coverage["status"] == ["SCORABLE", "UNSCORABLE_SEGMENT_STRUCTURE"]
    assert coverage["source_identity_disclosure"] == ["ALLOWED", "NOT_ALLOWED"]


def test_losing_a_covered_value_fails():
    corpus = copy.deepcopy(CORPUS)
    _segment(corpus, "CAL-06-S2")["key"]["label"]["fields"]["source_instruction_ref"] = None
    _, rows = cal.label_findings(corpus)
    _, problems = cal.field_coverage(corpus, rows)
    assert any("source_instruction_ref" in p for p in problems)


# --- contamination ----------------------------------------------------------------------------------


def _audit(corpus: dict, guide_text: str = GUIDE_TEXT) -> dict:
    result, _ = cal.contamination_audit(corpus, guide_text)
    return result


def test_an_exact_arm_a_sentence_is_detected():
    sentence = cal.prohibited_corpora()["arm_a_artifacts_48"][0][1]
    result = _audit(_replace_text(CORPUS, "CAL-01-S1", sentence))
    assert result["classes"]["arm_a_artifacts_48"]["exact"] >= 1 and result["passed"] is False


@pytest.mark.parametrize("name", ["operational_guide_worked_examples", "o5_prompt_worked_examples",
                                  "stage_f_dry_run_prose", "stage_e_prerecorded_prose"])
def test_a_sentence_from_each_prohibited_class_is_detected(name):
    rows = [s for _, s in cal.prohibited_corpora()[name] if len(cal.o5.tokens(s)) >= cal.o5.MIN_TOKENS]
    result = _audit(_replace_text(CORPUS, "CAL-01-S1", rows[0]))
    assert result["classes"][name]["exact"] >= 1 and result["passed"] is False


def test_a_near_paraphrase_of_evaluation_prose_is_detected():
    result = _audit(_replace_text(CORPUS, "CAL-02-S3", "Has anything changed on your side, and would a call help?"))
    assert result["totals"]["near"] >= 1 and result["passed"] is False


def test_a_shared_distinctive_four_gram_is_detected():
    result = _audit(_replace_text(CORPUS, "CAL-02-S3", "Rowing crews still the point at noon."))
    assert result["totals"]["distinctive_4_grams"] >= 1 and result["passed"] is False


@pytest.mark.parametrize("text", ["The boatyard sent Lakemoor an invoice.", "Hull costs rose 9% this year.",
                                  "Hull costs rose 9.00% this year.", "The renewal is close.",
                                  "A per-route charge applies.", "The cobalt hull paint dried."])
def test_banned_evaluation_and_guide_terms_are_detected(text):
    result = _audit(_replace_text(CORPUS, "CAL-02-S3", text))
    assert result["banned_term_findings"] >= 1 and result["passed"] is False


def test_the_calibration_guide_is_audited_too():
    result = _audit(CORPUS, GUIDE_TEXT + "\nWould a short call be useful?\n")
    assert result["passed"] is False


def test_audit_reports_zero_findings_with_no_allowlist():
    audit = FREEZE["contamination_audit"]
    assert audit["totals"] == {"exact": 0, "near": 0, "distinctive_4_grams": 0}
    assert audit["banned_term_findings"] == 0 and audit["unexplained_findings"] == 0 and audit["allowlisted"] == 0
    assert set(audit["classes"]) == set(FREEZE["prohibited_corpus_classes"]) == {
        "arm_a_artifacts_48", "arm_g_scenario_and_evaluation_material", "stage_e_prerecorded_prose",
        "stage_f_dry_run_prose", "operational_guide_worked_examples", "o5_prompt_worked_examples"}


def test_arm_a_class_covers_all_48_artifacts():
    labels = {label for label, _ in cal.prohibited_corpora()["arm_a_artifacts_48"]}
    assert len({lbl for lbl in labels if lbl.startswith("Arm A seed")}) == 32
    assert len({lbl for lbl in labels if lbl.startswith("Arm A O-6")}) == 16


def test_owner_review_decisions_on_the_five_boundary_cases_are_applied():
    expected = {
        "CAL-02-S2": (["R-LOG", "R-TURNAROUND"], "PARAPHRASE", "EXTERNAL_EVENT_FACT", True),
        "CAL-03-S4": ([], "DIRECT", "COURTESY", False),
        "CAL-08-S3": (["M-BULLETIN", "M-LEADTIME"], "PARAPHRASE", "OTHER_FACT", True),
        "CAL-13-S2": (["R-TURNAROUND"], "PARAPHRASE", "CONDITION_REFERENCE", False),
        "CAL-14-S2": (["M-STATEMENT"], "PARAPHRASE", "CONDITION_REFERENCE", False),
    }
    boundary = {k["packet_segment_id"] for i in CORPUS["items"] for k in i["training_key"]["segments"]
                if k["boundary_case"]}
    assert boundary == set(expected)
    for segment_id, (refs, mode, semantic_class, mixed) in expected.items():
        label = _segment(CORPUS, segment_id)["key"]["label"]
        fields = label["fields"]
        assert (fields["grounding_refs"], fields["reference_use_mode"], fields["semantic_class"]) == (
            refs, mode, semantic_class), segment_id
        assert label["mixed_reference_modes"] is mixed, segment_id
    assert _segment(CORPUS, "CAL-03-S4")["key"]["label"]["fields"]["prescribes_buyer_choice"] is True
    assert "M-LIMIT" not in _segment(CORPUS, "CAL-14-S2")["key"]["label"]["fields"]["grounding_refs"]
    # Correction after the boundary-case review.
    assert _segment(CORPUS, "CAL-13-S1")["key"]["label"]["fields"]["grounding_refs"] == ["R-LIMIT", "R-STATEMENT"]
    review = FREEZE["owner_label_review"]
    assert set(review["decisions"]) == set(expected) | {"CAL-13-S1"}
    assert review["attestations_changed"] is False
    assert review["all_segments_owner_reviewed_and_approved"] is True
    assert review["segments_reviewed"] == FREEZE["coverage"]["corpus_size"]["segments"] == 50
    assert review["scope"] == "all 50 calibration segments and all 14 artifact attestation records"
    assert "model-drafted" in review["approval_provenance"]
    assert "not an independent human annotation or independent adjudication" in review["approval_provenance"]
