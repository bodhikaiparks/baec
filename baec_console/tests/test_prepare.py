"""PREPARE: Engine 3 eligibility, deterministic preparation, validation, and human preparation review."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import pytest

from baec_app.engine3.enums import HumanReviewDecision
from baec_app.engine3.reason_codes import ReasonCode
from baec_console import engine3_adapter
from baec_console.console_service import Console, ConsoleError, PreparationRefused
from baec_console.deterministic_preparation import GENERATOR, G_STATEMENT
from baec_console.engine3_adapter import segment_to_dict
from baec_console.ui_common import when

from .conftest import ACTOR, ATTEST, StepClock, monitored, observe, permitted_request, review, verified, make_console


def _outputs(console, preparation_id):
    return {o["artifact"]["artifact_type"]: o for o in
            console.local.preparation_view(preparation_id)["preparation"]["artifacts"]}


def _prepared(console, **overrides):
    account_id, baec_id, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result, **overrides))
    return account_id, result, request_id, console.local.generate_preparation(request_id)


def test_unverified_correspondence_cannot_request_preparation(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    from baec_app.engine2.domain import DimensionFinding, SufficiencyFinding
    recorded = review(console, result.candidate_id, DimensionFinding.UNRESOLVED, SufficiencyFinding.UNKNOWN)
    with pytest.raises(ConsoleError, match="Only human-verified correspondence"):
        console.local.request_preparation(recorded.review.review_id, permitted_request(result))
    _, report = console.local.preparation_eligibility(recorded.review.review_id)
    assert report.issues[0].reason_code is ReasonCode.OUTCOME_NOT_HUMAN_VERIFIED  # Engine 3 agrees


def test_verified_correspondence_reaches_a_preparation_request_that_authorizes_nothing_else(console):
    account_id, baec_id, result, review_id = verified(console)
    snap, report = console.local.preparation_eligibility(review_id)
    assert report.allowed and snap["lineage_reconstructable"]
    request_id = console.local.request_preparation(review_id, permitted_request(result))
    view = console.local.account_view(account_id)
    assert [r["request_id"] for r in view.requests] == [request_id]
    assert view.preparations == []  # a request is not a preparation
    assert view.account.state is None  # and never an account-state change
    assert view.next_action == "Generate preparation"


def test_preparation_goes_through_the_engine3_validator(console, monkeypatch):
    calls = []
    real = engine3_adapter.validate_case

    def spy(case):
        calls.append(case)
        return real(case)

    monkeypatch.setattr(engine3_adapter, "validate_case", spy)
    _, _, _, preparation_id = _prepared(console)
    artifact_cases = [c for c in calls if c.artifact is not None]
    assert {c.artifact.artifact_id for c in artifact_cases} == {"WHY-NOW", "EMAIL", "CALL", "QUESTIONS"}
    view = console.local.preparation_view(preparation_id)
    assert view["preparation"]["generator"] == GENERATOR
    assert view["status"] == "Needs review"


def test_valid_preparation_reaches_human_review(console):
    _, _, _, preparation_id = _prepared(console)
    outputs = _outputs(console, preparation_id)
    assert all(o["report"]["allowed"] for o in outputs.values())
    pending = [r for r in console.review_queue()["pending"] if r["type"] == "Draft review"]
    assert len(pending) == 4


def test_missing_disclosure_permission_is_refused_not_repaired(console):
    _, _, _, preparation_id = _prepared(console, dispositions={})  # every fact UNKNOWN: Engine 3's default
    outputs = _outputs(console, preparation_id)
    email = outputs["FOLLOW_UP_EMAIL"]
    assert not email["report"]["allowed"]
    assert email["report"]["issues"][0]["reason_code"] == ReasonCode.BUYER_STATEMENT_NOT_PERMITTED.value
    assert "When we last spoke" in email["artifact"]["rendered_text"]  # stored exactly as built
    assert outputs["INTERNAL_WHY_NOW_SUMMARY"]["report"]["allowed"]  # internal output may use internal grounding
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)


def test_quote_where_only_paraphrase_is_permitted_is_refused(console):
    _, _, _, preparation_id = _prepared(console, statement_use="QUOTE")
    email = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]
    assert email["report"]["issues"][0]["reason_code"] == ReasonCode.DIRECT_QUOTE_EXCEEDS_PARAPHRASE_PERMISSION.value


def test_internal_only_evidence_never_reaches_buyer_facing_outputs(console):
    account_id, baec_id, result, review_id = verified(console)
    request = permitted_request(result)
    request["dispositions"] = {**request["dispositions"],
                               f"G-MEASUREMENT-{result.measurement_id}": "INTERNAL_ONLY"}
    request_id = console.local.request_preparation(review_id, request)
    outputs = _outputs(console, console.local.generate_preparation(request_id))
    assert outputs["FOLLOW_UP_EMAIL"]["report"]["issues"][0]["reason_code"] == ReasonCode.FACT_INTERNAL_ONLY.value


def test_accepted_draft_is_prepared_for_human_use_only(console):
    account_id, _, _, preparation_id = _prepared(console)
    for artifact_id in ("WHY-NOW", "EMAIL", "CALL", "QUESTIONS"):
        console.local.review_artifact(preparation_id, artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)
    view = console.local.account_view(account_id)
    assert view.stage == "PREPARED"
    assert view.account.state is None  # preparation never changes account state
    assert console.local.preparation_view(preparation_id)["status"] == "Accepted"


def test_acceptance_requires_every_attestation(console):
    _, _, _, preparation_id = _prepared(console)
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations={**ATTEST, "permitted": False})


def test_rejected_draft_stays_rejected(console):
    _, _, _, preparation_id = _prepared(console)
    console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.REJECT, attestations={},
                                  reason="Not the right moment")
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)
    assert console.local.preparation_view(preparation_id)["reviews"]["EMAIL"]["decision"] == "REJECT"


def test_edit_preserves_the_original_and_is_validated_again(console):
    _, _, _, preparation_id = _prepared(console)
    original = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]["artifact"]
    segments = [dict(s) for s in original["segments"]]
    segments[-1] = {**segments[-1], "text": "Would a short call next week be useful?"}
    edit = console.local.edit_artifact(preparation_id, "EMAIL", segments)
    assert edit["report"]["allowed"]
    assert edit["artifact"]["artifact_id"] != "EMAIL"
    console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.EDIT, attestations=ATTEST,
                                  edit_id=edit["edit_id"])
    after = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]["artifact"]
    assert after == original  # the generated draft is unchanged
    view = console.local.preparation_view(preparation_id)
    assert view["edits"][0]["artifact"]["artifact"]["segments"][-1]["text"] == "Would a short call next week be useful?"
    assert view["reviews"]["EMAIL"]["edit_id"] == edit["edit_id"]


def test_ungrounded_human_edit_is_refused(console):
    _, _, _, preparation_id = _prepared(console)
    original = _outputs(console, preparation_id)["FOLLOW_UP_EMAIL"]["artifact"]
    from baec_app.engine3.enums import ClaimSemanticClass, ClaimType
    from baec_app.engine3.models import ClaimField, DraftSegment
    added = segment_to_dict(DraftSegment("H1", "We can guarantee a 10% saving at renewal.",
                                         ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.SELLER_CAPABILITY,
                                         claim_field=ClaimField.SELLER_CAPABILITY))
    edit = console.local.edit_artifact(preparation_id, "EMAIL", original["segments"] + [added])
    assert not edit["report"]["allowed"]
    assert edit["report"]["issues"][0]["reason_code"] == ReasonCode.HUMAN_EDIT_UNGROUNDED.value
    with pytest.raises(ConsoleError):
        console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.EDIT, attestations=ATTEST,
                                      edit_id=edit["edit_id"])


def test_corrected_evidence_makes_the_request_stale(console):
    account_id, baec_id, result, review_id = verified(console)
    request_id = console.local.request_preparation(review_id, permitted_request(result))
    observe(console, account_id, baec_id, new_price="41000", content="Correction: the fee changes to USD 41,000.",
            corrects=result.observation_id, flag=False)
    with pytest.raises(PreparationRefused) as refused:
        console.local.generate_preparation(request_id)
    codes = {i.reason_code for i in refused.value.report.issues}
    assert ReasonCode.EVIDENCE_CONTEXT_CHANGED in codes  # Engine 3 refuses (context first); nothing generated
    assert console.local.account_view(account_id).preparations == []
    assert "Preparation refused by Engine 3" in " ".join(i.title for i in console.local.timeline(account_id))
    _, report = console.local.preparation_eligibility(review_id)
    assert ReasonCode.REQUIRED_EVIDENCE_NOT_USABLE in {i.reason_code for i in report.issues}


def test_a_later_review_is_reported_not_treated_as_superseding(console):
    """Engine 2 records no review supersession, so recency is never used; the later review is shown to the human."""
    from baec_app.engine2.domain import DimensionFinding, SufficiencyFinding
    account_id, baec_id, result, review_id = verified(console)
    later = review(console, result.candidate_id, DimensionFinding.UNRESOLVED, SufficiencyFinding.NO)
    snap, report = console.local.preparation_eligibility(review_id)
    assert report.allowed
    assert snap["later_reviews"] == [{"review_id": later.review.review_id, "outcome": "POSSIBLE_CORRESPONDENCE"}]
    (entry,) = console.local.account_view(account_id).verified_correspondences()
    assert [r.review_id for r, _ in entry["later_reviews"]] == [later.review.review_id]


def test_paraphrase_is_required_when_paraphrasing(console):
    account_id, baec_id, result, review_id = verified(console)
    with pytest.raises(ConsoleError):
        console.local.request_preparation(review_id, permitted_request(result, paraphrase=" "))


def test_preparation_records_are_append_only(console):
    _, _, _, preparation_id = _prepared(console)
    connection = sqlite3.connect(console.local.console_path)
    try:
        for sql in ("UPDATE preparations SET generator = 'x'", "DELETE FROM preparations",
                    "UPDATE preparation_requests SET context_digest = 'x'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
    finally:
        connection.close()


def test_buyer_statement_grounding_is_marked_as_buyer_statement(console):
    account_id, baec_id, result, review_id = verified(console)
    snap = console.local.preparation_snapshot(review_id)
    items = {g.grounding_item_id: g for g in engine3_adapter.grounding_items(snap, permitted_request(result))}
    assert items[G_STATEMENT].is_buyer_statement and items[G_STATEMENT].exact_value == snap["buyer_statement"]
    assert items["G-CORRESPONDENCE"].disposition.value == "INTERNAL_ONLY"


# --- seller paraphrase lead-in (live acceptance run: "you mentioned that You mentioned that ...") ----------------

CONDITION_TEXT = "a price increase above 8% at your March renewal could make it worth comparing other options"


@pytest.mark.parametrize("paraphrase", [
    f"You mentioned that {CONDITION_TEXT}.",
    f"you mentioned that {CONDITION_TEXT}",
    f"When we last spoke, you mentioned that {CONDITION_TEXT}.",
    f"{CONDITION_TEXT}.",
])
def test_paraphrase_lead_in_appears_once_in_email_and_call(console, paraphrase):
    *_, preparation_id = _prepared(console, paraphrase=paraphrase)
    outputs = _outputs(console, preparation_id)
    for kind in ("FOLLOW_UP_EMAIL", "CALL_OPENING"):
        text = outputs[kind]["artifact"]["rendered_text"]
        assert f"When we last spoke, you mentioned that {CONDITION_TEXT}." in text, (kind, text)
        assert "you mentioned that you mentioned that" not in text.lower(), (kind, text)
        assert text.lower().count("you mentioned") == 1, (kind, text)
        assert outputs[kind]["report"]["allowed"]  # Engine 3 still validates the composed draft
    # The human-written paraphrase is stored exactly as entered; only the composed sentence drops the lead-in.
    assert console.local.preparation_view(preparation_id)["request"]["request"]["paraphrase"] == paraphrase


# --- multiple confirmed conditions on one account (live acceptance run: Harborview >10% January, then >8% March) --

OLDER_STATEMENT = ("We're not reviewing other suture suppliers right now, but if our contracted pricing increases by "
                   "more than 10% at our January renewal, we'd compare other options.")
NEWER_STATEMENT = ("We're not reviewing other suture suppliers right now, but if our contracted pricing increases by "
                   "more than 8% at our March renewal, we'd compare other options.")


def _confirm_statement(console, account_id, statement, percent, month):
    from datetime import datetime, timezone
    from baec_app.domain.enums import ArticulationOrigin, BaecCriterion, ElicitationMode, ThresholdComparator
    from baec_console.engine1_adapter import ConditionForm
    interaction_id = console.local.record_interaction(
        account_id, buyer_role="Materials Manager", occurred_at=datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc),
        buyer_statement=statement, seller_notes="")
    threshold = f"by more than {percent}%"
    timing = f"at our {month} renewal"
    form = ConditionForm(
        criteria={
            BaecCriterion.PRESENT_NON_EVALUATION.value: {
                "finding": "MET", "excerpt": "We're not reviewing other suture suppliers right now", "rationale": ""},
            BaecCriterion.PROSPECTIVE_CONDITION.value: {
                "finding": "MET", "excerpt": f"if our contracted pricing increases {threshold} {timing}",
                "rationale": ""},
            BaecCriterion.BUYER_ARTICULATION.value: {"finding": "MET", "excerpt": statement, "rationale": ""},
            BaecCriterion.EVALUATION_LINKAGE.value: {
                "finding": "MET", "excerpt": "we'd compare other options", "rationale": ""},
        },
        origin=ArticulationOrigin.BUYER_GENERATED.value, elicitation=ElicitationMode.SPONTANEOUS.value,
        threshold_text=threshold, comparator=ThresholdComparator.GREATER_THAN.value, threshold_value=str(percent),
        threshold_unit="%", timing_text=timing)
    draft_id = console.local.capture_condition(account_id, interaction_id, form)
    fp = console.local.preview_condition(draft_id)["fingerprint"]
    return console.local.confirm_condition(draft_id, displayed_fingerprint=fp)


def _start_monitoring(console, account_id, baec_id):
    from baec_console.engine2_adapter import SourceForm
    from .conftest import REQUIRED
    console.local.start_monitoring(
        account_id, baec_id, target_entities=("Apex Surgical Supply (synthetic)",), required=REQUIRED,
        sources=(SourceForm("Supplier renewal notice", "Apex Surgical Supply (synthetic)", "Forwarded by the buyer",
                            "Email", "Notice text and dates"),))


def _prepare_and_accept(console, result, review_id):
    request_id = console.local.request_preparation(review_id, permitted_request(result))
    preparation_id = console.local.generate_preparation(request_id)
    for artifact_id in ("WHY-NOW", "EMAIL", "CALL", "QUESTIONS"):
        console.local.review_artifact(preparation_id, artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                      attestations=ATTEST)
    return preparation_id


def harborview_with_two_conditions(console):
    """Older >10% / January condition fully prepared; newer >8% / March condition confirmed after it."""
    from baec_app.domain.enums import BaecClassification
    from .conftest import new_account
    account_id = new_account(console, "Harborview Surgical Center (synthetic)")
    older = _confirm_statement(console, account_id, OLDER_STATEMENT, 10, "January")
    _start_monitoring(console, account_id, older)
    old_result = observe(console, account_id, older, new_price="44800",
                         content="Apex Surgical Supply renewal notice: contracted suture pricing increases by 12%.")
    old_review = review(console, old_result.candidate_id).review.review_id
    old_preparation = _prepare_and_accept(console, old_result, old_review)
    assert console.local.account_view(account_id).stage == "PREPARED"
    newer = _confirm_statement(console, account_id, NEWER_STATEMENT, 8, "March")
    view = console.local.account_view(account_id)
    assert [b.classification for b in view.baecs] == [BaecClassification.CONFIRMED_BAEC] * 2  # no supersession
    return account_id, older, newer, old_preparation


def test_workflow_stays_bound_to_the_newer_confirmed_condition(console):
    account_id, older, newer, old_preparation = harborview_with_two_conditions(console)
    view = console.local.account_view(account_id)
    assert view.current_baec.baec_id == newer
    assert view.plan is None and view.stage == "MONITOR"  # the older condition's plan is not silently reused

    _start_monitoring(console, account_id, newer)
    view = console.local.account_view(account_id)
    assert view.plan_record.baec_id == newer
    assert str(view.plan_record.threshold.value) == "8"
    assert view.plan_record.condition_reference == NEWER_STATEMENT

    result = observe(console, account_id, newer, new_price="43600",
                     content="Apex Surgical Supply renewal notice states that contracted suture pricing will increase "
                             "by 9% effective March 1, 2027.")
    review_id = review(console, result.candidate_id).review.review_id
    view = console.local.account_view(account_id)
    (only,) = view.verified_correspondences()
    assert only["review"].review_id == review_id
    assert only["candidate"].monitoring_plan_id == view.plan.monitoring_plan_id

    assert console.local.preparation_snapshot(review_id)["buyer_statement"] == NEWER_STATEMENT
    preparation_id = _prepare_and_accept(console, result, review_id)
    why_now = _outputs(console, preparation_id)["INTERNAL_WHY_NOW_SUMMARY"]["artifact"]["rendered_text"]
    assert NEWER_STATEMENT in why_now and OLDER_STATEMENT not in why_now
    # The older condition, and its preparation, stay on record.
    assert console.local.preparation_view(old_preparation)["status"] == "Accepted"
    assert {b.baec_id for b in console.local.account_view(account_id).baecs} == {older, newer}


def test_an_older_preparation_does_not_make_a_newer_correspondence_look_prepared(console):
    account_id, _, newer, _ = harborview_with_two_conditions(console)
    _start_monitoring(console, account_id, newer)
    result = observe(console, account_id, newer, new_price="43600")
    review_id = review(console, result.candidate_id).review.review_id
    view = console.local.account_view(account_id)
    assert (view.stage, view.next_action) == ("PREPARE", "Request preparation")
    _prepare_and_accept(console, result, review_id)
    assert console.local.account_view(account_id).stage == "PREPARED"


def test_a_newer_verified_correspondence_on_the_same_plan_needs_its_own_preparation(console):
    account_id, baec_id, result, review_id = verified(console)
    _prepare_and_accept(console, result, review_id)
    assert console.local.account_view(account_id).stage == "PREPARED"
    second = observe(console, account_id, baec_id, new_price="44000")
    review(console, second.candidate_id)
    view = console.local.account_view(account_id)
    assert (view.stage, view.next_action) == ("PREPARE", "Request preparation")


# --- one instant, one calendar date ------------------------------------------------------------------------------
# Riverside acceptance run: the correspondence card said 26 Oct 2026, 20:24 (New York) while Why Now said
# 27 October 2026, because the snapshot formatted the stored UTC timestamp (00:24 UTC on 27 Oct) in UTC.


@pytest.fixture
def display_zone(monkeypatch):
    """Run in a fixed local display zone, then restore the process zone."""
    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


class NearMidnightUTCClock(StepClock):
    """Starts at 00:00 UTC on 27 Oct 2026: still the evening of 26 Oct in New York for the whole test."""

    def __init__(self) -> None:
        self.at = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)


def _why_now_near_midnight(tmp_path, zone_name, display_zone):
    display_zone(zone_name)
    console = make_console(tmp_path / zone_name.replace("/", "-"), NearMidnightUTCClock())
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    recorded = review(console, result.candidate_id).review
    request_id = console.local.request_preparation(recorded.review_id, permitted_request(result))
    preparation_id = console.local.generate_preparation(request_id)
    return console, recorded, _outputs(console, preparation_id)


def test_riverside_review_time_shows_one_date_on_the_card_and_in_why_now(display_zone):
    display_zone("America/New_York")
    reviewed_at = datetime(2026, 10, 27, 0, 24, tzinfo=timezone.utc)  # as stored
    assert when(reviewed_at) == "26 Oct 2026, 20:24"  # the correspondence card
    assert engine3_adapter._day(reviewed_at) == "26 October 2026"  # the date Why Now is written with
    assert reviewed_at.isoformat() == "2026-10-27T00:24:00+00:00"  # display never changes the stored value


def test_near_midnight_review_has_the_same_date_on_the_card_and_in_why_now(tmp_path, display_zone):
    console, recorded, outputs = _why_now_near_midnight(tmp_path, "America/New_York", display_zone)
    assert recorded.reviewed_at.astimezone(timezone.utc).date().day == 27  # the UTC date differs...
    assert when(recorded.reviewed_at).startswith("26 Oct 2026, 20:")  # ...from the date the card shows
    why_now = outputs["INTERNAL_WHY_NOW_SUMMARY"]["artifact"]["rendered_text"]
    assert "human-verified correspondence on 26 October 2026" in why_now
    assert "27 October 2026" not in why_now
    # Every other surface built from the same snapshot agrees: preparation metadata and the timeline.
    snap = console.local.preparation_snapshot(recorded.review_id)
    assert snap["reviewed_on"] == "26 October 2026"
    assert snap["review_identity"].endswith(recorded.reviewed_at.isoformat())  # identity keeps the stored instant
    account_id = snap["account_id"]
    timeline = console.local.timeline(account_id)
    text = " ".join(f"{item.title} {item.detail}" for item in timeline)
    assert "Preparation requested from the correspondence verified 26 October 2026" in text
    assert "27 October 2026" not in text
    recorded_row = next(i for i in timeline if i.title == "Correspondence review recorded")
    assert when(recorded_row.at) == when(recorded.reviewed_at)  # the timeline row shows the card's date and time
    for output in outputs.values():
        assert "27 October 2026" not in output["artifact"]["rendered_text"]


def test_display_zone_changes_only_the_review_date_in_preparation(tmp_path, display_zone):
    _, _, in_utc = _why_now_near_midnight(tmp_path, "UTC", display_zone)
    _, _, in_new_york = _why_now_near_midnight(tmp_path, "America/New_York", display_zone)
    assert in_utc.keys() == in_new_york.keys() == {"INTERNAL_WHY_NOW_SUMMARY", "FOLLOW_UP_EMAIL", "CALL_OPENING",
                                                   "DISCOVERY_QUESTION_SET"}
    for kind in in_utc:
        utc_text = in_utc[kind]["artifact"]["rendered_text"]
        new_york_text = in_new_york[kind]["artifact"]["rendered_text"]
        assert utc_text.replace("27 October 2026", "26 October 2026") == new_york_text
        assert in_utc[kind]["report"] == in_new_york[kind]["report"]  # Engine 3 decides exactly the same
        assert in_new_york[kind]["report"]["allowed"]
    why_now = in_new_york["INTERNAL_WHY_NOW_SUMMARY"]["artifact"]["rendered_text"]
    assert "Renewal notice: the annual fee changes from USD 40,000 to USD 43600." in why_now  # evidence wording
