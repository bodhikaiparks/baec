"""Phase 4E: domain and repository errors pass through the facades unchanged."""

import pytest

from baec_app.application.composition import build_command_facade, build_proposal_facade, open_read_connection
from baec_app.application.errors import NotConfirmable, ReferenceMismatch, RequestNotCoherent
from baec_app.data.database import (
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
    open_database,
)
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.baec_rules import classify_candidate, create_nonconfirmed_classification_record
from baec_app.domain.enums import AccountState, ArticulationOrigin, NoPlausiblePathGround, ReviewAnswer
from baec_app.domain.models import Account, DomainValidationError
from baec_app.domain.state_machine import transition_to_no_plausible_path
from tests.application_builders import FixedClock, SequentialIds
from tests.builders import NOW, candidate, excerpt
from tests.persistence_builders import (  # noqa: F401
    INTERACTION_TEXTS,
    dump,
    no_leaked_connections,
    put_in_state,
    save_confirmed,
    tamper,
)


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "errors.sqlite3")
    writer = open_database(path)
    repo = Repository(writer)
    repo.add_account(Account("ACC-1", "Harbor Surgical Center"))
    for interaction_id, text in INTERACTION_TEXTS.items():
        repo.add_interaction(SourceInteraction(interaction_id, "ACC-1", NOW, text))
    save_confirmed(repo)
    reader = open_read_connection(path)
    command = build_command_facade(repo, clock=FixedClock(), ids=SequentialIds())
    proposal = build_proposal_facade(reader)
    session = command.gate.open_session("reviewer-1")

    class App:
        pass

    a = App()
    a.writer, a.repo, a.reader, a.command, a.proposal, a.session = writer, repo, reader, command, proposal, session
    yield a
    reader.close()
    writer.close()


def _raised(call):
    with pytest.raises(Exception) as raised:
        call()
    return raised.value


def _same(expected, actual):
    assert type(actual) is type(expected) and str(actual) == str(expected)


# --- repository errors -----------------------------------------------------------------


@pytest.mark.parametrize("facade", ["command", "proposal"])
def test_not_found_propagates_unchanged_through_reads(app, facade):
    expected = _raised(lambda: app.repo.get_account("ACC-404"))
    assert type(expected) is RepositoryNotFoundError
    _same(expected, _raised(lambda: getattr(app, facade).reads.get_account("ACC-404")))


def test_not_found_propagates_unchanged_through_request_opening(app):
    expected = _raised(lambda: app.repo.get_baec_record("B-404"))
    actual = _raised(lambda: app.command.request_dormancy_judgment(
        app.session, "B-404", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES
    ))
    _same(expected, actual)


def test_conflict_propagates_as_the_same_object(app, monkeypatch):
    request = app.command.request_baec_confirmation(app.session, candidate(), captured_at=NOW)
    approval = app.command.gate.approve(app.session, request.request_id, displayed_digest=request.digest)
    failure = RepositoryConflictError("synthetic conflict")

    def refuse(record):
        raise failure

    monkeypatch.setattr(app.repo, "save_confirmed_baec", refuse)
    with pytest.raises(RepositoryConflictError) as raised:
        app.command.confirm_baec(approval)
    assert raised.value is failure


def test_verification_failure_propagates_unchanged(app):
    fabricated = excerpt("We have started a formal RFP.")
    cand = candidate(source_excerpt=fabricated, criterion_evidence=fabricated)
    expected = _raised(lambda: app.repo.save_classification_record(
        create_nonconfirmed_classification_record(
            candidate(ArticulationOrigin.SELLER_SEEDED, source_excerpt=fabricated, criterion_evidence=fabricated),
            baec_id="B-DIRECT", captured_at=NOW,
        )
    ))
    request = app.command.request_baec_confirmation(app.session, cand, captured_at=NOW)
    approval = app.command.gate.approve(app.session, request.request_id, displayed_digest=request.digest)
    actual = _raised(lambda: app.command.confirm_baec(approval))
    assert type(actual) is RepositoryVerificationError
    assert str(actual) == str(expected)  # same repository message, not reworded


@pytest.mark.parametrize("facade", ["command", "proposal"])
def test_integrity_failure_propagates_unchanged(app, facade):
    tamper(app.writer, "UPDATE baec_records SET articulation_origin = 'SELLER_SEEDED'")
    expected = _raised(lambda: app.repo.get_baec_record("B-1"))
    assert type(expected) is PersistenceIntegrityError
    _same(expected, _raised(lambda: getattr(app, facade).reads.get_baec_record("B-1")))


def test_integrity_failure_propagates_through_request_opening(app):
    tamper(app.writer, "UPDATE baec_records SET articulation_origin = 'SELLER_SEEDED'")
    expected = _raised(lambda: app.repo.get_baec_record("B-1"))
    _same(expected, _raised(lambda: app.command.request_dormancy_judgment(
        app.session, "B-1", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES
    )))


# --- domain and research-rule failures ----------------------------------------------------


def test_domain_factory_refusal_propagates_unchanged(app):
    expected = _raised(lambda: create_nonconfirmed_classification_record(candidate(), baec_id="X", captured_at=NOW))
    assert type(expected) is DomainValidationError
    before = dump(app.writer)
    _same(expected, _raised(lambda: app.command.save_nonconfirmed_classification(candidate(), captured_at=NOW)))
    assert dump(app.writer) == before


@pytest.mark.parametrize("facade", ["command", "proposal"])
def test_not_confirmable_carries_the_locked_result_unreworded(app, facade):
    seller_seeded = candidate(ArticulationOrigin.SELLER_SEEDED)
    expected = classify_candidate(seller_seeded)
    if facade == "command":
        call = lambda: app.command.request_baec_confirmation(app.session, seller_seeded, captured_at=NOW)  # noqa: E731
    else:
        call = lambda: app.proposal.propose_confirmation(seller_seeded, captured_at=NOW)  # noqa: E731
    error = _raised(call)
    assert type(error) is NotConfirmable and error.result == expected and str(error) == expected.reason_text


@pytest.mark.parametrize("facade", ["command", "proposal"])
def test_request_not_coherent_carries_the_locked_preview_unreworded(app, facade):
    expected = transition_to_no_plausible_path(app.repo.get_account("ACC-1"), ground=None, reason="", authorization=None)
    if facade == "command":
        call = lambda: app.command.request_move_to_no_plausible_path(app.session, "ACC-1", ground=None, reason="")  # noqa: E731
    else:
        call = lambda: app.proposal.propose_move_to_no_plausible_path("ACC-1", ground=None, reason="")  # noqa: E731
    error = _raised(call)
    assert type(error) is RequestNotCoherent and error.result == expected and str(error) == expected.rejection_text


def test_reference_mismatch_passes_through_the_facade(app):
    error = _raised(lambda: app.command.request_move_to_conditionally_dormant(app.session, "ACC-1", baec_id="B-1", judgment_id=999))
    assert type(error) is ReferenceMismatch and str(error) == "judgment 999 is not a recorded judgment of BAEC B-1"


def test_a_rejected_transition_result_is_returned_unchanged_not_raised(app):
    request = app.command.request_move_to_no_plausible_path(
        app.session, "ACC-1", ground=NoPlausiblePathGround.OTHER, reason="No condition named."
    )
    approval = app.command.gate.approve(app.session, request.request_id, displayed_digest=request.digest)
    put_in_state(app.repo, AccountState.NO_PLAUSIBLE_PATH)  # another writer gets there first
    result = app.command.move_to_no_plausible_path(approval)
    assert not result.allowed and result.rejection_text.startswith("Transition to NO_PLAUSIBLE_PATH rejected")
