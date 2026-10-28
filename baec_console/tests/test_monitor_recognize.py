"""MONITOR and RECOGNIZE: Engine 2 plans, observations, and the human correspondence review."""

from __future__ import annotations

from decimal import Decimal

import pytest

from baec_app.engine2.domain import CorrespondenceOutcome, DimensionFinding, SufficiencyFinding
from baec_app.engine2.review import StaleReviewContextError
from baec_console.console_service import ConsoleError
from baec_console.engine2_adapter import MonitoringError

from .conftest import ACTOR, monitored, observe, review


def test_confirmed_condition_enters_monitoring_with_engine1_facts(console):
    account_id, baec_id = monitored(console)
    view = console.local.account_view(account_id)
    assert view.plan.activation_result == "ACTIVATED"
    plan = view.plan_record
    assert plan.baec_id == baec_id
    assert plan.condition_reference == view.current_baec.candidate.buyer_exact_statement
    assert plan.threshold.value == Decimal("7") and plan.threshold.unit == "%"
    assert plan.timing_context == "at the April renewal"
    assert view.next_action == "Add observations as evidence arrives"


def test_monitoring_cannot_start_twice(console):
    account_id, baec_id = monitored(console)
    with pytest.raises(ConsoleError):
        console.local.start_monitoring(account_id, baec_id, target_entities=("x",), required=frozenset(),
                                       sources=())


def test_observation_and_exact_measurement_are_recorded(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    view = console.local.account_view(account_id)
    assert [o.observation_id for o in view.observations] == [result.observation_id]
    assert view.measurements[0].output_value == Decimal("9.00")
    assert view.candidates[0]["reviews"] == []


def test_flagged_observation_waits_for_human_review(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    view = console.local.account_view(account_id)
    assert view.stage == "RECOGNIZE" and view.next_action == "Review possible correspondence"
    pending = console.review_queue()["pending"]
    assert [(r["type"], r["ref"]) for r in pending] == [("Correspondence review", result.candidate_id)]


def test_unflagged_observation_is_not_queued(console):
    account_id, baec_id = monitored(console)
    observe(console, account_id, baec_id, flag=False)
    assert console.review_queue()["pending"] == []


def test_every_required_finding_must_be_answered(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    packet = console.local.review_packet(result.candidate_id)
    with pytest.raises(MonitoringError):
        console.local.submit_correspondence_review(result.candidate_id, displayed_context_digest=packet.context_digest,
                                                   findings={}, notes={}, sufficiency="YES")


def test_verified_outcome_is_derived_by_engine2(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    recorded = review(console, result.candidate_id)
    assert recorded.outcome is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE
    assert console.local.account_view(account_id).next_action == "Request preparation"
    assert console.local.account_view(account_id).account.state is None  # correspondence is not an opportunity


def test_not_enough_evidence_is_preserved_and_not_verified(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    recorded = review(console, result.candidate_id, DimensionFinding.UNRESOLVED, SufficiencyFinding.NO)
    assert recorded.outcome is CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE
    view = console.local.account_view(account_id)
    assert view.candidates[0]["reviews"][0][1] is CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE
    assert view.next_action == "Add observations as evidence arrives"


def test_contradicted_dimension_gives_no_correspondence(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id, new_price="41000")
    packet = console.local.review_packet(result.candidate_id)
    findings = {d: "SUPPORTED" for d in packet.plan.required_dimensions}
    for d in findings:
        if d.value == "THRESHOLD_MATCH":
            findings[d] = "CONTRADICTED"
    recorded = console.local.submit_correspondence_review(
        result.candidate_id, displayed_context_digest=packet.context_digest, findings=findings, notes={},
        sufficiency="YES")
    assert recorded.outcome is CorrespondenceOutcome.NO_CORRESPONDENCE


def test_revalidation_flag_blocks_verification(console):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    recorded = review(console, result.candidate_id, triggers=("Buyer changed roles",))
    assert recorded.outcome is CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE


def test_review_against_a_stale_packet_is_refused(console):
    account_id, baec_id = monitored(console)
    first = observe(console, account_id, baec_id)
    packet = console.local.review_packet(first.candidate_id)
    observe(console, account_id, baec_id, new_price="43200")  # the ledger changes after the packet was shown
    with pytest.raises(StaleReviewContextError):
        console.local.submit_correspondence_review(
            first.candidate_id, displayed_context_digest=packet.context_digest,
            findings={d: "SUPPORTED" for d in packet.plan.required_dimensions}, notes={}, sufficiency="YES")


def test_a_measurement_value_that_is_not_a_number_is_refused_before_anything_is_written(console):
    from datetime import datetime, timezone
    from baec_console.engine2_adapter import ObservationForm
    account_id, baec_id = monitored(console)
    source_id = next(iter(console.local.account_view(account_id).sources))
    with pytest.raises(MonitoringError):
        console.local.add_observation(baec_id, ObservationForm(
            source_id=source_id, observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc), content="Notice text",
            transformation="corpus.percent_change_from_two_prices", baseline_value="forty", new_value="43600",
            value_unit="USD"))
    assert console.local.account_view(account_id).observations == []
