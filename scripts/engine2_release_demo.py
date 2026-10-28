"""BAEC Engine 2 release-candidate demonstration: one deterministic Engine 1 -> Engine 2 run, offline.

Usage, from the repository root:

    python3 scripts/engine2_release_demo.py      # prints the canonical release summary (JSON)

Every step uses the locked production boundary, in order:

    Engine 1 buyer interaction (Repository)
    -> human confirmation request, gate approval, and confirm_baec (Phase 4 HumanCommandFacade)
    -> persisted CONFIRMED_BAEC
    -> Stage K read-only handoff (query-only Engine 1 connection)
    -> Engine 2 MonitoringPlan (handoff facts + explicit planning choices) and activation (Stage D)
    -> authorized evidence capture, a correction and a retraction, refused source item, Derived Measurements
    -> Signal Candidate and Correspondence Candidate
    -> Stage I review packet -> human correspondence review (Stage I) -> authoritative outcome (locked classifier)

The scenario is the original synthetic Stage J scenario (Alder Ridge Clinics and Cobalt Imaging Supply), now starting
from a real Engine 1 confirmation. It is not taken from the research corpus. The human approval and the human review
judgments are scripted synthetic inputs, fixed below; nothing derives them from an expected answer. Clocks are fixed
and every identifier is explicit. Both stores are in-memory: nothing is written to disk, no AI is used, and no network,
credential, or Engine 1 development database is touched. The run changes no account state and takes no seller action.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from baec_app.application import build_command_facade  # noqa: E402
from baec_app.data.database import make_read_only, open_database  # noqa: E402
from baec_app.data.records import SourceInteraction  # noqa: E402
from baec_app.data.repository import Repository  # noqa: E402
from baec_app.domain.enums import (ArticulationOrigin, BaecCriterion, CriterionFinding, ElicitationMode,  # noqa: E402
                                   ProvenanceCategory, ThresholdComparator)
from baec_app.domain.models import (Account, BaecCandidate, CriterionAssessment, EvidenceExcerpt,  # noqa: E402
                                    StringencyExpression)
from baec_app.engine2 import persistence as e2  # noqa: E402
from baec_app.engine2 import review  # noqa: E402
from baec_app.engine2.classification import CrossCuttingCheck, DimensionAssessment  # noqa: E402
from baec_app.engine2.domain import (CorrespondenceCandidate, CorrespondenceDimension, CrossCuttingCheckName,  # noqa: E402
                                     DimensionFinding, SignalCandidate, SourceItem, SufficiencyFinding, Supersession,
                                     SupersessionBasis, capture_observation, content_integrity_sha256,
                                     reject_source_item)
from baec_app.engine2.integration import engine1_handoff as handoff_boundary  # noqa: E402
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement  # noqa: E402
from baec_app.engine2.operator_ui import demo as stage_j  # noqa: E402

SUMMARY_VERSION = "baec-engine2-release-demo-summary/v1"
D = CorrespondenceDimension

# --- Engine 1 inputs (synthetic) ---------------------------------------------------------------------------------

ACCOUNT_ID = "ACC-RC-ALDER"
INTERACTION_ID = "INT-RC-ALDER-01"
INTERACTION_AT = datetime(2027, 2, 15, 15, 0, tzinfo=timezone.utc)
CONFIRMED_AT = datetime(2027, 2, 16, 10, 0, tzinfo=timezone.utc)
REVIEWER_E1 = "RC-REVIEWER-E1 (synthetic)"
CURRENT_STATE_LINE = "Things are fine with Cobalt for now, and we are not looking at other suppliers."
INTERACTION_TEXT = f"Buyer: {CURRENT_STATE_LINE} {stage_j.BUYER_STATEMENT}"
BAEC_ID = "BAEC-RC-001"

# --- Engine 2 inputs (synthetic) ---------------------------------------------------------------------------------

PLAN_ID = "MP-RC-001"
ACTIVATION_ID = "ACT-RC-001"
CANDIDATE_ID = "CC-RC-001"
PLANNED_AT = datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)
RECORDED_AT = datetime(2027, 6, 1, 9, 0, tzinfo=timezone.utc)
REVIEWED_AT = datetime(2027, 6, 2, 9, 0, tzinfo=timezone.utc)
ACTOR = "RC-OPERATOR (synthetic)"
REVIEWER_E2 = "RC-REVIEWER-E2 (synthetic)"
REVIEW_ID = f"REV-{CANDIDATE_ID}-1"
CHOICES = handoff_boundary.PlanChoices(
    monitoring_plan_id=PLAN_ID, plan_version=1, target_entities=stage_j.PLAN.target_entities,
    required_dimensions=stage_j.PLAN.required_dimensions, not_applicable_dimensions=stage_j.PLAN.not_applicable_dimensions,
    authorized_source_ids=stage_j.PLAN.authorized_source_ids, authorized_by="RC-ACCOUNT-MANAGER (synthetic)",
    authorization_reference="RC-PLAN-AUTHORIZATION-001", authorized_at=PLANNED_AT,
    plan_fact_ids=stage_j.PLAN.plan_fact_ids, plan_rule_ids=stage_j.PLAN.plan_rule_ids)


class _FixedClock:
    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


class _ExplicitIds:
    """Engine 1 identifiers, fixed in advance: one BAEC id and numbered gate tokens. No random identity."""

    def __init__(self) -> None:
        self._tokens = 0

    def new_baec_id(self) -> str:
        return BAEC_ID

    def new_token(self) -> str:
        self._tokens += 1
        return f"RC-TOKEN-{self._tokens:03d}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate() -> BaecCandidate:
    buyer_line = EvidenceExcerpt(f"{CURRENT_STATE_LINE} {stage_j.BUYER_STATEMENT}", ProvenanceCategory.BUYER_FACT,
                                 INTERACTION_ID)
    present = EvidenceExcerpt(CURRENT_STATE_LINE, ProvenanceCategory.BUYER_FACT, INTERACTION_ID)
    statement = EvidenceExcerpt(stage_j.BUYER_STATEMENT, ProvenanceCategory.BUYER_FACT, INTERACTION_ID)
    evidence = {BaecCriterion.PRESENT_NON_EVALUATION: present}
    return BaecCandidate(
        account_id=ACCOUNT_ID, source_interaction_id=INTERACTION_ID, source_excerpt=buyer_line,
        assessments=tuple(CriterionAssessment(c, CriterionFinding.MET, (evidence.get(c, statement),))
                          for c in BaecCriterion),
        articulation_origin=ArticulationOrigin.BUYER_GENERATED, elicitation_mode=ElicitationMode.SPONTANEOUS,
        buyer_exact_statement=stage_j.BUYER_STATEMENT,
        stringency=StringencyExpression("more than 8%", ThresholdComparator.GREATER_THAN, Decimal("8"), "%",
                                        timing_text="at the July renewal"))


def confirm_in_engine1(connection) -> str:
    """Engine 1: the buyer interaction, then the locked Phase 4 human confirmation path."""
    repository = Repository(connection)
    repository.add_account(Account(ACCOUNT_ID, stage_j.SELECTION.account_name))
    repository.add_interaction(SourceInteraction(INTERACTION_ID, ACCOUNT_ID, INTERACTION_AT, INTERACTION_TEXT))
    facade = build_command_facade(repository, clock=_FixedClock(CONFIRMED_AT), ids=_ExplicitIds())
    session = facade.gate.open_session(REVIEWER_E1)
    request = facade.request_baec_confirmation(session, _candidate(), captured_at=INTERACTION_AT)
    approval = facade.gate.approve(session, request.request_id, displayed_digest=request.digest)
    return facade.confirm_baec(approval).baec_id


def _human_review(packet: review.ReviewPacket) -> dict:
    """The scripted synthetic reviewer's judgments. Supplied, never derived."""
    measured = {D.THRESHOLD_MATCH, D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH}
    findings = tuple(
        DimensionAssessment(d, DimensionFinding.SUPPORTED, ("DM-DEMO-02",) if d in measured else ("OBS-DEMO-02",),
                            f"The corrected renewal notice for contract AR-2207 bears on {d.label.lower()}.")
        if d in packet.plan.required_dimensions else
        DimensionAssessment(d, DimensionFinding.NOT_APPLICABLE, (), "The plan marks this dimension not applicable.")
        for d in D)
    notes = {CrossCuttingCheckName.FRESHNESS: ("The corrected notice is dated 10 May 2027.", ("OBS-DEMO-02",)),
             CrossCuttingCheckName.CONTRADICTORY_EVIDENCE: (
                 "The 3 May figure was corrected by the supplier; the retracted price-freeze statement is not relied on.",
                 ("OBS-DEMO-01", "OBS-DEMO-04")),
             CrossCuttingCheckName.MISSING_EVIDENCE: ("No required evidence is missing.", ()),
             CrossCuttingCheckName.PROVENANCE_QUALITY: (
                 "The notice was forwarded by the buyer; the forum post was refused.", ("ITEM-DEMO-FORUM-01",))}
    checks = tuple(CrossCuttingCheck(c, notes[c][0], notes[c][1]) for c in CrossCuttingCheckName)
    return dict(findings=findings, checks=checks, sufficiency=SufficiencyFinding.YES, reviewer=REVIEWER_E2,
                reviewed_at=REVIEWED_AT, baec_revalidation_required=False, revalidation_triggers=(),
                recorded_at=REVIEWED_AT, recorded_by=REVIEWER_E2)


def monitor_in_engine2(connection, handoff: handoff_boundary.ConfirmedBaecHandoff) -> dict:
    """Engine 2: plan from the handoff, activation, evidence, candidate, packet, and one human review."""
    plan = handoff_boundary.monitoring_plan_from_handoff(handoff, CHOICES)
    for source in stage_j.SOURCES:
        e2.record_authorized_source(connection, source, recorded_at=PLANNED_AT, recorded_by=ACTOR)
    e2.record_monitoring_plan(connection, plan, recorded_at=PLANNED_AT, recorded_by=ACTOR)
    active = e2.record_plan_activation(connection, PLAN_ID, 1, handoff_boundary.currentness_reading(plan, handoff),
                                       activation_id=ACTIVATION_ID, recorded_at=PLANNED_AT, recorded_by=ACTOR)
    sources = {s.source_id: s for s in stage_j.SOURCES}
    observations = {}
    for observation_id, source_id, provenance, received, content, effective in stage_j._ITEMS:
        item = SourceItem(f"ITEM-{observation_id}", source_id, sources[source_id].source_type, "manual capture",
                          received, content, published_at=received, effective_at=effective)
        observation = capture_observation(active, sources, item, observation_id=observation_id, provenance=provenance,
                                          content_sha256=content_integrity_sha256(content),
                                          source_locator=f"{source_id}/{item.item_id}",
                                          evidence_event_id=f"EVENT-{observation_id}")
        e2.record_observation(connection, observation, activation_id=ACTIVATION_ID, recorded_at=RECORDED_AT,
                              recorded_by=ACTOR)
        observations[observation_id] = observation
    refused = stage_j._REFUSED
    e2.record_source_refusal(connection, reject_source_item(active, refused, f"source {refused.source_id} is not "
                                                                              "authorized for this plan"),
                             activation_id=ACTIVATION_ID, source_reference=refused.source_id, recorded_at=RECORDED_AT,
                             recorded_by=ACTOR)
    correction = SupersessionBasis("explicit correction or retraction language")
    for old, new, retraction in (("OBS-DEMO-01", "OBS-DEMO-02", False), ("OBS-DEMO-04", "OBS-DEMO-05", True)):
        e2.record_supersession(connection, Supersession(old, new, correction, retraction), recorded_at=RECORDED_AT,
                               recorded_by=ACTOR)
    for measurement_id, observation_id, new_price in (("DM-DEMO-01", "OBS-DEMO-01", "44800"),
                                                      ("DM-DEMO-02", "OBS-DEMO-02", "43600")):
        source = observations[observation_id]
        e2.record_derived_measurement(connection, compute_derived_measurement(
            measurement_id, Transformation.PERCENT_CHANGE_FROM_TWO_PRICES,
            (MeasurementInput(source, "baseline_price", Decimal("40000"), "USD per year"),
             MeasurementInput(source, "new_price", Decimal(new_price), "USD per year")), RECORDED_AT),
            recorded_at=RECORDED_AT, recorded_by=ACTOR)
    signal = SignalCandidate("SC-RC-001", PLAN_ID, 1, (observations["OBS-DEMO-02"], observations["OBS-DEMO-03"]),
                             ACTOR, frozenset({D.NUMERIC_MAGNITUDE, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}))
    e2.record_signal_candidate(connection, signal, recorded_at=RECORDED_AT, recorded_by=ACTOR)
    e2.record_correspondence_candidate(connection, CorrespondenceCandidate(CANDIDATE_ID, handoff.baec_id, PLAN_ID, 1,
                                                                           (signal,)),
                                       recorded_at=RECORDED_AT, recorded_by=ACTOR)
    context = dict(monitoring_plan_id=PLAN_ID, plan_version=1, baec_id=handoff.baec_id, candidate_id=CANDIDATE_ID,
                   activation_id=ACTIVATION_ID)
    packet = review.build_review_packet(connection, **context)
    recorded = review.submit_human_review(connection, **context, context_digest=packet.context_digest,
                                          review_id=REVIEW_ID, **_human_review(packet))
    after = review.build_review_packet(connection, **context)  # what the Stage J operator page reads
    return {"plan": plan, "packet": packet, "recorded": recorded, "after": after}


def run_release_demo() -> dict:
    """Run the whole demonstration in memory and return its canonical summary."""
    engine1 = open_database(":memory:")
    engine2 = e2.open_engine2_database(":memory:")
    reader = open_database(":memory:")
    try:
        baec_id = confirm_in_engine1(engine1)
        engine1_image = engine1.serialize()
        reader.deserialize(engine1_image)
        make_read_only(reader)  # Engine 1's own query-only guard; the handoff refuses anything else
        handoff = handoff_boundary.read_confirmed_baec(reader, baec_id)
        result = monitor_in_engine2(engine2, handoff)
        e2.verify_database(engine2)
        if reader.serialize() != engine1_image:
            raise RuntimeError("the Engine 1 store changed during the handoff")
        accounts = engine1.execute("SELECT account_id, state FROM accounts").fetchall()
        transitions = engine1.execute("SELECT COUNT(*) FROM account_state_transitions").fetchone()[0]
        packet, after, recorded = result["packet"], result["after"], result["recorded"]
        summary = {
            "summary_version": SUMMARY_VERSION,
            "engine1": {"baec_id": baec_id, "classification": "CONFIRMED_BAEC",
                        "staleness_status": handoff.staleness_status.value, "confirmed_by": handoff.confirmed_by,
                        "confirmed_at": handoff.confirmed_at.isoformat(),
                        "account_states": [[a, s] for a, s in accounts], "account_state_transitions": transitions},
            "handoff": {"version": handoff_boundary.HANDOFF_VERSION, "digest": handoff.digest,
                        "engine1_reference": handoff.engine1_reference},
            "engine2": {
                "monitoring_plan": [PLAN_ID, 1], "activation_id": ACTIVATION_ID,
                "condition_reference": result["plan"].condition_reference,
                "threshold": [result["plan"].threshold.comparator.value, str(result["plan"].threshold.value),
                              result["plan"].threshold.unit],
                "timing_context": result["plan"].timing_context,
                "observations": [[o.observation_id, o.observation_id in packet.usable_observation_ids]
                                 for o in packet.ledger.observations],
                "measurements": [[m.measurement_id, str(m.output_value), m.measurement_id in packet.usable_measurement_ids]
                                 for m in packet.ledger.measurements],
                "refusals": [r.item_id for r in packet.ledger.rejected_items],
                "candidate_id": CANDIDATE_ID, "context_digest": packet.context_digest,
                "review_id": recorded.review.review_id, "outcome": recorded.outcome.value,
                "review_history": [[h.review.review_id, h.outcome.value] for h in after.review_history]},
        }
        # The digest covers the logical records above. Raw SQLite images are reported beside it, outside the digest,
        # because their bytes can differ between SQLite builds while the records do not.
        summary["release_demo_digest"] = _sha256(json.dumps(summary, sort_keys=True, ensure_ascii=False,
                                                            separators=(",", ":")).encode("utf-8"))
        summary["store_images"] = {"engine1_sha256": _sha256(engine1_image), "engine2_sha256": _sha256(engine2.serialize())}
        return summary
    finally:
        engine1.close()
        engine2.close()
        reader.close()


def main() -> int:
    print(json.dumps(run_release_demo(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
