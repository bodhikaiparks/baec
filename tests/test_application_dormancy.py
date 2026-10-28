"""Phase 4C: recording human dormancy judgments (no account transitions yet)."""

import inspect
from itertools import product

import pytest

from baec_app.application.account_state import AccountStateService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.errors import (
    ApplicationValidationError,
    ApprovalAlreadyUsed,
    ApprovalKindMismatch,
    ProposalNotAuthoritative,
)
from baec_app.application.requests import RequestKind
from baec_app.data.database import PersistenceIntegrityError, RepositoryConflictError, RepositoryNotFoundError
from baec_app.data.records import PersistedDormancyJudgment
from baec_app.domain.enums import ArticulationOrigin, AuthorizationAction, ReviewAnswer
from baec_app.domain.models import HumanAuthorization
from tests.application_builders import FixedClock, SequentialIds
from tests.builders import NOW, candidate
from tests.persistence_builders import (  # noqa: F401
    connection,
    dump,
    no_leaked_connections,
    repo,
    save_confirmed,
    tamper,
)


class Services:
    def __init__(self, repository):
        self.repository = repository
        self.clock = FixedClock()
        self.ids = SequentialIds()
        self.gate = HumanConfirmationGate(self.clock, self.ids)
        self.classification = ClassificationService(repository, self.gate, self.clock, self.ids)
        self.dormancy = DormancyJudgmentService(repository, self.gate, self.clock, self.ids)
        self.state = AccountStateService(repository, self.gate, self.clock, self.ids)
        self.session = self.gate.open_session("reviewer-1")

    def approve(self, request):
        return self.gate.approve(self.session, request.request_id, displayed_digest=request.digest)

    def open_judgment(self, baec_id="B-1", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.UNKNOWN, notes=None):
        return self.dormancy.open_request(
            self.session, baec_id, plausibility=plausibility, addressability=addressability, notes=notes
        )


@pytest.fixture
def services(repo):
    save_confirmed(repo)  # B-1, a confirmed CURRENT BAEC for ACC-1
    return Services(repo)


def test_a_request_opens_for_an_existing_baec(services):
    request = services.open_judgment(notes="Buyer named renewal as the trigger.")
    assert request.kind is RequestKind.RECORD_DORMANCY_JUDGMENT
    assert request.action is AuthorizationAction.RECORD_DORMANCY_JUDGMENT
    assert request.subject_id == "B-1" and request.target_state is None and request.preview is None
    assert list(services.gate._requests) == [request.request_id]


def test_a_missing_baec_propagates_the_repository_error_unchanged(services, repo):
    with pytest.raises(RepositoryNotFoundError) as expected:
        repo.get_baec_record("B-404")
    with pytest.raises(RepositoryNotFoundError) as raised:
        services.open_judgment("B-404")
    assert str(raised.value) == str(expected.value)
    assert services.gate._requests == {}


def test_a_tampered_baec_propagates_the_integrity_error_unchanged(services, connection):
    tamper(connection, "UPDATE baec_records SET articulation_origin = 'SELLER_SEEDED'")
    with pytest.raises(PersistenceIntegrityError):
        services.open_judgment()
    assert services.gate._requests == {}


@pytest.mark.parametrize("bad", [None, "T-0001"], ids=["None", "id"])
def test_open_request_requires_a_session_object(services, bad):
    with pytest.raises(ApplicationValidationError):
        services.dormancy.open_request(bad, "B-1", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES)


ANSWERS = list(product(ReviewAnswer, repeat=2))


@pytest.mark.parametrize("plausibility,addressability", ANSWERS, ids=[f"{p.name}-{a.name}" for p, a in ANSWERS])
def test_the_judgment_records_exactly_the_human_answers_without_judging_them(services, repo, plausibility, addressability):
    """Recording is not a dormancy decision: every answer pair is recorded as given, including UNKNOWN."""
    request = services.open_judgment(plausibility=plausibility, addressability=addressability, notes="Synthetic note.")
    persisted = services.dormancy.record(services.approve(request))
    assert type(persisted) is PersistedDormancyJudgment
    judgment = persisted.judgment
    assert (judgment.baec_id, judgment.plausibility, judgment.addressability, judgment.notes) == (
        "B-1",
        plausibility,
        addressability,
        "Synthetic note.",
    )
    assert repo.list_dormancy_judgments("B-1") == (persisted,)


def test_the_persisted_authorization_comes_from_the_session_and_the_redeemed_request(services, repo):
    request = services.open_judgment()
    services.clock.advance(minutes=4)
    approval = services.approve(request)
    services.clock.advance(minutes=4)
    persisted = services.dormancy.record(approval)
    expected = HumanAuthorization("reviewer-1", approval.approved_at, AuthorizationAction.RECORD_DORMANCY_JUDGMENT, "B-1")
    assert persisted.judgment.authorization == expected
    assert repo.list_dormancy_judgments("B-1")[0].judgment.authorization == expected


def test_a_judgment_may_be_recorded_on_a_nonconfirmed_record_because_the_domain_allows_it(services, repo):
    """No eligibility rule is invented here; whether a judgment supports dormancy is the state machine's call."""
    record = services.classification.save_nonconfirmed(candidate(ArticulationOrigin.SELLER_SEEDED), captured_at=NOW)
    request = services.open_judgment(record.baec_id, plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES)
    assert services.dormancy.record(services.approve(request)).judgment.baec_id == record.baec_id


def test_the_record_command_takes_only_an_approval():
    assert list(inspect.signature(DormancyJudgmentService.record).parameters) == ["self", "approval"]


@pytest.mark.parametrize("value", [True, {"approved": True}, None], ids=["True", "mapping", "None"])
def test_record_refuses_anything_but_an_approval(services, connection, value):
    before = dump(connection)
    with pytest.raises(ProposalNotAuthoritative):
        services.dormancy.record(value)
    assert dump(connection) == before


def test_a_judgment_approval_cannot_drive_confirmation_and_is_not_consumed(services, connection):
    approval = services.approve(services.open_judgment())
    before = dump(connection)
    with pytest.raises(ApprovalKindMismatch):
        services.classification.confirm(approval)
    assert dump(connection) == before
    assert services.dormancy.record(approval).judgment.baec_id == "B-1"


def test_a_confirmation_approval_cannot_drive_a_judgment_and_is_not_consumed(services, repo, connection):
    confirmation = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    approval = services.approve(confirmation)
    before = dump(connection)
    with pytest.raises(ApprovalKindMismatch):
        services.dormancy.record(approval)
    assert dump(connection) == before
    assert services.classification.confirm(approval).baec_id == confirmation.payload.baec_id


def test_a_repository_failure_after_redemption_propagates_unchanged_and_consumes_the_approval(services, repo, connection, monkeypatch):
    approval = services.approve(services.open_judgment())
    failure = RepositoryConflictError("synthetic conflict")

    def refuse(judgment):
        raise failure

    monkeypatch.setattr(repo, "record_dormancy_judgment", refuse)
    before = dump(connection)
    with pytest.raises(RepositoryConflictError) as raised:
        services.dormancy.record(approval)
    assert raised.value is failure
    assert dump(connection) == before
    monkeypatch.undo()
    with pytest.raises(ApprovalAlreadyUsed):
        services.dormancy.record(approval)


def test_recording_a_judgment_does_not_change_account_state(services, repo):
    assert repo.get_account("ACC-1").state is None
    services.dormancy.record(services.approve(services.open_judgment(addressability=ReviewAnswer.YES)))
    assert repo.get_account("ACC-1").state is None
    assert repo.get_transition_history("ACC-1") == ()


def test_judgment_content_comes_from_the_redeemed_request_not_the_latest_call(services, repo):
    first = services.open_judgment(plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.UNKNOWN, notes="First.")
    services.open_judgment(plausibility=ReviewAnswer.NO, addressability=ReviewAnswer.NO, notes="Second.")
    persisted = services.dormancy.record(services.approve(first))
    judgment = persisted.judgment
    assert (judgment.plausibility, judgment.addressability, judgment.notes) == (ReviewAnswer.YES, ReviewAnswer.UNKNOWN, "First.")


def test_a_look_alike_session_cannot_open_a_judgment_request(services, connection):
    from baec_app.application.context import Actor, InteractionSession
    from baec_app.application.errors import SessionNotRecognized

    twin = InteractionSession(services.session.session_id, Actor("reviewer-1"), services.session.opened_at)
    assert twin == services.session and twin is not services.session
    before = dump(connection)
    with pytest.raises(SessionNotRecognized):
        services.dormancy.open_request(twin, "B-1", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES)
    assert services.gate._requests == {}
    assert dump(connection) == before
    assert services.dormancy.record(services.approve(services.open_judgment())).judgment.baec_id == "B-1"


# --- the two-step dormancy flow (4D): judgment and transition are separate human actions -----


def test_judgment_then_transition_are_two_independent_human_actions(services, repo):
    from baec_app.application.errors import ReferenceMismatch
    from baec_app.domain.enums import AccountState, TransitionUnresolvedKind

    # Before any judgment exists, no transition request can be opened.
    with pytest.raises(ReferenceMismatch):
        services.state.open_move_to_conditionally_dormant_request(services.session, "ACC-1", baec_id="B-1", judgment_id=1)

    # Human action 1: the judgment.
    judgment_request = services.open_judgment(plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.UNKNOWN)
    services.clock.advance(minutes=1)
    judgment_approval = services.approve(judgment_request)
    persisted = services.dormancy.record(judgment_approval)
    assert repo.get_account("ACC-1").state is None  # recording a judgment changes no state

    # The judgment approval is spent and cannot also authorize the transition.
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_conditionally_dormant(judgment_approval)

    # Human action 2: a separate transition request and a separate approval.
    transition_request = services.state.open_move_to_conditionally_dormant_request(
        services.session, "ACC-1", baec_id="B-1", judgment_id=persisted.judgment_id
    )
    services.clock.advance(minutes=1)
    transition_approval = services.approve(transition_request)
    assert transition_approval.approval_id != judgment_approval.approval_id
    assert transition_approval.approved_at > judgment_approval.approved_at
    result = services.state.move_to_conditionally_dormant(transition_approval)

    assert result.allowed and result.to_state is AccountState.CONDITIONALLY_DORMANT
    assert result.unresolved == (TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,)
    entry = repo.get_transition_history("ACC-1")[-1]
    assert entry.judgment_id == persisted.judgment_id
    assert persisted.judgment.authorization.action is AuthorizationAction.RECORD_DORMANCY_JUDGMENT
    assert entry.authorization.action is AuthorizationAction.CHANGE_ACCOUNT_STATE
    assert entry.authorization.authorized_at == transition_approval.approved_at != persisted.judgment.authorization.authorized_at


def test_an_unapproved_judgment_request_does_not_enable_a_transition_request(services, repo):
    from baec_app.application.errors import ReferenceMismatch

    services.open_judgment()  # opened but never approved or recorded
    with pytest.raises(ReferenceMismatch):
        services.state.open_move_to_conditionally_dormant_request(services.session, "ACC-1", baec_id="B-1", judgment_id=1)
