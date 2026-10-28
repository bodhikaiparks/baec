"""Phase 4D: account-state previews, request coherence, and human-authorized transitions.

Where a test needs to know whether the locked state machine allows something,
it asks the locked state machine (the oracle) instead of restating its rules.
"""

import inspect
from itertools import product
from types import SimpleNamespace

import pytest

from baec_app.application.account_state import AccountStateService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.errors import (
    ApplicationValidationError,
    ApprovalAlreadyUsed,
    ApprovalKindMismatch,
    ProposalNotAuthoritative,
    ReferenceMismatch,
    RequestNotCoherent,
)
from baec_app.application.requests import RequestKind
from baec_app.data.database import RepositoryConflictError, RepositoryNotFoundError, RepositoryVerificationError
from baec_app.domain.baec_rules import create_confirmed_baec_record
from baec_app.domain.enums import (
    AccountState,
    AuthorizationAction,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    TransitionRejectionKind,
    TransitionUnresolvedKind,
)
from baec_app.domain.models import (
    Account,
    DomainValidationError,
    EvaluationEvidence,
    EvidenceExcerpt,
    HumanAuthorization,
    NonEvaluationEvidence,
)
from baec_app.domain.state_machine import (
    transition_to_active_opportunity,
    transition_to_conditionally_dormant,
    transition_to_no_plausible_path,
)
from tests.application_builders import FixedClock, SequentialIds
from tests.builders import (
    AO,
    CD,
    HARBOR_QUOTE,
    NOW,
    NP,
    candidate,
    confirm_auth,
    confirmed_record,
    evaluation_evidence,
    excerpt,
    external_excerpt,
    judgment,
    non_evaluation_evidence,
)
from tests.persistence_builders import (  # noqa: F401
    connection,
    counts,
    dump,
    no_leaked_connections,
    put_in_state,
    repo,
    save_confirmed,
    save_judgment,
)

K = TransitionRejectionKind
AUTH_ONLY = (K.AUTHORIZATION_MISSING,)
REASON = "CEE produced no foreseeable condition."
FABRICATED = "We have started a formal RFP."


class Services:
    def __init__(self, repository):
        self.repository = repository
        self.clock = FixedClock()
        self.ids = SequentialIds()
        self.gate = HumanConfirmationGate(self.clock, self.ids)
        self.dormancy = DormancyJudgmentService(repository, self.gate, self.clock, self.ids)
        self.state = AccountStateService(repository, self.gate, self.clock, self.ids)
        self.session = self.gate.open_session("reviewer-1")

    def approve(self, request):
        return self.gate.approve(self.session, request.request_id, displayed_digest=request.digest)

    def registered(self):
        return dict(self.gate._requests)

    def open_dormant(self, judgment_id, baec_id="B-1", account_id="ACC-1", **kw):
        return self.state.open_move_to_conditionally_dormant_request(
            self.session, account_id, baec_id=baec_id, judgment_id=judgment_id, **kw
        )

    def open_active(self, evidence=None, account_id="ACC-1"):
        evidence = evaluation_evidence() if evidence is None else evidence
        return self.state.open_move_to_active_opportunity_request(self.session, account_id, evaluation_evidence=evidence)

    def open_no_path(self, account_id="ACC-1", ground=NoPlausiblePathGround.NO_PLAUSIBLE_BAEC, reason=REASON, **kw):
        return self.state.open_move_to_no_plausible_path_request(
            self.session, account_id, ground=ground, reason=reason, **kw
        )


@pytest.fixture
def services(repo):
    return Services(repo)


def confirmed_with_judgment(repo, baec_id="B-1", **answers):
    save_confirmed(repo, baec_id)
    return save_judgment(repo, baec_id, **answers)


def save_acc2_baec(repo, baec_id="B-9"):
    cited = excerpt(source_id="INT-9")
    cand = candidate(account_id="ACC-2", source_interaction_id="INT-9", source_excerpt=cited, criterion_evidence=cited)
    repo.save_confirmed_baec(create_confirmed_baec_record(cand, baec_id=baec_id, captured_at=NOW, confirmation=confirm_auth(baec_id)))


def history(repo, account_id="ACC-1"):
    return repo.get_transition_history(account_id)


def assert_refused_without_writing(services, connection, call, error=RequestNotCoherent):
    before = dump(connection)
    with pytest.raises(error) as raised:
        call()
    assert services.registered() == {}
    assert dump(connection) == before
    return raised.value


# --- command signatures ------------------------------------------------------------

COMMANDS = ("move_to_conditionally_dormant", "move_to_active_opportunity", "move_to_no_plausible_path")


@pytest.mark.parametrize("name", COMMANDS)
def test_commands_take_only_an_approval(name):
    assert list(inspect.signature(getattr(AccountStateService, name)).parameters) == ["self", "approval"]


@pytest.mark.parametrize("name", COMMANDS)
@pytest.mark.parametrize("value", [True, {"approved": True}, None], ids=["True", "mapping", "None"])
def test_commands_refuse_anything_but_an_approval(services, connection, name, value):
    before = dump(connection)
    with pytest.raises(ProposalNotAuthoritative):
        getattr(services.state, name)(value)
    assert dump(connection) == before


# --- Conditionally Dormant ------------------------------------------------------------

ANSWERS = list(product(ReviewAnswer, repeat=2))


@pytest.mark.parametrize("plausibility,addressability", ANSWERS, ids=[f"{p.name}-{a.name}" for p, a in ANSWERS])
def test_dormant_request_registers_exactly_when_the_locked_preview_allows(services, repo, connection, plausibility, addressability):
    jid = confirmed_with_judgment(repo, plausibility=plausibility, addressability=addressability)
    oracle = transition_to_conditionally_dormant(
        repo.get_account("ACC-1"),
        baec_record=repo.get_baec_record("B-1"),
        judgment=repo.list_dormancy_judgments("B-1")[0].judgment,
        authorization=None,
    )
    if oracle.rejections == AUTH_ONLY:
        before = dump(connection)
        request = services.open_dormant(jid)
        assert request.preview == oracle and dump(connection) == before
    else:
        refused = assert_refused_without_writing(services, connection, lambda: services.open_dormant(jid))
        assert refused.result == oracle


def test_only_plausible_yes_with_addressability_yes_or_unknown_is_registrable():
    """Documents what the locked oracle above decides; the application decides none of it."""
    registrable = set()
    for plausibility, addressability in ANSWERS:
        result = transition_to_conditionally_dormant(
            Account("ACC-1", "x"),
            baec_record=confirmed_record(),
            judgment=judgment(plausibility, addressability),
            authorization=None,
        )
        if result.rejections == AUTH_ONLY:
            registrable.add((plausibility, addressability))
    assert registrable == {(ReviewAnswer.YES, ReviewAnswer.YES), (ReviewAnswer.YES, ReviewAnswer.UNKNOWN)}


@pytest.mark.parametrize("addressability", [ReviewAnswer.YES, ReviewAnswer.UNKNOWN])
def test_dormant_transition_executes_and_writes_exactly_one_history_row(services, repo, connection, addressability):
    jid = confirmed_with_judgment(repo, addressability=addressability)
    request = services.open_dormant(jid)
    services.clock.advance(minutes=2)
    approval = services.approve(request)
    services.clock.advance(minutes=2)
    before = counts(connection)
    result = services.state.move_to_conditionally_dormant(approval)
    after = counts(connection)
    assert result.allowed and result.to_state is CD
    expected_unresolved = (TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,) if addressability is ReviewAnswer.UNKNOWN else ()
    assert result.unresolved == expected_unresolved
    assert after["account_state_transitions"] - before["account_state_transitions"] == 1
    entry = history(repo)[-1]
    assert (entry.baec_id, entry.judgment_id, entry.unresolved) == ("B-1", jid, expected_unresolved)
    assert entry.authorization == HumanAuthorization(
        "reviewer-1", approval.approved_at, AuthorizationAction.CHANGE_ACCOUNT_STATE, "ACC-1", CD
    )
    assert entry.recorded_at == services.clock.now()
    assert repo.get_account("ACC-1").state is CD


def test_the_command_returns_the_repository_transition_result_unchanged(services, repo, monkeypatch):
    jid = confirmed_with_judgment(repo)
    approval = services.approve(services.open_dormant(jid))
    returned = []
    real = repo.persist_transition_to_conditionally_dormant

    def spy(*args, **kwargs):
        returned.append(real(*args, **kwargs))
        return returned[-1]

    monkeypatch.setattr(repo, "persist_transition_to_conditionally_dormant", spy)
    assert services.state.move_to_conditionally_dormant(approval) is returned[0]


def test_baec_not_current_is_refused_by_the_locked_preview(services, repo, connection):
    jid = confirmed_with_judgment(repo)
    connection.execute("UPDATE baec_records SET staleness_status = 'STALE'")
    refused = assert_refused_without_writing(services, connection, lambda: services.open_dormant(jid))
    assert refused.result.rejections == (K.AUTHORIZATION_MISSING, K.BAEC_NOT_CURRENT)


def test_a_baec_of_another_account_is_rejected_by_the_locked_preview(services, repo, connection):
    save_acc2_baec(repo)
    jid = save_judgment(repo, "B-9")
    refused = assert_refused_without_writing(services, connection, lambda: services.open_dormant(jid, baec_id="B-9"))
    assert K.BAEC_WRONG_ACCOUNT in refused.result.rejections


def test_a_judgment_of_another_baec_is_a_reference_mismatch(services, repo, connection):
    confirmed_with_judgment(repo, "B-1")
    other = confirmed_with_judgment(repo, "B-2")
    refused = assert_refused_without_writing(
        services, connection, lambda: services.open_dormant(other, baec_id="B-1"), ReferenceMismatch
    )
    assert str(refused) == f"judgment {other} is not a recorded judgment of BAEC B-1"


@pytest.mark.parametrize("judgment_id", [999, 0, -1, True, "1"], ids=["999", "0", "-1", "True", "text"])
def test_a_nonexistent_judgment_is_the_same_reference_mismatch(services, repo, connection, judgment_id):
    confirmed_with_judgment(repo)
    refused = assert_refused_without_writing(
        services, connection, lambda: services.open_dormant(judgment_id), ReferenceMismatch
    )
    assert str(refused) == f"judgment {judgment_id} is not a recorded judgment of BAEC B-1"


def test_a_missing_baec_propagates_the_repository_error(services, repo, connection):
    assert_refused_without_writing(services, connection, lambda: services.open_dormant(1, baec_id="B-404"), RepositoryNotFoundError)


def test_cross_account_non_evaluation_evidence_is_a_reference_mismatch(services, repo, connection):
    jid = confirmed_with_judgment(repo)
    foreign = NonEvaluationEvidence("ACC-1", excerpt(HARBOR_QUOTE, source_id="INT-9"), NOW)  # INT-9 is ACC-2's
    assert_refused_without_writing(
        services, connection, lambda: services.open_dormant(jid, non_evaluation_evidence=foreign), ReferenceMismatch
    )


def test_same_state_is_refused_by_the_locked_preview(services, repo, connection):
    jid = confirmed_with_judgment(repo)
    put_in_state(repo, CD)
    refused = assert_refused_without_writing(services, connection, lambda: services.open_dormant(jid))
    assert refused.result.rejections == (K.SAME_STATE,)


def test_a_judgment_approval_cannot_drive_the_transition_and_is_not_consumed(services, repo, connection):
    save_confirmed(repo)
    judgment_request = services.dormancy.open_request(
        services.session, "B-1", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES
    )
    approval = services.approve(judgment_request)
    before = dump(connection)
    with pytest.raises(ApprovalKindMismatch):
        services.state.move_to_conditionally_dormant(approval)
    assert dump(connection) == before
    assert services.dormancy.record(approval).judgment.baec_id == "B-1"


@pytest.mark.parametrize("wrong", ["move_to_active_opportunity", "move_to_no_plausible_path"])
def test_a_dormant_approval_cannot_drive_another_destination_and_is_not_consumed(services, repo, connection, wrong):
    approval = services.approve(services.open_dormant(confirmed_with_judgment(repo)))
    before = dump(connection)
    with pytest.raises(ApprovalKindMismatch):
        getattr(services.state, wrong)(approval)
    assert dump(connection) == before
    assert services.state.move_to_conditionally_dormant(approval).to_state is CD


def test_a_repository_failure_after_redemption_consumes_the_approval(services, repo, connection, monkeypatch):
    approval = services.approve(services.open_dormant(confirmed_with_judgment(repo)))
    failure = RepositoryConflictError("synthetic conflict")
    monkeypatch.setattr(repo, "persist_transition_to_conditionally_dormant", lambda *a, **k: (_ for _ in ()).throw(failure))
    before = dump(connection)
    with pytest.raises(RepositoryConflictError) as raised:
        services.state.move_to_conditionally_dormant(approval)
    assert raised.value is failure and dump(connection) == before
    monkeypatch.undo()
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_conditionally_dormant(approval)


def test_a_request_that_became_stale_fails_closed_at_execution(services, repo, connection):
    approval = services.approve(services.open_dormant(confirmed_with_judgment(repo)))
    connection.execute("UPDATE baec_records SET staleness_status = 'REVIEW_DUE'")  # condition changes after opening
    before = dump(connection)
    result = services.state.move_to_conditionally_dormant(approval)
    assert not result.allowed and result.rejections == (K.BAEC_NOT_CURRENT,)
    assert dump(connection) == before and history(repo) == ()
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_conditionally_dormant(approval)


def test_a_dormant_request_for_one_account_never_moves_another(services, repo):
    approval = services.approve(services.open_dormant(confirmed_with_judgment(repo)))
    services.state.move_to_conditionally_dormant(approval)
    assert repo.get_account("ACC-1").state is CD
    assert repo.get_account("ACC-2").state is None and history(repo, "ACC-2") == ()


# --- Active Opportunity -----------------------------------------------------------------


def test_activation_with_valid_evaluation_evidence(services, repo, connection):
    request = services.open_active()
    approval = services.approve(request)
    before = counts(connection)
    result = services.state.move_to_active_opportunity(approval)
    assert result.allowed and result.to_state is AO
    assert counts(connection)["account_state_transitions"] - before["account_state_transitions"] == 1
    entry = history(repo)[-1]
    assert entry.evaluation_evidence == evaluation_evidence()
    assert entry.authorization.target_state is AO and entry.authorization.subject_id == "ACC-1"


def test_a_request_cannot_be_formed_without_evaluation_evidence(services, connection):
    refused = assert_refused_without_writing(
        services,
        connection,
        lambda: services.state.open_move_to_active_opportunity_request(services.session, "ACC-1", evaluation_evidence=None),
    )
    assert refused.result.rejections == (K.AUTHORIZATION_MISSING, K.EVALUATION_EVIDENCE_MISSING)


@pytest.mark.parametrize(
    "provenance", [ProvenanceCategory.EXTERNAL_EVIDENCE, ProvenanceCategory.AI_INFERENCE, ProvenanceCategory.UNKNOWN]
)
def test_external_ai_and_unknown_provenance_cannot_be_evaluation_evidence(provenance):
    with pytest.raises(DomainValidationError):
        EvaluationEvidence("ACC-1", EvidenceExcerpt("We have opened a formal supplier review.", provenance, "INT-2"), NOW)


def test_an_external_signal_excerpt_cannot_become_evaluation_evidence():
    with pytest.raises(DomainValidationError):
        EvaluationEvidence("ACC-1", external_excerpt(), NOW)


SIGNAL_LIKE = {
    "signal mapping": {"signal": "competitor price increase", "account_id": "ACC-1"},
    "signal text": "12% increase announced",
    "look-alike object": SimpleNamespace(
        account_id="ACC-1", evidence=excerpt("We have opened a formal supplier review.", source_id="INT-2"), observed_at=NOW
    ),
    "bare excerpt": excerpt("We have opened a formal supplier review.", source_id="INT-2"),
    "non-evaluation evidence": non_evaluation_evidence(),
    "an approval-shaped mapping": {"approved": True},
}


@pytest.mark.parametrize("value", SIGNAL_LIKE.values(), ids=SIGNAL_LIKE.keys())
def test_signal_like_or_arbitrary_objects_cannot_substitute_for_evaluation_evidence(services, connection, value):
    assert_refused_without_writing(
        services,
        connection,
        lambda: services.state.open_move_to_active_opportunity_request(services.session, "ACC-1", evaluation_evidence=value),
        DomainValidationError,
    )


def test_non_verbatim_evaluation_evidence_is_refused_by_evidence_fidelity_at_execution(services, repo, connection):
    fabricated = EvaluationEvidence("ACC-1", excerpt(FABRICATED, source_id="INT-2"), NOW)
    approval = services.approve(services.open_active(fabricated))  # content rules are enforced at execution
    before = dump(connection)
    with pytest.raises(RepositoryVerificationError, match="verbatim"):
        services.state.move_to_active_opportunity(approval)
    assert dump(connection) == before and repo.get_account("ACC-1").state is None
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_active_opportunity(approval)


def test_evaluation_evidence_from_another_accounts_interaction_is_a_reference_mismatch(services, connection):
    foreign = EvaluationEvidence("ACC-1", excerpt(HARBOR_QUOTE, source_id="INT-9"), NOW)
    assert_refused_without_writing(services, connection, lambda: services.open_active(foreign), ReferenceMismatch)


def test_evidence_naming_another_account_is_rejected_by_the_locked_preview(services, connection):
    mislabelled = EvaluationEvidence("ACC-2", excerpt("We have opened a formal supplier review.", source_id="INT-2"), NOW)
    refused = assert_refused_without_writing(services, connection, lambda: services.open_active(mislabelled))
    assert K.EVALUATION_EVIDENCE_WRONG_ACCOUNT in refused.result.rejections


def test_an_approval_alone_cannot_activate(services, repo, connection):
    jid = confirmed_with_judgment(repo)
    other_approval = services.approve(services.open_dormant(jid))
    before = dump(connection)
    with pytest.raises(ApprovalKindMismatch):
        services.state.move_to_active_opportunity(other_approval)
    assert dump(connection) == before and repo.get_account("ACC-1").state is None


def test_previews_write_nothing_and_register_nothing(services, repo, connection):
    jid = confirmed_with_judgment(repo)
    before = dump(connection)
    services.state.preview_move_to_active_opportunity("ACC-1", evaluation_evidence=evaluation_evidence())
    services.state.preview_move_to_active_opportunity("ACC-1", evaluation_evidence=None)
    services.state.preview_move_to_conditionally_dormant("ACC-1", baec_id="B-1", judgment_id=jid)
    services.state.preview_move_to_no_plausible_path("ACC-1", ground=None, reason=None)
    assert dump(connection) == before and services.registered() == {}


def test_previews_are_the_locked_transition_with_no_authorization(services, repo):
    account = repo.get_account("ACC-1")
    assert services.state.preview_move_to_active_opportunity("ACC-1", evaluation_evidence=evaluation_evidence()) == (
        transition_to_active_opportunity(account, evaluation_evidence=evaluation_evidence(), authorization=None)
    )
    assert services.state.preview_move_to_no_plausible_path("ACC-1", ground=None, reason="") == (
        transition_to_no_plausible_path(account, ground=None, reason="", authorization=None)
    )


def test_activation_request_becomes_stale_when_another_writer_activates_first(services, repo, connection):
    approval = services.approve(services.open_active())
    put_in_state(repo, AO)  # another writer
    before = dump(connection)
    result = services.state.move_to_active_opportunity(approval)
    assert not result.allowed and result.rejections == (K.SAME_STATE,)
    assert dump(connection) == before
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_active_opportunity(approval)


def test_a_concurrent_state_change_inside_execution_propagates_the_conflict(services, repo, connection, monkeypatch):
    """The repository's compare-and-set detects a state change it did not read; nothing partial is written."""
    approval = services.approve(services.open_active())
    put_in_state(repo, NP)  # another writer moves the account
    stale = Account("ACC-1", "Harbor Surgical Center", None)  # what this execution believes it read
    monkeypatch.setattr(repo, "get_account", lambda account_id: stale)
    before = dump(connection)
    with pytest.raises(RepositoryConflictError):
        services.state.move_to_active_opportunity(approval)
    monkeypatch.undo()
    assert dump(connection) == before and repo.get_account("ACC-1").state is NP
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_active_opportunity(approval)


def test_an_activation_request_for_one_account_never_moves_another(services, repo):
    services.state.move_to_active_opportunity(services.approve(services.open_active()))
    assert repo.get_account("ACC-1").state is AO
    assert repo.get_account("ACC-2").state is None and history(repo, "ACC-2") == ()


# --- No Plausible Path ---------------------------------------------------------------


def test_no_plausible_path_transition(services, repo, connection):
    request = services.open_no_path(basis_interaction_id="INT-1")
    before = counts(connection)
    result = services.state.move_to_no_plausible_path(services.approve(request))
    assert result.allowed and result.to_state is NP
    assert counts(connection)["account_state_transitions"] - before["account_state_transitions"] == 1
    entry = history(repo)[-1]
    assert (entry.ground, entry.reason, entry.basis_interaction_id) == (NoPlausiblePathGround.NO_PLAUSIBLE_BAEC, REASON, "INT-1")


@pytest.mark.parametrize("reason", ["", "   ", None], ids=["empty", "blank", "None"])
def test_a_missing_reason_is_decided_by_the_locked_state_machine(services, connection, reason):
    refused = assert_refused_without_writing(services, connection, lambda: services.open_no_path(reason=reason))
    assert refused.result.rejections == (K.AUTHORIZATION_MISSING, K.REASON_MISSING)


def test_a_missing_ground_is_decided_by_the_locked_state_machine(services, connection):
    refused = assert_refused_without_writing(services, connection, lambda: services.open_no_path(ground=None))
    assert refused.result.rejections == (K.AUTHORIZATION_MISSING, K.GROUND_MISSING)


def test_leaving_active_without_non_evaluation_evidence_is_refused(services, repo, connection):
    put_in_state(repo, AO)
    refused = assert_refused_without_writing(services, connection, lambda: services.open_no_path())
    assert refused.result.rejections == (K.AUTHORIZATION_MISSING, K.NON_EVALUATION_EVIDENCE_MISSING)


def test_leaving_active_with_non_evaluation_evidence_is_allowed(services, repo):
    put_in_state(repo, AO)
    request = services.open_no_path(non_evaluation_evidence=non_evaluation_evidence())
    result = services.state.move_to_no_plausible_path(services.approve(request))
    assert result.allowed and (result.from_state, result.to_state) == (AO, NP)
    assert history(repo)[-1].non_evaluation_evidence == non_evaluation_evidence()


def test_a_cross_account_basis_interaction_is_a_reference_mismatch(services, connection):
    assert_refused_without_writing(services, connection, lambda: services.open_no_path(basis_interaction_id="INT-9"), ReferenceMismatch)


def test_a_missing_basis_interaction_propagates_the_repository_error(services, connection):
    assert_refused_without_writing(
        services, connection, lambda: services.open_no_path(basis_interaction_id="INT-404"), RepositoryNotFoundError
    )


def test_cross_account_non_evaluation_evidence_for_no_plausible_path_is_a_reference_mismatch(services, repo, connection):
    put_in_state(repo, AO)
    foreign = NonEvaluationEvidence("ACC-1", excerpt(HARBOR_QUOTE, source_id="INT-9"), NOW)
    assert_refused_without_writing(
        services, connection, lambda: services.open_no_path(non_evaluation_evidence=foreign), ReferenceMismatch
    )


def test_no_plausible_path_same_state_is_refused(services, repo, connection):
    put_in_state(repo, NP)
    refused = assert_refused_without_writing(services, connection, lambda: services.open_no_path())
    assert refused.result.rejections == (K.SAME_STATE,)


def test_no_plausible_path_request_becomes_stale_when_the_account_is_activated_first(services, repo, connection):
    approval = services.approve(services.open_no_path())  # opened while unclassified: no evidence needed then
    put_in_state(repo, AO)  # another writer
    before = dump(connection)
    result = services.state.move_to_no_plausible_path(approval)
    assert not result.allowed and result.rejections == (K.NON_EVALUATION_EVIDENCE_MISSING,)
    assert dump(connection) == before
    with pytest.raises(ApprovalAlreadyUsed):
        services.state.move_to_no_plausible_path(approval)


def test_a_no_plausible_path_request_for_one_account_never_moves_another(services, repo):
    services.state.move_to_no_plausible_path(services.approve(services.open_no_path()))
    assert repo.get_account("ACC-1").state is NP
    assert repo.get_account("ACC-2").state is None and history(repo, "ACC-2") == ()


# --- shared request-opening behavior ------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "T-0001"], ids=["None", "id"])
def test_request_opening_requires_a_session_object(services, bad):
    with pytest.raises(ApplicationValidationError):
        services.state.open_move_to_active_opportunity_request(bad, "ACC-1", evaluation_evidence=evaluation_evidence())


def test_a_look_alike_session_cannot_open_a_transition_request(services, connection):
    from baec_app.application.context import Actor, InteractionSession
    from baec_app.application.errors import SessionNotRecognized

    twin = InteractionSession(services.session.session_id, Actor("reviewer-1"), services.session.opened_at)
    assert_refused_without_writing(
        services,
        connection,
        lambda: services.state.open_move_to_active_opportunity_request(twin, "ACC-1", evaluation_evidence=evaluation_evidence()),
        SessionNotRecognized,
    )


def test_a_missing_account_propagates_the_repository_error(services, connection):
    assert_refused_without_writing(services, connection, lambda: services.open_active(account_id="ACC-404"), RepositoryNotFoundError)


def test_registered_requests_carry_their_locked_preview_and_target(services, repo):
    request = services.open_active()
    assert request.kind is RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY
    assert request.target_state is AccountState.ACTIVE_OPPORTUNITY and request.subject_id == "ACC-1"
    assert request.preview.rejections == AUTH_ONLY and request.preview.to_state is AO


def test_activation_content_comes_from_the_redeemed_request_not_the_latest_call(services, repo):
    first = services.open_active()  # ACC-1, evidence from INT-2
    acc2_evidence = EvaluationEvidence("ACC-2", excerpt(HARBOR_QUOTE, source_id="INT-9"), NOW)
    services.open_active(acc2_evidence, account_id="ACC-2")  # a later request for another account
    result = services.state.move_to_active_opportunity(services.approve(first))
    assert result.account_id == "ACC-1"
    assert repo.get_account("ACC-1").state is AO and repo.get_account("ACC-2").state is None
    assert history(repo)[-1].evaluation_evidence == evaluation_evidence()


def test_no_plausible_path_content_comes_from_the_redeemed_request_not_the_latest_call(services, repo):
    first = services.open_no_path(reason="First reason.", basis_interaction_id="INT-1")
    services.open_no_path(reason="Second reason.", ground=NoPlausiblePathGround.OTHER)
    services.state.move_to_no_plausible_path(services.approve(first))
    entry = history(repo)[-1]
    assert (entry.reason, entry.ground, entry.basis_interaction_id) == (
        "First reason.",
        NoPlausiblePathGround.NO_PLAUSIBLE_BAEC,
        "INT-1",
    )


def test_dormant_content_comes_from_the_redeemed_request_not_the_latest_call(services, repo):
    first_judgment = confirmed_with_judgment(repo, "B-1", addressability=ReviewAnswer.YES)
    second_judgment = confirmed_with_judgment(repo, "B-2", addressability=ReviewAnswer.UNKNOWN)
    first = services.open_dormant(first_judgment, baec_id="B-1")
    services.open_dormant(second_judgment, baec_id="B-2")
    result = services.state.move_to_conditionally_dormant(services.approve(first))
    assert result.unresolved == ()
    assert (history(repo)[-1].baec_id, history(repo)[-1].judgment_id) == ("B-1", first_judgment)


TRANSITIONS = {
    "active": (
        "transition_to_active_opportunity",
        lambda s: s.state.open_move_to_active_opportunity_request(s.session, "ACC-1", evaluation_evidence=None),
    ),
    "no plausible path": ("transition_to_no_plausible_path", lambda s: s.open_no_path(reason="")),
    "dormant": ("transition_to_conditionally_dormant", None),
}


@pytest.mark.parametrize("label", TRANSITIONS, ids=TRANSITIONS)
def test_request_not_coherent_carries_the_exact_preview_object(services, repo, monkeypatch, label):
    """The locked preview object itself is the source of truth: not a copy, reconstruction, or normalization."""
    import baec_app.application.account_state as account_state_module

    function_name, open_request = TRANSITIONS[label]
    if open_request is None:
        jid = confirmed_with_judgment(repo, plausibility=ReviewAnswer.NO)

        def open_request(s):
            return s.open_dormant(jid)

    produced = []
    locked = getattr(account_state_module, function_name)

    def spy(*args, **kwargs):
        produced.append(locked(*args, **kwargs))
        return produced[-1]

    monkeypatch.setattr(account_state_module, function_name, spy)
    with pytest.raises(RequestNotCoherent) as raised:
        open_request(services)
    assert len(produced) == 1
    assert raised.value.result is produced[0]
    assert str(raised.value) == produced[0].rejection_text
