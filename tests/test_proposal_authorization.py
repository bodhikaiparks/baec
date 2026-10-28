"""Phase 7F-B: persisted human authorization grants and atomic BAEC confirmation, tested without Streamlit.

AI may propose. Humans authorize. Domain rules decide. Execution records. All data is test-only fixture data
(tests/review_builders.py, design D15). The actor label is self-asserted; nothing here authenticates anyone.
"""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import replace
from datetime import timedelta

import pytest

from baec_app.application import proposal_authorization as module
from baec_app.application.proposal_authorization import (
    AUTHORIZATION_FAILURE_CODES,
    EXECUTION_FAILURE_CODES,
    AuthorizationRefused,
    ConfirmationExecutionService,
    ExecutionRefused,
    ProposalAuthorizationService,
    grant_status,
)
from baec_app.data.authorization_grants import GRANT_DIGEST_FIELDS, AuthorizationGrantStore, GrantRecord
from baec_app.data.database import DATA_TABLES, connect
from baec_app.data.proposal_bridge import ReviewDecision
from baec_app.domain.enums import BaecClassification, CriterionFinding
from tests.application_builders import FixedClock
from tests.persistence_builders import dump, tamper
from tests.review_builders import REVIEWED_AT, REVIEWER, SUGGESTED, Review, decisions, findings

ISSUED = REVIEWED_AT + timedelta(minutes=5)
EFFECTS = ("baec_records", "human_authorizations", "ai_proposal_confirmations", "human_authorization_grant_consumptions")


class Authorized(Review):
    """A Review database with revision 1 ACCEPTED, and authorization and execution services on injected clocks."""

    def __init__(self, path: str, **decision_changes) -> None:
        super().__init__(path)
        self.accepted = self.service.accept_review(self.pid, decisions(**decision_changes), actor_label=REVIEWER)
        self.clock = FixedClock(ISSUED)
        counter = iter(range(1, 10_000))
        self.auth = ProposalAuthorizationService(self.connection, clock=self.clock,
                                                 new_grant_id=lambda: "grant_" + f"{next(counter):064x}")
        self.execution = ConfirmationExecutionService(self.connection, clock=self.clock)
        self.grants = AuthorizationGrantStore(self.connection)

    def grant(self, actor=REVIEWER):
        return self.auth.authorize_confirmation(self.pid, actor_label=actor)

    def effects(self):
        return {t: self.count(t) for t in EFFECTS}


@pytest.fixture
def world(tmp_path):
    w = Authorized(str(tmp_path / "authorized.sqlite3"))
    yield w
    w.connection.close()


def refuse_execution(world, grant_id, code):
    before = dump(world.connection)
    with pytest.raises(ExecutionRefused) as raised:
        world.execution.execute(grant_id)
    assert raised.value.code == code
    assert dump(world.connection) == before  # nothing written, and the grant is not used
    return raised.value


def refuse_authorization(world, code, actor=REVIEWER):
    before = dump(world.connection)
    with pytest.raises(AuthorizationRefused) as raised:
        world.auth.authorize_confirmation(world.pid, actor_label=actor)
    assert raised.value.code == code and dump(world.connection) == before


# --- issuance: a separate explicit human action, from an ACCEPTED review only -------------------------------


def test_an_accepted_review_alone_is_not_an_authorization(world):
    assert world.count("human_authorization_grants") == 0
    status = world.auth.authorization_status(world.pid)
    assert (status.review_state, status.classification, status.eligibility_failure, status.grants) == (
        "REVIEW_ACCEPTED", BaecClassification.CONFIRMED_BAEC, None, ())
    assert world.count("human_authorization_grants") == 0  # reading status issues nothing


def test_authorizing_persists_exactly_one_grant_bound_to_the_accepted_revision(world):
    before = dump(world.connection)
    view = world.grant()
    after = dump(world.connection)
    assert {t for t in DATA_TABLES if after[t] != before[t]} == {"human_authorization_grants"}
    grant = world.grants.get_grant(view.grant_id)
    revision = world.accepted.revision
    assert (grant.action, grant.proposal_id, grant.review_revision_id, grant.account_id, grant.review_content_digest,
            grant.decision_id, grant.actor_label, grant.issue_sequence) == (
        "CONFIRM_BAEC", world.pid, revision.review_revision_id, world.proposal.account_id,
        revision.review_content_digest, world.accepted.decision.decision_id, REVIEWER, 1)
    assert (grant.issued_at, grant.expires_at - grant.issued_at) == (ISSUED, timedelta(minutes=15))
    assert grant.binding_is_intact() and view.status == "ACTIVE"
    for table in EFFECTS:
        assert after[table] == before[table], table  # authorizing confirms nothing


def test_the_grant_digest_covers_every_binding_field(world):
    grant = world.grants.get_grant(world.grant().grant_id)
    assert set(grant.binding()) == set(GRANT_DIGEST_FIELDS) and "grant_digest" not in grant.binding()
    assert len(GRANT_DIGEST_FIELDS) == 19 and grant.binding()["action"] == "CONFIRM_BAEC"
    for field in GRANT_DIGEST_FIELDS:  # substituting any bound value (revision, account, content, action...) moves it
        value = getattr(grant, field)
        if field in ("issued_at", "expires_at"):
            changed = value + timedelta(microseconds=1)
        elif type(value) is int:
            changed = value + 1
        elif value is None:
            changed = "grant_" + "f" * 64
        else:
            changed = value[:-1] + ("0" if value[-1] != "0" else "1")
        altered = object.__new__(GrantRecord)
        altered.__dict__.update(grant.__dict__)
        object.__setattr__(altered, field, changed)
        assert altered.computed_digest() != grant.grant_digest, field


@pytest.mark.parametrize("actor", ["", "  ", None])
def test_authorization_requires_a_self_asserted_actor_label(world, actor):
    refuse_authorization(world, "actor_blank", actor)


def test_an_open_or_rejected_proposal_cannot_be_authorized(tmp_path):
    open_world = Review(str(tmp_path / "open.sqlite3"))
    try:
        auth = ProposalAuthorizationService(open_world.connection, clock=FixedClock(ISSUED))
        with pytest.raises(AuthorizationRefused) as raised:
            auth.authorize_confirmation(open_world.pid, actor_label=REVIEWER)
        assert raised.value.code == "review_not_accepted"
        open_world.service.reject_proposal(open_world.pid, actor_label=REVIEWER)
        with pytest.raises(AuthorizationRefused) as raised:
            auth.authorize_confirmation(open_world.pid, actor_label=REVIEWER)
        assert raised.value.code == "proposal_rejected"
        assert open_world.count("human_authorization_grants") == 0
    finally:
        open_world.connection.close()


def test_a_non_confirmable_accepted_review_never_receives_a_grant(tmp_path):
    world = Authorized(str(tmp_path / "unknown.sqlite3"),
                       findings=findings(PRESENT_NON_EVALUATION=CriterionFinding.UNKNOWN))
    try:
        assert world.accepted.classification is BaecClassification.INSUFFICIENT_EVIDENCE  # 7E allowed the accept
        status = world.auth.authorization_status(world.pid)
        assert (status.classification, status.eligibility_failure) == (
            BaecClassification.INSUFFICIENT_EVIDENCE, "not_confirmable")
        refuse_authorization(world, "not_confirmable")
    finally:
        world.connection.close()


def test_issuance_recomputes_the_classification_and_never_trusts_the_preview(world, monkeypatch):
    monkeypatch.setattr(module, "classify_candidate",
                        lambda candidate: type("R", (), {"classification": BaecClassification.NOT_BAEC})())
    refuse_authorization(world, "not_confirmable")


def test_issuance_reruns_the_normalization_contract(world, monkeypatch):
    from baec_app.application import proposal_review
    monkeypatch.setattr(proposal_review, "validate_final_normalization",
                        lambda **_: ("human_normalization_condition_number_unsupported",))
    refuse_authorization(world, "normalization_invalid")


# --- one active grant; expiry; re-issue ---------------------------------------------------------------------------


def test_a_second_authorization_while_a_grant_is_active_is_refused_and_nothing_is_extended(world):
    first = world.grant()
    world.clock.advance(minutes=14, seconds=59)
    refuse_authorization(world, "active_grant_exists")
    assert world.grants.get_grant(first.grant_id).expires_at == ISSUED + timedelta(minutes=15)
    assert world.count("human_authorization_grants") == 1


def test_after_expiry_only_a_new_explicit_authorization_issues_a_replacement(world):
    first = world.grant()
    world.clock.advance(minutes=15)
    assert world.auth.authorization_status(world.pid).grants[0].status == "EXPIRED"
    assert world.count("human_authorization_grants") == 1  # nothing refreshes on its own
    second = world.grant()
    replacement = world.grants.get_grant(second.grant_id)
    assert (replacement.issue_sequence, replacement.supersedes_grant_id) == (2, first.grant_id)
    assert world.grants.get_grant(first.grant_id) == world.grants.get_grant(first.grant_id)  # unchanged history
    statuses = {g.grant_id: g.status for g in world.auth.authorization_status(world.pid).grants}
    assert statuses == {first.grant_id: "SUPERSEDED", second.grant_id: "ACTIVE"}
    refuse_execution(world, first.grant_id, "grant_superseded")


@pytest.mark.parametrize("offset,expected", [(timedelta(minutes=15) - timedelta(microseconds=1), "ACTIVE"),
                                             (timedelta(minutes=15), "EXPIRED"),
                                             (timedelta(minutes=15, microseconds=1), "EXPIRED")],
                         ids=["just before", "exactly at", "after"])
def test_expiry_is_now_at_or_after_expires_at(world, offset, expected):
    grant = world.grants.get_grant(world.grant().grant_id)
    assert grant_status(world.grants.lifecycle(grant.grant_id), grant, ISSUED + offset) == expected


@pytest.mark.parametrize("offset", [timedelta(minutes=15), timedelta(minutes=16)], ids=["exactly at", "after"])
def test_an_expired_grant_writes_nothing_and_stays_unused(world, offset):
    grant_id = world.grant().grant_id
    world.clock.advance(seconds=offset.total_seconds())
    refuse_execution(world, grant_id, "grant_expired")
    assert world.grants.lifecycle(grant_id).consumed_baec_id is None


def test_a_grant_executes_one_microsecond_before_expiry(world):
    grant_id = world.grant().grant_id
    world.clock.advance(minutes=15, microseconds=-1)
    assert world.execution.execute(grant_id).grant_id == grant_id


def test_a_clock_before_issuance_fails_closed(world):
    grant_id = world.grant().grant_id
    world.clock.advance(seconds=-1)
    refuse_execution(world, grant_id, "grant_clock_invalid")


# --- supersession -----------------------------------------------------------------------------------------------


def test_a_new_revision_supersedes_an_unexpired_grant_at_once(world):
    old = world.grant().grant_id
    world.service.accept_review(world.pid, decisions(final_normalized_condition="More than 10% at renewal."),
                                actor_label=REVIEWER)
    world.clock.advance(minutes=1)  # still well inside the old grant's 15 minutes
    assert world.grants.lifecycle(old).supersession_reason == "REVISION_SUPERSEDED"
    refuse_execution(world, old, "grant_superseded")
    fresh = world.grant()
    assert world.grants.get_grant(fresh.grant_id).review_revision_id != world.grants.get_grant(old).review_revision_id


def test_a_rejection_supersedes_the_grant_and_blocks_reauthorization(world):
    grant_id = world.grant().grant_id
    world.service.reject_proposal(world.pid, actor_label=REVIEWER)
    assert world.grants.lifecycle(grant_id).supersession_reason == "PROPOSAL_REJECTED"
    refuse_execution(world, grant_id, "grant_superseded")
    refuse_authorization(world, "proposal_rejected")


def test_superseded_is_reported_before_expired(world):
    grant = world.grants.get_grant(world.grant().grant_id)
    world.service.reject_proposal(world.pid, actor_label=REVIEWER)
    world.clock.advance(hours=1)
    assert grant_status(world.grants.lifecycle(grant.grant_id), grant, world.clock.now()) == "SUPERSEDED"
    refuse_execution(world, grant.grant_id, "grant_superseded")


# --- execution ------------------------------------------------------------------------------------------------------


def test_execution_takes_a_grant_id_and_nothing_else():
    assert list(inspect.signature(ConfirmationExecutionService.execute).parameters) == ["self", "grant_id"]
    assert list(inspect.signature(ConfirmationExecutionService.__init__).parameters) == ["self", "connection", "clock"]


def test_a_valid_grant_confirms_exactly_one_baec_atomically_with_its_audit_link_and_consumption(world):
    grant = world.grants.get_grant(world.grant().grant_id)
    world.clock.advance(minutes=2)
    accounts_before = dump(world.connection)["accounts"]
    result = world.execution.execute(grant.grant_id)
    assert (result.baec_id, result.proposal_id, result.review_revision_id, result.grant_id) == (
        grant.baec_id, world.pid, grant.review_revision_id, grant.grant_id)
    assert world.effects() == {t: 1 for t in EFFECTS}
    connection = world.connection
    record = connection.execute("SELECT classification, staleness_status, buyer_exact_statement, normalized_text, "
                                "normalized_generated_at, normalized_model, confirmation_authorization_id, captured_at "
                                "FROM baec_records").fetchone()
    assert record[:6] == ("CONFIRMED_BAEC", "CURRENT", SUGGESTED, None, None, None)  # no normalization-column misuse
    assert record[7] == "2026-04-30T09:00:00.000000+00:00"
    authorization = connection.execute("SELECT authorization_id, authorized_by, authorized_at, action, subject_id, "
                                       "target_state FROM human_authorizations").fetchone()
    assert authorization == (record[6], REVIEWER, "2026-05-01T12:05:00.000000+00:00", "CONFIRM_BAEC", grant.baec_id, None)
    link = connection.execute("SELECT baec_id, artifact_id, proposal_id, review_revision_id, grant_id, authorization_id "
                              "FROM ai_proposal_confirmations").fetchone()
    assert link == (grant.baec_id, world.proposal.artifact_id, world.pid, grant.review_revision_id, grant.grant_id,
                    record[6])
    assert world.grants.lifecycle(grant.grant_id).consumed_baec_id == grant.baec_id
    assert dump(connection)["accounts"] == accounts_before  # no account-state change
    for table in ("account_state_transitions", "dormancy_judgments"):
        assert world.count(table) == 0, table
    # The final human normalization is recoverable through the lineage, never from baec_records.
    content = json.loads(world.bridge.get_revision(link[3]).content)
    assert content["normalization"]["condition"]["final_value"] == "Pricing rising by more than 10% at renewal."


def test_replaying_a_consumed_grant_fails_and_writes_nothing(world):
    grant_id = world.grant().grant_id
    world.execution.execute(grant_id)
    refuse_execution(world, grant_id, "grant_already_consumed")
    assert world.effects() == {t: 1 for t in EFFECTS}
    refuse_authorization(world, "lineage_already_confirmed")


def test_two_concurrent_executions_of_one_grant_confirm_exactly_once(world):
    grant_id = world.grant().grant_id
    barrier, outcomes = threading.Barrier(2), []

    def attempt():
        connection = connect(world.path)
        try:
            service = ConfirmationExecutionService(connection, clock=FixedClock(ISSUED + timedelta(minutes=1)))
            barrier.wait()
            try:
                outcomes.append(("ok", service.execute(grant_id).baec_id))
            except ExecutionRefused as refusal:
                outcomes.append(("refused", refusal.code))
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(kind for kind, _ in outcomes) == ["ok", "refused"]
    assert [code for kind, code in outcomes if kind == "refused"][0] in ("grant_already_consumed", "conflict")
    assert world.effects() == {t: 1 for t in EFFECTS}


def test_a_failure_inside_the_transaction_rolls_everything_back_and_keeps_the_grant(world):
    grant_id = world.grant().grant_id
    world.connection.execute("CREATE TRIGGER fail_consumption BEFORE INSERT ON human_authorization_grant_consumptions "
                             "BEGIN SELECT RAISE(ABORT, 'forced failure'); END")
    refuse_execution(world, grant_id, "conflict")
    assert world.effects() == {t: 0 for t in EFFECTS}
    world.connection.execute("DROP TRIGGER fail_consumption")
    assert world.execution.execute(grant_id).grant_id == grant_id  # the failed attempt did not use the grant


def test_execution_reruns_normalization_and_classification(world, monkeypatch):
    grant_id = world.grant().grant_id
    from baec_app.application import proposal_review
    with monkeypatch.context() as patched:
        patched.setattr(proposal_review, "validate_final_normalization",
                        lambda **_: ("human_normalization_condition_comparator_changed",))
        refuse_execution(world, grant_id, "normalization_invalid")
    with monkeypatch.context() as patched:
        patched.setattr(module, "classify_candidate",
                        lambda candidate: type("R", (), {"classification": BaecClassification.NOT_BAEC})())
        refuse_execution(world, grant_id, "not_confirmable")
    assert world.execution.execute(grant_id).grant_id == grant_id


# --- binding and tamper ------------------------------------------------------------------------------------------


def _set_grant(world, grant_id, **fields):
    """Raw corruption with every guard off; recompute the digest so only the binding check can catch it."""
    grant = world.grants.get_grant(grant_id)
    altered = object.__new__(GrantRecord)
    altered.__dict__.update(grant.__dict__)
    for field, value in fields.items():
        object.__setattr__(altered, field, value)
    digest = altered.computed_digest()
    assignments = ", ".join(f"{field} = ?" for field in fields) + ", grant_digest = ?"
    tamper(world.connection, f"UPDATE human_authorization_grants SET {assignments} WHERE grant_id = ?",
           tuple(fields.values()) + (digest, grant_id))


def test_malformed_and_unknown_grant_ids_fail_closed(world):
    for bad in ("", "grant_", "grant_" + "G" * 64, "grant_123", 7, None):
        refuse_execution(world, bad, "grant_id_invalid")
    refuse_execution(world, "grant_" + "e" * 64, "grant_not_found")


@pytest.mark.parametrize("fields,code", [
    ({"account_id": "FIXTURE-ACC-2"}, "grant_binding_mismatch"),
    ({"proposal_id": "aiprop_other"}, "lineage_integrity_failure"),
    ({"review_content_digest": "0" * 64}, "grant_binding_mismatch"),
    ({"decision_id": "aidecision_other"}, "review_not_accepted"),
], ids=["cross-account", "wrong proposal", "changed content digest", "missing accepted decision"])
def test_a_grant_rebound_to_other_authority_fails_closed(world, fields, code):
    grant_id = world.grant().grant_id
    _set_grant(world, grant_id, **fields)
    refuse_execution(world, grant_id, code)


def test_a_grant_whose_digest_no_longer_covers_its_binding_fails_closed(world):
    grant_id = world.grant().grant_id
    tamper(world.connection, "UPDATE human_authorization_grants SET actor_label = 'someone-else'")
    refuse_execution(world, grant_id, "grant_binding_mismatch")


def test_a_grant_for_another_action_cannot_exist_or_execute(world):
    grant_id = world.grant().grant_id
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        world.connection.execute("UPDATE human_authorization_grants SET action = 'CHANGE_ACCOUNT_STATE'")
    tamper(world.connection, "UPDATE human_authorization_grants SET action = 'CHANGE_ACCOUNT_STATE'")
    refuse_execution(world, grant_id, "grant_binding_mismatch")  # the digest no longer covers the binding
    tamper(world.connection, "UPDATE human_authorization_grants SET action = 'CONFIRM_BAEC'")
    _set_grant(world, grant_id, action="CHANGE_ACCOUNT_STATE")  # even with a recomputed digest
    refuse_execution(world, grant_id, "grant_binding_mismatch")
    assert world.count("baec_records") == 0


def test_a_grant_on_a_stale_revision_fails_even_without_its_supersession_row(world):
    grant_id = world.grant().grant_id
    world.service.accept_review(world.pid, decisions(final_normalized_condition="More than 10% at renewal."),
                                actor_label=REVIEWER)
    tamper(world.connection, "DELETE FROM human_authorization_grant_supersessions")
    refuse_execution(world, grant_id, "review_revision_superseded")


# --- the codes and the documented limits ---------------------------------------------------------------------


def test_the_failure_codes_are_closed():
    assert AUTHORIZATION_FAILURE_CODES == (
        "actor_blank", "proposal_not_found", "proposal_integrity_failure", "proposal_rejected", "review_not_accepted",
        "lineage_already_confirmed", "evidence_invalid", "normalization_invalid", "not_confirmable",
        "active_grant_exists", "conflict")
    assert EXECUTION_FAILURE_CODES == (
        "grant_id_invalid", "grant_not_found", "grant_already_consumed", "grant_superseded", "grant_binding_mismatch",
        "grant_clock_invalid", "grant_expired", "review_revision_superseded", "proposal_rejected",
        "review_not_accepted", "lineage_integrity_failure", "lineage_already_confirmed",
        "normalization_contract_mismatch", "evidence_invalid", "normalization_invalid", "not_confirmable", "conflict")
    for error, codes in ((AuthorizationRefused, AUTHORIZATION_FAILURE_CODES), (ExecutionRefused, EXECUTION_FAILURE_CODES)):
        with pytest.raises(ValueError):
            error("something else")
        assert all(error(code).code == code for code in codes)


def test_the_limitations_are_stated_plainly():
    doc = " ".join(module.__doc__.split())
    for statement in ("A Review Accepted is only a finalized human review. It authorizes nothing.",
                      "The actor label is self-asserted; there is no authentication.",
                      "Python code with unrestricted access to the local database or process could bypass these conventions",
                      "now >= expires_at is expired",
                      "never written to the legacy AI-derived baec_records.normalized_* columns"):
        assert statement in doc, statement
    assert ReviewDecision.ACCEPTED  # the review decision vocabulary is unchanged
