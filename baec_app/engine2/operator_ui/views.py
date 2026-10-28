"""Presentation-only helpers for the Engine 2 operator page. Deterministic, and free of Streamlit.

Nothing here decides a finding, usability, sufficiency, or an outcome. Usability is read from the Stage C ledger
carried by the Stage I packet; review consistency is decided by the locked domain when Stage I records the review;
the outcome is derived by the locked classifier. This module only labels, arranges, and checks that the operator
answered every question before anything is submitted.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import persistence as store
from .. import review
from ..classification import CrossCuttingCheck, DimensionAssessment
from ..domain import CorrespondenceDimension, CrossCuttingCheckName, CorrespondenceOutcome, DimensionFinding, SufficiencyFinding
from ..errors import ReviewInconsistentError

QUESTION = "Has new evidence appeared that may correspond to a verified buyer-defined condition?"
BOUNDARY = ("Correspondence narrows human attention. It does not establish buyer evaluation, purchase intent, an "
            "opportunity, or permission to contact the buyer.")
RESULT_BOUNDARY = ("This result does not establish that the buyer is currently evaluating, intends to purchase, or that "
                   "an opportunity exists.")
CANDIDATE_MEANING = ("A correspondence candidate means only that evidence may be relevant to the buyer-defined "
                     "condition and warrants human review.")
AI_PANEL_TITLE = "AI advisory analysis: not a human finding"
AI_PANEL_NOTE = ("Produced by a model and validated only for structure and citations. It does not fill in, change, or "
                 "count toward any human judgment below.")
NO_ADVISORY = "No advisory artifact is attached to this review context."

OUTCOME_EXPLANATIONS = {
    CorrespondenceOutcome.NO_CORRESPONDENCE:
        "At least one required dimension was found contradicted, so the evidence does not correspond to the buyer's "
        "condition under the locked rule.",
    CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE:
        "Some required dimensions remain unresolved, or the evidence was not judged sufficient, so correspondence is "
        "not verified under the locked rule.",
    CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE:
        "Every required dimension was found supported, the evidence was judged sufficient, and no BAEC revalidation "
        "was flagged, so this human review meets the locked correspondence rule.",
}
FINDING_OPTIONS = tuple(f.value for f in DimensionFinding)
SUFFICIENCY_OPTIONS = tuple(s.value for s in SufficiencyFinding)
REVALIDATION_OPTIONS = ("Not required", "Required")


@dataclass(frozen=True)
class EvidenceRow:
    observation_id: str
    status: str
    superseded_by: str | None
    basis: str | None
    source_id: str
    source_type: str
    provenance: str
    observed_at: str
    effective_at: str | None
    exact_text: str


@dataclass(frozen=True)
class MeasurementRow:
    measurement_id: str
    status: str
    formula: str
    output: str
    inputs: tuple[tuple[str, str, str, str], ...]


def evidence_rows(packet: review.ReviewPacket) -> tuple[EvidenceRow, ...]:
    """Every stored Observation, in recording order, labeled by the ledger's own usability."""
    successor = {s.superseded_observation_id: s for s in packet.ledger.supersessions}
    rows = []
    for o in packet.ledger.observations:
        link = successor.get(o.observation_id)
        if o.observation_id in packet.usable_observation_ids:
            status = "USABLE"
        else:
            status = "RETRACTED" if link is not None and link.retraction else "SUPERSEDED"
        rows.append(EvidenceRow(o.observation_id, status, link.superseding_observation_id if link else None,
                                link.basis.value if link else None, o.source_id, o.source_type, o.provenance.value,
                                o.observed_at.isoformat(), o.effective_at.isoformat() if o.effective_at else None,
                                o.exact_evidence_content))
    return tuple(rows)


def measurement_rows(packet: review.ReviewPacket) -> tuple[MeasurementRow, ...]:
    return tuple(
        MeasurementRow(m.measurement_id, "USABLE" if m.measurement_id in packet.usable_measurement_ids else "INVALIDATED",
                       m.formula, f"{m.output_value} {m.output_unit}",
                       tuple((i.observation_id, i.quantity, str(i.value), i.unit) for i in m.inputs))
        for m in packet.ledger.measurements)


def finding_citations(packet: review.ReviewPacket) -> dict[str, str]:
    """Identifiers a finding may cite, labeled; the domain decides whether a citation is acceptable."""
    labels = {row.observation_id: f"{row.observation_id} (Observation, {row.status})" for row in evidence_rows(packet)}
    labels |= {row.measurement_id: f"{row.measurement_id} (Derived Measurement, {row.status})"
               for row in measurement_rows(packet)}
    labels |= {i: f"{i} (plan fact)" for i in sorted(packet.plan.plan_fact_ids)}
    labels |= {i: f"{i} (plan rule)" for i in sorted(packet.plan.plan_rule_ids)}
    return labels


def check_citations(packet: review.ReviewPacket) -> dict[str, str]:
    return finding_citations(packet) | {r.item_id: f"{r.item_id} (refused source item)" for r in packet.ledger.rejected_items}


def plan_requirement(packet: review.ReviewPacket, dimension: CorrespondenceDimension) -> str:
    return "required by the plan" if dimension in packet.plan.required_dimensions else "not applicable under the plan"


# Every human-entered field of the review form, by widget key. A draft holds exactly these, as the operator entered them.
DRAFT_KEYS = tuple(
    [f"e2_{field}_{d.name}" for d in CorrespondenceDimension for field in ("finding", "evidence", "reason")]
    + [f"e2_{field}_{c.name}" for c in CrossCuttingCheckName for field in ("check_note", "check_evidence", "check_dimensions")]
    + ["e2_sufficiency", "e2_revalidation", "e2_triggers", "e2_reviewer"])


def draft_context(packet: review.ReviewPacket, context_digest: str) -> tuple[str, int, str, str, str, str]:
    """The exact review context a draft belongs to. A draft never moves to another context."""
    return (packet.monitoring_plan_id, packet.plan_version, packet.baec_id, packet.candidate_id, packet.activation_id,
            context_digest)


def next_review_id(packet: review.ReviewPacket) -> str:
    return f"REV-{packet.candidate_id}-{len(packet.review_history) + 1}"


@dataclass(frozen=True)
class ReviewAnswers:
    """Exactly the human judgments the locked HumanCorrespondenceReview needs, as the operator entered them."""

    findings: tuple[DimensionAssessment, ...]
    checks: tuple[CrossCuttingCheck, ...]
    sufficiency: SufficiencyFinding
    baec_revalidation_required: bool
    revalidation_triggers: tuple[str, ...]
    reviewer: str


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def collect_answers(values: dict) -> ReviewAnswers | list[str]:
    """Return the answers, or every unanswered question. Nothing is defaulted, inferred, or taken from AI."""
    missing = []
    for d in CorrespondenceDimension:
        if _blank(values.get(f"finding:{d.name}")):
            missing.append(f"{d.label}: finding")
        if _blank(values.get(f"reason:{d.name}")):
            missing.append(f"{d.label}: reason")
    for c in CrossCuttingCheckName:
        if _blank(values.get(f"check_note:{c.name}")):
            missing.append(f"{c.label} check: note")
    if _blank(values.get("sufficiency")):
        missing.append("Evidence sufficiency")
    if _blank(values.get("revalidation")):
        missing.append("BAEC revalidation")
    if _blank(values.get("reviewer")):
        missing.append("Reviewer name")
    if missing:
        return missing
    try:
        findings = tuple(DimensionAssessment(d, DimensionFinding(values[f"finding:{d.name}"]),
                                             tuple(values.get(f"evidence:{d.name}") or ()), values[f"reason:{d.name}"])
                         for d in CorrespondenceDimension)
        checks = tuple(CrossCuttingCheck(c, values[f"check_note:{c.name}"], tuple(values.get(f"check_evidence:{c.name}") or ()),
                                         frozenset(CorrespondenceDimension[n] for n in values.get(f"check_dimensions:{c.name}") or ()))
                       for c in CrossCuttingCheckName)
    except (ValueError, KeyError) as error:
        raise ReviewInconsistentError(f"an answer is not in the locked vocabulary: {error}") from error
    triggers = tuple(line for line in (values.get("triggers") or "").splitlines() if line.strip())
    return ReviewAnswers(findings, checks, SufficiencyFinding(values["sufficiency"]),
                         values["revalidation"] == "Required", triggers, values["reviewer"])


def failure_message(error: BaseException) -> tuple[str, str]:
    """(kind, operator text). Integrity failures are never presented as a review outcome."""
    if isinstance(error, review.StaleReviewContextError):
        return ("stale", "The stored evidence changed after you opened this review packet. Nothing was recorded. "
                         "Reopen the packet and review the current record.")
    if isinstance(error, review.ReviewContextError):
        return ("context", f"The review context could not be opened. Nothing was recorded. {error}")
    if isinstance(error, ReviewInconsistentError):
        return ("inconsistent", f"The review does not satisfy the locked review rules. Nothing was recorded. {error}")
    if isinstance(error, store.DuplicateRecordError):
        return ("duplicate", f"A review with this identifier already exists. Nothing was overwritten. {error}")
    if isinstance(error, review.AdvisoryProposalRefused):
        return ("advisory", f"No advisory artifact is attached. {error}")
    if isinstance(error, store.Engine2PersistenceError):
        return ("integrity", "INTEGRITY FAILURE: a stored record failed verification. Nothing was recorded, and this "
                             f"is not a review result. {error}")
    raise error
