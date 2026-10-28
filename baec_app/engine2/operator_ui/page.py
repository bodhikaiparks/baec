"""BAEC Engine 2 operator workflow (Streamlit research prototype).

    streamlit run streamlit_engine2_app.py

One fixed synthetic monitoring context (demo.SELECTION), walked through in order: overview, monitoring context,
evidence, candidate, review packet, human review, result. Every authoritative read is the Stage I review packet
(plus the Stage D authorized-source reader); the only write is Stage I submit_human_review, inside the submit
button's callback, against the context_digest captured when the operator opened the packet. The locked
classifier derives the result; no control submits an outcome.

Each operator's store is a private in-memory database, kept between reruns as its serialized image in the
Streamlit session and discarded when the session ends. An AI advisory artifact is shown only if the caller
passes one explicitly; the page never calls a model or a provider. Stored text is rendered as plain text.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import streamlit as st

from .. import ai_persistence as ai_store
from .. import persistence as store
from .. import review
from ..domain import CorrespondenceDimension, CorrespondenceOutcome, CrossCuttingCheckName
from . import demo, views

PAGE_TITLE = "BAEC Engine 2: Monitoring + Correspondence"
BANNER = "Research Prototype · Synthetic Data Only"
STEPS = ("Overview", "Monitoring context", "Evidence", "Candidate", "Review packet", "Human review", "Result")
SELECTION = demo.SELECTION


@dataclass(frozen=True)
class AdvisorySource:
    """An already-stored Stage G artifact, named explicitly by the caller. Read only; never created here."""

    image: bytes
    attempt_id: str
    artifact_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _packet(connection) -> review.ReviewPacket:
    return review.build_review_packet(connection, monitoring_plan_id=SELECTION.monitoring_plan_id,
                                      plan_version=SELECTION.plan_version, baec_id=SELECTION.baec_id,
                                      candidate_id=SELECTION.candidate_id, activation_id=SELECTION.activation_id)


def _show_failure(error: BaseException) -> None:
    kind, text = views.failure_message(error)
    (st.error if kind in ("integrity", "context", "stale") else st.warning)(text)


# --- the submit callback: the only write -----------------------------------------------------------------------


def _answers() -> dict:
    state = st.session_state
    values = {"sufficiency": state.get("e2_sufficiency"), "revalidation": state.get("e2_revalidation"),
              "triggers": state.get("e2_triggers"), "reviewer": state.get("e2_reviewer")}
    for d in CorrespondenceDimension:
        values[f"finding:{d.name}"] = state.get(f"e2_finding_{d.name}")
        values[f"evidence:{d.name}"] = state.get(f"e2_evidence_{d.name}")
        values[f"reason:{d.name}"] = state.get(f"e2_reason_{d.name}")
    for c in CrossCuttingCheckName:
        values[f"check_note:{c.name}"] = state.get(f"e2_check_note_{c.name}")
        values[f"check_evidence:{c.name}"] = state.get(f"e2_check_evidence_{c.name}")
        values[f"check_dimensions:{c.name}"] = state.get(f"e2_check_dimensions_{c.name}")
    return values


def _submit(clock: Callable[[], datetime]) -> None:
    state = st.session_state
    state.pop("e2_error", None)
    state.pop("e2_missing", None)
    try:
        answers = views.collect_answers(_answers())
    except Exception as error:
        state["e2_error"] = views.failure_message(error)
        return
    if isinstance(answers, list):
        state["e2_missing"] = answers
        return
    context = state.get("e2_form_context")
    if context is None:
        state["e2_error"] = ("context", "Open the human review form before submitting. Nothing was recorded.")
        return
    connection = demo.open_image(state["e2_image"])
    try:
        packet = _packet(connection)
        when = clock()
        recorded = review.submit_human_review(
            connection, monitoring_plan_id=SELECTION.monitoring_plan_id, plan_version=SELECTION.plan_version,
            baec_id=SELECTION.baec_id, candidate_id=SELECTION.candidate_id, activation_id=SELECTION.activation_id,
            context_digest=context[-1], review_id=views.next_review_id(packet),
            findings=answers.findings, checks=answers.checks, sufficiency=answers.sufficiency,
            reviewer=answers.reviewer, reviewed_at=when, baec_revalidation_required=answers.baec_revalidation_required,
            revalidation_triggers=answers.revalidation_triggers, recorded_at=when, recorded_by=answers.reviewer)
        state["e2_image"] = connection.serialize()
        state["e2_result"] = (recorded.review.review_id, recorded.outcome.value)
        _retire_draft(context)
        state["e2_step"] = "Result"
    except Exception as error:
        state["e2_error"] = views.failure_message(error)
    finally:
        connection.close()


# --- review drafts: presentation state only -----------------------------------------------------------------
#
# Streamlit forgets a widget's value when the widget is not rendered, so the form's answers are also kept in
# e2_drafts, keyed by views.draft_context (plan, version, BAEC, candidate, activation, context_digest). The form
# restores only the draft of its own context; a changed context starts blank and never inherits answers.


def _retire_draft(context) -> None:
    state = st.session_state
    state.get("e2_drafts", {}).pop(context, None)
    for key in views.DRAFT_KEYS:
        state.pop(key, None)
    state.pop("e2_form_context", None)
    state.pop("e2_context_notice", None)


def _bind_form(packet: review.ReviewPacket) -> tuple:
    """Bind the form to the exact current context and restore that context's draft, if any."""
    state = st.session_state
    bound = state.setdefault("e2_packet_digest", packet.context_digest)
    if bound != packet.context_digest:
        state["e2_context_notice"] = True
        state["e2_packet_digest"] = packet.context_digest
    context = views.draft_context(packet, state["e2_packet_digest"])
    draft = state.setdefault("e2_drafts", {}).get(context, {})
    if state.get("e2_form_context") != context:
        for key in views.DRAFT_KEYS:
            state.pop(key, None)
        state["e2_form_context"] = context
    for key, value in draft.items():
        if key not in state:
            state[key] = list(value) if isinstance(value, list) else value
    return context


def _save_draft(context) -> None:
    state = st.session_state
    state["e2_drafts"][context] = {key: list(state[key]) if isinstance(state[key], list) else state[key]
                                   for key in views.DRAFT_KEYS if key in state}


def _reopen() -> None:
    st.session_state.pop("e2_packet_digest", None)
    st.session_state.pop("e2_error", None)


def _go(step: str) -> None:
    st.session_state["e2_step"] = step


# --- steps ---------------------------------------------------------------------------------------------------


def _overview() -> None:
    st.header("Engine 2: Monitoring + Correspondence")
    st.subheader(views.QUESTION)
    st.write("Engine 2 watches authorized sources for evidence about one buyer-articulated condition that a human "
             "has already verified in Engine 1. When evidence may bear on that condition, it prepares a review "
             "packet, and a human decides, dimension by dimension, whether the evidence corresponds.")
    st.write("It does not rank or rate accounts, infer buyer intent, predict purchases, or contact anyone.")
    st.info(f"Demo context: {SELECTION.account_name}, supplier {SELECTION.supplier_name}. Every record is synthetic.")
    st.button("Open the monitoring context", key="e2_start", on_click=_go, args=("Monitoring context",))


def _context(connection, packet: review.ReviewPacket) -> None:
    plan, reading = packet.plan, packet.active_plan.currentness
    st.header("Monitoring context")
    st.caption("Monitoring watches for evidence. It has not shown that the buyer's condition occurred.")
    st.text(f"Demo selection (fixed): {SELECTION.account_name}")
    st.text(f"BAEC: {plan.baec_id}\nMonitoring Plan: {plan.monitoring_plan_id}, version {plan.plan_version}\n"
            f"Activation: {packet.activation_id}, ACTIVATED (Engine 1 reported the BAEC "
            f"{reading.staleness_status.value}, record {reading.record_reference})")
    st.subheader("Buyer-defined condition (exact)")
    st.text(plan.condition_reference)
    st.subheader("Plan terms")
    t = plan.threshold
    st.text(f"Target entities: {', '.join(plan.target_entities)}\n"
            f"Threshold: {'none stated' if t is None else f'{t.comparator.value} {t.value} {t.unit}'}\n"
            f"Timing: {plan.timing_context or 'none stated'}\n"
            f"Plan facts: {', '.join(sorted(plan.plan_fact_ids)) or 'none'}\n"
            f"Plan rules: {', '.join(sorted(plan.plan_rule_ids)) or 'none'}")
    st.text("Required dimensions: " + ", ".join(d.label for d in CorrespondenceDimension if d in plan.required_dimensions))
    st.text("Not applicable: " + ", ".join(d.label for d in CorrespondenceDimension if d in plan.not_applicable_dimensions))
    st.subheader("Authorized sources")
    for source_id in sorted(plan.authorized_source_ids):
        s = store.load_authorized_source(connection, source_id)
        st.text(f"{s.source_id}: {s.source_type}, {s.owner_or_publisher}\n  basis: {s.legitimate_basis}\n"
                f"  access: {s.access_method}; capture: {s.permitted_capture}"
                + (f"\n  limitations: {s.known_limitations}" if s.known_limitations else ""))
    st.button("Inspect the evidence", key="e2_to_evidence", on_click=_go, args=("Evidence",))


def _observation(row: views.EvidenceRow) -> None:
    with st.container(border=True):
        history = f"; {row.status.lower()} by {row.superseded_by} ({row.basis})" if row.superseded_by else ""
        st.text(f"{row.observation_id} [{row.status}]{history}\n{row.source_id} ({row.source_type}), {row.provenance}, "
                f"observed {row.observed_at}" + (f", effective {row.effective_at}" if row.effective_at else ""))
        st.text(row.exact_text)


def _evidence(packet: review.ReviewPacket) -> None:
    rows = views.evidence_rows(packet)
    st.header("Evidence")
    st.subheader("Currently usable evidence")
    st.caption("Usability comes from the locked supersession rules: a superseded or retracted Observation is kept but "
               "cannot support a current finding.")
    for row in rows:
        if row.status == "USABLE":
            _observation(row)
    st.subheader("Stored historical record")
    st.caption("Every Observation ever captured for this plan version, in recording order. Nothing is edited or hidden.")
    for row in rows:
        _observation(row)
    st.subheader("Derived Measurements")
    for m in views.measurement_rows(packet):
        with st.container(border=True):
            st.text(f"{m.measurement_id} [{m.status}]: {m.output}\nformula: {m.formula}")
            for observation_id, quantity, value, unit in m.inputs:
                st.text(f"  input {quantity} = {value} {unit} from {observation_id}")
    st.subheader("Refused source items")
    st.caption("Items from sources not authorized for this plan. They are never evidence.")
    for r in packet.ledger.rejected_items:
        st.text(f"{r.item_id}: {r.reason}")
    st.button("Inspect the candidate", key="e2_to_candidate", on_click=_go, args=("Candidate",))


def _candidate(packet: review.ReviewPacket) -> None:
    st.header("Correspondence candidate")
    st.info(views.CANDIDATE_MEANING)
    st.text(f"Candidate {packet.candidate_id} for {packet.baec_id}, plan {packet.monitoring_plan_id} "
            f"v{packet.plan_version}")
    for s in packet.candidate.signal_candidates:
        with st.container(border=True):
            st.text(f"Signal {s.signal_candidate_id}, selected by {s.selected_by}\n"
                    f"Observations: {', '.join(s.observation_ids)}\n"
                    "Dimensions the selector marked as possibly relevant (not findings): "
                    + (", ".join(d.label for d in CorrespondenceDimension if d in s.possibly_relevant_dimensions) or "none"))
    st.button("Open the review packet", key="e2_to_packet", on_click=_go, args=("Review packet",))


def _advisory(packet: review.ReviewPacket, advisory: AdvisorySource | None) -> None:
    with st.container(border=True):
        st.subheader(views.AI_PANEL_TITLE)
        if advisory is None:
            st.caption(views.NO_ADVISORY)
            return
        connection = ai_store.open_ai_audit_database(":memory:")
        try:
            connection.deserialize(advisory.image)
            ai_store.require_ai_schema(connection)
            attached = review.attach_advisory_proposal(packet, connection, condition=demo.CONDITION,
                                                       attempt_id=advisory.attempt_id, artifact_id=advisory.artifact_id)
        except review.AdvisoryProposalRefused as refused:
            st.caption(f"{views.NO_ADVISORY} ({refused})")
            return
        except (ai_store.AIStoreError, sqlite3.DatabaseError) as failed:
            st.error(f"INTEGRITY FAILURE in the AI audit store; no advisory artifact is shown. {failed}")
            return
        finally:
            connection.close()
        p = attached.proposal
        st.caption(views.AI_PANEL_NOTE)
        st.text(f"Origin {p.origin.value}; artifact {attached.artifact_id}; attempt {attached.attempt_id}; "
                f"model {p.provider}/{p.model}")
        for proposal in p.dimension_proposals:
            cited = ", ".join(c.reference_id for c in proposal.evidence) or "none"
            basis = f", basis {proposal.unresolved_basis.value}" if proposal.unresolved_basis else ""
            st.text(f"AI proposal | {proposal.dimension.label}: {proposal.proposal.value}{basis}; cites {cited}\n"
                    f"  {proposal.rationale}")
        for c in p.cross_cutting_observations:
            st.text(f"AI observation | {c.check.label}: {c.concern}")
        for q in p.unresolved_questions:
            st.text(f"AI open question | {q}")
        st.text(p.prohibited_inference_acknowledgement)


def _review_packet(packet: review.ReviewPacket, advisory: AdvisorySource | None) -> None:
    state = st.session_state
    state.setdefault("e2_packet_digest", packet.context_digest)
    st.header("Human review packet")
    if state["e2_packet_digest"] != packet.context_digest:
        st.error("The stored record changed after you opened this packet. A review against the old packet will be "
                 "refused.")
    st.button("Reopen the packet with the current record", key="e2_reopen", on_click=_reopen)
    st.text(f"Context token: {state['e2_packet_digest']}\nPlan {packet.monitoring_plan_id} v{packet.plan_version}, "
            f"BAEC {packet.baec_id}, activation {packet.activation_id}, candidate {packet.candidate_id}")
    st.subheader("Correspondence dimensions")
    for d in CorrespondenceDimension:
        st.text(f"{d.label}: {views.plan_requirement(packet, d)}")
    st.subheader("Cross-cutting checks")
    st.text(", ".join(c.label for c in CrossCuttingCheckName))
    st.subheader("Evidence in this packet")
    for row in views.evidence_rows(packet):
        _observation(row)
    for m in views.measurement_rows(packet):
        st.text(f"{m.measurement_id} [{m.status}]: {m.output} from "
                f"{', '.join(sorted({i[0] for i in m.inputs}))}")
    for r in packet.ledger.rejected_items:
        st.text(f"Refused: {r.item_id}: {r.reason}")
    _history(packet)
    _advisory(packet, advisory)
    st.button("Make the human judgments", key="e2_to_review", on_click=_go, args=("Human review",))


def _history(packet: review.ReviewPacket) -> None:
    st.subheader("Human review history")
    if not packet.review_history:
        st.caption("No human review has been recorded for this candidate.")
    for h in packet.review_history:
        st.text(f"{h.review.review_id} by {h.review.reviewer} at {h.review.reviewed_at.isoformat()}: {h.outcome.value}")


def _human_review(packet: review.ReviewPacket, clock: Callable[[], datetime]) -> None:
    state = st.session_state
    context = _bind_form(packet)
    st.header("Human judgments")
    st.caption("Every answer is yours. Nothing is pre-filled, and no AI output is copied here. The result is derived "
               "by the locked rule from these answers; it cannot be chosen directly. Your draft is kept while you "
               "move between steps.")
    if state.get("e2_context_notice"):
        st.error("The evidence context changed since your draft was started. Your earlier answers are not applied to "
                 "the changed record; this is a fresh draft for the current record.")
    citations, check_citations = views.finding_citations(packet), views.check_citations(packet)
    for d in CorrespondenceDimension:
        with st.container(border=True):
            st.text(f"{d.label} ({views.plan_requirement(packet, d)})")
            st.selectbox("Finding", views.FINDING_OPTIONS, index=None, placeholder="Choose a finding",
                         key=f"e2_finding_{d.name}")
            st.multiselect("Evidence cited", list(citations), format_func=citations.get, key=f"e2_evidence_{d.name}")
            st.text_area("Reason", key=f"e2_reason_{d.name}")
    st.subheader("Cross-cutting checks")
    for c in CrossCuttingCheckName:
        with st.container(border=True):
            st.text(c.label)
            st.text_area("Note", key=f"e2_check_note_{c.name}")
            st.multiselect("Evidence", list(check_citations), format_func=check_citations.get,
                           key=f"e2_check_evidence_{c.name}")
            st.multiselect("Affected dimensions", [d.name for d in CorrespondenceDimension],
                           format_func=lambda n: CorrespondenceDimension[n].label, key=f"e2_check_dimensions_{c.name}")
    st.radio("Is the evidence sufficient for a human correspondence finding?", views.SUFFICIENCY_OPTIONS, index=None,
             key="e2_sufficiency")
    st.radio("BAEC revalidation", views.REVALIDATION_OPTIONS, index=None, key="e2_revalidation")
    st.text_area("Revalidation triggers (one per line; only when revalidation is required)", key="e2_triggers")
    st.text_input("Reviewer name", key="e2_reviewer")
    if state.get("e2_missing"):
        st.warning("Answer every question before submitting. Unanswered: " + "; ".join(state["e2_missing"]))
    if state.get("e2_error"):
        kind, text = state["e2_error"]
        (st.error if kind in ("integrity", "context", "stale") else st.warning)(text)
    _save_draft(context)
    st.button("Submit the human review", key="e2_submit", type="primary", on_click=_submit, args=(clock,))


def _result(packet: review.ReviewPacket) -> None:
    st.header("Result")
    result = st.session_state.get("e2_result")
    if result is None:
        st.caption("No review has been submitted in this session.")
    else:
        review_id, outcome = result
        st.subheader(outcome)
        st.text(f"Recorded review {review_id}. Derived by the locked Engine 2 rule from the human judgments.")
        st.write(views.OUTCOME_EXPLANATIONS[CorrespondenceOutcome(outcome)])
    st.warning(views.RESULT_BOUNDARY)
    _history(packet)


# --- page --------------------------------------------------------------------------------------------------


def main(*, advisory: AdvisorySource | None = None, clock: Callable[[], datetime] = _utc_now) -> None:
    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
    state = st.session_state
    if "e2_image" not in state:
        state["e2_image"] = demo.new_demo_image()
    st.caption(BANNER)
    step = st.sidebar.radio("Workflow", STEPS, key="e2_step")
    st.sidebar.caption(views.BOUNDARY)
    if step == "Overview":
        _overview()
    else:
        connection = demo.open_image(state["e2_image"])
        try:
            packet = _packet(connection)
            if step == "Monitoring context":
                _context(connection, packet)
            elif step == "Evidence":
                _evidence(packet)
            elif step == "Candidate":
                _candidate(packet)
            elif step == "Review packet":
                _review_packet(packet, advisory)
            elif step == "Human review":
                _human_review(packet, clock)
            else:
                _result(packet)
        except (review.ReviewContextError, store.Engine2PersistenceError) as error:
            _show_failure(error)
        finally:
            connection.close()
    st.divider()
    st.caption(views.BOUNDARY)
