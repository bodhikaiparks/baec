"""Classification previews, non-confirmed saves, and human confirmation of BAECs.

Every classification comes from the locked classifier and record factories;
this module never inspects findings or origins itself. Saving a NOT_BAEC or
INSUFFICIENT_EVIDENCE record needs no human authorization (RC-31 requires
none). Confirming a CONFIRMED_BAEC needs a redeemed human approval.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from baec_app.application import authority
from baec_app.application.approval import HumanApproval, HumanConfirmationGate
from baec_app.application.context import Clock, IdFactory, InteractionSession
from baec_app.application.errors import ApplicationValidationError, NotConfirmable, ReferenceMismatch
from baec_app.application.proposals import ProposalOrigin
from baec_app.application.requests import (
    ApprovalRequest,
    ClassificationPreview,
    ConfirmBaecPayload,
    RequestKind,
    build_request,
)
from baec_app.domain.baec_rules import (
    classify_candidate,
    create_confirmed_baec_record,
    create_nonconfirmed_classification_record,
)
from baec_app.domain.enums import BaecClassification
from baec_app.domain.models import BaecCandidate, BaecRecord

if TYPE_CHECKING:
    from baec_app.data.repository import Repository


def preview_classification(candidate: BaecCandidate) -> ClassificationPreview:
    """The locked classifier's result, for display. Writes nothing; needs no repository or gate.

    The one implementation of the classification preview, used by
    ClassificationService and by the read-only ProposalFacade.
    """
    result = classify_candidate(candidate)
    return ClassificationPreview(
        candidate=candidate,
        result=result,
        confirmable=result.classification is BaecClassification.CONFIRMED_BAEC,
    )


class ClassificationService:
    def __init__(self, repository: Repository, gate: HumanConfirmationGate, clock: Clock, ids: IdFactory) -> None:
        self._repository = repository
        self._gate = gate
        self._clock = clock
        self._ids = ids

    def preview(self, candidate: BaecCandidate) -> ClassificationPreview:
        """The locked classifier's result, for display. Writes nothing."""
        return preview_classification(candidate)

    def save_nonconfirmed(self, candidate: BaecCandidate, *, captured_at: datetime) -> BaecRecord:
        """Save a NOT_BAEC or INSUFFICIENT_EVIDENCE record. The locked factory refuses a confirmable candidate."""
        record = create_nonconfirmed_classification_record(
            candidate, baec_id=self._ids.new_baec_id(), captured_at=captured_at
        )
        self._repository.save_classification_record(record)
        return record

    def open_confirmation_request(
        self,
        session: InteractionSession,
        candidate: BaecCandidate,
        *,
        captured_at: datetime,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        """Validate references, run the locked classifier, and register a confirmation request."""
        if type(session) is not InteractionSession:
            raise ApplicationValidationError("a confirmation request needs an InteractionSession")
        if type(candidate) is not BaecCandidate:
            raise ApplicationValidationError("a confirmation request needs a BaecCandidate")
        self._repository.get_account(candidate.account_id)
        interaction = self._repository.get_interaction(candidate.source_interaction_id)
        if interaction.account_id != candidate.account_id:
            raise ReferenceMismatch(
                f"interaction {interaction.interaction_id!r} belongs to account {interaction.account_id!r}, "
                f"not {candidate.account_id!r}"
            )
        preview = self.preview(candidate)
        if not preview.confirmable:
            raise NotConfirmable(preview.result)
        request = build_request(
            request_id=self._ids.new_token(),
            session_id=session.session_id,
            opened_at=self._clock.now(),
            kind=RequestKind.CONFIRM_BAEC,
            origin=origin,
            payload=ConfirmBaecPayload(baec_id=self._ids.new_baec_id(), candidate=candidate, captured_at=captured_at),
            preview=preview,
        )
        self._gate.register(session, request)
        return request

    def confirm(self, approval: HumanApproval) -> BaecRecord:
        """Confirm the BAEC in the redeemed request. All content comes from the stored request."""
        request = self._gate.redeem(approval, RequestKind.CONFIRM_BAEC)
        authorization = authority.authorize(request, approval)
        payload = request.payload
        record = create_confirmed_baec_record(
            payload.candidate,
            baec_id=payload.baec_id,
            captured_at=payload.captured_at,
            confirmation=authorization,
        )
        self._repository.save_confirmed_baec(record)
        return record
