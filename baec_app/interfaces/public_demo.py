"""BAEC Engine 1 public demo (Streamlit): capture and human verification of one buyer-articulated condition.

    streamlit run streamlit_app.py

A guided walk through the real Phase 7 human path on one fixed synthetic interaction:

    Buyer Interaction -> AI Draft -> Human Review -> Review Accepted -> Authorization

The AI Draft is the authentic prerecorded Phase 6 artifact (public_demo_assets/), replayed per visitor; no model is
called. Every authoritative value is the visitor's explicit decision, submitted to the unchanged
ProposalReviewService and ProposalAuthorizationService. The browser demo stops at Authorization Granted by design:
it holds no execution capability, and nothing here confirms a BAEC.

Each visitor's state lives only in their own Streamlit session: the serialized image of a private in-memory
database (see public_demo_recording, "per-visitor demo sessions"). Every write happens in an explicit button
callback on a connection opened for that callback; the page body only reads, so the progress shown always
reflects what is actually persisted. Source and AI text is untrusted and rendered only as inert plain text.
"""

from __future__ import annotations

import json
import re

import streamlit as st

from baec_app.application import SystemClock
from baec_app.application import public_demo_recording as recording
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
    stringency_from_fields,
)
from baec_app.domain.enums import (
    ArticulationOrigin,
    CriterionFinding,
    ElicitationMode,
    ProvenanceCategory,
    ThresholdComparator,
)

# --- public copy (no em dashes anywhere) --------------------------------------------------------------------------

PAGE_TITLE = "BAEC Engine 1: Capture + Verification"
BANNER = "Research Prototype · Synthetic Data Only"
DISCLOSURE = ("BAEC Engine 1 demonstrates one software implementation of the BAEC framework. It does not validate "
              "the BAEC theory, predict purchase intent, or establish that a buyer is currently evaluating "
              "alternatives.")
SESSION_NOTE = ("Your demo decisions are temporary, isolated to this session, and discarded on reset or when the "
                "session ends.")
LANDING_HEADING = "BAEC"
LANDING_ENGINE = "Engine 1"
LANDING_SUBHEADING = "Capture + Verification"
LANDING_STATEMENT = "Remember what the buyer said would make them reconsider."
LANDING_TEXT = ("Buyers often say they are not evaluating today, but explain what would have to change for "
                "evaluation to become worthwhile.\n\nThose conditions are easy to lose in notes, CRM fields or "
                "memory.\n\nBAEC Engine 1 captures the buyer's exact condition, uses AI to organize the evidence, "
                "and requires a human to verify it before anything becomes authoritative.")
VALUE_CARDS = (("Capture", "Preserve what the buyer actually said."),
               ("Verify", "Separate buyer evidence from seller interpretation and AI inference."),
               ("Structure", "Turn the verified condition into information that can later be monitored."))
LANDING_LEVELS_HEADING = "Start simple. Go deeper anytime."
LANDING_GUIDED = ("Guided workflow", "Move through five stages from buyer conversation to human authorization. "
                  "No technical knowledge is required.")
LANDING_INSPECT = ("Inspect the engine", "Open the expandable technical sections throughout the workflow to inspect "
                   "exact evidence, AI provenance, BAEC criteria, normalization, classifier output and authorization "
                   "boundaries.")
INSPECT_LOOK_FOR = "Look for: Technical details · Recording provenance · field help · expandable review sections"
LANDING_LEVELS_CLOSING = ("The core workflow stays simple. The underlying reasoning and architecture remain "
                          "available whenever you want to inspect them.")
ARCHITECTURE = ("Buyer conversation", "AI proposal", "Human verification", "Deterministic classification",
                "Explicit authorization", "Separate execution")
ARCHITECTURE_NOTE = "This public browser demo stops before execution."
BRIEF_LABEL = "Research Brief"
BRIEF_PATH = recording.ASSET_DIRECTORY / "BAEC_Engine_1_Research_and_Technical_Brief.pdf"
BRIEF_UNAVAILABLE = "The Research Brief will be available here once it is published."
PURPOSE = {
    1: "Purpose: Understand what the buyer actually said.",
    2: "Purpose: See how AI organizes the conversation without making an authoritative decision.",
    3: "Purpose: Decide which evidence and interpretation should become authoritative.",
    4: "Purpose: See what the verified human review implies under the deterministic BAEC rules.",
    5: "Purpose: Explicitly authorize the next system action separately from the review decision.",
}
VALUE_ACCEPTED = ("The buyer-defined condition has now been converted from conversation evidence into a structured, "
                  "human-verified review.")
SEPARATION_TEXT = ("Authorization is separated from review so accepting evidence cannot automatically trigger "
                   "confirmation.")
RESTART_HINT = "Use Restart Demo to start again."
LANDING_FACTS = "No real customer data. No live AI call. Nothing in this demo predicts purchase intent."
RECORDED_NOTICE = ("Recorded AI output from a synthetic demonstration. No model call is made while you use this "
                   "demo.")
MODEL_NOTE = ("The model named below is historical provenance for this one recording. It is not a recommendation, "
              "and no model is qualified as a default.")
LOOK_FOR_HEADING = "What to look for"
LOOK_FOR_TEXT = "A condition the buyer says could make evaluating alternatives worthwhile."
REVIEW_HEADLINE = "AI can suggest. You decide what becomes authoritative."
REVIEW_GUIDE = ("Work through five review steps. Required decisions are shown in the checklist. Optional controls "
                "are labeled clearly.")
OPTIONAL_EVIDENCE = ("The AI-suggested excerpts above are enough for this demonstration. Use this only if you want to "
                     "verify a different exact span from the conversation.")
NO_EVIDENCE_YET = ("First select at least one evidence excerpt. These fields will then let you identify who said it "
                   "and which statement should be treated as the buyer's exact statement.")
CRITERIA_INTRO = ("A BAEC requires all four:\n\n1. The buyer is not currently evaluating alternatives.\n"
                  "2. The condition is prospective.\n3. The buyer articulated the condition.\n"
                  "4. The buyer linked that condition to starting or reopening evaluation.")
THRESHOLD_INTRO = ("Some buyer conditions include a specific threshold. This section preserves the buyer's wording "
                   "without weakening or inventing it.")
TIMING_HELP = ("Record timing only when needed to preserve the buyer's condition. This field is optional in this "
               "demonstration.")
WORDING_INTRO = ("Normalization makes the condition easier to use consistently. It never replaces the buyer's exact "
                 "words or changes a threshold.")
SUBMIT_HEADING = "Before you submit"
SUBMIT_TEXT = ("Accepting the review records your human decisions.\n\nIt does not confirm a BAEC and does not "
               "authorize execution.")
INTERACTION_NOTE = ("The buyer is not currently evaluating alternatives. Your job is to identify what condition "
                    "they said could change that.")
AI_NOTE = ("Everything on this page is AI_INFERENCE: a model-generated interpretation. It is not authoritative, "
           "and none of it is a decision.")
RECORDED_HEADING = "Recorded AI analysis"
RECORDED_SUMMARY = ("This is a prerecorded model interpretation of synthetic data. Nothing here is authoritative "
                    "until a human reviews it.")
AI_FLAG_HEADING = "AI noticed this"
AI_FLAG_TEXT = "Possible condition. Not yet verified."
AI_FLAG_HELP = "Model interpretation (AI_INFERENCE). You decide what counts as evidence in Human Review."
AUTHORIZE_INTRO = "Human review is complete. Confirmation still requires a separate authorization decision."
CONFIRMED_PRIMARY = "The accepted human review satisfies the BAEC criteria."
CONFIRMED_SECONDARY = "This is still a classification preview. No BAEC has been confirmed."
NOT_BAEC_PRIMARY = "The accepted review does not satisfy all BAEC criteria."
INSUFFICIENT_PRIMARY = "The accepted review does not establish all BAEC criteria."
REVIEW_NOTE = ("Every authoritative value below starts unset and is decided by you. AI suggestions help, but they "
               "never fill a field unless you click to use one.")
ALIAS_NOTE = "Use initials or an alias. Do not enter personal or customer information."
PRIVACY_NOTE = "Do not enter personal information or real customer data. This demo uses synthetic data only."
ACCEPTED_TEXT = ("The human review is complete.\n\nThis does not confirm a BAEC and does not mean the buyer is "
                 "evaluating alternatives.")
AUTHORIZE_TEXT = ("Authorization is a separate, explicit human action. It permits one later confirmation of exactly "
                  "this accepted review. It does not confirm anything by itself.")
GRANTED_TEXT = ("This browser demo stops here by design.\n\nIn the full BAEC Engine 1 architecture, this "
                "short-lived, single-use authorization is passed to a separate MCP server. The MCP server "
                "independently revalidates the authorization and the reviewed evidence before it can create the "
                "confirmed BAEC.\n\nThe browser interface cannot execute that write path.")
GRANT_RULES = ("The authorization expires 15 minutes after it is issued and can be used at most once. Editing or "
               "rejecting the review would supersede it.")
DISTINCTIONS = ("Review Accepted ≠ Authorization Granted", "Authorization Granted ≠ BAEC Confirmed",
                "BAEC Confirmed ≠ Active Opportunity", "BAEC Confirmed ≠ Purchase Intent")
ENGINE2_HEADING = "BAEC Engine 2"
ENGINE2_SUBHEADING = "Monitoring + Correspondence"
ENGINE2_TEXT = ("Take a confirmed BAEC and monitor for new evidence that may correspond to the buyer-defined "
                "condition.")
ENGINE2_CLOSING = "Remember what the buyer said mattered, then watch for it."
STAGES = ("Buyer Interaction", "AI Draft", "Human Review", "Review Accepted", "Authorization")

GLOSSARY = {
    "BUYER_FACT": "Something you, the human reviewer, confirm the buyer actually said.",
    "SELLER_OBSERVATION": "Something the human seller documented. It is not the buyer's own words.",
    "AI_INFERENCE": "A model-generated interpretation. It is not authoritative.",
}
# The four constitutive criteria, in their locked wording (Research Contract RC-02).
CRITERION_HELP = {
    "PRESENT_NON_EVALUATION": ("C1 Present non-evaluation",
                               "The buyer is presently outside an active evaluation of the relevant alternatives."),
    "PROSPECTIVE_CONDITION": ("C2 Prospective condition",
                              "The condition is prospective, not an event that has already occurred."),
    "BUYER_ARTICULATION": ("C3 Buyer articulation",
                           "The condition is articulated by the buyer rather than supplied solely by the seller."),
    "EVALUATION_LINKAGE": ("C4 Evaluation linkage",
                           "The buyer links the condition to initiating or reopening evaluation."),
}
FINDING_HELP = ("“Criterion met” and “Criterion not met” each need supporting evidence you selected. "
                "“Cannot determine” is always allowed.")
ORIGIN_HELP = "Who supplied the core condition: the buyer, the seller, or not established."
MODE_HELP = ("Whether the buyer volunteered the condition or was invited to name it. Recorded only; it does not "
             "change the classification.")
STRINGENCY_HELP = ("How much change the buyer said is required, stored exactly as stated. The verbatim text must "
                   "appear in your selected evidence. A number is never invented.")
NORMALIZATION_HELP = ("A plain restatement of the condition. It never replaces the buyer's words and may not change "
                      "any number, unit, or comparator from your selected evidence (up to 500 characters).")
SOURCE_HELP = "The evidence selection the BAEC record cites as its source excerpt."
STATEMENT_HELP = ("The buyer's exact words. It must be the full text of one of your selections that you marked "
                  "BUYER_FACT. To use a shorter span, add that span as its own selection first.")

SUMMARY_CRITERIA = {"PRESENT_NON_EVALUATION": "Buyer currently not evaluating",
                    "PROSPECTIVE_CONDITION": "Future condition", "BUYER_ARTICULATION": "Articulated by the buyer",
                    "EVALUATION_LINKAGE": "Linked to evaluation"}
SUMMARY_FINDINGS = {"MET": "Verified", "NOT_MET": "Not met", "UNKNOWN": "Cannot determine"}
FLOW = ("Accepted Human Review", "Authorization Grant", "Separate MCP Executor")
THRESHOLD_HINT = re.compile(r"\b(?:more than|less than|at least|at most|over|under|up to|exactly|about|around|"
                            r"approximately)\s+[^,.;!?]*\d[^,.;!?]*", re.IGNORECASE)
REFUSAL_TEXT = {
    "actor_blank": "Enter your initials or an alias.",
    "selection_not_verbatim": "Evidence must be exact text from the conversation.",
    "selection_duplicate": "That evidence is already selected.",
    "selection_suggestion_mismatch": "A selection taken from an AI suggestion must match it exactly.",
    "provenance_invalid": "Each selection needs “Verified buyer statement” or “Seller's observation”.",
    "source_selection_unknown": "Choose a source selection from your evidence.",
    "buyer_statement_not_selected": "The buyer statement must be the full text of one of your selections.",
    "buyer_statement_requires_buyer_fact": ("The buyer statement's selection must be marked “Verified buyer "
                                            "statement”."),
    "criteria_incomplete": "Decide all four criteria.",
    "finding_evidence_unknown": "“Criterion met” and “Criterion not met” need evidence from your selections.",
    "stringency_not_verbatim": "The exact threshold text must appear exactly in your selected evidence.",
    "normalization_invalid": "The final wording is not supported by your selected evidence.",
    "candidate_invalid": "These decisions do not form a valid BAEC candidate.",
    "not_confirmable": "The deterministic classification is not a confirmed BAEC.",
}
GRANT_STATUS_TEXT = {"ACTIVE": "active", "EXPIRED": "expired", "SUPERSEDED": "superseded", "CONSUMED": "used"}
BUYER_STATEMENT_CHOICES = ("No exact buyer statement", "Record an exact buyer statement")
STRINGENCY_CHOICES = ("No stringency stated", "Record stringency")
THRESHOLD_LABELS = {"No stringency stated": "No threshold stated", "Record stringency": "Record the buyer's threshold"}
NORMALIZATION_CHOICES = ("No final normalization", "Provide final text")
FIELDS = (("condition", "Final normalized condition", "ai_normalized_condition"),
          ("evaluation_link", "Final normalized evaluation link", "ai_normalized_evaluation_link"))
KEY = "pd_"  # every key this page owns starts with this prefix, so a reset can clear all of them

# Plain-English display labels. The stored values are the exact enum values; only the display changes.
PROVENANCE_LABELS = {"BUYER_FACT": "Verified buyer statement",
                     "SELLER_OBSERVATION": "Seller's observation (not the buyer's own words)"}
FINDING_LABELS = {"MET": "Criterion met", "NOT_MET": "Criterion not met", "UNKNOWN": "Cannot determine"}
ORIGIN_LABELS = {"BUYER_GENERATED": "Condition stated by buyer", "SELLER_SEEDED": "Condition suggested by seller",
                 "UNCERTAIN": "Not clear who stated it"}
MODE_LABELS = {"SPONTANEOUS": "Volunteered by the buyer", "CEE_ELICITED": "Elicited through an open question",
               "UNKNOWN": "Not known"}
COMPARATOR_LABELS = {"GREATER_THAN": "More than", "AT_LEAST": "At least", "LESS_THAN": "Less than",
                     "AT_MOST": "At most", "EXACTLY": "Exactly", "APPROXIMATELY": "Approximately",
                     "QUALITATIVE_ONLY": "Qualitative only (no number)", "NONE_STATED": "No comparator stated"}
AI_STATUS_LABELS = {"supported": "AI thinks this is supported", "not_supported": "AI thinks this is not supported",
                    "unclear": "AI is unsure"}
OUTCOME_LABELS = {
    "CONFIRMED_BAEC": ("Confirmed BAEC",
                       "Your decisions meet all four criteria, and the buyer stated the condition. This is only a "
                       "preview: nothing is confirmed by accepting a review."),
    "NOT_BAEC": ("Not a BAEC",
                 "At least one criterion is not met, or the seller suggested the condition. The review is recorded, "
                 "but it does not describe a BAEC."),
    "INSUFFICIENT_EVIDENCE": ("Not enough evidence",
                              "At least one criterion could not be determined, or who stated the condition is "
                              "unclear. The review is recorded, but it cannot support a BAEC."),
}


USE_LABEL, SELECTED_LABEL, REMOVE_LABEL = "Use as evidence", "Selected as evidence", "Remove"
CHECKLIST = ("Buyer evidence verified", "Source and buyer statement confirmed", "Four BAEC criteria reviewed",
             "Condition origin reviewed", "Elicitation method reviewed", "Exact threshold reviewed",
             "Final wording reviewed", "Reviewer alias entered")
CHECKLIST_TODO = {"Buyer evidence verified": "verify buyer evidence and who said it",
                  "Source and buyer statement confirmed": "confirm the source and the buyer statement",
                  "Four BAEC criteria reviewed": "decide all four BAEC criteria",
                  "Condition origin reviewed": "choose who stated the condition",
                  "Elicitation method reviewed": "choose how the condition was elicited",
                  "Exact threshold reviewed": "review the exact threshold",
                  "Final wording reviewed": "review the final wording",
                  "Reviewer alias entered": "enter a reviewer alias"}


def _technical(mapping: dict) -> str:
    return "Stored values: " + "; ".join(f"{label} = {value}" for value, label in mapping.items())


# --- session plumbing ---------------------------------------------------------------------------------------------


def _clear_page_state() -> None:
    for key in [k for k in st.session_state if str(k).startswith(KEY)]:
        del st.session_state[key]


def _restart() -> None:
    """Restart Demo: discard this visitor's session image and every page key, and return to the entrance. No new
    database exists until Start Demo is pressed again."""
    _clear_page_state()


def _start() -> None:
    """Start Demo: discard anything left from before, then a fresh database, the recording, and a new AI_DRAFT."""
    _clear_page_state()
    st.session_state[KEY + "session"] = recording.start_demo_session()
    st.session_state[KEY + "view"] = 1
    st.session_state[KEY + "selections"] = []


def _write(action):
    """Run one write on a connection opened for it, keep the new image in this session, and close the connection."""
    session = st.session_state[KEY + "session"]
    connection = recording.open_demo_database(session.image)
    try:
        result = action(connection)
        st.session_state[KEY + "session"] = recording.DemoSession(recording.snapshot_demo_database(connection),
                                                                  session.proposal_id)
        return result
    finally:
        connection.close()


def _flash(kind: str, text: str, technical: str | None = None) -> None:
    st.session_state[KEY + "flash"] = (kind, text, technical)


def _go(view: int) -> None:
    st.session_state[KEY + "view"] = view


# --- review inputs ------------------------------------------------------------------------------------------------


def _candidate_spans(source: str) -> list[str]:
    """Exact spans of the conversation a visitor can choose: each utterance, and each of its sentences."""
    spans = []
    for line in source.splitlines():
        utterance = line.split(": ", 1)[1] if ": " in line else line
        pieces = [utterance] + _sentences(utterance)
        for piece in pieces:
            if piece and piece in source and piece not in spans:
                spans.append(piece)
    return spans


def _sentences(text: str) -> list[str]:
    out, start = [], 0
    for index, character in enumerate(text):
        if character in ".?!" and (index + 1 == len(text) or text[index + 1] == " "):
            out.append(text[start:index + 1].strip())
            start = index + 1
    return [s for s in out if s]


def _add_selection(text: str, suggested: str | None, source: str) -> None:
    selections = st.session_state.setdefault(KEY + "selections", [])
    if not text or text not in source:
        _flash("error", REFUSAL_TEXT["selection_not_verbatim"])
    elif any(s["text"] == text for s in selections):
        _flash("error", REFUSAL_TEXT["selection_duplicate"])
    else:
        # Ids are never reused, so a removed selection's links can never attach to a new one.
        number = st.session_state.get(KEY + "next_selection", 1)
        st.session_state[KEY + "next_selection"] = number + 1
        selections.append({"selection_id": f"s{number}", "text": text, "suggested_excerpt_id": suggested})


def _remove_selection(selection_id: str) -> None:
    """Undo one unsubmitted evidence choice, and every control that referred to it. UI state only: nothing persisted
    exists yet, and the review service re-verifies every selection when a review is accepted."""
    s = st.session_state
    s[KEY + "selections"] = [sel for sel in s.get(KEY + "selections", []) if sel["selection_id"] != selection_id]
    s.pop(f"{KEY}prov_{selection_id}", None)
    for key in (KEY + "source", KEY + "bes_selection"):
        if s.get(key) == selection_id:
            s.pop(key)
    for criterion in CRITERIA:
        key = f"{KEY}fev_{criterion.value}"
        if selection_id in s.get(key, []):
            s[key] = [value for value in s[key] if value != selection_id]


def _add_chosen_span(source: str) -> None:
    index = st.session_state.get(KEY + "span")
    if index is None:
        _flash("error", "Choose a span from the conversation first.")
        return
    _add_selection(_candidate_spans(source)[index], None, source)


def _use_ai_normalization(field: str, value: str | None) -> None:
    # An explicit click: copies the AI suggestion into the editable field. It decides nothing.
    st.session_state[f"{KEY}norm_mode_{field}"] = NORMALIZATION_CHOICES[1]
    st.session_state[f"{KEY}norm_text_{field}"] = value or ""


def _checklist(selections: list[dict]) -> dict[str, bool]:
    """UI guidance from the current form state only. The review service remains the authority at Accept."""
    s = st.session_state
    stringency = s.get(KEY + "stringency_mode")
    stringency_done = stringency == STRINGENCY_CHOICES[0] or (
        stringency == STRINGENCY_CHOICES[1] and bool(s.get(KEY + "st_verbatim")) and s.get(KEY + "st_comparator"))
    wording_done = all(s.get(f"{KEY}norm_mode_{field}") == NORMALIZATION_CHOICES[0] or (
        s.get(f"{KEY}norm_mode_{field}") == NORMALIZATION_CHOICES[1] and bool(s.get(f"{KEY}norm_text_{field}")))
        for field, _, _ in FIELDS)
    statement = s.get(KEY + "bes_mode")
    return {
        "Buyer evidence verified": bool(selections) and all(
            s.get(f"{KEY}prov_{sel['selection_id']}") is not None for sel in selections),
        "Source and buyer statement confirmed": s.get(KEY + "source") is not None and (
            statement == BUYER_STATEMENT_CHOICES[0] or (statement == BUYER_STATEMENT_CHOICES[1]
                                                        and s.get(KEY + "bes_selection") is not None)),
        "Four BAEC criteria reviewed": all(s.get(f"{KEY}finding_{c.value}") is not None for c in CRITERIA),
        "Condition origin reviewed": s.get(KEY + "origin") is not None,
        "Elicitation method reviewed": s.get(KEY + "mode") is not None,
        "Exact threshold reviewed": bool(stringency_done),
        "Final wording reviewed": wording_done,
        "Reviewer alias entered": bool(str(s.get(KEY + "alias", "")).strip()),
    }


def _sections_complete(checklist: dict[str, bool]) -> int:
    """How many of the five sections A to E are complete, by the checklist. Display only."""
    groups = (("Buyer evidence verified", "Source and buyer statement confirmed"),
              ("Four BAEC criteria reviewed", "Condition origin reviewed", "Elicitation method reviewed"),
              ("Exact threshold reviewed",), ("Final wording reviewed",), ("Reviewer alias entered",))
    return sum(all(checklist[item] for item in group) for group in groups)


def _missing(selections: list[dict]) -> list[str]:
    return [CHECKLIST_TODO[item] for item, done in _checklist(selections).items() if not done]


def _decisions(selections: list[dict]) -> ReviewDecisions:
    s = st.session_state
    stringency = NO_STRINGENCY_STATED
    if s[KEY + "stringency_mode"] == STRINGENCY_CHOICES[1]:
        comparator = s.get(KEY + "st_comparator")
        stringency = stringency_from_fields(
            verbatim_text=s.get(KEY + "st_verbatim", ""),
            comparator=None if comparator is None else ThresholdComparator(comparator),
            numeric_value=s.get(KEY + "st_value") or None, unit=s.get(KEY + "st_unit") or None,
            qualitative_term=None, recurrence_text=None, timing_text=s.get(KEY + "st_timing") or None)
    finals = {field: (None if s[f"{KEY}norm_mode_{field}"] == NORMALIZATION_CHOICES[0]
                      else s.get(f"{KEY}norm_text_{field}", "")) for field, _, _ in FIELDS}
    return ReviewDecisions(
        evidence_selections=tuple(EvidenceSelection(sel["selection_id"], sel["text"],
                                                    ProvenanceCategory(s[f"{KEY}prov_{sel['selection_id']}"]),
                                                    sel["suggested_excerpt_id"]) for sel in selections),
        source_selection_id=s[KEY + "source"],
        buyer_exact_statement=(next((sel["text"] for sel in selections
                                     if sel["selection_id"] == s.get(KEY + "bes_selection")), "")
                               if s[KEY + "bes_mode"] == BUYER_STATEMENT_CHOICES[1] else None),
        findings=tuple(CriterionDecision(c, CriterionFinding(s[f"{KEY}finding_{c.value}"]),
                                         tuple(s.get(f"{KEY}fev_{c.value}", []))) for c in CRITERIA),
        articulation_origin=ArticulationOrigin(s[KEY + "origin"]),
        elicitation_mode=ElicitationMode(s[KEY + "mode"]),
        stringency=stringency,
        final_normalized_condition=finals["condition"],
        final_normalized_evaluation_link=finals["evaluation_link"],
    )


def _refusal(code: str) -> str:
    return REFUSAL_TEXT.get(code, "The review could not be accepted.")


def _codes(code: str, extra: tuple[str, ...] = ()) -> str:
    return "Technical detail: " + ", ".join((code,) + tuple(extra))


# --- the three writes ---------------------------------------------------------------------------------------------


def _accept() -> None:
    selections = st.session_state.get(KEY + "selections", [])
    missing = _missing(selections)
    if missing:
        _flash("error", "Not accepted yet. Still to decide: " + "; ".join(missing) + ".")
        return
    pid = st.session_state[KEY + "session"].proposal_id
    alias = st.session_state[KEY + "alias"]
    try:
        decisions = _decisions(selections)
        _write(lambda c: ProposalReviewService(c, clock=SystemClock()).accept_review(pid, decisions,
                                                                                     actor_label=alias))
    except ReviewNotSaved as refusal:
        _flash("error", "Not accepted. " + _refusal(refusal.code), _codes(refusal.code, refusal.normalization_codes))
        return
    st.session_state.pop(KEY + "flash", None)
    # Widget values exist only while their widget is drawn; keep the self-asserted label for the later stages.
    st.session_state[KEY + "reviewer"] = alias
    _go(4)


def _reject() -> None:
    pid = st.session_state[KEY + "session"].proposal_id
    alias = str(st.session_state.get(KEY + "alias", ""))
    try:
        _write(lambda c: ProposalReviewService(c, clock=SystemClock()).reject_proposal(pid, actor_label=alias))
    except ReviewNotSaved as refusal:
        _flash("error", "Not rejected. " + _refusal(refusal.code), _codes(refusal.code))
        return
    _flash("info", "Review rejected. Nothing was authorized. " + RESTART_HINT)


def _authorize() -> None:
    pid = st.session_state[KEY + "session"].proposal_id
    alias = str(st.session_state.get(KEY + "reviewer", ""))
    try:
        _write(lambda c: ProposalAuthorizationService(c, clock=SystemClock()).authorize_confirmation(
            pid, actor_label=alias))
    except AuthorizationRefused as refusal:
        _flash("error", "Not authorized. " + _refusal(refusal.code), _codes(refusal.code))
        return
    st.session_state.pop(KEY + "flash", None)


# --- rendering (reads only) ---------------------------------------------------------------------------------------


def _inert(value: object) -> None:
    """Untrusted source or AI text: plain text only, never Markdown or HTML."""
    st.text("" if value is None else str(value))


def _footer() -> None:
    st.divider()
    st.caption(BANNER)
    st.caption(DISCLOSURE)
    st.caption(SESSION_NOTE)


def _landing() -> None:
    st.title(LANDING_HEADING)
    st.subheader(LANDING_ENGINE)
    st.markdown(f"**{LANDING_SUBHEADING}**")
    st.markdown(f"### {LANDING_STATEMENT}")
    st.markdown(LANDING_TEXT)
    for column, (heading, text) in zip(st.columns(3), VALUE_CARDS):
        with column.container(border=True, height="stretch"):  # equal height per row
            st.markdown(f"**{heading}**")
            st.caption(text)
    with st.container(horizontal=True):
        st.button("Start Demo", key=KEY + "start", type="primary", on_click=_start)
        if BRIEF_PATH.is_file():  # the packaged repository asset, served as is; no rerun, no session change
            st.download_button(BRIEF_LABEL, data=BRIEF_PATH.read_bytes(), file_name=BRIEF_PATH.name,
                               mime="application/pdf", key=KEY + "brief", type="secondary", on_click="ignore")
        else:
            st.button(BRIEF_LABEL, key=KEY + "brief", type="secondary", disabled=True, help=BRIEF_UNAVAILABLE)
    if not BRIEF_PATH.is_file():
        st.caption(BRIEF_UNAVAILABLE)
    st.divider()
    st.markdown(f"**{LANDING_LEVELS_HEADING}**")
    for column, (heading, text) in zip(st.columns(2), (LANDING_GUIDED, LANDING_INSPECT)):
        with column.container(border=True, height="stretch"):  # equal height per row
            st.markdown(f"**{heading}**")
            st.caption(text)
            if heading == LANDING_INSPECT[0]:
                st.caption(INSPECT_LOOK_FOR)
    st.caption(LANDING_LEVELS_CLOSING)
    with st.expander("How this engine works"):
        for number, step in enumerate(ARCHITECTURE, start=1):
            st.markdown(f"{number}. {step}")
        st.caption(ARCHITECTURE_NOTE)
    st.caption(LANDING_FACTS)


def _header() -> None:
    title, badges = st.columns([3, 2], vertical_alignment="center")
    with title:
        st.markdown("**BAEC**  \nEngine 1")
        st.caption(LANDING_SUBHEADING)
    with badges:
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.badge("Research Prototype", color="gray")
            st.badge("Synthetic Data Only", color="gray")


STAGE_STYLE = {"done": ":green[✓ {number}] {label}", "current": ":blue[**● {number} {label}**]",
               "upcoming": ":gray[○ {number} {label}]"}


def _progress(done: tuple[bool, ...], view: int) -> None:
    """Not clickable. Completion comes from persisted state; the current stage is the one shown."""
    columns = st.columns(len(STAGES))
    for number, (column, label, finished) in enumerate(zip(columns, STAGES, done), start=1):
        state = "current" if number == view else ("done" if finished else "upcoming")
        column.markdown(STAGE_STYLE[state].format(number=number, label=label))


TURN = re.compile(r"^(Seller|Buyer)(?: \(([^()]*)\))?: (.*)$")


def _buyer_interaction(review) -> None:
    st.header("1  Buyer Interaction")
    st.caption(PURPOSE[1])
    st.info(INTERACTION_NOTE)
    st.markdown(f"**{LOOK_FOR_HEADING}**  \n{LOOK_FOR_TEXT}")
    st.caption("A synthetic conversation. Each turn's words are shown exactly as stored.")
    flagged = [excerpt.text for excerpt in review.ai_suggested_excerpts]
    for line in review.interaction_text.splitlines():
        match = TURN.match(line)
        speaker, role, words = (match.group(1), match.group(2), match.group(3)) if match else (None, None, line)
        buyer = speaker == "Buyer"
        left, right = st.columns([1, 6]) if buyer else st.columns([6, 1])
        with (right if buyer else left):
            with st.container(border=True):
                st.caption("Buyer" if buyer else ("Seller" if speaker == "Seller" else "Turn"))
                if role:
                    _inert(role)
                _inert(words)  # display only: the stored text and every evidence span are unchanged
                if any(text in line for text in flagged):
                    st.caption(f"**{AI_FLAG_HEADING}** · {AI_FLAG_TEXT}", help=AI_FLAG_HELP)
    st.button("Continue to AI Draft", key=KEY + "to_2", type="primary", on_click=_go, args=(2,))


def _ai_draft(review) -> None:
    st.header("2  AI Draft")
    st.caption(PURPOSE[2])
    st.info(f"**{RECORDED_HEADING}**  \n{RECORDED_SUMMARY}")
    st.caption(RECORDED_NOTICE)  # includes: no model call is made while you use this demo
    _ai_summary(review)

    st.subheader("What the AI noticed")
    for number, excerpt in enumerate(review.ai_suggested_excerpts, start=1):
        with st.container(border=True):
            st.caption(f"AI suggestion {number}", help=AI_FLAG_HELP)
            _inert(excerpt.text)
            _inert(f"AI-attributed speaker: {excerpt.ai_attributed_speaker}")

    st.subheader("Potential evaluation condition")
    st.caption("The AI's restatement, never the buyer's own words (AI_INFERENCE).")
    with st.container(border=True):
        _inert(f"Condition: {review.ai_normalized_condition}")
        _inert(f"Evaluation link: {review.ai_normalized_evaluation_link}")

    st.subheader("Why the AI flagged it")
    st.caption("A hypothesis is not a finding: you decide each criterion in Human Review (AI_INFERENCE).")
    for hypothesis in review.ai_criterion_hypotheses:
        label = CRITERION_HELP.get(hypothesis.criterion, (hypothesis.criterion, ""))[0]
        status = AI_STATUS_LABELS.get(hypothesis.ai_status, hypothesis.ai_status)
        with st.container(border=True):
            _inert(f"{label}: {status}")
            _inert(hypothesis.explanation)

    st.subheader("What remains unknown")
    st.caption("Uncertainties the AI noted (AI_INFERENCE).")
    for uncertainty in review.ai_uncertainties:
        _inert(f"• {uncertainty}")

    with st.expander("Technical details"):
        _evidence_map(review)
        with st.expander("Raw recorded values"):
            st.caption("Exact recorded values, unchanged.")
            for number, excerpt in enumerate(review.ai_suggested_excerpts, start=1):
                _inert(f"Suggestion {number}: excerpt id {excerpt.excerpt_id} · status {excerpt.status}")
            for hypothesis in review.ai_criterion_hypotheses:
                _inert(f"{hypothesis.criterion}: {hypothesis.ai_status} · excerpts "
                       f"{', '.join(hypothesis.excerpt_refs) or 'none'} · {hypothesis.status}")
    with st.expander("Recording provenance"):
        st.caption(MODEL_NOTE)
        _inert(f"Provider {review.provider} · requested model {review.requested_model} · returned model "
               f"{review.response_model}")
        _inert(f"Prompt {review.prompt_version} · validator {review.validation_version}")
        _inert(f"Artifact {review.artifact_id} · digest {review.artifact_digest}")
    st.button("Continue to Human Review", key=KEY + "to_3", type="primary", on_click=_go, args=(3,))


def _ai_summary(review) -> None:
    """A quick read of the recording. Every value is quoted from the recorded artifact; nothing is inferred here."""
    hypotheses = {h.criterion: h for h in review.ai_criterion_hypotheses}
    current = hypotheses.get("PRESENT_NON_EVALUATION")
    st.markdown("**AI identified**")
    with st.container(border=True):
        rows = (("Current state", None if current is None else current.explanation),
                ("Possible trigger", review.ai_normalized_condition),
                ("Potential response", review.ai_normalized_evaluation_link))
        for label, value in rows:
            st.caption(label)
            _inert(value or "Not stated in the recording")
        st.caption("Status")
        st.markdown("**Requires human verification**")
    st.caption("Quoted from the recorded AI output. The details follow below.")


EVIDENCE_MAP_INTRO = ("This shows which exact excerpts the AI associated with each BAEC criterion. These are model "
                      "inferences, not verified human findings.")


def _evidence_map(review) -> None:
    """Which recorded excerpts the recorded hypotheses cite, read from the authentic artifact. Display only."""
    st.markdown("**AI Evidence Map**")
    st.caption(EVIDENCE_MAP_INTRO)
    for excerpt in review.ai_suggested_excerpts:
        criteria = [CRITERION_HELP.get(h.criterion, (h.criterion, ""))[0]
                    for h in review.ai_criterion_hypotheses if excerpt.excerpt_id in h.excerpt_refs]
        with st.container(border=True):
            _inert(excerpt.excerpt_id)
            st.caption("Exact excerpt:")
            _inert(f"“{excerpt.text}”")
            st.caption("AI associated this excerpt with:")
            for criterion in criteria or ["No criterion"]:
                _inert(criterion)
            st.caption("Status: AI inference only")


def _section_a_evidence(review, selections: list[dict], ids: list[str]) -> None:
    source = review.interaction_text
    chosen = {sel["text"]: sel["selection_id"] for sel in selections}
    st.caption("Choose the exact words that support the condition. Evidence is never edited or paraphrased. You can "
               "remove a choice until you submit the review.")
    st.caption("AI suggested · exact quotation from the conversation")
    for number, excerpt in enumerate(review.ai_suggested_excerpts, start=1):
        taken = excerpt.text in chosen
        with st.container(border=True):
            st.caption(f"AI suggestion {number}", help=AI_FLAG_HELP)
            _inert(excerpt.text)
            if taken:
                with st.container(horizontal=True):
                    st.button(SELECTED_LABEL, key=f"{KEY}use_{number}", disabled=True)
                    st.button(REMOVE_LABEL, key=f"{KEY}remove_ai_{number}", type="tertiary",
                              on_click=_remove_selection, args=(chosen[excerpt.text],))
            else:
                st.button(USE_LABEL, key=f"{KEY}use_{number}", on_click=_add_selection,
                          args=(excerpt.text, excerpt.excerpt_id, source))
    spans = _candidate_spans(source)
    with st.expander("Optional: choose different evidence"):
        st.caption(OPTIONAL_EVIDENCE)
        for number, span in enumerate(spans, start=1):
            _inert(f"{number}. {span}")
        st.selectbox("Conversation span", list(range(len(spans))), format_func=lambda i: str(i + 1), index=None,
                     key=KEY + "span")
        st.button("Add selected span", key=KEY + "add_span", on_click=_add_chosen_span, args=(source,))

    st.markdown("**Who said it?**")
    if not selections:
        st.info(NO_EVIDENCE_YET)
    for sel in selections:
        with st.container(border=True):
            _inert(f"{sel['selection_id']}: {sel['text']}")
            st.selectbox(f"Provenance for {sel['selection_id']}", [p.value for p in EVIDENCE_PROVENANCE],
                         format_func=PROVENANCE_LABELS.get, index=None, key=f"{KEY}prov_{sel['selection_id']}",
                         help=_technical(PROVENANCE_LABELS) + ". " + GLOSSARY["BUYER_FACT"])
            st.button(REMOVE_LABEL, key=f"{KEY}remove_{sel['selection_id']}", type="tertiary",
                      on_click=_remove_selection, args=(sel["selection_id"],))
    st.selectbox("Source selection", ids, index=None, key=KEY + "source", help=SOURCE_HELP, disabled=not ids)
    st.radio("Buyer exact statement", BUYER_STATEMENT_CHOICES, index=None, key=KEY + "bes_mode", help=STATEMENT_HELP)
    st.selectbox("Selection that is the buyer exact statement", ids, index=None, key=KEY + "bes_selection",
                 disabled=not ids)


def _section_b_criteria(review, ids: list[str]) -> None:
    st.markdown(CRITERIA_INTRO)
    st.caption(FINDING_HELP)
    if not ids:
        st.info("The evidence lists below are unavailable until you select evidence in section A, because each "
                "finding must cite your own selections.")
    hypotheses = {h.criterion: h for h in review.ai_criterion_hypotheses}
    for criterion in CRITERIA:
        label, requirement = CRITERION_HELP[criterion.value]
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption(requirement)
            if criterion.value in hypotheses:
                ai_status = hypotheses[criterion.value].ai_status
                _inert(f"AI suggestion (AI_INFERENCE): {AI_STATUS_LABELS.get(ai_status, ai_status)}")
            st.radio(f"Your finding: {label}", [f.value for f in CriterionFinding], format_func=FINDING_LABELS.get,
                     index=None, key=f"{KEY}finding_{criterion.value}", horizontal=True,
                     help=_technical(FINDING_LABELS))
            st.multiselect(f"Evidence for {label}", ids, default=[], key=f"{KEY}fev_{criterion.value}",
                           disabled=not ids)
    st.markdown("**How the condition was stated**")
    st.radio("Articulation origin", [o.value for o in ArticulationOrigin], format_func=ORIGIN_LABELS.get,
             index=None, key=KEY + "origin", help=ORIGIN_HELP + " " + _technical(ORIGIN_LABELS))
    st.radio("Elicitation mode", [m.value for m in ElicitationMode], format_func=MODE_LABELS.get, index=None,
             key=KEY + "mode", help=MODE_HELP + " " + _technical(MODE_LABELS))


def _section_c_threshold(selections: list[dict]) -> None:
    st.markdown(THRESHOLD_INTRO)
    st.caption(STRINGENCY_HELP)
    hints = []
    for sel in selections:  # display only: phrases found in the visitor's own selected evidence
        hints += [match.group(0).strip() for match in THRESHOLD_HINT.finditer(sel["text"])]
    if hints:
        st.caption("Threshold wording in your selected evidence (copy it exactly if it applies):")
        for hint in dict.fromkeys(hints):
            _inert(f"“{hint}”")
    st.caption(PRIVACY_NOTE)
    st.radio("Threshold", STRINGENCY_CHOICES, format_func=THRESHOLD_LABELS.get, index=None,
             key=KEY + "stringency_mode", help="Stored as stringency: " + " or ".join(STRINGENCY_CHOICES) + ".")
    st.text_input("Exact threshold text", key=KEY + "st_verbatim", max_chars=200,
                  help="Exactly as the buyer said it, for example: more than 10%")
    st.selectbox("Comparator", [c.value for c in ThresholdComparator], format_func=COMPARATOR_LABELS.get,
                 index=None, key=KEY + "st_comparator", help=_technical(COMPARATOR_LABELS))
    st.text_input("Number", key=KEY + "st_value", max_chars=20, help="The buyer's number only. Never invented.")
    st.text_input("Unit", key=KEY + "st_unit", max_chars=20)
    st.text_input("Timing, if needed (optional)", key=KEY + "st_timing", max_chars=200, help=TIMING_HELP)
    st.caption(TIMING_HELP)


def _section_d_wording(review) -> None:
    st.markdown(WORDING_INTRO)
    st.caption(NORMALIZATION_HELP)
    st.caption(PRIVACY_NOTE)
    for field, label, ai_attribute in FIELDS:
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.caption("AI suggested wording")
            _inert(getattr(review, ai_attribute) or "No AI suggestion")
            st.caption("Your authoritative wording")
            st.radio(f"{label}: choice", NORMALIZATION_CHOICES, index=None, key=f"{KEY}norm_mode_{field}")
            st.button(f"Use AI suggestion for the {label.lower()}", key=f"{KEY}use_ai_{field}",
                      on_click=_use_ai_normalization, args=(field, getattr(review, ai_attribute)))
            st.text_area(label, key=f"{KEY}norm_text_{field}", max_chars=500, help=PRIVACY_NOTE)


CHECKLIST_GROUPS = (("Evidence", ("Buyer evidence verified", "Source and buyer statement confirmed")),
                    ("Criteria", ("Four BAEC criteria reviewed", "Condition origin reviewed",
                                  "Elicitation method reviewed")),
                    ("Condition details", ("Exact threshold reviewed",)),
                    ("Final wording", ("Final wording reviewed",)),
                    ("Reviewer", ("Reviewer alias entered",)))


def _section_e_submit(selections: list[dict]) -> None:
    st.markdown(f"**{SUBMIT_HEADING}**")
    st.markdown(SUBMIT_TEXT)
    checklist = _checklist(selections)
    st.markdown("**Review checklist**")
    st.caption("Guidance only. The review service checks every decision again when you accept.")
    for group, items in CHECKLIST_GROUPS:
        st.caption(group)
        for item in items:
            st.markdown(f":green[✓] {item}" if checklist[item] else f":gray[○ {item}]")
    if all(checklist.values()):
        st.markdown(":green[✓] **Ready to submit**")
    else:
        st.caption("Still needed: " + "; ".join(_missing(selections)) + ".")
    st.text_input("Your initials or alias", key=KEY + "alias", max_chars=40, help=ALIAS_NOTE)
    st.caption(ALIAS_NOTE)
    st.caption("Reviewer identity is self-asserted in this prototype.")
    st.button("Accept Review", key=KEY + "accept", type="primary", on_click=_accept)
    st.button("Reject AI Draft", key=KEY + "reject", type="tertiary", on_click=_reject)


def _human_review(review) -> None:
    st.header("3  Human Review")
    st.caption(PURPOSE[3])
    if review.review_state == "REVIEW_REJECTED":
        st.info("Review rejected. Nothing was authorized. " + RESTART_HINT)
        return
    if review.review_state == "REVIEW_ACCEPTED":
        st.success("Your review has been accepted.")
        st.button("Continue to Review Accepted", key=KEY + "to_4", type="primary", on_click=_go, args=(4,))
        return
    st.markdown(f"**{REVIEW_HEADLINE}**")
    st.caption(REVIEW_GUIDE)
    with st.expander("Why the engine asks for these decisions"):
        st.caption(REVIEW_NOTE)
    selections = st.session_state.setdefault(KEY + "selections", [])
    ids = [sel["selection_id"] for sel in selections]
    complete = _sections_complete(_checklist(selections))
    st.markdown("**Review progress**")
    st.progress(complete / 5, text=f"{complete} of 5 sections complete")
    st.caption("Work through the sections in order. Closing a section keeps everything you chose in it.")
    # Plain expanders: their content always runs, so a closed section never loses a choice.
    with st.expander("A. Verify buyer evidence", expanded=True):
        _section_a_evidence(review, selections, ids)
    with st.expander("B. Evaluate the four BAEC criteria"):
        _section_b_criteria(review, ids)
    with st.expander("C. Preserve the exact threshold"):
        _section_c_threshold(selections)
    with st.expander("D. Verify how the condition will be stored"):
        _section_d_wording(review)
    with st.expander("E. Submit human review"):
        _section_e_submit(selections)


def _accepted_content(review) -> dict:
    """The accepted revision's stored content: the visitor's own decisions, as the review service recorded them."""
    return json.loads(review.revisions[-1].content)


def _summary(content: dict) -> None:
    st.markdown("**Verified review summary**")
    st.caption("From your accepted review only.")
    with st.container(border=True):
        findings = {f["criterion"]: f["finding"] for f in content["findings"]}
        for criterion, label in SUMMARY_CRITERIA.items():
            st.markdown(f"{label}: **{SUMMARY_FINDINGS.get(findings.get(criterion), 'Not available')}**")
        st.markdown(f"Condition origin: **{ORIGIN_LABELS.get(content['articulation_origin'], 'Not available')}**")
        stringency = content["stringency"]
        st.caption("Threshold")
        if stringency is None:
            st.markdown("**No threshold stated**")
        else:
            comparator = COMPARATOR_LABELS.get(stringency["comparator"], "Stated")
            unit = stringency["unit"] or ""
            _inert(f"{comparator} {stringency['numeric_value'] or ''}{unit if unit == '%' else ' ' + unit}".strip())


def _reasons(content: dict) -> list[str]:
    """Plain restatements of the locked classification rule (RC-13) applied to the accepted decisions."""
    reasons = []
    for finding in content["findings"]:
        name = CRITERION_HELP.get(finding["criterion"], (finding["criterion"], ""))[0]
        if finding["finding"] == "NOT_MET":
            reasons.append(f"{name} was marked not met.")
        elif finding["finding"] == "UNKNOWN":
            reasons.append(f"{name} could not be determined.")
    origin = content["articulation_origin"]
    if origin == "SELLER_SEEDED":
        reasons.append("The condition was marked as suggested by the seller, not stated by the buyer.")
    elif origin == "UNCERTAIN":
        reasons.append("It is not clear who stated the condition.")
    return reasons


def _review_accepted(review, status) -> None:
    st.header("4  Review Accepted")
    st.caption(PURPOSE[4])
    st.success("REVIEW ACCEPTED")
    st.markdown(ACCEPTED_TEXT)
    classification = None if status.classification is None else status.classification.value
    title, detail = OUTCOME_LABELS.get(classification, ("Not available", "No classification preview is available."))
    eligible = status.eligibility_failure is None
    first, second, third = st.columns(3)
    with first.container(border=True):
        st.caption("Human Review")
        st.markdown("**Recorded**")
    with second.container(border=True):
        st.caption("Classification Preview")
        st.markdown(f"**{title}**")
    with third.container(border=True):
        st.caption("Authorization")
        st.markdown("**Available**" if eligible else "**Unavailable**")
    content = _accepted_content(review)
    _summary(content)
    if classification == "CONFIRMED_BAEC":
        st.markdown(VALUE_ACCEPTED)
        st.markdown(CONFIRMED_PRIMARY)
        st.caption(CONFIRMED_SECONDARY)
    else:
        st.markdown(NOT_BAEC_PRIMARY if classification == "NOT_BAEC" else INSUFFICIENT_PRIMARY)
        for reason in _reasons(content):
            st.markdown(f"- {reason}")
        st.caption(detail)
    st.caption("The classification preview comes from the deterministic classifier, recomputed from your accepted "
               "review.")
    with st.expander("Technical details"):
        st.caption("Classifier output, exactly:")
        _inert(classification or "not available")
        if not eligible:
            st.caption("Authorization eligibility, exactly:")
            _inert(f"Reason code: {status.eligibility_failure}")
        st.caption("Stored findings, exactly:")
        for finding in content["findings"]:
            _inert(f"{finding['criterion']}: {finding['finding']} · evidence "
                   f"{', '.join(finding['evidence_selection_ids']) or 'none'}")
        _inert(f"articulation_origin: {content['articulation_origin']} · elicitation_mode: "
               f"{content['elicitation_mode']}")
    if not eligible:
        st.warning("Authorization is unavailable for this review. A BAEC can be authorized for confirmation only "
                   "when the deterministic classification is a confirmed BAEC. The classification above stands.")
        st.caption(RESTART_HINT)
        return
    st.button("Continue to Authorization", key=KEY + "to_5", type="primary", on_click=_go, args=(5,))


def _flow(granted: bool) -> None:
    """Where this step sits in the architecture. Labels only, never navigation."""
    states = ("✓ Complete", "✓ Granted" if granted else "● You are here", "○ Not executed in this browser demo")
    for column, label, state in zip(st.columns(3), FLOW, states):
        with column.container(border=True):
            st.caption(label)
            st.markdown(f":blue[**{state}**]" if state.startswith("●") else f"**{state}**")


def _authorization(status) -> None:
    st.header("5  Authorization")
    st.caption(PURPOSE[5])
    active = [g for g in status.grants if g.status == "ACTIVE"]
    _flow(bool(active))
    st.caption(SEPARATION_TEXT)
    if not active:
        st.markdown(AUTHORIZE_INTRO)
        st.caption(AUTHORIZE_TEXT)
        for grant in status.grants:
            _inert(f"Earlier authorization: {GRANT_STATUS_TEXT[grant.status]}")
        if status.eligibility_failure is None:
            st.button("Authorize BAEC Confirmation", key=KEY + "authorize", type="primary", on_click=_authorize)
        return
    [grant] = active
    st.success("AUTHORIZATION GRANTED")
    for column, (caption, value) in zip(st.columns(3), (("Valid for", "15 minutes"), ("Usage", "Single use"),
                                                        ("Execution", "Not yet performed"))):
        with column.container(border=True):
            st.caption(caption)
            st.markdown(f"**{value}**")
    _inert(f"Issued {grant.issued_at:%Y-%m-%d %H:%M} UTC · expires {grant.expires_at:%Y-%m-%d %H:%M} UTC")
    st.caption(GRANT_RULES)
    st.markdown(GRANTED_TEXT)
    with st.container(border=True):
        for line in DISTINCTIONS:
            st.markdown(f"- {line}")
    with st.container(border=True):
        st.caption("NEXT")
        st.subheader(ENGINE2_HEADING)
        st.caption(ENGINE2_SUBHEADING)
        st.markdown(ENGINE2_TEXT)
        st.markdown(f"*{ENGINE2_CLOSING}*")


def _demo() -> None:
    session = st.session_state[KEY + "session"]
    connection = recording.open_demo_database(session.image)
    try:
        review = ProposalReviewService(connection, clock=SystemClock()).load_review(session.proposal_id)
        accepted = review.review_state == "REVIEW_ACCEPTED"
        status = (ProposalAuthorizationService(connection, clock=SystemClock()).authorization_status(
            session.proposal_id) if accepted else None)
    finally:
        connection.close()
    view = st.session_state.get(KEY + "view", 1)
    if view >= 4 and not accepted:
        view = 3  # a later stage shows only after the real application action
    granted = accepted and any(g.status == "ACTIVE" for g in status.grants)
    if view == 5 and status.eligibility_failure is not None:
        view = 4
    done = (view > 1 or review.review_state != "OPEN", view > 2 or review.review_state != "OPEN",
            review.review_state != "OPEN", accepted, granted)
    _header()
    _progress(done, view)
    flash = st.session_state.pop(KEY + "flash", None)
    if flash is not None:
        kind, text, technical = flash
        {"error": st.error, "info": st.info}[kind](text)
        if technical is not None:
            st.caption(technical)  # the exact refusal codes, directly under the plain message
    if view == 1:
        _buyer_interaction(review)
    elif view == 2:
        _ai_draft(review)
    elif view == 3:
        _human_review(review)
    elif view == 4:
        _review_accepted(review, status)
    else:
        _authorization(status)
    st.divider()
    st.button("Restart Demo", key=KEY + "reset", type="tertiary", on_click=_restart)


def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, layout="centered")
    problem = recording.session_support_problem()
    if problem is not None:  # fail clearly and safely: no demo, no file fallback
        st.title(LANDING_HEADING)
        st.error("The demo cannot start on this server. " + problem)
        _footer()
        return
    if KEY + "session" not in st.session_state:
        _landing()
    else:
        _demo()
    _footer()
