"""Phase 7E human-review page for persisted AI_DRAFT proposals (Streamlit).

    streamlit run baec_app/interfaces/review_page.py -- --database PATH

The page renders the application's ProposalReview and submits explicit human decisions to
ProposalReviewService. Every rule lives in the application layer; this page holds none. Source interaction
text and all AI-produced text are untrusted and rendered as inert plain text (st.text), never as HTML or
Markdown. Authoritative controls start unset, and AI suggestions are never copied into them except by an
explicit "Use AI suggestion" click, which still finalizes nothing.

A review decision records only that a human reviewed an AI Draft. Accepting a review does not confirm a
BAEC, establish purchase intent, change account state, issue an authorization grant, or contact anyone.
Reviewer identity is self-asserted in this prototype; authentication is out of scope.

Phase 7F-B adds one separate, explicit human action on an accepted review: "Authorize BAEC confirmation",
which asks the application to persist a 15-minute, single-use grant. Authorizing is not confirming: this page
never executes a grant and never creates a BAEC.
"""

from __future__ import annotations

import sys

import streamlit as st

from baec_app.application import SystemClock
from baec_app.application.proposal_authorization import AuthorizationRefused, ProposalAuthorizationService
from baec_app.application.proposal_review import (
    CRITERIA,
    EVIDENCE_PROVENANCE,
    NO_STRINGENCY_STATED,
    CriterionDecision,
    EvidenceSelection,
    ProposalReviewService,
    ReviewDecisions,
    ReviewNotSaved,
    open_review_connection,
    stringency_from_fields,
)
from baec_app.domain.enums import (
    ArticulationOrigin,
    CriterionFinding,
    ElicitationMode,
    ProvenanceCategory,
    ThresholdComparator,
)

BANNER = (
    "Research Prototype • Synthetic Data Only\n\n"
    "BAEC Trigger Intelligence is a demonstration of buyer-defined prospective account monitoring. "
    "It does not predict purchase intent, automatically contact buyers, or validate the BAEC construct."
)
IDENTITY_NOTE = "Reviewer identity is self-asserted in this prototype; authentication is out of scope."
ACCEPT_MEANING = ("Accepting records only that a human finalized this review. It does not confirm a BAEC, "
                  "establish purchase intent, change account state, or authorize anything.")
AUTHORIZATION_NOTE = ("Authorization is a separate human action. It permits one later execution of the BAEC-confirmation "
                      "action for exactly this accepted revision, for 15 minutes after issuance. It does not confirm the BAEC, and no "
                      "confirmation is executed from this page.")
GRANT_LABELS = {"ACTIVE": "Authorization Granted (active)", "EXPIRED": "Authorization expired",
                "SUPERSEDED": "Authorization superseded", "CONSUMED": "Authorization consumed"}
STATE_LABELS = {"OPEN": "AI Draft — Human Review open", "REVIEW_ACCEPTED": "Review Accepted",
                "REVIEW_REJECTED": "Review Rejected"}
BUYER_STATEMENT_CHOICES = ("No exact buyer statement", "Record an exact buyer statement")
STRINGENCY_CHOICES = ("No stringency stated", "Record stringency")
NORMALIZATION_CHOICES = ("No final normalization", "Provide final text")
FIELDS = (("condition", "Final normalized condition", "ai_normalized_condition"),
          ("evaluation_link", "Final normalized evaluation link", "ai_normalized_evaluation_link"))


def _database_path() -> str | None:
    configured = st.session_state.get("baec_database")
    if configured:
        return configured
    arguments = sys.argv[1:]
    if "--database" in arguments and arguments.index("--database") + 1 < len(arguments):
        return arguments[arguments.index("--database") + 1]
    return None


def _selections(pid: str) -> list[dict]:
    return st.session_state.setdefault(f"selections_{pid}", [])


def _add_selection(pid: str, text: str, suggested: str | None, source_text: str) -> None:
    selections = _selections(pid)
    if not text.strip():
        st.session_state[f"selection_error_{pid}"] = "selection_blank"
    elif text not in source_text:
        st.session_state[f"selection_error_{pid}"] = "selection_not_verbatim"
    elif any(s["text"] == text for s in selections):
        st.session_state[f"selection_error_{pid}"] = "selection_duplicate"
    else:
        st.session_state.pop(f"selection_error_{pid}", None)
        selections.append({"selection_id": f"s{len(selections) + 1}", "text": text, "suggested_excerpt_id": suggested})


def _use_ai_normalization(pid: str, field: str, value: str | None) -> None:
    # An explicit human click: copies the AI suggestion into the editable field. It finalizes nothing.
    st.session_state[f"norm_mode_{pid}_{field}"] = NORMALIZATION_CHOICES[1]
    st.session_state[f"norm_text_{pid}_{field}"] = value or ""


def _render_ai(review) -> None:
    st.header("AI SUGGESTIONS — AI_INFERENCE, not evidence and not decisions")
    st.caption("Everything in this section is model output. Nothing here is a human decision.")
    for excerpt in review.ai_suggested_excerpts:
        st.text(f"[{excerpt.excerpt_id}] {excerpt.status} · AI-attributed speaker: {excerpt.ai_attributed_speaker}")
        st.text(excerpt.text)
    st.text(f"AI_INFERENCE · AI normalized condition: {review.ai_normalized_condition}")
    st.text(f"AI_INFERENCE · AI normalized evaluation link: {review.ai_normalized_evaluation_link}")
    for hypothesis in review.ai_criterion_hypotheses:
        st.text(f"AI_INFERENCE · {hypothesis.criterion}: {hypothesis.ai_status} "
                f"(excerpts {', '.join(hypothesis.excerpt_refs) or 'none'}) — {hypothesis.explanation}")
    for uncertainty in review.ai_uncertainties:
        st.text(f"AI_INFERENCE · uncertainty: {uncertainty}")
    st.text(f"Provenance · artifact {review.artifact_id} (digest {review.artifact_digest}) · "
            f"mapping {review.mapping_version}")
    st.text(f"Provenance · run {review.ai_run_id} · provider {review.provider} · requested model "
            f"{review.requested_model} · returned model {review.response_model}")
    st.text(f"Provenance · prompt {review.prompt_version} ({review.prompt_digest}) · validator "
            f"{review.validation_version} · request {review.request_digest}")


def _unset_items(pid: str, selections: list[dict]) -> list[str]:
    s = st.session_state
    missing = []
    if not selections:
        missing.append("evidence selection")
    missing += [f"provenance for {sel['selection_id']}" for sel in selections if s.get(f"prov_{pid}_{sel['selection_id']}") is None]
    for key, label in ((f"source_{pid}", "source selection"), (f"bes_mode_{pid}", "buyer exact statement choice"),
                       (f"origin_{pid}", "articulation origin"), (f"mode_{pid}", "elicitation mode"),
                       (f"stringency_mode_{pid}", "stringency decision")):
        if s.get(key) is None:
            missing.append(label)
    if s.get(f"bes_mode_{pid}") == BUYER_STATEMENT_CHOICES[1] and s.get(f"bes_selection_{pid}") is None:
        missing.append("buyer exact statement selection")
    missing += [f"finding for {c.value}" for c in CRITERIA if s.get(f"finding_{pid}_{c.value}") is None]
    missing += [f"final normalization choice for {field}" for field, _, _ in FIELDS if s.get(f"norm_mode_{pid}_{field}") is None]
    return missing


def _decisions(pid: str, selections: list[dict]) -> ReviewDecisions:
    s = st.session_state
    stringency = NO_STRINGENCY_STATED
    if s[f"stringency_mode_{pid}"] == STRINGENCY_CHOICES[1]:
        comparator = s.get(f"st_comparator_{pid}")
        stringency = stringency_from_fields(
            verbatim_text=s.get(f"st_verbatim_{pid}", ""),
            comparator=None if comparator is None else ThresholdComparator(comparator),
            numeric_value=s.get(f"st_value_{pid}") or None, unit=s.get(f"st_unit_{pid}") or None,
            qualitative_term=s.get(f"st_qualitative_{pid}") or None,
            recurrence_text=s.get(f"st_recurrence_{pid}") or None, timing_text=s.get(f"st_timing_{pid}") or None)
    finals = {field: (None if s[f"norm_mode_{pid}_{field}"] == NORMALIZATION_CHOICES[0]
                      else s.get(f"norm_text_{pid}_{field}", "")) for field, _, _ in FIELDS}
    return ReviewDecisions(
        evidence_selections=tuple(EvidenceSelection(sel["selection_id"], sel["text"],
                                                    ProvenanceCategory(s[f"prov_{pid}_{sel['selection_id']}"]),
                                                    sel["suggested_excerpt_id"]) for sel in selections),
        source_selection_id=s[f"source_{pid}"],
        buyer_exact_statement=(next((sel["text"] for sel in selections
                                     if sel["selection_id"] == s.get(f"bes_selection_{pid}")), "")
                               if s[f"bes_mode_{pid}"] == BUYER_STATEMENT_CHOICES[1] else None),
        findings=tuple(CriterionDecision(c, CriterionFinding(s[f"finding_{pid}_{c.value}"]),
                                         tuple(s.get(f"fev_{pid}_{c.value}", []))) for c in CRITERIA),
        articulation_origin=ArticulationOrigin(s[f"origin_{pid}"]),
        elicitation_mode=ElicitationMode(s[f"mode_{pid}"]),
        stringency=stringency,
        final_normalized_condition=finals["condition"],
        final_normalized_evaluation_link=finals["evaluation_link"],
    )


def _render_review(service: ProposalReviewService, review) -> None:
    pid = review.proposal_id
    st.header("HUMAN REVIEW — authoritative decisions, each made explicitly by you")
    st.caption(IDENTITY_NOTE)
    st.caption(ACCEPT_MEANING)
    st.text(f"Review state: {STATE_LABELS[review.review_state]} · revisions recorded: {len(review.revisions)}")
    if review.review_state == "REVIEW_REJECTED":
        st.info("Review Rejected. This AI Draft is closed; no further review is possible.")
        return

    st.text_input("Reviewer label (self-asserted)", key=f"reviewer_{pid}")

    st.subheader("Evidence selection (exact verbatim text from the source interaction)")
    for excerpt in review.ai_suggested_excerpts:
        st.button(f"Select AI suggestion {excerpt.excerpt_id} as evidence", key=f"use_{pid}_{excerpt.excerpt_id}",
                  on_click=_add_selection, args=(pid, excerpt.text, excerpt.excerpt_id, review.interaction_text))
    st.text_area("Exact text copied from the source interaction", key=f"manual_{pid}")
    st.button("Add exact source text as evidence", key=f"add_manual_{pid}", on_click=lambda: _add_selection(
        pid, st.session_state.get(f"manual_{pid}", ""), None, review.interaction_text))
    if st.session_state.get(f"selection_error_{pid}"):
        st.error(f"Not added: {st.session_state[f'selection_error_{pid}']}")
    selections = _selections(pid)
    for sel in selections:
        st.text(f"{sel['selection_id']}: {sel['text']}")
        st.selectbox(f"Evidence provenance for {sel['selection_id']}", [p.value for p in EVIDENCE_PROVENANCE],
                     index=None, key=f"prov_{pid}_{sel['selection_id']}")
    ids = [sel["selection_id"] for sel in selections]
    st.selectbox("Source selection for the BAEC record", ids, index=None, key=f"source_{pid}")
    st.radio("Buyer exact statement", BUYER_STATEMENT_CHOICES, index=None, key=f"bes_mode_{pid}")
    st.selectbox("Buyer exact statement: the evidence selection whose exact text it is (must be BUYER_FACT; "
                 "add a smaller span as its own selection first)", ids, index=None, key=f"bes_selection_{pid}")

    st.subheader("Criterion findings (your decision; the AI hypothesis is shown only as a suggestion)")
    hypotheses = {h.criterion: h for h in review.ai_criterion_hypotheses}
    for criterion in CRITERIA:
        hypothesis = hypotheses.get(criterion.value)
        if hypothesis is not None:
            st.caption(f"AI suggestion (AI_INFERENCE): {hypothesis.ai_status}")
        st.radio(f"Finding for {criterion.value}", [f.value for f in CriterionFinding], index=None,
                 key=f"finding_{pid}_{criterion.value}")
        st.multiselect(f"Evidence for {criterion.value}", ids, default=[], key=f"fev_{pid}_{criterion.value}")

    st.radio("Articulation origin", [o.value for o in ArticulationOrigin], index=None, key=f"origin_{pid}")
    st.radio("Elicitation mode", [m.value for m in ElicitationMode], index=None, key=f"mode_{pid}")

    st.subheader("Stringency (your decision)")
    st.radio("Stringency decision", STRINGENCY_CHOICES, index=None, key=f"stringency_mode_{pid}")
    st.text_input("Stringency verbatim text", key=f"st_verbatim_{pid}")
    st.selectbox("Threshold comparator", [c.value for c in ThresholdComparator], index=None, key=f"st_comparator_{pid}")
    st.text_input("Numeric value (the buyer's number only)", key=f"st_value_{pid}")
    st.text_input("Unit", key=f"st_unit_{pid}")
    st.text_input("Qualitative term (verbatim)", key=f"st_qualitative_{pid}")
    st.text_input("Recurrence text (verbatim)", key=f"st_recurrence_{pid}")
    st.text_input("Timing text (verbatim)", key=f"st_timing_{pid}")

    st.subheader("Final normalization (derived text, never evidence)")
    for field, label, ai_attribute in FIELDS:
        st.radio(f"{label}: choice", NORMALIZATION_CHOICES, index=None, key=f"norm_mode_{pid}_{field}")
        st.button(f"Use AI suggestion for {label.lower()}", key=f"use_ai_{pid}_{field}",
                  on_click=_use_ai_normalization, args=(pid, field, getattr(review, ai_attribute)))
        st.text_area(label, key=f"norm_text_{pid}_{field}")

    if st.button("Accept review (record a human review decision)", key=f"accept_{pid}"):
        missing = _unset_items(pid, selections)
        if missing:
            st.error("Not accepted. Still unset: " + "; ".join(missing))
        else:
            try:
                accepted = service.accept_review(pid, _decisions(pid, selections),
                                                 actor_label=st.session_state.get(f"reviewer_{pid}", ""))
            except ReviewNotSaved as refusal:
                st.error(f"Not accepted: {refusal}")
            else:
                st.success(f"Review Accepted — revision {accepted.revision.revision_number} recorded. "
                           f"{ACCEPT_MEANING}")
    if st.button("Reject AI draft (record a terminal review rejection)", key=f"reject_{pid}"):
        try:
            service.reject_proposal(pid, actor_label=st.session_state.get(f"reviewer_{pid}", ""))
        except ReviewNotSaved as refusal:
            st.error(f"Not rejected: {refusal}")
        else:
            st.success("Review Rejected. No BAEC, grant, or account change was made.")


def _render_authorization(authorization: ProposalAuthorizationService, pid: str) -> None:
    try:
        status = authorization.authorization_status(pid)
    except AuthorizationRefused as refusal:
        st.error(f"Authorization unavailable: {refusal}")
        return
    st.header("AUTHORIZATION — a separate explicit human action, not a confirmation")
    st.caption(IDENTITY_NOTE)
    st.caption(AUTHORIZATION_NOTE)
    classification = status.classification.value if status.classification is not None else "not available"
    st.text(f"Classifier result, recomputed from the accepted revision: {classification}")
    if status.eligibility_failure is not None:
        st.text(f"Not eligible for authorization: {status.eligibility_failure}")
    for grant in status.grants:
        st.text(f"{GRANT_LABELS[grant.status]} · {grant.grant_id} · issued {grant.issued_at.isoformat()} · "
                f"expires {grant.expires_at.isoformat()} · by {grant.actor_label} (self-asserted)")
    if st.button("Authorize BAEC confirmation (a separate human authorization; does not confirm)",
                 key=f"authorize_{pid}"):
        try:
            grant = authorization.authorize_confirmation(pid, actor_label=st.session_state.get(f"reviewer_{pid}", ""))
        except AuthorizationRefused as refusal:
            st.error(f"Not authorized: {refusal}")
        else:
            st.success(f"Authorization Granted — {grant.grant_id}, expires {grant.expires_at.isoformat()}. "
                       "Not executed: no BAEC has been confirmed.")


def main() -> None:
    st.title("BAEC Human Review of AI Drafts")
    st.warning(BANNER)
    path = _database_path()
    if path is None:
        st.info("No database configured. Run: streamlit run baec_app/interfaces/review_page.py -- --database PATH")
        return
    connection = open_review_connection(path)
    try:
        service = ProposalReviewService(connection, clock=SystemClock())
        proposals = service.list_reviewable()
        if not proposals:
            st.info("No AI Draft proposals are available for review.")
            return
        labels = {p.proposal_id: f"{p.proposal_id} · {p.account_id} · {STATE_LABELS[p.review_state]}" for p in proposals}
        pid = st.selectbox("AI Draft", list(labels), format_func=labels.get, key="proposal")
        try:
            review = service.load_review(pid)
        except ReviewNotSaved as closed:
            st.info(f"This AI Draft is closed to review: {closed}")
            return
        st.header("SOURCE EVIDENCE — the stored interaction (untrusted text, shown as plain text)")
        st.text(f"Account {review.account_id} · interaction {review.interaction_id} · {review.interaction_occurred_at}")
        st.text(review.interaction_text)
        _render_ai(review)
        _render_review(service, review)
        if review.review_state == "REVIEW_ACCEPTED":
            _render_authorization(ProposalAuthorizationService(connection, clock=SystemClock()), pid)
    finally:
        connection.close()


main()
