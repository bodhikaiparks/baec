from datetime import datetime, timezone

import pytest

from baec_console import crm_ai


def _criterion(
    finding="UNKNOWN",
    evidence="",
    rationale="",
):
    return crm_ai.CriterionProposal(
        finding=finding,
        evidence_excerpt=evidence,
        rationale=rationale,
    )


def test_ai_assist_requires_explicit_token():
    assert crm_ai.available("") is False
    assert crm_ai.available("test-token") is True


def test_ai_model_can_be_overridden(monkeypatch):
    monkeypatch.delenv(
        "BAEC_AI_MODEL",
        raising=False,
    )

    assert (
        crm_ai.model_name()
        == "claude-sonnet-5-5"
    )

    monkeypatch.setenv(
        "BAEC_AI_MODEL",
        "test-model",
    )

    assert crm_ai.model_name() == "test-model"


def test_analysis_rejects_nonverbatim_evidence(monkeypatch):
    raw = (
        "We are not reviewing vendors now, but if pricing "
        "increases by more than 8% at March renewal, "
        "we would compare alternatives."
    )

    result = crm_ai.InteractionAnalysis(
        summary="Buyer described a future pricing condition.",
        buyer_role="Materials Manager",
        buyer_statement=(
            "We are not reviewing vendors now"
        ),
        seller_notes="",
        possible_baec=True,
        present_non_evaluation=_criterion(
            "MET",
            "We are not reviewing vendors now",
        ),
        prospective_condition=_criterion(
            "MET",
            "if pricing increases by more than 8%",
        ),
        buyer_articulation=_criterion(
            "MET",
            "we would compare alternatives",
        ),
        evaluation_linkage=_criterion(
            "MET",
            "FABRICATED EVIDENCE",
        ),
        articulation_origin="BUYER_GENERATED",
        elicitation_mode="UNKNOWN",
        threshold_text="more than 8%",
        comparator="GREATER_THAN",
        numeric_value="8",
        unit="%",
        timing_text="March renewal",
        recurrence_text="",
        qualitative_term="",
        suggested_next_action=(
            "Preserve the condition for review."
        ),
        caution="Human review required.",
    )

    monkeypatch.setattr(
        crm_ai,
        "_parse",
        lambda *args, **kwargs: result,
    )

    with pytest.raises(
        crm_ai.CRMAIError,
        match="not verbatim",
    ):
        crm_ai.analyze_interaction(
            raw_text=raw,
            token="test-token",
        )


def test_analysis_accepts_supported_exact_evidence(monkeypatch):
    raw = (
        "We are not reviewing vendors now, but if pricing "
        "increases by more than 8% at March renewal, "
        "we would compare alternatives."
    )

    result = crm_ai.InteractionAnalysis(
        summary="Buyer described a future pricing condition.",
        buyer_role="Materials Manager",
        buyer_statement=raw,
        seller_notes="",
        possible_baec=True,
        present_non_evaluation=_criterion(
            "MET",
            "We are not reviewing vendors now",
        ),
        prospective_condition=_criterion(
            "MET",
            "if pricing increases by more than 8%",
        ),
        buyer_articulation=_criterion(
            "MET",
            "we would compare alternatives",
        ),
        evaluation_linkage=_criterion(
            "MET",
            "we would compare alternatives",
        ),
        articulation_origin="BUYER_GENERATED",
        elicitation_mode="UNKNOWN",
        threshold_text="more than 8%",
        comparator="GREATER_THAN",
        numeric_value="8",
        unit="%",
        timing_text="March renewal",
        recurrence_text="",
        qualitative_term="",
        suggested_next_action=(
            "Review the proposed buyer condition."
        ),
        caution="Human confirmation is still required.",
    )

    monkeypatch.setattr(
        crm_ai,
        "_parse",
        lambda *args, **kwargs: result,
    )

    analysis = crm_ai.analyze_interaction(
        raw_text=raw,
        token="test-token",
    )

    assert analysis.possible_baec is True
    assert analysis.numeric_value == "8"
    assert analysis.comparator == "GREATER_THAN"


def _baec_analysis(raw: str):
    return crm_ai.InteractionAnalysis(
        summary="Buyer described a future pricing condition.",
        buyer_role="Materials Manager",
        buyer_statement=raw,
        seller_notes="",
        possible_baec=True,
        present_non_evaluation=_criterion(
            "MET",
            "We are not reviewing vendors now",
        ),
        prospective_condition=_criterion(
            "MET",
            "if pricing increases by more than 8%",
        ),
        buyer_articulation=_criterion(
            "MET",
            "we would compare alternatives",
        ),
        evaluation_linkage=_criterion(
            "MET",
            "we would compare alternatives",
        ),
        articulation_origin="BUYER_GENERATED",
        elicitation_mode="UNKNOWN",
        threshold_text="more than 8%",
        comparator="GREATER_THAN",
        numeric_value="8",
        unit="%",
        timing_text="March renewal",
        recurrence_text="",
        qualitative_term="",
        suggested_next_action="Review the condition.",
        caution="Human confirmation required.",
    )


def test_ai_proposal_maps_to_engine1_condition_form():
    from baec_console.ui_crm import _condition_form_from_analysis

    raw = (
        "We are not reviewing vendors now, but if pricing "
        "increases by more than 8% at March renewal, "
        "we would compare alternatives."
    )

    form = _condition_form_from_analysis(
        _baec_analysis(raw)
    )

    data = form.to_dict()

    assert data["origin"] == "BUYER_GENERATED"
    assert data["comparator"] == "GREATER_THAN"
    assert data["threshold_value"] == "8"
    assert data["threshold_unit"] == "%"
    assert data["timing_text"] == "March renewal"

    assert (
        data["criteria"]["PRESENT_NON_EVALUATION"]["finding"]
        == "MET"
    )


def test_baec_promotion_requires_exact_supported_buyer_language():
    from baec_console.ui_crm import _promotion_problem

    raw = (
        "We are not reviewing vendors now, but if pricing "
        "increases by more than 8% at March renewal, "
        "we would compare alternatives."
    )

    analysis = _baec_analysis(raw)

    assert (
        _promotion_problem(
            analysis,
            raw_notes=raw,
            buyer_statement=raw,
        )
        is None
    )

    assert _promotion_problem(
        analysis,
        raw_notes=raw,
        buyer_statement=(
            "The buyer may review vendors if prices change."
        ),
    )


# --- selected CRM contact title is authoritative over model inference --------------------------------------------

RIVERSIDE_RAW = (
    'Spoke with Morgan today. She said, "We\'re not reviewing other suture suppliers right now, but if our '
    'contracted pricing increases by at least 7% at our April renewal, we\'d compare other options." She said '
    "the current agreement remains in place and there is no active supplier evaluation underway."
)
RIVERSIDE_STATEMENT = (
    "We're not reviewing other suture suppliers right now, but if our contracted pricing increases by at least "
    "7% at our April renewal, we'd compare other options."
)


def _riverside_analysis(buyer_role: str):
    return crm_ai.InteractionAnalysis(
        summary="Buyer described a future pricing condition at the April renewal.",
        buyer_role=buyer_role,
        buyer_statement=RIVERSIDE_STATEMENT,
        seller_notes="Current agreement remains in place; no active supplier evaluation.",
        possible_baec=True,
        present_non_evaluation=_criterion("MET", "We're not reviewing other suture suppliers right now"),
        prospective_condition=_criterion(
            "MET", "if our contracted pricing increases by at least 7% at our April renewal"),
        buyer_articulation=_criterion("MET", RIVERSIDE_STATEMENT),
        evaluation_linkage=_criterion("MET", "we'd compare other options"),
        articulation_origin="BUYER_GENERATED",
        elicitation_mode="UNKNOWN",
        threshold_text="at least 7%",
        comparator="AT_LEAST",
        numeric_value="7",
        unit="%",
        timing_text="April renewal",
        recurrence_text="",
        qualitative_term="",
        suggested_next_action="Review the proposed buyer condition.",
        caution="Human confirmation is still required.",
    )


def _analyze_with(monkeypatch, model_result, **contact):
    calls = []

    def fake_parse(*args, **kwargs):
        calls.append(kwargs)
        return model_result

    monkeypatch.setattr(crm_ai, "_parse", fake_parse)
    analysis = crm_ai.analyze_interaction(raw_text=RIVERSIDE_RAW, token="test-token", **contact)
    return analysis, calls


def test_selected_contact_title_fills_blank_model_buyer_role(monkeypatch):
    analysis, _ = _analyze_with(monkeypatch, _riverside_analysis(""),
                                contact_name="Morgan Lee", contact_title="Materials Manager")
    assert analysis.buyer_role == "Materials Manager"


def test_selected_contact_title_overrides_conflicting_model_buyer_role(monkeypatch):
    analysis, _ = _analyze_with(monkeypatch, _riverside_analysis("Procurement Director"),
                                contact_name="Morgan Lee", contact_title="Materials Manager")
    assert analysis.buyer_role == "Materials Manager"


@pytest.mark.parametrize("contact", [{}, {"contact_name": "Morgan Lee", "contact_title": ""},
                                     {"contact_name": "Morgan Lee", "contact_title": "   "}])
def test_without_a_crm_title_the_model_buyer_role_is_preserved(monkeypatch, contact):
    analysis, _ = _analyze_with(monkeypatch, _riverside_analysis("Materials Manager"), **contact)
    assert analysis.buyer_role == "Materials Manager"
    blank, _ = _analyze_with(monkeypatch, _riverside_analysis(""), **contact)
    assert blank.buyer_role == ""  # unsupported stays blank; nothing is invented


def test_selected_contact_name_and_title_are_sent_to_claude_as_context(monkeypatch):
    _, calls = _analyze_with(monkeypatch, _riverside_analysis(""),
                             contact_name="Morgan Lee", contact_title="Materials Manager")
    (call,) = calls
    assert call["payload"]["known_contact"] == {"name": "Morgan Lee", "title": "Materials Manager"}
    assert call["payload"]["raw_interaction"] == RIVERSIDE_RAW


def test_crm_title_is_never_written_into_buyer_evidence(monkeypatch):
    analysis, _ = _analyze_with(monkeypatch, _riverside_analysis(""),
                                contact_name="Morgan Lee", contact_title="Materials Manager")
    evidence = [
        analysis.buyer_statement,
        analysis.present_non_evaluation.evidence_excerpt,
        analysis.prospective_condition.evidence_excerpt,
        analysis.buyer_articulation.evidence_excerpt,
        analysis.evaluation_linkage.evidence_excerpt,
        analysis.threshold_text,
        analysis.timing_text,
    ]
    assert evidence == [
        RIVERSIDE_STATEMENT,
        "We're not reviewing other suture suppliers right now",
        "if our contracted pricing increases by at least 7% at our April renewal",
        RIVERSIDE_STATEMENT,
        "we'd compare other options",
        "at least 7%",
        "April renewal",
    ]
    assert not any("Materials Manager" in value for value in evidence)


def test_model_cannot_launder_the_crm_title_into_buyer_evidence(monkeypatch):
    injected = _riverside_analysis("").model_copy(
        update={"buyer_statement": "Materials Manager: " + RIVERSIDE_STATEMENT})
    with pytest.raises(crm_ai.CRMAIError, match="not verbatim"):
        _analyze_with(monkeypatch, injected, contact_name="Morgan Lee", contact_title="Materials Manager")


# --- seller context must not call known CRM contact metadata missing ---------------------------------------------

RIVERSIDE_SELLER_CONTEXT = (
    "Morgan stated the current agreement remains in place with no active supplier evaluation. "
    "No seller-introduced framing of the condition is shown in the notes."
)
MORGAN = {"contact_name": "Morgan Lee", "contact_title": "Materials Manager"}


def _with_seller_notes(seller_notes: str):
    return _riverside_analysis("").model_copy(update={"seller_notes": seller_notes})


def test_known_title_removes_only_the_sentence_calling_it_missing(monkeypatch):
    model = _with_seller_notes(RIVERSIDE_SELLER_CONTEXT + " Morgan's title and role were not provided.")
    analysis, _ = _analyze_with(monkeypatch, model, contact_title="Materials Manager")
    assert analysis.seller_notes == RIVERSIDE_SELLER_CONTEXT
    assert "Materials Manager" not in analysis.seller_notes  # removed, not replaced with the CRM title


@pytest.mark.parametrize("contradiction", [
    "Buyer identity was not provided.",
    "The contact's name is unknown.",
    "No name was given for the buyer.",
])
def test_known_name_removes_the_sentence_calling_it_missing(monkeypatch, contradiction):
    model = _with_seller_notes(contradiction + " " + RIVERSIDE_SELLER_CONTEXT)
    analysis, _ = _analyze_with(monkeypatch, model, contact_name="Morgan Lee")
    assert analysis.seller_notes == RIVERSIDE_SELLER_CONTEXT


def test_name_only_does_not_remove_a_title_sentence(monkeypatch):
    # Only the metadata the CRM actually supplied can be contradicted.
    notes = RIVERSIDE_SELLER_CONTEXT + " Morgan's title was not provided."
    analysis, _ = _analyze_with(monkeypatch, _with_seller_notes(notes), contact_name="Morgan Lee")
    assert analysis.seller_notes == notes


@pytest.mark.parametrize("contact", [{}, {"contact_name": "", "contact_title": "  "}])
def test_without_contact_metadata_seller_context_is_unchanged(monkeypatch, contact):
    notes = RIVERSIDE_SELLER_CONTEXT + " Morgan's title and role were not provided."
    analysis, _ = _analyze_with(monkeypatch, _with_seller_notes(notes), **contact)
    assert analysis.seller_notes == notes


@pytest.mark.parametrize("notes", [
    RIVERSIDE_SELLER_CONTEXT,
    "  Morgan said the agreement stays.\n\nNo evaluation is underway.  ",
    "Renewal timing beyond April is unknown. Whether pricing will rise is not stated.",
    "Morgan's decision-making authority is unknown.",
    "Morgan's role in purchasing decisions was not stated.",
    "Whether the seller prompted the condition is unknown; the seller's role was not stated.",
    "",
])
def test_non_contradictory_seller_context_is_preserved_byte_for_byte(monkeypatch, notes):
    analysis, _ = _analyze_with(monkeypatch, _with_seller_notes(notes), **MORGAN)
    assert analysis.seller_notes == notes


def test_seller_context_guard_leaves_buyer_evidence_untouched(monkeypatch):
    model = _with_seller_notes(RIVERSIDE_SELLER_CONTEXT + " Morgan's title and role were not provided.")
    analysis, _ = _analyze_with(monkeypatch, model, **MORGAN)
    assert analysis.model_dump(exclude={"buyer_role", "seller_notes"}) == model.model_dump(
        exclude={"buyer_role", "seller_notes"})
    for value in (analysis.buyer_statement, analysis.threshold_text, analysis.timing_text,
                  analysis.present_non_evaluation.evidence_excerpt, analysis.prospective_condition.evidence_excerpt,
                  analysis.buyer_articulation.evidence_excerpt, analysis.evaluation_linkage.evidence_excerpt):
        assert "Materials Manager" not in value


def test_demo_call_notes_ai_proposal_reaches_engine1_only_as_an_unconfirmed_draft(tmp_path, monkeypatch):
    """The walkthrough's AI path, offline: a faithful structured proposal for demo_seed.CALL_NOTES passes the
    exact-evidence gate and becomes an Engine 1 draft. Nothing is confirmed without the human gate."""
    from baec_console import demo_seed, ui_crm
    from baec_console.console_service import Console

    quote = f"{demo_seed.NON_EVALUATION}. {demo_seed.BUYER_STATEMENT}"
    result = crm_ai.InteractionAnalysis(
        summary="Not evaluating other suppliers; stated a pricing condition for the April renewal.",
        buyer_role="",
        buyer_statement=quote,
        seller_notes="",
        possible_baec=True,
        present_non_evaluation=_criterion("MET", demo_seed.NON_EVALUATION),
        prospective_condition=_criterion("MET", "If our contracted pricing increases by at least 7%"),
        buyer_articulation=_criterion("MET", demo_seed.BUYER_STATEMENT),
        evaluation_linkage=_criterion("MET", "we would compare other options"),
        articulation_origin="BUYER_GENERATED",
        elicitation_mode="UNKNOWN",
        threshold_text="by at least 7%",
        comparator="AT_LEAST",
        numeric_value="7",
        unit="%",
        timing_text="at the April renewal",
        recurrence_text="",
        qualitative_term="",
        suggested_next_action="Preserve the condition for human review.",
        caution="Human review required.",
    )
    monkeypatch.setattr(crm_ai, "_parse", lambda *args, **kwargs: result)
    analysis = crm_ai.analyze_interaction(raw_text=demo_seed.CALL_NOTES, contact_name=demo_seed.CONTACT_NAME,
                                          contact_title=demo_seed.BUYER_ROLE, token="test-token")
    assert ui_crm._promotion_problem(analysis, raw_notes=demo_seed.CALL_NOTES, buyer_statement=quote) is None

    console = Console.local_development(tmp_path / "data")
    account_id = demo_seed.load(console, "call_notes")
    service = console.synthetic
    interaction_id = service.record_interaction(account_id, buyer_role=analysis.buyer_role,
                                                occurred_at=datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc),
                                                buyer_statement=quote)
    draft_id = service.capture_condition(account_id, interaction_id, ui_crm._condition_form_from_analysis(analysis))
    assert service.preview_condition(draft_id)["preview"].confirmable is True
    view = service.account_view(account_id)
    assert view.baecs == [] and view.current_baec is None and view.account.state is None
