"""Engine 3 Stage E: offline evaluation of AI preparation proposals.

Every proposal artifact is submitted unchanged to the Stage D validator, which
remains the deterministic authority. This module adds only one computed fact:
whether the proposal's bound context digest equals the current context digest.
It performs no generation, I/O, persistence, network access, or seller action.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .canonical import canonical_sha256
from .enums import ArtifactOrigin, ArtifactType
from .errors import Engine3ValidationError
from .models import (
    ContextState,
    GroundingItem,
    PreparationArtifact,
    UpstreamEligibility,
    ValidationCase,
    ValidationReport,
)
from .proposal_contract import AIPreparationProposal, ProposedArtifact
from .validator import validate_case


@dataclass(frozen=True)
class EvaluationContext:
    """Current authoritative context, supplied by the caller and never by a proposal."""

    upstream: UpstreamEligibility
    context_state: ContextState
    grounding_items: tuple[GroundingItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.upstream, UpstreamEligibility):
            raise Engine3ValidationError("EvaluationContext.upstream has wrong type")
        if not isinstance(self.context_state, ContextState):
            raise Engine3ValidationError("EvaluationContext.context_state has wrong type")
        if not isinstance(self.grounding_items, tuple) or not all(
            isinstance(g, GroundingItem) for g in self.grounding_items
        ):
            raise Engine3ValidationError("EvaluationContext.grounding_items has wrong type")
        ids = [g.grounding_item_id for g in self.grounding_items]
        if len(ids) != len(set(ids)):
            raise Engine3ValidationError("EvaluationContext grounding item ids must be unique")

    @property
    def digest(self) -> str:
        # Stage C section 5: unordered grounding items are sorted by stable id.
        items = sorted(self.grounding_items, key=lambda g: g.grounding_item_id)
        return canonical_sha256({"upstream": self.upstream, "grounding_items": items})


@dataclass(frozen=True)
class ArtifactEvaluation:
    artifact_id: str
    artifact_type: ArtifactType
    origin: ArtifactOrigin
    report: ValidationReport


@dataclass(frozen=True)
class ProposalEvaluation:
    proposal_digest: str
    bound_context_digest: str
    current_context_digest: str
    artifacts: tuple[ArtifactEvaluation, ...]

    @property
    def reaches_human_preparation_review(self) -> bool:
        # Means only that a human may review the material. It is not outreach
        # authorization, buyer evidence, or an opportunity.
        return all(a.report.allowed for a in self.artifacts)


@dataclass(frozen=True)
class RecordedHumanEdit:
    """A recorded human edit of one proposal artifact.

    The edited artifact is a new HUMAN_EDITED artifact. It is never relabeled as
    AI output and never becomes buyer evidence.
    """

    edit_id: str
    parent_proposal_digest: str
    parent_artifact_id: str
    edited: ProposedArtifact

    def __post_init__(self) -> None:
        for name in ("edit_id", "parent_proposal_digest", "parent_artifact_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise Engine3ValidationError(f"RecordedHumanEdit.{name} must be a non-empty string")
        if not isinstance(self.edited, ProposedArtifact):
            raise Engine3ValidationError("RecordedHumanEdit.edited has wrong type")
        if self.edited.artifact_id == self.parent_artifact_id:
            raise Engine3ValidationError("a human edit must create a new artifact id")


def _bound_state(bound_digest: str, context: EvaluationContext) -> ContextState:
    match = context.context_state.digest_match and bound_digest == context.digest
    return replace(context.context_state, digest_match=match)


def _validate(
    artifact: ProposedArtifact,
    origin: ArtifactOrigin,
    state: ContextState,
    context: EvaluationContext,
    proposal: AIPreparationProposal,
) -> ArtifactEvaluation:
    case = ValidationCase(
        upstream=context.upstream,
        context=state,
        grounding_items=context.grounding_items,
        artifact=PreparationArtifact(artifact.artifact_id, artifact.audience, origin, artifact.segments),
        action_attempt=proposal.requested_action,
        prohibited_score_requested=proposal.requests_score,
    )
    return ArtifactEvaluation(artifact.artifact_id, artifact.artifact_type, origin, validate_case(case))


def evaluate_proposal(proposal: AIPreparationProposal, context: EvaluationContext) -> ProposalEvaluation:
    if not isinstance(proposal, AIPreparationProposal):
        raise Engine3ValidationError("proposal has wrong type")
    if not isinstance(context, EvaluationContext):
        raise Engine3ValidationError("context has wrong type")
    state = _bound_state(proposal.bound_context_digest, context)
    results = tuple(
        _validate(a, ArtifactOrigin.AI_INFERENCE, state, context, proposal) for a in proposal.artifacts
    )
    return ProposalEvaluation(proposal.digest, proposal.bound_context_digest, context.digest, results)


def evaluate_human_edit(
    proposal: AIPreparationProposal,
    edit: RecordedHumanEdit,
    context: EvaluationContext,
) -> ArtifactEvaluation:
    if not isinstance(edit, RecordedHumanEdit):
        raise Engine3ValidationError("edit has wrong type")
    if edit.parent_proposal_digest != proposal.digest:
        raise Engine3ValidationError("human edit is not bound to this proposal")
    parents = [a for a in proposal.artifacts if a.artifact_id == edit.parent_artifact_id]
    if not parents:
        raise Engine3ValidationError("human edit parent artifact not found")
    if edit.edited.artifact_type is not parents[0].artifact_type:
        raise Engine3ValidationError("human edit cannot change artifact type")
    state = _bound_state(proposal.bound_context_digest, context)
    return _validate(edit.edited, ArtifactOrigin.HUMAN_EDITED, state, context, proposal)
