"""Engine 2 domain records: vocabularies, Monitoring Plan, sources, Observations.

Authority: docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md
(Stage A, LOCKED) and the Stage B corpus (LOCKED). Every concept here is
IMPLEMENTATION; none is a manuscript construct.

Like the Engine 1 domain layer, every record is a frozen dataclass that
validates itself on creation, and every vocabulary is a strict enum. This
module reads Engine 1 values (provenance categories, staleness, comparators)
but never writes to Engine 1 and never decides BAEC validity or currentness.

Nothing here interprets source text. Observations are stored verbatim;
relevance and findings are supplied by humans (classification.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from baec_app.domain.enums import ProvenanceCategory, StalenessStatus, ThresholdComparator

from .errors import Engine2ValidationError, MonitoringPlanActivationError, SourceNotAuthorizedError

# --- vocabularies (Stage A Sections 8.2, 8.3, 10, 11) ------------------------


class CorrespondenceDimension(Enum):
    """The 11 correspondence dimensions (Stage A Section 8.2). Value = machine id."""

    ENTITY_MATCH = "ENTITY_MATCH"
    RELATIONSHIP_MATCH = "RELATIONSHIP_MATCH"
    SOURCE_RELEVANCE = "SOURCE_RELEVANCE"
    EVENT_OR_CONDITION_TYPE = "EVENT_OR_CONDITION_TYPE"
    THRESHOLD_MATCH = "THRESHOLD_MATCH"
    COMPARATOR_MATCH = "COMPARATOR_MATCH"
    NUMERIC_MAGNITUDE = "NUMERIC_MAGNITUDE"
    UNIT_MATCH = "UNIT_MATCH"
    TIMING_MATCH = "TIMING_MATCH"
    RENEWAL_OR_EFFECTIVE_DATE_MATCH = "RENEWAL_OR_EFFECTIVE_DATE_MATCH"
    CAUSAL_OR_CONTEXTUAL_RELEVANCE = "CAUSAL_OR_CONTEXTUAL_RELEVANCE"

    @property
    def label(self) -> str:
        return _DIMENSION_LABELS[self]

    @classmethod
    def from_label(cls, label: str) -> CorrespondenceDimension:
        for member, text in _DIMENSION_LABELS.items():
            if text == label:
                return member
        raise Engine2ValidationError(f"unknown correspondence dimension: {label!r}")


_DIMENSION_LABELS = {
    CorrespondenceDimension.ENTITY_MATCH: "Entity match",
    CorrespondenceDimension.RELATIONSHIP_MATCH: "Relationship match",
    CorrespondenceDimension.SOURCE_RELEVANCE: "Source relevance",
    CorrespondenceDimension.EVENT_OR_CONDITION_TYPE: "Event or condition type",
    CorrespondenceDimension.THRESHOLD_MATCH: "Threshold match",
    CorrespondenceDimension.COMPARATOR_MATCH: "Comparator match",
    CorrespondenceDimension.NUMERIC_MAGNITUDE: "Numeric magnitude",
    CorrespondenceDimension.UNIT_MATCH: "Unit match",
    CorrespondenceDimension.TIMING_MATCH: "Timing match",
    CorrespondenceDimension.RENEWAL_OR_EFFECTIVE_DATE_MATCH: "Renewal or effective-date match",
    CorrespondenceDimension.CAUSAL_OR_CONTEXTUAL_RELEVANCE: "Causal or contextual relevance",
}


class DimensionFinding(Enum):
    """A dimension finding (Stage A Section 8.3). Only dimensions take these."""

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CrossCuttingCheckName(Enum):
    """The 4 cross-cutting checks (Stage A Section 8.2). Not dimensions; no findings."""

    FRESHNESS = "FRESHNESS"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    PROVENANCE_QUALITY = "PROVENANCE_QUALITY"

    @property
    def label(self) -> str:
        return _CHECK_LABELS[self]

    @classmethod
    def from_label(cls, label: str) -> CrossCuttingCheckName:
        for member, text in _CHECK_LABELS.items():
            if text == label:
                return member
        raise Engine2ValidationError(f"unknown cross-cutting check: {label!r}")


_CHECK_LABELS = {
    CrossCuttingCheckName.FRESHNESS: "Freshness",
    CrossCuttingCheckName.CONTRADICTORY_EVIDENCE: "Contradictory evidence",
    CrossCuttingCheckName.MISSING_EVIDENCE: "Missing evidence",
    CrossCuttingCheckName.PROVENANCE_QUALITY: "Provenance quality",
}


class SufficiencyFinding(Enum):
    """The human sufficiency judgment, exact Stage A Section 10 vocabulary."""

    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class CorrespondenceOutcome(Enum):
    """The three correspondence outcomes (Stage A Section 11.1). Always derived."""

    NO_CORRESPONDENCE = "NO_CORRESPONDENCE"
    POSSIBLE_CORRESPONDENCE = "POSSIBLE_CORRESPONDENCE"
    HUMAN_VERIFIED_CORRESPONDENCE = "HUMAN_VERIFIED_CORRESPONDENCE"


BAEC_REVALIDATION_REQUIRED = "BAEC_REVALIDATION_REQUIRED"
"""Name of the Stage A Section 4.3 workflow condition. A flag, never an outcome."""


class SupersessionBasis(Enum):
    """Authority bases on which one Observation may supersede another (Stage B, locked)."""

    EXPLICIT_CORRECTION_OR_RETRACTION = "explicit correction or retraction language"
    VERSION_OR_REVISION_METADATA = "version or revision metadata"
    CONTRACTUAL_AMENDMENT_HIERARCHY = "contractual amendment hierarchy"
    EFFECTIVE_DATE_LOGIC = "effective-date logic"
    MONITORING_PLAN_SOURCE_RULE = "Monitoring Plan source authority rule"


# Observations are evidence; AI inference and unknown-origin text never are (RC-32).
_OBSERVATION_PROVENANCE = frozenset(
    {ProvenanceCategory.BUYER_FACT, ProvenanceCategory.SELLER_OBSERVATION, ProvenanceCategory.EXTERNAL_EVIDENCE}
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
ALL_DIMENSIONS = frozenset(CorrespondenceDimension)

# --- content integrity (approved Stage C rule) -------------------------------


def content_integrity_sha256(content: str) -> str:
    """SHA-256 of the exact stored evidence text, encoded as UTF-8.

    No trimming, Unicode or case normalization, line-ending conversion,
    whitespace collapsing, or metadata. Not a hash of any original source artifact.
    """
    if not isinstance(content, str):
        raise Engine2ValidationError("evidence content must be a string")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --- validation helpers -------------------------------------------------------


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise Engine2ValidationError(f"{field} must be a non-empty string")


def _optional_text(value: object, field: str) -> None:
    if value is not None:
        _text(value, field)


def _instance(value: object, expected: type, field: str) -> None:
    if not isinstance(value, expected):
        raise Engine2ValidationError(f"{field} must be {expected.__name__}, got {type(value).__name__}")


def _aware(value: object, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Engine2ValidationError(f"{field} must be a timezone-aware datetime")


def _optional_aware(value: object, field: str) -> None:
    if value is not None:
        _aware(value, field)


def _plan_version(value: object, field: str) -> None:
    """Exact Monitoring Plan version: a positive int, never a default and never inferred."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise Engine2ValidationError(f"{field} must be a positive integer")


def _text_set(value: object, field: str, *, allow_empty: bool = True) -> None:
    if not isinstance(value, frozenset):
        raise Engine2ValidationError(f"{field} must be a frozenset")
    if not allow_empty and not value:
        raise Engine2ValidationError(f"{field} must not be empty")
    for item in value:
        _text(item, f"{field} item")


def _text_tuple(value: object, field: str, *, allow_empty: bool = True) -> None:
    if not isinstance(value, tuple):
        raise Engine2ValidationError(f"{field} must be a tuple")
    if not allow_empty and not value:
        raise Engine2ValidationError(f"{field} must not be empty")
    for item in value:
        _text(item, f"{field} item")
    if len(set(value)) != len(value):
        raise Engine2ValidationError(f"{field} contains duplicates")


# --- Authorized Source (Stage A Section 6) ----------------------------------


@dataclass(frozen=True)
class AuthorizedSource:
    """A source a human approved for a plan, with its legitimate basis and scope."""

    source_id: str
    source_type: str
    owner_or_publisher: str
    legitimate_basis: str
    access_method: str
    permitted_capture: str
    authorized_by: str
    authorized_at: datetime
    known_limitations: str | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "source_type", "owner_or_publisher", "legitimate_basis",
                     "access_method", "permitted_capture", "authorized_by"):
            _text(getattr(self, name), f"AuthorizedSource.{name}")
        _aware(self.authorized_at, "AuthorizedSource.authorized_at")
        _optional_text(self.known_limitations, "AuthorizedSource.known_limitations")


# --- Monitoring Plan (Stage A Sections 4.3 and 5) ---------------------------


@dataclass(frozen=True)
class ThresholdSpec:
    """The buyer's threshold exactly as Engine 1 stores it (RC-18). Never re-derived."""

    comparator: ThresholdComparator
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        _instance(self.comparator, ThresholdComparator, "ThresholdSpec.comparator")
        _instance(self.value, Decimal, "ThresholdSpec.value")
        if not self.value.is_finite():
            raise Engine2ValidationError("ThresholdSpec.value must be finite")
        _text(self.unit, "ThresholdSpec.unit")


@dataclass(frozen=True)
class MonitoringPlan:
    """A human-authorized plan specification (Stage A Section 5). Not active by itself.

    Only activate_monitoring_plan() produces an ActiveMonitoringPlan, and only
    when Engine 1 reports the BAEC CURRENT.
    """

    monitoring_plan_id: str
    plan_version: int
    baec_id: str
    condition_reference: str
    target_entities: tuple[str, ...]
    required_dimensions: frozenset[CorrespondenceDimension]
    not_applicable_dimensions: frozenset[CorrespondenceDimension]
    authorized_source_ids: frozenset[str]
    engine1_currentness_reference: str
    authorized_by: str
    authorization_reference: str
    authorized_at: datetime
    threshold: ThresholdSpec | None = None
    timing_context: str | None = None
    plan_fact_ids: frozenset[str] = frozenset()
    plan_rule_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("monitoring_plan_id", "baec_id", "condition_reference", "engine1_currentness_reference",
                     "authorized_by", "authorization_reference"):
            _text(getattr(self, name), f"MonitoringPlan.{name}")
        _plan_version(self.plan_version, "MonitoringPlan.plan_version")
        _text_tuple(self.target_entities, "MonitoringPlan.target_entities", allow_empty=False)
        for name in ("required_dimensions", "not_applicable_dimensions"):
            value = getattr(self, name)
            if not isinstance(value, frozenset) or not all(isinstance(d, CorrespondenceDimension) for d in value):
                raise Engine2ValidationError(f"MonitoringPlan.{name} must be a frozenset of CorrespondenceDimension")
        if self.required_dimensions & self.not_applicable_dimensions:
            raise Engine2ValidationError("a dimension cannot be both required and not applicable")
        if self.required_dimensions | self.not_applicable_dimensions != ALL_DIMENSIONS:
            raise Engine2ValidationError("the plan must mark every one of the 11 dimensions required or not applicable")
        if not self.required_dimensions:
            raise Engine2ValidationError("a plan must require at least one dimension")
        _text_set(self.authorized_source_ids, "MonitoringPlan.authorized_source_ids", allow_empty=False)
        _aware(self.authorized_at, "MonitoringPlan.authorized_at")
        if self.threshold is not None:
            _instance(self.threshold, ThresholdSpec, "MonitoringPlan.threshold")
        _optional_text(self.timing_context, "MonitoringPlan.timing_context")
        _text_set(self.plan_fact_ids, "MonitoringPlan.plan_fact_ids")
        _text_set(self.plan_rule_ids, "MonitoringPlan.plan_rule_ids")

    @property
    def reference_ids(self) -> frozenset[str]:
        """Plan facts and rules that a finding may cite alongside evidence."""
        return self.plan_fact_ids | self.plan_rule_ids


@dataclass(frozen=True)
class Engine1CurrentnessReading:
    """Engine 1's currentness report for one BAEC, supplied as an input.

    Engine 2 never determines currentness. staleness_status None means the
    reading was missing or unreadable.
    """

    baec_id: str
    staleness_status: StalenessStatus | None
    record_reference: str | None

    def __post_init__(self) -> None:
        _text(self.baec_id, "Engine1CurrentnessReading.baec_id")
        if self.staleness_status is not None:
            _instance(self.staleness_status, StalenessStatus, "Engine1CurrentnessReading.staleness_status")
        _optional_text(self.record_reference, "Engine1CurrentnessReading.record_reference")


def _activation_problem(plan: MonitoringPlan, reading: Engine1CurrentnessReading | None) -> str | None:
    if reading is None:
        return "Engine 1 currentness reading is missing"
    if not isinstance(reading, Engine1CurrentnessReading):
        return "Engine 1 currentness reading has the wrong type"
    if reading.baec_id != plan.baec_id:
        return "Engine 1 currentness reading is for a different BAEC"
    if reading.staleness_status is None or reading.record_reference is None:
        return "Engine 1 currentness reading is unreadable"
    if reading.record_reference != plan.engine1_currentness_reference:
        return "Engine 1 currentness record does not match the plan's currentness reference"
    if reading.staleness_status is not StalenessStatus.CURRENT:
        return f"Engine 1 reports the BAEC {reading.staleness_status.value}, not CURRENT"
    return None


@dataclass(frozen=True)
class ActiveMonitoringPlan:
    """A plan that is ACTIVE because Engine 1 reported its BAEC CURRENT.

    Validated on creation, so an active plan without a CURRENT reading cannot
    exist (fail closed).
    """

    plan: MonitoringPlan
    currentness: Engine1CurrentnessReading

    def __post_init__(self) -> None:
        _instance(self.plan, MonitoringPlan, "ActiveMonitoringPlan.plan")
        problem = _activation_problem(self.plan, self.currentness)
        if problem is not None:
            raise MonitoringPlanActivationError(problem)

    @property
    def monitoring_plan_id(self) -> str:
        return self.plan.monitoring_plan_id

    @property
    def plan_version(self) -> int:
        return self.plan.plan_version


def activate_monitoring_plan(plan: MonitoringPlan, reading: Engine1CurrentnessReading | None) -> ActiveMonitoringPlan:
    """Activate a plan, or raise MonitoringPlanActivationError (Stage A Section 4.3)."""
    _instance(plan, MonitoringPlan, "plan")
    problem = _activation_problem(plan, reading)
    if problem is not None:
        raise MonitoringPlanActivationError(problem)
    return ActiveMonitoringPlan(plan=plan, currentness=reading)


# --- Source items and Observations (Stage A Sections 7.1, 14) ----------------


@dataclass(frozen=True)
class SourceItem:
    """A raw item presented for capture. Not evidence until it becomes an Observation."""

    item_id: str
    source_id: str | None
    source_type: str
    acquisition_method: str
    received_at: datetime
    content: str
    published_at: datetime | None = None
    effective_at: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.item_id, "SourceItem.item_id")
        _optional_text(self.source_id, "SourceItem.source_id")
        _text(self.source_type, "SourceItem.source_type")
        _text(self.acquisition_method, "SourceItem.acquisition_method")
        _aware(self.received_at, "SourceItem.received_at")
        _text(self.content, "SourceItem.content")
        _optional_aware(self.published_at, "SourceItem.published_at")
        _optional_aware(self.effective_at, "SourceItem.effective_at")


@dataclass(frozen=True)
class Observation:
    """Raw captured source evidence with provenance (Stage A Section 7.1).

    Immutable. Holds no relevance judgment and no calculation. A correction or
    retraction is a new Observation plus a Supersession, never an edit. It
    belongs to exactly one Monitoring Plan version: (monitoring_plan_id, plan_version).

    content_sha256 is verified on creation (fail closed): it must equal
    content_integrity_sha256(exact_evidence_content). It covers the stored text
    only, not any original file, email, HTTP response, or image.
    """

    observation_id: str
    monitoring_plan_id: str
    plan_version: int
    source_id: str
    source_type: str
    observed_at: datetime
    exact_evidence_content: str
    source_locator: str
    provenance: ProvenanceCategory
    content_sha256: str
    acquisition_method: str
    authorization_reference: str
    evidence_event_id: str
    published_at: datetime | None = None
    effective_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("observation_id", "monitoring_plan_id", "source_id", "source_type", "exact_evidence_content",
                     "source_locator", "acquisition_method", "authorization_reference", "evidence_event_id"):
            _text(getattr(self, name), f"Observation.{name}")
        _plan_version(self.plan_version, "Observation.plan_version")
        _aware(self.observed_at, "Observation.observed_at")
        _optional_aware(self.published_at, "Observation.published_at")
        _optional_aware(self.effective_at, "Observation.effective_at")
        _instance(self.provenance, ProvenanceCategory, "Observation.provenance")
        if self.provenance not in _OBSERVATION_PROVENANCE:
            raise Engine2ValidationError(f"an Observation cannot have provenance {self.provenance.value}")
        if not isinstance(self.content_sha256, str) or not _SHA256_HEX.fullmatch(self.content_sha256):
            raise Engine2ValidationError("Observation.content_sha256 must be 64 lowercase hexadecimal characters")
        if self.content_sha256 != content_integrity_sha256(self.exact_evidence_content):
            raise Engine2ValidationError("Observation.content_sha256 does not match the stored evidence content")
        if self.authorization_reference != self.source_id:
            raise Engine2ValidationError("Observation.authorization_reference must name its Authorized Source")


def capture_observation(
    active_plan: ActiveMonitoringPlan,
    authorized_sources: dict[str, AuthorizedSource],
    item: SourceItem,
    *,
    observation_id: str,
    provenance: ProvenanceCategory,
    content_sha256: str,
    source_locator: str,
    evidence_event_id: str,
) -> Observation:
    """Capture one item as an Observation, or refuse it before any Observation exists.

    Refusal (SourceNotAuthorizedError) happens when the item has no source, or
    its source is not authorized for this plan.
    """
    _instance(active_plan, ActiveMonitoringPlan, "active_plan")
    _instance(item, SourceItem, "item")
    source_id = item.source_id
    if source_id is None:
        raise SourceNotAuthorizedError(f"{item.item_id}: no Authorized Source")
    if source_id not in active_plan.plan.authorized_source_ids:
        raise SourceNotAuthorizedError(f"{item.item_id}: source {source_id} is not authorized for this plan")
    source = authorized_sources.get(source_id)
    if not isinstance(source, AuthorizedSource) or source.source_id != source_id:
        raise SourceNotAuthorizedError(f"{item.item_id}: source {source_id} has no authorization record")
    return Observation(
        observation_id=observation_id,
        monitoring_plan_id=active_plan.monitoring_plan_id,
        plan_version=active_plan.plan_version,
        source_id=source_id,
        source_type=item.source_type,
        observed_at=item.received_at,
        exact_evidence_content=item.content,
        source_locator=source_locator,
        provenance=provenance,
        content_sha256=content_sha256,
        acquisition_method=item.acquisition_method,
        authorization_reference=source_id,
        evidence_event_id=evidence_event_id,
        published_at=item.published_at,
        effective_at=item.effective_at,
    )


@dataclass(frozen=True)
class Supersession:
    """One Observation superseding or retracting another, on a recorded authority basis.

    Newer is never enough on its own (Stage B supersession rule).
    """

    superseded_observation_id: str
    superseding_observation_id: str
    basis: SupersessionBasis
    retraction: bool = False

    def __post_init__(self) -> None:
        _text(self.superseded_observation_id, "Supersession.superseded_observation_id")
        _text(self.superseding_observation_id, "Supersession.superseding_observation_id")
        if self.superseded_observation_id == self.superseding_observation_id:
            raise Engine2ValidationError("an Observation cannot supersede itself")
        _instance(self.basis, SupersessionBasis, "Supersession.basis")
        if not isinstance(self.retraction, bool):
            raise Engine2ValidationError("Supersession.retraction must be a bool")


@dataclass(frozen=True)
class RejectedSourceItem:
    """Audit record of an item refused during capture under one exact plan version.

    Never an Observation, never evidence, never a finding or an outcome.
    """

    item_id: str
    monitoring_plan_id: str
    plan_version: int
    reason: str

    def __post_init__(self) -> None:
        _text(self.item_id, "RejectedSourceItem.item_id")
        _text(self.monitoring_plan_id, "RejectedSourceItem.monitoring_plan_id")
        _plan_version(self.plan_version, "RejectedSourceItem.plan_version")
        _text(self.reason, "RejectedSourceItem.reason")


def reject_source_item(active_plan: ActiveMonitoringPlan, item: SourceItem, reason: str) -> RejectedSourceItem:
    """Record a capture refusal under the exact active plan version that refused it."""
    _instance(active_plan, ActiveMonitoringPlan, "active_plan")
    _instance(item, SourceItem, "item")
    return RejectedSourceItem(item.item_id, active_plan.monitoring_plan_id, active_plan.plan_version, reason)


# --- Signal and Correspondence Candidates (Stage A Sections 7.2, 8.1) --------


def plan_key(record) -> tuple[str, int]:
    """The exact (monitoring_plan_id, plan_version) a plan-scoped record belongs to."""
    return (record.monitoring_plan_id, record.plan_version)


@dataclass(frozen=True)
class SignalCandidate:
    """Observations selected as possibly relevant. Asserts no finding and nothing about the buyer.

    Scoped to one exact plan version; every Observation it holds must belong to
    that same (monitoring_plan_id, plan_version).
    """

    signal_candidate_id: str
    monitoring_plan_id: str
    plan_version: int
    observations: tuple[Observation, ...]
    selected_by: str
    possibly_relevant_dimensions: frozenset[CorrespondenceDimension] = frozenset()

    def __post_init__(self) -> None:
        _text(self.signal_candidate_id, "SignalCandidate.signal_candidate_id")
        _text(self.monitoring_plan_id, "SignalCandidate.monitoring_plan_id")
        _plan_version(self.plan_version, "SignalCandidate.plan_version")
        if not isinstance(self.observations, tuple) or not self.observations:
            raise Engine2ValidationError("SignalCandidate needs at least one Observation")
        for o in self.observations:
            _instance(o, Observation, "SignalCandidate.observations item")
            if plan_key(o) != plan_key(self):
                raise Engine2ValidationError(
                    f"{o.observation_id} belongs to {o.monitoring_plan_id} v{o.plan_version}, not "
                    f"{self.monitoring_plan_id} v{self.plan_version}; plan versions never mix")
        ids = [o.observation_id for o in self.observations]
        if len(set(ids)) != len(ids):
            raise Engine2ValidationError("SignalCandidate.observations contains duplicates")
        _text(self.selected_by, "SignalCandidate.selected_by")
        if not isinstance(self.possibly_relevant_dimensions, frozenset) or not all(
            isinstance(d, CorrespondenceDimension) for d in self.possibly_relevant_dimensions
        ):
            raise Engine2ValidationError("SignalCandidate.possibly_relevant_dimensions must be a frozenset of dimensions")

    @property
    def observation_ids(self) -> tuple[str, ...]:
        return tuple(o.observation_id for o in self.observations)


@dataclass(frozen=True)
class CorrespondenceCandidate:
    """A hypothesis that Signal Candidates may correspond to one confirmed BAEC's condition.

    Scoped to one exact plan version; every Signal Candidate must match it.
    """

    candidate_id: str
    baec_id: str
    monitoring_plan_id: str
    plan_version: int
    signal_candidates: tuple[SignalCandidate, ...]

    def __post_init__(self) -> None:
        _text(self.candidate_id, "CorrespondenceCandidate.candidate_id")
        _text(self.baec_id, "CorrespondenceCandidate.baec_id")
        _text(self.monitoring_plan_id, "CorrespondenceCandidate.monitoring_plan_id")
        _plan_version(self.plan_version, "CorrespondenceCandidate.plan_version")
        if not isinstance(self.signal_candidates, tuple) or not self.signal_candidates:
            raise Engine2ValidationError("CorrespondenceCandidate needs at least one SignalCandidate")
        for signal in self.signal_candidates:
            _instance(signal, SignalCandidate, "CorrespondenceCandidate.signal_candidates item")
            if plan_key(signal) != plan_key(self):
                raise Engine2ValidationError(
                    f"{signal.signal_candidate_id} belongs to {signal.monitoring_plan_id} v{signal.plan_version}, "
                    f"not {self.monitoring_plan_id} v{self.plan_version}; plan versions never mix")

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(o for s in self.signal_candidates for o in s.observations)

    @property
    def observation_ids(self) -> frozenset[str]:
        return frozenset(o.observation_id for o in self.observations)


__all__ = [
    "ALL_DIMENSIONS", "BAEC_REVALIDATION_REQUIRED", "ActiveMonitoringPlan", "AuthorizedSource",
    "CorrespondenceCandidate", "CorrespondenceDimension", "CorrespondenceOutcome", "CrossCuttingCheckName",
    "DimensionFinding", "Engine1CurrentnessReading", "MonitoringPlan", "Observation", "RejectedSourceItem",
    "SignalCandidate", "SourceItem", "SufficiencyFinding", "Supersession", "SupersessionBasis", "ThresholdSpec",
    "activate_monitoring_plan", "capture_observation", "content_integrity_sha256", "plan_key", "reject_source_item",
]
