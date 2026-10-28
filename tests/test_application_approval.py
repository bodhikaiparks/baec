"""Phase 4B: the human confirmation gate (sessions, registration, approval, redemption)."""

import contextlib
import copy
import dataclasses
import pickle
import sys
import threading

import pytest

from baec_app.application import approval as approval_module
from baec_app.application.approval import HumanApproval, HumanConfirmationGate
from baec_app.application.context import Actor, InteractionSession
from baec_app.application.errors import (
    ApplicationError,
    ApplicationValidationError,
    ApprovalAlreadyUsed,
    ApprovalKindMismatch,
    ApprovalNotRecognized,
    DigestMismatch,
    HumanActionError,
    ProposalNotAuthoritative,
    RequestAlreadyApproved,
    RequestAlreadyRegistered,
    RequestNotRecognized,
    SessionNotRecognized,
)
from baec_app.application.requests import RequestKind
from baec_app.domain.enums import AuthorizationAction
from baec_app.domain.models import HumanAuthorization
from tests.application_builders import FixedClock, SequentialIds, active_payload, request_for
from tests.builders import NOW

K = RequestKind
ACTIVE = K.MOVE_TO_ACTIVE_OPPORTUNITY


class Flow:
    """One gate with one open session and one registered request."""

    def __init__(self, kind=ACTIVE):
        self.clock = FixedClock()
        self.gate = HumanConfirmationGate(self.clock, SequentialIds())
        self.session = self.gate.open_session("reviewer-1")
        self.kind = kind
        self.request = self.add_request("REQ-1", kind)

    def add_request(self, request_id, kind=ACTIVE, session=None, **overrides):
        session = session or self.session
        request = request_for(kind, request_id=request_id, session_id=session.session_id, **overrides)
        self.gate.register(session, request)
        return request

    def approve(self, request=None, session=None):
        request = request or self.request
        return self.gate.approve(session or self.session, request.request_id, displayed_digest=request.digest)


@pytest.fixture
def flow():
    return Flow()


def genuine_redeems(flow, approval):
    assert flow.gate.redeem(approval, flow.kind) is flow.request


# --- normal lifecycle -------------------------------------------------------------


def test_session_carries_the_actor_and_the_clock_time(flow):
    assert flow.session.actor == Actor("reviewer-1")
    assert flow.session.opened_at == NOW


def test_approval_records_actor_session_digest_and_clock_time(flow):
    flow.clock.advance(minutes=5)
    approval = flow.approve()
    assert type(approval) is HumanApproval
    assert approval.request_id == "REQ-1"
    assert approval.session_id == flow.session.session_id
    assert approval.actor_id == "reviewer-1"
    assert approval.digest == flow.request.digest
    assert approval.approved_at == flow.clock.now() != NOW


def test_redeem_returns_the_exact_stored_request(flow):
    genuine_redeems(flow, flow.approve())


def test_sentinel_is_not_shown_in_repr(flow):
    assert "_key" not in repr(flow.approve())


# --- one approval per request, single use ---------------------------------------------


def test_exactly_one_approval_per_request(flow):
    first = flow.approve()
    with pytest.raises(RequestAlreadyApproved):
        flow.approve()
    genuine_redeems(flow, first)
    with pytest.raises(RequestAlreadyApproved):
        flow.approve()


def test_an_approval_redeems_only_once(flow):
    approval = flow.approve()
    genuine_redeems(flow, approval)
    with pytest.raises(ApprovalAlreadyUsed):
        flow.gate.redeem(approval, ACTIVE)


# --- forged, copied, foreign, or non-approval objects ------------------------------------


def _stolen_sentinel_copy(approval):
    return HumanApproval(
        approval_id=approval.approval_id,
        request_id=approval.request_id,
        session_id=approval.session_id,
        actor_id=approval.actor_id,
        digest=approval.digest,
        approved_at=approval.approved_at,
        _key=approval_module._GATE_KEY,  # test code may reach the private sentinel; production code may not
    )


FORGERIES = {
    "copy.copy": copy.copy,
    "copy.deepcopy": copy.deepcopy,
    "pickle round trip": lambda a: pickle.loads(pickle.dumps(a)),
    "dataclasses.replace": lambda a: dataclasses.replace(a),
    "hand-built with the stolen sentinel": _stolen_sentinel_copy,
}


@pytest.mark.parametrize("forge", FORGERIES.values(), ids=FORGERIES.keys())
def test_forged_or_copied_approvals_are_refused_and_consume_nothing(flow, forge):
    approval = flow.approve()
    forged = forge(approval)
    assert forged is not approval
    with pytest.raises(ApprovalNotRecognized):
        flow.gate.redeem(forged, ACTIVE)
    genuine_redeems(flow, approval)


def test_an_approval_from_another_gate_is_refused(flow):
    other = Flow()  # same deterministic ids, so the foreign approval has the same approval_id
    foreign = other.approve()
    approval = flow.approve()
    assert foreign.approval_id == approval.approval_id and foreign.digest == approval.digest
    with pytest.raises(ApprovalNotRecognized):
        flow.gate.redeem(foreign, ACTIVE)
    genuine_redeems(flow, approval)


NOT_APPROVALS = {
    "True": True,
    "mapping approved true": {"approved": True},
    "mapping shaped like an approval": {"approval_id": "T-0002", "request_id": "REQ-1"},
    "HumanAuthorization": HumanAuthorization("reviewer-1", NOW, AuthorizationAction.CONFIRM_BAEC, "B-1"),
    "ApprovalRequest": request_for(ACTIVE),
    "None": None,
    "string": "approved",
}


@pytest.mark.parametrize("value", NOT_APPROVALS.values(), ids=NOT_APPROVALS.keys())
def test_non_approvals_are_not_authoritative_and_consume_nothing(flow, value):
    approval = flow.approve()
    with pytest.raises(ProposalNotAuthoritative):
        flow.gate.redeem(value, ACTIVE)
    genuine_redeems(flow, approval)


@pytest.mark.parametrize("key", [object(), None, "_GATE_KEY", True], ids=["object", "None", "string", "True"])
def test_an_approval_cannot_be_constructed_without_the_gate_sentinel(key):
    with pytest.raises(ApprovalNotRecognized):
        HumanApproval("A", "REQ-1", "S-1", "reviewer-1", "0" * 64, NOW, _key=key)


# --- sessions ----------------------------------------------------------------------


def _look_alike(session):
    return InteractionSession(session.session_id, Actor(session.actor.actor_id), session.opened_at)


def test_a_look_alike_session_is_refused_for_approval(flow):
    twin = _look_alike(flow.session)
    assert twin == flow.session and twin is not flow.session
    with pytest.raises(SessionNotRecognized):
        flow.gate.approve(twin, "REQ-1", displayed_digest=flow.request.digest)
    genuine_redeems(flow, flow.approve())


def test_a_look_alike_session_cannot_close_the_real_one(flow):
    with pytest.raises(SessionNotRecognized):
        flow.gate.close_session(_look_alike(flow.session))
    genuine_redeems(flow, flow.approve())  # the real session is still open


@pytest.mark.parametrize("value", [None, "T-0001", {"session_id": "T-0001"}], ids=["None", "id string", "mapping"])
def test_non_sessions_are_refused(flow, value):
    with pytest.raises(SessionNotRecognized):
        flow.gate.approve(value, "REQ-1", displayed_digest=flow.request.digest)
    with pytest.raises(SessionNotRecognized):
        flow.gate.close_session(value)


def test_a_request_cannot_be_approved_from_another_session(flow):
    other_session = flow.gate.open_session("reviewer-2")
    with pytest.raises(RequestNotRecognized):
        flow.approve(session=other_session)
    genuine_redeems(flow, flow.approve())


def test_closing_a_session_invalidates_open_and_approved_requests_and_their_approvals(flow):
    approved = flow.approve()
    still_open = flow.add_request("REQ-2")
    flow.gate.close_session(flow.session)
    with pytest.raises(SessionNotRecognized):
        flow.gate.redeem(approved, ACTIVE)
    with pytest.raises(SessionNotRecognized):
        flow.approve(still_open)
    with pytest.raises(SessionNotRecognized):
        flow.gate.close_session(flow.session)
    with pytest.raises(SessionNotRecognized):
        flow.add_request("REQ-3")
    # a new session for the same actor cannot use, re-approve, or register into the old one
    new_session = flow.gate.open_session("reviewer-1")
    assert new_session.session_id != flow.session.session_id
    with pytest.raises(RequestNotRecognized):
        flow.approve(still_open, session=new_session)
    with pytest.raises(SessionNotRecognized):
        flow.gate.redeem(approved, ACTIVE)


def test_closing_a_session_marks_its_open_and_approved_entries_invalidated_and_leaves_others(flow):
    """Second, independent layer behind the session check: the entries themselves are invalidated."""
    states = approval_module._RequestState
    redeemed_request = flow.request
    genuine_redeems(flow, flow.approve())
    approved_request = flow.add_request("REQ-2")
    flow.approve(approved_request)
    open_request = flow.add_request("REQ-3")
    other_session = flow.gate.open_session("reviewer-2")
    other_request = flow.add_request("REQ-4", session=other_session)
    flow.gate.close_session(flow.session)
    entries = flow.gate._requests
    assert entries[redeemed_request.request_id].state is states.REDEEMED
    assert entries[approved_request.request_id].state is states.INVALIDATED
    assert entries[open_request.request_id].state is states.INVALIDATED
    assert entries[other_request.request_id].state is states.OPEN


def test_open_session_validates_the_actor():
    gate = HumanConfirmationGate(FixedClock(), SequentialIds())
    for bad in ("", " ", None, 3):
        with pytest.raises(ApplicationValidationError):
            gate.open_session(bad)


def test_duplicate_identifiers_from_a_faulty_id_factory_are_refused():
    class SameId:
        def new_baec_id(self):
            return "BAEC-1"

        def new_token(self):
            return "T-1"

    gate = HumanConfirmationGate(FixedClock(), SameId())
    gate.open_session("reviewer-1")
    with pytest.raises(ApplicationValidationError):
        gate.open_session("reviewer-2")


# --- registration -----------------------------------------------------------------


def test_a_request_id_can_be_registered_only_once(flow):
    with pytest.raises(RequestAlreadyRegistered):
        flow.gate.register(flow.session, flow.request)
    different_content = request_for(ACTIVE, session_id=flow.session.session_id, payload=active_payload(account_id="ACC-2"))
    with pytest.raises(RequestAlreadyRegistered):
        flow.gate.register(flow.session, different_content)
    genuine_redeems(flow, flow.approve())  # the original registration is untouched


def _registered(flow):
    return dict(flow.gate._requests)


def test_a_look_alike_session_with_a_live_id_cannot_register(flow):
    twin = _look_alike(flow.session)
    assert (twin.session_id, twin.actor, twin.opened_at) == (flow.session.session_id, flow.session.actor, flow.session.opened_at)
    before = _registered(flow)
    request = request_for(ACTIVE, request_id="REQ-9", session_id=twin.session_id)
    with pytest.raises(SessionNotRecognized):
        flow.gate.register(twin, request)
    assert _registered(flow) == before
    flow.gate.register(flow.session, request)  # the genuine session still works
    assert flow.gate.approve(flow.session, "REQ-9", displayed_digest=request.digest).request_id == "REQ-9"


@pytest.mark.parametrize("value", [None, "T-0001", {"session_id": "T-0001"}], ids=["None", "id string", "mapping"])
def test_registration_requires_a_session_object(flow, value):
    before = _registered(flow)
    with pytest.raises(SessionNotRecognized):
        flow.gate.register(value, request_for(ACTIVE, request_id="REQ-9", session_id=flow.session.session_id))
    assert _registered(flow) == before


def test_registration_requires_an_open_session(flow):
    flow.gate.close_session(flow.session)
    with pytest.raises(SessionNotRecognized):
        flow.gate.register(flow.session, request_for(ACTIVE, request_id="REQ-9", session_id=flow.session.session_id))


def test_a_request_of_another_session_cannot_be_registered_in_this_one(flow):
    other = flow.gate.open_session("reviewer-2")
    before = _registered(flow)
    for session, owner in ((flow.session, other), (other, flow.session)):
        with pytest.raises(RequestNotRecognized):
            flow.gate.register(session, request_for(ACTIVE, request_id="REQ-9", session_id=owner.session_id))
    with pytest.raises(RequestNotRecognized):
        flow.gate.register(flow.session, request_for(ACTIVE, request_id="REQ-9", session_id="NO-SUCH-SESSION"))
    assert _registered(flow) == before


@pytest.mark.parametrize("value", [None, {"request_id": "REQ-9"}, active_payload()], ids=["None", "mapping", "payload"])
def test_registering_something_that_is_not_an_approval_request_is_a_validation_error(flow, value):
    """Wrong-type input is a validation failure; RequestNotRecognized is for request identity and lifecycle."""
    with pytest.raises(ApplicationValidationError) as raised:
        flow.gate.register(flow.session, value)
    assert not isinstance(raised.value, HumanActionError)


def test_registration_grants_nothing(flow):
    with pytest.raises(ProposalNotAuthoritative):
        flow.gate.redeem(flow.request, ACTIVE)


# --- approve preconditions ----------------------------------------------------------


@pytest.mark.parametrize("digest", ["0" * 64, "", None, "78A204E6"], ids=["other digest", "empty", "None", "prefix"])
def test_approve_requires_the_exact_displayed_digest(flow, digest):
    with pytest.raises(DigestMismatch):
        flow.gate.approve(flow.session, "REQ-1", displayed_digest=digest)
    genuine_redeems(flow, flow.approve())  # a refused approve leaves the request open


def test_approve_unknown_request_is_refused(flow):
    with pytest.raises(RequestNotRecognized):
        flow.gate.approve(flow.session, "REQ-404", displayed_digest=flow.request.digest)


# --- redemption: validate everything first, then consume ---------------------------------


def test_wrong_kind_is_refused_without_consuming(flow):
    approval = flow.approve()
    for wrong in [k for k in K if k is not ACTIVE] + ["MOVE_TO_ACTIVE_OPPORTUNITY", None]:
        with pytest.raises(ApprovalKindMismatch):
            flow.gate.redeem(approval, wrong)
    genuine_redeems(flow, approval)


def test_tampered_stored_request_content_is_refused_without_consuming(flow):
    approval = flow.approve()
    original = flow.request.payload
    object.__setattr__(flow.request, "payload", active_payload(account_id="ACC-2"))  # bypass frozen
    with pytest.raises(DigestMismatch):
        flow.gate.redeem(approval, ACTIVE)
    object.__setattr__(flow.request, "payload", original)
    genuine_redeems(flow, approval)


def test_tampered_approval_digest_is_refused_without_consuming(flow):
    approval = flow.approve()
    original = approval.digest
    object.__setattr__(approval, "digest", "0" * 64)
    with pytest.raises(DigestMismatch):
        flow.gate.redeem(approval, ACTIVE)
    object.__setattr__(approval, "digest", original)
    genuine_redeems(flow, approval)


def test_every_invalid_attempt_in_sequence_leaves_the_genuine_approval_redeemable(flow):
    approval = flow.approve()
    other = Flow()
    attempts = [
        (copy.copy(approval), ApprovalNotRecognized),
        (copy.deepcopy(approval), ApprovalNotRecognized),
        (pickle.loads(pickle.dumps(approval)), ApprovalNotRecognized),
        (_stolen_sentinel_copy(approval), ApprovalNotRecognized),
        (other.approve(), ApprovalNotRecognized),
        (True, ProposalNotAuthoritative),
        ({"approved": True}, ProposalNotAuthoritative),
        (HumanAuthorization("reviewer-1", NOW, AuthorizationAction.CONFIRM_BAEC, "B-1"), ProposalNotAuthoritative),
    ]
    for value, error in attempts:
        with pytest.raises(error):
            flow.gate.redeem(value, ACTIVE)
    for wrong_kind in (K.CONFIRM_BAEC, K.MOVE_TO_NO_PLAUSIBLE_PATH):
        with pytest.raises(ApprovalKindMismatch):
            flow.gate.redeem(approval, wrong_kind)
    genuine_redeems(flow, approval)


def test_error_hierarchy():
    for error in (
        SessionNotRecognized,
        RequestNotRecognized,
        RequestAlreadyRegistered,
        RequestAlreadyApproved,
        DigestMismatch,
        ApprovalNotRecognized,
        ApprovalAlreadyUsed,
        ApprovalKindMismatch,
    ):
        assert error.__bases__ == (HumanActionError,), error
    assert HumanActionError.__bases__ == (ApplicationError,)
    # An authority-boundary violation sits beside HumanActionError, not under it.
    assert ProposalNotAuthoritative.__bases__ == (ApplicationError,)
    assert not issubclass(ProposalNotAuthoritative, HumanActionError)


# --- concurrency --------------------------------------------------------------------
# No sleeps. A tiny thread switch interval forces frequent preemption so that an
# unlocked check-then-consume sequence would interleave.

THREADS = 16
ROUNDS = 25


@contextlib.contextmanager
def frequent_switching_context():
    """Shorten the interpreter switch interval, restoring the previous value in a finally block."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


@pytest.fixture
def frequent_switching():
    with frequent_switching_context():
        yield


def _race(function):
    """Run function in THREADS threads released together; every thread is joined before returning."""
    barrier = threading.Barrier(THREADS, timeout=30)  # safety timeout only; no timing assumption
    results, errors, unexpected = [], [], []

    def worker():
        try:
            barrier.wait()
            results.append(function())
        except HumanActionError as error:
            errors.append(error)
        except BaseException as error:  # reported below instead of being lost in the thread
            unexpected.append(error)

    threads = []
    try:
        for _ in range(THREADS):
            thread = threading.Thread(target=worker)
            threads.append(thread)
            thread.start()
    finally:
        if len(threads) < THREADS:
            barrier.abort()  # release any thread already waiting
        for thread in threads:
            thread.join()
    assert unexpected == []
    return results, errors


def test_switch_interval_is_restored_even_when_the_body_raises():
    before = sys.getswitchinterval()
    with pytest.raises(RuntimeError):
        with frequent_switching_context():
            assert sys.getswitchinterval() == pytest.approx(1e-6)
            raise RuntimeError("test failure inside the block")
    assert sys.getswitchinterval() == before


def test_race_joins_every_thread_even_when_the_function_fails():
    started = threading.active_count()
    with pytest.raises(AssertionError):
        _race(lambda: 1 / 0)  # ZeroDivisionError in every thread is collected as unexpected
    assert threading.active_count() == started


def test_concurrent_redemption_succeeds_exactly_once(frequent_switching):
    for _ in range(ROUNDS):
        flow = Flow()
        approval = flow.approve()
        results, errors = _race(lambda: flow.gate.redeem(approval, ACTIVE))
        assert len(results) == 1 and results[0] is flow.request
        assert len(errors) == THREADS - 1 and all(type(e) is ApprovalAlreadyUsed for e in errors)


def test_concurrent_approval_issues_exactly_one_approval(frequent_switching):
    for _ in range(ROUNDS):
        flow = Flow()
        results, errors = _race(lambda: flow.approve())
        assert len(results) == 1
        assert len(errors) == THREADS - 1 and all(type(e) is RequestAlreadyApproved for e in errors)
        genuine_redeems(flow, results[0])
