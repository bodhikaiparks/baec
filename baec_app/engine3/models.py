from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .canonical import canonical_sha256
from .enums import (
    ActionAttempt,
    ArtifactAudience,
    ArtifactOrigin,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    ReferenceUseMode,
    SourceIdentityDisclosure,
)
from .errors import Engine3ValidationError
from .reason_codes import ReasonCode


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise Engine3ValidationError(f"{field} must be a non-empty string")


def _tuple_text(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise Engine3ValidationError(f"{field} must be a tuple")
    for item in value:
        _text(item, f"{field} item")
    if len(value) != len(set(value)):
        raise Engine3ValidationError(f"{field} contains duplicates")


@dataclass(frozen=True)
class UpstreamEligibility:
    outcome: str
    baec_current: bool = True
    revalidation_required: bool = False
    review_superseded: bool = False
    required_evidence_usable: bool = True
    lineage_reconstructable: bool = True
    human_preparation_block: bool = False
    preparation_request_present: bool = True

    def __post_init__(self) -> None:
        _text(self.outcome, "UpstreamEligibility.outcome")
        for name in (
            "baec_current", "revalidation_required", "review_superseded",
            "required_evidence_usable", "lineage_reconstructable",
            "human_preparation_block", "preparation_request_present",
        ):
            if not isinstance(getattr(self, name), bool):
                raise Engine3ValidationError(f"UpstreamEligibility.{name} must be bool")


@dataclass(frozen=True)
class ContextState:
    evidence_changed: bool = False
    disclosure_changed: bool = False
    baec_currentness_changed: bool = False
    review_changed: bool = False
    request_changed: bool = False
    digest_match: bool = True

    def __post_init__(self) -> None:
        for name in (
            "evidence_changed", "disclosure_changed", "baec_currentness_changed",
            "review_changed", "request_changed", "digest_match",
        ):
            if not isinstance(getattr(self, name), bool):
                raise Engine3ValidationError(f"ContextState.{name} must be bool")


@dataclass(frozen=True)
class GroundingItem:
    grounding_item_id: str
    exact_value: str
    disposition: ExternalReferenceDisposition = ExternalReferenceDisposition.DIRECT_REFERENCE_ALLOWED
    source_identity_disclosure: SourceIdentityDisclosure = SourceIdentityDisclosure.NOT_ALLOWED
    is_buyer_statement: bool = False
    adversity_related: bool = False
    adversity_context_reviewed: bool = True
    approximate: bool = False
    approved_specificity: int = 100

    def __post_init__(self) -> None:
        _text(self.grounding_item_id, "GroundingItem.grounding_item_id")
        if not isinstance(self.exact_value, str):
            raise Engine3ValidationError("GroundingItem.exact_value must be a string")
        if not isinstance(self.disposition, ExternalReferenceDisposition):
            raise Engine3ValidationError("GroundingItem.disposition has wrong type")
        if not isinstance(self.source_identity_disclosure, SourceIdentityDisclosure):
            raise Engine3ValidationError("GroundingItem.source_identity_disclosure has wrong type")
        if not isinstance(self.approved_specificity, int) or isinstance(self.approved_specificity, bool):
            raise Engine3ValidationError("GroundingItem.approved_specificity must be int")
        if self.approved_specificity < 0:
            raise Engine3ValidationError("GroundingItem.approved_specificity must be >= 0")


class ClaimField(Enum):
    NONE = "NONE"
    THRESHOLD = "THRESHOLD"
    DATE = "DATE"
    ROLE_OR_RELATIONSHIP = "ROLE_OR_RELATIONSHIP"
    SELLER_CAPABILITY = "SELLER_CAPABILITY"
    COMPETITOR = "COMPETITOR"
    EVENT_MAGNITUDE = "EVENT_MAGNITUDE"
    OTHER = "OTHER"


@dataclass(frozen=True)
class DraftSegment:
    segment_id: str
    text: str
    claim_type: ClaimType
    semantic_class: ClaimSemanticClass
    grounding_refs: tuple[str, ...] = ()
    reference_use_mode: ReferenceUseMode = ReferenceUseMode.DIRECT
    source_identity_refs: tuple[str, ...] = ()
    claim_field: ClaimField = ClaimField.NONE
    declared_value: str | None = None
    specificity: int = 0
    question_has_premise: bool = False
    source_instruction_ref: str | None = None
    prescribes_buyer_choice: bool = False

    def __post_init__(self) -> None:
        _text(self.segment_id, "DraftSegment.segment_id")
        _text(self.text, "DraftSegment.text")
        if not isinstance(self.claim_type, ClaimType):
            raise Engine3ValidationError("DraftSegment.claim_type has wrong type")
        if not isinstance(self.semantic_class, ClaimSemanticClass):
            raise Engine3ValidationError("DraftSegment.semantic_class has wrong type")
        if not isinstance(self.reference_use_mode, ReferenceUseMode):
            raise Engine3ValidationError("DraftSegment.reference_use_mode has wrong type")
        if not isinstance(self.claim_field, ClaimField):
            raise Engine3ValidationError("DraftSegment.claim_field has wrong type")
        _tuple_text(self.grounding_refs, "DraftSegment.grounding_refs")
        _tuple_text(self.source_identity_refs, "DraftSegment.source_identity_refs")
        if self.declared_value is not None and not isinstance(self.declared_value, str):
            raise Engine3ValidationError("DraftSegment.declared_value must be str or None")
        if not isinstance(self.specificity, int) or isinstance(self.specificity, bool) or self.specificity < 0:
            raise Engine3ValidationError("DraftSegment.specificity must be a non-negative int")
        if self.source_instruction_ref is not None:
            _text(self.source_instruction_ref, "DraftSegment.source_instruction_ref")


@dataclass(frozen=True)
class PreparationArtifact:
    artifact_id: str
    audience: ArtifactAudience
    origin: ArtifactOrigin
    segments: tuple[DraftSegment, ...]

    def __post_init__(self) -> None:
        _text(self.artifact_id, "PreparationArtifact.artifact_id")
        if not isinstance(self.audience, ArtifactAudience):
            raise Engine3ValidationError("PreparationArtifact.audience has wrong type")
        if not isinstance(self.origin, ArtifactOrigin):
            raise Engine3ValidationError("PreparationArtifact.origin has wrong type")
        if not isinstance(self.segments, tuple):
            raise Engine3ValidationError("PreparationArtifact.segments must be a tuple")
        if not all(isinstance(s, DraftSegment) for s in self.segments):
            raise Engine3ValidationError("PreparationArtifact.segments must contain DraftSegment")
        ids = [s.segment_id for s in self.segments]
        if len(ids) != len(set(ids)):
            raise Engine3ValidationError("PreparationArtifact.segment ids must be unique")

    @property
    def digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True)
class ValidationIssue:
    reason_code: ReasonCode
    segment_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason_code, ReasonCode):
            raise Engine3ValidationError("ValidationIssue.reason_code has wrong type")


@dataclass(frozen=True)
class ValidationReport:
    allowed: bool
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise Engine3ValidationError("ValidationReport.allowed must be bool")
        if not isinstance(self.issues, tuple) or not all(isinstance(i, ValidationIssue) for i in self.issues):
            raise Engine3ValidationError("ValidationReport.issues must be tuple[ValidationIssue, ...]")
        if self.allowed and self.issues:
            raise Engine3ValidationError("an allowed report cannot contain issues")
        if not self.allowed and not self.issues:
            raise Engine3ValidationError("a refused report must contain at least one issue")

    @property
    def primary_reason_code(self) -> str | None:
        return self.issues[0].reason_code.value if self.issues else None


@dataclass(frozen=True)
class ValidationCase:
    upstream: UpstreamEligibility = UpstreamEligibility("HUMAN_VERIFIED_CORRESPONDENCE")
    context: ContextState = ContextState()
    grounding_items: tuple[GroundingItem, ...] = ()
    artifact: PreparationArtifact | None = None
    action_attempt: ActionAttempt = ActionAttempt.NONE
    prohibited_score_requested: bool = False
    empirical_validation_claim: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.upstream, UpstreamEligibility):
            raise Engine3ValidationError("ValidationCase.upstream has wrong type")
        if not isinstance(self.context, ContextState):
            raise Engine3ValidationError("ValidationCase.context has wrong type")
        if not isinstance(self.grounding_items, tuple) or not all(isinstance(g, GroundingItem) for g in self.grounding_items):
            raise Engine3ValidationError("ValidationCase.grounding_items has wrong type")
        ids = [g.grounding_item_id for g in self.grounding_items]
        if len(ids) != len(set(ids)):
            raise Engine3ValidationError("grounding item ids must be unique")
        if self.artifact is not None and not isinstance(self.artifact, PreparationArtifact):
            raise Engine3ValidationError("ValidationCase.artifact has wrong type")
        if not isinstance(self.action_attempt, ActionAttempt):
            raise Engine3ValidationError("ValidationCase.action_attempt has wrong type")
