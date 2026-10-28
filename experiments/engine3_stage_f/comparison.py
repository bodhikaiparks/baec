"""Stage F counterfactual replay and model-vs-reference comparison (design section 8).

For identical prose and context, Stage D is called unchanged twice: once with the
model-supplied annotations (D_model) and once with the eleven fields replaced by
final human reference values (D_ref). Nothing is normalized before replay.
Comparison labels are experiment metadata, not Engine 3 states. No score exists.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum

from baec_app.engine3.enums import (
    ArtifactOrigin,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    ReferenceUseMode,
)
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.models import (
    ClaimField,
    DraftSegment,
    PreparationArtifact,
    ValidationCase,
    ValidationReport,
)
from baec_app.engine3.offline_evaluation import EvaluationContext
from baec_app.engine3.proposal_contract import ProposedArtifact
from baec_app.engine3.validator import validate_case

from .annotation import REFERENCE_FIELDS, AnnotationStatus, ArtifactReference, packet_id_for
from .envelope import ExperimentalProposal

ALLOWED = "ALLOWED"
ANNOTATION_INSENSITIVE_PREFIXES = ("E3C-CTX-", "E3C-IN-")
_SET_FIELDS = frozenset({"grounding_refs", "source_identity_refs"})
_STATE_CLASSES = frozenset({
    ClaimSemanticClass.BUYER_CURRENT_EVALUATION_STATE,
    ClaimSemanticClass.BUYER_PURCHASE_OR_SWITCH_INTENT,
    ClaimSemanticClass.OPPORTUNITY_STATUS,
    ClaimSemanticClass.DEADLINE_OR_URGENCY,
})
_GROUNDED_CLAIM_TYPES = frozenset({ClaimType.FACTUAL_ASSERTION, ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE})
_LOOSE_TYPES = frozenset({ClaimType.QUESTION, ClaimType.COURTESY_OR_TRANSITION})
_OMISSION_PERMISSIVE = frozenset({
    ExternalReferenceDisposition.INTERNAL_ONLY,
    ExternalReferenceDisposition.UNKNOWN,
    ExternalReferenceDisposition.PARAPHRASE_ALLOWED,
})


class ComparisonCategory(Enum):
    """Design section 8.5. Experiment comparison labels only."""

    CONCORDANT_PASS = "CONCORDANT_PASS"
    CONCORDANT_REFUSAL_SAME_PRIMARY = "CONCORDANT_REFUSAL_SAME_PRIMARY"
    CONCORDANT_REFUSAL_DIFFERENT_PRIMARY = "CONCORDANT_REFUSAL_DIFFERENT_PRIMARY"
    FALSE_PASS = "FALSE_PASS"
    FALSE_REFUSAL = "FALSE_REFUSAL"


class Direction(Enum):
    """Design section 8.6 disagreement direction."""

    PERMISSIVE = "PERMISSIVE"
    RESTRICTIVE = "RESTRICTIVE"
    NEUTRAL = "NEUTRAL"


# --- replay ---------------------------------------------------------------------------------------


def _case(
    proposal: ExperimentalProposal,
    artifact: ProposedArtifact,
    context: EvaluationContext,
    segments: tuple[DraftSegment, ...],
) -> ValidationCase:
    # Built exactly as Stage E evaluate_proposal builds it (design section 8.1).
    state = replace(
        context.context_state,
        digest_match=context.context_state.digest_match and proposal.binding.bound_context_digest == context.digest,
    )
    return ValidationCase(
        upstream=context.upstream,
        context=state,
        grounding_items=context.grounding_items,
        artifact=PreparationArtifact(artifact.artifact_id, artifact.audience, ArtifactOrigin.AI_INFERENCE, segments),
        action_attempt=proposal.requested_action,
        prohibited_score_requested=proposal.requests_score,
    )


def _with_fields(segment: DraftSegment, values: dict) -> DraftSegment:
    return replace(segment, **values)


def reference_segments(artifact: ProposedArtifact, reference: ArtifactReference, only: str | None = None):
    """Model segments with the eleven fields (or only one field) replaced by reference values."""
    if not reference.reference_complete:
        raise Engine3ValidationError("only a reference-complete artifact can be replayed")
    names = REFERENCE_FIELDS if only is None else (only,)
    return tuple(
        _with_fields(segment, {name: getattr(final.fields, name) for name in names})
        for segment, final in zip(artifact.segments, reference.segments)
    )


def _primary(report: ValidationReport) -> str:
    return ALLOWED if report.allowed else report.primary_reason_code


def annotation_insensitive(report: ValidationReport) -> bool:
    return not report.allowed and report.primary_reason_code.startswith(ANNOTATION_INSENSITIVE_PREFIXES)


# --- field agreement ------------------------------------------------------------------------------


def field_equal(name: str, model_value, reference_value) -> bool:
    """Design section 8.6 equality: sets for id lists, exact otherwise. No normalization."""
    if name in _SET_FIELDS:
        return set(model_value) == set(reference_value)
    return model_value == reference_value


def direction(name: str, model: DraftSegment, reference: DraftSegment, context: EvaluationContext) -> Direction:
    """Fixed per-field direction rules (spec evaluated_annotation_fields[].permissive_direction).

    'Restrictive' is the mirror of the permissive rule. For id sets, a permissive
    omission takes precedence over a restrictive addition in the same segment.
    """
    m, r = getattr(model, name), getattr(reference, name)

    def mirror(permissive, restrictive) -> Direction:
        if permissive:
            return Direction.PERMISSIVE
        return Direction.RESTRICTIVE if restrictive else Direction.NEUTRAL

    if name == "claim_type":
        def escapes(a: DraftSegment, b: DraftSegment) -> bool:
            return (a.claim_type in _LOOSE_TYPES and b.claim_type in _GROUNDED_CLAIM_TYPES) or (
                b.claim_type is ClaimType.QUESTION and b.question_has_premise and a.claim_type is not ClaimType.QUESTION
            )
        return mirror(escapes(model, reference), escapes(reference, model))
    if name == "semantic_class":
        return mirror(m not in _STATE_CLASSES and r in _STATE_CLASSES, m in _STATE_CLASSES and r not in _STATE_CLASSES)
    if name == "grounding_refs":
        items = {g.grounding_item_id: g for g in context.grounding_items}
        omitted = set(r) - set(m)
        return mirror(
            any(i in items and items[i].disposition in _OMISSION_PERMISSIVE for i in omitted),
            bool(set(m) - set(r)),
        )
    if name == "source_identity_refs":
        return mirror(bool(set(r) - set(m)), bool(set(m) - set(r)))
    if name == "reference_use_mode":
        return mirror(m is not ReferenceUseMode.DIRECT and r is ReferenceUseMode.DIRECT,
                      m is ReferenceUseMode.DIRECT and r is not ReferenceUseMode.DIRECT)
    if name == "claim_field":
        return mirror(m is ClaimField.NONE and r is not ClaimField.NONE, m is not ClaimField.NONE and r is ClaimField.NONE)
    if name == "declared_value":
        exact_values = {g.exact_value for g in context.grounding_items}
        normalized = m is not None and r is not None and m in exact_values and m != r
        return mirror((m is None and r is not None) or normalized, m is not None and r is None)
    if name == "specificity":
        return mirror(m < r, m > r)
    if name in ("question_has_premise", "prescribes_buyer_choice"):
        return mirror(not m and r, m and not r)
    if name == "source_instruction_ref":
        return mirror(m is None and r is not None, m is not None and r is None)
    raise Engine3ValidationError(f"unknown field {name}")


@dataclass(frozen=True)
class FieldDisagreement:
    ordinal: int
    segment_id: str
    field: str
    model_value: object
    reference_value: object
    direction: Direction


@dataclass(frozen=True)
class NonexistentCitation:
    ordinal: int
    segment_id: str
    field: str
    grounding_item_id: str


def nonexistent_citations(artifact: ProposedArtifact, context: EvaluationContext) -> tuple[NonexistentCitation, ...]:
    """Model citations of grounding ids absent from the context (design section 8.6, output 10)."""
    known = {g.grounding_item_id for g in context.grounding_items}
    found = []
    for n, s in enumerate(artifact.segments, start=1):
        cited = [("grounding_refs", i) for i in s.grounding_refs] + [("source_identity_refs", i) for i in s.source_identity_refs]
        if s.source_instruction_ref is not None:
            cited.append(("source_instruction_ref", s.source_instruction_ref))
        found += [NonexistentCitation(n, s.segment_id, f, i) for f, i in cited if i not in known]
    return tuple(found)


# --- artifact and proposal comparison -------------------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    """Design section 8.8. Deterministic bookkeeping, not a score."""

    individually_sufficient: tuple[str, ...]
    joint_only: bool
    contributing: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactComparison:
    artifact_id: str
    packet_id: str
    d_model: ValidationReport
    annotation_sensitive: bool
    reference_complete: bool
    d_ref: ValidationReport | None
    category: ComparisonCategory | None
    reason_transition: tuple[str, str] | None
    issues_only_in_model: tuple[tuple[str, str | None], ...]
    issues_only_in_reference: tuple[tuple[str, str | None], ...]
    disagreements: tuple[FieldDisagreement, ...]
    attribution: Attribution | None
    human_safe: bool | None
    unscorable_ordinals: tuple[int, ...]
    unfinalized_ordinals: tuple[int, ...]
    formatting_variant_ordinals: tuple[int, ...]
    mixed_reference_mode_ordinals: tuple[int, ...]
    nonexistent_citations: tuple[NonexistentCitation, ...]

    @property
    def false_pass(self) -> bool:
        return self.category is ComparisonCategory.FALSE_PASS

    @property
    def false_refusal(self) -> bool:
        return self.category is ComparisonCategory.FALSE_REFUSAL

    @property
    def disagreeing_fields(self) -> frozenset[str]:
        return frozenset(d.field for d in self.disagreements)


def _issue_keys(report: ValidationReport) -> list[tuple[str, str | None]]:
    return [(i.reason_code.value, i.segment_id) for i in report.issues]


def _in_report_order(remaining: Counter, report: ValidationReport) -> tuple[tuple[str, str | None], ...]:
    return tuple(key for key in dict.fromkeys(_issue_keys(report)) for _ in range(remaining[key]))


def _issue_difference(left: ValidationReport, right: ValidationReport):
    """Multiset difference of (reason code, segment id) issue tuples, in each report's own order."""
    a, b = Counter(_issue_keys(left)), Counter(_issue_keys(right))
    return _in_report_order(a - b, left), _in_report_order(b - a, right)


def _category(d_model: ValidationReport, d_ref: ValidationReport) -> ComparisonCategory:
    if d_model.allowed and d_ref.allowed:
        return ComparisonCategory.CONCORDANT_PASS
    if d_model.allowed:
        return ComparisonCategory.FALSE_PASS
    if d_ref.allowed:
        return ComparisonCategory.FALSE_REFUSAL
    if d_model.primary_reason_code == d_ref.primary_reason_code:
        return ComparisonCategory.CONCORDANT_REFUSAL_SAME_PRIMARY
    return ComparisonCategory.CONCORDANT_REFUSAL_DIFFERENT_PRIMARY


def compare_artifact(
    proposal: ExperimentalProposal,
    artifact_id: str,
    context: EvaluationContext,
    reference: ArtifactReference | None,
) -> ArtifactComparison:
    if not isinstance(proposal, ExperimentalProposal):
        raise Engine3ValidationError("proposal has wrong type")
    if not isinstance(context, EvaluationContext):
        raise Engine3ValidationError("context has wrong type")
    matches = [a for a in proposal.artifacts if a.artifact_id == artifact_id]
    if not matches:
        raise Engine3ValidationError("artifact not found in proposal")
    artifact = matches[0]
    packet_id = packet_id_for(artifact, context)
    if reference is not None:
        if not isinstance(reference, ArtifactReference):
            raise Engine3ValidationError("reference has wrong type")
        if reference.packet_id != packet_id or len(reference.segments) != len(artifact.segments):
            raise Engine3ValidationError("reference does not belong to this prose and context")

    d_model = validate_case(_case(proposal, artifact, context, artifact.segments))
    sensitive = not annotation_insensitive(d_model)
    finals = () if reference is None else reference.segments
    complete = reference is not None and reference.reference_complete

    disagreements = []
    for n, (segment, final) in enumerate(zip(artifact.segments, finals), start=1):
        if final.fields is None:
            continue
        ref_segment = _with_fields(segment, {name: getattr(final.fields, name) for name in REFERENCE_FIELDS})
        for name in REFERENCE_FIELDS:
            m, r = getattr(segment, name), getattr(ref_segment, name)
            if not field_equal(name, m, r):
                disagreements.append(FieldDisagreement(n, segment.segment_id, name, m, r,
                                                       direction(name, segment, ref_segment, context)))

    d_ref = category = transition = attribution = None
    only_model = only_ref = ()
    if complete:
        d_ref = validate_case(_case(proposal, artifact, context, reference_segments(artifact, reference)))
        if sensitive:
            category = _category(d_model, d_ref)
            transition = (_primary(d_model), _primary(d_ref))
            only_model, only_ref = _issue_difference(d_model, d_ref)
            if category in (ComparisonCategory.FALSE_PASS, ComparisonCategory.FALSE_REFUSAL):
                sufficient = tuple(
                    name for name in REFERENCE_FIELDS
                    if validate_case(_case(proposal, artifact, context,
                                           reference_segments(artifact, reference, only=name))).allowed == d_ref.allowed
                )
                joint = not sufficient
                disagreeing = {d.field for d in disagreements}
                contributing = tuple(n for n in REFERENCE_FIELDS if n in disagreeing) if joint else sufficient
                attribution = Attribution(sufficient, joint, contributing)

    def ordinals(predicate) -> tuple[int, ...]:
        return tuple(s.ordinal for s in finals if predicate(s))

    return ArtifactComparison(
        artifact_id=artifact.artifact_id,
        packet_id=packet_id,
        d_model=d_model,
        annotation_sensitive=sensitive,
        reference_complete=complete,
        d_ref=d_ref,
        category=category,
        reason_transition=transition,
        issues_only_in_model=only_model,
        issues_only_in_reference=only_ref,
        disagreements=tuple(disagreements),
        attribution=attribution,
        human_safe=None if reference is None else reference.human_safe,
        unscorable_ordinals=ordinals(lambda s: s.status is AnnotationStatus.UNSCORABLE_SEGMENT_STRUCTURE),
        unfinalized_ordinals=ordinals(lambda s: s.status is AnnotationStatus.ADJUDICATION_REQUIRED),
        formatting_variant_ordinals=ordinals(lambda s: s.formatting_variant),
        mixed_reference_mode_ordinals=ordinals(lambda s: s.mixed_reference_modes),
        nonexistent_citations=nonexistent_citations(artifact, context),
    )


@dataclass(frozen=True)
class ProposalComparison:
    proposal_digest: str
    artifacts: tuple[ArtifactComparison, ...]

    @property
    def reaches_review_under_model(self) -> bool:
        return all(a.d_model.allowed for a in self.artifacts)

    @property
    def false_pass(self) -> bool:
        # Design section 8.3, proposal level.
        return self.reaches_review_under_model and any(
            a.reference_complete and not a.d_ref.allowed for a in self.artifacts
        )

    @property
    def false_refusal(self) -> bool:
        # Design section 8.4, proposal level: every artifact reference-complete.
        return (
            not self.reaches_review_under_model
            and all(a.reference_complete for a in self.artifacts)
            and all(a.d_ref.allowed for a in self.artifacts)
        )


def compare_proposal(
    proposal: ExperimentalProposal,
    context: EvaluationContext,
    references: dict[str, ArtifactReference | None],
) -> ProposalComparison:
    if set(references) != {a.artifact_id for a in proposal.artifacts}:
        raise Engine3ValidationError("references must name exactly the proposal's artifacts")
    return ProposalComparison(
        proposal.digest,
        tuple(compare_artifact(proposal, a.artifact_id, context, references[a.artifact_id]) for a in proposal.artifacts),
    )
