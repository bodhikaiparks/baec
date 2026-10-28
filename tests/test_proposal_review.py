"""Phase 7E: the human-review application layer, tested without Streamlit.

Every authoritative review rule is enforced here; the Streamlit page only renders and submits. All data is
test-only fixture data (tests/review_builders.py, design D15).
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from baec_app.application import human_normalization, proposal_review
from baec_app.application.proposal_review import (
    EVIDENCE_PROVENANCE,
    NO_STRINGENCY_STATED,
    REVIEW_FAILURE_CODES,
    EvidenceSelection,
    ProposalReviewService,
    ReviewNotSaved,
    open_review_connection,
)
from baec_app.data.database import BRIDGE_TABLES, DATA_TABLES, PersistenceError
from baec_app.data.proposal_bridge import ReviewDecision, sha256_text
from baec_app.domain.enums import BaecClassification, ProvenanceCategory
from tests.persistence_builders import dump, tamper
from tests.review_builders import (  # noqa: F401
    MANUAL,
    REVIEWED_AT,
    REVIEWER,
    STATEMENT,
    SUGGESTED,
    decisions,
    findings,
    review,
    selections,
)

MODULE = Path(proposal_review.__file__)
REVIEW_TABLES = {"ai_proposal_review_revisions", "ai_proposal_review_decisions"}


def refused(review, code, call):
    before = dump(review.connection)
    with pytest.raises(ReviewNotSaved) as raised:
        call()
    assert raised.value.code == code
    assert dump(review.connection) == before  # a refusal writes nothing
    return raised.value


def accept(review, **changes):
    return review.service.accept_review(review.pid, decisions(**changes), actor_label=REVIEWER)


def changed_tables(before, after):
    return {t for t in DATA_TABLES if after[t] != before[t]}


# --- the read model -----------------------------------------------------------------------------------


def test_the_review_shows_source_ai_suggestions_and_reconstructed_provenance_separately(review):
    loaded = review.service.load_review(review.pid)
    assert loaded.interaction_text.startswith("Seller: Are you evaluating other suppliers right now?")
    assert [(e.excerpt_id, e.ai_attributed_speaker, e.status) for e in loaded.ai_suggested_excerpts] == [
        ("e1", "buyer", "AI_INFERENCE"), ("e2", "buyer", "AI_INFERENCE")]
    assert loaded.ai_normalized_condition == "A price increase of more than 10% at renewal."
    assert [(h.criterion, h.ai_status, h.status) for h in loaded.ai_criterion_hypotheses] == [
        (c, "supported", "AI_INFERENCE") for c in ("PRESENT_NON_EVALUATION", "PROSPECTIVE_CONDITION",
                                                   "BUYER_ARTICULATION", "EVALUATION_LINKAGE")]
    assert loaded.ai_uncertainties == ("Whether the buyer's statement reflects the whole buying group is not stated.",)
    # Provenance through proposal -> artifact_id -> the Phase 6 run; none of it is in the proposal snapshot.
    assert (loaded.requested_model, loaded.response_model) == ("FIXTURE-model-not-a-real-invocation",) * 2
    assert (loaded.prompt_version, loaded.validation_version, loaded.mapping_version) == (
        "baec-extraction-prompt/v2", "baec-extraction-validation/v2", "baec-ai-proposal-mapping/v1")
    for copied in (loaded.prompt_digest, loaded.request_digest, loaded.requested_model):
        assert copied not in review.proposal.content
    assert (loaded.review_state, loaded.revisions, loaded.decisions) == ("OPEN", (), ())


def test_listing_and_an_empty_database(review, tmp_path):
    assert [(p.proposal_id, p.review_state) for p in review.service.list_reviewable()] == [(review.pid, "OPEN")]
    from tests.mapping_builders import Mapping
    from tests.application_builders import FixedClock
    empty = Mapping(str(tmp_path / "empty.sqlite3"))
    try:
        assert ProposalReviewService(empty.connection, clock=FixedClock()).list_reviewable() == ()
    finally:
        empty.connection.close()


def test_loading_writes_nothing(review):
    before = dump(review.connection)
    review.service.load_review(review.pid)
    review.service.list_reviewable()
    assert dump(review.connection) == before


def test_a_tampered_proposal_is_never_reviewed(review):
    text = review.proposal.content.replace('"supported"', '"not_supported"')
    tamper(review.connection, "UPDATE ai_proposals SET content = ?, proposal_digest = ?", (text, sha256_text(text)))
    refused(review, "proposal_integrity_failure", lambda: review.service.load_review(review.pid))
    refused(review, "proposal_integrity_failure", lambda: accept(review))
    refused(review, "proposal_not_found", lambda: review.service.load_review("aiprop_none"))


def test_the_review_connection_opens_only_an_existing_current_schema_file(tmp_path, review):
    for bad in (":memory:", "file:x", str(tmp_path / "missing.sqlite3"), ""):
        with pytest.raises(PersistenceError):
            open_review_connection(bad)
    open_review_connection(review.path).close()


# --- ACCEPT ----------------------------------------------------------------------------------------------


def test_accepting_writes_exactly_one_immutable_revision_and_its_accepted_decision(review):
    before = dump(review.connection)
    accepted = accept(review)
    after = dump(review.connection)
    assert changed_tables(before, after) == REVIEW_TABLES
    assert len(after["ai_proposal_review_revisions"]) == len(after["ai_proposal_review_decisions"]) == 1
    revision, decision = accepted.revision, accepted.decision
    assert (revision.revision_number, revision.previous_revision_id, revision.actor_label) == (1, None, REVIEWER)
    assert (decision.decision, decision.review_revision_id) == (ReviewDecision.ACCEPTED, revision.review_revision_id)
    assert revision.review_content_digest == sha256_text(revision.content)
    assert review.bridge.list_revisions(review.pid) == (revision,)
    assert accepted.classification is BaecClassification.CONFIRMED_BAEC  # a display preview only
    assert review.service.load_review(review.pid).review_state == "REVIEW_ACCEPTED"


def test_the_revision_content_is_exactly_the_approved_authoritative_shape(review):
    content = json.loads(accept(review).revision.content)
    assert set(content) == {
        "review_content_version", "normalization_validation_version", "proposal_id", "proposal_digest",
        "account_id", "interaction_id", "artifact_id", "revision_number", "previous_revision_id",
        "evidence_selections", "source_selection_id", "buyer_exact_statement", "buyer_role", "findings",
        "articulation_origin", "elicitation_mode", "stringency", "normalization", "captured_at"}
    assert (content["review_content_version"], content["normalization_validation_version"]) == (
        "baec-ai-review-content/v1", "baec-human-normalization-validation/v1")
    assert content["evidence_selections"] == [
        {"selection_id": "s1", "text": SUGGESTED, "provenance": "BUYER_FACT", "suggested_excerpt_id": "e1"},
        {"selection_id": "s2", "text": MANUAL, "provenance": "BUYER_FACT", "suggested_excerpt_id": None}]
    assert content["buyer_role"] is None and content["buyer_exact_statement"] == SUGGESTED  # exactly selection s1
    assert [f["criterion"] for f in content["findings"]] == [
        "PRESENT_NON_EVALUATION", "PROSPECTIVE_CONDITION", "BUYER_ARTICULATION", "EVALUATION_LINKAGE"]
    assert content["stringency"] == {"verbatim_text": "more than 10%", "comparator": "GREATER_THAN",
                                     "numeric_value": "10", "unit": "%", "qualitative_term": None,
                                     "recurrence_text": None, "timing_text": None}
    assert content["normalization"] == {
        "condition": {"disposition": "EDITED", "ai_value": "A price increase of more than 10% at renewal.",
                      "final_value": "Pricing rising by more than 10% at renewal."},
        "evaluation_link": {"disposition": "NONE",
                            "ai_value": "The buyer says such an increase would reopen the evaluation.",
                            "final_value": None}}
    assert content["captured_at"] == "2026-04-30T09:00:00.000000+00:00"


def test_accepting_creates_no_grant_baec_authorization_or_state_change(review):
    before = dump(review.connection)
    accept(review)
    after = dump(review.connection)
    for table in DATA_TABLES:
        if table not in REVIEW_TABLES:
            assert after[table] == before[table], table
    for table in ("baec_records", "human_authorizations", "account_state_transitions", "dormancy_judgments") + tuple(
            t for t in BRIDGE_TABLES if t not in REVIEW_TABLES | {"ai_proposals"}):
        assert review.count(table) == 0, table


# --- source binding --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,code", [
    ("", "selection_blank"), ("   ", "selection_blank"),
    ("If our supplier raises pricing by MORE than 10% at renewal.", "selection_not_verbatim"),
    ("IF OUR SUPPLIER RAISES PRICING by more than 10% at renewal", "selection_not_verbatim"),
    ("If our supplier raises prices by over 10% at renewal.", "selection_not_verbatim"),
    ("The buyer would reopen the evaluation after a 10% rise.", "selection_not_verbatim"),
], ids=["empty", "blank", "rewritten", "case changed", "paraphrased", "summarized"])
def test_every_selection_is_rechecked_against_the_stored_source(review, text, code):
    bad = (EvidenceSelection("s1", text, ProvenanceCategory.BUYER_FACT, None),) + selections()[1:]
    refused(review, code, lambda: accept(review, evidence_selections=bad))


def test_text_from_another_stored_interaction_is_refused(review):
    from datetime import datetime, timezone
    from baec_app.data.records import SourceInteraction
    from baec_app.data.repository import Repository
    other = "Buyer: If lead times exceed six weeks, we would look at other suppliers."
    Repository(review.connection).add_interaction(SourceInteraction(
        "FIXTURE-INT-9", "FIXTURE-ACC-1", datetime(2026, 4, 1, tzinfo=timezone.utc), other))
    bad = (EvidenceSelection("s1", other, ProvenanceCategory.BUYER_FACT, None),) + selections()[1:]
    refused(review, "selection_not_verbatim", lambda: accept(review, evidence_selections=bad))


def test_any_exact_contiguous_span_is_accepted_not_only_ai_suggestions(review):
    span = "Buyer: We would not switch for anything under 10%."
    own = selections() + (EvidenceSelection("s3", span, ProvenanceCategory.SELLER_OBSERVATION, None),)
    content = json.loads(accept(review, evidence_selections=own).revision.content)
    assert content["evidence_selections"][2] == {"selection_id": "s3", "text": span, "provenance": "SELLER_OBSERVATION",
                                                 "suggested_excerpt_id": None}


def test_a_selection_claiming_an_ai_suggestion_must_equal_it_and_selections_are_unique(review):
    claimed = (EvidenceSelection("s1", MANUAL, ProvenanceCategory.BUYER_FACT, "e1"),) + selections()[1:]
    refused(review, "selection_suggestion_mismatch", lambda: accept(review, evidence_selections=claimed))
    twice = selections() + (EvidenceSelection("s3", MANUAL, ProvenanceCategory.BUYER_FACT, None),)
    refused(review, "selection_duplicate", lambda: accept(review, evidence_selections=twice))
    refused(review, "selection_none", lambda: accept(review, evidence_selections=()))


# --- provenance is a human assertion -------------------------------------------------------------------


@pytest.mark.parametrize("provenance", [ProvenanceCategory.AI_INFERENCE, ProvenanceCategory.UNKNOWN,
                                        ProvenanceCategory.EXTERNAL_EVIDENCE, "BUYER_FACT", None])
def test_only_buyer_fact_or_seller_observation_may_be_asserted(review, provenance):
    bad = (EvidenceSelection("s1", SUGGESTED, provenance, "e1"),) + selections()[1:]
    refused(review, "provenance_invalid", lambda: accept(review, evidence_selections=bad))
    assert EVIDENCE_PROVENANCE == (ProvenanceCategory.BUYER_FACT, ProvenanceCategory.SELLER_OBSERVATION)


def test_the_ai_speaker_never_sets_provenance(review):
    # The AI attributed e1 to the buyer; the human asserts SELLER_OBSERVATION, and that is what is stored.
    loaded = review.service.load_review(review.pid)
    assert loaded.ai_suggested_excerpts[0].ai_attributed_speaker == "buyer"
    content = json.loads(accept(review, evidence_selections=selections(ProvenanceCategory.SELLER_OBSERVATION),
                                buyer_exact_statement=None).revision.content)
    assert content["evidence_selections"][0]["provenance"] == "SELLER_OBSERVATION"
    source = MODULE.read_text(encoding="utf-8")
    assert source.count("ai_attributed_speaker") == 2  # the read-model field and its display value only


# --- buyer exact statement -----------------------------------------------------------------------------


def test_the_buyer_exact_statement_requires_a_buyer_fact_source_and_verbatim_text(review):
    """Name kept for ID continuity. The rule is now exact selection identity: see the lineage tests below."""
    refused(review, "buyer_statement_requires_buyer_fact",
            lambda: accept(review, evidence_selections=selections(ProvenanceCategory.SELLER_OBSERVATION)))
    refused(review, "buyer_statement_not_selected", lambda: accept(review, buyer_exact_statement="we'd reopen it"))
    refused(review, "source_selection_unknown", lambda: accept(review, source_selection_id="s9"))


# Lineage: stored source -> exact contiguous selection -> explicit human BUYER_FACT -> exact text equality.


def test_an_exact_selected_buyer_fact_text_is_accepted_as_the_buyer_exact_statement(review):
    content = json.loads(accept(review, buyer_exact_statement=SUGGESTED).revision.content)
    (s1,) = [s for s in content["evidence_selections"] if s["text"] == content["buyer_exact_statement"]]
    assert (s1["selection_id"], s1["provenance"]) == ("s1", "BUYER_FACT")


def test_a_substring_of_a_selection_without_its_own_selection_is_refused(review):
    assert STATEMENT in SUGGESTED  # inside the BUYER_FACT selection s1, but not itself selected
    refused(review, "buyer_statement_not_selected", lambda: accept(review, buyer_exact_statement=STATEMENT))


def test_the_same_substring_added_as_its_own_buyer_fact_selection_is_accepted(review):
    own = selections() + (EvidenceSelection("s3", STATEMENT, ProvenanceCategory.BUYER_FACT, None),)
    content = json.loads(accept(review, evidence_selections=own, buyer_exact_statement=STATEMENT).revision.content)
    assert content["buyer_exact_statement"] == STATEMENT
    assert content["evidence_selections"][2] == {"selection_id": "s3", "text": STATEMENT, "provenance": "BUYER_FACT",
                                                 "suggested_excerpt_id": None}


def test_an_exact_seller_observation_text_is_refused_as_the_buyer_exact_statement(review):
    own = selections() + (EvidenceSelection("s3", STATEMENT, ProvenanceCategory.SELLER_OBSERVATION, None),)
    refused(review, "buyer_statement_requires_buyer_fact",
            lambda: accept(review, evidence_selections=own, buyer_exact_statement=STATEMENT))


def test_an_ai_attributed_buyer_without_a_human_buyer_fact_assertion_is_refused(review):
    assert review.service.load_review(review.pid).ai_suggested_excerpts[0].ai_attributed_speaker == "buyer"
    refused(review, "buyer_statement_requires_buyer_fact", lambda: accept(
        review, evidence_selections=selections(ProvenanceCategory.SELLER_OBSERVATION), buyer_exact_statement=SUGGESTED))


def test_review_accepted_is_not_baec_confirmed(review):
    accepted = accept(review)
    assert "Review Accepted ≠ BAEC Confirmed" in proposal_review.AcceptedReview.__doc__
    assert "Review Accepted ≠ BAEC Confirmed" in proposal_review.__doc__
    assert accepted.classification is BaecClassification.CONFIRMED_BAEC  # a preview only:
    assert review.count("baec_records") == review.count("human_authorizations") == 0  # nothing confirmed or persisted


# --- the four findings, origin, mode -------------------------------------------------------------------


def test_all_four_criterion_findings_are_required(review):
    refused(review, "criteria_incomplete", lambda: accept(review, findings=findings()[:3]))
    refused(review, "criteria_incomplete", lambda: accept(review, findings=findings()[:3] + findings()[:1]))
    refused(review, "criteria_incomplete", lambda: accept(review, findings=findings()[:3] + (None,)))
    unset = findings()[:3] + (replace(findings()[3], finding=None),)
    refused(review, "criteria_incomplete", lambda: accept(review, findings=unset))


def test_finding_evidence_must_be_selected_and_met_needs_evidence(review):
    unknown = (replace(findings()[0], evidence_selection_ids=("s9",)),) + findings()[1:]
    refused(review, "finding_evidence_unknown", lambda: accept(review, findings=unknown))
    bare = (replace(findings()[0], evidence_selection_ids=()),) + findings()[1:]
    refused(review, "candidate_invalid", lambda: accept(review, findings=bare))
    refused(review, "candidate_invalid", lambda: accept(review, articulation_origin=None))
    refused(review, "candidate_invalid", lambda: accept(review, elicitation_mode="CEE_ELICITED"))


# --- stringency and buyer role -------------------------------------------------------------------------


def test_stringency_is_an_explicit_decision(review):
    refused(review, "stringency_unset", lambda: accept(review, stringency=None))
    from baec_app.domain.enums import ThresholdComparator
    from baec_app.domain.models import StringencyExpression
    from decimal import Decimal
    invented = StringencyExpression("more than 15%", ThresholdComparator.GREATER_THAN, Decimal("15"), "%")
    refused(review, "stringency_not_verbatim", lambda: accept(review, stringency=invented))
    content = json.loads(accept(review, stringency=NO_STRINGENCY_STATED).revision.content)
    assert content["stringency"] is None


def test_buyer_role_is_always_none_and_cannot_be_supplied(review):
    import dataclasses
    assert "buyer_role" not in {f.name for f in dataclasses.fields(proposal_review.ReviewDecisions)}
    assert json.loads(accept(review).revision.content)["buyer_role"] is None


# --- normalization ----------------------------------------------------------------------------------------


def test_the_human_normalization_contract_runs_on_the_selected_source_bound_texts_only(review, monkeypatch):
    seen = []
    real = proposal_review.validate_final_normalization
    monkeypatch.setattr(proposal_review, "validate_final_normalization",
                        lambda **kwargs: seen.append(kwargs) or real(**kwargs))
    accept(review)
    assert seen == [{"normalized_condition": "Pricing rising by more than 10% at renewal.",
                     "normalized_evaluation_link": None, "evidence_texts": (SUGGESTED, MANUAL)}]


def test_an_unsupported_final_normalization_is_refused_with_its_codes(review):
    error = refused(review, "normalization_invalid",
                    lambda: accept(review, final_normalized_condition="Pricing rising by at least 10% at renewal."))
    assert error.normalization_codes == ("human_normalization_condition_comparator_changed",)
    only_manual = (selections()[1],)
    error = refused(review, "normalization_invalid", lambda: accept(
        review, evidence_selections=only_manual, source_selection_id="s2", buyer_exact_statement=None,
        findings=tuple(replace(f, evidence_selection_ids=("s2",)) for f in findings()),
        stringency=NO_STRINGENCY_STATED))
    assert error.normalization_codes == ("human_normalization_condition_number_unsupported",)  # never the whole text


def test_keeping_the_ai_normalization_is_recorded_as_keep_ai(review):
    content = json.loads(accept(review, final_normalized_condition="A price increase of more than 10% at renewal.")
                         .revision.content)
    assert content["normalization"]["condition"]["disposition"] == "KEEP_AI"


# --- actor --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["", "  ", None, 7])
def test_a_reviewer_label_is_required_for_every_decision(review, actor):
    refused(review, "actor_blank", lambda: review.service.accept_review(review.pid, decisions(), actor_label=actor))
    refused(review, "actor_blank", lambda: review.service.reject_proposal(review.pid, actor_label=actor))


def test_the_actor_label_is_documented_as_self_asserted():
    assert "self-asserted in this prototype; there is no authentication" in " ".join(proposal_review.__doc__.split())


# --- atomicity -------------------------------------------------------------------------------------------


def test_revision_and_decision_are_written_together_or_not_at_all(review):
    review.connection.execute("CREATE TRIGGER fail_decisions BEFORE INSERT ON ai_proposal_review_decisions "
                              "BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
    refused(review, "conflict", lambda: accept(review))
    assert review.count("ai_proposal_review_revisions") == 0  # the revision was rolled back with the decision


# --- revisions are immutable and append --------------------------------------------------------------------


def test_an_edit_appends_revision_2_and_never_changes_revision_1(review):
    first = accept(review)
    stored_first = review.bridge.get_revision(first.revision.review_revision_id)
    second = accept(review, final_normalized_condition="More than 10% at renewal.")
    assert review.bridge.get_revision(first.revision.review_revision_id) == stored_first == first.revision
    assert (second.revision.revision_number, second.revision.previous_revision_id) == (
        2, first.revision.review_revision_id)
    assert second.revision.review_content_digest != first.revision.review_content_digest
    decisions_now = review.bridge.list_decisions(review.pid)
    assert [(d.decision, d.review_revision_id) for d in decisions_now] == [
        (ReviewDecision.ACCEPTED, first.revision.review_revision_id),
        (ReviewDecision.ACCEPTED, second.revision.review_revision_id)]


# --- REJECT ------------------------------------------------------------------------------------------------


def test_rejecting_writes_one_terminal_decision_and_nothing_else(review):
    before = dump(review.connection)
    rejection = review.service.reject_proposal(review.pid, actor_label=REVIEWER)
    after = dump(review.connection)
    assert changed_tables(before, after) == {"ai_proposal_review_decisions"}
    assert (rejection.decision, rejection.review_revision_id, rejection.actor_label) == (
        ReviewDecision.REJECTED, None, REVIEWER)
    assert review.service.load_review(review.pid).review_state == "REVIEW_REJECTED"
    refused(review, "proposal_closed", lambda: review.service.reject_proposal(review.pid, actor_label=REVIEWER))
    refused(review, "proposal_closed", lambda: accept(review))


def test_a_review_can_be_rejected_after_acceptance_and_history_is_kept(review):
    accepted = accept(review)
    review.service.reject_proposal(review.pid, actor_label=REVIEWER)
    assert review.bridge.get_revision(accepted.revision.review_revision_id) == accepted.revision
    assert [d.decision for d in review.bridge.list_decisions(review.pid)] == [ReviewDecision.ACCEPTED,
                                                                             ReviewDecision.REJECTED]


# --- boundaries -----------------------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_review_layer_has_no_grant_baec_authorization_state_or_ui_path():
    imported = _imports(MODULE)
    assert not {name for name in imported if name.startswith(("streamlit", "baec_app.ai", "baec_app.mcp"))}
    for forbidden in ("baec_app.domain.state_machine", "baec_app.application.authority",
                      "baec_app.application.approval", "baec_app.application.facades"):
        assert forbidden not in imported, forbidden
    code = MODULE.read_text(encoding="utf-8").split('"""', 2)[2]
    for forbidden in ("human_authorization_grant", "ai_proposal_confirmations", "HumanAuthorization",
                      "create_confirmed_baec_record", "save_confirmed_baec", "persist_transition",
                      "record_dormancy_judgment", "staleness", "issue_grant", "authorize_confirmation", "confirm_baec"):
        assert forbidden not in code, forbidden
    public = {name for name in dir(ProposalReviewService) if not name.startswith("_")}
    assert public == {"list_reviewable", "load_review", "accept_review", "reject_proposal"}


def test_the_failure_codes_are_closed():
    assert REVIEW_FAILURE_CODES == (
        "actor_blank", "proposal_not_found", "proposal_integrity_failure", "proposal_closed", "selection_none",
        "selection_blank", "selection_duplicate", "selection_not_verbatim", "selection_suggestion_mismatch",
        "provenance_invalid", "source_selection_unknown", "buyer_statement_not_selected",
        "buyer_statement_requires_buyer_fact", "criteria_incomplete", "finding_evidence_unknown", "stringency_unset",
        "stringency_not_verbatim", "normalization_invalid", "candidate_invalid", "conflict")
    with pytest.raises(ValueError):
        ReviewNotSaved("something else")


def test_accepted_and_rejected_are_documented_as_review_decisions_only():
    doc = " ".join(proposal_review.__doc__.split())
    assert "ACCEPTED means only that a human finalized this immutable review revision" in doc
    assert "It does not confirm a BAEC, establish purchase intent, activate an account, issue an authorization " \
           "grant, or authorize any execution" in doc
    assert human_normalization  # the validator is the locked 7F-A contract, imported, never copied
