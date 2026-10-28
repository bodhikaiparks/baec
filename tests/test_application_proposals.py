"""Phase 4E: proposal DTOs, the proposal facade, request_from_proposal, and the read connection."""

import dataclasses
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from baec_app.application import composition
from baec_app.application.account_state import AccountStateService
from baec_app.application.approval import HumanApproval, HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.composition import build_command_facade, build_proposal_facade, open_read_connection
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.errors import (
    ApplicationValidationError,
    NotConfirmable,
    ProposalNotAuthoritative,
    ReadDatabaseUnavailable,
    ReadOnlyConnectionRequired,
    ReferenceMismatch,
    RequestNotCoherent,
)
from baec_app.application.facades import HumanCommandFacade, ProposalFacade
from baec_app.application.proposals import (
    PROPOSAL_TYPES,
    ConfirmationProposal,
    DormancyJudgmentProposal,
    MoveToActiveProposal,
    MoveToDormantProposal,
    MoveToNoPlausiblePathProposal,
    ProposalOrigin,
)
from baec_app.application.requests import RequestKind
from baec_app.data.database import DatabaseVersionError, RepositoryNotFoundError, open_database
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    NoPlausiblePathGround,
    ReviewAnswer,
    TransitionRejectionKind,
)
from baec_app.domain.models import Account, BaecCandidate, EvaluationEvidence, HumanAuthorization
from tests.application_builders import FixedClock, SequentialIds, request_for
from tests.builders import HARBOR_QUOTE, NOW, candidate, evaluation_evidence, excerpt, non_evaluation_evidence
from tests.persistence_builders import INTERACTION_TEXTS, dump, no_leaked_connections, save_confirmed, save_judgment  # noqa: F401

DET = ProposalOrigin.DETERMINISTIC
HUMAN = ProposalOrigin.HUMAN_DRAFT
REASON = "CEE produced no foreseeable condition."


@pytest.fixture
def db(tmp_path):
    """A file database with ACC-1 (INT-1..3), ACC-2 (INT-9), confirmed B-1 and one YES/YES judgment."""
    path = str(tmp_path / "phase4.sqlite3")
    writer = open_database(path)
    repo = Repository(writer)
    repo.add_account(Account("ACC-1", "Harbor Surgical Center"))
    repo.add_account(Account("ACC-2", "Other Synthetic Account"))
    for interaction_id, text in INTERACTION_TEXTS.items():
        repo.add_interaction(SourceInteraction(interaction_id, "ACC-1", NOW, text))
    repo.add_interaction(SourceInteraction("INT-9", "ACC-2", NOW, "Buyer: " + HARBOR_QUOTE))
    save_confirmed(repo)
    judgment_id = save_judgment(repo)
    reader = open_read_connection(path)
    clock, ids = FixedClock(), SequentialIds()
    command = build_command_facade(repo, clock=clock, ids=ids)
    proposal = build_proposal_facade(reader)
    yield SimpleNamespace(
        path=path, writer=writer, repo=repo, reader=reader, command=command, proposal=proposal,
        clock=clock, judgment_id=judgment_id, session=command.gate.open_session("reviewer-1"),
    )
    reader.close()
    writer.close()


def approve(db, request):
    return db.command.gate.approve(db.session, request.request_id, displayed_digest=request.digest)


def human_proposals(judgment_id=1):
    return {
        ConfirmationProposal: ConfirmationProposal(candidate(), NOW, HUMAN),
        DormancyJudgmentProposal: DormancyJudgmentProposal("B-1", ReviewAnswer.YES, ReviewAnswer.UNKNOWN, "Note.", HUMAN),
        MoveToDormantProposal: MoveToDormantProposal("ACC-1", "B-1", judgment_id, None, HUMAN),
        MoveToActiveProposal: MoveToActiveProposal("ACC-1", evaluation_evidence(), HUMAN),
        MoveToNoPlausiblePathProposal: MoveToNoPlausiblePathProposal(
            "ACC-1", NoPlausiblePathGround.NO_PLAUSIBLE_BAEC, REASON, None, "INT-1", HUMAN
        ),
    }


# --- proposal DTOs ---------------------------------------------------------------------


def test_exactly_the_five_phase4_proposal_types_exist():
    assert {t.__name__ for t in PROPOSAL_TYPES} == {
        "ConfirmationProposal",
        "DormancyJudgmentProposal",
        "MoveToDormantProposal",
        "MoveToActiveProposal",
        "MoveToNoPlausiblePathProposal",
    }


@pytest.mark.parametrize("proposal", human_proposals().values(), ids=lambda p: type(p).__name__)
def test_each_proposal_accepts_its_exact_field_types(proposal):
    assert proposal.origin is HUMAN


AUTHORITY_FIELD_NAMES = {"authorization", "approval", "approved", "authorized_by", "authorized_at", "actor", "human_confirmed"}


@pytest.mark.parametrize("cls", PROPOSAL_TYPES, ids=lambda c: c.__name__)
def test_no_proposal_has_an_authority_field(cls):
    assert not {f.name for f in dataclasses.fields(cls)} & AUTHORITY_FIELD_NAMES


def _gate_approval():
    gate = HumanConfirmationGate(FixedClock(), SequentialIds())
    session = gate.open_session("reviewer-1")
    request = request_for(RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY, session_id=session.session_id)
    gate.register(session, request)
    return gate.approve(session, request.request_id, displayed_digest=request.digest)


SUBSTITUTES = {
    "HumanAuthorization": HumanAuthorization("reviewer-1", NOW, AuthorizationAction.CONFIRM_BAEC, "B-1"),
    "HumanApproval": _gate_approval(),
    "ApprovalRequest": request_for(RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY),
    "mapping approved": {"approved": True},
    "True": True,
}
SUBSTITUTION_CASES = [
    (cls, field.name, label)
    for cls, proposal in human_proposals().items()
    for field in dataclasses.fields(cls)
    for label in SUBSTITUTES
]


@pytest.mark.parametrize(
    "cls,field,label", SUBSTITUTION_CASES, ids=[f"{c.__name__}-{f}-{l}" for c, f, l in SUBSTITUTION_CASES]
)
def test_no_proposal_field_can_hold_an_authority_object(cls, field, label):
    with pytest.raises(ApplicationValidationError):
        dataclasses.replace(human_proposals()[cls], **{field: SUBSTITUTES[label]})


class _CandidateSubclass(BaecCandidate):
    pass


class _EvidenceSubclass(EvaluationEvidence):
    pass


class _Text(str):
    pass


LOOK_ALIKES = {
    "candidate subclass": (ConfirmationProposal, "candidate", lambda: _CandidateSubclass(**{f.name: getattr(candidate(), f.name) for f in dataclasses.fields(BaecCandidate)})),
    "evidence subclass": (MoveToActiveProposal, "evaluation_evidence", lambda: _EvidenceSubclass(**{f.name: getattr(evaluation_evidence(), f.name) for f in dataclasses.fields(EvaluationEvidence)})),
    "evidence look-alike": (MoveToActiveProposal, "evaluation_evidence", lambda: SimpleNamespace(account_id="ACC-1", evidence=excerpt(), observed_at=NOW)),
    "str subclass account": (MoveToActiveProposal, "account_id", lambda: _Text("ACC-1")),
    "bool judgment id": (MoveToDormantProposal, "judgment_id", lambda: True),
    "origin as text": (MoveToActiveProposal, "origin", lambda: "DETERMINISTIC"),
    "AI_MODEL as text": (MoveToActiveProposal, "origin", lambda: "AI_MODEL"),
    "ground as text": (MoveToNoPlausiblePathProposal, "ground", lambda: "NO_PLAUSIBLE_BAEC"),
    "naive captured_at": (ConfirmationProposal, "captured_at", lambda: NOW.replace(tzinfo=None)),
}


@pytest.mark.parametrize("case", LOOK_ALIKES.values(), ids=LOOK_ALIKES.keys())
def test_subclasses_and_look_alikes_are_refused(case):
    cls, field, make = case
    with pytest.raises(ApplicationValidationError):
        dataclasses.replace(human_proposals()[cls], **{field: make()})


def test_phase4_origins_are_exactly_two_and_ai_model_is_not_one_of_them():
    """Phase 7C added exactly AI_DRAFT (name kept for ID continuity); AI_MODEL stays absent."""
    assert [o.name for o in ProposalOrigin] == ["HUMAN_DRAFT", "DETERMINISTIC", "AI_DRAFT"]
    assert "AI_MODEL" not in ProposalOrigin.__members__
    with pytest.raises(ValueError):
        ProposalOrigin("AI_MODEL")
    with pytest.raises(ApplicationValidationError):
        request_for(RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY, origin="AI_MODEL")


def test_constructing_proposals_writes_nothing(db):
    before = dump(db.writer)
    human_proposals(db.judgment_id)
    assert dump(db.writer) == before


# --- the proposal facade -------------------------------------------------------------


def _deterministic_proposals(db):
    return [
        db.proposal.propose_confirmation(candidate(), captured_at=NOW),
        db.proposal.propose_move_to_conditionally_dormant("ACC-1", baec_id="B-1", judgment_id=db.judgment_id),
        db.proposal.propose_move_to_active_opportunity("ACC-1", evaluation_evidence=evaluation_evidence()),
        db.proposal.propose_move_to_no_plausible_path("ACC-1", ground=NoPlausiblePathGround.OTHER, reason=REASON),
    ]


def test_every_facade_proposal_is_deterministic_and_writes_nothing(db):
    before = dump(db.writer)
    proposals = _deterministic_proposals(db)
    assert {type(p) for p in proposals} == set(PROPOSAL_TYPES) - {DormancyJudgmentProposal}
    assert all(p.origin is DET for p in proposals)
    assert dump(db.writer) == before
    assert db.command.gate._requests == {}


def test_facade_proposals_respect_the_locked_checks(db):
    with pytest.raises(NotConfirmable) as raised:
        db.proposal.propose_confirmation(candidate(ArticulationOrigin.SELLER_SEEDED), captured_at=NOW)
    assert raised.value.result.reason_text == str(raised.value)
    with pytest.raises(RequestNotCoherent) as raised:
        db.proposal.propose_move_to_no_plausible_path("ACC-1", ground=None, reason="")
    assert raised.value.result.rejections == (
        TransitionRejectionKind.AUTHORIZATION_MISSING, TransitionRejectionKind.GROUND_MISSING, TransitionRejectionKind.REASON_MISSING,
    )
    with pytest.raises(ReferenceMismatch):
        db.proposal.propose_move_to_conditionally_dormant("ACC-1", baec_id="B-1", judgment_id=999)


def test_proposal_and_command_facades_preview_identically_and_previews_write_nothing(db):
    before = dump(db.writer)
    assert db.proposal.preview_classification(candidate()) == db.command.preview_classification(candidate())
    assert db.proposal.preview_move_to_active_opportunity("ACC-1", evaluation_evidence=None) == (
        db.command.preview_move_to_active_opportunity("ACC-1", evaluation_evidence=None)
    )
    assert db.proposal.preview_move_to_conditionally_dormant("ACC-1", baec_id="B-1", judgment_id=db.judgment_id) == (
        db.command.preview_move_to_conditionally_dormant("ACC-1", baec_id="B-1", judgment_id=db.judgment_id)
    )
    assert db.proposal.preview_move_to_no_plausible_path("ACC-1", ground=None, reason=None) == (
        db.command.preview_move_to_no_plausible_path("ACC-1", ground=None, reason=None)
    )
    assert dump(db.writer) == before


READS = [
    ("get_account", ("ACC-1",)),
    ("list_accounts", ()),
    ("get_interaction", ("INT-1",)),
    ("list_interactions", ("ACC-1",)),
    ("get_baec_record", ("B-1",)),
    ("list_baec_records", ("ACC-1",)),
    ("list_dormancy_judgments", ("B-1",)),
    ("get_transition_history", ("ACC-1",)),
]


@pytest.mark.parametrize("name,args", READS, ids=[r[0] for r in READS])
def test_reads_match_the_repository_and_write_nothing(db, name, args):
    before = dump(db.writer)
    assert getattr(db.proposal.reads, name)(*args) == getattr(db.repo, name)(*args)
    assert getattr(db.command.reads, name)(*args) == getattr(db.repo, name)(*args)
    assert dump(db.writer) == before


# --- proposal-facade isolation -------------------------------------------------------------

FORBIDDEN_REACHABLE = (HumanConfirmationGate, ClassificationService, DormancyJudgmentService, AccountStateService, HumanCommandFacade)


def _reachable(root):
    seen, stack, found = set(), [root], []
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        found.append(obj)
        if isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        elif isinstance(obj, dict):
            stack.extend(obj.values())
        elif hasattr(obj, "__dict__") and not isinstance(obj, type):
            stack.extend(vars(obj).values())
    return found


def test_the_proposal_facade_reaches_no_gate_command_service_or_writable_connection(db):
    reachable = _reachable(db.proposal)
    assert not [o for o in reachable if isinstance(o, FORBIDDEN_REACHABLE)]
    repositories = [o for o in reachable if type(o) is Repository]
    connections = [o for o in reachable if type(o) is sqlite3.Connection]
    assert repositories and connections
    assert all(c.execute("PRAGMA query_only").fetchone()[0] == 1 for c in connections)
    assert all(c is db.reader for c in connections)


def test_the_walk_would_find_a_gate_if_one_were_reachable(db):
    """Positive control for the isolation walk."""
    assert any(isinstance(o, HumanConfirmationGate) for o in _reachable(db.command))


def test_a_write_through_the_proposal_side_is_rejected_by_sqlite(db):
    before = dump(db.writer)
    repository = next(o for o in _reachable(db.proposal) if type(o) is Repository)
    with pytest.raises(sqlite3.OperationalError, match="readonly|query_only"):
        repository.add_account(Account("ACC-X", "Synthetic"))
    with pytest.raises(sqlite3.OperationalError):
        db.reader.execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-Y', 'Synthetic')")
    assert dump(db.writer) == before


# --- request_from_proposal ----------------------------------------------------------------


def _kind_of(proposal):
    return {
        ConfirmationProposal: RequestKind.CONFIRM_BAEC,
        DormancyJudgmentProposal: RequestKind.RECORD_DORMANCY_JUDGMENT,
        MoveToDormantProposal: RequestKind.MOVE_TO_CONDITIONALLY_DORMANT,
        MoveToActiveProposal: RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY,
        MoveToNoPlausiblePathProposal: RequestKind.MOVE_TO_NO_PLAUSIBLE_PATH,
    }[type(proposal)]


@pytest.mark.parametrize("origin", [HUMAN, DET], ids=["human draft", "deterministic"])
@pytest.mark.parametrize("cls", PROPOSAL_TYPES, ids=lambda c: c.__name__)
def test_request_from_proposal_preserves_origin_and_writes_nothing(db, cls, origin):
    proposal = dataclasses.replace(human_proposals(db.judgment_id)[cls], origin=origin)
    before = dump(db.writer)
    request = db.command.request_from_proposal(db.session, proposal)
    assert request.origin is origin and request.kind is _kind_of(proposal)
    assert list(db.command.gate._requests) == [request.request_id]
    assert dump(db.writer) == before


def test_the_origin_changes_the_request_digest(db):
    human = db.command.request_from_proposal(db.session, human_proposals()[MoveToActiveProposal])
    deterministic = db.command.request_from_proposal(
        db.session, db.proposal.propose_move_to_active_opportunity("ACC-1", evaluation_evidence=evaluation_evidence())
    )
    assert human.payload == deterministic.payload and human.digest != deterministic.digest


def test_a_proposal_reaches_an_authoritative_write_only_through_explicit_approval(db):
    proposal = db.proposal.propose_move_to_active_opportunity("ACC-1", evaluation_evidence=evaluation_evidence())
    for command in (db.command.move_to_active_opportunity, db.command.confirm_baec):
        with pytest.raises(ProposalNotAuthoritative):
            command(proposal)
    request = db.command.request_from_proposal(db.session, proposal)
    with pytest.raises(ProposalNotAuthoritative):
        db.command.move_to_active_opportunity(request)
    assert db.repo.get_account("ACC-1").state is None  # nothing has happened yet
    result = db.command.move_to_active_opportunity(approve(db, request))
    assert result.allowed and db.repo.get_account("ACC-1").state is AccountState.ACTIVE_OPPORTUNITY


class _ProposalSubclass(MoveToActiveProposal):
    pass


NOT_PROPOSALS = {
    "mapping": {"kind": "MOVE_TO_ACTIVE_OPPORTUNITY", "account_id": "ACC-1", "approved": True},
    "True": True,
    "object": object(),
    "HumanAuthorization": HumanAuthorization("reviewer-1", NOW, AuthorizationAction.CONFIRM_BAEC, "B-1"),
    "ApprovalRequest": request_for(RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY),
    "proposal subclass": _ProposalSubclass("ACC-1", evaluation_evidence(), HUMAN),
    "look-alike": SimpleNamespace(account_id="ACC-1", evaluation_evidence=evaluation_evidence(), origin=HUMAN),
}


@pytest.mark.parametrize("value", NOT_PROPOSALS.values(), ids=NOT_PROPOSALS.keys())
def test_only_exact_proposals_can_enter_the_request_path(db, value):
    before = dump(db.writer)
    with pytest.raises(ProposalNotAuthoritative):
        db.command.request_from_proposal(db.session, value)
    assert db.command.gate._requests == {} and dump(db.writer) == before


def test_a_human_approval_cannot_enter_the_request_path(db):
    with pytest.raises(ProposalNotAuthoritative):
        db.command.request_from_proposal(db.session, SUBSTITUTES["HumanApproval"])


REQUEST_TIME_FAILURES = {
    "missing account": (lambda j: MoveToActiveProposal("ACC-404", evaluation_evidence(), HUMAN), RepositoryNotFoundError),
    "cross-account evidence": (
        lambda j: MoveToActiveProposal("ACC-1", EvaluationEvidence("ACC-1", excerpt(HARBOR_QUOTE, source_id="INT-9"), NOW), HUMAN),
        ReferenceMismatch,
    ),
    "judgment not on BAEC": (lambda j: MoveToDormantProposal("ACC-1", "B-1", 999, None, HUMAN), ReferenceMismatch),
    "incoherent reason": (
        lambda j: MoveToNoPlausiblePathProposal("ACC-1", NoPlausiblePathGround.OTHER, "", None, None, HUMAN),
        RequestNotCoherent,
    ),
    "non-confirmable candidate": (lambda j: ConfirmationProposal(candidate(ArticulationOrigin.SELLER_SEEDED), NOW, HUMAN), NotConfirmable),
    "missing BAEC": (lambda j: DormancyJudgmentProposal("B-404", ReviewAnswer.YES, ReviewAnswer.YES, None, HUMAN), RepositoryNotFoundError),
}


@pytest.mark.parametrize("case", REQUEST_TIME_FAILURES.values(), ids=REQUEST_TIME_FAILURES.keys())
def test_request_from_proposal_runs_the_normal_request_time_validation(db, case):
    make, error = case
    before = dump(db.writer)
    with pytest.raises(error):
        db.command.request_from_proposal(db.session, make(db.judgment_id))
    assert db.command.gate._requests == {} and dump(db.writer) == before


# --- open_read_connection ------------------------------------------------------------------


def _tracked_connections(monkeypatch):
    opened = []
    real = composition.sqlite3.connect

    def tracking(*args, **kwargs):
        connection = real(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(composition.sqlite3, "connect", tracking)
    return opened


def _closed(connection):
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_a_valid_database_opens_query_only_and_refuses_writes(db):
    connection = open_read_connection(db.path)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO accounts (account_id, name) VALUES ('X', 'y')")
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["", "   ", ":memory:", None, 7], ids=["empty", "blank", "memory", "None", "int"])
def test_unusable_path_values_are_refused(path):
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(path)


def test_a_caller_supplied_file_uri_is_refused(db):
    for uri in (Path(db.path).as_uri(), Path(db.path).as_uri() + "?mode=rw", "FILE:" + db.path):
        with pytest.raises(ReadDatabaseUnavailable):
            open_read_connection(uri)


def test_a_nonexistent_path_is_refused_and_not_created(tmp_path, monkeypatch):
    opened = _tracked_connections(monkeypatch)
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(str(missing))
    assert not missing.exists() and opened == []


def test_a_directory_is_refused(tmp_path):
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(str(tmp_path))


def test_a_file_that_disappears_after_the_check_is_refused_without_creating_it(tmp_path, monkeypatch):
    missing = tmp_path / "vanished.sqlite3"
    monkeypatch.setattr(composition.Path, "is_file", lambda self: True)  # pretend it existed at the check
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(str(missing))
    assert not missing.exists()


def test_a_non_database_file_is_refused_and_its_connection_closed(tmp_path, monkeypatch):
    text = tmp_path / "notes.txt"
    text.write_text("This is not a database. " * 50, encoding="utf-8")
    opened = _tracked_connections(monkeypatch)
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(str(text))
    assert len(opened) == 1 and _closed(opened[0])
    assert text.read_text(encoding="utf-8").startswith("This is not a database.")


def test_a_version_3_database_keeps_its_version_error_and_closes(tmp_path, monkeypatch):
    path = tmp_path / "v3.sqlite3"
    old = open_database(str(path))
    old.execute("PRAGMA user_version = 3")
    old.close()
    opened = _tracked_connections(monkeypatch)
    with pytest.raises(DatabaseVersionError, match="rebuild the database from the seed files"):
        open_read_connection(str(path))
    assert len(opened) == 1 and _closed(opened[0])


def test_an_empty_file_is_refused_and_left_empty(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    with pytest.raises(DatabaseVersionError):
        open_read_connection(str(empty))
    assert empty.read_bytes() == b""  # never initialized as a side effect


# --- build_proposal_facade ------------------------------------------------------------------


def test_build_proposal_facade_requires_its_connection_argument():
    with pytest.raises(TypeError):
        build_proposal_facade()


def test_a_writable_connection_is_refused(db):
    with pytest.raises(ReadOnlyConnectionRequired):
        build_proposal_facade(db.writer)


@pytest.mark.parametrize("value", [None, "phase4.sqlite3"], ids=["None", "path string"])
def test_non_connections_are_refused(value):
    with pytest.raises(ReadOnlyConnectionRequired):
        build_proposal_facade(value)


def test_the_command_repository_is_refused_in_place_of_a_connection(db):
    with pytest.raises(ReadOnlyConnectionRequired):
        build_proposal_facade(db.repo)


def test_a_query_only_connection_with_the_wrong_schema_is_refused(tmp_path):
    path = tmp_path / "v3.sqlite3"
    old = open_database(str(path))
    old.execute("PRAGMA user_version = 3")
    old.close()
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA query_only = ON")
        with pytest.raises(DatabaseVersionError):
            build_proposal_facade(connection)
    finally:
        connection.close()


def test_a_query_only_current_schema_connection_is_accepted(db):
    assert type(build_proposal_facade(db.reader)) is ProposalFacade


def test_build_command_facade_requires_a_repository(db):
    with pytest.raises(ApplicationValidationError):
        build_command_facade(db.writer, clock=FixedClock(), ids=SequentialIds())


def test_the_command_facade_shares_one_gate_across_its_services(db):
    gates = {id(o) for o in _reachable(db.command) if isinstance(o, HumanConfirmationGate)}
    assert gates == {id(db.command.gate)}


# --- dormancy judgments stay human-originated (4E refinement) --------------------------


def test_the_proposal_facade_never_proposes_a_dormancy_judgment():
    names = {name for name in dir(ProposalFacade) if not name.startswith("_")}
    assert not [name for name in names if "judgment" in name or "dormancy_judgment" in name]
    assert {name for name in names if name.startswith("propose_")} == {
        "propose_confirmation",
        "propose_move_to_conditionally_dormant",
        "propose_move_to_active_opportunity",
        "propose_move_to_no_plausible_path",
    }


def test_a_human_draft_judgment_proposal_goes_through_request_approval_and_record(db):
    proposal = DormancyJudgmentProposal("B-1", ReviewAnswer.YES, ReviewAnswer.UNKNOWN, "Reviewer's own judgment.", HUMAN)
    before = dump(db.writer)
    request = db.command.request_from_proposal(db.session, proposal)
    assert request.origin is HUMAN and request.kind is RequestKind.RECORD_DORMANCY_JUDGMENT
    assert dump(db.writer) == before  # nothing recorded without approval
    persisted = db.command.record_dormancy_judgment(approve(db, request))
    judgment = persisted.judgment
    assert (judgment.plausibility, judgment.addressability, judgment.notes) == (
        ReviewAnswer.YES, ReviewAnswer.UNKNOWN, "Reviewer's own judgment."
    )
    assert judgment.authorization.action is AuthorizationAction.RECORD_DORMANCY_JUDGMENT


def test_human_judgment_is_separate_from_deterministic_transition_eligibility(db):
    """The judgment is a human action; whether it permits dormancy is the locked state machine's call."""
    proposal = DormancyJudgmentProposal("B-1", ReviewAnswer.NO, ReviewAnswer.YES, None, HUMAN)
    request = db.command.request_from_proposal(db.session, proposal)
    persisted = db.command.record_dormancy_judgment(approve(db, request))  # recorded exactly as the human said
    assert persisted.judgment.plausibility is ReviewAnswer.NO
    with pytest.raises(RequestNotCoherent) as raised:  # the deterministic side then refuses dormancy on it
        db.proposal.propose_move_to_conditionally_dormant("ACC-1", baec_id="B-1", judgment_id=persisted.judgment_id)
    assert TransitionRejectionKind.PLAUSIBILITY_NOT_YES in raised.value.result.rejections
    assert db.repo.get_account("ACC-1").state is None


# --- path objects --------------------------------------------------------------------


class _PathLike:
    def __init__(self, path):
        self._path = path

    def __fspath__(self):
        return self._path


@pytest.mark.parametrize("wrap", [Path, _PathLike], ids=["pathlib.Path", "os.PathLike"])
def test_path_objects_open_read_only(db, wrap):
    connection = open_read_connection(wrap(db.path))
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO accounts (account_id, name) VALUES ('X', 'y')")
    finally:
        connection.close()


def test_path_objects_keep_every_refusal(tmp_path, db):
    missing = tmp_path / "missing.sqlite3"
    for bad in (missing, tmp_path, Path(":memory:"), _PathLike(""), _PathLike("file:" + db.path), _PathLike(":memory:")):
        with pytest.raises(ReadDatabaseUnavailable):
            open_read_connection(bad)
    assert not missing.exists()
    text = tmp_path / "notes.txt"
    text.write_text("not a database " * 50, encoding="utf-8")
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(text)
    old = tmp_path / "v3.sqlite3"
    connection = open_database(str(old))
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    with pytest.raises(DatabaseVersionError):
        open_read_connection(old)


def test_bytes_paths_are_refused(db):
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(db.path.encode())
    with pytest.raises(ReadDatabaseUnavailable):
        open_read_connection(_PathLike(db.path.encode()))


def test_the_proposal_side_preview_service_reaches_only_the_read_only_connection(db):
    from baec_app.application.account_state import AccountStatePreviewService

    services = [o for o in _reachable(db.proposal) if type(o) is AccountStatePreviewService]
    assert len(services) == 1
    reachable = _reachable(services[0])
    assert not [o for o in reachable if isinstance(o, FORBIDDEN_REACHABLE)]
    connections = [o for o in reachable if type(o) is sqlite3.Connection]
    assert connections == [db.reader] and db.reader.execute("PRAGMA query_only").fetchone()[0] == 1
    assert set(vars(services[0])) == {"_repository"}


def test_the_command_side_uses_its_own_preview_service_over_the_writable_repository(db):
    from baec_app.application.account_state import AccountStatePreviewService

    previews = [o for o in _reachable(db.command) if type(o) is AccountStatePreviewService]
    assert len(previews) == 1 and vars(previews[0])["_repository"] is db.repo


def test_build_proposal_facade_takes_only_the_read_connection():
    import inspect

    assert list(inspect.signature(build_proposal_facade).parameters) == ["read_connection"]
