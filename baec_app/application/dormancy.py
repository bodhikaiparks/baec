"""Human dormancy judgments on a BAEC (RC-22).

Recording a judgment needs a redeemed human approval. Whether a judgment
permits Conditionally Dormant is decided later by the locked state machine;
this module records the human's answers exactly and judges nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baec_app.application import authority
from baec_app.application.approval import HumanApproval, HumanConfirmationGate
from baec_app.application.context import Clock, IdFactory, InteractionSession
from baec_app.application.errors import ApplicationValidationError
from baec_app.application.proposals import ProposalOrigin
from baec_app.application.requests import ApprovalRequest, DormancyJudgmentPayload, RequestKind, build_request
from baec_app.data.records import PersistedDormancyJudgment
from baec_app.domain.enums import ReviewAnswer
from baec_app.domain.models import DormancyJudgment

if TYPE_CHECKING:
    from baec_app.data.repository import Repository


class DormancyJudgmentService:
    def __init__(self, repository: Repository, gate: HumanConfirmationGate, clock: Clock, ids: IdFactory) -> None:
        self._repository = repository
        self._gate = gate
        self._clock = clock
        self._ids = ids

    def open_request(
        self,
        session: InteractionSession,
        baec_id: str,
        *,
        plausibility: ReviewAnswer,
        addressability: ReviewAnswer,
        notes: str | None = None,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        """Check that the BAEC exists, then register a judgment request with the human's answers."""
        if type(session) is not InteractionSession:
            raise ApplicationValidationError("a judgment request needs an InteractionSession")
        payload = DormancyJudgmentPayload(
            baec_id=baec_id, plausibility=plausibility, addressability=addressability, notes=notes
        )
        self._repository.get_baec_record(baec_id)  # referential check only; errors propagate unchanged
        request = build_request(
            request_id=self._ids.new_token(),
            session_id=session.session_id,
            opened_at=self._clock.now(),
            kind=RequestKind.RECORD_DORMANCY_JUDGMENT,
            origin=origin,
            payload=payload,
        )
        self._gate.register(session, request)
        return request

    def record(self, approval: HumanApproval) -> PersistedDormancyJudgment:
        """Record the judgment in the redeemed request. All content comes from the stored request."""
        request = self._gate.redeem(approval, RequestKind.RECORD_DORMANCY_JUDGMENT)
        authorization = authority.authorize(request, approval)
        payload = request.payload
        judgment = DormancyJudgment(
            baec_id=payload.baec_id,
            plausibility=payload.plausibility,
            addressability=payload.addressability,
            authorization=authorization,
            notes=payload.notes,
        )
        judgment_id = self._repository.record_dormancy_judgment(judgment)
        return PersistedDormancyJudgment(judgment_id, judgment)
