"""Engine 1 trigger clauses that no other test isolated (docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md §11).

Each test builds a row that violates exactly one clause of one schema trigger, with every other guard satisfied
(using persistence_builders.tamper or PRAGMA ignore_check_constraints where the only way to reach the state is a
corrupted or constraint-bypassing write), and checks that the trigger itself refuses it with its own message.
A clause-level mutant that removes the clause makes the test fail; the unmodified schema passes. They pin the
behavior a PostgreSQL port must reproduce block by block. All data is the synthetic FIXTURE set; these are
software tests of the storage layer, not evidence for the theory, and they change no rule.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from baec_app.data.database import RepositoryConflictError
from baec_app.data.proposal_bridge import ReviewDecision
from baec_app.data.repository import encode_datetime
from tests.ai_provenance_builders import db  # noqa: F401
from tests.bridge_builders import (  # noqa: F401
    ACC,
    ACTOR,
    INT_SAME_ACCOUNT,
    OTHER_ACC,
    T0,
    accepted_lineage,
    bridge,
    confirmed_baec,
    consumption_values,
    decision_record,
    grant_values,
    insert_row,
    link_values,
    proposal_record,
    revision_record,
)
from tests.persistence_builders import tamper
from tests.test_ai_provenance_schema import ARTIFACT_SQL, RESULT_SQL, _raw_run, _result
from tests.test_ai_provenance_schema import D as OUTPUT_DIGEST
from tests.test_proposal_bridge_schema import decision_row, revision_row

AUTHORIZATION_BINDING = "derived from its grant"
UNRETIRED = "unretired grant on the latest revision of an open proposal"


def refused(connection, table: str, values: dict, match: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=match):
        insert_row(connection, table, values)


def tamper_insert(connection, table: str, values: dict) -> None:
    """Write a row with every guard off: a state no guarded write can reach."""
    columns = tuple(values)
    tamper(connection, f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
           tuple(values[c] for c in columns))


def supersession(grant_id: str, revision_id: str) -> dict:
    return dict(grant_id=grant_id, reason="REVISION_SUPERSEDED", superseding_revision_id=revision_id,
                rejection_decision_id=None, actor_label=ACTOR, superseded_at=encode_datetime(T0 + timedelta(hours=2)))


@pytest.fixture
def granted(bridge):
    """An accepted revision 1 with one unretired grant. Returns (proposal, revision, decision, grant)."""
    proposal, revision, accept = accepted_lineage(bridge)
    grant = grant_values(revision, accept.decision_id)
    insert_row(bridge.connection, "human_authorization_grants", grant)
    return proposal, revision, accept, grant


def other_proposal(bridge, revisions: int) -> list:
    """A second proposal of the same account with `revisions` review revisions and no decision."""
    proposal = proposal_record(bridge.artifact_same_account, "aiprop_fixture2", ACC, INT_SAME_ACCOUNT)
    bridge.store.add_proposal(proposal)
    stored, previous = [], None
    for number in range(1, revisions + 1):
        revision = revision_record(proposal, number, previous, review_revision_id=f"aireview_other{number}")
        bridge.store.add_revision(revision)
        stored.append(revision)
        previous = revision.review_revision_id
    return stored


# --- G8: ai_proposal_confirmations_authorization_binding ----------------------------------------------------------


def test_a_link_must_name_the_baecs_own_confirmation_authorization(bridge, granted):
    """Another BAEC's authorization, even with the grant's actor and time, is not this BAEC's confirmation."""
    *_, grant = granted
    confirmed_baec(bridge, grant["baec_id"])
    other_authorization = confirmed_baec(bridge, "BAEC-fixture-9")
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, other_authorization),
            AUTHORIZATION_BINDING)


@pytest.mark.parametrize("column,value", [
    ("action", "RECORD_DORMANCY_JUDGMENT"),
    ("subject_id", "BAEC-fixture-elsewhere"),
    ("target_state", "ACTIVE_OPPORTUNITY"),
], ids=["action", "subject", "target state"])
def test_a_link_requires_a_confirm_baec_authorization_for_this_baec_with_no_target_state(bridge, granted, column,
                                                                                         value):
    *_, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    tamper(bridge.connection, f"UPDATE human_authorizations SET {column} = ? WHERE authorization_id = ?",
           (value, authorization_id))
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id),
            AUTHORIZATION_BINDING)


# --- G8: ai_proposal_confirmations_unretired ----------------------------------------------------------------------


def test_a_link_requires_the_grants_revision_to_be_the_latest(bridge, granted):
    proposal, revision, _, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    bridge.store.add_revision(revision_record(proposal, 2, revision.review_revision_id))
    tamper(bridge.connection, "DELETE FROM human_authorization_grant_supersessions")  # unretired, but not latest
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id), UNRETIRED)


def test_a_link_requires_a_proposal_that_was_never_rejected(bridge, granted):
    proposal, _, _, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    bridge.store.add_decision(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject"))
    tamper(bridge.connection, "DELETE FROM human_authorization_grant_supersessions")  # unretired, latest, rejected
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id), UNRETIRED)


def test_a_link_requires_a_grant_that_was_never_consumed(bridge, granted):
    *_, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    tamper_insert(bridge.connection, "human_authorization_grant_consumptions", consumption_values(grant))
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id), UNRETIRED)


def test_a_link_requires_a_grant_with_no_supersession_record(bridge, granted):
    _, revision, _, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    tamper_insert(bridge.connection, "human_authorization_grant_supersessions",
                  supersession(grant["grant_id"], revision.review_revision_id))
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id), UNRETIRED)


def test_a_link_cannot_use_a_grant_that_a_reissue_replaced(bridge, granted):
    """A re-issue retires its predecessor without a supersession row; only the successor clause sees it."""
    _, revision, accept, grant = granted
    reissue = grant_values(revision, accept.decision_id, n=2, sequence=2, supersedes=grant["grant_id"])
    insert_row(bridge.connection, "human_authorization_grants", reissue)
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id), UNRETIRED)


# --- G8: ai_proposal_confirmations_classification -----------------------------------------------------------------


def test_a_link_requires_the_baec_to_still_be_current(bridge, granted):
    *_, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    # staleness_status is the one unlocked baec_records column (reserved for a later phase), so no tamper is needed.
    bridge.execute("UPDATE baec_records SET staleness_status = 'STALE' WHERE baec_id = ?", (grant["baec_id"],))
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id),
            "CURRENT confirmed BAEC")


def test_a_link_requires_a_confirmed_classification(bridge, granted):
    *_, grant = granted
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    tamper(bridge.connection, "UPDATE baec_records SET classification = 'NOT_BAEC' WHERE baec_id = ?",
           (grant["baec_id"],))
    refused(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id),
            "CURRENT confirmed BAEC")


# --- G7: grant issuance and retirement ----------------------------------------------------------------------------


def test_no_grant_is_issued_after_the_proposal_was_rejected_even_for_an_accepted_latest_revision(bridge):
    proposal, revision, accept = accepted_lineage(bridge)
    bridge.store.add_decision(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject"))
    refused(bridge.connection, "human_authorization_grants", grant_values(revision, accept.decision_id),
            "accepted latest revision of an open proposal")


def test_a_reissue_must_name_a_predecessor_on_its_own_revision(bridge, granted):
    *_, grant = granted
    _, revision, accept = accepted_lineage(bridge, bridge.artifact_same_account, "aiprop_fixture2", "2")
    foreign_reissue = grant_values(revision, accept.decision_id, n=2, sequence=2, supersedes=grant["grant_id"])
    refused(bridge.connection, "human_authorization_grants", foreign_reissue, "one unretired grant")


def test_a_supersession_needs_a_later_revision_of_the_grants_own_proposal(bridge, granted):
    *_, grant = granted
    later_elsewhere = other_proposal(bridge, 2)[-1]  # revision 2, but of another proposal
    refused(bridge.connection, "human_authorization_grant_supersessions",
            supersession(grant["grant_id"], later_elsewhere.review_revision_id), "must retire an unretired grant")


def test_a_consumed_grant_cannot_be_superseded_even_by_a_later_revision(bridge, granted):
    proposal, revision, _, grant = granted
    insert_row(bridge.connection, "ai_proposal_confirmations",
               link_values(grant, confirmed_baec(bridge, grant["baec_id"])))
    insert_row(bridge.connection, "human_authorization_grant_consumptions", consumption_values(grant))
    later = revision_record(proposal, 2, revision.review_revision_id)
    tamper_insert(bridge.connection, "ai_proposal_review_revisions", revision_row(later))  # no guarded path exists
    refused(bridge.connection, "human_authorization_grant_supersessions",
            supersession(grant["grant_id"], later.review_revision_id), "must retire an unretired grant")


# --- G6: ai_proposal_review_decisions_binding and the one-rejection replace guard -------------------------------


def test_a_decision_must_carry_its_proposals_account(bridge):
    proposal, _, _ = accepted_lineage(bridge)
    wrong_account = decision_row(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject"),
                                 account_id=OTHER_ACC)
    refused(bridge.connection, "ai_proposal_review_decisions", wrong_account,
            "must name its own proposal, revision, and account")


def test_a_rejection_cannot_name_another_proposals_revision(bridge):
    proposal, _, _ = accepted_lineage(bridge)
    foreign = other_proposal(bridge, 1)[0]
    rejection = decision_row(decision_record(proposal, foreign.review_revision_id, ReviewDecision.REJECTED,
                                             "aidecision_reject"))
    refused(bridge.connection, "ai_proposal_review_decisions", rejection,
            "must name its own proposal, revision, and account")


def test_the_replace_guard_alone_keeps_a_second_rejection_from_replacing_the_first(bridge):
    proposal, _, _ = accepted_lineage(bridge)
    first = decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject1")
    bridge.store.add_decision(first)
    bridge.execute("DROP TRIGGER ai_proposal_review_decisions_terminal")  # isolate the replace guard
    second = decision_row(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject2"))
    columns = tuple(second)
    with pytest.raises(sqlite3.IntegrityError, match="ai_proposal_review_decisions rows cannot be replaced"):
        bridge.execute(f"INSERT OR REPLACE INTO ai_proposal_review_decisions ({', '.join(columns)}) "
                       f"VALUES ({', '.join('?' for _ in columns)})", tuple(second[c] for c in columns))
    assert bridge.execute("SELECT decision_id FROM ai_proposal_review_decisions WHERE decision = 'REJECTED'"
                          ).fetchall() == [(first.decision_id,)]


# --- G6: ai_proposals_artifact_binding ----------------------------------------------------------------------------


def test_a_proposal_cannot_snapshot_an_artifact_whose_run_result_is_not_success(bridge):
    tamper(bridge.connection, "UPDATE ai_run_results SET status = 'max_tokens' WHERE ai_run_id = 'FIXTURE-RUN-1'")
    with pytest.raises(RepositoryConflictError, match="must snapshot a successful, eligible artifact"):
        bridge.store.add_proposal(proposal_record(bridge.artifact))
    assert bridge.count("ai_proposals") == 0


# --- G5: AI run provenance ----------------------------------------------------------------------------------------


def test_failure_codes_outside_parse_and_semantic_statuses_are_refused_by_the_trigger_alone(db):
    _, connection, _ = db
    _raw_run(connection)
    row = _result("refusal", "response_received", stop="refusal", codes='["invalid_json"]')
    connection.execute("PRAGMA ignore_check_constraints = ON")  # the CHECK backstop off: the trigger alone refuses
    try:
        with pytest.raises(sqlite3.IntegrityError, match="outside the closed vocabulary"):
            connection.execute(RESULT_SQL, row)
    finally:
        connection.execute("PRAGMA ignore_check_constraints = OFF")
    assert connection.execute("SELECT COUNT(*) FROM ai_run_results").fetchone()[0] == 0


def test_an_artifact_requires_the_stored_output_to_carry_the_results_digest(db):
    _, connection, _ = db
    _raw_run(connection)
    connection.execute(RESULT_SQL, _result("success", "response_received", "msg", "m", "end_turn", out=OUTPUT_DIGEST,
                                           tokens=5))
    connection.execute("INSERT INTO ai_run_outputs VALUES ('RAW-1', 'out', ?)", (OUTPUT_DIGEST,))
    tamper(connection, "UPDATE ai_run_outputs SET output_digest = ? WHERE ai_run_id = 'RAW-1'", ("f" * 64,))
    with pytest.raises(sqlite3.IntegrityError, match="requires a successful run with its raw output"):
        connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", OUTPUT_DIGEST))
