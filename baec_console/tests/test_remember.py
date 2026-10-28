"""REMEMBER: accounts, interactions, and human confirmation through the Engine 1 gate."""

from __future__ import annotations

import dataclasses

import pytest

from baec_app.application import NotConfirmable
from baec_app.domain.enums import AccountState, ArticulationOrigin, BaecClassification, ReviewAnswer
from baec_app.engine2.integration.engine1_handoff import HandoffRefused
from baec_console import demo_seed
from baec_console.console_service import AccessDenied, ConsoleError
from baec_console.engine1_adapter import CaptureError, preview_blockers

from .conftest import ACTOR, REQUIRED, add_interaction, confirmed, new_account


def test_create_account_is_unclassified_and_infers_nothing(console):
    account_id = new_account(console)
    view = console.local.account_view(account_id)
    assert view.account.state is None
    assert view.baecs == [] and view.interactions == [] and view.drafts == []
    assert view.stage == "REMEMBER" and view.next_action == "Record a buyer interaction"


def test_account_name_is_required(console):
    with pytest.raises(ConsoleError):
        console.local.create_account("  ")


def test_interaction_preserves_the_exact_buyer_statement(console):
    account_id = new_account(console)
    add_interaction(console, account_id)
    interaction, meta = console.local.account_view(account_id).interactions[0]
    assert demo_seed.BUYER_STATEMENT in interaction.text
    assert meta["buyer_statement"] == demo_seed.BUYER_STATEMENT
    assert meta["buyer_role"] == demo_seed.BUYER_ROLE


def test_capture_builds_a_real_candidate_and_is_not_a_confirmation(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    draft_id = console.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())
    preview = console.local.preview_condition(draft_id)
    candidate = preview["candidate"]
    assert candidate.buyer_exact_statement == demo_seed.BUYER_STATEMENT
    assert candidate.stringency.verbatim_text == "by at least 7%"
    assert preview["preview"].confirmable is True
    view = console.local.account_view(account_id)
    assert view.baecs == [] and view.current_baec is None  # captured is not confirmed
    assert view.next_action == "Confirm the captured condition"


def test_evidence_must_be_copied_exactly_from_the_interaction(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    form = demo_seed.condition_form()
    criteria = dict(form.criteria)
    criteria["EVALUATION_LINKAGE"] = {**criteria["EVALUATION_LINKAGE"], "excerpt": "they would switch suppliers"}
    with pytest.raises(CaptureError):
        console.local.capture_condition(account_id, interaction_id, dataclasses.replace(form, criteria=criteria))


def test_threshold_wording_must_be_verbatim(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    form = dataclasses.replace(demo_seed.condition_form(), threshold_text="7% or more")
    with pytest.raises(CaptureError):
        console.local.capture_condition(account_id, interaction_id, form)


def test_confirmation_is_bound_to_the_previewed_content(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    draft_id = console.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())
    with pytest.raises(ConsoleError):
        console.local.confirm_condition(draft_id, displayed_fingerprint="0" * 64)
    assert console.local.account_view(account_id).baecs == []


def test_human_confirmation_creates_a_confirmed_engine1_record(console):
    account_id, baec_id = confirmed(console)
    view = console.local.account_view(account_id)
    record = view.current_baec
    assert record.baec_id == baec_id and record.classification is BaecClassification.CONFIRMED_BAEC
    assert record.confirmation.authorized_by == ACTOR
    assert view.account.state is None  # a BAEC is not an account state
    assert view.next_action == "Start monitoring"


def test_a_draft_cannot_be_confirmed_twice(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    draft_id = console.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())
    fp = console.local.preview_condition(draft_id)["fingerprint"]
    console.local.confirm_condition(draft_id, displayed_fingerprint=fp)
    with pytest.raises(ConsoleError):
        console.local.confirm_condition(draft_id, displayed_fingerprint=fp)
    assert len(console.local.account_view(account_id).baecs) == 1


def test_seller_seeded_condition_cannot_be_confirmed_but_is_preserved(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    form = dataclasses.replace(demo_seed.condition_form(), origin=ArticulationOrigin.SELLER_SEEDED.value)
    draft_id = console.local.capture_condition(account_id, interaction_id, form)
    preview = console.local.preview_condition(draft_id)
    assert preview["preview"].confirmable is False
    with pytest.raises(NotConfirmable):
        console.local.confirm_condition(draft_id, displayed_fingerprint=preview["fingerprint"])
    baec_id = console.local.record_not_confirmable(draft_id, displayed_fingerprint=preview["fingerprint"])
    record = console.local.account_view(account_id).baecs[0]
    assert record.baec_id == baec_id and record.classification is BaecClassification.NOT_BAEC
    assert record.classification_reason


def test_cannot_monitor_an_unconfirmed_condition(console):
    account_id = new_account(console)
    interaction_id = add_interaction(console, account_id)
    form = dataclasses.replace(demo_seed.condition_form(), origin=ArticulationOrigin.UNCERTAIN.value)
    draft_id = console.local.capture_condition(account_id, interaction_id, form)
    fp = console.local.preview_condition(draft_id)["fingerprint"]
    baec_id = console.local.record_not_confirmable(draft_id, displayed_fingerprint=fp)
    with pytest.raises(HandoffRefused):
        console.local.start_monitoring(account_id, baec_id, target_entities=("Cobalt",), required=REQUIRED,
                                       sources=())
    with pytest.raises(AccessDenied):  # an unknown id is refused exactly like another organization's id
        console.local.start_monitoring(account_id, "BAEC-DOES-NOT-EXIST", target_entities=("Cobalt",),
                                       required=REQUIRED, sources=())


def test_dormancy_requires_the_gate_and_the_state_machine(console):
    account_id, baec_id = confirmed(console)
    judgment_id = console.local.record_dormancy_judgment(baec_id, plausibility=ReviewAnswer.YES,
                                                         addressability=ReviewAnswer.UNKNOWN, notes="")
    inputs = {"baec_id": baec_id, "judgment_id": judgment_id}
    # A preview never carries authorization; "would be allowed" means nothing else blocks it.
    assert preview_blockers(console.local.preview_state_change(account_id, AccountState.CONDITIONALLY_DORMANT, inputs)) == []
    result = console.local.change_account_state(account_id, AccountState.CONDITIONALLY_DORMANT, inputs)
    assert result.allowed
    view = console.local.account_view(account_id)
    assert view.account.state is AccountState.CONDITIONALLY_DORMANT
    assert view.transitions[0].authorization.authorized_by == ACTOR


def test_dormancy_refused_without_plausibility(console):
    account_id, baec_id = confirmed(console)
    judgment_id = console.local.record_dormancy_judgment(baec_id, plausibility=ReviewAnswer.NOT_YET,
                                                         addressability=ReviewAnswer.YES, notes="")
    preview = console.local.preview_state_change(account_id, AccountState.CONDITIONALLY_DORMANT,
                                                 {"baec_id": baec_id, "judgment_id": judgment_id})
    assert preview_blockers(preview) == ["Plausibility must be judged Yes."]


def test_active_opportunity_needs_evaluation_evidence_not_a_signal(console):
    account_id, _ = confirmed(console)
    preview = console.local.preview_state_change(account_id, AccountState.ACTIVE_OPPORTUNITY, {})
    assert preview_blockers(preview) == [
        "Active Opportunity needs evidence from an interaction that the buyer is evaluating."]
    assert console.local.account_view(account_id).account.state is None
