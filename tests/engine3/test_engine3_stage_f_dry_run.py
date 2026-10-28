"""Stage F offline dry run: handwritten stand-ins for live output, compared against a separate oracle.

These are software tests over synthetic inputs. They say nothing about any model's reliability.
"""

import json

import pytest

from baec_app.engine3.errors import Engine3ValidationError
from experiments.engine3_stage_f.comparison import ComparisonCategory, compare_artifact, compare_proposal
from tests.engine3 import stage_f_adapter as ad

INPUTS = ad.load_inputs()
ORACLE = {c["case_id"]: c for c in ad.load_oracle()["cases"]}
CASES = INPUTS["cases"]
CTX = ad.contexts()


def _compare(case):
    record, proposal = ad.run(case, CTX)
    if proposal is None:
        return record, None, None
    context = CTX[case["evaluation_context_id"]]
    references = {a.artifact_id: ad.reference(case, a.artifact_id, ad.packet(case, a, CTX))
                  for a in proposal.artifacts}
    return record, proposal, compare_proposal(proposal, context, references)


def _keys(value):
    if isinstance(value, dict):
        yield from value
        for v in value.values():
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def test_inputs_and_oracle_are_separate_and_aligned():
    assert [c["case_id"] for c in CASES] == list(ORACLE)
    assert not [k for k in _keys(INPUTS) if k.startswith("expected") or k in ("category", "oracle")]
    assert INPUTS["all_data_synthetic"] is True and INPUTS["dry_run_only"] is True
    raw = ad.INPUTS_PATH.read_text(encoding="utf-8")
    assert "E3C-" not in raw and "FALSE_PASS" not in raw and "FALSE_REFUSAL" not in raw


def test_dry_run_covers_every_required_kind_of_case():
    oracle = list(ORACLE.values())
    artifacts = [a for c in oracle for a in c["artifacts"]]
    categories = {a["category"] for a in artifacts}
    assert {"CONCORDANT_PASS", "CONCORDANT_REFUSAL_SAME_PRIMARY", "FALSE_PASS", "FALSE_REFUSAL"} <= categories
    assert any(a["category"] == "CONCORDANT_PASS" and a["disagreements"] for a in artifacts)  # safe, wrong labels
    assert any(a["category"] == "CONCORDANT_REFUSAL_SAME_PRIMARY" and a["human_safe"] is False for a in artifacts)
    assert any(c["structural_outcome"] == "REJECTED" for c in oracle)
    assert any(a["unscorable_ordinals"] for a in artifacts)
    assert any(a["formatting_variant_ordinals"] for a in artifacts)
    assert any(not a["annotation_sensitive"] for a in artifacts)
    purposes = " ".join(c["purpose"] for c in CASES)
    for needle in ("9.00% -> 9%", "more than 8% -> above 8%", "prompt-injection-like", "stale-context control"):
        assert needle in purposes
    assert {c["arm"] for c in CASES} == {"G", "A"} and len(CASES) >= 11


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_dry_run_case_matches_the_hand_derived_oracle(case):
    want = ORACLE[case["case_id"]]
    record, proposal, comparison = _compare(case)
    if want["structural_outcome"] == "REJECTED":
        assert proposal is None and record.outcome.rejection.value == want["rejection_class"]
        return
    assert record.outcome.rejection is None and record.outcome.proposal_digest == proposal.digest
    assert comparison.reaches_review_under_model is want["reaches_review_under_model"]
    assert comparison.false_pass is want["proposal_false_pass"]
    assert comparison.false_refusal is want["proposal_false_refusal"]
    assert [a.artifact_id for a in comparison.artifacts] == [a["artifact_id"] for a in want["artifacts"]]
    for got, exp in zip(comparison.artifacts, want["artifacts"]):
        assert got.annotation_sensitive is exp["annotation_sensitive"]
        assert got.reference_complete is exp["reference_complete"]
        assert got.d_model.allowed is exp["d_model_allowed"]
        assert got.d_model.primary_reason_code == exp["d_model_primary_reason_code"]
        assert (None if got.d_ref is None else got.d_ref.allowed) == exp["d_ref_allowed"]
        assert (None if got.d_ref is None else got.d_ref.primary_reason_code) == exp["d_ref_primary_reason_code"]
        assert (None if got.category is None else got.category.value) == exp["category"]
        assert [(d.ordinal, d.field, d.direction.value) for d in got.disagreements] == [
            (d["ordinal"], d["field"], d["direction"]) for d in exp["disagreements"]]
        if exp["individually_sufficient_fields"] is None:
            assert got.attribution is None
        else:
            assert list(got.attribution.individually_sufficient) == exp["individually_sufficient_fields"]
            assert got.attribution.joint_only is exp["joint_only"]
            assert list(got.attribution.contributing) == exp["contributing_fields"]
        assert got.human_safe is exp["human_safe"]
        assert list(got.unscorable_ordinals) == exp["unscorable_ordinals"]
        assert list(got.unfinalized_ordinals) == exp["unfinalized_ordinals"]
        assert list(got.formatting_variant_ordinals) == exp["formatting_variant_ordinals"]
        assert list(got.mixed_reference_mode_ordinals) == exp["mixed_reference_mode_ordinals"]
        assert len(got.nonexistent_citations) == exp["nonexistent_citation_count"]


def _artifact(case_id, artifact_id):
    case = next(c for c in CASES if c["case_id"] == case_id)
    _, _, comparison = _compare(case)
    return next(a for a in comparison.artifacts if a.artifact_id == artifact_id)


def test_false_pass_reports_reason_transition_and_exact_issue_difference():
    fp = _artifact("F-DRY-04", "A-EMAIL")
    assert fp.false_pass and fp.reason_transition == ("ALLOWED", "E3C-CLM-001")
    assert fp.issues_only_in_model == () and fp.issues_only_in_reference == (("E3C-CLM-001", "S1"),)


def test_false_refusal_reports_reason_transition():
    fr = _artifact("F-DRY-09", "A-CALL")
    assert fr.false_refusal and fr.reason_transition == ("E3C-DISC-005", "ALLOWED")
    assert fr.issues_only_in_model == (("E3C-DISC-005", "S1"),) and fr.issues_only_in_reference == ()


def test_annotation_insensitive_stale_context_is_excluded_from_comparison_categories():
    stale = _artifact("F-DRY-11", "A-EMAIL")
    assert not stale.annotation_sensitive and stale.category is None and stale.reason_transition is None
    assert stale.d_model == stale.d_ref  # identical by construction


def test_formatting_variant_is_compared_verbatim_and_refusal_is_stage_d_strictness_not_model_error():
    strict = _artifact("F-DRY-08", "A-EMAIL")
    assert strict.category is ComparisonCategory.CONCORDANT_REFUSAL_SAME_PRIMARY and strict.human_safe is True
    assert strict.disagreements == ()  # the model wrote "9%" exactly as rendered


def test_human_safe_and_human_refused_prose_are_both_reported():
    safe = {(c["case_id"], a["artifact_id"]): a["human_safe"] for c in ORACLE.values() for a in c["artifacts"]}
    assert True in safe.values() and False in safe.values()


def test_nonexistent_grounding_citations_are_counted_separately():
    case = next(c for c in CASES if c["case_id"] == "F-DRY-01")
    output = json.loads(case["raw_model_output"])
    output["artifacts"][0]["segments"][1]["grounding_refs"].append("G-DOES-NOT-EXIST")
    changed = dict(case, raw_model_output=json.dumps(output))
    _, _, comparison = _compare(changed)
    [artifact] = comparison.artifacts
    assert [(c.ordinal, c.field, c.grounding_item_id) for c in artifact.nonexistent_citations] == [
        (2, "grounding_refs", "G-DOES-NOT-EXIST")]
    assert [(d.field, d.direction.value) for d in artifact.disagreements] == [("grounding_refs", "RESTRICTIVE")]


def test_a_reference_for_different_prose_or_context_is_refused():
    case = next(c for c in CASES if c["case_id"] == "F-DRY-01")
    _, proposal = ad.run(case, CTX)
    artifact = proposal.artifacts[0]
    reference = ad.reference(case, "A-EMAIL", ad.packet(case, artifact, CTX))
    with pytest.raises(Engine3ValidationError):
        compare_artifact(proposal, "A-EMAIL", CTX["CTX-AR-9PCT-DIRECT"], reference)
    with pytest.raises(Engine3ValidationError):
        compare_proposal(proposal, CTX["CTX-AR-CURRENT"], {})


def test_comparison_exposes_no_composite_score():
    fp = _artifact("F-DRY-10", "A-EMAIL")
    names = set(type(fp).__dataclass_fields__)
    assert not {n for n in names if any(w in n for w in ("score", "rate", "accuracy", "confidence", "probability"))}
