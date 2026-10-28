"""Synthetic builders for Engine 2 unit tests. Fixed timestamps; no wall clock."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from baec_app.domain.enums import ProvenanceCategory, StalenessStatus, ThresholdComparator
from baec_app.engine2.classification import (
    CrossCuttingCheck,
    DimensionAssessment,
    EvidenceLedger,
    HumanCorrespondenceReview,
)
from baec_app.engine2.domain import (
    ALL_DIMENSIONS,
    AuthorizedSource,
    CorrespondenceCandidate,
    CorrespondenceDimension,
    CrossCuttingCheckName,
    DimensionFinding,
    Engine1CurrentnessReading,
    MonitoringPlan,
    SignalCandidate,
    SourceItem,
    SufficiencyFinding,
    ThresholdSpec,
    activate_monitoring_plan,
    capture_observation,
)

T0 = datetime(2027, 1, 15, 14, 0, tzinfo=timezone.utc)
SRC = "SRC-TEST-01"
PLAN_ID = "MP-TEST-001"
BAEC_ID = "BAEC-TEST-001"
CONF = "CONF-TEST-001"
S = DimensionFinding.SUPPORTED


def source(source_id: str = SRC) -> AuthorizedSource:
    return AuthorizedSource(source_id, "buyer-provided document", "Synthetic Buyer", "Supplied by the buyer.",
                            "documents the buyer sends", "pricing notices", "REVIEWER-T", T0)


def plan(**changes) -> MonitoringPlan:
    base = MonitoringPlan(
        monitoring_plan_id=PLAN_ID, plan_version=1, baec_id=BAEC_ID, condition_reference="EXC-TEST-001",
        target_entities=("Synthetic Supplier Co.",), required_dimensions=ALL_DIMENSIONS,
        not_applicable_dimensions=frozenset(), authorized_source_ids=frozenset({SRC}),
        engine1_currentness_reference=CONF, authorized_by="REVIEWER-T", authorization_reference="AUTH-MP-TEST",
        authorized_at=T0, threshold=ThresholdSpec(ThresholdComparator.GREATER_THAN, Decimal("10"), "%"),
        timing_context="when our agreement renews", plan_rule_ids=frozenset({"PLAN-RULE-TEST"}),
    )
    return replace(base, **changes) if changes else base


def current(baec_id: str = BAEC_ID, status=StalenessStatus.CURRENT, record=CONF) -> Engine1CurrentnessReading:
    return Engine1CurrentnessReading(baec_id, status, record)


def active(p: MonitoringPlan | None = None):
    return activate_monitoring_plan(p or plan(), current())


def item(item_id: str = "PKT-T-01", source_id: str | None = SRC, content: str = "Synthetic notice: pricing rises 12% at renewal."):
    return SourceItem(item_id, source_id, "buyer-provided document", "forwarded by the buyer", T0, content)


def observe(active_plan, observation_id: str = "OBS-T-01", it: SourceItem | None = None, event: str | None = None):
    it = it or item()
    return capture_observation(
        active_plan, {SRC: source()}, it, observation_id=observation_id,
        provenance=ProvenanceCategory.EXTERNAL_EVIDENCE,
        content_sha256=hashlib.sha256(it.content.encode()).hexdigest(),
        source_locator=f"{it.item_id}: full text", evidence_event_id=event or f"EVT-{observation_id}",
    )


def findings(ev=("OBS-T-01",), **overrides) -> tuple[DimensionAssessment, ...]:
    """All 11 dimensions SUPPORTED by ev, except overrides {MACHINE_ID: (finding, refs)}."""
    out = []
    for d in CorrespondenceDimension:
        finding, refs = overrides.get(d.name, (S, ev))
        out.append(DimensionAssessment(d, finding, tuple(refs), f"synthetic reason for {d.label}"))
    return tuple(out)


def checks(unresolved=frozenset(), **notes) -> tuple[CrossCuttingCheck, ...]:
    return tuple(
        CrossCuttingCheck(
            name, notes.get(name.name, "No issue."), (),
            frozenset(unresolved) if name is CrossCuttingCheckName.MISSING_EVIDENCE else frozenset(),
        )
        for name in CrossCuttingCheckName
    )


def ledger(active_plan=None, observations=None, **kw) -> EvidenceLedger:
    active_plan = active_plan or active()
    return EvidenceLedger(active_plan, tuple(observations or [observe(active_plan)]), **kw)


def review(led: EvidenceLedger | None = None, **changes) -> HumanCorrespondenceReview:
    led = led or ledger()
    p = led.plan
    observations = tuple(sorted(led.observations, key=lambda o: o.observation_id))
    signal = SignalCandidate("SC-T", p.monitoring_plan_id, p.plan_version, observations, "REVIEWER-T")
    candidate = CorrespondenceCandidate("CC-T", p.baec_id, p.monitoring_plan_id, p.plan_version, (signal,))
    kwargs = dict(review_id="REV-T", candidate=candidate, ledger=led, findings=findings(), checks=checks(),
                  sufficiency=SufficiencyFinding.YES, reviewer="REVIEWER-T", reviewed_at=T0,
                  baec_revalidation_required=False, revalidation_triggers=())
    kwargs.update(changes)
    return HumanCorrespondenceReview(**kwargs)
