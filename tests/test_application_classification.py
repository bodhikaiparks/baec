"""Phase 4C: classification previews, non-confirmed saves, and human confirmation."""

import inspect
from itertools import product

import pytest

from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.errors import (
    ApplicationValidationError,
    ApprovalAlreadyUsed,
    ApprovalKindMismatch,
    NotConfirmable,
    ProposalNotAuthoritative,
    ReferenceMismatch,
)
from baec_app.application.proposals import ProposalOrigin
from baec_app.application.requests import ConfirmBaecPayload, RequestKind, build_request
from baec_app.data.database import RepositoryNotFoundError, RepositoryVerificationError
from baec_app.domain.baec_rules import classify_candidate
from baec_app.domain.enums import (
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    StalenessStatus,
)
from baec_app.domain.models import DomainValidationError, HumanAuthorization
from tests.application_builders import FixedClock, SequentialIds
from tests.builders import NOW, candidate, excerpt
from tests.persistence_builders import connection, dump, no_leaked_connections, repo  # noqa: F401

CRITERIA = list(BaecCriterion)


class Services:
    def __init__(self, repository):
        self.repository = repository
        self.clock = FixedClock()
        self.ids = SequentialIds()
        self.gate = HumanConfirmationGate(self.clock, self.ids)
        self.classification = ClassificationService(repository, self.gate, self.clock, self.ids)
        self.dormancy = DormancyJudgmentService(repository, self.gate, self.clock, self.ids)
        self.session = self.gate.open_session("reviewer-1")

    def approve(self, request):
        return self.gate.approve(self.session, request.request_id, displayed_digest=request.digest)

    def registered(self):
        return dict(self.gate._requests)


@pytest.fixture
def services(repo):
    return Services(repo)


# --- preview ----------------------------------------------------------------------


def test_preview_is_the_locked_classifier_result_and_writes_nothing(services, connection):
    before = dump(connection)
    for cand in (candidate(), candidate(ArticulationOrigin.SELLER_SEEDED), candidate(ArticulationOrigin.UNCERTAIN)):
        preview = services.classification.preview(cand)
        assert preview.result == classify_candidate(cand)
        assert preview.confirmable is (preview.result.classification is BaecClassification.CONFIRMED_BAEC)
    assert dump(connection) == before


# --- the exhaustive confirmation boundary (243 combinations) ----------------------------

COMBINATIONS = [(findings, origin) for findings in product(list(CriterionFinding), repeat=4) for origin in ArticulationOrigin]


@pytest.mark.parametrize(
    "combo", COMBINATIONS, ids=lambda c: "-".join(f.name for f in c[0]) + "+" + c[1].name
)
def test_only_all_met_buyer_generated_opens_a_confirmation_request(services, connection, combo):
    findings, origin = combo
    cand = candidate(origin, dict(zip(CRITERIA, findings)))
    expected = classify_candidate(cand)
    confirmable = all(f is CriterionFinding.MET for f in findings) and origin is ArticulationOrigin.BUYER_GENERATED
    assert (expected.classification is BaecClassification.CONFIRMED_BAEC) is confirmable  # locked oracle agrees
    before = dump(connection)
    if confirmable:
        request = services.classification.open_confirmation_request(services.session, cand, captured_at=NOW)
        assert request.kind is RequestKind.CONFIRM_BAEC and request.preview.result == expected
        assert list(services.registered()) == [request.request_id]
    else:
        with pytest.raises(NotConfirmable) as raised:
            services.classification.open_confirmation_request(services.session, cand, captured_at=NOW)
        assert raised.value.result == expected
        assert str(raised.value) == expected.reason_text
        assert services.registered() == {}
    assert dump(connection) == before


def test_the_243_cases_contain_exactly_one_confirmable_combination():
    assert len(COMBINATIONS) == 243
    confirmable = [
        c for c in COMBINATIONS if classify_candidate(candidate(c[1], dict(zip(CRITERIA, c[0])))).classification
        is BaecClassification.CONFIRMED_BAEC
    ]
    assert confirmable == [((CriterionFinding.MET,) * 4, ArticulationOrigin.BUYER_GENERATED)]


# --- request-time referential validation ---------------------------------------------


def _repository_error(call):
    with pytest.raises(Exception) as raised:
        call()
    return raised.value


def test_missing_account_propagates_the_repository_error_unchanged(services, repo):
    expected = _repository_error(lambda: repo.get_account("ACC-404"))
    with pytest.raises(RepositoryNotFoundError) as raised:
        services.classification.open_confirmation_request(services.session, candidate(account_id="ACC-404"), captured_at=NOW)
    assert type(raised.value) is type(expected) and str(raised.value) == str(expected)
    assert services.registered() == {}


def test_missing_interaction_propagates_the_repository_error_unchanged(services, repo):
    missing = excerpt(source_id="INT-404")
    cand = candidate(source_interaction_id="INT-404", source_excerpt=missing, criterion_evidence=missing)
    expected = _repository_error(lambda: repo.get_interaction("INT-404"))
    with pytest.raises(RepositoryNotFoundError) as raised:
        services.classification.open_confirmation_request(services.session, cand, captured_at=NOW)
    assert type(raised.value) is type(expected) and str(raised.value) == str(expected)
    assert services.registered() == {}


@pytest.mark.parametrize(
    "account_id,interaction_id",
    [("ACC-2", "INT-1"), ("ACC-1", "INT-9")],
    ids=["ACC-2 citing ACC-1's interaction", "ACC-1 citing ACC-2's interaction"],
)
def test_an_interaction_of_another_account_is_a_reference_mismatch(services, connection, account_id, interaction_id):
    cited = excerpt(source_id=interaction_id)
    cand = candidate(account_id=account_id, source_interaction_id=interaction_id, source_excerpt=cited, criterion_evidence=cited)
    before = dump(connection)
    with pytest.raises(ReferenceMismatch):
        services.classification.open_confirmation_request(services.session, cand, captured_at=NOW)
    assert services.registered() == {}
    assert dump(connection) == before


@pytest.mark.parametrize("bad", [None, "T-0001", {"session_id": "T-0001"}], ids=["None", "id", "mapping"])
def test_open_confirmation_request_requires_a_session_object(services, bad):
    with pytest.raises(ApplicationValidationError):
        services.classification.open_confirmation_request(bad, candidate(), captured_at=NOW)


# --- non-confirmed saves ----------------------------------------------------------------


def test_save_nonconfirmed_refuses_a_confirmable_candidate(services, connection):
    before = dump(connection)
    with pytest.raises(DomainValidationError):
        services.classification.save_nonconfirmed(candidate(), captured_at=NOW)
    assert dump(connection) == before


@pytest.mark.parametrize(
    "cand",
    [candidate(ArticulationOrigin.SELLER_SEEDED), candidate(findings={BaecCriterion.PROSPECTIVE_CONDITION: CriterionFinding.UNKNOWN})],
    ids=["NOT_BAEC", "INSUFFICIENT_EVIDENCE"],
)
def test_save_nonconfirmed_persists_the_locked_classification(services, repo, cand):
    record = services.classification.save_nonconfirmed(cand, captured_at=NOW)
    expected = classify_candidate(cand)
    assert record.baec_id == "BAEC-0001"
    assert (record.classification, record.classification_reason) == (expected.classification, expected.reason_text)
    assert record.confirmation is None
    assert repo.get_baec_record("BAEC-0001") == record


# --- confirmation ------------------------------------------------------------------------


def test_confirmation_request_fixes_its_baec_id_before_approval(services):
    request = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    assert request.payload.baec_id == request.subject_id == "BAEC-0001"
    assert request.action is AuthorizationAction.CONFIRM_BAEC
    record = services.classification.confirm(services.approve(request))
    assert record.baec_id == "BAEC-0001"


def test_confirmation_persists_an_authorization_derived_from_the_session_and_request(services, repo):
    request = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    services.clock.advance(minutes=7)
    approval = services.approve(request)
    services.clock.advance(minutes=3)  # later than the approval: authorized_at must still be the approval time
    record = services.classification.confirm(approval)
    expected = HumanAuthorization("reviewer-1", approval.approved_at, AuthorizationAction.CONFIRM_BAEC, "BAEC-0001")
    assert record.confirmation == expected
    assert approval.approved_at != services.clock.now() and approval.approved_at != request.opened_at
    assert record.classification is BaecClassification.CONFIRMED_BAEC
    assert record.staleness_status is StalenessStatus.CURRENT
    assert record.candidate == request.payload.candidate and record.captured_at == request.payload.captured_at
    assert repo.get_baec_record("BAEC-0001") == record


def test_the_confirm_command_takes_only_an_approval():
    assert list(inspect.signature(ClassificationService.confirm).parameters) == ["self", "approval"]


@pytest.mark.parametrize("value", [True, {"approved": True}, None], ids=["True", "mapping", "None"])
def test_confirm_refuses_anything_but_an_approval(services, connection, value):
    before = dump(connection)
    with pytest.raises(ProposalNotAuthoritative):
        services.classification.confirm(value)
    assert dump(connection) == before


def test_a_confirmation_approval_used_for_a_judgment_is_refused_without_consuming(services, repo, connection):
    request = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    approval = services.approve(request)
    before = dump(connection)
    with pytest.raises(ApprovalKindMismatch):
        services.dormancy.record(approval)
    assert dump(connection) == before
    assert services.classification.confirm(approval).baec_id == "BAEC-0001"


def _inject_confirmation(services, cand, baec_id="B-INJECTED"):
    """Register a confirmation request directly, bypassing open_confirmation_request's classifier gate."""
    request = build_request(
        request_id="REQ-INJECTED",
        session_id=services.session.session_id,
        opened_at=NOW,
        kind=RequestKind.CONFIRM_BAEC,
        origin=ProposalOrigin.HUMAN_DRAFT,
        payload=ConfirmBaecPayload(baec_id=baec_id, candidate=cand, captured_at=NOW),
    )
    services.gate.register(services.session, request)
    return services.approve(request)


@pytest.mark.parametrize(
    "cand",
    [candidate(ArticulationOrigin.SELLER_SEEDED), candidate(ArticulationOrigin.UNCERTAIN),
     candidate(findings={BaecCriterion.PRESENT_NON_EVALUATION: CriterionFinding.NOT_MET})],
    ids=["seller-seeded", "uncertain origin", "C1 not met"],
)
def test_an_injected_request_for_a_nonqualifying_candidate_fails_at_the_locked_factory(services, connection, cand):
    approval = _inject_confirmation(services, cand)
    before = dump(connection)
    with pytest.raises(DomainValidationError, match="human confirmation does not change classification"):
        services.classification.confirm(approval)
    assert dump(connection) == before
    with pytest.raises(ApprovalAlreadyUsed):  # the failure came after a valid redemption
        services.classification.confirm(approval)


def test_fabricated_evidence_fails_at_the_repository_unchanged_and_consumes_the_approval(services, connection):
    fabricated = excerpt("We have started a formal RFP.")  # not in INT-1; the classifier cannot know that
    cand = candidate(source_excerpt=fabricated, criterion_evidence=fabricated)
    request = services.classification.open_confirmation_request(services.session, cand, captured_at=NOW)
    approval = services.approve(request)
    before = dump(connection)
    with pytest.raises(RepositoryVerificationError, match="verbatim") as raised:
        services.classification.confirm(approval)
    assert type(raised.value) is RepositoryVerificationError
    assert dump(connection) == before
    with pytest.raises(ApprovalAlreadyUsed):
        services.classification.confirm(approval)


def test_a_confirmed_baec_cannot_be_confirmed_twice_through_a_second_request(services, repo):
    first = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    services.classification.confirm(services.approve(first))
    second = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    assert second.payload.baec_id == "BAEC-0002"  # a new identifier; the first record is never overwritten
    services.classification.confirm(services.approve(second))
    assert [r.baec_id for r in repo.list_baec_records("ACC-1")] == ["BAEC-0001", "BAEC-0002"]



def test_confirmation_content_comes_from_the_redeemed_request_not_the_latest_call(services, repo):
    first_candidate = candidate(buyer_role="Materials manager")
    second_candidate = candidate(buyer_role="Chief financial officer")
    first = services.classification.open_confirmation_request(services.session, first_candidate, captured_at=NOW)
    second = services.classification.open_confirmation_request(services.session, second_candidate, captured_at=NOW)
    record = services.classification.confirm(services.approve(first))
    assert record.baec_id == first.payload.baec_id
    assert record.candidate == first_candidate != second_candidate
    assert repo.get_baec_record(first.payload.baec_id).candidate.buyer_role == "Materials manager"


# --- exact session identity and session binding ---------------------------------------


def _look_alike(session):
    from baec_app.application.context import Actor, InteractionSession

    return InteractionSession(session.session_id, Actor(session.actor.actor_id), session.opened_at)


def test_a_look_alike_session_cannot_open_a_confirmation_request(services, connection):
    from baec_app.application.errors import SessionNotRecognized

    twin = _look_alike(services.session)
    assert twin == services.session and twin is not services.session
    before = dump(connection)
    with pytest.raises(SessionNotRecognized):
        services.classification.open_confirmation_request(twin, candidate(), captured_at=NOW)
    assert services.registered() == {}
    assert dump(connection) == before
    request = services.classification.open_confirmation_request(services.session, candidate(), captured_at=NOW)
    assert services.classification.confirm(services.approve(request)).baec_id == request.payload.baec_id


def test_authorize_refuses_an_approval_from_a_different_session_than_the_request(services):
    """Identical content (so identical digest) and request id, but a different session: no authorization."""
    from baec_app.application import authority
    from baec_app.application.errors import RequestNotRecognized

    other_session = services.gate.open_session("reviewer-2")
    payload = ConfirmBaecPayload(baec_id="B-1", candidate=candidate(), captured_at=NOW)

    def request_in(session):
        return build_request(
            request_id="REQ-SHARED", session_id=session.session_id, opened_at=NOW,
            kind=RequestKind.CONFIRM_BAEC, origin=ProposalOrigin.HUMAN_DRAFT, payload=payload,
        )

    approved = request_in(services.session)
    elsewhere = request_in(other_session)
    assert (approved.request_id, approved.digest) == (elsewhere.request_id, elsewhere.digest)
    services.gate.register(services.session, approved)
    approval = services.approve(approved)
    with pytest.raises(RequestNotRecognized, match="different session"):
        authority.authorize(elsewhere, approval)
    assert authority.authorize(approved, approval).subject_id == "B-1"
