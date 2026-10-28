"""Engine 3 adapter: eligibility from stored upstream records, grounding from human disclosure choices,
validation only through the released Engine 3 validator (baec_app.engine3.validate_case).

Engine 3 V1 is a pure validator: it takes an UpstreamEligibility, a ContextState, grounding items, and an
artifact, and it has no persistence. This adapter derives those inputs from what Engines 1 and 2 actually
store, fail closed:

* outcome            the outcome the locked Engine 2 classifier derives for the stored review;
* baec_current       Engine 1's stored staleness, read through the Stage K handoff;
* revalidation       the review's BAEC_REVALIDATION_REQUIRED flag;
* binding            preparation is bound to one explicitly selected Engine 2 review (review_id), with its
                     candidate, plan version, and activation. Nothing here chooses a review by recency, and a
                     later review of the same evidence never replaces the bound one;
* review_superseded  Engine 2 records no supersession of a review (reviews are append-only history), so this is
                     False; later reviews of the same evidence are reported to the human instead (later_reviews);
* evidence usable    every Observation or Measurement the review's decided findings cite is still usable in the
                     current Engine 2 ledger (not superseded or retracted since);
* lineage            every one of the above loaded without error; any failure makes lineage unreconstructable;
* request present    a Preparation Request exists (a separate human action);
* human block        the latest human block decision for the bound review is BLOCK (Engine 3's existing
                     human_preparation_block input, refused as E3C-IN-007).

Context state compares the stored request's snapshot with the current records, and the request's context
digest with a fresh EvaluationContext digest, so a stale request is refused by Engine 3 itself.

Origin compatibility (V1 limitation): Engine 3's ArtifactOrigin has only AI_INFERENCE and HUMAN_EDITED. Output of
the deterministic console template is machine-produced, non-human text, so it is submitted to the validator under
AI_INFERENCE, the existing validator-compatible value. Each console preparation separately stores
console_generator_origin DETERMINISTIC_CONSOLE_TEMPLATE_V1, engine3_validator_origin AI_INFERENCE, and the fixed
note E3-ORIGIN-COMPAT-V1 (console_store.py). No model is used; the console never says otherwise.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from baec_app.domain.enums import StalenessStatus
from baec_app.engine2 import persistence as store
from baec_app.engine2.domain import CorrespondenceOutcome, DimensionFinding
from baec_app.engine3 import validate_case
from baec_app.engine3.enums import (
    ActionAttempt,
    ArtifactOrigin,
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    ReferenceUseMode,
    SourceIdentityDisclosure,
)
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.models import (
    ClaimField,
    ContextState,
    DraftSegment,
    GroundingItem,
    PreparationArtifact,
    UpstreamEligibility,
    ValidationCase,
    ValidationReport,
)
from baec_app.engine3.offline_evaluation import EvaluationContext
from baec_app.engine3.proposal_contract import SEGMENT_SEPARATOR, ProposedArtifact
from baec_app.engine3.reason_codes import ReasonCode

from . import engine2_adapter as e2a
from .console_store import ENGINE3_VALIDATOR_ORIGIN, display_time
from .deterministic_preparation import G_CORRESPONDENCE, G_STATEMENT, G_THRESHOLD, G_TIMING, g_evidence, g_measurement

# The template's output is validated under Engine 3's machine-proposal origin; see the module docstring.
TEMPLATE_VALIDATOR_ORIGIN = ArtifactOrigin(ENGINE3_VALIDATOR_ORIGIN)

ARTIFACT_LABELS = {
    ArtifactType.INTERNAL_WHY_NOW_SUMMARY: "Why now",
    ArtifactType.FOLLOW_UP_EMAIL: "Email draft",
    ArtifactType.CALL_OPENING: "Call opening",
    ArtifactType.DISCOVERY_QUESTION_SET: "Discovery questions",
}
DISPOSITION_LABELS = {
    ExternalReferenceDisposition.DIRECT_REFERENCE_ALLOWED: "May be stated to the buyer",
    ExternalReferenceDisposition.PARAPHRASE_ALLOWED: "May inform wording, not quoted",
    ExternalReferenceDisposition.INTERNAL_ONLY: "Internal only",
    ExternalReferenceDisposition.UNKNOWN: "Not decided (treated as internal)",
}
SOURCE_IDENTITY_LABELS = {
    SourceIdentityDisclosure.ALLOWED: "Source may be named",
    SourceIdentityDisclosure.NOT_ALLOWED: "Do not name the source",
    SourceIdentityDisclosure.UNKNOWN: "Not decided",
}

REASON_TEXT = {
    ReasonCode.OUTCOME_NOT_HUMAN_VERIFIED: "Correspondence has not been human-verified.",
    ReasonCode.BAEC_NOT_CURRENT: "The buyer's condition is no longer current in Engine 1.",
    ReasonCode.BAEC_REVALIDATION_REQUIRED: "The reviewer flagged that the buyer's condition needs revalidation.",
    ReasonCode.CORRESPONDENCE_REVIEW_SUPERSEDED: "A later correspondence review has replaced this one.",
    ReasonCode.REQUIRED_EVIDENCE_NOT_USABLE: "Evidence the review relied on has since been corrected or retracted.",
    ReasonCode.UPSTREAM_LINEAGE_UNRECONSTRUCTABLE: "The upstream records could not be reconstructed.",
    ReasonCode.HUMAN_PREPARATION_BLOCK: "A human has blocked preparation for this account.",
    ReasonCode.PREPARATION_REQUEST_REQUIRED: "Preparation needs a separate human Preparation Request.",
    ReasonCode.EVIDENCE_CONTEXT_CHANGED: "The evidence changed after preparation was requested.",
    ReasonCode.DISCLOSURE_CONTEXT_CHANGED: "Disclosure permissions changed after preparation was requested.",
    ReasonCode.BAEC_CURRENTNESS_CHANGED: "The condition's currentness changed after preparation was requested.",
    ReasonCode.CORRESPONDENCE_REVIEW_CHANGED: "The correspondence review changed after preparation was requested.",
    ReasonCode.PREPARATION_REQUEST_CHANGED: "A newer Preparation Request replaced this one.",
    ReasonCode.CONTEXT_DIGEST_MISMATCH: "The recorded context no longer matches the current records.",
    ReasonCode.FACT_INTERNAL_ONLY: "Uses a fact marked internal only.",
    ReasonCode.FACT_PERMISSION_UNKNOWN: "Uses a fact whose external use has not been decided.",
    ReasonCode.SOURCE_IDENTITY_NOT_PERMITTED: "Names a source that may not be named.",
    ReasonCode.BUYER_STATEMENT_NOT_PERMITTED: "Refers to the buyer's statement without permission to do so.",
    ReasonCode.DIRECT_QUOTE_EXCEEDS_PARAPHRASE_PERMISSION: "Quotes the buyer where only paraphrase is permitted.",
    ReasonCode.SPECIFICITY_EXCEEDS_PERMISSION: "States a fact more specifically than permitted.",
    ReasonCode.ADVERSITY_CONTEXT_REVIEW_REQUIRED: "Refers to an adverse event whose context has not been reviewed.",
    ReasonCode.FACTUAL_ASSERTION_UNGROUNDED: "Makes a factual statement with no recorded support.",
    ReasonCode.THRESHOLD_INVENTED: "States a threshold that is not in the record.",
    ReasonCode.DATE_INVENTED: "States a date that is not in the record.",
    ReasonCode.ROLE_OR_RELATIONSHIP_INVENTED: "States a role or relationship that is not in the record.",
    ReasonCode.SELLER_CAPABILITY_CLAIM_UNGROUNDED: "Makes a product or capability claim with no approved source.",
    ReasonCode.COMPETITOR_CLAIM_UNGROUNDED: "Makes a competitor claim with no approved source.",
    ReasonCode.QUESTION_CONTAINS_UNSUPPORTED_PREMISE: "Asks a question that assumes something unsupported.",
    ReasonCode.EVENT_MAGNITUDE_UNGROUNDED: "States a magnitude that does not match the record.",
    ReasonCode.SOURCE_CONTENT_TREATED_AS_INSTRUCTION: "Treats source content as an instruction.",
    ReasonCode.APPROXIMATE_EVIDENCE_OVERSTATED_AS_EXACT: "Presents approximate evidence as exact.",
    ReasonCode.CURRENT_EVALUATION_ASSERTED: "Asserts that the buyer is currently evaluating.",
    ReasonCode.PURCHASE_OR_SWITCH_INTENT_ASSERTED: "Asserts purchase or switching intent.",
    ReasonCode.OPPORTUNITY_STATUS_ASSERTED: "Treats correspondence as an opportunity.",
    ReasonCode.URGENCY_INVENTED: "Invents urgency or a deadline.",
    ReasonCode.BUYER_COMMERCIAL_CHOICE_PRESCRIBED: "Tells the buyer what commercial choice to make.",
    ReasonCode.PROHIBITED_SCORE_OR_PROBABILITY: "Requests a score or probability.",
    ReasonCode.HUMAN_EDIT_REQUIRES_REVALIDATION: "The human edit must be validated again.",
    ReasonCode.HUMAN_EDIT_UNGROUNDED: "The human edit makes a factual statement with no recorded support.",
    ReasonCode.OUTREACH_EXECUTION_NOT_SUPPORTED: "Outreach execution is not supported.",
    ReasonCode.CRM_ACTION_NOT_SUPPORTED: "CRM actions are not supported.",
    ReasonCode.ACCOUNT_STATE_CHANGE_NOT_SUPPORTED: "Engine 3 never changes account state.",
    ReasonCode.EMPIRICAL_VALIDATION_CLAIM_PROHIBITED: "Claims empirical validation.",
}


def reason_text(code_value: str) -> str:
    for code in ReasonCode:
        if code.value == code_value:
            return REASON_TEXT.get(code, code.name)
    return code_value


def _day(value: datetime) -> str:
    """The calendar date the console UI shows for the same instant (ui_common.when uses the same conversion)."""
    return f"{display_time(value):%d %B %Y}".lstrip("0")


# --- upstream snapshot ----------------------------------------------------------------------------------------


def upstream_snapshot(engine1_reader, e2, review_id: str, *, blocked: bool = False) -> dict:
    """Everything Engine 3 may read about one explicitly bound review, from stored records. Fail closed."""
    snap: dict = {"review_id": review_id, "lineage_reconstructable": False, "outcome": "UNRECONSTRUCTABLE",
                  "lineage_error": None, "human_preparation_block": bool(blocked)}
    try:
        review = store.load_review(e2, review_id)
        candidate = review.candidate
        snap["candidate_id"] = candidate.candidate_id
        snap["outcome"] = review.outcome.value
        snap["revalidation_required"] = review.baec_revalidation_required
        snap["reviewed_on"] = _day(review.reviewed_at)
        snap["reviewed_by"] = review.reviewer
        snap["review_identity"] = f"{review_id}|{review.outcome.value}|{review.reviewed_at.isoformat()}"
        current = e2a.packet(e2, candidate.candidate_id)
        snap["monitoring_plan_id"] = current.plan.monitoring_plan_id
        snap["plan_version"] = current.plan.plan_version
        snap["activation_id"] = current.activation_id
        history = store.review_ids_for_candidate(e2, candidate.candidate_id)
        if review_id not in history:
            raise store.Engine2IntegrityError(f"review {review_id} is not in its candidate's history")
        snap["review_superseded"] = False  # Engine 2 has no review supersession; recency is never used
        snap["later_reviews"] = [{"review_id": r, "outcome": store.load_review(e2, r).outcome.value}
                                 for r in history[history.index(review_id) + 1:]]
        usable = current.usable_observation_ids | current.usable_measurement_ids | current.plan.reference_ids
        cited = {r for f in review.findings if f.finding in (DimensionFinding.SUPPORTED, DimensionFinding.CONTRADICTED)
                 for r in f.evidence_refs}
        snap["required_evidence_usable"] = cited <= usable
        snap["e2_context_digest"] = current.context_digest
        handoff = e2a.read_handoff(engine1_reader, current.plan.baec_id)
        snap["baec_id"] = handoff.baec_id
        snap["account_id"] = handoff.account_id
        snap["staleness"] = handoff.staleness_status.value
        snap["handoff_digest"] = handoff.digest
        snap["baec_current"] = (handoff.staleness_status is StalenessStatus.CURRENT
                                and current.plan.engine1_currentness_reference == handoff.engine1_reference)
        snap["buyer_statement"] = handoff.buyer_exact_statement
        snap["threshold_text"] = handoff.stringency.verbatim_text if handoff.stringency else None
        snap["timing_text"] = handoff.stringency.timing_text if handoff.stringency else None
        mine = candidate.observation_ids
        snap["evidence"] = [
            {"id": o.observation_id, "text": o.exact_evidence_content, "source_type": o.source_type,
             "observed_on": _day(o.observed_at), "provenance": o.provenance.value}
            for o in current.ledger.observations
            if o.observation_id in mine and o.observation_id in current.usable_observation_ids]
        snap["measurements"] = [
            {"id": m.measurement_id, "exact_value": f"{m.output_value}{m.output_unit}"}
            for m in current.ledger.measurements
            if m.source_observation_ids <= mine and m.measurement_id in current.usable_measurement_ids]
        snap["lineage_reconstructable"] = True
    except Exception as error:  # noqa: BLE001 - any failure to rebuild lineage fails closed
        snap["lineage_reconstructable"] = False
        snap["lineage_error"] = f"{type(error).__name__}: {error}"
    return snap


def upstream_eligibility(snap: dict, *, request_present: bool) -> UpstreamEligibility:
    if not snap["lineage_reconstructable"]:
        return UpstreamEligibility(outcome=snap.get("outcome") or "UNRECONSTRUCTABLE", lineage_reconstructable=False,
                                   human_preparation_block=snap.get("human_preparation_block", False),
                                   preparation_request_present=request_present)
    return UpstreamEligibility(
        outcome=snap["outcome"], baec_current=snap["baec_current"], revalidation_required=snap["revalidation_required"],
        review_superseded=snap["review_superseded"], required_evidence_usable=snap["required_evidence_usable"],
        lineage_reconstructable=True, human_preparation_block=snap["human_preparation_block"],
        preparation_request_present=request_present)


def binding(snap: dict) -> dict:
    """The exact Engine 2 records a preparation is bound to."""
    return {key: snap.get(key) for key in ("review_id", "candidate_id", "monitoring_plan_id", "plan_version",
                                           "activation_id", "outcome", "reviewed_on", "reviewed_by")}


def eligibility_report(snap: dict, *, request_present: bool) -> ValidationReport:
    """Engine 3's own eligibility decision, through the public validator with no artifact."""
    return validate_case(ValidationCase(upstream=upstream_eligibility(snap, request_present=request_present)))


# --- grounding ------------------------------------------------------------------------------------------------


def grounding_rows(snap: dict) -> list[dict]:
    """The facts a human assigns a disclosure decision to, before any preparation exists."""
    rows = [{"id": G_STATEMENT, "label": "The buyer's earlier statement", "value": snap["buyer_statement"],
             "buyer_statement": True}]
    if snap.get("threshold_text"):
        rows.append({"id": G_THRESHOLD, "label": "The buyer's threshold", "value": snap["threshold_text"]})
    if snap.get("timing_text"):
        rows.append({"id": G_TIMING, "label": "The buyer's timing", "value": snap["timing_text"]})
    for e in snap["evidence"]:
        rows.append({"id": g_evidence(e["id"]), "label": f"Evidence: {e['source_type']}", "value": e["text"],
                     "evidence": True})
    for m in snap["measurements"]:
        rows.append({"id": g_measurement(m["id"]), "label": "Derived measurement", "value": m["exact_value"]})
    return rows


def grounding_items(snap: dict, request: dict) -> tuple[GroundingItem, ...]:
    """Grounding items with the human's disclosure decisions. Anything undecided is UNKNOWN (Engine 3 default)."""
    dispositions = request.get("dispositions", {})
    identities = request.get("source_identity", {})
    adversity = request.get("adversity", {})
    items = []
    for row in grounding_rows(snap):
        flags = adversity.get(row["id"], {})
        items.append(GroundingItem(
            grounding_item_id=row["id"], exact_value=row["value"],
            disposition=ExternalReferenceDisposition(dispositions.get(row["id"], "UNKNOWN")),
            source_identity_disclosure=SourceIdentityDisclosure(identities.get(row["id"], "UNKNOWN")),
            is_buyer_statement=bool(row.get("buyer_statement")),
            adversity_related=bool(flags.get("related", False)),
            adversity_context_reviewed=bool(flags.get("reviewed", False)) if flags.get("related") else True))
    # The correspondence outcome itself reveals monitoring; it is internal grounding only.
    items.append(GroundingItem(G_CORRESPONDENCE, f"Engine 2 review {snap['review_id']} outcome {snap['outcome']}",
                               ExternalReferenceDisposition.INTERNAL_ONLY, SourceIdentityDisclosure.NOT_ALLOWED))
    return tuple(items)


def context_digest(snap: dict, request: dict) -> str:
    return EvaluationContext(upstream_eligibility(snap, request_present=True), ContextState(),
                             grounding_items(snap, request)).digest


def context_state(stored_request: dict, current: dict, *, newer_request_exists: bool) -> ContextState:
    """How the current records differ from the request's snapshot. Engine 3 refuses on any difference."""
    before = stored_request["snapshot"]
    if not current["lineage_reconstructable"]:
        return ContextState(digest_match=False)
    return ContextState(
        evidence_changed=before.get("e2_context_digest") != current.get("e2_context_digest"),
        disclosure_changed=False,  # dispositions are fixed inside the stored request
        baec_currentness_changed=(before.get("staleness"), before.get("handoff_digest"))
        != (current.get("staleness"), current.get("handoff_digest")),
        # The bound review itself: same id, outcome, and time. A later review of the same evidence is not a change.
        review_changed=before.get("review_identity") != current.get("review_identity"),
        request_changed=newer_request_exists,
        digest_match=stored_request["context_digest"] == context_digest(current, stored_request["request"]),
    )


# --- validation -----------------------------------------------------------------------------------------------


def validate(artifact: ProposedArtifact, origin: ArtifactOrigin, upstream: UpstreamEligibility, state: ContextState,
             grounding: tuple[GroundingItem, ...]) -> ValidationReport:
    case = ValidationCase(upstream=upstream, context=state, grounding_items=grounding,
                          artifact=PreparationArtifact(artifact.artifact_id, artifact.audience, origin, artifact.segments),
                          action_attempt=ActionAttempt.NONE, prohibited_score_requested=False,
                          empirical_validation_claim=False)
    return validate_case(case)


def report_to_dict(report: ValidationReport) -> dict:
    return {"allowed": report.allowed,
            "issues": [{"reason_code": i.reason_code.value, "segment_id": i.segment_id} for i in report.issues]}


def structural_refusal(message: str) -> dict:
    return {"allowed": False, "issues": [], "structural_error": message}


# --- (de)serialization of artifacts ---------------------------------------------------------------------------


def segment_to_dict(s: DraftSegment) -> dict:
    data = asdict(s)
    for key in ("claim_type", "semantic_class", "reference_use_mode", "claim_field"):
        data[key] = data[key].value
    data["grounding_refs"] = list(s.grounding_refs)
    data["source_identity_refs"] = list(s.source_identity_refs)
    return data


def segment_from_dict(d: dict) -> DraftSegment:
    return DraftSegment(
        segment_id=d["segment_id"], text=d["text"], claim_type=ClaimType(d["claim_type"]),
        semantic_class=ClaimSemanticClass(d["semantic_class"]), grounding_refs=tuple(d["grounding_refs"]),
        reference_use_mode=ReferenceUseMode(d["reference_use_mode"]),
        source_identity_refs=tuple(d["source_identity_refs"]), claim_field=ClaimField(d["claim_field"]),
        declared_value=d["declared_value"], specificity=d["specificity"],
        question_has_premise=d["question_has_premise"], source_instruction_ref=d["source_instruction_ref"],
        prescribes_buyer_choice=d["prescribes_buyer_choice"])


def artifact_to_dict(a: ProposedArtifact) -> dict:
    return {"artifact_id": a.artifact_id, "artifact_type": a.artifact_type.value, "rendered_text": a.rendered_text,
            "segments": [segment_to_dict(s) for s in a.segments]}


def artifact_from_dict(d: dict) -> ProposedArtifact:
    """Rebuild through the Engine 3 constructor, so stored artifacts are re-validated structurally on load."""
    return ProposedArtifact(d["artifact_id"], ArtifactType(d["artifact_type"]),
                            tuple(segment_from_dict(s) for s in d["segments"]), d["rendered_text"])


def build_edited_artifact(parent: ProposedArtifact, edit_id: str, segments: list[dict]) -> ProposedArtifact:
    """A new HUMAN_EDITED artifact: a new id, the same type, the human's segments. Structure errors propagate."""
    rebuilt = tuple(segment_from_dict(s) for s in segments)
    if not rebuilt:
        raise Engine3ValidationError("an edited draft needs at least one sentence")
    return ProposedArtifact(f"{parent.artifact_id}-{edit_id}", parent.artifact_type, rebuilt,
                            SEGMENT_SEPARATOR.join(s.text for s in rebuilt))


def is_verified(outcome: str) -> bool:
    return outcome == CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE.value
