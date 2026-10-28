"""Builders for the Phase 4 application tests. Setup only; expectations live in the tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from baec_app.application.proposals import ProposalOrigin
from baec_app.application.requests import (
    ConfirmBaecPayload,
    DormancyJudgmentPayload,
    MoveToActivePayload,
    MoveToDormantPayload,
    MoveToNoPlausiblePathPayload,
    RequestKind,
    build_request,
)
from baec_app.domain.enums import NoPlausiblePathGround, ReviewAnswer, ThresholdComparator
from baec_app.domain.models import StringencyExpression
from tests.builders import NOW, candidate, evaluation_evidence, non_evaluation_evidence


class FixedClock:
    """Returns a fixed instant; advance() moves it forward."""

    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta) -> None:
        self.current = self.current + timedelta(**delta)


class SequentialIds:
    """Deterministic identifiers: BAEC-0001, T-0001, ..."""

    def __init__(self) -> None:
        self._baec = 0
        self._token = 0

    def new_baec_id(self) -> str:
        self._baec += 1
        return f"BAEC-{self._baec:04d}"

    def new_token(self) -> str:
        self._token += 1
        return f"T-{self._token:04d}"


def stringency_candidate():
    return candidate(
        stringency=StringencyExpression("more than 10%", ThresholdComparator.GREATER_THAN, Decimal("10"))
    )


def confirm_payload(**overrides):
    values = dict(baec_id="B-1", candidate=stringency_candidate(), captured_at=NOW)
    values.update(overrides)
    return ConfirmBaecPayload(**values)


def judgment_payload(**overrides):
    values = dict(baec_id="B-1", plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.UNKNOWN, notes="Synthetic note.")
    values.update(overrides)
    return DormancyJudgmentPayload(**values)


def dormant_payload(**overrides):
    values = dict(account_id="ACC-1", baec_id="B-1", judgment_id=1, non_evaluation_evidence=non_evaluation_evidence())
    values.update(overrides)
    return MoveToDormantPayload(**values)


def active_payload(**overrides):
    values = dict(account_id="ACC-1", evaluation_evidence=evaluation_evidence())
    values.update(overrides)
    return MoveToActivePayload(**values)


def no_path_payload(**overrides):
    values = dict(
        account_id="ACC-1",
        ground=NoPlausiblePathGround.NO_PLAUSIBLE_BAEC,
        reason="CEE produced no foreseeable condition.",
        non_evaluation_evidence=non_evaluation_evidence(),
        basis_interaction_id="INT-1",
    )
    values.update(overrides)
    return MoveToNoPlausiblePathPayload(**values)


PAYLOAD_BUILDERS = {
    RequestKind.CONFIRM_BAEC: confirm_payload,
    RequestKind.RECORD_DORMANCY_JUDGMENT: judgment_payload,
    RequestKind.MOVE_TO_CONDITIONALLY_DORMANT: dormant_payload,
    RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY: active_payload,
    RequestKind.MOVE_TO_NO_PLAUSIBLE_PATH: no_path_payload,
}


def request_for(kind: RequestKind, *, origin=ProposalOrigin.HUMAN_DRAFT, payload=None, **overrides):
    values = dict(
        request_id="REQ-1",
        session_id="S-1",
        opened_at=NOW,
        kind=kind,
        origin=origin,
        payload=payload if payload is not None else PAYLOAD_BUILDERS[kind](),
    )
    values.update(overrides)
    return build_request(**values)
