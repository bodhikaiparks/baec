"""Verified, persisted account-state transitions and append-only history (Phase 3)."""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from baec_app.data.database import (
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
)
from baec_app.data.records import RecordValidationError, TransitionHistoryEntry
from baec_app.data.repository import Repository
from baec_app.domain.enums import (
    AuthorizationAction,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    TransitionRejectionKind,
    TransitionUnresolvedKind,
)
from baec_app.domain.models import Account, DomainValidationError, EvaluationEvidence, NonEvaluationEvidence
from baec_app.domain.state_machine import (
    transition_to_active_opportunity,
    transition_to_conditionally_dormant,
    transition_to_no_plausible_path,
)
from tests.builders import (
    AO,
    CD,
    NOW,
    NP,
    authorization,
    confirmed_record,
    evaluation_evidence,
    excerpt,
    judgment,
    non_evaluation_evidence,
    state_auth,
)
from tests.persistence_builders import (  # noqa: F401
    LATER,
    connection,
    counts,
    ensure_dormancy_prerequisites,
    move,
    no_leaked_connections,
    put_in_state,
    repo,
    save_confirmed,
    save_judgment,
    tamper,
)

G = NoPlausiblePathGround
K = TransitionRejectionKind
STATES = (AO, CD, NP)
ALLOWED_MOVES = [(frm, to) for frm in (None, AO, CD, NP) for to in STATES if frm is not to]


def name(state):
    return state.value if state else "None"


def history_states(repo):
    return [(e.from_state, e.to_state) for e in repo.get_transition_history("ACC-1")]


# --- allowed transitions are persisted ----------------------------------------


@pytest.mark.parametrize("frm,to", ALLOWED_MOVES, ids=lambda s: name(s))
def test_rc20_every_allowed_move_is_persisted_with_one_history_row(repo, connection, frm, to):
    """All nine allowed moves: state updated, exactly one history row appended."""
    put_in_state(repo, frm)
    ensure_dormancy_prerequisites(repo)
    before = counts(connection)["account_state_transitions"]

    result = move(repo, to)

    assert result.allowed
    assert repo.get_account("ACC-1").state is to
    assert counts(connection)["account_state_transitions"] == before + 1
    last = repo.get_transition_history("ACC-1")[-1]
    assert (last.from_state, last.to_state) == (frm, to)
    assert last.recorded_at == LATER
    assert last.authorization == state_auth(to)


def test_persisted_result_equals_what_the_locked_state_machine_returns(repo):
    judgment_id = ensure_dormancy_prerequisites(repo)
    expected = transition_to_conditionally_dormant(
        Account("ACC-1", "Harbor Surgical Center"),
        baec_record=confirmed_record(),
        judgment=judgment(),
        authorization=state_auth(CD),
    )
    assert move(repo, CD, judgment_id=judgment_id) == expected


def test_history_is_appended_in_order_and_never_overwritten(repo):
    for state in (AO, NP, CD, AO):
        assert move(repo, state).allowed
    assert history_states(repo) == [(None, AO), (AO, NP), (NP, CD), (CD, AO)]
    ids = [e.transition_id for e in repo.get_transition_history("ACC-1")]
    assert ids == sorted(ids) and len(set(ids)) == 4
    assert repo.get_transition_history("ACC-2") == ()


# --- what each history entry records ------------------------------------------


def test_rc22_dormant_entry_references_the_baec_judgment_and_unresolved_items(repo):
    save_confirmed(repo)
    judgment_id = save_judgment(repo, addressability=ReviewAnswer.UNKNOWN)
    result = move(repo, CD, judgment_id=judgment_id)
    assert result.allowed
    entry = repo.get_transition_history("ACC-1")[0]
    assert entry.baec_id == "B-1"
    assert entry.judgment_id == judgment_id
    assert entry.unresolved == (TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,) == result.unresolved
    assert entry.evaluation_evidence is None and entry.ground is None and entry.reason is None


def test_rc27_active_entry_records_the_evaluation_evidence(repo, connection):
    supplied = evaluation_evidence()
    assert move(repo, AO, evaluation_evidence=supplied).allowed
    entry = repo.get_transition_history("ACC-1")[0]
    assert entry.evaluation_evidence == supplied
    assert entry.non_evaluation_evidence is None and entry.baec_id is None
    assert counts(connection)["evaluation_evidence"] == 1
    assert counts(connection)["non_evaluation_evidence"] == 0


def test_rc20_no_plausible_path_entry_records_ground_reason_and_basis_interaction(repo):
    assert move(
        repo, NP, ground=G.NO_PLAUSIBLE_BAEC, reason="No condition was named.", basis_interaction_id="INT-1"
    ).allowed
    entry = repo.get_transition_history("ACC-1")[0]
    assert entry.ground is G.NO_PLAUSIBLE_BAEC
    assert entry.reason == "No condition was named."
    assert entry.basis_interaction_id == "INT-1"


@pytest.mark.parametrize("ground", list(G), ids=lambda g: g.value)
def test_every_no_plausible_path_ground_round_trips(repo, ground):
    assert move(repo, NP, ground=ground).allowed
    entry = repo.get_transition_history("ACC-1")[0]
    assert entry.ground is ground and entry.basis_interaction_id is None


@pytest.mark.parametrize("to", [CD, NP], ids=name)
def test_rc34_leaving_active_stores_the_non_evaluation_evidence_separately(repo, connection, to):
    put_in_state(repo, AO)
    ensure_dormancy_prerequisites(repo)
    supplied = non_evaluation_evidence()
    assert move(repo, to, non_evaluation_evidence=supplied).allowed
    entry = repo.get_transition_history("ACC-1")[-1]
    assert entry.non_evaluation_evidence == supplied
    assert entry.evaluation_evidence is None
    assert counts(connection)["evaluation_evidence"] == 1  # from entering Active
    assert counts(connection)["non_evaluation_evidence"] == 1


def test_recorded_at_is_supplied_by_the_caller_and_stored_in_utc(repo, connection):
    local = datetime(2026, 5, 1, 9, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert move(repo, AO, recorded_at=local).allowed
    assert connection.execute("SELECT recorded_at FROM account_state_transitions").fetchone()[0] == (
        "2026-05-01T13:00:00.000000+00:00"
    )
    assert repo.get_transition_history("ACC-1")[0].recorded_at == local
    with pytest.raises(RecordValidationError):
        move(repo, NP, recorded_at=datetime(2026, 5, 1))


# --- rejected transitions write nothing ---------------------------------------

REJECTED = {
    "same state": (AO, AO, {}, (K.SAME_STATE,)),
    "authorization missing": (None, AO, dict(authorization=None), (K.AUTHORIZATION_MISSING,)),
    "authorization for another destination": (None, AO, dict(authorization=state_auth(NP)), (K.AUTHORIZATION_WRONG_TARGET,)),
    "authorization for another account": (None, AO, dict(authorization=state_auth(AO, "ACC-2")), (K.AUTHORIZATION_WRONG_SUBJECT,)),
    "authorization with another action": (
        None,
        AO,
        dict(authorization=authorization(AuthorizationAction.CONFIRM_BAEC, "ACC-1")),
        (K.AUTHORIZATION_WRONG_ACTION,),
    ),
    "signal only: no evaluation evidence": (CD, AO, dict(evaluation_evidence=None), (K.EVALUATION_EVIDENCE_MISSING,)),
    "evaluation evidence for another account": (
        CD,
        AO,
        dict(evaluation_evidence=EvaluationEvidence("ACC-2", excerpt("x", source_id="INT-9"), NOW)),
        (K.EVALUATION_EVIDENCE_WRONG_ACCOUNT,),
    ),
    "leaving Active for dormant without evidence": (AO, CD, dict(non_evaluation_evidence=None), (K.NON_EVALUATION_EVIDENCE_MISSING,)),
    "leaving Active for no plausible path without evidence": (AO, NP, dict(non_evaluation_evidence=None), (K.NON_EVALUATION_EVIDENCE_MISSING,)),
    "BAEC missing": (None, CD, dict(baec_id=None), (K.BAEC_MISSING,)),
    "judgment missing": (None, CD, dict(judgment_id=None), (K.JUDGMENT_MISSING,)),
    "ground missing": (None, NP, dict(ground=None), (K.GROUND_MISSING,)),
    "reason missing": (None, NP, dict(reason=None), (K.REASON_MISSING,)),
    "nothing supplied for dormancy": (
        None,
        CD,
        dict(baec_id=None, judgment_id=None, authorization=None),
        (K.AUTHORIZATION_MISSING, K.BAEC_MISSING, K.JUDGMENT_MISSING),
    ),
}


@pytest.mark.parametrize("case", REJECTED.values(), ids=REJECTED.keys())
def test_rc31_rejected_transition_persists_nothing(repo, connection, case):
    """A rejected request leaves no history, no authorization row, no evidence, no state change.

    None for a business prerequisite flows into the state machine and comes
    back as a normal structured rejection.
    """
    frm, to, overrides, expected = case
    put_in_state(repo, frm)
    ensure_dormancy_prerequisites(repo)
    before = counts(connection)

    result = move(repo, to, **overrides)

    assert not result.allowed
    assert result.rejections == expected
    assert counts(connection) == before
    assert repo.get_account("ACC-1").state is frm


DORMANCY_BLOCKERS = {
    "plausibility NO": (dict(plausibility=ReviewAnswer.NO), K.PLAUSIBILITY_NOT_YES),
    "plausibility UNKNOWN": (dict(plausibility=ReviewAnswer.UNKNOWN), K.PLAUSIBILITY_NOT_YES),
    "plausibility NOT_YET": (dict(plausibility=ReviewAnswer.NOT_YET), K.PLAUSIBILITY_NOT_YES),
    "addressability NO": (dict(addressability=ReviewAnswer.NO), K.ADDRESSABILITY_NO),
    "addressability NOT_YET": (dict(addressability=ReviewAnswer.NOT_YET), K.ADDRESSABILITY_NOT_YET),
}


@pytest.mark.parametrize("case", DORMANCY_BLOCKERS.values(), ids=DORMANCY_BLOCKERS.keys())
def test_rc22_stored_judgment_that_blocks_dormancy_persists_nothing(repo, connection, case):
    answers, expected = case
    save_confirmed(repo)
    judgment_id = save_judgment(repo, **answers)
    before = counts(connection)
    result = move(repo, CD, judgment_id=judgment_id)
    assert result.rejections == (expected,)
    assert counts(connection) == before
    assert repo.get_account("ACC-1").state is None


def test_rc22_judgment_for_a_different_baec_is_rejected(repo, connection):
    save_confirmed(repo, "B-1")
    save_confirmed(repo, "B-2")
    other = save_judgment(repo, "B-2")
    before = counts(connection)
    assert move(repo, CD, baec_id="B-1", judgment_id=other).rejections == (K.JUDGMENT_WRONG_BAEC,)
    assert counts(connection) == before


def test_rc22_nonconfirmed_stored_record_cannot_support_dormancy(repo, connection):
    from baec_app.domain.baec_rules import create_nonconfirmed_classification_record
    from baec_app.domain.enums import ArticulationOrigin
    from tests.builders import candidate

    repo.save_classification_record(
        create_nonconfirmed_classification_record(
            candidate(ArticulationOrigin.SELLER_SEEDED), baec_id="B-1", captured_at=NOW
        )
    )
    judgment_id = save_judgment(repo)
    before = counts(connection)
    assert move(repo, CD, judgment_id=judgment_id).rejections == (K.BAEC_NOT_CONFIRMED,)
    assert counts(connection) == before


@pytest.mark.parametrize(
    "status", [StalenessStatus.REVIEW_DUE, StalenessStatus.STALE, StalenessStatus.RETIRED], ids=lambda s: s.value
)
def test_rc29_stored_baec_that_is_not_current_cannot_support_dormancy(repo, connection, status):
    judgment_id = ensure_dormancy_prerequisites(repo)
    connection.execute("UPDATE baec_records SET staleness_status = ?", (status.value,))
    before = counts(connection)
    assert move(repo, CD, judgment_id=judgment_id).rejections == (K.BAEC_NOT_CURRENT,)
    assert counts(connection) == before


def test_the_transition_uses_the_stored_account_not_a_caller_supplied_one(repo):
    """The stored state is authoritative: a second request for the same state is a no-op."""
    assert move(repo, AO).allowed
    again = move(repo, AO)
    assert again.rejections == (K.SAME_STATE,)
    assert history_states(repo) == [(None, AO)]


# --- identifiers that do not exist --------------------------------------------

NOT_FOUND = {
    "account": lambda repo: repo.persist_transition_to_active_opportunity(
        "ACC-404", evaluation_evidence=evaluation_evidence(), authorization=state_auth(AO), recorded_at=LATER
    ),
    "BAEC record": lambda repo: move(repo, CD, baec_id="B-404"),
    "dormancy judgment": lambda repo: move(repo, CD, judgment_id=999),
    "basis interaction": lambda repo: move(repo, NP, basis_interaction_id="INT-404"),
    "interaction behind evaluation evidence": lambda repo: move(
        repo, AO, evaluation_evidence=EvaluationEvidence("ACC-1", excerpt("x", source_id="INT-404"), NOW)
    ),
    "interaction behind non-evaluation evidence": lambda repo: move(
        repo, NP, non_evaluation_evidence=NonEvaluationEvidence("ACC-1", excerpt("x", source_id="INT-404"), NOW)
    ),
    "history of an unknown account": lambda repo: repo.get_transition_history("ACC-404"),
}


@pytest.mark.parametrize("request_fn", NOT_FOUND.values(), ids=NOT_FOUND.keys())
def test_a_non_none_identifier_absent_from_storage_raises_not_found(repo, connection, request_fn):
    """A missing stored object is a caller error, not a business rejection; nothing is written."""
    ensure_dormancy_prerequisites(repo)
    before = counts(connection)
    with pytest.raises(RepositoryNotFoundError):
        request_fn(repo)
    assert counts(connection) == before  # in particular, no interaction was synthesized
    assert repo.get_account("ACC-1").state is None


def test_evidence_from_another_accounts_interaction_is_refused(repo, connection):
    before = counts(connection)
    foreign = EvaluationEvidence("ACC-1", excerpt("x", source_id="INT-9"), NOW)  # INT-9 is ACC-2's
    with pytest.raises(RepositoryVerificationError):
        move(repo, AO, evaluation_evidence=foreign)
    with pytest.raises(RepositoryVerificationError):
        move(repo, NP, basis_interaction_id="INT-9")
    assert counts(connection) == before
    assert repo.get_account("ACC-1").state is None


@pytest.mark.parametrize("fake", [True, "approved", {"human_confirmed": True}], ids=repr)
@pytest.mark.parametrize("to", STATES, ids=name)
def test_rc31_fake_authorization_raises_and_persists_nothing(repo, connection, to, fake):
    ensure_dormancy_prerequisites(repo)
    before = counts(connection)
    with pytest.raises(DomainValidationError):
        move(repo, to, authorization=fake)
    assert counts(connection) == before


def test_rc27_no_persistence_function_accepts_a_transition_result_or_a_signal():
    """No caller-built TransitionResult is ever taken as authority."""
    for method_name, method in inspect.getmembers(Repository, inspect.isfunction):
        if method_name.startswith("_"):
            continue
        signature = inspect.signature(method)
        for parameter in signature.parameters.values():
            assert parameter.name not in ("result", "transition_result", "signal")
            assert "TransitionResult" not in str(parameter.annotation)
    active = inspect.signature(Repository.persist_transition_to_active_opportunity)
    assert set(active.parameters) == {"self", "account_id", "evaluation_evidence", "authorization", "recorded_at"}


# --- stored state and history must agree --------------------------------------


def test_stale_state_rolls_back_the_whole_transition(repo, connection, monkeypatch):
    """Compare-and-set: if the stored state is not the state the decision was made on, nothing is kept."""
    assert move(repo, AO).allowed
    before = counts(connection)
    monkeypatch.setattr(repo, "get_account", lambda account_id: Account("ACC-1", "Harbor Surgical Center", None))
    with pytest.raises(RepositoryConflictError):
        repo.persist_transition_to_no_plausible_path(
            "ACC-1", ground=G.OTHER, reason="r", authorization=state_auth(NP), recorded_at=LATER
        )
    monkeypatch.undo()
    assert counts(connection) == before
    assert repo.get_account("ACC-1").state is AO


def test_account_state_overwritten_outside_a_transition_fails_closed(repo, connection):
    assert move(repo, AO).allowed
    connection.execute("UPDATE accounts SET state = 'CONDITIONALLY_DORMANT' WHERE account_id = 'ACC-1'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_account("ACC-1")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_transition_history("ACC-1")
    with pytest.raises(PersistenceIntegrityError):
        move(repo, NP)


def test_state_set_with_no_history_at_all_fails_closed(repo, connection):
    connection.execute("UPDATE accounts SET state = 'ACTIVE_OPPORTUNITY' WHERE account_id = 'ACC-2'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_account("ACC-2")


HISTORY_CORRUPTIONS = {
    "broken chain": "UPDATE account_state_transitions SET from_state = 'NO_PLAUSIBLE_PATH' WHERE transition_id = 2",
    "unknown destination state": "UPDATE account_state_transitions SET to_state = 'HOT_LEAD' WHERE transition_id = 2",
    "authorization for another destination": "UPDATE human_authorizations SET target_state = 'ACTIVE_OPPORTUNITY' WHERE authorization_id = (SELECT authorization_id FROM account_state_transitions WHERE transition_id = 2)",
    "authorization for another account": "UPDATE human_authorizations SET subject_id = 'ACC-2' WHERE authorization_id = (SELECT authorization_id FROM account_state_transitions WHERE transition_id = 2)",
    "ground removed from a no-plausible-path row": "UPDATE account_state_transitions SET ground = NULL WHERE transition_id = 2",
    "reason removed from a no-plausible-path row": "UPDATE account_state_transitions SET reason = NULL WHERE transition_id = 2",
    "non-evaluation evidence removed when leaving Active": "UPDATE account_state_transitions SET non_evaluation_evidence_id = NULL WHERE transition_id = 2",
    "evaluation evidence removed from the Active row": "UPDATE account_state_transitions SET evaluation_evidence_id = NULL WHERE transition_id = 1",
    "ground added to the Active row": "UPDATE account_state_transitions SET ground = 'OTHER', reason = 'r' WHERE transition_id = 1",
    "unresolved value is not a list": "UPDATE account_state_transitions SET unresolved = '{}' WHERE transition_id = 2",
    "unresolved value is not JSON": "UPDATE account_state_transitions SET unresolved = 'none' WHERE transition_id = 2",
    "unknown unresolved kind": "UPDATE account_state_transitions SET unresolved = '[\"SOMETHING\"]' WHERE transition_id = 2",
    "unresolved item on a no-plausible-path row": "UPDATE account_state_transitions SET unresolved = '[\"ADDRESSABILITY_UNKNOWN\"]' WHERE transition_id = 2",
    "naive recorded timestamp": "UPDATE account_state_transitions SET recorded_at = '2026-02-01T09:30:00' WHERE transition_id = 2",
    "evaluation evidence re-pointed at another account": "UPDATE evaluation_evidence SET account_id = 'ACC-2'",
    "evaluation evidence row points at evidence from another interaction": "UPDATE evaluation_evidence SET interaction_id = 'INT-3'",
}


@pytest.mark.parametrize("sql", HISTORY_CORRUPTIONS.values(), ids=HISTORY_CORRUPTIONS.keys())
def test_corrupted_transition_history_fails_closed(repo, connection, sql):
    assert move(repo, AO).allowed
    assert move(repo, NP).allowed
    assert len(repo.get_transition_history("ACC-1")) == 2
    tamper(connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        repo.get_transition_history("ACC-1")


def test_transition_history_entry_validates_itself():
    good = dict(transition_id=1, account_id="ACC-1", from_state=None, to_state=AO,
                authorization=state_auth(AO), recorded_at=LATER, evaluation_evidence=evaluation_evidence())
    assert TransitionHistoryEntry(**good).to_state is AO
    for change in (
        dict(transition_id=0),
        dict(from_state=AO),
        dict(authorization=state_auth(NP)),
        dict(authorization=state_auth(AO, "ACC-2")),
        dict(evaluation_evidence=None),
        dict(recorded_at=datetime(2026, 1, 1)),
        dict(ground=G.OTHER, reason="r"),
        dict(baec_id="B-1"),
        dict(basis_interaction_id="INT-1"),
        dict(unresolved=(TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,)),
        dict(non_evaluation_evidence=non_evaluation_evidence()),
    ):
        with pytest.raises(RecordValidationError):
            TransitionHistoryEntry(**{**good, **change})


# --- dormant history must agree with the BAEC and judgment it references -------
#
# These tests deliberately bypass the database guards (tamper) so that the
# repository's own fail-closed checks are exercised independently of SQL.


def _confirmed_for_other_account(repo, baec_id="B-OTHER"):
    """A genuine confirmed BAEC and judgment belonging to ACC-2."""
    from baec_app.domain.baec_rules import create_confirmed_baec_record
    from tests.builders import candidate, confirm_auth

    theirs = excerpt(source_id="INT-9")
    repo.save_confirmed_baec(
        create_confirmed_baec_record(
            candidate(
                account_id="ACC-2", source_interaction_id="INT-9", source_excerpt=theirs, criterion_evidence=theirs
            ),
            baec_id=baec_id,
            captured_at=NOW,
            confirmation=confirm_auth(baec_id),
        )
    )
    return save_judgment(repo, baec_id)


def _dormant(repo, **answers):
    save_confirmed(repo)
    judgment_id = save_judgment(repo, **answers)
    assert move(repo, CD, judgment_id=judgment_id).allowed
    assert len(repo.get_transition_history("ACC-1")) == 1
    return judgment_id


def test_dormant_history_referencing_a_baec_from_another_account_fails_closed(repo, connection):
    _dormant(repo)
    other_judgment = _confirmed_for_other_account(repo)
    tamper(
        connection,
        "UPDATE account_state_transitions SET baec_id = 'B-OTHER', judgment_id = ?",
        (other_judgment,),
    )
    with pytest.raises(PersistenceIntegrityError, match="BAEC of a different account"):
        repo.get_transition_history("ACC-1")


def test_dormant_history_referencing_a_judgment_for_another_baec_fails_closed(repo, connection):
    _dormant(repo)
    save_confirmed(repo, "B-2")
    other_judgment = save_judgment(repo, "B-2")
    tamper(connection, "UPDATE account_state_transitions SET judgment_id = ?", (other_judgment,))
    with pytest.raises(PersistenceIntegrityError, match="judgment for a different BAEC"):
        repo.get_transition_history("ACC-1")


def test_dormant_history_referencing_a_non_confirmed_record_fails_closed(repo, connection):
    from baec_app.domain.baec_rules import create_nonconfirmed_classification_record
    from baec_app.domain.enums import ArticulationOrigin
    from tests.builders import candidate

    _dormant(repo)
    repo.save_classification_record(
        create_nonconfirmed_classification_record(
            candidate(ArticulationOrigin.SELLER_SEEDED), baec_id="B-NEG", captured_at=NOW
        )
    )
    negative_judgment = save_judgment(repo, "B-NEG")
    tamper(
        connection,
        "UPDATE account_state_transitions SET baec_id = 'B-NEG', judgment_id = ?",
        (negative_judgment,),
    )
    with pytest.raises(PersistenceIntegrityError, match="not a confirmed BAEC"):
        repo.get_transition_history("ACC-1")


UNRESOLVED_MISMATCHES = {
    "addressability YES but unresolved claims ADDRESSABILITY_UNKNOWN": (
        ReviewAnswer.YES,
        '["ADDRESSABILITY_UNKNOWN"]',
    ),
    "addressability UNKNOWN but unresolved is empty": (ReviewAnswer.UNKNOWN, "[]"),
}


@pytest.mark.parametrize("case", UNRESOLVED_MISMATCHES.values(), ids=UNRESOLVED_MISMATCHES.keys())
def test_rc22_unresolved_disagreeing_with_the_judgment_fails_closed(repo, connection, case):
    """RC-22: unknown addressability must stay visibly unresolved, and only then."""
    addressability, stored_unresolved = case
    _dormant(repo, addressability=addressability)
    tamper(connection, "UPDATE account_state_transitions SET unresolved = ?", (stored_unresolved,))
    with pytest.raises(PersistenceIntegrityError, match="unresolved items disagree"):
        repo.get_transition_history("ACC-1")


JUDGMENT_NO_LONGER_SUPPORTS = {
    "plausibility NO": ("plausibility", "NO", "plausibility is not YES"),
    "plausibility UNKNOWN": ("plausibility", "UNKNOWN", "plausibility is not YES"),
    "plausibility NOT_YET": ("plausibility", "NOT_YET", "plausibility is not YES"),
    "addressability NO": ("addressability", "NO", "addressability blocks dormancy"),
    "addressability NOT_YET": ("addressability", "NOT_YET", "addressability blocks dormancy"),
}


@pytest.mark.parametrize("case", JUDGMENT_NO_LONGER_SUPPORTS.values(), ids=JUDGMENT_NO_LONGER_SUPPORTS.keys())
def test_rc22_dormant_history_whose_judgment_no_longer_supports_dormancy_fails_closed(repo, connection, case):
    column, answer, message = case
    _dormant(repo)
    tamper(connection, f"UPDATE dormancy_judgments SET {column} = ?", (answer,))
    with pytest.raises(PersistenceIntegrityError, match=message):
        repo.get_transition_history("ACC-1")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE account_state_transitions SET baec_id = 'B-404'",
        "UPDATE account_state_transitions SET judgment_id = 999",
    ],
    ids=["BAEC row missing", "judgment row missing"],
)
def test_dormant_history_referencing_a_missing_row_fails_closed(repo, connection, sql):
    """A dangling reference in stored history is corruption, not a caller's not-found."""
    _dormant(repo)
    tamper(connection, sql)
    with pytest.raises(PersistenceIntegrityError, match="missing row"):
        repo.get_transition_history("ACC-1")


def test_valid_dormant_history_still_loads_including_unknown_addressability(repo):
    judgment_id = _dormant(repo, addressability=ReviewAnswer.UNKNOWN)
    (entry,) = repo.get_transition_history("ACC-1")
    assert entry.judgment_id == judgment_id
    assert entry.unresolved == (TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,)


@pytest.mark.parametrize(
    "status", [StalenessStatus.REVIEW_DUE, StalenessStatus.STALE, StalenessStatus.RETIRED], ids=lambda s: s.value
)
def test_rc29_baec_going_stale_later_does_not_invalidate_the_historical_transition(repo, connection, status):
    """History records what was true when the transition happened; staleness may change afterwards."""
    _dormant(repo)
    connection.execute("UPDATE baec_records SET staleness_status = ?", (status.value,))
    (entry,) = repo.get_transition_history("ACC-1")
    assert entry.to_state is CD and entry.baec_id == "B-1"
    assert repo.get_account("ACC-1").state is CD


def _raw_dormant_insert(connection, baec_id, judgment_id):
    """Insert a dormant history row by raw SQL, valid in every respect except the given pair."""
    authorization_id = connection.execute(
        "INSERT INTO human_authorizations (authorized_by, authorized_at, action, subject_id, target_state) "
        "VALUES ('x', '2026-02-01T09:30:00.000000+00:00', 'CHANGE_ACCOUNT_STATE', 'ACC-1', 'CONDITIONALLY_DORMANT')"
    ).lastrowid
    connection.execute(
        "INSERT INTO account_state_transitions (account_id, to_state, authorization_id, baec_id, judgment_id, "
        "unresolved, recorded_at) VALUES ('ACC-1', 'CONDITIONALLY_DORMANT', ?, ?, ?, '[]', "
        "'2026-02-01T09:30:00.000000+00:00')",
        (authorization_id, baec_id, judgment_id),
    )


def test_database_binds_a_transitions_baec_to_its_account_and_its_judgment_to_that_baec(repo, connection):
    """Schema-level guard, independent of the repository checks above."""
    import sqlite3

    save_confirmed(repo, "B-1")
    own_judgment = save_judgment(repo, "B-1")
    save_confirmed(repo, "B-2")
    judgment_for_b2 = save_judgment(repo, "B-2")
    other_account_judgment = _confirmed_for_other_account(repo)
    before = counts(connection)["account_state_transitions"]

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _raw_dormant_insert(connection, "B-OTHER", other_account_judgment)  # BAEC of ACC-2
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _raw_dormant_insert(connection, "B-1", judgment_for_b2)  # judgment of another BAEC
    assert counts(connection)["account_state_transitions"] == before

    _raw_dormant_insert(connection, "B-1", own_judgment)  # the matching pair is accepted
    assert counts(connection)["account_state_transitions"] == before + 1


def test_composite_parent_keys_exist_on_baec_records_and_dormancy_judgments(connection):
    def unique_column_sets(table):
        found = []
        for index in connection.execute(f"PRAGMA index_list({table})").fetchall():
            if index[2]:  # unique
                found.append(tuple(r[2] for r in connection.execute(f"PRAGMA index_info({index[1]})")))
        return found

    assert ("baec_id", "account_id") in unique_column_sets("baec_records")
    assert ("judgment_id", "baec_id") in unique_column_sets("dormancy_judgments")
    targets = {
        (row[2], row[3], row[4]) for row in connection.execute("PRAGMA foreign_key_list(account_state_transitions)")
    }
    assert ("baec_records", "baec_id", "baec_id") in targets and ("baec_records", "account_id", "account_id") in targets
    assert ("dormancy_judgments", "judgment_id", "judgment_id") in targets
    assert ("dormancy_judgments", "baec_id", "baec_id") in targets
