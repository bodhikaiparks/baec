"""Engine 2 adapter: monitoring and correspondence review through the released Engine 2 code.

* Monitoring starts only from the Stage K read-only handoff of a CONFIRMED Engine 1 BAEC
  (read_confirmed_baec over a query-only Engine 1 connection). The BAEC facts in the plan come only from the
  handoff; the salesperson supplies only the monitoring choices (PlanChoices). Activation is decided by the
  locked Engine 2 rule against Engine 1's stored currentness, and a refusal is recorded by Engine 2 itself.
* Observations are captured with capture_observation under an Authorized Source and recorded with the Stage D
  writers. Derived Measurements use the closed Engine 2 transformations with exact Decimal arithmetic.
* A salesperson flags an Observation for correspondence review; that creates an Engine 2 Signal Candidate and
  Correspondence Candidate selected by that person. Nothing here judges relevance.
* The review is recorded with submit_human_review. The human supplies every finding; the locked classifier
  derives the outcome. No function here accepts or computes an outcome.

The only SQL here enumerates Engine 2 identifiers so records can be loaded by the Stage D loaders.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from baec_app.domain.enums import ProvenanceCategory, ThresholdComparator
from baec_app.engine2 import persistence as store
from baec_app.engine2 import review as review_app
from baec_app.engine2.classification import CrossCuttingCheck, DimensionAssessment
from baec_app.engine2.domain import (
    ALL_DIMENSIONS,
    AuthorizedSource,
    CorrespondenceCandidate,
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    SignalCandidate,
    SourceItem,
    SufficiencyFinding,
    Supersession,
    SupersessionBasis,
    capture_observation,
    content_integrity_sha256,
)
from baec_app.engine2.integration.engine1_handoff import (
    ConfirmedBaecHandoff,
    PlanChoices,
    currentness_reading,
    monitoring_plan_from_handoff,
    read_confirmed_baec,
)
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement

D = CorrespondenceDimension

DIMENSION_QUESTIONS = {
    D.ENTITY_MATCH: "Does the evidence concern the same organizations the buyer was talking about?",
    D.RELATIONSHIP_MATCH: "Does it concern the same relationship or contract?",
    D.SOURCE_RELEVANCE: "Is the source relevant and authorized for this condition?",
    D.EVENT_OR_CONDITION_TYPE: "Is it the same kind of event or change the buyer described?",
    D.THRESHOLD_MATCH: "Does the evidence meet the buyer's threshold exactly as stated?",
    D.COMPARATOR_MATCH: "Does the comparison follow the buyer's wording (for example 'more than')?",
    D.NUMERIC_MAGNITUDE: "Does the evidence establish the exact magnitude?",
    D.UNIT_MATCH: "Is it in the same unit the buyer used?",
    D.TIMING_MATCH: "Does it fall within the buyer's stated timing?",
    D.RENEWAL_OR_EFFECTIVE_DATE_MATCH: "Does the renewal or effective date match what the buyer described?",
    D.CAUSAL_OR_CONTEXTUAL_RELEVANCE: "Is the context relevant to why the buyer said it mattered?",
}
FINDING_LABELS = {
    DimensionFinding.SUPPORTED: "Supported",
    DimensionFinding.CONTRADICTED: "Contradicted",
    DimensionFinding.UNRESOLVED: "Unresolved",
    DimensionFinding.NOT_APPLICABLE: "Not applicable",
}
OUTCOME_LABELS = {
    CorrespondenceOutcome.NO_CORRESPONDENCE: "No correspondence",
    CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE: "Possible correspondence (not verified)",
    CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE: "Human-verified correspondence",
}
PROVENANCE_LABELS = {
    ProvenanceCategory.EXTERNAL_EVIDENCE: "External evidence",
    ProvenanceCategory.BUYER_FACT: "Buyer-provided",
    ProvenanceCategory.SELLER_OBSERVATION: "Seller observation",
}
NOT_FLAGGED = "Not flagged by the reviewer."
TRANSFORMATION_LABELS = {
    Transformation.PERCENT_CHANGE_FROM_TWO_PRICES: "Percent change from two prices",
    Transformation.BASIS_POINTS_TO_PERCENT: "Basis points to percent",
}


class MonitoringError(ValueError):
    """The monitoring action was refused. The message says why; nothing partial was written by the console."""


def default_required_dimensions(handoff: ConfirmedBaecHandoff) -> frozenset[CorrespondenceDimension]:
    """A starting selection for the salesperson to adjust. The plan records whatever they choose."""
    dims = {D.ENTITY_MATCH, D.SOURCE_RELEVANCE, D.EVENT_OR_CONDITION_TYPE}
    stringency = handoff.stringency
    if stringency is not None and stringency.numeric_value is not None:
        dims |= {D.THRESHOLD_MATCH, D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH}
    if stringency is not None and stringency.timing_text:
        dims.add(D.RENEWAL_OR_EFFECTIVE_DATE_MATCH if "renewal" in stringency.timing_text.lower() else D.TIMING_MATCH)
    return frozenset(dims)


def read_handoff(engine1_reader, baec_id: str) -> ConfirmedBaecHandoff:
    """Stage K read of one confirmed BAEC through a query-only Engine 1 connection. HandoffRefused propagates.

    engine1_reader() opens that connection (the storage backend's engine1_reader: open_read_connection on SQLite, a
    database-enforced read-only block on PostgreSQL)."""
    with engine1_reader() as connection:
        return read_confirmed_baec(connection, baec_id)


@dataclass(frozen=True)
class SourceForm:
    source_type: str
    owner_or_publisher: str
    legitimate_basis: str
    access_method: str
    permitted_capture: str
    known_limitations: str = ""


@dataclass(frozen=True)
class PlanRefs:
    monitoring_plan_id: str
    plan_version: int
    activation_id: str | None
    activation_result: str | None


def start_monitoring(e2: sqlite3.Connection, handoff: ConfirmedBaecHandoff, *, ids, target_entities: tuple[str, ...],
                     required: frozenset[CorrespondenceDimension], sources: tuple[SourceForm, ...], actor: str,
                     now: datetime) -> PlanRefs:
    """Record the human-authorized plan and attempt activation. Activation refusals are recorded by Engine 2."""
    if not sources:
        raise MonitoringError("authorize at least one source before monitoring")
    authorized = []
    for form in sources:
        authorized.append(AuthorizedSource(
            source_id=ids("SRC"), source_type=form.source_type, owner_or_publisher=form.owner_or_publisher,
            legitimate_basis=form.legitimate_basis, access_method=form.access_method,
            permitted_capture=form.permitted_capture, authorized_by=actor, authorized_at=now,
            known_limitations=form.known_limitations.strip() or None))
    choices = PlanChoices(
        monitoring_plan_id=ids("MP"), plan_version=1, target_entities=target_entities,
        required_dimensions=frozenset(required), not_applicable_dimensions=ALL_DIMENSIONS - frozenset(required),
        authorized_source_ids=frozenset(s.source_id for s in authorized), authorized_by=actor,
        authorization_reference=ids("PLAN-AUTH"), authorized_at=now)
    plan = monitoring_plan_from_handoff(handoff, choices)  # validates the plan before anything is written
    reading = currentness_reading(plan, handoff)
    for source in authorized:
        store.record_authorized_source(e2, source, recorded_at=now, recorded_by=actor)
    store.record_monitoring_plan(e2, plan, recorded_at=now, recorded_by=actor)
    activation_id = ids("ACT")
    try:
        store.record_plan_activation(e2, plan.monitoring_plan_id, plan.plan_version, reading,
                                     activation_id=activation_id, recorded_at=now, recorded_by=actor)
    except store.MonitoringPlanActivationError:
        return PlanRefs(plan.monitoring_plan_id, plan.plan_version, activation_id, "REFUSED")
    return PlanRefs(plan.monitoring_plan_id, plan.plan_version, activation_id, "ACTIVATED")


# --- reads ------------------------------------------------------------------------------------------------------


def plans_for_baec(e2: sqlite3.Connection, baec_id: str) -> list[PlanRefs]:
    rows = e2.execute("SELECT monitoring_plan_id, plan_version FROM monitoring_plans WHERE baec_id = ? ORDER BY rowid",
                      (baec_id,)).fetchall()
    out = []
    for plan_id, version in rows:
        activation = e2.execute("SELECT activation_id, result FROM plan_activations WHERE monitoring_plan_id = ? AND "
                                "plan_version = ? ORDER BY rowid DESC LIMIT 1", (plan_id, version)).fetchone()
        out.append(PlanRefs(plan_id, version, activation[0] if activation else None,
                            activation[1] if activation else None))
    return out


def _ids(e2, sql: str, args: tuple) -> list[str]:
    return [r[0] for r in e2.execute(sql, args).fetchall()]


def observation_ids(e2, plan: PlanRefs) -> list[str]:
    return _ids(e2, "SELECT observation_id FROM observations WHERE monitoring_plan_id = ? AND plan_version = ? "
                    "ORDER BY rowid", (plan.monitoring_plan_id, plan.plan_version))


def measurement_ids(e2, plan: PlanRefs) -> list[str]:
    return _ids(e2, "SELECT measurement_id FROM derived_measurements WHERE monitoring_plan_id = ? AND plan_version = ? "
                    "ORDER BY rowid", (plan.monitoring_plan_id, plan.plan_version))


def candidate_ids(e2, plan: PlanRefs) -> list[str]:
    return _ids(e2, "SELECT candidate_id FROM correspondence_candidates WHERE monitoring_plan_id = ? AND "
                    "plan_version = ? ORDER BY rowid", (plan.monitoring_plan_id, plan.plan_version))


def signal_ids(e2, plan: PlanRefs) -> list[str]:
    return _ids(e2, "SELECT signal_candidate_id FROM signal_candidates WHERE monitoring_plan_id = ? AND "
                    "plan_version = ? ORDER BY rowid", (plan.monitoring_plan_id, plan.plan_version))


def superseded_ids(e2, plan: PlanRefs) -> list[str]:
    return _ids(e2, "SELECT superseded_observation_id FROM supersessions WHERE monitoring_plan_id = ? AND "
                    "plan_version = ? ORDER BY rowid", (plan.monitoring_plan_id, plan.plan_version))


def entity_ids(e2, plan: PlanRefs) -> set[str]:
    """Every Engine 2 record identity that belongs to one plan, for mapping audit events to an account."""
    loaded = store.load_monitoring_plan(e2, plan.monitoring_plan_id, plan.plan_version)
    found = {f"{plan.monitoring_plan_id}@v{plan.plan_version}", *loaded.authorized_source_ids}
    found |= set(_ids(e2, "SELECT activation_id FROM plan_activations WHERE monitoring_plan_id = ? AND plan_version = ?",
                      (plan.monitoring_plan_id, plan.plan_version)))
    found |= set(observation_ids(e2, plan)) | set(measurement_ids(e2, plan)) | set(candidate_ids(e2, plan))
    found |= set(signal_ids(e2, plan)) | set(superseded_ids(e2, plan))
    for cid in candidate_ids(e2, plan):
        found |= set(store.review_ids_for_candidate(e2, cid))
    found |= set(_ids(e2, "SELECT item_id FROM source_refusals WHERE monitoring_plan_id = ? AND plan_version = ?",
                      (plan.monitoring_plan_id, plan.plan_version)))
    return found


def candidate_plan(e2, candidate_id: str) -> PlanRefs:
    candidate = store.load_correspondence_candidate(e2, candidate_id)
    for plan in plans_for_baec(e2, candidate.baec_id):
        if (plan.monitoring_plan_id, plan.plan_version) == (candidate.monitoring_plan_id, candidate.plan_version):
            return plan
    raise MonitoringError(f"no plan for candidate {candidate_id}")


def packet(e2, candidate_id: str) -> review_app.ReviewPacket:
    candidate = store.load_correspondence_candidate(e2, candidate_id)
    plan = candidate_plan(e2, candidate_id)
    return review_app.build_review_packet(
        e2, monitoring_plan_id=plan.monitoring_plan_id, plan_version=plan.plan_version, baec_id=candidate.baec_id,
        candidate_id=candidate_id, activation_id=plan.activation_id)


# --- observations ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationForm:
    source_id: str
    observed_at: datetime
    content: str
    provenance: str = ProvenanceCategory.EXTERNAL_EVIDENCE.value
    effective_at: datetime | None = None
    source_locator: str = ""
    corrects_observation_id: str = ""
    is_retraction: bool = False
    transformation: str = ""  # Transformation value, or "" for no measurement
    baseline_value: str = ""
    new_value: str = ""
    value_unit: str = ""
    flag_for_review: bool = True


@dataclass(frozen=True)
class ObservationResult:
    observation_id: str
    measurement_id: str | None
    candidate_id: str | None


def queue_observation_for_review(
    e2, plan: PlanRefs, observation_id: str, *, ids, actor: str, now: datetime
) -> str:
    """Queue an already-recorded current observation for human correspondence review."""
    if plan.activation_result != "ACTIVATED" or not plan.activation_id:
        raise MonitoringError("monitoring is not active for this condition")

    active = store.load_active_plan(e2, plan.activation_id)
    observation = store.load_observation(e2, observation_id)

    if (observation.monitoring_plan_id, observation.plan_version) != (
        plan.monitoring_plan_id,
        plan.plan_version,
    ):
        raise MonitoringError("the observation belongs to a different monitoring plan")

    superseded = e2.execute(
        "SELECT 1 FROM supersessions WHERE superseded_observation_id = ? LIMIT 1",
        (observation_id,),
    ).fetchone()
    if superseded:
        raise MonitoringError("a superseded observation cannot be newly queued for review")

    existing = e2.execute(
        """
        SELECT cc.candidate_id
        FROM signal_candidate_observations sco
        JOIN candidate_signals cs
          ON cs.signal_candidate_id = sco.signal_candidate_id
        JOIN correspondence_candidates cc
          ON cc.candidate_id = cs.candidate_id
        WHERE sco.observation_id = ?
        LIMIT 1
        """,
        (observation_id,),
    ).fetchone()

    if existing:
        raise MonitoringError("this observation is already queued for correspondence review")

    signal = SignalCandidate(
        ids("SC"),
        plan.monitoring_plan_id,
        plan.plan_version,
        (observation,),
        actor,
    )
    store.record_signal_candidate(
        e2, signal, recorded_at=now, recorded_by=actor
    )

    candidate = CorrespondenceCandidate(
        ids("CC"),
        active.plan.baec_id,
        plan.monitoring_plan_id,
        plan.plan_version,
        (signal,),
    )
    store.record_correspondence_candidate(
        e2, candidate, recorded_at=now, recorded_by=actor
    )

    return candidate.candidate_id


def add_observation(e2, plan: PlanRefs, form: ObservationForm, *, ids, actor: str, now: datetime) -> ObservationResult:
    """Capture and record one Observation, then optional supersession, measurement, and review flag."""
    if plan.activation_result != "ACTIVATED":
        raise MonitoringError("monitoring is not active for this condition; nothing can be captured")
    active = store.load_active_plan(e2, plan.activation_id)
    sources = store.load_authorized_sources(e2)
    if not form.content or not form.content.strip():
        raise MonitoringError("record the exact observation text")
    source = sources.get(form.source_id)
    if source is None:
        raise MonitoringError("choose one of the sources authorized for this monitoring plan")
    item_id = ids("ITEM")
    item = SourceItem(item_id, form.source_id, source.source_type, "manual entry in BAEC Console", form.observed_at,
                      form.content, published_at=form.observed_at, effective_at=form.effective_at)
    observation = capture_observation(
        active, sources, item, observation_id=ids("OBS"), provenance=ProvenanceCategory(form.provenance),
        content_sha256=content_integrity_sha256(form.content),
        source_locator=form.source_locator.strip() or f"{form.source_id}/{item_id}", evidence_event_id=ids("EVENT"))
    measurement = None
    if form.transformation:
        transformation = Transformation(form.transformation)
        unit = form.value_unit.strip()
        if transformation is Transformation.PERCENT_CHANGE_FROM_TWO_PRICES:
            inputs = (MeasurementInput(observation, "baseline_price", _number(form.baseline_value), unit),
                      MeasurementInput(observation, "new_price", _number(form.new_value), unit))
        else:
            inputs = (MeasurementInput(observation, "basis_points", _number(form.new_value), "basis points"),)
        measurement = compute_derived_measurement(ids("DM"), transformation, inputs, now)  # validated before writing
    supersession = None
    if form.corrects_observation_id:
        supersession = Supersession(form.corrects_observation_id, observation.observation_id,
                                    SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION, form.is_retraction)
    store.record_observation(e2, observation, activation_id=plan.activation_id, recorded_at=now, recorded_by=actor)
    if supersession is not None:
        store.record_supersession(e2, supersession, recorded_at=now, recorded_by=actor)
    if measurement is not None:
        store.record_derived_measurement(e2, measurement, recorded_at=now, recorded_by=actor)
    candidate_id = None
    if form.flag_for_review:
        signal = SignalCandidate(ids("SC"), plan.monitoring_plan_id, plan.plan_version, (observation,), actor)
        store.record_signal_candidate(e2, signal, recorded_at=now, recorded_by=actor)
        candidate = CorrespondenceCandidate(ids("CC"), active.plan.baec_id, plan.monitoring_plan_id, plan.plan_version,
                                            (signal,))
        store.record_correspondence_candidate(e2, candidate, recorded_at=now, recorded_by=actor)
        candidate_id = candidate.candidate_id
    return ObservationResult(observation.observation_id, measurement.measurement_id if measurement else None,
                             candidate_id)


def _number(text: str) -> Decimal:
    try:
        value = Decimal(text.strip().replace(",", ""))
    except (InvalidOperation, AttributeError) as error:
        raise MonitoringError(f"{text!r} is not a number; enter the exact value from the evidence") from error
    if not value.is_finite():
        raise MonitoringError(f"{text!r} is not a finite number")
    return value


def threshold_arithmetic(threshold, value: Decimal) -> bool | None:
    """Display-only arithmetic: does value satisfy the buyer's comparator? Never a finding; the human decides."""
    if threshold is None:
        return None
    c, t = threshold.comparator, threshold.value
    return {ThresholdComparator.GREATER_THAN: value > t, ThresholdComparator.AT_LEAST: value >= t,
            ThresholdComparator.LESS_THAN: value < t, ThresholdComparator.AT_MOST: value <= t,
            ThresholdComparator.EXACTLY: value == t}.get(c)


# --- human correspondence review ---------------------------------------------------------------------------------


def candidate_evidence_refs(p: review_app.ReviewPacket) -> tuple[str, ...]:
    """Usable Observations of the candidate plus usable Measurements derived only from them, in ledger order."""
    mine = p.candidate.observation_ids
    obs = tuple(o.observation_id for o in p.ledger.observations
                if o.observation_id in mine and o.observation_id in p.usable_observation_ids)
    meas = tuple(m.measurement_id for m in p.ledger.measurements
                 if m.source_observation_ids <= mine and m.measurement_id in p.usable_measurement_ids)
    return obs + meas


def submit_review(e2, candidate_id: str, *, displayed_context_digest: str, findings: dict[CorrespondenceDimension, str],
                  notes: dict[CorrespondenceDimension, str], sufficiency: str, revalidation_triggers: tuple[str, ...],
                  check_notes: dict[CrossCuttingCheckName, str], ids, actor: str, now: datetime) -> review_app.RecordedReview:
    """Record the human's review. Every required dimension needs an explicit human finding; the outcome is derived."""
    p = packet(e2, candidate_id)
    refs = candidate_evidence_refs(p)
    plan = p.plan
    assessments = []
    for dim in sorted(ALL_DIMENSIONS, key=lambda d: list(D).index(d)):
        if dim not in plan.required_dimensions:
            assessments.append(DimensionAssessment(dim, DimensionFinding.NOT_APPLICABLE, (),
                                                   "Not required by the monitoring plan."))
            continue
        chosen = findings.get(dim)
        if chosen is None:
            raise MonitoringError(f"answer every question before recording the review ({dim.label})")
        finding = DimensionFinding(chosen)
        if finding is DimensionFinding.NOT_APPLICABLE:
            raise MonitoringError(f"{dim.label} is required by the plan and cannot be not applicable")
        reason = f'Reviewer answered "{FINDING_LABELS[finding]}" to: {DIMENSION_QUESTIONS[dim]}'
        note = (notes.get(dim) or "").strip()
        if note:
            reason += f" Note: {note}"
        assessments.append(DimensionAssessment(dim, finding, refs, reason))
    checks = tuple(CrossCuttingCheck(name, (check_notes.get(name) or "").strip() or NOT_FLAGGED)
                   for name in CrossCuttingCheckName)
    triggers = tuple(t.strip() for t in revalidation_triggers if t and t.strip())
    return review_app.submit_human_review(
        e2, monitoring_plan_id=plan.monitoring_plan_id, plan_version=plan.plan_version, baec_id=plan.baec_id,
        candidate_id=candidate_id, activation_id=p.activation_id, context_digest=displayed_context_digest,
        review_id=ids("HCR"), findings=tuple(assessments), checks=checks, sufficiency=SufficiencyFinding(sufficiency),
        reviewer=actor, reviewed_at=now, baec_revalidation_required=bool(triggers), revalidation_triggers=triggers,
        recorded_at=now, recorded_by=actor)
