"""Test-only adapter: locked Stage B corpus inputs -> Stage C domain objects.

It reads only the corpus inputs a human or upstream process would supply:
plan, sources, packets, observation capture details, measurement inputs,
supersession records, and the human review (findings, checks, sufficiency,
revalidation flag). It never reads expected_correspondence_outcome,
expected_processing_disposition, or any other oracle field; tests compare the
derived result with those fields afterwards.

Adapter scaffolding (not corpus authority). The corpus defines no Signal
Candidates, reviewer, or review time, so this adapter supplies fixed synthetic
values: one Signal Candidate per case over the admitted Observations, the
reviewer alias REVIEWER, and the fixed time REVIEWED_AT. Nothing reads the wall
clock, and none of these values is a production default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from baec_app.domain.enums import ProvenanceCategory, StalenessStatus, ThresholdComparator
from baec_app.engine2.classification import (
    CrossCuttingCheck,
    DimensionAssessment,
    EvidenceLedger,
    HumanCorrespondenceReview,
)
from baec_app.engine2.domain import (
    ActiveMonitoringPlan,
    content_integrity_sha256,
    AuthorizedSource,
    CorrespondenceCandidate,
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    Engine1CurrentnessReading,
    MonitoringPlan,
    Observation,
    RejectedSourceItem,
    SignalCandidate,
    SourceItem,
    SufficiencyFinding,
    Supersession,
    SupersessionBasis,
    ThresholdSpec,
    activate_monitoring_plan,
    capture_observation,
    reject_source_item,
)
from baec_app.engine2.errors import MonitoringPlanActivationError, SourceNotAuthorizedError
from baec_app.engine2.measurement import DerivedMeasurement, MeasurementInput, Transformation, compute_derived_measurement

REPO = Path(__file__).resolve().parents[2]
CORPUS_JSON = REPO / "docs/engine2/corpus/HARBOR_CORRESPONDENCE_CORPUS.json"
CORPUS_MD = REPO / "docs/engine2/corpus/ENGINE2_STAGE_B_SYNTHETIC_CORPUS.md"
STAGE_A = REPO / "docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md"
REVIEWER = "REVIEWER-B (synthetic human alias)"
REVIEWED_AT = datetime.fromisoformat("2027-06-01T00:00:00+00:00")  # fixed; after every corpus observation
PLAN_FACT_RENEWAL = "PLAN-FACT-RENEWAL-DATE"
_COMPARATOR_WORDS = {"more than": ThresholdComparator.GREATER_THAN}


def load_corpus() -> dict:
    return json.loads(CORPUS_JSON.read_text(encoding="utf-8"))


def ts(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def authorized_sources(corpus: dict) -> dict[str, AuthorizedSource]:
    return {
        s["source_id"]: AuthorizedSource(
            source_id=s["source_id"], source_type=s["source_type"], owner_or_publisher=s["owner_or_publisher"],
            legitimate_basis=s["legitimate_basis"], access_method=s["access_method"],
            permitted_capture=s["permitted_capture"], authorized_by=s["authorized_by"],
            authorized_at=ts(s["authorized_at"]), known_limitations=s["known_limitations"],
        )
        for s in corpus["authorized_sources"]
    }


def plan_for(corpus: dict, case: dict) -> MonitoringPlan:
    plan, context = corpus["monitoring_plan"], case["monitoring_plan_context"]
    facts = {f["fact_id"] for f in plan["plan_facts"]}
    if context["recorded_renewal_date"] is None:
        facts.discard(PLAN_FACT_RENEWAL)
    threshold = plan["threshold"]
    return MonitoringPlan(
        monitoring_plan_id=plan["monitoring_plan_id"],
        plan_version=plan["plan_version"],
        baec_id=plan["baec_id"],
        condition_reference=plan["authoritative_condition_reference"]["buyer_exact_statement"],
        target_entities=tuple(e["name"] for e in plan["target_entities"]),
        required_dimensions=frozenset(CorrespondenceDimension.from_label(d) for d in plan["required_dimensions"]),
        not_applicable_dimensions=frozenset(CorrespondenceDimension.from_label(d) for d in plan["not_applicable_dimensions"]),
        authorized_source_ids=frozenset(plan["authorized_source_set"]),
        engine1_currentness_reference=plan["engine1_currentness_reference"],
        authorized_by=plan["authorized_by"],
        authorization_reference=plan["authorization_reference"],
        authorized_at=ts(plan["activated_at"]),
        threshold=ThresholdSpec(_COMPARATOR_WORDS[threshold["comparator"]], Decimal(threshold["value"]), threshold["unit"]),
        timing_context=plan["timing_context"],
        plan_fact_ids=frozenset(facts),
        plan_rule_ids=frozenset(r["rule_id"] for r in plan["plan_rules"]),
    )


def currentness_for(corpus: dict, case: dict) -> Engine1CurrentnessReading | None:
    """Engine 1's currentness input for the case.

    HBR-CORR-057 is the only case whose plan context is not ACTIVE; its corpus
    note says the Engine 1 currentness reference "is missing or unreadable". The
    smallest faithful input for that is no reading at all (None). The adapter
    does not substitute REVIEW_DUE, STALE, RETIRED, or any other Engine 1
    status the corpus does not state. Every other case receives the fixture's
    CURRENT reading.
    """
    if case["monitoring_plan_context"]["status"] != "ACTIVE":
        return None
    fixture = corpus["engine1_fixture"]
    ref = fixture["currentness_reference"]
    return Engine1CurrentnessReading(fixture["baec_id"], StalenessStatus(ref["staleness_status"]), ref["engine1_record"])


def source_item(packet: dict) -> SourceItem:
    return SourceItem(
        item_id=packet["packet_id"], source_id=packet["source_id"], source_type=packet["source_type"],
        acquisition_method=packet["acquisition"], received_at=ts(packet["received_at"]), content=packet["content"],
        published_at=ts(packet["published_at"]), effective_at=ts(packet["effective_at"]),
    )


def measurement_inputs(dm: dict, observations: dict[str, Observation]) -> tuple[MeasurementInput, ...]:
    return tuple(MeasurementInput(observations[i["observation_id"]], i["quantity"], Decimal(i["value"]), i["unit"])
                 for i in dm["inputs"])


def findings_from(case: dict, overrides: dict[str, DimensionFinding] | None = None) -> tuple[DimensionAssessment, ...]:
    out = []
    for label, f in case["expected_dimension_findings"].items():
        finding = (overrides or {}).get(label, DimensionFinding(f["finding"]))
        out.append(DimensionAssessment(CorrespondenceDimension.from_label(label), finding, tuple(f["evidence"]), f["reason"]))
    return tuple(out)


def checks_from(case: dict) -> tuple[CrossCuttingCheck, ...]:
    return tuple(
        CrossCuttingCheck(
            CrossCuttingCheckName.from_label(label), c["note"], tuple(c["evidence"]),
            frozenset(CorrespondenceDimension.from_label(d) for d in c["affected_dimensions"]),
        )
        for label, c in case["cross_cutting_checks"].items()
    )


@dataclass
class CaseRun:
    activation_error: MonitoringPlanActivationError | None = None
    active_plan: ActiveMonitoringPlan | None = None
    observations: list[Observation] = field(default_factory=list)
    rejected: list[RejectedSourceItem] = field(default_factory=list)
    measurements: list[DerivedMeasurement] = field(default_factory=list)
    ledger: EvidenceLedger | None = None
    candidate: CorrespondenceCandidate | None = None
    review_kwargs: dict | None = None
    review: HumanCorrespondenceReview | None = None
    outcome: CorrespondenceOutcome | None = None


def run_case(corpus: dict, case: dict) -> CaseRun:
    run = CaseRun()
    try:
        run.active_plan = activate_monitoring_plan(plan_for(corpus, case), currentness_for(corpus, case))
    except MonitoringPlanActivationError as error:
        run.activation_error = error
        return run
    sources = authorized_sources(corpus)
    entries = {o["packet_id"]: o for o in case["raw_observations_expected"]}
    for packet in case["source_packets"]:
        entry = entries.get(packet["packet_id"])
        try:
            observation = capture_observation(
                run.active_plan, sources, source_item(packet),
                observation_id=entry["observation_id"] if entry else f"ATTEMPT-{packet['packet_id']}",
                provenance=ProvenanceCategory(entry["provenance_category"]) if entry else ProvenanceCategory.EXTERNAL_EVIDENCE,
                content_sha256=entry["integrity"]["content_sha256"] if entry else content_integrity_sha256(packet["content"]),
                source_locator=entry["source_locator"] if entry else packet["packet_id"],
                evidence_event_id=entry["evidence_event_id"] if entry else packet["packet_id"],
            )
        except SourceNotAuthorizedError as error:
            run.rejected.append(reject_source_item(run.active_plan, source_item(packet), str(error)))
            continue
        run.observations.append(observation)
    supersessions = tuple(
        Supersession(o["observation_id"], o["superseded_by"], SupersessionBasis(o["supersession_basis"]["basis"]), o["retracted"])
        for o in case["raw_observations_expected"] if o["superseded_by"]
    )
    run.measurements = [
        compute_derived_measurement(dm["measurement_id"], Transformation(dm["transformation_id"]),
                                    measurement_inputs(dm, {o.observation_id: o for o in run.observations}),
                                    ts(dm["calculated_at"]))
        for dm in case["derived_measurements_expected"]
    ]
    run.ledger = EvidenceLedger(run.active_plan, tuple(run.observations), supersessions, tuple(run.measurements), tuple(run.rejected))
    if not run.observations:
        return run  # nothing admitted: no Signal Candidate, no review, no outcome
    plan = run.active_plan.plan
    signal = SignalCandidate(f"SC-{case['case_id']}", plan.monitoring_plan_id, plan.plan_version,
                             tuple(run.observations), REVIEWER)
    run.candidate = CorrespondenceCandidate(f"CC-{case['case_id']}", plan.baec_id, plan.monitoring_plan_id,
                                            plan.plan_version, (signal,))
    run.review_kwargs = dict(
        review_id=f"REV-{case['case_id']}", candidate=run.candidate, ledger=run.ledger,
        findings=findings_from(case), checks=checks_from(case),
        sufficiency=SufficiencyFinding(case["expected_human_sufficiency_finding"]),
        reviewer=REVIEWER, reviewed_at=REVIEWED_AT,
        baec_revalidation_required=case["expected_baec_revalidation_required"],
        revalidation_triggers=tuple(case["revalidation_triggers"]),
    )
    run.review = HumanCorrespondenceReview(**run.review_kwargs)
    run.outcome = run.review.outcome
    return run
