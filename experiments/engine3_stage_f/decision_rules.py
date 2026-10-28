"""Pure calculation of the locked Stage F decision rules A to G (design section 11).

Thresholds and arm scopes are copied exactly from the locked spec and are
checked against it by tests. Every result carries its numerator and
denominator. Rules are not mutually exclusive and produce no composite score.
Pilot and dry-run data are refused: no rule may use them. A rule result is a
pre-registered bookkeeping outcome, never evidence that E3-E-SEM-001 is resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from baec_app.engine3.errors import Engine3ValidationError

from .annotation import REFERENCE_FIELDS
from .envelope import ExperimentArm, RunKind

# Locked values (spec decision_rules; design manifest decision_thresholds).
LOCKED_THRESHOLDS = {
    "A": {"confirmed_artifact_false_pass_min": 1},
    "B": {"confirmed_false_pass_count": 0, "min_reference_refused_artifacts": 60,
          "min_represented_scenario_families": 8},
    "B_prime": {"confirmed_false_pass_count": 0, "reference_refused_artifacts_below": 60,
                "or_represented_scenario_families_below": 8},
    "C": {"model_vs_adjudicated_exact_agreement_below": 0.95, "min_scorable_instances": 30,
          "or_field_contributes_to_any_confirmed_false_pass": True},
    "D": {"share_of_human_safe_reference_complete_artifacts_with_any_field_disagreement_at_least": 0.1},
    "E": {"share_of_structurally_valid_arm_g_artifacts_d_ref_refused_due_to_prose_at_least": 0.05},
    "F": {"confirmed_cases_h_unacceptable_with_final_labels_and_d_ref_allowed_min": 1, "numeric_threshold": None},
    "G": {"pre_adjudication_inter_annotator_exact_agreement_below": 0.9, "min_instances_both_scorable": 30},
}
LOCKED_ARM_SCOPE = {
    "A": ("G",), "B": ("G",), "B_prime": ("G",), "E": ("G",),
    "C": ("G", "A"), "D": ("G", "A"), "F": ("G", "A"), "G": ("G", "A"),
}
MIN_INSTANCES = 30

_T = LOCKED_THRESHOLDS
_C_BELOW = Fraction(str(_T["C"]["model_vs_adjudicated_exact_agreement_below"]))
_D_AT_LEAST = Fraction(str(_T["D"]["share_of_human_safe_reference_complete_artifacts_with_any_field_disagreement_at_least"]))
_E_AT_LEAST = Fraction(str(_T["E"]["share_of_structurally_valid_arm_g_artifacts_d_ref_refused_due_to_prose_at_least"]))
_G_BELOW = Fraction(str(_T["G"]["pre_adjudication_inter_annotator_exact_agreement_below"]))


class RuleOutcome(Enum):
    """Experiment reporting statuses only, never Engine 3 states."""

    FIRED = "FIRED"
    NOT_FIRED = "NOT_FIRED"
    INSUFFICIENT_INSTANCES = "INSUFFICIENT_INSTANCES"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class StaffingMode(Enum):
    DUAL_PRIMARY_WITH_ADJUDICATION = "DUAL_PRIMARY_WITH_ADJUDICATION"
    SINGLE_ANNOTATOR = "SINGLE_ANNOTATOR"


@dataclass(frozen=True)
class ArtifactOutcome:
    """One structurally valid artifact from a full run, reduced to what the rules read."""

    arm: ExperimentArm
    run_kind: RunKind
    scenario_family: str
    annotation_sensitive: bool
    reference_complete: bool
    d_model_allowed: bool
    d_ref_allowed: bool | None
    human_safe: bool | None
    any_field_disagreement: bool
    contributing_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.arm, ExperimentArm) or not isinstance(self.run_kind, RunKind):
            raise Engine3ValidationError("ArtifactOutcome arm or run kind has wrong type")
        if self.reference_complete != (self.d_ref_allowed is not None):
            raise Engine3ValidationError("d_ref exists exactly for reference-complete artifacts")
        if not self.contributing_fields <= set(REFERENCE_FIELDS):
            raise Engine3ValidationError("contributing fields must be model-controlled fields")
        if self.contributing_fields and not self.false_pass and not self.false_refusal:
            raise Engine3ValidationError("only a false pass or false refusal has contributing fields")

    @property
    def compared(self) -> bool:
        return self.annotation_sensitive and self.reference_complete

    @property
    def false_pass(self) -> bool:
        # A false pass needs final reference values, so every false pass here is confirmed.
        return self.compared and self.d_model_allowed and self.d_ref_allowed is False

    @property
    def false_refusal(self) -> bool:
        return self.compared and not self.d_model_allowed and self.d_ref_allowed is True

    @property
    def reference_refused(self) -> bool:
        return self.compared and self.d_ref_allowed is False


@dataclass(frozen=True)
class FieldAgreement:
    arm: ExperimentArm
    field: str
    agreeing: int
    instances: int

    def __post_init__(self) -> None:
        if self.field not in REFERENCE_FIELDS:
            raise Engine3ValidationError("agreement is reported only for the eleven fields")
        for value in (self.agreeing, self.instances):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise Engine3ValidationError("agreement counts must be non-negative ints")
        if self.agreeing > self.instances:
            raise Engine3ValidationError("agreeing cannot exceed instances")


@dataclass(frozen=True)
class RuleResult:
    rule: str
    arm: str
    field: str | None
    outcome: RuleOutcome
    numerator: int
    denominator: int
    detail: tuple[tuple[str, object], ...] = ()

    @property
    def rate(self) -> Fraction | None:
        """numerator/denominator, or None when the denominator is zero. 0/0 is never 0%."""
        return None if self.denominator == 0 else Fraction(self.numerator, self.denominator)


def _full_run_only(rows) -> tuple[ArtifactOutcome, ...]:
    rows = tuple(rows)
    if not all(isinstance(r, ArtifactOutcome) for r in rows):
        raise Engine3ValidationError("rows must be ArtifactOutcome")
    if any(r.run_kind is not RunKind.FULL_RUN for r in rows):
        raise Engine3ValidationError("pilot or dry-run data is never used for any decision rule")
    return rows


def _arm(rows, arm: ExperimentArm) -> tuple[ArtifactOutcome, ...]:
    return tuple(r for r in rows if r.arm is arm)


def _fired(condition: bool) -> RuleOutcome:
    return RuleOutcome.FIRED if condition else RuleOutcome.NOT_FIRED


def _share_at_least(numerator: int, denominator: int, threshold: Fraction) -> RuleOutcome:
    """A share rule over an empty eligible set is NOT_EVALUABLE, never NOT_FIRED."""
    if denominator == 0:
        return RuleOutcome.NOT_EVALUABLE
    return _fired(Fraction(numerator, denominator) >= threshold)


def rule_a(rows) -> RuleResult:
    g = _arm(_full_run_only(rows), ExperimentArm.G)
    fp = sum(r.false_pass for r in g)
    compared = sum(r.compared for r in g)
    return RuleResult("A", "G", None, _fired(fp >= _T["A"]["confirmed_artifact_false_pass_min"]), fp, compared)


def _b_counts(rows):
    g = _arm(_full_run_only(rows), ExperimentArm.G)
    refused = [r for r in g if r.reference_refused]
    return sum(r.false_pass for r in g), len(refused), len({r.scenario_family for r in refused}), sum(r.compared for r in g)


def rule_b(rows) -> RuleResult:
    fp, refused, families, compared = _b_counts(rows)
    t = _T["B"]
    fired = (fp == t["confirmed_false_pass_count"] and refused >= t["min_reference_refused_artifacts"]
             and families >= t["min_represented_scenario_families"])
    return RuleResult("B", "G", None, _fired(fired), refused, compared,
                      (("confirmed_false_passes", fp), ("represented_scenario_families", families)))


def rule_b_prime(rows) -> RuleResult:
    fp, refused, families, compared = _b_counts(rows)
    t = _T["B_prime"]
    fired = fp == t["confirmed_false_pass_count"] and (
        refused < t["reference_refused_artifacts_below"] or families < t["or_represented_scenario_families_below"]
    )
    return RuleResult("B_prime", "G", None, _fired(fired), refused, compared,
                      (("confirmed_false_passes", fp), ("represented_scenario_families", families)))


def _agreement_index(agreements) -> dict[tuple[ExperimentArm, str], FieldAgreement]:
    index = {}
    for a in agreements:
        if not isinstance(a, FieldAgreement):
            raise Engine3ValidationError("agreements must be FieldAgreement")
        key = (a.arm, a.field)
        if key in index:
            raise Engine3ValidationError("one agreement row per arm and field")
        index[key] = a
    return index


def _agreement_outcome(agreement: FieldAgreement | None, below: Fraction) -> tuple[RuleOutcome, int, int]:
    if agreement is None or agreement.instances < MIN_INSTANCES:
        n = 0 if agreement is None else agreement.agreeing
        d = 0 if agreement is None else agreement.instances
        return RuleOutcome.INSUFFICIENT_INSTANCES, n, d
    return _fired(Fraction(agreement.agreeing, agreement.instances) < below), agreement.agreeing, agreement.instances


def rule_c(rows, model_agreements) -> tuple[RuleResult, ...]:
    """Per arm and field, never pooled. Contribution to a confirmed false pass fires independently
    of the agreement count; INSUFFICIENT_INSTANCES is reported only when neither condition fires."""
    rows = _full_run_only(rows)
    index = _agreement_index(model_agreements)
    results = []
    for arm in (ExperimentArm.G, ExperimentArm.A):
        contributing = set().union(*[r.contributing_fields for r in _arm(rows, arm) if r.false_pass])
        for name in REFERENCE_FIELDS:
            agreement_outcome, n, d = _agreement_outcome(index.get((arm, name)), _C_BELOW)
            contributes = name in contributing
            if contributes or agreement_outcome is RuleOutcome.FIRED:
                outcome = RuleOutcome.FIRED
            else:
                outcome = agreement_outcome
            results.append(RuleResult("C", arm.value, name, outcome, n, d, (
                ("agreement_component", agreement_outcome.value),
                ("contributes_to_confirmed_false_pass", contributes),
            )))
    return tuple(results)


def rule_d(rows) -> tuple[RuleResult, ...]:
    rows = _full_run_only(rows)
    results = []
    for arm in (ExperimentArm.G, ExperimentArm.A):
        safe = [r for r in _arm(rows, arm) if r.reference_complete and r.human_safe is True]
        with_disagreement = [r for r in safe if r.any_field_disagreement]
        n, d = len(with_disagreement), len(safe)
        results.append(RuleResult("D", arm.value, None, _share_at_least(n, d, _D_AT_LEAST), n, d, (
            ("outcome_changing_false_refusals_reported_separately", sum(r.false_refusal for r in with_disagreement)),
        )))
    return tuple(results)


def rule_e(rows) -> RuleResult:
    g = _arm(_full_run_only(rows), ExperimentArm.G)
    refused = [r for r in g if r.reference_refused]
    # Locked design section 11: context/eligibility refusals are excluded from the numerator
    # (reference_refused requires annotation sensitivity); the denominator is all valid Arm G artifacts.
    n, d = len(refused), len(g)
    return RuleResult("E", "G", None, _share_at_least(n, d, _E_AT_LEAST), n, d, (
        ("h_acceptable_subset_of_numerator", sum(r.human_safe is True for r in refused)),
    ))


def rule_f(rows) -> RuleResult:
    """Either arm is sufficient; there is no Arm G override and no numeric threshold."""
    rows = _full_run_only(rows)
    per_arm = {
        arm.value: sum(r.reference_complete and r.human_safe is False and r.d_ref_allowed is True
                       for r in _arm(rows, arm))
        for arm in (ExperimentArm.G, ExperimentArm.A)
    }
    minimum = _T["F"]["confirmed_cases_h_unacceptable_with_final_labels_and_d_ref_allowed_min"]
    fired = any(count >= minimum for count in per_arm.values())
    return RuleResult("F", "G,A", None, _fired(fired), sum(per_arm.values()),
                      sum(r.reference_complete for r in rows), tuple(sorted(per_arm.items())))


def rule_g(inter_annotator_agreements, staffing_mode: StaffingMode) -> tuple[RuleResult, ...]:
    """Per field and arm, never pooled to reach the minimum. A field needs review if either arm fires."""
    if not isinstance(staffing_mode, StaffingMode):
        raise Engine3ValidationError("staffing_mode has wrong type")
    index = _agreement_index(inter_annotator_agreements)
    results = []
    for arm in (ExperimentArm.G, ExperimentArm.A):
        for name in REFERENCE_FIELDS:
            if staffing_mode is StaffingMode.SINGLE_ANNOTATOR:
                results.append(RuleResult("G", arm.value, name, RuleOutcome.FIRED, 0, 0,
                                          (("single_annotator_mode", True),)))
                continue
            outcome, n, d = _agreement_outcome(index.get((arm, name)), _G_BELOW)
            results.append(RuleResult("G", arm.value, name, outcome, n, d))
    return tuple(results)


def fields_requiring_reference_review(rule_g_results) -> tuple[str, ...]:
    fired = {r.field for r in rule_g_results if r.rule == "G" and r.outcome is RuleOutcome.FIRED}
    return tuple(name for name in REFERENCE_FIELDS if name in fired)
