"""Stage F-AR blinded review requests, autonomous review records, and the autonomous reference.

A review request is built from three things only: the exact blinded AnnotationPacket
of the locked Stage F harness, the frozen operational guide (checked by SHA-256),
and a prompt bundle. Generator annotations, Stage D results, run identity, arm,
scenario, and calibration answers never reach a request, because the builder
takes nothing else. Reviewer A and Reviewer B receive identical requests.

The Autonomous Adjudicator is invoked only when Reviewer A and Reviewer B
disagree on a governed item, and its request can be built only from both parsed
A and B records. It sees them only as anonymized Review X and Review Y payloads;
the A and B record hashes are bound outside the visible request. It returns a
complete annotation of the packet, not a choice between A and B.

Raw output is parsed strictly. Any deviation is recorded as a rejection and never
repaired; a rejected review yields no autonomous reference. The final record keeps
both raw outputs, both parsed annotations, the adjudicator output when used, and
every disagreement. The result is AUTONOMOUS_CONSENSUS_REFERENCE or
AUTONOMOUS_ADJUDICATED_REFERENCE: never a human reference or ground truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum

from baec_app.engine3.canonical import canonical_json, canonical_sha256
from baec_app.engine3.errors import Engine3ValidationError
from experiments.engine3_stage_f.annotation import (
    LOCKED_PROTOCOL_SHA256,
    OPERATIONAL_GUIDE_SHA256,
    REFERENCE_FIELDS,
    AnnotationPacket,
    AnnotationStatus,
    SegmentAnnotation,
    _check_against_packet,
    annotation_packet_sha256,
    needs_adjudication,
    segment_annotation_from_dict,
)
from experiments.engine3_stage_f.envelope import RunKind

from .panel import REVIEWER_OUTPUT_SCHEMA_VERSION, ReviewerConfiguration, ReviewerRole

REQUEST_SCHEMA_VERSION = "engine3.stage_f_ar.review_request.v1"
TRACK = "STAGE_F_AR"
RESULT_NAMESPACE = "engine3_stage_f_autonomous_reference"
# Governed items compared between Reviewer A and Reviewer B. Flags are governed as in the human track.
DISAGREEMENT_ITEMS = ("status", "reviewer_requested_adjudication") + REFERENCE_FIELDS + (
    "formatting_variant", "mixed_reference_modes")

_DIGEST = re.compile(r"[0-9a-f]{64}")
_OUTPUT_KEYS = frozenset({"schema_version", "segments"})
_SEGMENT_KEYS = frozenset({"packet_segment_id", "status", "fields", "formatting_variant", "mixed_reference_modes",
                           "note"})


class AutonomousBatch(Enum):
    """Stage F-AR execution batches. None is a pilot: that term is reserved for the human-reference track.

    Stage F-AR does not execute, constitute, substitute for, or satisfy the Stage F human-reference pilot.
    """

    OFFLINE_DRY_RUN = "OFFLINE_DRY_RUN"
    AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH = "AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH"
    AR_FULL_RUN = "AR_FULL_RUN"


# The locked harness binds generator runs with its own RunKind enum, which cannot be renamed. Its identifier
# for the locked 44-slot material is mapped here to AR-44 and is never used to describe a Stage F-AR run.
BATCH_FOR_GENERATOR_RUN_KIND = {
    RunKind.DRY_RUN: AutonomousBatch.OFFLINE_DRY_RUN,
    RunKind.PILOT: AutonomousBatch.AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH,
    RunKind.FULL_RUN: AutonomousBatch.AR_FULL_RUN,
}


class AutonomousProvenance(Enum):
    """Experiment metadata only. Distinct from the generator's LIVE_SYNTHETIC_EXPERIMENT."""

    AUTONOMOUS_SYNTHETIC_REFERENCE = "AUTONOMOUS_SYNTHETIC_REFERENCE"
    OFFLINE_HANDWRITTEN_AUTONOMOUS_DRY_RUN = "OFFLINE_HANDWRITTEN_AUTONOMOUS_DRY_RUN"


_PROVENANCE = {
    AutonomousBatch.OFFLINE_DRY_RUN: AutonomousProvenance.OFFLINE_HANDWRITTEN_AUTONOMOUS_DRY_RUN,
    AutonomousBatch.AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH: AutonomousProvenance.AUTONOMOUS_SYNTHETIC_REFERENCE,
    AutonomousBatch.AR_FULL_RUN: AutonomousProvenance.AUTONOMOUS_SYNTHETIC_REFERENCE,
}


class AutonomousReferenceKind(Enum):
    """The only two labels an autonomous result can carry."""

    AUTONOMOUS_CONSENSUS_REFERENCE = "AUTONOMOUS_CONSENSUS_REFERENCE"
    AUTONOMOUS_ADJUDICATED_REFERENCE = "AUTONOMOUS_ADJUDICATED_REFERENCE"


class ReviewRejection(Enum):
    NOT_UTF8 = "NOT_UTF8"
    NOT_JSON = "NOT_JSON"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"


class ReviewRejectionError(Engine3ValidationError):
    def __init__(self, rejection: ReviewRejection, detail: str) -> None:
        super().__init__(f"{rejection.value}: {detail}")
        self.rejection = rejection
        self.detail = detail


def _text(value: object, where: str) -> None:
    if not isinstance(value, str) or not value:
        raise Engine3ValidationError(f"{where} must be a non-empty string")


def _digest(value: object, where: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise Engine3ValidationError(f"{where} must be SHA-256 hex")


# --- requests -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewRequest:
    """The exact canonical request a reviewer receives, with the hashes it is bound to."""

    body: str
    annotation_packet_sha256: str
    prompt_bundle_sha256: str
    reviewed_record_sha256s: tuple[str, str] | None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def is_adjudication(self) -> bool:
        return self.reviewed_record_sha256s is not None


def _guide_text(operational_guide: bytes) -> str:
    if not isinstance(operational_guide, bytes):
        raise Engine3ValidationError("operational guide must be bytes")
    if hashlib.sha256(operational_guide).hexdigest() != OPERATIONAL_GUIDE_SHA256:
        raise Engine3ValidationError("operational guide is not the frozen operational guide")
    return operational_guide.decode("utf-8")


def _request(packet: AnnotationPacket, operational_guide: bytes, prompt_bundle: str, task: str,
             extra: dict, reviewed: tuple[str, str] | None) -> ReviewRequest:
    if not isinstance(packet, AnnotationPacket):
        raise Engine3ValidationError("packet has wrong type")
    _text(prompt_bundle, "prompt bundle")
    body = {
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "task": task,
        "instructions": prompt_bundle,
        "operational_guide": _guide_text(operational_guide),
        "packet": packet,
        "output_schema_version": REVIEWER_OUTPUT_SCHEMA_VERSION,
        **extra,
    }
    return ReviewRequest(
        body=canonical_json(body),
        annotation_packet_sha256=annotation_packet_sha256(packet),
        prompt_bundle_sha256=hashlib.sha256(prompt_bundle.encode("utf-8")).hexdigest(),
        reviewed_record_sha256s=reviewed,
    )


def build_review_request(packet: AnnotationPacket, operational_guide: bytes, prompt_bundle: str) -> ReviewRequest:
    """Independent review request. Identical for Reviewer A and Reviewer B; carries no role or reviewer output."""
    return _request(packet, operational_guide, prompt_bundle, "INDEPENDENT_REVIEW", {}, None)


def build_adjudication_request(
    packet: AnnotationPacket,
    operational_guide: bytes,
    prompt_bundle: str,
    record_a: ReviewRecord,
    record_b: ReviewRecord,
) -> ReviewRequest:
    """Adjudication request. Requires both recorded, parsed A and B reviews and at least one disagreement.

    The visible body holds the packet, the guide, two anonymized annotation payloads (Review X and Review Y),
    and the (segment, item) pairs on which they disagree. It never names a reviewer role, family, model,
    configuration, provider, or record hash, and the X/Y order is fixed by payload content, so it cannot
    reveal which review came from Reviewer A. The A and B record hashes are bound outside the body.
    """
    _check_pair(packet, record_a, record_b)
    disagreements = ab_disagreements(packet, record_a, record_b)
    if not disagreements:
        raise Engine3ValidationError("the adjudicator is invoked only when Reviewer A and Reviewer B disagree")
    payloads = sorted((anonymized_payload(r) for r in (record_a, record_b)), key=canonical_sha256)
    pairs = dict.fromkeys((d.ordinal, d.packet_segment_id, d.item) for d in disagreements)
    extra = {
        "review_x": payloads[0],
        "review_y": payloads[1],
        "disagreements": [{"packet_segment_id": s, "item": i} for _, s, i in sorted(
            pairs, key=lambda p: (p[0], DISAGREEMENT_ITEMS.index(p[2])))],
    }
    return _request(packet, operational_guide, prompt_bundle, "AUTONOMOUS_ADJUDICATION", extra,
                    (record_a.record_sha256, record_b.record_sha256))


def anonymized_payload(record: ReviewRecord) -> list[dict]:
    """A parsed annotation with nothing but governed content: no role, provenance, hash, or free-text note."""
    if not isinstance(record, ReviewRecord) or not record.parsed:
        raise Engine3ValidationError("only a parsed review record has an annotation payload")
    return [
        {"packet_segment_id": s.packet_segment_id, "status": s.status.value,
         "fields": None if s.fields is None else json.loads(canonical_json(s.fields)),
         "formatting_variant": s.formatting_variant, "mixed_reference_modes": s.mixed_reference_modes}
        for s in record.segments
    ]


# --- strict output parsing ------------------------------------------------------------------------


def _reject_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ReviewRejectionError(ReviewRejection.NOT_JSON, "duplicate object key")
    return dict(pairs)


def _reject_constant(name):
    raise ReviewRejectionError(ReviewRejection.NOT_JSON, f"non-standard constant {name}")


def parse_review_output(packet: AnnotationPacket, raw_output: bytes) -> tuple[SegmentAnnotation, ...]:
    """Parse one reviewer's raw output against the packet it reviewed. Fails closed with a rejection class."""
    if not isinstance(packet, AnnotationPacket):
        raise Engine3ValidationError("packet has wrong type")
    if not isinstance(raw_output, bytes):
        raise Engine3ValidationError("raw output must be bytes")
    try:
        text = raw_output.decode("utf-8")
    except UnicodeDecodeError:
        raise ReviewRejectionError(ReviewRejection.NOT_UTF8, "output is not UTF-8") from None
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ReviewRejectionError(ReviewRejection.NOT_JSON, exc.msg) from None
    try:
        if not isinstance(value, dict) or set(value) != _OUTPUT_KEYS:
            raise Engine3ValidationError("output fields invalid")
        if value["schema_version"] != REVIEWER_OUTPUT_SCHEMA_VERSION:
            raise Engine3ValidationError("output schema version is not supported")
        items = value["segments"]
        if not isinstance(items, list) or not all(isinstance(i, dict) and set(i) == _SEGMENT_KEYS for i in items):
            raise Engine3ValidationError("output segments invalid")
        if tuple(i["packet_segment_id"] for i in items) != packet.segment_ids:
            raise Engine3ValidationError("output must cover every packet segment in order")
        segments = tuple(
            segment_annotation_from_dict(i["packet_segment_id"], {k: v for k, v in i.items() if k != "packet_segment_id"})
            for i in items
        )
        # Reused unchanged: autonomous annotations meet exactly the constraints on human records.
        _check_against_packet(packet, segments, "review output")
    except ReviewRejectionError:
        raise
    except Engine3ValidationError as exc:
        raise ReviewRejectionError(ReviewRejection.INVALID_STRUCTURE, str(exc)) from None
    return segments


# --- records --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewRecord:
    """Write-once record of one review attempt: provenance, exact raw bytes, and parse outcome."""

    reviewer_role: ReviewerRole
    reviewer_family: str
    reviewer_model_id: str
    reviewer_configuration_sha256: str
    prompt_bundle_sha256: str
    locked_protocol_sha256: str
    operational_guide_sha256: str
    annotation_packet_sha256: str
    request_sha256: str
    reviewed_record_sha256s: tuple[str, str] | None
    raw_output: bytes
    raw_output_sha256: str
    rejection: ReviewRejection | None
    rejection_detail: str | None
    segments: tuple[SegmentAnnotation, ...] | None
    parsed_annotation_sha256: str | None
    run_slot_id: str
    attempt_number: int
    batch: AutonomousBatch
    provenance: AutonomousProvenance
    track: str = TRACK

    def __post_init__(self) -> None:
        if not isinstance(self.reviewer_role, ReviewerRole):
            raise Engine3ValidationError("ReviewRecord.reviewer_role has wrong type")
        for name in ("reviewer_family", "reviewer_model_id", "run_slot_id"):
            _text(getattr(self, name), f"ReviewRecord.{name}")
        for name in ("reviewer_configuration_sha256", "prompt_bundle_sha256", "locked_protocol_sha256",
                     "operational_guide_sha256", "annotation_packet_sha256", "request_sha256", "raw_output_sha256"):
            _digest(getattr(self, name), f"ReviewRecord.{name}")
        if self.locked_protocol_sha256 != LOCKED_PROTOCOL_SHA256:
            raise Engine3ValidationError("ReviewRecord.locked_protocol_sha256 is not the historical locked protocol")
        if self.operational_guide_sha256 != OPERATIONAL_GUIDE_SHA256:
            raise Engine3ValidationError("ReviewRecord.operational_guide_sha256 is not the frozen operational guide")
        if not isinstance(self.raw_output, bytes) or hashlib.sha256(self.raw_output).hexdigest() != self.raw_output_sha256:
            raise Engine3ValidationError("ReviewRecord.raw_output_sha256 does not match the raw output")
        adjudicator = self.reviewer_role is ReviewerRole.AUTONOMOUS_ADJUDICATOR
        if adjudicator != (self.reviewed_record_sha256s is not None):
            raise Engine3ValidationError("only an adjudicator record binds the reviewed A and B records")
        if self.reviewed_record_sha256s is not None:
            if not isinstance(self.reviewed_record_sha256s, tuple) or len(self.reviewed_record_sha256s) != 2:
                raise Engine3ValidationError("ReviewRecord.reviewed_record_sha256s must be (A, B)")
            for value in self.reviewed_record_sha256s:
                _digest(value, "ReviewRecord.reviewed_record_sha256s")
        if not isinstance(self.batch, AutonomousBatch):
            raise Engine3ValidationError("ReviewRecord.batch has wrong type")
        if self.provenance is not _PROVENANCE[self.batch]:
            raise Engine3ValidationError("ReviewRecord provenance must match its batch")
        if (not isinstance(self.attempt_number, int) or isinstance(self.attempt_number, bool)
                or self.attempt_number < 1):
            raise Engine3ValidationError("ReviewRecord.attempt_number must be an int >= 1")
        if self.track != TRACK:
            raise Engine3ValidationError("ReviewRecord.track must be STAGE_F_AR")
        if self.rejection is None:
            if (not isinstance(self.segments, tuple) or not all(isinstance(s, SegmentAnnotation) for s in self.segments)
                    or self.rejection_detail is not None):
                raise Engine3ValidationError("a parsed record holds its parsed segments and no rejection")
            if self.parsed_annotation_sha256 != canonical_sha256(self.segments):
                raise Engine3ValidationError("ReviewRecord.parsed_annotation_sha256 does not match the segments")
        else:
            if not isinstance(self.rejection, ReviewRejection):
                raise Engine3ValidationError("ReviewRecord.rejection has wrong type")
            if self.segments is not None or self.parsed_annotation_sha256 is not None:
                raise Engine3ValidationError("a rejected record holds no parsed annotation")
            _text(self.rejection_detail, "ReviewRecord.rejection_detail")

    @property
    def parsed(self) -> bool:
        return self.rejection is None

    def provenance_record(self) -> dict:
        """Every provenance field, without the raw bytes (bound by raw_output_sha256)."""
        return {
            "reviewer_role": self.reviewer_role.value,
            "reviewer_family": self.reviewer_family,
            "reviewer_model_id": self.reviewer_model_id,
            "reviewer_configuration_sha256": self.reviewer_configuration_sha256,
            "prompt_bundle_sha256": self.prompt_bundle_sha256,
            "locked_protocol_sha256": self.locked_protocol_sha256,
            "operational_guide_sha256": self.operational_guide_sha256,
            "annotation_packet_sha256": self.annotation_packet_sha256,
            "request_sha256": self.request_sha256,
            "reviewed_record_sha256s": self.reviewed_record_sha256s,
            "raw_output_sha256": self.raw_output_sha256,
            "parsed_annotation_sha256": self.parsed_annotation_sha256,
            "run_slot_id": self.run_slot_id,
            "attempt_number": self.attempt_number,
            "batch": self.batch.value,
            "provenance": self.provenance.value,
            "track": self.track,
        }

    @property
    def record_sha256(self) -> str:
        return canonical_sha256({
            "provenance": self.provenance_record(),
            "rejection": None if self.rejection is None else self.rejection.value,
            "rejection_detail": self.rejection_detail,
            "segments": self.segments,
        })


def record_review(
    configuration: ReviewerConfiguration,
    request: ReviewRequest,
    packet: AnnotationPacket,
    raw_output: bytes,
    run_slot_id: str,
    attempt_number: int,
    batch: AutonomousBatch,
) -> ReviewRecord:
    """Record one review attempt. A rejection is recorded, never repaired."""
    if not isinstance(configuration, ReviewerConfiguration):
        raise Engine3ValidationError("configuration has wrong type")
    if not isinstance(request, ReviewRequest):
        raise Engine3ValidationError("request has wrong type")
    if not isinstance(batch, AutonomousBatch):
        raise Engine3ValidationError("batch has wrong type")
    if batch is not AutonomousBatch.OFFLINE_DRY_RUN and configuration.frozen is not True:
        raise Engine3ValidationError("an AR-44 or AR full-run review needs a frozen reviewer configuration")
    if request.annotation_packet_sha256 != annotation_packet_sha256(packet):
        raise Engine3ValidationError("the request was not built from this exact packet")
    if request.prompt_bundle_sha256 != configuration.prompt_bundle_sha256:
        raise Engine3ValidationError("the request prompt bundle is not the configured prompt bundle")
    if request.is_adjudication != (configuration.role is ReviewerRole.AUTONOMOUS_ADJUDICATOR):
        raise Engine3ValidationError("only the Autonomous Adjudicator receives an adjudication request")
    try:
        segments, rejection, detail = parse_review_output(packet, raw_output), None, None
    except ReviewRejectionError as exc:
        segments, rejection, detail = None, exc.rejection, exc.detail
    return ReviewRecord(
        reviewer_role=configuration.role,
        reviewer_family=configuration.model_family,
        reviewer_model_id=configuration.model_id,
        reviewer_configuration_sha256=configuration.configuration_sha256,
        prompt_bundle_sha256=configuration.prompt_bundle_sha256,
        locked_protocol_sha256=LOCKED_PROTOCOL_SHA256,
        operational_guide_sha256=OPERATIONAL_GUIDE_SHA256,
        annotation_packet_sha256=request.annotation_packet_sha256,
        request_sha256=request.sha256,
        reviewed_record_sha256s=request.reviewed_record_sha256s,
        raw_output=raw_output,
        raw_output_sha256=hashlib.sha256(raw_output).hexdigest(),
        rejection=rejection,
        rejection_detail=detail,
        segments=segments,
        parsed_annotation_sha256=None if segments is None else canonical_sha256(segments),
        run_slot_id=run_slot_id,
        attempt_number=attempt_number,
        batch=batch,
        provenance=_PROVENANCE[batch],
    )


# --- consensus and adjudication -------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewDisagreement:
    packet_segment_id: str
    ordinal: int
    item: str
    reviewer_a_value: object
    reviewer_b_value: object


def _check_pair(packet: AnnotationPacket, record_a: ReviewRecord, record_b: ReviewRecord) -> None:
    if not isinstance(packet, AnnotationPacket):
        raise Engine3ValidationError("packet has wrong type")
    if not isinstance(record_a, ReviewRecord) or not isinstance(record_b, ReviewRecord):
        raise Engine3ValidationError("Reviewer A and Reviewer B records are both required")
    if record_a.reviewer_role is not ReviewerRole.REVIEWER_A or record_b.reviewer_role is not ReviewerRole.REVIEWER_B:
        raise Engine3ValidationError("records must be Reviewer A and Reviewer B, in that order")
    if not record_a.parsed or not record_b.parsed:
        raise Engine3ValidationError("a rejected review yields no autonomous reference")
    digest = annotation_packet_sha256(packet)
    if record_a.annotation_packet_sha256 != digest or record_b.annotation_packet_sha256 != digest:
        raise Engine3ValidationError("a review record was not made on this exact packet")
    if record_a.batch is not record_b.batch:
        raise Engine3ValidationError("Reviewer A and Reviewer B records belong to different batches")
    if record_a.reviewer_family.strip().casefold() == record_b.reviewer_family.strip().casefold():
        raise Engine3ValidationError("Reviewer A and Reviewer B must be different model families")


def ab_disagreements(
    packet: AnnotationPacket, record_a: ReviewRecord, record_b: ReviewRecord
) -> tuple[ReviewDisagreement, ...]:
    """Every governed item on which Reviewer A and Reviewer B differ, segment by segment."""
    _check_pair(packet, record_a, record_b)
    found = []
    for segment, a, b in zip(packet.segments, record_a.segments, record_b.segments):
        def add(item: str, a_value: object, b_value: object) -> None:
            found.append(ReviewDisagreement(segment.packet_segment_id, segment.ordinal, item, a_value, b_value))

        if a.status is not b.status:
            add("status", a.status, b.status)
        if AnnotationStatus.ADJUDICATION_REQUIRED in (a.status, b.status):
            add("reviewer_requested_adjudication", a.status, b.status)
        if a.status is AnnotationStatus.SCORABLE and b.status is AnnotationStatus.SCORABLE:
            for name in REFERENCE_FIELDS:
                if getattr(a.fields, name) != getattr(b.fields, name):
                    add(name, getattr(a.fields, name), getattr(b.fields, name))
            for name in ("formatting_variant", "mixed_reference_modes"):
                if getattr(a, name) != getattr(b, name):
                    add(name, getattr(a, name), getattr(b, name))
        if needs_adjudication(a, b) != any(d.packet_segment_id == segment.packet_segment_id for d in found):
            raise Engine3ValidationError("disagreement items diverge from the locked adjudication trigger")
    return tuple(found)


def _same_decision(a: SegmentAnnotation, b: SegmentAnnotation) -> bool:
    return (a.status, a.fields, a.formatting_variant, a.mixed_reference_modes) == (
        b.status, b.fields, b.formatting_variant, b.mixed_reference_modes)


@dataclass(frozen=True)
class AutonomousReference:
    """Final autonomous reference for one packet. Preserves the full A, B, and adjudication history."""

    packet_id: str
    annotation_packet_sha256: str
    kind: AutonomousReferenceKind
    segments: tuple[SegmentAnnotation, ...]
    reviewer_a: ReviewRecord
    reviewer_b: ReviewRecord
    adjudicator: ReviewRecord | None
    disagreements: tuple[ReviewDisagreement, ...]
    batch: AutonomousBatch
    provenance: AutonomousProvenance
    track: str = TRACK
    result_namespace: str = RESULT_NAMESPACE

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AutonomousReferenceKind):
            raise Engine3ValidationError("an autonomous result is labelled only AUTONOMOUS_CONSENSUS_REFERENCE "
                                         "or AUTONOMOUS_ADJUDICATED_REFERENCE")
        if self.track != TRACK or self.result_namespace != RESULT_NAMESPACE:
            raise Engine3ValidationError("an autonomous reference lives only in the Stage F-AR namespace")
        if self.provenance is not _PROVENANCE.get(self.batch):
            raise Engine3ValidationError("AutonomousReference provenance must match its batch")
        consensus = self.kind is AutonomousReferenceKind.AUTONOMOUS_CONSENSUS_REFERENCE
        if consensus != (self.adjudicator is None) or consensus == bool(self.disagreements):
            raise Engine3ValidationError("consensus means no disagreement and no adjudicator; otherwise both")

    @property
    def label(self) -> str:
        return self.kind.value

    @property
    def reference_complete(self) -> bool:
        return all(s.status is AnnotationStatus.SCORABLE for s in self.segments)

    @property
    def adjudicated_items(self) -> tuple[str, ...]:
        """Which governed items required adjudication, in the locked item order."""
        items = {d.item for d in self.disagreements}
        return tuple(i for i in DISAGREEMENT_ITEMS if i in items)

    @property
    def adjudicator_departures(self) -> tuple[str, ...]:
        """Segments where A and B agreed but the adjudicator's complete annotation differs from them."""
        if self.adjudicator is None:
            return ()
        disputed = {d.packet_segment_id for d in self.disagreements}
        return tuple(
            final.packet_segment_id
            for a, final in zip(self.reviewer_a.segments, self.segments)
            if final.packet_segment_id not in disputed and not _same_decision(a, final)
        )

    @property
    def reference_sha256(self) -> str:
        return canonical_sha256({
            "packet_id": self.packet_id,
            "annotation_packet_sha256": self.annotation_packet_sha256,
            "kind": self.kind,
            "segments": self.segments,
            "reviewer_a_record_sha256": self.reviewer_a.record_sha256,
            "reviewer_b_record_sha256": self.reviewer_b.record_sha256,
            "adjudicator_record_sha256": None if self.adjudicator is None else self.adjudicator.record_sha256,
            "disagreements": self.disagreements,
            "batch": self.batch,
            "provenance": self.provenance,
            "track": self.track,
            "result_namespace": self.result_namespace,
        })


def finalize_autonomous_reference(
    packet: AnnotationPacket,
    record_a: ReviewRecord,
    record_b: ReviewRecord,
    adjudicator: ReviewRecord | None = None,
) -> AutonomousReference:
    """Locked consensus logic: exact A/B agreement on every governed item is consensus; anything else
    requires a complete, parsed adjudication bound to both recorded reviews."""
    disagreements = ab_disagreements(packet, record_a, record_b)
    if not disagreements:
        if adjudicator is not None:
            raise Engine3ValidationError("the adjudicator is invoked only when Reviewer A and Reviewer B disagree")
        kind, segments = AutonomousReferenceKind.AUTONOMOUS_CONSENSUS_REFERENCE, record_a.segments
    else:
        if adjudicator is None:
            raise Engine3ValidationError("a Reviewer A / Reviewer B disagreement requires the Autonomous Adjudicator")
        if not isinstance(adjudicator, ReviewRecord) or adjudicator.reviewer_role is not ReviewerRole.AUTONOMOUS_ADJUDICATOR:
            raise Engine3ValidationError("the adjudication record must come from the Autonomous Adjudicator")
        if not adjudicator.parsed:
            raise Engine3ValidationError("a rejected adjudication yields no autonomous reference")
        if adjudicator.annotation_packet_sha256 != record_a.annotation_packet_sha256:
            raise Engine3ValidationError("the adjudication was not made on this exact packet")
        if adjudicator.reviewed_record_sha256s != (record_a.record_sha256, record_b.record_sha256):
            raise Engine3ValidationError("the adjudication is not bound to these recorded A and B reviews")
        if adjudicator.batch is not record_a.batch:
            raise Engine3ValidationError("the adjudication belongs to a different batch")
        kind, segments = AutonomousReferenceKind.AUTONOMOUS_ADJUDICATED_REFERENCE, adjudicator.segments
    configurations = {r.reviewer_configuration_sha256 for r in (record_a, record_b, adjudicator) if r is not None}
    if len(configurations) != (2 if adjudicator is None else 3):
        raise Engine3ValidationError("every panel role must be separately configured")
    return AutonomousReference(
        packet_id=packet.packet_id,
        annotation_packet_sha256=record_a.annotation_packet_sha256,
        kind=kind,
        segments=segments,
        reviewer_a=record_a,
        reviewer_b=record_b,
        adjudicator=adjudicator,
        disagreements=disagreements,
        batch=record_a.batch,
        provenance=_PROVENANCE[record_a.batch],
    )
