"""Approval requests: the immutable content a human is asked to approve.

A request grants nothing. Its action, subject, and target are derived from
its kind and payload, never supplied separately, and its digest must equal
the canonical digest of its content, so a request whose content and digest
disagree cannot be constructed.

Payload fields are validated by exact type. The schema is closed: no field
can hold an authorization, approval, request, mapping, or other object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from baec_app.application.canonical import digest_request
from baec_app.application.errors import ApplicationValidationError
from baec_app.application.proposals import ProposalOrigin, require_phase4_origin
from baec_app.domain.baec_rules import ClassificationResult, classify_candidate
from baec_app.domain.enums import (
    AccountState,
    AuthorizationAction,
    BaecClassification,
    NoPlausiblePathGround,
    ReviewAnswer,
)
from baec_app.domain.models import (
    BaecCandidate,
    EvaluationEvidence,
    NonEvaluationEvidence,
)
from baec_app.domain.state_machine import TransitionResult


def _require_text(value: object, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise ApplicationValidationError(f"{field} must be a non-blank string")


def _require_optional_text(value: object, field: str) -> None:
    if value is not None and type(value) is not str:
        raise ApplicationValidationError(f"{field} must be a string or None")


def _require_aware(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationValidationError(f"{field} must be a timezone-aware datetime")


def _require_exact(value: object, expected: type, field: str) -> None:
    if type(value) is not expected:
        raise ApplicationValidationError(f"{field} must be exactly {expected.__name__}")


def _require_optional_exact(value: object, expected: type, field: str) -> None:
    if value is not None and type(value) is not expected:
        raise ApplicationValidationError(f"{field} must be exactly {expected.__name__} or None")


class RequestKind(Enum):
    CONFIRM_BAEC = "CONFIRM_BAEC"
    RECORD_DORMANCY_JUDGMENT = "RECORD_DORMANCY_JUDGMENT"
    MOVE_TO_CONDITIONALLY_DORMANT = "MOVE_TO_CONDITIONALLY_DORMANT"
    MOVE_TO_ACTIVE_OPPORTUNITY = "MOVE_TO_ACTIVE_OPPORTUNITY"
    MOVE_TO_NO_PLAUSIBLE_PATH = "MOVE_TO_NO_PLAUSIBLE_PATH"


# --- payloads -------------------------------------------------------------------


@dataclass(frozen=True)
class ConfirmBaecPayload:
    baec_id: str
    candidate: BaecCandidate
    captured_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.baec_id, "ConfirmBaecPayload.baec_id")
        _require_exact(self.candidate, BaecCandidate, "ConfirmBaecPayload.candidate")
        _require_aware(self.captured_at, "ConfirmBaecPayload.captured_at")


@dataclass(frozen=True)
class DormancyJudgmentPayload:
    baec_id: str
    plausibility: ReviewAnswer
    addressability: ReviewAnswer
    notes: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.baec_id, "DormancyJudgmentPayload.baec_id")
        _require_exact(self.plausibility, ReviewAnswer, "DormancyJudgmentPayload.plausibility")
        _require_exact(self.addressability, ReviewAnswer, "DormancyJudgmentPayload.addressability")
        _require_optional_text(self.notes, "DormancyJudgmentPayload.notes")


@dataclass(frozen=True)
class MoveToDormantPayload:
    account_id: str
    baec_id: str
    judgment_id: int
    non_evaluation_evidence: NonEvaluationEvidence | None = None

    def __post_init__(self) -> None:
        _require_text(self.account_id, "MoveToDormantPayload.account_id")
        _require_text(self.baec_id, "MoveToDormantPayload.baec_id")
        if type(self.judgment_id) is not int or self.judgment_id < 1:
            raise ApplicationValidationError("MoveToDormantPayload.judgment_id must be a positive int")
        _require_optional_exact(
            self.non_evaluation_evidence, NonEvaluationEvidence, "MoveToDormantPayload.non_evaluation_evidence"
        )


@dataclass(frozen=True)
class MoveToActivePayload:
    account_id: str
    evaluation_evidence: EvaluationEvidence

    def __post_init__(self) -> None:
        _require_text(self.account_id, "MoveToActivePayload.account_id")
        _require_exact(self.evaluation_evidence, EvaluationEvidence, "MoveToActivePayload.evaluation_evidence")


@dataclass(frozen=True)
class MoveToNoPlausiblePathPayload:
    account_id: str
    ground: NoPlausiblePathGround
    reason: str
    non_evaluation_evidence: NonEvaluationEvidence | None = None
    basis_interaction_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.account_id, "MoveToNoPlausiblePathPayload.account_id")
        _require_exact(self.ground, NoPlausiblePathGround, "MoveToNoPlausiblePathPayload.ground")
        # Only the type is checked; whether the reason is acceptable is the
        # locked state machine's decision (REASON_MISSING).
        if type(self.reason) is not str:
            raise ApplicationValidationError("MoveToNoPlausiblePathPayload.reason must be a string")
        _require_optional_exact(
            self.non_evaluation_evidence,
            NonEvaluationEvidence,
            "MoveToNoPlausiblePathPayload.non_evaluation_evidence",
        )
        _require_optional_text(self.basis_interaction_id, "MoveToNoPlausiblePathPayload.basis_interaction_id")


# kind -> (payload type, authorization action, subject field, target state)
_KINDS: dict[RequestKind, tuple[type, AuthorizationAction, str, AccountState | None]] = {
    RequestKind.CONFIRM_BAEC: (ConfirmBaecPayload, AuthorizationAction.CONFIRM_BAEC, "baec_id", None),
    RequestKind.RECORD_DORMANCY_JUDGMENT: (
        DormancyJudgmentPayload,
        AuthorizationAction.RECORD_DORMANCY_JUDGMENT,
        "baec_id",
        None,
    ),
    RequestKind.MOVE_TO_CONDITIONALLY_DORMANT: (
        MoveToDormantPayload,
        AuthorizationAction.CHANGE_ACCOUNT_STATE,
        "account_id",
        AccountState.CONDITIONALLY_DORMANT,
    ),
    RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY: (
        MoveToActivePayload,
        AuthorizationAction.CHANGE_ACCOUNT_STATE,
        "account_id",
        AccountState.ACTIVE_OPPORTUNITY,
    ),
    RequestKind.MOVE_TO_NO_PLAUSIBLE_PATH: (
        MoveToNoPlausiblePathPayload,
        AuthorizationAction.CHANGE_ACCOUNT_STATE,
        "account_id",
        AccountState.NO_PLAUSIBLE_PATH,
    ),
}


# --- previews -------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationPreview:
    """The locked classifier's result for a candidate, for display."""

    candidate: BaecCandidate
    result: ClassificationResult
    confirmable: bool

    def __post_init__(self) -> None:
        _require_exact(self.candidate, BaecCandidate, "ClassificationPreview.candidate")
        _require_exact(self.result, ClassificationResult, "ClassificationPreview.result")
        if self.result != classify_candidate(self.candidate):
            raise ApplicationValidationError("ClassificationPreview.result must come from the locked classifier")
        if self.confirmable is not (self.result.classification is BaecClassification.CONFIRMED_BAEC):
            raise ApplicationValidationError("ClassificationPreview.confirmable must match the classification")


def _check_preview(kind: RequestKind, payload, preview, target: AccountState | None) -> None:
    if preview is None:
        return
    if kind is RequestKind.CONFIRM_BAEC:
        if type(preview) is not ClassificationPreview or preview.candidate != payload.candidate:
            raise ApplicationValidationError("a confirmation preview must classify the request's candidate")
    elif target is not None:
        if (
            type(preview) is not TransitionResult
            or preview.account_id != payload.account_id
            or preview.to_state is not target
        ):
            raise ApplicationValidationError("a transition preview must be for the request's account and target")
    else:
        raise ApplicationValidationError(f"{kind.value} requests carry no preview")


# --- request --------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalRequest:
    """Immutable request content. Grants nothing until a human approves it."""

    request_id: str
    session_id: str
    opened_at: datetime
    kind: RequestKind
    action: AuthorizationAction
    subject_id: str
    target_state: AccountState | None
    origin: ProposalOrigin
    payload: object
    digest: str
    preview: ClassificationPreview | TransitionResult | None = None

    def __post_init__(self) -> None:
        _require_text(self.request_id, "ApprovalRequest.request_id")
        _require_text(self.session_id, "ApprovalRequest.session_id")
        _require_aware(self.opened_at, "ApprovalRequest.opened_at")
        _require_exact(self.kind, RequestKind, "ApprovalRequest.kind")
        _require_exact(self.origin, ProposalOrigin, "ApprovalRequest.origin")
        require_phase4_origin(self.origin, "ApprovalRequest.origin")
        payload_type, action, subject_field, target = _KINDS[self.kind]
        _require_exact(self.payload, payload_type, "ApprovalRequest.payload")
        if self.action is not action:
            raise ApplicationValidationError(f"{self.kind.value} requires action {action.value}")
        if self.subject_id != getattr(self.payload, subject_field):
            raise ApplicationValidationError(f"subject_id must equal the payload's {subject_field}")
        if self.target_state is not target:
            raise ApplicationValidationError(f"{self.kind.value} requires target_state {target}")
        expected = digest_request(
            kind=self.kind,
            action=self.action,
            subject_id=self.subject_id,
            target_state=self.target_state,
            origin=self.origin,
            payload=self.payload,
        )
        if self.digest != expected:
            raise ApplicationValidationError("digest does not equal the canonical digest of the request content")
        _check_preview(self.kind, self.payload, self.preview, target)


def build_request(
    *,
    request_id: str,
    session_id: str,
    opened_at: datetime,
    kind: RequestKind,
    origin: ProposalOrigin,
    payload: object,
    preview: ClassificationPreview | TransitionResult | None = None,
) -> ApprovalRequest:
    """Build a request, deriving action, subject, target, and digest from kind and payload."""
    _require_exact(kind, RequestKind, "kind")
    _require_exact(origin, ProposalOrigin, "origin")
    require_phase4_origin(origin, "origin")
    payload_type, action, subject_field, target = _KINDS[kind]
    _require_exact(payload, payload_type, "payload")
    subject_id = getattr(payload, subject_field)
    digest = digest_request(
        kind=kind, action=action, subject_id=subject_id, target_state=target, origin=origin, payload=payload
    )
    return ApprovalRequest(
        request_id=request_id,
        session_id=session_id,
        opened_at=opened_at,
        kind=kind,
        action=action,
        subject_id=subject_id,
        target_state=target,
        origin=origin,
        payload=payload,
        digest=digest,
        preview=preview,
    )
