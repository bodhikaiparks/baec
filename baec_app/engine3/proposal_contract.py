"""Engine 3 Stage E: AI preparation proposal contract.

A proposal is advisory model output captured before deterministic Stage D
validation. Parsing establishes structure only. It grants no eligibility,
disclosure permission, buyer state, intent, opportunity status, or action
authority, and nothing in a proposal becomes buyer evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any

from .canonical import canonical_sha256
from .enums import (
    ActionAttempt,
    ArtifactAudience,
    ArtifactOrigin,
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    ReferenceUseMode,
)
from .errors import Engine3ValidationError
from .models import ClaimField, DraftSegment

PROPOSAL_SCHEMA_VERSION = "engine3.ai_preparation_proposal.v1"

# Stage E evaluates fixed prerecorded proposals only. No live provenance exists.
PRERECORDED_PROVENANCE = "PRERECORDED_SYNTHETIC"

# Stage C section 9: rendered text reconstructs exactly from ordered segments
# plus one deterministic separator.
SEGMENT_SEPARATOR = " "

# Stage A section 8: no more than five discovery questions in V1.
MAX_DISCOVERY_QUESTIONS = 5

# Stage A section 8: the internal Why-Now Summary must state this exactly.
INTERNAL_SUMMARY_BOUNDARY_STATEMENT = (
    "Verified correspondence does not establish that the buyer is currently evaluating, "
    "intends to purchase, or that an opportunity exists."
)

# Audience is derived from artifact type (Stage A section 8, Stage C section 4.7).
# A proposal cannot choose its own audience.
ARTIFACT_AUDIENCE = {
    ArtifactType.INTERNAL_WHY_NOW_SUMMARY: ArtifactAudience.INTERNAL,
    ArtifactType.FOLLOW_UP_EMAIL: ArtifactAudience.BUYER_FACING,
    ArtifactType.CALL_OPENING: ArtifactAudience.BUYER_FACING,
    ArtifactType.DISCOVERY_QUESTION_SET: ArtifactAudience.BUYER_FACING,
}

_DIGEST = re.compile(r"[0-9a-f]{64}")

_PROPOSAL_KEYS = frozenset({
    "schema_version", "proposal_id", "origin", "provenance", "prerecorded_run_id",
    "bound_context_digest", "requested_action", "requests_score", "artifacts",
})
_ARTIFACT_KEYS = frozenset({"artifact_id", "artifact_type", "rendered_text", "segments"})
_SEGMENT_KEYS = frozenset(f.name for f in fields(DraftSegment))


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise Engine3ValidationError(f"{field} must be a non-empty string")


@dataclass(frozen=True)
class ProposedArtifact:
    artifact_id: str
    artifact_type: ArtifactType
    segments: tuple[DraftSegment, ...]
    rendered_text: str

    def __post_init__(self) -> None:
        _text(self.artifact_id, "ProposedArtifact.artifact_id")
        if not isinstance(self.artifact_type, ArtifactType):
            raise Engine3ValidationError("ProposedArtifact.artifact_type has wrong type")
        if not isinstance(self.segments, tuple) or not all(isinstance(s, DraftSegment) for s in self.segments):
            raise Engine3ValidationError("ProposedArtifact.segments must be tuple[DraftSegment, ...]")
        if not self.segments:
            raise Engine3ValidationError("ProposedArtifact.segments must not be empty")
        ids = [s.segment_id for s in self.segments]
        if len(ids) != len(set(ids)):
            raise Engine3ValidationError("ProposedArtifact segment ids must be unique")
        if self.rendered_text != SEGMENT_SEPARATOR.join(s.text for s in self.segments):
            raise Engine3ValidationError("ProposedArtifact.rendered_text must reconstruct exactly from segments")
        if self.artifact_type is ArtifactType.DISCOVERY_QUESTION_SET:
            questions = sum(1 for s in self.segments if s.claim_type is ClaimType.QUESTION)
            if questions > MAX_DISCOVERY_QUESTIONS:
                raise Engine3ValidationError("discovery question set exceeds the V1 question limit")
        if self.artifact_type is ArtifactType.INTERNAL_WHY_NOW_SUMMARY:
            if not any(s.text == INTERNAL_SUMMARY_BOUNDARY_STATEMENT for s in self.segments):
                raise Engine3ValidationError("internal why-now summary must state the correspondence boundary")

    @property
    def audience(self) -> ArtifactAudience:
        return ARTIFACT_AUDIENCE[self.artifact_type]


@dataclass(frozen=True)
class AIPreparationProposal:
    proposal_id: str
    prerecorded_run_id: str
    bound_context_digest: str
    artifacts: tuple[ProposedArtifact, ...]
    requested_action: ActionAttempt = ActionAttempt.NONE
    requests_score: bool = False
    schema_version: str = PROPOSAL_SCHEMA_VERSION
    origin: ArtifactOrigin = ArtifactOrigin.AI_INFERENCE
    provenance: str = PRERECORDED_PROVENANCE

    def __post_init__(self) -> None:
        _text(self.proposal_id, "AIPreparationProposal.proposal_id")
        _text(self.prerecorded_run_id, "AIPreparationProposal.prerecorded_run_id")
        if self.schema_version != PROPOSAL_SCHEMA_VERSION:
            raise Engine3ValidationError("AIPreparationProposal.schema_version is not supported")
        if self.origin is not ArtifactOrigin.AI_INFERENCE:
            raise Engine3ValidationError("AIPreparationProposal.origin must be AI_INFERENCE")
        if self.provenance != PRERECORDED_PROVENANCE:
            raise Engine3ValidationError("AIPreparationProposal.provenance must be prerecorded")
        if not isinstance(self.bound_context_digest, str) or not _DIGEST.fullmatch(self.bound_context_digest):
            raise Engine3ValidationError("AIPreparationProposal.bound_context_digest must be lowercase SHA-256 hex")
        if not isinstance(self.requested_action, ActionAttempt):
            raise Engine3ValidationError("AIPreparationProposal.requested_action has wrong type")
        if not isinstance(self.requests_score, bool):
            raise Engine3ValidationError("AIPreparationProposal.requests_score must be bool")
        if not isinstance(self.artifacts, tuple) or not all(isinstance(a, ProposedArtifact) for a in self.artifacts):
            raise Engine3ValidationError("AIPreparationProposal.artifacts must be tuple[ProposedArtifact, ...]")
        if not self.artifacts:
            raise Engine3ValidationError("AIPreparationProposal.artifacts must not be empty")
        ids = [a.artifact_id for a in self.artifacts]
        if len(ids) != len(set(ids)):
            raise Engine3ValidationError("AIPreparationProposal artifact ids must be unique")

    @property
    def digest(self) -> str:
        return canonical_sha256(self)


def _object(value: object, keys: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Engine3ValidationError(f"{where} must be an object")
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown or missing:
        raise Engine3ValidationError(f"{where} fields invalid: unknown={unknown} missing={missing}")
    return value


def _enum(cls, value: object, where: str):
    if not isinstance(value, str):
        raise Engine3ValidationError(f"{where} must be a string")
    try:
        return cls(value)
    except ValueError:
        raise Engine3ValidationError(f"{where} is not a valid {cls.__name__}") from None


def _bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise Engine3ValidationError(f"{where} must be bool")
    return value


def _int(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise Engine3ValidationError(f"{where} must be int")
    return value


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise Engine3ValidationError(f"{where} must be a string")
    return value


def _optional_str(value: object, where: str) -> str | None:
    return None if value is None else _str(value, where)


def _str_tuple(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise Engine3ValidationError(f"{where} must be a list")
    return tuple(_str(v, where) for v in value)


def parse_segment(value: object) -> DraftSegment:
    d = _object(value, _SEGMENT_KEYS, "segment")
    return DraftSegment(
        segment_id=_str(d["segment_id"], "segment.segment_id"),
        text=_str(d["text"], "segment.text"),
        claim_type=_enum(ClaimType, d["claim_type"], "segment.claim_type"),
        semantic_class=_enum(ClaimSemanticClass, d["semantic_class"], "segment.semantic_class"),
        grounding_refs=_str_tuple(d["grounding_refs"], "segment.grounding_refs"),
        reference_use_mode=_enum(ReferenceUseMode, d["reference_use_mode"], "segment.reference_use_mode"),
        source_identity_refs=_str_tuple(d["source_identity_refs"], "segment.source_identity_refs"),
        claim_field=_enum(ClaimField, d["claim_field"], "segment.claim_field"),
        declared_value=_optional_str(d["declared_value"], "segment.declared_value"),
        specificity=_int(d["specificity"], "segment.specificity"),
        question_has_premise=_bool(d["question_has_premise"], "segment.question_has_premise"),
        source_instruction_ref=_optional_str(d["source_instruction_ref"], "segment.source_instruction_ref"),
        prescribes_buyer_choice=_bool(d["prescribes_buyer_choice"], "segment.prescribes_buyer_choice"),
    )


def parse_artifact(value: object) -> ProposedArtifact:
    d = _object(value, _ARTIFACT_KEYS, "artifact")
    if not isinstance(d["segments"], list):
        raise Engine3ValidationError("artifact.segments must be a list")
    return ProposedArtifact(
        artifact_id=_str(d["artifact_id"], "artifact.artifact_id"),
        artifact_type=_enum(ArtifactType, d["artifact_type"], "artifact.artifact_type"),
        segments=tuple(parse_segment(s) for s in d["segments"]),
        rendered_text=_str(d["rendered_text"], "artifact.rendered_text"),
    )


def load_prerecorded_preparation_proposal(value: object) -> AIPreparationProposal:
    """Load one prerecorded synthetic proposal envelope. Any deviation fails closed.

    Offline only. This is not a live model-output surface.
    """
    d = _object(value, _PROPOSAL_KEYS, "proposal")
    if not isinstance(d["artifacts"], list):
        raise Engine3ValidationError("proposal.artifacts must be a list")
    return AIPreparationProposal(
        proposal_id=_str(d["proposal_id"], "proposal.proposal_id"),
        prerecorded_run_id=_str(d["prerecorded_run_id"], "proposal.prerecorded_run_id"),
        bound_context_digest=_str(d["bound_context_digest"], "proposal.bound_context_digest"),
        artifacts=tuple(parse_artifact(a) for a in d["artifacts"]),
        requested_action=_enum(ActionAttempt, d["requested_action"], "proposal.requested_action"),
        requests_score=_bool(d["requests_score"], "proposal.requests_score"),
        schema_version=_str(d["schema_version"], "proposal.schema_version"),
        origin=_enum(ArtifactOrigin, d["origin"], "proposal.origin"),
        provenance=_str(d["provenance"], "proposal.provenance"),
    )
