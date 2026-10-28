"""Stage F-AR replay, metrics, and decision rules AR-A to AR-G (offline).

Software tests over handwritten synthetic records. Counts here are not results about any model.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from baec_app.engine3.errors import Engine3ValidationError
from experiments.engine3_stage_f.comparison import compare_artifact
from experiments.engine3_stage_f.envelope import ExperimentArm, RunKind
from experiments.engine3_stage_f_ar import analysis
from experiments.engine3_stage_f_ar.analysis import (
    ArComparisonCategory,
    ArRuleOutcome,
    compare_to_autonomous_reference,
    metrics_by_group,
    rule_ar_a,
    rule_ar_b,
    rule_ar_c,
    rule_ar_d,
    rule_ar_e,
    rule_ar_f,
    rule_ar_g,
)
from experiments.engine3_stage_f_ar.panel import ReviewerRole
from experiments.engine3_stage_f_ar.review import BATCH_FOR_GENERATOR_RUN_KIND, AutonomousBatch
from tests.engine3 import stage_f_adapter as ad
from tests.engine3 import stage_f_ar_adapter as ar

REPO = Path(__file__).resolve().parents[2]
CTX = ad.contexts()
CASES = ad.load_inputs()["cases"]
ORACLE = {c["case_id"]: c for c in ad.load_oracle()["cases"]}
HUMAN_TO_AR = {
    "CONCORDANT_PASS": ArComparisonCategory.CONCORDANT_PASS,
    "CONCORDANT_REFUSAL_SAME_PRIMARY": ArComparisonCategory.CONCORDANT_REFUSAL_SAME_PRIMARY,
    "CONCORDANT_REFUSAL_DIFFERENT_PRIMARY": ArComparisonCategory.CONCORDANT_REFUSAL_DIFFERENT_PRIMARY,
    "FALSE_PASS": ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS,
    "FALSE_REFUSAL": ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL,
    None: None,
}


def _family(case):
    scenario = ad.scenario(case["scenario_id"])
    return scenario["family"] if scenario else "ARM_A_PROSE"


def _build():
    rows, references, human = [], {}, []
    for case in CASES:
        _, proposal = ad.run(case, CTX)
        if proposal is None:
            continue
        context = CTX[case["evaluation_context_id"]]
        for artifact in proposal.artifacts:
            reference = ar.reference(case, artifact, CTX)
            references[reference.packet_id] = reference
            rows.append(compare_to_autonomous_reference(proposal, artifact.artifact_id, context, reference,
                                                        _family(case)))
            human.append(compare_artifact(proposal, artifact.artifact_id, context,
                                          ad.reference(case, artifact.artifact_id, ad.packet(case, artifact, CTX))))
    return rows, references, human


ROWS, REFERENCES, HUMAN = _build()


def _full(rows):
    return tuple(dataclasses.replace(r, batch=AutonomousBatch.AR_FULL_RUN) for r in rows)


def test_replay_matches_the_locked_comparison_when_annotations_are_identical():
    """Same annotations through both paths give the same Stage D outcomes (a software equivalence check)."""
    oracle = [a for case in CASES for a in ORACLE[case["case_id"]]["artifacts"]]
    assert len(ROWS) == len(HUMAN) == len(oracle)
    for row, human, want in zip(ROWS, HUMAN, oracle):
        assert row.d_generator == human.d_model
        assert row.d_autonomous == human.d_ref
        assert row.annotation_sensitive is human.annotation_sensitive
        assert row.reference_complete is human.reference_complete
        assert row.category is HUMAN_TO_AR[want["category"]]
        assert row.disagreements == human.disagreements


def test_categories_cover_potential_false_pass_and_false_refusal():
    categories = {r.category for r in ROWS}
    assert ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS in categories
    assert ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL in categories
    assert any(not r.reference_complete and r.reference_kind is not None for r in ROWS)
    assert any(not r.annotation_sensitive for r in ROWS)


def test_no_reference_means_no_autonomous_stage_d_result():
    case = CASES[0]
    _, proposal = ad.run(case, CTX)
    row = compare_to_autonomous_reference(proposal, proposal.artifacts[0].artifact_id,
                                          CTX[case["evaluation_context_id"]], None, "F")
    assert row.reference_kind is None and row.d_autonomous is None and row.category is None and not row.compared


def test_reference_must_belong_to_the_prose_context_and_batch():
    case, other = CASES[0], CASES[2]
    _, proposal = ad.run(case, CTX)
    _, other_proposal = ad.run(other, CTX)
    wrong = ar.reference(other, other_proposal.artifacts[0], CTX)
    with pytest.raises(Engine3ValidationError, match="does not belong"):
        compare_to_autonomous_reference(proposal, proposal.artifacts[0].artifact_id,
                                        CTX[case["evaluation_context_id"]], wrong, "F")
    right = ar.reference(case, proposal.artifacts[0], CTX)
    full = dataclasses.replace(proposal, binding=dataclasses.replace(proposal.binding, run_kind=RunKind.FULL_RUN),
                               experiment_provenance=dataclasses.replace(
                                   proposal.binding, run_kind=RunKind.FULL_RUN).experiment_provenance)
    with pytest.raises(Engine3ValidationError, match="different batch"):
        compare_to_autonomous_reference(full, proposal.artifacts[0].artifact_id,
                                        CTX[case["evaluation_context_id"]], right, "F")


# --- metrics ---------------------------------------------------------------------------------------------------


def test_metrics_are_separate_per_batch_and_arm_and_never_composite():
    report = metrics_by_group(ROWS, REFERENCES)
    assert set(report) == {("OFFLINE_DRY_RUN", "G"), ("OFFLINE_DRY_RUN", "A")}
    for group in report.values():
        assert set(group) == {"ab_agreement", "adjudication", "adjudicated_items", "generator_field_agreement",
                              "generator_status_agreement", "outcome_table", "disagreement_families", "coverage",
                              "identical_prose_sensitivity"}
    text = repr(report).casefold()
    for word in ("score", "composite", "accuracy", "confidence", "probability", "ground_truth", "human"):
        assert word not in text


def test_ab_agreement_and_adjudication_counts():
    g = metrics_by_group(ROWS, REFERENCES)[("OFFLINE_DRY_RUN", "G")]
    agree, total = g["ab_agreement"]["specificity"]
    assert total - agree == 1  # F-DRY-09: Reviewer A and Reviewer B differ on one specificity value
    assert g["adjudication"] == (1, len({r.packet_id for r in ROWS if r.arm is ExperimentArm.G}))
    assert g["adjudicated_items"]["specificity"] == 1 and g["adjudicated_items"]["claim_type"] == 0
    assert g["ab_agreement"]["status"][1] >= g["ab_agreement"]["specificity"][1]


def test_generator_agreement_counts_match_field_disagreements():
    g = metrics_by_group(ROWS, REFERENCES)[("OFFLINE_DRY_RUN", "G")]
    g_rows = [r for r in ROWS if r.arm is ExperimentArm.G]
    for name, (agree, total) in g["generator_field_agreement"].items():
        assert total - agree == sum(d.field == name for r in g_rows for d in r.disagreements)
    scorable, segments = g["generator_status_agreement"]
    assert segments == sum(len(r.segment_statuses) for r in g_rows) and scorable < segments  # F-DRY-07 unscorable
    assert sum(g["disagreement_families"].values()) == sum(len(r.disagreements) for r in g_rows)


def test_outcome_table_and_coverage():
    g = metrics_by_group(ROWS, REFERENCES)[("OFFLINE_DRY_RUN", "G")]
    table = g["outcome_table"]
    assert table["AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS"] == 2
    assert table["AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL"] == 2
    assert table["autonomous_reference_incomplete"] == 1 and table["no_autonomous_reference"] == 0
    complete, artifacts = g["coverage"]["artifacts_autonomous_reference_complete"]
    assert artifacts - complete == 1


def test_identical_prose_sensitivity_detects_outcome_change_on_the_same_packet():
    rows = [r for r in ROWS if r.compared]
    passing = next(r for r in rows if r.d_generator.allowed)
    refused = next(r for r in rows if not r.d_generator.allowed)
    twin = dataclasses.replace(refused, packet_id=passing.packet_id, artifact_id="TWIN")
    sensitivity = analysis.identical_prose_sensitivity([passing, twin])
    assert sensitivity["repeated_packets_with_varying_generator_outcome"] == (1, 1)
    assert analysis.identical_prose_sensitivity([passing])["repeated_packets_with_varying_generator_outcome"] == (0, 0)


# --- decision rules ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("batch", [AutonomousBatch.OFFLINE_DRY_RUN,
                                   AutonomousBatch.AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH])
def test_rules_refuse_ar_44_and_dry_run_data(batch):
    rows = tuple(dataclasses.replace(r, batch=batch) for r in ROWS)
    for rule in (rule_ar_a, rule_ar_b, rule_ar_e):
        with pytest.raises(Engine3ValidationError, match="never used"):
            rule(rows)
    for rule in (rule_ar_c, rule_ar_d):
        with pytest.raises(Engine3ValidationError, match="never used"):
            rule(rows, REFERENCES)


def test_ar_a_reports_every_potential_false_pass_individually_per_arm():
    results = {r.arm: r for r in rule_ar_a(_full(ROWS))}
    g = results["G"]
    assert g.outcome is ArRuleOutcome.FIRED and g.numerator == 2
    cases = dict(g.detail)["potential_false_passes"]
    assert len(cases) == 2 and all(len(c) == 3 for c in cases)
    assert results["A"].outcome is ArRuleOutcome.NOT_FIRED


def test_ar_b_supports_only_a_bounded_observation():
    no_false_pass = tuple(r for r in _full(ROWS) if not r.potential_false_pass)
    for result in rule_ar_b(no_false_pass):
        detail = dict(result.detail)
        assert detail["supports"] == "BOUNDED_SYNTHETIC_AUTONOMOUS_CONSENSUS_OBSERVATION_ONLY"
        assert detail["establishes_safety"] is False and detail["establishes_human_semantic_accuracy"] is False
        assert result.outcome is ArRuleOutcome.FIRED and result.numerator == 0
    assert {r.arm: r.outcome for r in rule_ar_b(_full(ROWS))}["G"] is ArRuleOutcome.NOT_FIRED
    assert all(r.outcome is ArRuleOutcome.NOT_EVALUABLE for r in rule_ar_b(()))


def test_ar_c_reports_ab_instability_without_a_threshold():
    results = rule_ar_c(_full(ROWS), REFERENCES)
    assert {r.outcome for r in results} == {ArRuleOutcome.REPORTED}
    specificity = next(r for r in results if r.arm == "G" and r.item == "specificity")
    assert specificity.numerator == 1 and dict(specificity.detail)["autonomous_reference_instability_present"]
    assert len(results) == 2 * 14  # status, eleven fields, two flags; per arm


def test_ar_d_reports_the_adjudication_rate():
    results = {r.arm: r for r in rule_ar_d(_full(ROWS), REFERENCES)}
    assert results["G"].outcome is ArRuleOutcome.REPORTED and results["G"].numerator == 1
    assert results["A"].numerator == 0 and results["A"].denominator == 1
    assert all(r.outcome is ArRuleOutcome.NOT_EVALUABLE for r in rule_ar_d((), REFERENCES))


def test_ar_e_flags_annotation_driven_outcome_change_on_identical_prose():
    results = {r.arm: r for r in rule_ar_e(_full(ROWS))}
    assert results["G"].outcome is ArRuleOutcome.FIRED and results["G"].numerator >= 4
    assert dict(results["G"].detail)["e3_e_sem_001_resolved"] is False
    assert results["A"].outcome is ArRuleOutcome.NOT_FIRED


def test_ar_f_discloses_family_and_developer_correlation():
    assert rule_ar_f(ar.PANEL).outcome is ArRuleOutcome.NOT_FIRED
    correlated = (ar.CONFIG_A, ar.CONFIG_B,
                  ar.configuration(ReviewerRole.AUTONOMOUS_ADJUDICATOR, "Synthetic Family Gamma", " anthropic "))
    result = rule_ar_f(correlated)
    assert result.outcome is ArRuleOutcome.FIRED
    assert dict(result.detail)["correlations"] == (("GENERATOR", "AUTONOMOUS_ADJUDICATOR", "SAME_MODEL_DEVELOPER"),)


def test_ar_g_never_resolves_o3_or_supports_a_human_reliability_claim():
    detail = dict(rule_ar_g().detail)
    assert detail == {"resolves_o3": False, "o3_status": "PRE_RUN_REQUIRED",
                      "supports_human_reference_reliability_claim": False}


def test_ar_rules_do_not_use_the_human_decision_rules_or_thresholds():
    tree = ast.parse((REPO / "experiments/engine3_stage_f_ar/analysis.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "experiments.engine3_stage_f.decision_rules" not in imported
    source = (REPO / "experiments/engine3_stage_f_ar/analysis.py").read_text(encoding="utf-8")
    for threshold in ("0.95", "0.9", "0.1", "0.05", "60", "30"):
        assert threshold not in source


def test_locked_44_slot_generator_material_maps_to_the_ar_44_batch_and_never_to_a_full_run():
    assert BATCH_FOR_GENERATOR_RUN_KIND == {
        RunKind.DRY_RUN: AutonomousBatch.OFFLINE_DRY_RUN,
        RunKind.PILOT: AutonomousBatch.AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH,
        RunKind.FULL_RUN: AutonomousBatch.AR_FULL_RUN,
    }
    assert {b.value for b in AutonomousBatch} == {
        "OFFLINE_DRY_RUN", "AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH", "AR_FULL_RUN"}
