"""Human Correspondence Review and the deterministic outcome rule (Stage A Sections 8 to 11).

The findings for the 11 correspondence dimensions are human decisions supplied
as inputs. This module does not read source text and does not infer any
finding. It validates that a review is internally consistent, refusing it
otherwise, and derives the outcome. There is no outcome field and no override:
a review's outcome is always computed by classify_correspondence().

The 4 cross-cutting checks are recorded beside the findings. They take no
finding value and never enter the outcome rule. Each constrains only the
dimensions it explicitly names, and only in one direction:

* Missing evidence or Provenance quality names a dimension: it must be UNRESOLVED.
* Contradictory evidence names a dimension: it stays UNRESOLVED unless its
  finding cites an authoritative resolution (a Monitoring Plan rule, or an
  Observation that supersedes another on a recorded basis). Newer alone is not one.
* Freshness names a dimension: a decided finding must rest on evidence that is
  currently usable for that proposition, never only on stale evidence the check
  cites. Stale means superseded, retracted, or otherwise non-authoritative, not
  old: a past-dated source can still prove a historical fact, for example that an
  event fell outside the buyer's timing window (Timing match CONTRADICTED).
  Stale evidence stays in the ledger as audit history.

An UNRESOLVED dimension need not appear in any check: it may be unresolved for
ambiguity, timing, or another reason. Stricter Stage B corpus conventions are
enforced by the corpus conformance tests, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .domain import (
    ALL_DIMENSIONS,
    ActiveMonitoringPlan,
    CorrespondenceCandidate,
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    Observation,
    RejectedSourceItem,
    SufficiencyFinding,
    Supersession,
    plan_key,
)
from .errors import Engine2ValidationError, ReviewInconsistentError
from .measurement import DerivedMeasurement

D = CorrespondenceDimension
_DECIDED = frozenset({DimensionFinding.SUPPORTED, DimensionFinding.CONTRADICTED})
_FINDING_WORDS = frozenset(f.value for f in DimensionFinding)
# Threshold match is the result of applying the comparator to an exact magnitude
# in the buyer's unit. Where the plan requires any of these dimensions, a decided
# Threshold match needs each required one SUPPORTED. Where the plan marks one
# NOT_APPLICABLE, nothing is imposed for it.
_THRESHOLD_PREREQUISITES = (D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH)


def _text(value: object, field: str, error: type[Exception] = Engine2ValidationError) -> None:
    if not isinstance(value, str) or not value.strip():
        raise error(f"{field} must be a non-empty string")


def _refs(value: object, field: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(r, str) and r.strip() for r in value):
        raise Engine2ValidationError(f"{field} must be a tuple of non-empty strings")
    if len(set(value)) != len(value):
        raise Engine2ValidationError(f"{field} contains duplicates")


# --- evidence ledger -----------------------------------------------------------


@dataclass(frozen=True)
class EvidenceLedger:
    """Everything captured under one active plan, with its supersession history.

    Records are never edited. An Observation is usable for current findings
    unless a Supersession supersedes or retracts it; a Derived Measurement is
    usable only while every input Observation is usable.
    """

    active_plan: ActiveMonitoringPlan
    observations: tuple[Observation, ...]
    supersessions: tuple[Supersession, ...] = ()
    measurements: tuple[DerivedMeasurement, ...] = ()
    rejected_items: tuple[RejectedSourceItem, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.active_plan, ActiveMonitoringPlan):
            raise Engine2ValidationError("EvidenceLedger requires an ActiveMonitoringPlan")
        plan = self.active_plan.plan
        for name, kind in (("observations", Observation), ("supersessions", Supersession),
                           ("measurements", DerivedMeasurement), ("rejected_items", RejectedSourceItem)):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(isinstance(v, kind) for v in value):
                raise Engine2ValidationError(f"EvidenceLedger.{name} must be a tuple of {kind.__name__}")
        ids = [o.observation_id for o in self.observations]
        if len(set(ids)) != len(ids):
            raise Engine2ValidationError("duplicate observation_id in ledger")
        for o in self.observations:
            if plan_key(o) != plan_key(plan):
                raise Engine2ValidationError(
                    f"{o.observation_id} belongs to {o.monitoring_plan_id} v{o.plan_version}, not "
                    f"{plan.monitoring_plan_id} v{plan.plan_version}; plan versions never mix")
            if o.source_id not in plan.authorized_source_ids:
                raise Engine2ValidationError(f"{o.observation_id} is from a source not authorized for this plan")
        known = set(ids)
        superseded = [s.superseded_observation_id for s in self.supersessions]
        if len(set(superseded)) != len(superseded):
            raise Engine2ValidationError("an Observation is superseded more than once")
        for s in self.supersessions:
            if s.superseded_observation_id not in known or s.superseding_observation_id not in known:
                raise Engine2ValidationError("a Supersession references an Observation not in the ledger")
            # Both sides are ledger Observations, which share the ledger's exact plan version,
            # so a Supersession can never cross versions: a plan revision is not a supersession.
        nxt = {s.superseded_observation_id: s.superseding_observation_id for s in self.supersessions}
        for start in nxt:
            seen, node = set(), start
            while node in nxt:
                if node in seen:
                    raise Engine2ValidationError("supersession cycle")
                seen.add(node)
                node = nxt[node]
        mids = [m.measurement_id for m in self.measurements]
        if len(set(mids)) != len(mids) or known & set(mids):
            raise Engine2ValidationError("measurement ids must be unique and distinct from observation ids")
        by_id = {o.observation_id: o for o in self.observations}
        for m in self.measurements:
            if plan_key(m) != plan_key(plan):
                raise Engine2ValidationError(f"{m.measurement_id} belongs to another plan version; plan versions never mix")
            if any(by_id.get(i.observation_id) != i.observation for i in m.inputs):
                raise Engine2ValidationError(f"{m.measurement_id} uses an Observation not in the ledger")
        rids = [r.item_id for r in self.rejected_items]
        if len(set(rids)) != len(rids):
            raise Engine2ValidationError("duplicate rejected item id")
        for r in self.rejected_items:
            if plan_key(r) != plan_key(plan):
                raise Engine2ValidationError(
                    f"refusal {r.item_id} belongs to {r.monitoring_plan_id} v{r.plan_version}, not "
                    f"{plan.monitoring_plan_id} v{plan.plan_version}; plan versions never mix")

    @property
    def plan(self):
        return self.active_plan.plan

    @property
    def observation_ids(self) -> frozenset[str]:
        return frozenset(o.observation_id for o in self.observations)

    @property
    def usable_observation_ids(self) -> frozenset[str]:
        superseded = {s.superseded_observation_id for s in self.supersessions}
        return self.observation_ids - superseded

    @property
    def measurement_ids(self) -> frozenset[str]:
        return frozenset(m.measurement_id for m in self.measurements)

    @property
    def usable_measurement_ids(self) -> frozenset[str]:
        usable = self.usable_observation_ids
        return frozenset(m.measurement_id for m in self.measurements if m.source_observation_ids <= usable)

    def evidence_events(self, refs) -> frozenset[str]:
        """The distinct underlying evidence events behind Observation references."""
        by_id = {o.observation_id: o for o in self.observations}
        return frozenset(by_id[r].evidence_event_id for r in refs if r in by_id)


# --- findings and checks ------------------------------------------------------


@dataclass(frozen=True)
class DimensionAssessment:
    """One human finding on one correspondence dimension, with its evidence and reason."""

    dimension: CorrespondenceDimension
    finding: DimensionFinding
    evidence_refs: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, CorrespondenceDimension):
            raise Engine2ValidationError("DimensionAssessment.dimension must be a CorrespondenceDimension")
        if not isinstance(self.finding, DimensionFinding):
            raise Engine2ValidationError("DimensionAssessment.finding must be a DimensionFinding")
        _refs(self.evidence_refs, "DimensionAssessment.evidence_refs")
        _text(self.reason, "DimensionAssessment.reason")


@dataclass(frozen=True)
class CrossCuttingCheck:
    """One of the four Stage A cross-cutting checks. Structured; never a finding."""

    check: CrossCuttingCheckName
    note: str
    evidence_refs: tuple[str, ...] = ()
    affected_dimensions: frozenset[CorrespondenceDimension] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.check, CrossCuttingCheckName):
            raise Engine2ValidationError("CrossCuttingCheck.check must be a CrossCuttingCheckName")
        _text(self.note, "CrossCuttingCheck.note")
        if self.note.strip() in _FINDING_WORDS:
            raise Engine2ValidationError("a cross-cutting check takes no finding value")
        _refs(self.evidence_refs, "CrossCuttingCheck.evidence_refs")
        if not isinstance(self.affected_dimensions, frozenset) or not all(
            isinstance(d, CorrespondenceDimension) for d in self.affected_dimensions
        ):
            raise Engine2ValidationError("CrossCuttingCheck.affected_dimensions must be a frozenset of dimensions")


# --- Human Correspondence Review ---------------------------------------------


@dataclass(frozen=True)
class HumanCorrespondenceReview:
    """A human's findings on one Correspondence Candidate (Stage A Section 10).

    It is not BAEC revalidation and not action authorization. Its outcome is
    derived, never stored: see the outcome property.
    """

    review_id: str
    candidate: CorrespondenceCandidate
    ledger: EvidenceLedger
    findings: tuple[DimensionAssessment, ...]
    checks: tuple[CrossCuttingCheck, ...]
    sufficiency: SufficiencyFinding
    reviewer: str
    reviewed_at: datetime
    baec_revalidation_required: bool
    revalidation_triggers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.review_id, "review_id", ReviewInconsistentError)
        _text(self.reviewer, "reviewer", ReviewInconsistentError)
        if not isinstance(self.reviewed_at, datetime) or self.reviewed_at.utcoffset() is None:
            raise ReviewInconsistentError("reviewed_at must be timezone-aware")
        if not isinstance(self.candidate, CorrespondenceCandidate) or not isinstance(self.ledger, EvidenceLedger):
            raise ReviewInconsistentError("a review needs a CorrespondenceCandidate and an EvidenceLedger")
        plan = self.ledger.plan
        if plan_key(self.candidate) != plan_key(plan) or self.candidate.baec_id != plan.baec_id:
            raise ReviewInconsistentError("candidate does not belong to the ledger's exact plan version and BAEC")
        ledger_obs = {o.observation_id: o for o in self.ledger.observations}
        if any(ledger_obs.get(o.observation_id) != o for o in self.candidate.observations):
            raise ReviewInconsistentError("candidate references Observations outside the ledger")
        if not isinstance(self.sufficiency, SufficiencyFinding):
            raise ReviewInconsistentError("sufficiency must be YES, NO, or UNKNOWN")
        if not isinstance(self.baec_revalidation_required, bool):
            raise ReviewInconsistentError("baec_revalidation_required must be a bool")
        _refs(self.revalidation_triggers, "revalidation_triggers")
        if self.baec_revalidation_required != bool(self.revalidation_triggers):
            raise ReviewInconsistentError("BAEC_REVALIDATION_REQUIRED needs triggers, and triggers need the flag")
        self._check_findings(plan)
        self._check_checks(plan)

    def _check_findings(self, plan) -> None:
        if not isinstance(self.findings, tuple) or not all(isinstance(f, DimensionAssessment) for f in self.findings):
            raise ReviewInconsistentError("findings must be a tuple of DimensionAssessment")
        dims = [f.dimension for f in self.findings]
        if len(dims) != len(set(dims)) or set(dims) != ALL_DIMENSIONS:
            raise ReviewInconsistentError("a review records exactly one finding for each of the 11 dimensions")
        usable = self.ledger.usable_observation_ids | self.ledger.usable_measurement_ids
        for f in self.findings:
            required = f.dimension in plan.required_dimensions
            if f.finding is DimensionFinding.NOT_APPLICABLE and required:
                raise ReviewInconsistentError(f"{f.dimension.label} is required by the plan and cannot be NOT_APPLICABLE")
            if not required and f.finding is not DimensionFinding.NOT_APPLICABLE:
                raise ReviewInconsistentError(f"{f.dimension.label} is not required by the plan and must be NOT_APPLICABLE")
            for ref in f.evidence_refs:
                if ref in usable or ref in plan.reference_ids:
                    continue
                if ref in self.ledger.observation_ids or ref in self.ledger.measurement_ids:
                    raise ReviewInconsistentError(f"{f.dimension.label} cites superseded or invalidated evidence {ref}")
                raise ReviewInconsistentError(f"{f.dimension.label} cites unknown evidence {ref}")
            if f.finding in _DECIDED and not any(r in usable for r in f.evidence_refs):
                raise ReviewInconsistentError(
                    f"{f.dimension.label} is {f.finding.value} without an accepted, provenanced Observation or Measurement"
                )
        by_dim = self.finding_map
        if by_dim[D.THRESHOLD_MATCH] in _DECIDED:
            for prerequisite in _THRESHOLD_PREREQUISITES:
                if prerequisite in plan.required_dimensions and by_dim[prerequisite] is not DimensionFinding.SUPPORTED:
                    raise ReviewInconsistentError(
                        f"Threshold match is decided but {prerequisite.label} is {by_dim[prerequisite].value}"
                    )

    def _check_checks(self, plan) -> None:
        if not isinstance(self.checks, tuple) or not all(isinstance(c, CrossCuttingCheck) for c in self.checks):
            raise ReviewInconsistentError("checks must be a tuple of CrossCuttingCheck")
        names = [c.check for c in self.checks]
        if len(names) != len(set(names)) or set(names) != set(CrossCuttingCheckName):
            raise ReviewInconsistentError("a review records each of the 4 cross-cutting checks exactly once")
        known = (self.ledger.observation_ids | self.ledger.measurement_ids | plan.reference_ids
                 | {r.item_id for r in self.ledger.rejected_items})
        for c in self.checks:
            for ref in c.evidence_refs:
                if ref not in known:
                    raise ReviewInconsistentError(f"{c.check.label} check cites unknown evidence {ref}")
        by_dim = self.finding_map
        refs = {f.dimension: f.evidence_refs for f in self.findings}
        checks = {c.check: c for c in self.checks}
        for name in (CrossCuttingCheckName.MISSING_EVIDENCE, CrossCuttingCheckName.PROVENANCE_QUALITY):
            for d in checks[name].affected_dimensions:
                if by_dim[d] is not DimensionFinding.UNRESOLVED:
                    raise ReviewInconsistentError(f"{d.label} is limited by the {name.label} check but not UNRESOLVED")
        resolving = plan.plan_rule_ids | {s.superseding_observation_id for s in self.ledger.supersessions}
        for d in checks[CrossCuttingCheckName.CONTRADICTORY_EVIDENCE].affected_dimensions:
            if by_dim[d] in _DECIDED and not any(r in resolving for r in refs[d]):
                raise ReviewInconsistentError(
                    f"{d.label} is in an evidence conflict but decided without an authoritative resolution basis"
                )
        usable = self.ledger.usable_observation_ids | self.ledger.usable_measurement_ids
        freshness = checks[CrossCuttingCheckName.FRESHNESS]
        stale = {r for r in freshness.evidence_refs if r not in usable}
        for d in freshness.affected_dimensions:
            if by_dim[d] in _DECIDED and not any(r in usable and r not in stale for r in refs[d]):
                raise ReviewInconsistentError(f"{d.label} is decided only on evidence the Freshness check marks stale")

    @property
    def finding_map(self) -> dict[CorrespondenceDimension, DimensionFinding]:
        return {f.dimension: f.finding for f in self.findings}

    @property
    def outcome(self) -> CorrespondenceOutcome:
        return classify_correspondence(self)


def classify_correspondence(review: HumanCorrespondenceReview) -> CorrespondenceOutcome:
    """The Stage A Section 11.2 rule, applied to the 11 dimensions only.

    1. Any required dimension CONTRADICTED: NO_CORRESPONDENCE.
    2. Every required dimension SUPPORTED, sufficiency YES, and no
       BAEC_REVALIDATION_REQUIRED: HUMAN_VERIFIED_CORRESPONDENCE.
    3. Otherwise: POSSIBLE_CORRESPONDENCE.

    Evidence and provenance invariants were enforced when the review was created.
    """
    if not isinstance(review, HumanCorrespondenceReview):
        raise Engine2ValidationError("classify_correspondence requires a HumanCorrespondenceReview")
    required = review.ledger.plan.required_dimensions
    findings = review.finding_map
    if any(findings[d] is DimensionFinding.CONTRADICTED for d in required):
        return CorrespondenceOutcome.NO_CORRESPONDENCE
    if (
        all(findings[d] is DimensionFinding.SUPPORTED for d in required)
        and review.sufficiency is SufficiencyFinding.YES
        and not review.baec_revalidation_required
    ):
        return CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE
    return CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE
