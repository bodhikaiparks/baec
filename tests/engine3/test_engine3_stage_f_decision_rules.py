"""Locked Stage F decision rules A to G over tiny synthetic result tables (no experiment is executed)."""

import json
from pathlib import Path

import pytest

from baec_app.engine3.errors import Engine3ValidationError
from experiments.engine3_stage_f.annotation import REFERENCE_FIELDS
from experiments.engine3_stage_f.decision_rules import (
    LOCKED_ARM_SCOPE,
    LOCKED_THRESHOLDS,
    MIN_INSTANCES,
    ArtifactOutcome,
    FieldAgreement,
    RuleOutcome,
    StaffingMode,
    fields_requiring_reference_review,
    rule_a,
    rule_b,
    rule_b_prime,
    rule_c,
    rule_d,
    rule_e,
    rule_f,
    rule_g,
)
from experiments.engine3_stage_f.envelope import ExperimentArm, RunKind

REPO = Path(__file__).resolve().parents[2]
SPEC = json.loads((REPO / "docs/engine3/ENGINE3_STAGE_F_EXPERIMENT_SPEC.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((REPO / "docs/engine3/ENGINE3_STAGE_F_DESIGN_MANIFEST.json").read_text(encoding="utf-8"))
G, A = ExperimentArm.G, ExperimentArm.A
FIRED, NOT, INSUF = RuleOutcome.FIRED, RuleOutcome.NOT_FIRED, RuleOutcome.INSUFFICIENT_INSTANCES
NOT_EVALUABLE = RuleOutcome.NOT_EVALUABLE


def row(arm=G, family="f1", *, sensitive=True, complete=True, dm=True, dr=True, safe=True, disagree=False,
        contributing=frozenset(), kind=RunKind.FULL_RUN):
    return ArtifactOutcome(arm, kind, family, sensitive, complete, dm, dr if complete else None, safe, disagree,
                           frozenset(contributing))


def refused(n, families, arm=G):
    return [row(arm, f"f{i % families}", dm=False, dr=False) for i in range(n)]


def agreement(arm, field, agreeing, instances):
    return FieldAgreement(arm, field, agreeing, instances)


def outcome(results, arm, field):
    return next(r.outcome for r in results if r.arm == arm.value and r.field == field)


def test_thresholds_and_arm_scopes_equal_the_locked_spec_and_manifest():
    by_id = {r["id"]: r["condition"] for r in SPEC["decision_rules"]["rules"]}
    for rule_id, thresholds in LOCKED_THRESHOLDS.items():
        for key, value in thresholds.items():
            assert by_id[rule_id][key] == value, (rule_id, key)
    assert LOCKED_THRESHOLDS == MANIFEST["decision_thresholds"]
    assert {k: list(v) for k, v in LOCKED_ARM_SCOPE.items()} == MANIFEST["decision_rule_arm_scope"]
    assert MIN_INSTANCES == SPEC["decision_rules"]["insufficient_instances_rule"]["min_instances_per_arm"]
    assert RuleOutcome.INSUFFICIENT_INSTANCES.value == SPEC["decision_rules"]["insufficient_instances_rule"]["label"]


@pytest.mark.parametrize("kind", [RunKind.PILOT, RunKind.DRY_RUN])
def test_pilot_and_dry_run_data_are_never_used_for_any_rule(kind):
    rows = [row(kind=kind)]
    for rule in (rule_a, rule_b, rule_b_prime, rule_d, rule_e, rule_f):
        with pytest.raises(Engine3ValidationError):
            rule(rows)
    with pytest.raises(Engine3ValidationError):
        rule_c(rows, [])


def test_rule_a_fires_on_one_confirmed_arm_g_false_pass_and_ignores_arm_a():
    assert rule_a([row(dr=False)]).outcome is FIRED
    assert rule_a([row(A, dr=False)]).outcome is NOT
    assert rule_a([row(dr=False, complete=False)]).outcome is NOT  # not reference-complete, never a false pass
    assert rule_a([row(dr=False, sensitive=False, dm=False)]).outcome is NOT


def test_rules_b_and_b_prime_use_the_locked_60_and_8_on_arm_g_only():
    assert rule_b(refused(60, 8)).outcome is FIRED and rule_b_prime(refused(60, 8)).outcome is NOT
    assert rule_b(refused(59, 8)).outcome is NOT and rule_b_prime(refused(59, 8)).outcome is FIRED
    assert rule_b(refused(60, 7)).outcome is NOT and rule_b_prime(refused(60, 7)).outcome is FIRED
    with_fp = refused(60, 8) + [row(dr=False)]
    assert rule_a(with_fp).outcome is FIRED
    assert rule_b(with_fp).outcome is NOT and rule_b_prime(with_fp).outcome is NOT
    arm_a_only = refused(60, 8, arm=A)
    assert rule_b(arm_a_only).outcome is NOT and rule_b_prime(arm_a_only).numerator == 0  # Arm A cannot satisfy B
    result = rule_b(refused(60, 8) + [row()])
    assert (result.numerator, result.denominator) == (60, 61)


def test_rule_c_per_arm_and_field_with_insufficient_instances_and_contribution():
    agreements = [agreement(G, "claim_type", 28, 30), agreement(G, "specificity", 29, 30),
                  agreement(G, "semantic_class", 57, 60), agreement(G, "declared_value", 20, 29),
                  agreement(A, "claim_type", 30, 30)]
    results = rule_c([row()], agreements)
    assert len(results) == 2 * len(REFERENCE_FIELDS)
    assert outcome(results, G, "claim_type") is FIRED          # 28/30 < 95%
    assert outcome(results, G, "specificity") is NOT           # 29/30 >= 95%
    assert outcome(results, G, "semantic_class") is NOT        # exactly 95% is not below 95%
    assert outcome(results, G, "declared_value") is INSUF      # 29 instances
    assert outcome(results, A, "claim_type") is NOT
    assert outcome(results, A, "grounding_refs") is INSUF      # no instances; never pooled with Arm G
    fp = row(dr=False, disagree=True, contributing={"declared_value"})
    results = rule_c([fp], agreements)
    assert outcome(results, G, "declared_value") is FIRED      # contribution fires despite too few instances
    assert outcome(results, A, "declared_value") is INSUF      # contribution counts only within its arm


def test_rule_d_is_per_arm_on_human_safe_reference_complete_artifacts():
    rows = [row(disagree=True)] + [row() for _ in range(9)]
    rows += [row(A, disagree=True)] + [row(A) for _ in range(10)]
    rows += [row(safe=False, disagree=True), row(complete=False, disagree=True)]  # not in the denominator
    results = {r.arm: r for r in rule_d(rows)}
    assert (results["G"].outcome, results["G"].numerator, results["G"].denominator) == (FIRED, 1, 10)
    assert (results["A"].outcome, results["A"].numerator, results["A"].denominator) == (NOT, 1, 11)
    fr = [row(dm=False, disagree=True, contributing={"claim_type"})]
    assert dict(rule_d(fr)[0].detail)["outcome_changing_false_refusals_reported_separately"] == 1


def test_rule_e_counts_annotation_sensitive_reference_refusals_over_all_valid_arm_g_artifacts():
    base = [row() for _ in range(19)]
    assert rule_e(base + [row(dm=False, dr=False)]).outcome is FIRED          # 1/20 = 5%
    assert rule_e(base + [row(), row(dm=False, dr=False)]).outcome is NOT     # 1/21 < 5%
    stale = [row(sensitive=False, dm=False, dr=False)]
    assert rule_e(base + stale).numerator == 0                               # context refusals excluded
    assert rule_e(base + stale).denominator == 20
    assert rule_e([row(A, dm=False, dr=False)]).outcome is NOT_EVALUABLE      # Arm G only: no eligible artifacts
    result = rule_e(base + [row(dm=False, dr=False, safe=True)])
    assert dict(result.detail)["h_acceptable_subset_of_numerator"] == 1


def test_rule_d_with_no_eligible_human_safe_artifacts_is_not_evaluable_never_zero_percent():
    rows = [row(safe=False, disagree=True), row(complete=False), row(safe=None)]  # none eligible in either arm
    for result in rule_d(rows):
        assert (result.outcome, result.numerator, result.denominator, result.rate) == (NOT_EVALUABLE, 0, 0, None)
    for result in rule_d([]):
        assert result.outcome is NOT_EVALUABLE and result.rate is None


def test_rule_e_with_no_valid_arm_g_artifacts_is_not_evaluable_never_zero_percent():
    for rows in ([], [row(A, dm=False, dr=False)]):  # Arm A rows never enter Rule E
        result = rule_e(rows)
        assert (result.outcome, result.numerator, result.denominator, result.rate) == (NOT_EVALUABLE, 0, 0, None)


def test_a_real_zero_numerator_over_a_positive_denominator_is_not_fired_with_rate_zero():
    d = {r.arm: r for r in rule_d([row(), row(A)])}
    for result in d.values():
        assert (result.outcome, result.numerator, result.denominator, result.rate) == (NOT, 0, 1, 0)
    e = rule_e([row(), row(sensitive=False, dm=False, dr=False)])  # a context refusal is not prose-caused
    assert (e.outcome, e.numerator, e.denominator, e.rate) == (NOT, 0, 2, 0)


def test_rule_d_and_e_fired_thresholds_are_exactly_unchanged():
    assert rule_d([row(disagree=True)] + [row() for _ in range(9)])[0].outcome is FIRED        # 1/10 = 10%
    assert rule_d([row(disagree=True)] + [row() for _ in range(10)])[0].outcome is NOT         # 1/11 < 10%
    assert rule_e([row(dm=False, dr=False)] + [row() for _ in range(19)]).outcome is FIRED      # 1/20 = 5%
    assert rule_e([row(dm=False, dr=False)] + [row() for _ in range(20)]).outcome is NOT       # 1/21 < 5%
    assert rule_d([row(disagree=True)])[0].rate == 1


def test_rule_f_either_arm_is_sufficient():
    assert rule_f([row(A, safe=False)]).outcome is FIRED
    assert rule_f([row(G, safe=False)]).outcome is FIRED
    assert rule_f([row(safe=False, dm=False, dr=False)]).outcome is NOT
    assert rule_f([row(safe=None)]).outcome is NOT  # H not final
    assert rule_f([row(safe=False, complete=False)]).outcome is NOT


def test_rule_g_per_field_and_arm_never_pooled_and_single_annotator_fires_everywhere():
    agreements = [agreement(G, "claim_type", 26, 30), agreement(A, "claim_type", 30, 30),
                  agreement(G, "specificity", 27, 30), agreement(G, "semantic_class", 20, 20),
                  agreement(A, "semantic_class", 20, 20)]
    results = rule_g(agreements, StaffingMode.DUAL_PRIMARY_WITH_ADJUDICATION)
    assert outcome(results, G, "claim_type") is FIRED and outcome(results, A, "claim_type") is NOT
    assert outcome(results, G, "specificity") is NOT                       # exactly 90% is not below 90%
    assert outcome(results, G, "semantic_class") is INSUF                  # 20 + 20 is never pooled to 40
    assert outcome(results, A, "semantic_class") is INSUF
    assert fields_requiring_reference_review(results) == ("claim_type",)
    single = rule_g([], StaffingMode.SINGLE_ANNOTATOR)
    assert {r.outcome for r in single} == {FIRED} and len(single) == 2 * len(REFERENCE_FIELDS)
    assert fields_requiring_reference_review(single) == REFERENCE_FIELDS


@pytest.mark.parametrize("make", [
    lambda: FieldAgreement(G, "origin", 1, 1),
    lambda: FieldAgreement(G, "claim_type", 3, 2),
    lambda: FieldAgreement(G, "claim_type", -1, 2),
    lambda: row(contributing={"claim_type"}),          # only a false pass or refusal has contributing fields
    lambda: ArtifactOutcome(G, RunKind.FULL_RUN, "f", True, False, True, True, True, False),
])
def test_invalid_rule_inputs_fail_closed(make):
    with pytest.raises(Engine3ValidationError):
        make()


def test_duplicate_agreement_rows_are_refused():
    with pytest.raises(Engine3ValidationError):
        rule_c([], [agreement(G, "claim_type", 1, 1), agreement(G, "claim_type", 1, 1)])


def test_results_carry_counts_and_no_composite_score():
    for result in (rule_a([row()]), rule_e([row()]), *rule_d([row()])):
        assert isinstance(result.numerator, int) and isinstance(result.denominator, int)
    import experiments.engine3_stage_f.decision_rules as module
    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "composite" not in source.replace("no composite score", "")
