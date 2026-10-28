"""Engine 2 human review application layer (Stage I): one workflow over the locked Stages C, D, F, and G.

It composes existing rules and never restates them:

* build_review_packet reads one stored Correspondence Candidate and the complete authoritative record of its
  exact plan version: every Observation, Supersession, Derived Measurement, and Source Refusal recorded under
  (monitoring_plan_id, plan_version), in recording order, plus every earlier Human Correspondence Review of the
  candidate. Each record is rebuilt by the Stage D loaders, so Stage C validation runs again; the evidence
  ledger and its usability are the Stage C EvidenceLedger's. Nothing is selected, inferred, normalized, or
  repaired. The only SQL here enumerates record identifiers by plan version, because Stage D reads by identifier;
* submit_human_review rebuilds the same packet, refuses it if the authoritative context changed since the
  reviewer's packet (context_digest), builds the locked HumanCorrespondenceReview from judgments the human
  supplies in full, and records it with Stage D record_review, where the Stage C classifier derives the
  outcome. No argument accepts an outcome or a classification, and nothing is filled in from AI output;
* attach_advisory_proposal returns an ACCEPTED Stage G proposal only on explicit request, only when its stored
  AI input is exactly the input Stage F builds from this packet, and only as a separate advisory object. The
  packet and the review workflow never need an AI store, a provider, or a credential.

Reviews are append-only history: there is no mutable or designated review, only every review in recording order.
Failures raise. Integrity failures from Stage D or Stage G propagate unchanged and never become an outcome.
This module takes no seller action, changes no account state, and opens no connection of its own.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from . import ai_persistence as ai_store
from . import persistence as store
from .ai import BaecConditionInput, CorrespondenceAIProposal, build_correspondence_ai_input
from .classification import CrossCuttingCheck, DimensionAssessment, EvidenceLedger, HumanCorrespondenceReview
from .domain import (ActiveMonitoringPlan, CorrespondenceCandidate, CorrespondenceOutcome, MonitoringPlan,
                     SufficiencyFinding, plan_key)
from .errors import Engine2Error, MonitoringPlanActivationError

CONTEXT_DIGEST_VERSION = "baec-e2-review-context/v1"


class ReviewContextError(Engine2Error):
    """The requested review context does not exist or does not agree with itself. Nothing was recorded."""


class StaleReviewContextError(ReviewContextError):
    """The authoritative context changed after the reviewer's packet was built. Nothing was recorded."""


class AdvisoryProposalRefused(Engine2Error):
    """No ACCEPTED AI proposal exists for exactly this review context. Nothing is attached."""


@dataclass(frozen=True)
class RecordedReview:
    """One stored Human Correspondence Review and the outcome the Stage C classifier derives from it."""

    review: HumanCorrespondenceReview
    outcome: CorrespondenceOutcome


@dataclass(frozen=True)
class ReviewPacket:
    """Everything authoritative a human reviewer needs for one candidate. Read-only; holds no AI output."""

    activation_id: str
    active_plan: ActiveMonitoringPlan
    candidate: CorrespondenceCandidate
    ledger: EvidenceLedger
    review_history: tuple[RecordedReview, ...]
    context_digest: str

    @property
    def plan(self) -> MonitoringPlan:
        return self.active_plan.plan

    @property
    def monitoring_plan_id(self) -> str:
        return self.plan.monitoring_plan_id

    @property
    def plan_version(self) -> int:
        return self.plan.plan_version

    @property
    def baec_id(self) -> str:
        return self.plan.baec_id

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def usable_observation_ids(self) -> frozenset[str]:
        return self.ledger.usable_observation_ids

    @property
    def usable_measurement_ids(self) -> frozenset[str]:
        return self.ledger.usable_measurement_ids


@dataclass(frozen=True)
class AdvisoryProposal:
    """An ACCEPTED Stage G proposal for exactly one review context. AI_INFERENCE: advisory, never authoritative."""

    attempt_id: str
    artifact_id: str
    input_digest: str
    proposal: CorrespondenceAIProposal


def _ids(connection, sql: str, key: tuple[str, int]) -> list[str]:
    return [row[0] for row in connection.execute(sql, key)]


def _context_digest(activation_id: str, candidate: CorrespondenceCandidate, ledger: EvidenceLedger) -> str:
    """SHA-256 over the identity of every authoritative record in the context, in order."""
    plan = ledger.plan
    material = {
        "version": CONTEXT_DIGEST_VERSION, "monitoring_plan_id": plan.monitoring_plan_id,
        "plan_version": plan.plan_version, "baec_id": plan.baec_id, "activation_id": activation_id,
        "candidate_id": candidate.candidate_id,
        "signal_candidates": [[s.signal_candidate_id, list(s.observation_ids)] for s in candidate.signal_candidates],
        "observations": [[o.observation_id, o.content_sha256] for o in ledger.observations],
        "supersessions": [[s.superseded_observation_id, s.superseding_observation_id, s.basis.value, s.retraction]
                          for s in ledger.supersessions],
        "measurements": [[m.measurement_id, str(m.output_value), [i.observation_id for i in m.inputs]]
                         for m in ledger.measurements],
        "refusals": [[r.item_id, r.reason] for r in ledger.rejected_items],
    }
    text = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_review_packet(connection, *, monitoring_plan_id: str, plan_version: int, baec_id: str, candidate_id: str,
                        activation_id: str) -> ReviewPacket:
    """Read the complete authoritative review context for one candidate, or raise. Deterministic; writes nothing."""
    key = (monitoring_plan_id, plan_version)
    try:
        plan = store.load_monitoring_plan(connection, monitoring_plan_id, plan_version)
    except store.RecordNotFoundError as error:
        raise ReviewContextError(f"no Monitoring Plan {monitoring_plan_id} version {plan_version}") from error
    if plan.baec_id != baec_id:
        raise ReviewContextError(f"Monitoring Plan {monitoring_plan_id} v{plan_version} is for another BAEC")
    try:
        active = store.load_active_plan(connection, activation_id)
    except store.RecordNotFoundError as error:
        raise ReviewContextError(f"no plan activation {activation_id}") from error
    except MonitoringPlanActivationError as error:
        raise ReviewContextError(f"plan activation {activation_id} was refused") from error
    if plan_key(active) != key:
        raise ReviewContextError(f"plan activation {activation_id} is for another plan version")
    try:
        candidate = store.load_correspondence_candidate(connection, candidate_id)
    except store.RecordNotFoundError as error:
        raise ReviewContextError(f"no Correspondence Candidate {candidate_id}") from error
    if plan_key(candidate) != key:
        raise ReviewContextError(f"Correspondence Candidate {candidate_id} belongs to another plan version")
    if candidate.baec_id != baec_id:
        raise ReviewContextError(f"Correspondence Candidate {candidate_id} is for another BAEC")
    where = "WHERE monitoring_plan_id = ? AND plan_version = ? ORDER BY rowid"
    ledger = EvidenceLedger(
        active,
        tuple(store.load_observation(connection, i) for i in _ids(connection, f"SELECT observation_id FROM observations {where}", key)),
        tuple(store.load_supersession(connection, i) for i in _ids(
            connection, f"SELECT superseded_observation_id FROM supersessions {where}", key)),
        tuple(store.load_derived_measurement(connection, i) for i in _ids(
            connection, f"SELECT measurement_id FROM derived_measurements {where}", key)),
        tuple(store.load_source_refusal(connection, monitoring_plan_id, plan_version, i) for i in _ids(
            connection, f"SELECT item_id FROM source_refusals {where}", key)),
    )
    if not candidate.observation_ids <= ledger.observation_ids:
        raise store.Engine2IntegrityError(f"Correspondence Candidate {candidate_id} cites Observations outside its plan version")
    history = []
    for review_id in store.review_ids_for_candidate(connection, candidate_id):
        review = store.load_review(connection, review_id)
        if review.candidate != candidate or plan_key(review.ledger.plan) != key:
            raise store.Engine2IntegrityError(f"review {review_id} disagrees with its candidate's context")
        history.append(RecordedReview(review, review.outcome))
    return ReviewPacket(activation_id, active, candidate, ledger, tuple(history),
                        _context_digest(activation_id, candidate, ledger))


def submit_human_review(
    connection, *, monitoring_plan_id: str, plan_version: int, baec_id: str, candidate_id: str, activation_id: str,
    context_digest: str, review_id: str, findings: tuple[DimensionAssessment, ...], checks: tuple[CrossCuttingCheck, ...],
    sufficiency: SufficiencyFinding, reviewer: str, reviewed_at: datetime, baec_revalidation_required: bool,
    revalidation_triggers: tuple[str, ...], recorded_at: datetime, recorded_by: str,
) -> RecordedReview:
    """Record one human review against the context the reviewer saw. The outcome is derived, never supplied."""
    packet = build_review_packet(connection, monitoring_plan_id=monitoring_plan_id, plan_version=plan_version,
                                 baec_id=baec_id, candidate_id=candidate_id, activation_id=activation_id)
    if context_digest != packet.context_digest:
        raise StaleReviewContextError("the review context changed after the reviewer's packet was built")
    review = HumanCorrespondenceReview(
        review_id=review_id, candidate=packet.candidate, ledger=packet.ledger, findings=findings, checks=checks,
        sufficiency=sufficiency, reviewer=reviewer, reviewed_at=reviewed_at,
        baec_revalidation_required=baec_revalidation_required, revalidation_triggers=revalidation_triggers,
    )
    store.record_review(connection, review, activation_id=activation_id, recorded_at=recorded_at, recorded_by=recorded_by)
    stored = store.load_review(connection, review_id)
    if stored != review:
        raise store.Engine2IntegrityError(f"review {review_id} did not round-trip exactly")
    return RecordedReview(stored, stored.outcome)


def attach_advisory_proposal(packet: ReviewPacket, ai_connection, *, condition: BaecConditionInput, attempt_id: str,
                             artifact_id: str) -> AdvisoryProposal:
    """Return the ACCEPTED proposal of one attempt, only if its input is exactly this packet's AI input."""
    if not isinstance(packet, ReviewPacket):
        raise AdvisoryProposalRefused("an advisory proposal attaches only to a ReviewPacket")
    try:
        expected = build_correspondence_ai_input(condition, packet.candidate, packet.ledger)
    except Engine2Error as error:
        raise AdvisoryProposalRefused(f"the BAEC condition does not fit this review context: {error}") from error
    try:
        validation = ai_store.load_ai_validation(ai_connection, attempt_id)
    except ai_store.AINotFoundError as error:
        raise AdvisoryProposalRefused(f"AI attempt {attempt_id} has no validated response") from error
    if validation.status != "ACCEPTED" or validation.proposal is None:
        raise AdvisoryProposalRefused(f"AI attempt {attempt_id} was not ACCEPTED; nothing is attached")
    proposal = validation.proposal
    if proposal.artifact_id != artifact_id:
        raise AdvisoryProposalRefused(f"AI proposal {artifact_id} does not belong to attempt {attempt_id}")
    attempt = ai_store.load_ai_attempt(ai_connection, attempt_id)
    if ai_store.load_ai_input(ai_connection, attempt.input_digest) != expected or proposal.input_digest != expected.input_digest:
        raise AdvisoryProposalRefused(f"AI attempt {attempt_id} was made on a different review context")
    if (proposal.monitoring_plan_id, proposal.plan_version, proposal.baec_id, proposal.candidate_id) != \
            (packet.monitoring_plan_id, packet.plan_version, packet.baec_id, packet.candidate_id):
        raise AdvisoryProposalRefused(f"AI proposal {artifact_id} names another plan, BAEC, or candidate")
    return AdvisoryProposal(attempt_id, artifact_id, expected.input_digest, proposal)
