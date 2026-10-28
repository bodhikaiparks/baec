"""Phase 7F-B: the separate "Authorize BAEC confirmation" human action on the review page (AppTest, offline).

AppTest proves human-interface behaviour, not authenticated identity: the label is self-asserted. The page never
executes a grant; confirmation execution is not reachable from it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from baec_app.application.proposal_authorization import (
    AuthorizationRefused,
    ConfirmationExecutionService,
    ExecutionRefused,
    ProposalAuthorizationService,
)
from baec_app.data.database import connect
from tests.application_builders import FixedClock
from tests.review_builders import Review
from tests.test_review_page import Page, snapshot


def count(path, table):
    connection = connect(path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


@pytest.fixture
def page(tmp_path):
    review = Review(str(tmp_path / "authorize.sqlite3"))
    review.connection.close()
    return Page(review.path, review.pid)


def accepted(page, **fill):
    page.fill(condition="Pricing rising by more than 10% at renewal.", **fill).accept()
    assert page.errors() == []
    return page.run()


def authorize_button(page):
    return [b for b in page.at.button if b.key == f"authorize_{page.pid}"]


def authorize(page):
    authorize_button(page)[0].click()
    return page.run()


def test_render_and_accept_issue_no_grant(page):
    assert authorize_button(page) == [] and count(page.path, "human_authorization_grants") == 0
    accepted(page)
    page.run()
    assert count(page.path, "human_authorization_grants") == 0  # Review Accepted is not an authorization
    assert len(authorize_button(page)) == 1
    captions = " ".join(c.value for c in page.at.caption)
    assert "self-asserted" in captions and "15 minutes after issuance" in captions
    assert "It does not confirm the BAEC, and no confirmation is executed from this page." in captions
    assert any(t == "Classifier result, recomputed from the accepted revision: CONFIRMED_BAEC" for t in page.texts())


def test_an_explicit_authorize_click_issues_exactly_one_grant_and_confirms_nothing(page):
    accepted(page)
    before = snapshot(page.path)
    authorize(page)
    after = snapshot(page.path)
    assert {t for t in after if after[t] != before[t]} == {"human_authorization_grants"}
    assert page.at.success[0].value.startswith("Authorization Granted — grant_")
    assert "Not executed: no BAEC has been confirmed." in page.at.success[0].value
    assert after["baec_records"] == [] and after["human_authorizations"] == [] and after["account_state_transitions"] == []
    page.run()
    assert any(t.startswith("Authorization Granted (active) · grant_") and "(self-asserted)" in t for t in page.texts())


def test_a_non_confirmable_accepted_review_receives_no_grant(page):
    page.fill(condition="Pricing rising by more than 10% at renewal.", skip=("PRESENT_NON_EVALUATION",))
    page.at.radio(key=f"finding_{page.pid}_PRESENT_NON_EVALUATION").set_value("UNKNOWN")
    page.run().accept()
    page.run()
    assert "Not eligible for authorization: not_confirmable" in page.texts()
    authorize(page)
    assert page.errors() == ["Not authorized: not_confirmable"]
    assert count(page.path, "human_authorization_grants") == 0


def test_a_second_click_while_a_grant_is_active_creates_no_duplicate(page):
    accepted(page)
    authorize(page)
    authorize(page)
    assert page.errors() == ["Not authorized: active_grant_exists"]
    assert count(page.path, "human_authorization_grants") == 1


def test_an_expired_authorization_needs_a_new_explicit_click(page):
    accepted(page)
    connection = connect(page.path)
    try:
        hour_ago = FixedClock(datetime.now(timezone.utc) - timedelta(hours=1))
        ProposalAuthorizationService(connection, clock=hour_ago).authorize_confirmation(page.pid, actor_label="FIXTURE-reviewer")
    finally:
        connection.close()
    page.run()
    page.run()
    assert any(t.startswith("Authorization expired · grant_") for t in page.texts())
    assert count(page.path, "human_authorization_grants") == 1  # rendering never refreshes an authorization
    authorize(page)
    assert page.errors() == [] and count(page.path, "human_authorization_grants") == 2


def test_a_revision_edit_supersedes_the_earlier_grant_and_it_cannot_execute_changed_content(page):
    accepted(page)
    authorize(page)
    old = snapshot(page.path)["human_authorization_grants"][0][0]
    page.at.text_area(key=f"norm_text_{page.pid}_condition").input("More than 10% at renewal.")
    page.run().accept()
    page.run()
    assert any(t.startswith("Authorization superseded · " + old) for t in page.texts())
    connection = connect(page.path)
    try:
        with pytest.raises(ExecutionRefused) as raised:
            ConfirmationExecutionService(connection, clock=FixedClock(datetime.now(timezone.utc))).execute(old)
        assert raised.value.code == "grant_superseded"
    finally:
        connection.close()
    assert count(page.path, "baec_records") == 0


def test_a_rejected_proposal_cannot_receive_a_grant(page):
    accepted(page)
    page.reject()
    page.run()
    assert authorize_button(page) == []
    connection = connect(page.path)
    try:
        with pytest.raises(AuthorizationRefused) as raised:
            ProposalAuthorizationService(connection, clock=FixedClock(datetime.now(timezone.utc))).authorize_confirmation(
                page.pid, actor_label="FIXTURE-reviewer")
        assert raised.value.code == "proposal_rejected"
    finally:
        connection.close()
    assert count(page.path, "human_authorization_grants") == 0


def test_the_page_offers_no_confirmation_execution(page):
    accepted(page)
    authorize(page)
    labels = [b.label.lower() for b in page.at.button]
    assert not [label for label in labels if "execute" in label or label.startswith("confirm")]
    assert count(page.path, "baec_records") == 0 and count(page.path, "human_authorization_grant_consumptions") == 0
