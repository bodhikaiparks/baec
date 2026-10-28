"""Stage F blinded annotation packets and human reference records.

Packets carry only what the protocol (section 4.1) lets an annotator see. The
builder takes an artifact and its context and nothing else, so run identity,
arm, model identity, and Stage D results cannot reach a packet. Model-supplied
segment annotations are dropped, and model segment ids are replaced by opaque
packet segment ids.

Reference records follow the protocol (sections 6 to 9). Annotation statuses
and experiment flags are experiment metadata, never Engine 3 states, and are
never sent to Stage D. A model never creates, edits, or initializes a record.

Every record carries three provenance hashes (HARNESS_AMENDMENT_003): the
historical locked protocol, the frozen operational guide, and the exact packet
the reviewer saw. There is no single protocol-version hash to fall back on.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

from baec_app.engine3.canonical import canonical_json, canonical_sha256
from baec_app.engine3.enums import (
    ArtifactAudience,
    ClaimSemanticClass,
    ClaimType,
    PreparationChannel,
    PreparationGoal,
    ReferenceUseMode,
)
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.models import ClaimField
from baec_app.engine3.offline_evaluation import EvaluationContext
from baec_app.engine3.proposal_contract import ProposedArtifact

# The eleven model-controlled DraftSegment fields (design section 7.1), in locked order.
REFERENCE_FIELDS = (
    "claim_type",
    "semantic_class",
    "grounding_refs",
    "reference_use_mode",
    "source_identity_refs",
    "claim_field",
    "declared_value",
    "specificity",
    "question_has_premise",
    "source_instruction_ref",
    "prescribes_buyer_choice",
)

# Stage F measurement convention (owner decision D-2). Not a Stage D definition.
SPECIFICITY_CODES = (0, 10, 90)

# Protocol section 7.2 precedence order.
SEMANTIC_CLASS_PRECEDENCE = (
    ClaimSemanticClass.BUYER_CURRENT_EVALUATION_STATE,
    ClaimSemanticClass.BUYER_PURCHASE_OR_SWITCH_INTENT,
    ClaimSemanticClass.OPPORTUNITY_STATUS,
    ClaimSemanticClass.DEADLINE_OR_URGENCY,
    ClaimSemanticClass.SELLER_CAPABILITY,
    ClaimSemanticClass.COMPETITOR_CLAIM,
    ClaimSemanticClass.RELATIONSHIP_OR_ROLE,
    ClaimSemanticClass.CONDITION_REFERENCE,
    ClaimSemanticClass.EXTERNAL_EVENT_FACT,
    ClaimSemanticClass.OTHER_FACT,
    ClaimSemanticClass.NEUTRAL_QUESTION,
    ClaimSemanticClass.COURTESY,
)

POLICY_ATTESTATIONS = (
    "condition_fidelity",
    "factual_accuracy",
    "disclosure_compliance",
    "buyer_state_intent_boundary",
    "contextual_appropriateness",
)
HUMAN_SAFE_ATTESTATIONS = POLICY_ATTESTATIONS[:4]

_VALUE_CLAIM_FIELDS = frozenset({
    ClaimField.THRESHOLD, ClaimField.DATE, ClaimField.ROLE_OR_RELATIONSHIP,
    ClaimField.SELLER_CAPABILITY, ClaimField.COMPETITOR, ClaimField.EVENT_MAGNITUDE,
})
_NO_CLAIM_TYPES = frozenset({ClaimType.QUESTION, ClaimType.COURTESY_OR_TRANSITION})
_DIGEST = re.compile(r"[0-9a-f]{64}")

# HARNESS_AMENDMENT_003: exact SHA-256 of the artifacts that govern every human annotation event.
# The historical locked protocol (governing design record; not shown to annotators).
LOCKED_PROTOCOL_SHA256 = "cba979c91cdcc96f419b0f3758cadd651f3427064a7f66ebebd23929c623b5a4"
# The frozen decontaminated operational guide annotators actually use.
OPERATIONAL_GUIDE_SHA256 = "fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011"
PROVENANCE_FIELDS = ("locked_protocol_sha256", "operational_guide_sha256", "annotation_packet_sha256")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")


class AnnotationStatus(Enum):
    """Design section 8.2. Experiment metadata outside the Engine 3 vocabulary."""

    SCORABLE = "SCORABLE"
    ADJUDICATION_REQUIRED = "ADJUDICATION_REQUIRED"
    UNSCORABLE_SEGMENT_STRUCTURE = "UNSCORABLE_SEGMENT_STRUCTURE"


class AnnotatorRole(Enum):
    PRIMARY_A = "PRIMARY_A"
    PRIMARY_B = "PRIMARY_B"


# --- annotation guide shown in every packet -------------------------------------------------------

ANNOTATION_GUIDE = {
    "annotation_statuses": [s.value for s in AnnotationStatus],
    "fields": list(REFERENCE_FIELDS),
    "vocabularies": {
        "claim_type": [c.value for c in ClaimType],
        "semantic_class_in_precedence_order": [c.value for c in SEMANTIC_CLASS_PRECEDENCE],
        "reference_use_mode": [m.value for m in ReferenceUseMode],
        "claim_field": [f.value for f in ClaimField],
        "specificity": list(SPECIFICITY_CODES),
    },
    "specificity_convention": {
        "0": "no externally meaningful factual specificity is expressed",
        "10": "generalized or qualitative paraphrase without exposing an exact numeric value, exact date, "
              "exact named entity/source identity, or similarly exact detail",
        "90": "exact or near-exact factual detail exposed, including formatting-only variants such as $46.00 -> $46",
        "excluded_values": [50],
        "multiple_levels_in_segment": "record the highest",
        "independent_of_reference_use_mode": True,
    },
    "reference_use_convention": {
        "INTERNAL": "only when the segment is evaluated as internal use (INTERNAL_WHY_NOW_SUMMARY)",
        "DIRECT": "only when all externally used cited content is used directly/exactly; vacuously when no content is cited",
        "PARAPHRASE": "when any externally used cited content is semantically reworded",
        "formatting_only_numeric_variant": "may be coded DIRECT (example $46.00 -> $46) and is flagged formatting_variant",
        "semantically_equivalent_rewording": "coded PARAPHRASE (example over $40 -> higher than $40)",
        "mixed_modes": "code PARAPHRASE and set the flag mixed_reference_modes",
        "independent_of_specificity": True,
    },
    "declared_value_convention": "exact surface form appearing in the rendered prose; never normalized back to "
                                 "the grounding representation",
    "experiment_flags": ["mixed_reference_modes", "formatting_variant"],
    "policy_attestations": list(POLICY_ATTESTATIONS),
    "policy_attestations_recorded_before_segment_fields": True,
    "grounding_text_is_untrusted_quoted_data": True,
    "unscorable_segments_receive_no_field_values": True,
}


# --- packets --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PacketSegment:
    packet_segment_id: str
    ordinal: int
    text: str


@dataclass(frozen=True)
class PacketGroundingItem:
    grounding_item_id: str
    exact_value: str
    disposition: str
    source_identity_disclosure: str
    is_buyer_statement: bool
    approximate: bool
    approved_specificity: int
    adversity_related: bool
    adversity_context_reviewed: bool


@dataclass(frozen=True)
class AnnotationPacket:
    packet_id: str
    context_label: str
    artifact_type: str
    audience: str
    rendered_text: str
    segments: tuple[PacketSegment, ...]
    grounding_items: tuple[PacketGroundingItem, ...]
    preparation_goal: str | None
    channel_scope: str | None
    guide: dict

    def to_json(self) -> str:
        return canonical_json(self)

    @property
    def segment_ids(self) -> tuple[str, ...]:
        return tuple(s.packet_segment_id for s in self.segments)

    @property
    def grounding_ids(self) -> frozenset[str]:
        return frozenset(g.grounding_item_id for g in self.grounding_items)


def packet_id_for(artifact: ProposedArtifact, context: EvaluationContext) -> str:
    """Deduplication key: same context, artifact type, segment boundaries, and texts (protocol 4.3)."""
    return canonical_sha256({
        "context_digest": context.digest,
        "artifact_type": artifact.artifact_type,
        "segment_texts": [s.text for s in artifact.segments],
    })


def build_annotation_packet(
    artifact: ProposedArtifact,
    context: EvaluationContext,
    preparation_goal: PreparationGoal | None,
    channel_scope: PreparationChannel | None,
) -> AnnotationPacket:
    if not isinstance(artifact, ProposedArtifact):
        raise Engine3ValidationError("artifact has wrong type")
    if not isinstance(context, EvaluationContext):
        raise Engine3ValidationError("context has wrong type")
    if preparation_goal is not None and not isinstance(preparation_goal, PreparationGoal):
        raise Engine3ValidationError("preparation_goal has wrong type")
    if channel_scope is not None and not isinstance(channel_scope, PreparationChannel):
        raise Engine3ValidationError("channel_scope has wrong type")
    items = sorted(context.grounding_items, key=lambda g: g.grounding_item_id)
    return AnnotationPacket(
        packet_id=packet_id_for(artifact, context),
        context_label="context-" + context.digest[:12],
        artifact_type=artifact.artifact_type.value,
        audience=artifact.audience.value,
        rendered_text=artifact.rendered_text,
        segments=tuple(
            PacketSegment(f"PS-{n}", n, s.text) for n, s in enumerate(artifact.segments, start=1)
        ),
        grounding_items=tuple(
            PacketGroundingItem(
                grounding_item_id=g.grounding_item_id,
                exact_value=g.exact_value,
                disposition=g.disposition.value,
                source_identity_disclosure=g.source_identity_disclosure.value,
                is_buyer_statement=g.is_buyer_statement,
                approximate=g.approximate,
                approved_specificity=g.approved_specificity,
                adversity_related=g.adversity_related,
                adversity_context_reviewed=g.adversity_context_reviewed,
            )
            for g in items
        ),
        preparation_goal=None if preparation_goal is None else preparation_goal.value,
        channel_scope=None if channel_scope is None else channel_scope.value,
        guide=ANNOTATION_GUIDE,
    )


def annotation_packet_sha256(packet: AnnotationPacket) -> str:
    """SHA-256 of the exact packet shown to the reviewer: the UTF-8 bytes of its canonical JSON, guide included.

    A packet holds only human-visible content, so model annotations and run metadata cannot change this hash.
    """
    if not isinstance(packet, AnnotationPacket):
        raise Engine3ValidationError("packet has wrong type")
    return hashlib.sha256(packet.to_json().encode("utf-8")).hexdigest()


def record_provenance(packet: AnnotationPacket) -> dict[str, str]:
    """The three provenance hashes for a record made on this packet (HARNESS_AMENDMENT_003)."""
    return {
        "locked_protocol_sha256": LOCKED_PROTOCOL_SHA256,
        "operational_guide_sha256": OPERATIONAL_GUIDE_SHA256,
        "annotation_packet_sha256": annotation_packet_sha256(packet),
    }


def annotator_order(packet_ids: tuple[str, ...], seed: str) -> tuple[str, ...]:
    """Deterministic per-annotator pseudorandom order from a recorded seed (protocol 4.3)."""
    if not isinstance(seed, str) or not seed:
        raise Engine3ValidationError("order seed must be a non-empty string")
    unique = sorted(set(packet_ids))
    return tuple(sorted(unique, key=lambda p: hashlib.sha256(f"{seed}\x00{p}".encode("utf-8")).hexdigest()))


# --- reference records ----------------------------------------------------------------------------


def _text(value: object, where: str) -> None:
    if not isinstance(value, str) or not value:
        raise Engine3ValidationError(f"{where} must be a non-empty string")


def _refs(value: object, where: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(v, str) and v for v in value):
        raise Engine3ValidationError(f"{where} must be a tuple of ids")
    if list(value) != sorted(set(value)):
        raise Engine3ValidationError(f"{where} must be sorted and unique")


@dataclass(frozen=True)
class ReferenceFields:
    """The eleven fields of one SCORABLE segment under the protocol (section 7)."""

    claim_type: ClaimType
    semantic_class: ClaimSemanticClass
    grounding_refs: tuple[str, ...]
    reference_use_mode: ReferenceUseMode
    source_identity_refs: tuple[str, ...]
    claim_field: ClaimField
    declared_value: str | None
    specificity: int
    question_has_premise: bool
    source_instruction_ref: str | None
    prescribes_buyer_choice: bool

    def __post_init__(self) -> None:
        for name, cls in (("claim_type", ClaimType), ("semantic_class", ClaimSemanticClass),
                          ("reference_use_mode", ReferenceUseMode), ("claim_field", ClaimField)):
            if not isinstance(getattr(self, name), cls):
                raise Engine3ValidationError(f"ReferenceFields.{name} has wrong type")
        _refs(self.grounding_refs, "ReferenceFields.grounding_refs")
        _refs(self.source_identity_refs, "ReferenceFields.source_identity_refs")
        for name in ("question_has_premise", "prescribes_buyer_choice"):
            if not isinstance(getattr(self, name), bool):
                raise Engine3ValidationError(f"ReferenceFields.{name} must be bool")
        if isinstance(self.specificity, bool) or self.specificity not in SPECIFICITY_CODES:
            raise Engine3ValidationError("ReferenceFields.specificity must be 0, 10, or 90")
        if self.declared_value is not None:
            _text(self.declared_value, "ReferenceFields.declared_value")
        if self.source_instruction_ref is not None:
            _text(self.source_instruction_ref, "ReferenceFields.source_instruction_ref")
        # Protocol 7.6, 7.7, 7.10.
        if (self.claim_field is ClaimField.NONE) != (self.claim_type in _NO_CLAIM_TYPES):
            raise Engine3ValidationError("claim_field is NONE exactly for QUESTION and COURTESY_OR_TRANSITION")
        if self.declared_value is not None and self.claim_field not in _VALUE_CLAIM_FIELDS:
            raise Engine3ValidationError("declared_value is set only for a specific-value claim_field")
        if self.question_has_premise and self.claim_type is not ClaimType.QUESTION:
            raise Engine3ValidationError("question_has_premise is false for non-questions")

    def cited_ids(self) -> frozenset[str]:
        ids = set(self.grounding_refs) | set(self.source_identity_refs)
        if self.source_instruction_ref is not None:
            ids.add(self.source_instruction_ref)
        return frozenset(ids)


@dataclass(frozen=True)
class SegmentAnnotation:
    """One annotator's (or the adjudicator's) decision for one packet segment."""

    packet_segment_id: str
    status: AnnotationStatus
    fields: ReferenceFields | None = None
    formatting_variant: bool = False
    mixed_reference_modes: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        _text(self.packet_segment_id, "SegmentAnnotation.packet_segment_id")
        if not isinstance(self.status, AnnotationStatus):
            raise Engine3ValidationError("SegmentAnnotation.status has wrong type")
        if self.fields is not None and not isinstance(self.fields, ReferenceFields):
            raise Engine3ValidationError("SegmentAnnotation.fields has wrong type")
        for name in ("formatting_variant", "mixed_reference_modes"):
            if not isinstance(getattr(self, name), bool):
                raise Engine3ValidationError(f"SegmentAnnotation.{name} must be bool")
        if self.note is not None:
            _text(self.note, "SegmentAnnotation.note")
        if self.status is AnnotationStatus.SCORABLE:
            if self.fields is None:
                raise Engine3ValidationError("a SCORABLE segment needs all eleven fields")
            if self.formatting_variant and self.fields.reference_use_mode is not ReferenceUseMode.DIRECT:
                raise Engine3ValidationError("formatting_variant applies only to DIRECT use")
            if self.mixed_reference_modes and self.fields.reference_use_mode is not ReferenceUseMode.PARAPHRASE:
                raise Engine3ValidationError("mixed_reference_modes applies only to PARAPHRASE")
        else:
            # Protocol 7.0: never force a label onto a segment that is not SCORABLE.
            if self.fields is not None or self.formatting_variant or self.mixed_reference_modes:
                raise Engine3ValidationError("a segment that is not SCORABLE receives no field values or flags")
            if self.status is AnnotationStatus.ADJUDICATION_REQUIRED and self.note is None:
                raise Engine3ValidationError("a flagged segment needs a short note")


@dataclass(frozen=True)
class HumanPolicyAttestation:
    """Artifact-level H record (protocol section 6). Not a HumanPreparationReview."""

    condition_fidelity: bool
    factual_accuracy: bool
    disclosure_compliance: bool
    buyer_state_intent_boundary: bool
    contextual_appropriateness: bool
    note: str | None = None

    def __post_init__(self) -> None:
        for name in POLICY_ATTESTATIONS:
            if not isinstance(getattr(self, name), bool):
                raise Engine3ValidationError(f"HumanPolicyAttestation.{name} must be bool")
        if self.note is not None:
            _text(self.note, "HumanPolicyAttestation.note")

    @property
    def human_safe(self) -> bool:
        return all(getattr(self, name) for name in HUMAN_SAFE_ATTESTATIONS)

    def values(self) -> tuple[bool, ...]:
        return tuple(getattr(self, name) for name in POLICY_ATTESTATIONS)


def _record_header(record, where: str) -> None:
    _text(record.alias, f"{where}.alias")
    if not isinstance(record.packet_id, str) or not _DIGEST.fullmatch(record.packet_id):
        raise Engine3ValidationError(f"{where}.packet_id must be a packet digest")
    for name in PROVENANCE_FIELDS:
        value = getattr(record, name)
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise Engine3ValidationError(f"{where}.{name} must be SHA-256 hex")
    if record.locked_protocol_sha256 != LOCKED_PROTOCOL_SHA256:
        raise Engine3ValidationError(f"{where}.locked_protocol_sha256 is not the historical locked protocol")
    if record.operational_guide_sha256 != OPERATIONAL_GUIDE_SHA256:
        raise Engine3ValidationError(f"{where}.operational_guide_sha256 is not the frozen operational guide")
    if not isinstance(record.recorded_at, str) or not _UTC.fullmatch(record.recorded_at):
        raise Engine3ValidationError(f"{where}.recorded_at must be RFC 3339 UTC")


@dataclass(frozen=True)
class PrimaryAnnotation:
    alias: str
    role: AnnotatorRole
    packet_id: str
    locked_protocol_sha256: str
    operational_guide_sha256: str
    annotation_packet_sha256: str
    recorded_at: str
    attestation: HumanPolicyAttestation
    segments: tuple[SegmentAnnotation, ...]

    def __post_init__(self) -> None:
        _record_header(self, "PrimaryAnnotation")
        if not isinstance(self.role, AnnotatorRole):
            raise Engine3ValidationError("PrimaryAnnotation.role has wrong type")
        if not isinstance(self.attestation, HumanPolicyAttestation):
            raise Engine3ValidationError("PrimaryAnnotation.attestation has wrong type")
        if not isinstance(self.segments, tuple) or not all(isinstance(s, SegmentAnnotation) for s in self.segments):
            raise Engine3ValidationError("PrimaryAnnotation.segments has wrong type")


@dataclass(frozen=True)
class AdjudicatedSegment:
    decision: SegmentAnnotation
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.decision, SegmentAnnotation):
            raise Engine3ValidationError("AdjudicatedSegment.decision has wrong type")
        _text(self.reason, "AdjudicatedSegment.reason")


@dataclass(frozen=True)
class Adjudication:
    alias: str
    packet_id: str
    locked_protocol_sha256: str
    operational_guide_sha256: str
    annotation_packet_sha256: str
    recorded_at: str
    segments: tuple[AdjudicatedSegment, ...]
    attestation: HumanPolicyAttestation | None = None

    def __post_init__(self) -> None:
        _record_header(self, "Adjudication")
        if not isinstance(self.segments, tuple) or not all(isinstance(s, AdjudicatedSegment) for s in self.segments):
            raise Engine3ValidationError("Adjudication.segments has wrong type")
        if self.attestation is not None and not isinstance(self.attestation, HumanPolicyAttestation):
            raise Engine3ValidationError("Adjudication.attestation has wrong type")


@dataclass(frozen=True)
class FinalSegment:
    packet_segment_id: str
    ordinal: int
    status: AnnotationStatus
    fields: ReferenceFields | None
    formatting_variant: bool
    mixed_reference_modes: bool
    adjudicated: bool


@dataclass(frozen=True)
class ArtifactReference:
    """Final human reference for one packet (protocol section 8). Never derived from model output."""

    packet_id: str
    segments: tuple[FinalSegment, ...]
    attestation: HumanPolicyAttestation | None

    @property
    def reference_complete(self) -> bool:
        return all(s.status is AnnotationStatus.SCORABLE for s in self.segments)

    @property
    def human_safe(self) -> bool | None:
        return None if self.attestation is None else self.attestation.human_safe


def _check_against_packet(packet: AnnotationPacket, decisions, where: str) -> None:
    for decision in decisions:
        if decision.fields is None:
            continue
        unknown = decision.fields.cited_ids() - packet.grounding_ids
        if unknown:
            raise Engine3ValidationError(f"{where} cites ids not shown in the packet: {sorted(unknown)}")
        internal = packet.audience == ArtifactAudience.INTERNAL.value
        if (decision.fields.reference_use_mode is ReferenceUseMode.INTERNAL) != internal:
            raise Engine3ValidationError(f"{where}: INTERNAL is used exactly for internal artifacts")


def _same_decision(a: SegmentAnnotation, b: SegmentAnnotation) -> bool:
    return (a.status, a.fields, a.formatting_variant, a.mixed_reference_modes) == (
        b.status, b.fields, b.formatting_variant, b.mixed_reference_modes
    )


def needs_adjudication(a: SegmentAnnotation, b: SegmentAnnotation) -> bool:
    """Protocol section 8 triggers, plus flag disagreement (an implementation choice)."""
    if AnnotationStatus.ADJUDICATION_REQUIRED in (a.status, b.status):
        return True
    return not _same_decision(a, b)


def finalize_reference(
    packet: AnnotationPacket,
    primary_a: PrimaryAnnotation,
    primary_b: PrimaryAnnotation,
    adjudication: Adjudication | None,
) -> ArtifactReference:
    """Dual-primary mode with a separate adjudicator. Unresolved segments stay ADJUDICATION_REQUIRED."""
    if not isinstance(packet, AnnotationPacket):
        raise Engine3ValidationError("packet has wrong type")
    if primary_a.role is not AnnotatorRole.PRIMARY_A or primary_b.role is not AnnotatorRole.PRIMARY_B:
        raise Engine3ValidationError("primary roles must be PRIMARY_A and PRIMARY_B")
    if primary_a.alias == primary_b.alias:
        raise Engine3ValidationError("the two primary annotators must be different people")
    records = [primary_a, primary_b] + ([adjudication] if adjudication is not None else [])
    if adjudication is not None:
        if not isinstance(adjudication, Adjudication):
            raise Engine3ValidationError("adjudication has wrong type")
        if adjudication.alias in (primary_a.alias, primary_b.alias):
            raise Engine3ValidationError("the adjudicator must be neither primary annotator")
    if any(r.packet_id != packet.packet_id for r in records):
        raise Engine3ValidationError("a reference record is not bound to this packet")
    if any(r.annotation_packet_sha256 != annotation_packet_sha256(packet) for r in records):
        raise Engine3ValidationError("a reference record was not made on this exact packet")
    for primary in (primary_a, primary_b):
        if tuple(s.packet_segment_id for s in primary.segments) != packet.segment_ids:
            raise Engine3ValidationError("a primary annotation must cover every packet segment in order")
        _check_against_packet(packet, primary.segments, "primary annotation")

    adjudicated = {}
    if adjudication is not None:
        adjudicated = {s.decision.packet_segment_id: s.decision for s in adjudication.segments}
        if len(adjudicated) != len(adjudication.segments):
            raise Engine3ValidationError("adjudication repeats a segment")
        _check_against_packet(packet, adjudicated.values(), "adjudication")

    final = []
    for segment, a, b in zip(packet.segments, primary_a.segments, primary_b.segments):
        if not needs_adjudication(a, b):
            if segment.packet_segment_id in adjudicated:
                raise Engine3ValidationError("adjudication may cover only segments that require it")
            decision, was_adjudicated = a, False
        elif segment.packet_segment_id in adjudicated:
            decision, was_adjudicated = adjudicated[segment.packet_segment_id], True
        else:
            decision = SegmentAnnotation(segment.packet_segment_id, AnnotationStatus.ADJUDICATION_REQUIRED,
                                         note="not adjudicated at analysis freeze")
            was_adjudicated = False
        final.append(FinalSegment(segment.packet_segment_id, segment.ordinal, decision.status, decision.fields,
                                  decision.formatting_variant, decision.mixed_reference_modes, was_adjudicated))
    if set(adjudicated) - set(packet.segment_ids):
        raise Engine3ValidationError("adjudication names a segment outside the packet")

    if primary_a.attestation.values() == primary_b.attestation.values():
        attestation = primary_a.attestation
        if adjudication is not None and adjudication.attestation is not None:
            raise Engine3ValidationError("adjudication may not override agreeing attestations")
    else:
        attestation = None if adjudication is None else adjudication.attestation
    return ArtifactReference(packet.packet_id, tuple(final), attestation)


def primary_field_agreement(pairs) -> dict[str, tuple[int, int]]:
    """Pre-adjudication exact agreement per field over segments both primaries marked SCORABLE.

    Input: iterable of (primary_a, primary_b) for packets of one arm. Output: field -> (agreeing, instances).
    """
    counts = {name: [0, 0] for name in REFERENCE_FIELDS}
    for primary_a, primary_b in pairs:
        for a, b in zip(primary_a.segments, primary_b.segments):
            if a.status is not AnnotationStatus.SCORABLE or b.status is not AnnotationStatus.SCORABLE:
                continue
            for name in REFERENCE_FIELDS:
                counts[name][1] += 1
                counts[name][0] += getattr(a.fields, name) == getattr(b.fields, name)
    return {name: (agree, total) for name, (agree, total) in counts.items()}


# --- strict parsing of reference records ----------------------------------------------------------


def _obj(value: object, keys: frozenset[str], where: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise Engine3ValidationError(f"{where} fields invalid")
    return value


def _enum(cls, value: object, where: str):
    if not isinstance(value, str):
        raise Engine3ValidationError(f"{where} must be a string")
    try:
        return cls(value)
    except ValueError:
        raise Engine3ValidationError(f"{where} is not a valid {cls.__name__}") from None


def _id_tuple(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Engine3ValidationError(f"{where} must be a list of ids")
    return tuple(value)


def reference_fields_from_dict(value: object) -> ReferenceFields:
    d = _obj(value, frozenset(REFERENCE_FIELDS), "reference fields")
    return ReferenceFields(
        claim_type=_enum(ClaimType, d["claim_type"], "claim_type"),
        semantic_class=_enum(ClaimSemanticClass, d["semantic_class"], "semantic_class"),
        grounding_refs=_id_tuple(d["grounding_refs"], "grounding_refs"),
        reference_use_mode=_enum(ReferenceUseMode, d["reference_use_mode"], "reference_use_mode"),
        source_identity_refs=_id_tuple(d["source_identity_refs"], "source_identity_refs"),
        claim_field=_enum(ClaimField, d["claim_field"], "claim_field"),
        declared_value=d["declared_value"],
        specificity=d["specificity"],
        question_has_premise=d["question_has_premise"],
        source_instruction_ref=d["source_instruction_ref"],
        prescribes_buyer_choice=d["prescribes_buyer_choice"],
    )


_SEGMENT_KEYS = frozenset({"status", "fields", "formatting_variant", "mixed_reference_modes", "note"})
_ATTESTATION_KEYS = frozenset(POLICY_ATTESTATIONS) | {"note"}


def segment_annotation_from_dict(packet_segment_id: str, value: object) -> SegmentAnnotation:
    d = _obj(value, _SEGMENT_KEYS, "segment annotation")
    return SegmentAnnotation(
        packet_segment_id=packet_segment_id,
        status=_enum(AnnotationStatus, d["status"], "status"),
        fields=None if d["fields"] is None else reference_fields_from_dict(d["fields"]),
        formatting_variant=d["formatting_variant"],
        mixed_reference_modes=d["mixed_reference_modes"],
        note=d["note"],
    )


def attestation_from_dict(value: object) -> HumanPolicyAttestation:
    d = _obj(value, _ATTESTATION_KEYS, "attestation")
    return HumanPolicyAttestation(**d)
