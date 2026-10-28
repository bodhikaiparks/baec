"""Stage F-AR comparison, metrics, and decision rules AR-A to AR-G.

For identical prose and context, Stage D is called unchanged twice: once with the
generator's annotations (D_generator) and once with the eleven fields replaced by
the final autonomous reference (D_autonomous). The case is built exactly as in the
locked Stage F comparison. Nothing is normalized before replay.

Every figure is a count with its denominator, reported per AR batch and per arm,
never pooled, and never combined into a composite score. Outcomes are relative to
a blinded autonomous reviewer consensus only. They are not accuracy against
humans, and the human Stage F decision rules A to G are not computed here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum

from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.models import DraftSegment, ValidationReport
from baec_app.engine3.offline_evaluation import EvaluationContext
from baec_app.engine3.validator import validate_case
from experiments.engine3_stage_f.annotation import REFERENCE_FIELDS, AnnotationStatus, packet_id_for
from experiments.engine3_stage_f.comparison import (
    ALLOWED,
    FieldDisagreement,
    _case,
    annotation_insensitive,
    direction,
    field_equal,
)
from experiments.engine3_stage_f.envelope import ExperimentalProposal, ExperimentArm

from .panel import ReviewerConfiguration, family_correlations
from .review import (
    BATCH_FOR_GENERATOR_RUN_KIND,
    DISAGREEMENT_ITEMS,
    AutonomousBatch,
    AutonomousReference,
    AutonomousReferenceKind,
)


class ArComparisonCategory(Enum):
    """Generator Stage D outcome against the autonomous-reference Stage D outcome. Experiment labels only."""

    CONCORDANT_PASS = "CONCORDANT_PASS"
    CONCORDANT_REFUSAL_SAME_PRIMARY = "CONCORDANT_REFUSAL_SAME_PRIMARY"
    CONCORDANT_REFUSAL_DIFFERENT_PRIMARY = "CONCORDANT_REFUSAL_DIFFERENT_PRIMARY"
    AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS = "AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS"
    AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL = "AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL"


_SAME_OUTCOME = frozenset({ArComparisonCategory.CONCORDANT_PASS, ArComparisonCategory.CONCORDANT_REFUSAL_SAME_PRIMARY})


def _primary(report: ValidationReport) -> str:
    return ALLOWED if report.allowed else report.primary_reason_code


def _category(d_generator: ValidationReport, d_autonomous: ValidationReport) -> ArComparisonCategory:
    if d_generator.allowed and d_autonomous.allowed:
        return ArComparisonCategory.CONCORDANT_PASS
    if d_generator.allowed:
        return ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS
    if d_autonomous.allowed:
        return ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL
    if d_generator.primary_reason_code == d_autonomous.primary_reason_code:
        return ArComparisonCategory.CONCORDANT_REFUSAL_SAME_PRIMARY
    return ArComparisonCategory.CONCORDANT_REFUSAL_DIFFERENT_PRIMARY


@dataclass(frozen=True)
class ArArtifactComparison:
    """One generator artifact against the autonomous reference for its packet."""

    artifact_id: str
    packet_id: str
    arm: ExperimentArm
    batch: AutonomousBatch
    scenario_family: str
    d_generator: ValidationReport
    annotation_sensitive: bool
    reference_kind: AutonomousReferenceKind | None
    reference_complete: bool
    d_autonomous: ValidationReport | None
    category: ArComparisonCategory | None
    reason_transition: tuple[str, str] | None
    disagreements: tuple[FieldDisagreement, ...]
    segment_statuses: tuple[AnnotationStatus, ...]

    @property
    def compared(self) -> bool:
        return self.category is not None

    @property
    def potential_false_pass(self) -> bool:
        return self.category is ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_PASS

    @property
    def potential_false_refusal(self) -> bool:
        return self.category is ArComparisonCategory.AUTONOMOUS_RELATIVE_POTENTIAL_FALSE_REFUSAL

    @property
    def outcome_changed(self) -> bool:
        return self.compared and self.category not in _SAME_OUTCOME


def compare_to_autonomous_reference(
    proposal: ExperimentalProposal,
    artifact_id: str,
    context: EvaluationContext,
    reference: AutonomousReference | None,
    scenario_family: str,
) -> ArArtifactComparison:
    """Replay one artifact. No reference (a rejected review) or an incomplete one means no D_autonomous."""
    if not isinstance(proposal, ExperimentalProposal):
        raise Engine3ValidationError("proposal has wrong type")
    if not isinstance(context, EvaluationContext):
        raise Engine3ValidationError("context has wrong type")
    if not isinstance(scenario_family, str) or not scenario_family:
        raise Engine3ValidationError("scenario_family must be a non-empty string")
    matches = [a for a in proposal.artifacts if a.artifact_id == artifact_id]
    if not matches:
        raise Engine3ValidationError("artifact not found in proposal")
    artifact = matches[0]
    packet_id = packet_id_for(artifact, context)
    batch = BATCH_FOR_GENERATOR_RUN_KIND[proposal.binding.run_kind]
    if reference is not None:
        if not isinstance(reference, AutonomousReference):
            raise Engine3ValidationError("reference has wrong type")
        if reference.packet_id != packet_id or len(reference.segments) != len(artifact.segments):
            raise Engine3ValidationError("reference does not belong to this prose and context")
        if reference.batch is not batch:
            raise Engine3ValidationError("reference belongs to a different batch")

    d_generator = validate_case(_case(proposal, artifact, context, artifact.segments))
    sensitive = not annotation_insensitive(d_generator)
    finals = () if reference is None else reference.segments
    complete = reference is not None and reference.reference_complete

    replaced: list[DraftSegment] = []
    disagreements = []
    for n, (segment, final) in enumerate(zip(artifact.segments, finals), start=1):
        if final.fields is None:
            continue
        ref_segment = replace(segment, **{name: getattr(final.fields, name) for name in REFERENCE_FIELDS})
        replaced.append(ref_segment)
        for name in REFERENCE_FIELDS:
            m, r = getattr(segment, name), getattr(ref_segment, name)
            if not field_equal(name, m, r):
                disagreements.append(FieldDisagreement(n, segment.segment_id, name, m, r,
                                                       direction(name, segment, ref_segment, context)))

    d_autonomous = category = transition = None
    if complete:
        d_autonomous = validate_case(_case(proposal, artifact, context, tuple(replaced)))
        if sensitive:
            category = _category(d_generator, d_autonomous)
            transition = (_primary(d_generator), _primary(d_autonomous))
    return ArArtifactComparison(
        artifact_id=artifact.artifact_id,
        packet_id=packet_id,
        arm=proposal.binding.arm,
        batch=batch,
        scenario_family=scenario_family,
        d_generator=d_generator,
        annotation_sensitive=sensitive,
        reference_kind=None if reference is None else reference.kind,
        reference_complete=complete,
        d_autonomous=d_autonomous,
        category=category,
        reason_transition=transition,
        disagreements=tuple(disagreements),
        segment_statuses=tuple(s.status for s in finals),
    )


# --- metrics (each separate; no composite) ----------------------------------------------------------


def _rows(comparisons) -> tuple[ArArtifactComparison, ...]:
    rows = tuple(comparisons)
    if not all(isinstance(r, ArArtifactComparison) for r in rows):
        raise Engine3ValidationError("rows must be ArArtifactComparison")
    return rows


def _references_for(rows, references: dict[str, AutonomousReference]) -> tuple[AutonomousReference, ...]:
    """Distinct references for the packets in these rows (identical prose is reviewed once)."""
    if not isinstance(references, dict) or not all(isinstance(r, AutonomousReference) for r in references.values()):
        raise Engine3ValidationError("references must map packet ids to AutonomousReference")
    if any(packet_id != r.packet_id for packet_id, r in references.items()):
        raise Engine3ValidationError("a reference is filed under the wrong packet id")
    return tuple(references[p] for p in sorted({r.packet_id for r in rows}) if p in references)


def ab_agreement(references) -> dict[str, tuple[int, int]]:
    """Pre-adjudication Reviewer A / Reviewer B exact agreement: status over every segment, each field and
    flag over segments both marked SCORABLE. Item -> (agreeing, instances)."""
    counts = {item: [0, 0] for item in ("status",) + REFERENCE_FIELDS + ("formatting_variant", "mixed_reference_modes")}
    for reference in references:
        for a, b in zip(reference.reviewer_a.segments, reference.reviewer_b.segments):
            counts["status"][1] += 1
            counts["status"][0] += a.status is b.status
            if a.status is not AnnotationStatus.SCORABLE or b.status is not AnnotationStatus.SCORABLE:
                continue
            for name in REFERENCE_FIELDS:
                counts[name][1] += 1
                counts[name][0] += getattr(a.fields, name) == getattr(b.fields, name)
            for name in ("formatting_variant", "mixed_reference_modes"):
                counts[name][1] += 1
                counts[name][0] += getattr(a, name) == getattr(b, name)
    return {item: (agree, total) for item, (agree, total) in counts.items()}


def adjudication_count(references) -> tuple[int, int]:
    references = tuple(references)
    return sum(r.adjudicator is not None for r in references), len(references)


def adjudicated_item_counts(references) -> dict[str, int]:
    """Packets in which each governed item required adjudication."""
    counts = Counter(item for r in references for item in r.adjudicated_items)
    return {item: counts[item] for item in DISAGREEMENT_ITEMS}


def generator_field_agreement(comparisons) -> dict[str, tuple[int, int]]:
    """Generator vs autonomous reference exact agreement per field over autonomous SCORABLE segments."""
    rows = _rows(comparisons)
    instances = sum(s is AnnotationStatus.SCORABLE for r in rows for s in r.segment_statuses)
    disagreeing = Counter(d.field for r in rows for d in r.disagreements)
    return {name: (instances - disagreeing[name], instances) for name in REFERENCE_FIELDS}


def generator_status_agreement(comparisons) -> tuple[int, int]:
    """The generator labels every segment; agreement means the autonomous reference also marked it SCORABLE."""
    rows = _rows(comparisons)
    statuses = [s for r in rows for s in r.segment_statuses]
    return sum(s is AnnotationStatus.SCORABLE for s in statuses), len(statuses)


def outcome_table(comparisons) -> dict[str, int]:
    rows = _rows(comparisons)
    counts = Counter(r.category.value for r in rows if r.compared)
    table = {c.value: counts[c.value] for c in ArComparisonCategory}
    table["annotation_insensitive"] = sum(not r.annotation_sensitive for r in rows)
    table["no_autonomous_reference"] = sum(r.reference_kind is None for r in rows)
    table["autonomous_reference_incomplete"] = sum(r.reference_kind is not None and not r.reference_complete
                                                   for r in rows)
    return table


def disagreement_families(comparisons) -> dict[tuple[str, str], int]:
    """Generator vs autonomous-reference field disagreements grouped by (field, permissive/restrictive direction)."""
    counts = Counter((d.field, d.direction.value) for r in _rows(comparisons) for d in r.disagreements)
    return dict(sorted(counts.items()))


def coverage(comparisons) -> dict[str, tuple[int, int]]:
    rows = _rows(comparisons)
    statuses = Counter(s for r in rows for s in r.segment_statuses)
    segments = sum(statuses.values())
    return {
        "artifacts_with_autonomous_reference": (sum(r.reference_kind is not None for r in rows), len(rows)),
        "artifacts_autonomous_reference_complete": (sum(r.reference_complete for r in rows), len(rows)),
        "segments_unscorable": (statuses[AnnotationStatus.UNSCORABLE_SEGMENT_STRUCTURE], segments),
        "segments_unresolved_adjudication_required": (statuses[AnnotationStatus.ADJUDICATION_REQUIRED], segments),
    }


def identical_prose_sensitivity(comparisons) -> dict[str, tuple[int, int]]:
    """Identical prose (same packet) whose Stage D outcome changes only because annotations change."""
    rows = _rows(comparisons)
    by_packet: dict[str, list[ArArtifactComparison]] = {}
    for r in rows:
        by_packet.setdefault(r.packet_id, []).append(r)
    repeated = [group for group in by_packet.values() if len(group) > 1]
    varying = sum(len({_primary(r.d_generator) for r in group}) > 1 for group in repeated)
    compared = [r for r in rows if r.compared]
    return {
        "compared_artifacts_outcome_changed_under_autonomous_reference": (
            sum(r.outcome_changed for r in compared), len(compared)),
        "repeated_packets_with_varying_generator_outcome": (varying, len(repeated)),
    }


def metrics_by_group(comparisons, references: dict[str, AutonomousReference]) -> dict[tuple[str, str], dict]:
    """Every metric, separately per (AR batch, arm). Never pooled across arms or batches."""
    rows = _rows(comparisons)
    report = {}
    for batch in AutonomousBatch:
        for arm in ExperimentArm:
            group = tuple(r for r in rows if r.batch is batch and r.arm is arm)
            if not group:
                continue
            refs = _references_for(group, references)
            report[(batch.value, arm.value)] = {
                "ab_agreement": ab_agreement(refs),
                "adjudication": adjudication_count(refs),
                "adjudicated_items": adjudicated_item_counts(refs),
                "generator_field_agreement": generator_field_agreement(group),
                "generator_status_agreement": generator_status_agreement(group),
                "outcome_table": outcome_table(group),
                "disagreement_families": disagreement_families(group),
                "coverage": coverage(group),
                "identical_prose_sensitivity": identical_prose_sensitivity(group),
            }
    return report


# --- decision rules AR-A to AR-G --------------------------------------------------------------------


class ArRuleOutcome(Enum):
    """Stage F-AR reporting statuses only, never Engine 3 states. REPORTED carries no pass/fail threshold."""

    FIRED = "FIRED"
    NOT_FIRED = "NOT_FIRED"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    REPORTED = "REPORTED"


@dataclass(frozen=True)
class ArRuleResult:
    rule: str
    arm: str
    item: str | None
    outcome: ArRuleOutcome
    numerator: int
    denominator: int
    detail: tuple[tuple[str, object], ...] = ()


def _full_run_only(comparisons) -> tuple[ArArtifactComparison, ...]:
    rows = _rows(comparisons)
    if any(r.batch is not AutonomousBatch.AR_FULL_RUN for r in rows):
        raise Engine3ValidationError("AR-44 or dry-run data is never used for any Stage F-AR decision rule")
    return rows


def _arm(rows, arm: ExperimentArm) -> tuple[ArArtifactComparison, ...]:
    return tuple(r for r in rows if r.arm is arm)


def rule_ar_a(comparisons) -> tuple[ArRuleResult, ...]:
    """Every generator pass refused under the autonomous reference, reported individually, per arm."""
    rows = _full_run_only(comparisons)
    results = []
    for arm in ExperimentArm:
        group = _arm(rows, arm)
        compared = [r for r in group if r.compared]
        cases = tuple((r.artifact_id, r.packet_id, r.reason_transition) for r in compared if r.potential_false_pass)
        outcome = ArRuleOutcome.NOT_EVALUABLE if not compared else (
            ArRuleOutcome.FIRED if cases else ArRuleOutcome.NOT_FIRED)
        results.append(ArRuleResult("AR-A", arm.value, None, outcome, len(cases), len(compared),
                                    (("potential_false_passes", cases),)))
    return tuple(results)


def rule_ar_b(comparisons) -> tuple[ArRuleResult, ...]:
    """Zero potential false passes supports only a bounded synthetic autonomous-consensus observation."""
    rows = _full_run_only(comparisons)
    results = []
    for arm in ExperimentArm:
        compared = [r for r in _arm(rows, arm) if r.compared]
        passes = sum(r.potential_false_pass for r in compared)
        refused = [r for r in compared if not r.d_autonomous.allowed]
        outcome = ArRuleOutcome.NOT_EVALUABLE if not compared else (
            ArRuleOutcome.FIRED if passes == 0 else ArRuleOutcome.NOT_FIRED)
        results.append(ArRuleResult("AR-B", arm.value, None, outcome, passes, len(compared), (
            ("supports", "BOUNDED_SYNTHETIC_AUTONOMOUS_CONSENSUS_OBSERVATION_ONLY"),
            ("establishes_safety", False),
            ("establishes_human_semantic_accuracy", False),
            ("autonomous_reference_refused_artifacts", len(refused)),
            ("represented_scenario_families", len({r.scenario_family for r in refused})),
        )))
    return tuple(results)


def rule_ar_c(comparisons, references: dict[str, AutonomousReference]) -> tuple[ArRuleResult, ...]:
    """Per arm and item: Reviewer A / Reviewer B disagreement, reported as autonomous-reference instability.
    No threshold is frozen, so every item is REPORTED with its counts; adjudication never hides it."""
    rows = _full_run_only(comparisons)
    results = []
    for arm in ExperimentArm:
        agreement = ab_agreement(_references_for(_arm(rows, arm), references))
        for item, (agree, total) in agreement.items():
            results.append(ArRuleResult("AR-C", arm.value, item, ArRuleOutcome.REPORTED, total - agree, total,
                                        (("autonomous_reference_instability_present", total > agree),)))
    return tuple(results)


def rule_ar_d(comparisons, references: dict[str, AutonomousReference]) -> tuple[ArRuleResult, ...]:
    """Adjudication rate per arm, reported prominently. High adjudication means an unstable autonomous panel."""
    rows = _full_run_only(comparisons)
    results = []
    for arm in ExperimentArm:
        refs = _references_for(_arm(rows, arm), references)
        adjudicated, total = adjudication_count(refs)
        outcome = ArRuleOutcome.NOT_EVALUABLE if total == 0 else ArRuleOutcome.REPORTED
        results.append(ArRuleResult("AR-D", arm.value, None, outcome, adjudicated, total,
                                    tuple(adjudicated_item_counts(refs).items())))
    return tuple(results)


def rule_ar_e(comparisons) -> tuple[ArRuleResult, ...]:
    """Identical prose whose Stage D outcome changes only because annotations change (E3-E-SEM-001 type)."""
    rows = _full_run_only(comparisons)
    results = []
    for arm in ExperimentArm:
        sensitivity = identical_prose_sensitivity(_arm(rows, arm))
        changed, compared = sensitivity["compared_artifacts_outcome_changed_under_autonomous_reference"]
        varying, repeated = sensitivity["repeated_packets_with_varying_generator_outcome"]
        if compared == 0 and repeated == 0:
            outcome = ArRuleOutcome.NOT_EVALUABLE
        else:
            outcome = ArRuleOutcome.FIRED if changed or varying else ArRuleOutcome.NOT_FIRED
        results.append(ArRuleResult("AR-E", arm.value, None, outcome, changed, compared, (
            ("repeated_packets_with_varying_generator_outcome", (varying, repeated)),
            ("e3_e_sem_001_resolved", False),
        )))
    return tuple(results)


def rule_ar_f(panel: tuple[ReviewerConfiguration, ...]) -> ArRuleResult:
    """Shared reviewer families or developers weaken the independence claim and must be disclosed."""
    correlations = family_correlations(panel)
    outcome = ArRuleOutcome.FIRED if correlations else ArRuleOutcome.NOT_FIRED
    return ArRuleResult("AR-F", "G,A", None, outcome, len(correlations), 6, (("correlations", correlations),))


def rule_ar_g() -> ArRuleResult:
    """Nothing in Stage F-AR resolves O-3 or supports a human-reference reliability claim."""
    return ArRuleResult("AR-G", "G,A", None, ArRuleOutcome.REPORTED, 0, 0, (
        ("resolves_o3", False),
        ("o3_status", "PRE_RUN_REQUIRED"),
        ("supports_human_reference_reliability_claim", False),
    ))
