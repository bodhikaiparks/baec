"""Persistence-layer data transfer objects (DTOs).

These describe things the storage layer needs that the locked domain layer
does not model. They are not domain models and carry no BAEC decision logic.
All are frozen and validate themselves on construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from baec_app.domain.enums import (
    AccountState,
    AuthorizationAction,
    NoPlausiblePathGround,
    TransitionUnresolvedKind,
)
from baec_app.domain.models import (
    DormancyJudgment,
    EvaluationEvidence,
    HumanAuthorization,
    NonEvaluationEvidence,
)


class RecordValidationError(ValueError):
    """Raised when a persistence DTO would be internally inconsistent."""


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RecordValidationError(f"{field} must be a non-empty string")


def _aware(value: object, field: str) -> None:
    if not isinstance(value, datetime):
        raise RecordValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecordValidationError(f"{field} must be timezone-aware")


def _row_id(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RecordValidationError(f"{field} must be a positive integer")


@dataclass(frozen=True)
class SourceInteraction:
    """One recorded buyer-seller interaction: the immutable source text."""

    interaction_id: str
    account_id: str
    occurred_at: datetime
    text: str

    def __post_init__(self) -> None:
        _text(self.interaction_id, "SourceInteraction.interaction_id")
        _text(self.account_id, "SourceInteraction.account_id")
        _aware(self.occurred_at, "SourceInteraction.occurred_at")
        _text(self.text, "SourceInteraction.text")


@dataclass(frozen=True)
class PersistedDormancyJudgment:
    """A stored dormancy judgment together with its storage identifier."""

    judgment_id: int
    judgment: DormancyJudgment

    def __post_init__(self) -> None:
        _row_id(self.judgment_id, "PersistedDormancyJudgment.judgment_id")
        if not isinstance(self.judgment, DormancyJudgment):
            raise RecordValidationError("judgment must be a DormancyJudgment")


@dataclass(frozen=True)
class TransitionHistoryEntry:
    """One append-only row of account-state history.

    Only allowed transitions are ever stored. The structural checks here
    mirror what an allowed TransitionResult guarantees, so a contradictory
    stored row cannot be loaded.
    """

    transition_id: int
    account_id: str
    from_state: AccountState | None
    to_state: AccountState
    authorization: HumanAuthorization
    recorded_at: datetime
    baec_id: str | None = None
    judgment_id: int | None = None
    evaluation_evidence: EvaluationEvidence | None = None
    non_evaluation_evidence: NonEvaluationEvidence | None = None
    ground: NoPlausiblePathGround | None = None
    reason: str | None = None
    basis_interaction_id: str | None = None
    unresolved: tuple[TransitionUnresolvedKind, ...] = ()

    def __post_init__(self) -> None:
        def fail(message: str) -> None:
            raise RecordValidationError(f"TransitionHistoryEntry: {message}")

        _row_id(self.transition_id, "TransitionHistoryEntry.transition_id")
        _text(self.account_id, "TransitionHistoryEntry.account_id")
        _aware(self.recorded_at, "TransitionHistoryEntry.recorded_at")
        if self.from_state is not None and not isinstance(self.from_state, AccountState):
            fail("from_state must be an AccountState or None")
        if not isinstance(self.to_state, AccountState):
            fail("to_state must be an AccountState")
        if self.from_state is self.to_state:
            fail("a stored transition must change the state")

        auth = self.authorization
        if not isinstance(auth, HumanAuthorization):
            fail("authorization must be a HumanAuthorization")
        if auth.action is not AuthorizationAction.CHANGE_ACCOUNT_STATE:
            fail("authorization is not for changing account state")
        if auth.subject_id != self.account_id:
            fail("authorization is for a different account")
        if auth.target_state is not self.to_state:
            fail("authorization is for a different destination state")

        dormant = self.to_state is AccountState.CONDITIONALLY_DORMANT
        active = self.to_state is AccountState.ACTIVE_OPPORTUNITY
        no_path = self.to_state is AccountState.NO_PLAUSIBLE_PATH
        leaving_active = self.from_state is AccountState.ACTIVE_OPPORTUNITY

        if dormant:
            _text(self.baec_id, "TransitionHistoryEntry.baec_id")
            _row_id(self.judgment_id, "TransitionHistoryEntry.judgment_id")
        elif self.baec_id is not None or self.judgment_id is not None:
            fail("baec_id and judgment_id apply only to CONDITIONALLY_DORMANT")

        if active:
            if not isinstance(self.evaluation_evidence, EvaluationEvidence):
                fail("ACTIVE_OPPORTUNITY requires evaluation evidence")
            if self.evaluation_evidence.account_id != self.account_id:
                fail("evaluation evidence is for a different account")
        elif self.evaluation_evidence is not None:
            fail("evaluation evidence applies only to ACTIVE_OPPORTUNITY")

        if self.non_evaluation_evidence is not None:
            if not isinstance(self.non_evaluation_evidence, NonEvaluationEvidence):
                fail("non_evaluation_evidence must be NonEvaluationEvidence")
            if active:
                fail("non-evaluation evidence cannot support ACTIVE_OPPORTUNITY")
            if self.non_evaluation_evidence.account_id != self.account_id:
                fail("non-evaluation evidence is for a different account")
        elif leaving_active:
            fail("leaving ACTIVE_OPPORTUNITY requires non-evaluation evidence")

        if no_path:
            if not isinstance(self.ground, NoPlausiblePathGround):
                fail("NO_PLAUSIBLE_PATH requires a ground")
            _text(self.reason, "TransitionHistoryEntry.reason")
            if self.basis_interaction_id is not None:
                _text(self.basis_interaction_id, "TransitionHistoryEntry.basis_interaction_id")
        elif (
            self.ground is not None
            or self.reason is not None
            or self.basis_interaction_id is not None
        ):
            fail("ground, reason and basis interaction apply only to NO_PLAUSIBLE_PATH")

        if not isinstance(self.unresolved, tuple) or not all(
            isinstance(kind, TransitionUnresolvedKind) for kind in self.unresolved
        ):
            fail("unresolved must be a tuple of TransitionUnresolvedKind")
        if len(set(self.unresolved)) != len(self.unresolved):
            fail("unresolved items must not repeat")
        if self.unresolved and not dormant:
            fail("unresolved items apply only to CONDITIONALLY_DORMANT")
