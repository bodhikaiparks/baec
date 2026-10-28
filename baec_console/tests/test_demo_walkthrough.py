"""The public demo walkthrough (docs/DEMO_WALKTHROUGH.md), end to end on synthetic data, offline.

Steps 1 to 4 run through the Streamlit app itself: load the call-notes demo, record the notes through CRM intake,
analyze them with a stubbed Claude response (no network, no key), save for BAEC review, preview, and confirm. Steps
5 to 10 make the same service calls the workspace forms make, each one an explicit human action, and then every
workspace section is rendered. No step is pre-confirmed, and nothing sends, schedules, or contacts anyone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from baec_app.domain.enums import BaecClassification
from baec_app.engine3.enums import HumanReviewDecision
from baec_console import crm_ai, demo_seed
from baec_console.console_service import Console
from baec_console.engine2_adapter import ObservationForm, SourceForm
from baec_app.engine2.domain import DimensionFinding, SufficiencyFinding
from baec_app.engine2.measurement import Transformation

from .conftest import ATTEST, REQUIRED
from .test_crm_ai import _criterion

APP = str(Path(__file__).resolve().parents[1] / "app.py")
QUOTE = f"{demo_seed.NON_EVALUATION}. {demo_seed.BUYER_STATEMENT}"


def _stub_analysis() -> crm_ai.InteractionAnalysis:
    return crm_ai.InteractionAnalysis(
        summary="Not evaluating other suppliers; stated a pricing condition for the April renewal.",
        buyer_role="", buyer_statement=QUOTE, seller_notes="", possible_baec=True,
        present_non_evaluation=_criterion("MET", demo_seed.NON_EVALUATION),
        prospective_condition=_criterion("MET", "If our contracted pricing increases by at least 7%"),
        buyer_articulation=_criterion("MET", demo_seed.BUYER_STATEMENT),
        evaluation_linkage=_criterion("MET", "we would compare other options"),
        articulation_origin="BUYER_GENERATED", elicitation_mode="UNKNOWN", threshold_text="by at least 7%",
        comparator="AT_LEAST", numeric_value="7", unit="%", timing_text="at the April renewal", recurrence_text="",
        qualitative_term="", suggested_next_action="Preserve the condition for human review.",
        caution="Human review required.")


def _click(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def _texts(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def _section(at: AppTest, label: str) -> None:
    (button,) = [b for b in at.button if b.label == label and (b.key or "").startswith(("section-nav-", "record-nav-"))]
    button.click().run()
    assert not at.exception, at.exception


def test_demo_walkthrough_from_call_notes_to_human_reviewed_preparation(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(data))
    calls = []
    monkeypatch.setattr(crm_ai, "_parse", lambda *args, **kwargs: calls.append(kwargs) or _stub_analysis())

    # 1. Load the demo account (account + contact only) and record the call notes through CRM intake.
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    _click(at, "seed-call-notes")
    console = Console.local_development(data)
    (account_id,) = console.synthetic.account_ids()
    (contact,) = console.synthetic.crm_contacts(account_id)
    assert console.synthetic.account_view(account_id).interactions == []

    # 2. Claude-assisted analysis with the visitor's own key (stubbed here), held only in this session.
    _section(at, "Overview")
    at.text_input(key="baec-ai-token").input("test-only-session-key").run()
    at.selectbox(key=f"crm-intake-contact-{account_id}").set_value(contact["contact_id"]).run()
    at.text_area(key=f"crm-intake-raw-{account_id}").input(demo_seed.CALL_NOTES).run()
    _click(at, f"crm-analyze-{account_id}")
    assert calls and calls[0]["token"] == "test-only-session-key"
    assert calls[0]["payload"]["raw_interaction"] == demo_seed.CALL_NOTES

    # 3. A possible condition is shown for review; nothing is recorded until the person chooses to save it.
    assert "Possible buyer-articulated condition" in _texts(at)
    assert console.synthetic.crm_activities(account_id) == []
    _click(at, f"crm-promote-{account_id}")
    view = console.synthetic.account_view(account_id)
    (draft,) = view.drafts
    assert view.baecs == [] and view.current_baec is None  # captured is not confirmed

    # 4. Engine 1 human confirmation: preview first, then the person confirms what was previewed.
    draft_id = draft["draft_id"]
    if not [b for b in at.button if b.key == f"cf-{draft_id}"]:
        _click(at, f"pv-{draft_id}")
    assert "Exactly what will be recorded" in _texts(at)
    _click(at, f"cf-{draft_id}")
    view = console.synthetic.account_view(account_id)
    baec = view.current_baec
    assert baec is not None and baec.classification is BaecClassification.CONFIRMED_BAEC
    assert view.account.state is None  # confirming a condition does not change account state
    service = console.synthetic

    # 5. Monitoring, authorized by the person, on one authorized source.
    service.start_monitoring(
        account_id, baec.baec_id, target_entities=(f"{demo_seed.SUPPLIER} (synthetic)",), required=REQUIRED,
        sources=(SourceForm("Supplier renewal notice", f"{demo_seed.SUPPLIER} (synthetic)",
                            "Forwarded by the buyer under the account agreement", "Buyer forwards notices by email",
                            "Notice text and dates"),))
    source_id = next(iter(service.account_view(account_id).sources))

    # 6. The synthetic renewal notice, then its correction (the first stays in history, superseded).
    observed = datetime.now(timezone.utc) + timedelta(minutes=5)
    effective = datetime(2027, 4, 1, tzinfo=timezone.utc)
    common = dict(source_id=source_id, effective_at=effective, value_unit="USD per year", baseline_value="120000",
                  transformation=Transformation.PERCENT_CHANGE_FROM_TWO_PRICES.value)
    first = service.add_observation(baec.baec_id, ObservationForm(
        observed_at=observed, content=demo_seed.INITIAL_NOTICE, new_value="130800",
        source_locator="Forwarded renewal notice", flag_for_review=False, **common))
    result = service.add_observation(baec.baec_id, ObservationForm(
        observed_at=observed + timedelta(minutes=1), content=demo_seed.CORRECTED_NOTICE, new_value="128400",
        source_locator="Forwarded correction", corrects_observation_id=first.observation_id, **common))
    view = service.account_view(account_id)
    assert {str(m.output_value) for m in view.measurements} == {"9.00", "7.00"} and len(view.superseded) == 1

    # 7. Engine 2 correspondence: the person answers every required check; the outcome is derived from them.
    packet = service.review_packet(result.candidate_id)
    recorded = service.submit_correspondence_review(
        result.candidate_id, displayed_context_digest=packet.context_digest,
        findings={d: DimensionFinding.SUPPORTED.value for d in packet.plan.required_dimensions}, notes={},
        sufficiency=SufficiencyFinding.YES.value)
    assert recorded.outcome.value == "HUMAN_VERIFIED_CORRESPONDENCE"
    assert service.account_view(account_id).account.state is None  # correspondence is not an opportunity

    # 8-9. Prepare: a human Preparation Request; Engine 3 validates the Why Now, email, call opening, and questions.
    from .conftest import permitted_request
    request_id = service.request_preparation(recorded.review.review_id, permitted_request(result))
    preparation_id = service.generate_preparation(request_id)
    outputs = service.preparation_view(preparation_id)["preparation"]["artifacts"]
    assert {o["artifact"]["artifact_id"] for o in outputs if o["artifact"]} == {"WHY-NOW", "EMAIL", "CALL", "QUESTIONS"}
    assert all(o["report"]["allowed"] for o in outputs)

    # 10. Human review of each draft. Accepting a draft sends nothing.
    for artifact_id in ("WHY-NOW", "EMAIL", "CALL", "QUESTIONS"):
        service.review_artifact(preparation_id, artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED, attestations=ATTEST)
    view = service.account_view(account_id)
    assert view.stage == "PREPARED" and view.account.state is None

    # 11. Timeline and every section render; there is still no send, schedule, or outreach control.
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["account"] = account_id
    at.query_params["section"] = "Overview"
    at.run()
    assert not at.exception, at.exception
    for section in ("Remember", "Monitor", "Recognize", "Prepare", "Timeline"):
        _section(at, section)
        labels = {b.label for b in at.button}
        assert not {"Send", "Send email", "Schedule", "Ready to send", "Approved for outreach"} & labels
    human = [i for i in service.timeline(account_id) if i.actor_kind == "Human"]
    assert human and all(i.actor == console.actor for i in human)
