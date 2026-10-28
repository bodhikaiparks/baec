"""The human confirmation gate.

A request grants nothing. Only the trusted human-interaction adapter calls
approve(), and only in response to an explicit human action; the gate then
issues exactly one HumanApproval for that request. A command redeems the
approval once, and receives the stored request content, never content
supplied by the caller.

_GATE_KEY is private to this module. HumanApproval refuses any other key, so
only HumanConfirmationGate constructs approvals. This is accident resistance,
not a security boundary: code running in this process can still reach
private names.

All gate state is in memory and guarded by one lock. Every check-and-mutate
sequence runs under that lock, so redemption validates and consumes
atomically.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from baec_app.application.canonical import digest_request
from baec_app.application.context import Actor, Clock, IdFactory, InteractionSession
from baec_app.application.errors import (
    ApplicationValidationError,
    ApprovalAlreadyUsed,
    ApprovalKindMismatch,
    ApprovalNotRecognized,
    DigestMismatch,
    ProposalNotAuthoritative,
    RequestAlreadyApproved,
    RequestAlreadyRegistered,
    RequestNotRecognized,
    SessionNotRecognized,
)
from baec_app.application.requests import ApprovalRequest, RequestKind

_GATE_KEY = object()


@dataclass(frozen=True)
class HumanApproval:
    """Evidence, inside this process, that the trusted adapter approved one request."""

    approval_id: str
    request_id: str
    session_id: str
    actor_id: str
    digest: str
    approved_at: datetime
    _key: object = field(repr=False)

    def __post_init__(self) -> None:
        if self._key is not _GATE_KEY:
            raise ApprovalNotRecognized("a HumanApproval can be issued only by HumanConfirmationGate")


class _RequestState(Enum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REDEEMED = "REDEEMED"
    INVALIDATED = "INVALIDATED"


@dataclass
class _Entry:
    request: ApprovalRequest
    state: _RequestState = _RequestState.OPEN
    approval_id: str | None = None


class HumanConfirmationGate:
    """Sessions, registered requests, and single-use approvals, all in memory."""

    def __init__(self, clock: Clock, ids: IdFactory) -> None:
        self._clock = clock
        self._ids = ids
        self._lock = threading.Lock()
        self._sessions: dict[str, InteractionSession] = {}  # open sessions: the exact issued objects
        self._closed_sessions: set[str] = set()
        self._requests: dict[str, _Entry] = {}
        self._issued: dict[str, HumanApproval] = {}  # the exact approval objects issued
        self._consumed: set[str] = set()  # approval ids already redeemed

    # --- sessions ---------------------------------------------------------------

    def open_session(self, actor_id: str) -> InteractionSession:
        actor = Actor(actor_id)
        with self._lock:
            session_id = self._ids.new_token()
            if session_id in self._sessions or session_id in self._closed_sessions:
                raise ApplicationValidationError(f"session id {session_id!r} was already issued")
            session = InteractionSession(session_id, actor, self._clock.now())
            self._sessions[session_id] = session
            return session

    def _require_open_session(self, session: object) -> InteractionSession:
        """Caller holds the lock. Only the exact issued, still-open object is accepted."""
        if type(session) is not InteractionSession or self._sessions.get(session.session_id) is not session:
            raise SessionNotRecognized("the session is not an open session issued by this gate")
        return session

    def close_session(self, session: InteractionSession) -> None:
        with self._lock:
            session = self._require_open_session(session)
            del self._sessions[session.session_id]
            self._closed_sessions.add(session.session_id)
            for entry in self._requests.values():
                if entry.request.session_id == session.session_id and entry.state in (
                    _RequestState.OPEN,
                    _RequestState.APPROVED,
                ):
                    entry.state = _RequestState.INVALIDATED

    # --- requests and approvals -------------------------------------------------

    def register(self, session: InteractionSession, request: ApprovalRequest) -> None:
        """Record a request for display and approval in the exact open session. Grants nothing."""
        with self._lock:
            session = self._require_open_session(session)
            if type(request) is not ApprovalRequest:
                raise ApplicationValidationError("only an ApprovalRequest can be registered")
            if request.session_id != session.session_id:
                raise RequestNotRecognized("the request belongs to a different session")
            if request.request_id in self._requests:
                raise RequestAlreadyRegistered(f"request {request.request_id!r} is already registered")
            self._requests[request.request_id] = _Entry(request)

    def approve(self, session: InteractionSession, request_id: str, *, displayed_digest: str) -> HumanApproval:
        """Issue the one approval for a request. Trusted human-interaction adapter only."""
        with self._lock:
            session = self._require_open_session(session)
            entry = self._requests.get(request_id) if type(request_id) is str else None
            if entry is None or entry.request.session_id != session.session_id:
                raise RequestNotRecognized("the request is not registered in this session")
            if entry.state in (_RequestState.APPROVED, _RequestState.REDEEMED):
                raise RequestAlreadyApproved(f"request {request_id!r} already has its approval")
            if entry.state is not _RequestState.OPEN:
                raise RequestNotRecognized(f"request {request_id!r} is no longer open")
            if type(displayed_digest) is not str or displayed_digest != entry.request.digest:
                raise DigestMismatch("the displayed digest does not match the registered request")
            approval_id = self._ids.new_token()
            if approval_id in self._issued:
                raise ApplicationValidationError(f"approval id {approval_id!r} was already issued")
            approval = HumanApproval(
                approval_id=approval_id,
                request_id=request_id,
                session_id=session.session_id,
                actor_id=session.actor.actor_id,
                digest=entry.request.digest,
                approved_at=self._clock.now(),
                _key=_GATE_KEY,
            )
            self._issued[approval_id] = approval
            entry.state = _RequestState.APPROVED
            entry.approval_id = approval_id
            return approval

    def redeem(self, approval: HumanApproval, expected: RequestKind) -> ApprovalRequest:
        """Validate everything, then consume, then return the stored request.

        Any failed check raises before anything is consumed.
        """
        with self._lock:
            # 1. a HumanApproval at all
            if type(approval) is not HumanApproval:
                raise ProposalNotAuthoritative("only a gate-issued HumanApproval carries authority")
            # 2. the exact object this gate issued
            if self._issued.get(approval.approval_id) is not approval:
                raise ApprovalNotRecognized("the approval is not the object this gate issued")
            # 3. not yet consumed
            if approval.approval_id in self._consumed:
                raise ApprovalAlreadyUsed(f"approval {approval.approval_id!r} has already been redeemed")
            # 4. its session is still open
            if approval.session_id not in self._sessions:
                raise SessionNotRecognized("the approval's session is closed")
            # 5. its request is approved and bound to this approval
            entry = self._requests.get(approval.request_id)
            if (
                entry is None
                or entry.state is not _RequestState.APPROVED
                or entry.approval_id != approval.approval_id
            ):
                raise RequestNotRecognized("the approval's request is not awaiting redemption")
            request = entry.request
            # 6. the stored content still has the digest that was displayed and approved
            recomputed = digest_request(
                kind=request.kind,
                action=request.action,
                subject_id=request.subject_id,
                target_state=request.target_state,
                origin=request.origin,
                payload=request.payload,
            )
            if not (recomputed == request.digest == approval.digest):
                raise DigestMismatch("the stored request no longer matches the approved digest")
            # 7. the command is for this kind of request
            if type(expected) is not RequestKind or request.kind is not expected:
                raise ApprovalKindMismatch(f"the approval is for {request.kind.value}, not {expected}")
            # every check passed: consume
            self._consumed.add(approval.approval_id)
            entry.state = _RequestState.REDEEMED
            return request
