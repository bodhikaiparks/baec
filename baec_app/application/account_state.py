"""Human-authorized account-state transitions (RC-20 to RC-24, RC-27, RC-31, RC-34).

Every decision about whether a transition is allowed belongs to the locked
state machine. This module only:

* loads the referenced stored objects through public repository reads;
* checks the two relations the domain cannot see (an interaction belongs to
  the named account; a judgment is recorded for the named BAEC);
* previews with the locked transition functions and authorization=None;
* registers a request only when the preview's sole rejection is
  AUTHORIZATION_MISSING, and otherwise raises RequestNotCoherent carrying the
  preview unchanged;
* executes a redeemed request through the repository, which re-loads and
  re-verifies everything.

A human approval is authorization, never evidence: Active Opportunity still
requires EvaluationEvidence, and no signal type exists here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baec_app.application import authority
from baec_app.application.approval import HumanApproval, HumanConfirmationGate
from baec_app.application.context import Clock, IdFactory, InteractionSession
from baec_app.application.errors import ApplicationValidationError, ReferenceMismatch, RequestNotCoherent
from baec_app.application.proposals import ProposalOrigin
from baec_app.application.requests import (
    ApprovalRequest,
    MoveToActivePayload,
    MoveToDormantPayload,
    MoveToNoPlausiblePathPayload,
    RequestKind,
    build_request,
)
from baec_app.domain.enums import NoPlausiblePathGround, TransitionRejectionKind
from baec_app.domain.models import EvaluationEvidence, NonEvaluationEvidence
from baec_app.domain.state_machine import (
    TransitionResult,
    transition_to_active_opportunity,
    transition_to_conditionally_dormant,
    transition_to_no_plausible_path,
)

if TYPE_CHECKING:
    from baec_app.data.repository import Repository

# The only rejection a preview may contain for its request to be registered:
# a preview never carries an authorization. No other rejection kind is
# inspected by application code.
_ONLY_AUTHORIZATION_MISSING = (TransitionRejectionKind.AUTHORIZATION_MISSING,)


def require_coherent_preview(preview: TransitionResult) -> None:
    """The single registration rule: the locked preview's only rejection is the missing authorization.

    Used both before registering a request and before producing a deterministic proposal.
    """
    if preview.rejections != _ONLY_AUTHORIZATION_MISSING:
        raise RequestNotCoherent(preview)


class AccountStatePreviewService:
    """The one implementation of the three read-only transition previews.

    Holds only a repository for reads: no gate, clock, identifier factory,
    authority path, approval path, or write method. Used by AccountStateService
    on the command side and by ProposalFacade over a query-only repository.
    """

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    # --- referential checks the domain cannot make ---------------------------

    def _require_interaction_of(self, interaction_id: str, account_id: str) -> None:
        interaction = self._repository.get_interaction(interaction_id)
        if interaction.account_id != account_id:
            raise ReferenceMismatch(
                f"interaction {interaction_id!r} belongs to account {interaction.account_id!r}, not {account_id!r}"
            )

    def _require_evidence_interaction_of(self, evidence: object, evidence_type: type, account_id: str) -> None:
        # Only well-typed evidence names an interaction; anything else is left
        # for the locked transition function to refuse.
        if type(evidence) is evidence_type:
            self._require_interaction_of(evidence.evidence.source_id, account_id)

    # --- read-only previews (locked state machine, authorization=None) --------

    def preview_move_to_conditionally_dormant(
        self,
        account_id: str,
        *,
        baec_id: str,
        judgment_id: int,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
    ) -> TransitionResult:
        account = self._repository.get_account(account_id)
        record = self._repository.get_baec_record(baec_id)
        judgment = None
        for persisted in self._repository.list_dormancy_judgments(baec_id):
            if type(judgment_id) is int and persisted.judgment_id == judgment_id:
                judgment = persisted.judgment
        if judgment is None:
            raise ReferenceMismatch(f"judgment {judgment_id} is not a recorded judgment of BAEC {baec_id}")
        self._require_evidence_interaction_of(non_evaluation_evidence, NonEvaluationEvidence, account_id)
        return transition_to_conditionally_dormant(
            account,
            baec_record=record,
            judgment=judgment,
            authorization=None,
            non_evaluation_evidence=non_evaluation_evidence,
        )

    def preview_move_to_active_opportunity(
        self, account_id: str, *, evaluation_evidence: EvaluationEvidence | None
    ) -> TransitionResult:
        account = self._repository.get_account(account_id)
        self._require_evidence_interaction_of(evaluation_evidence, EvaluationEvidence, account_id)
        return transition_to_active_opportunity(account, evaluation_evidence=evaluation_evidence, authorization=None)

    def preview_move_to_no_plausible_path(
        self,
        account_id: str,
        *,
        ground: NoPlausiblePathGround | None,
        reason: str | None,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        basis_interaction_id: str | None = None,
    ) -> TransitionResult:
        account = self._repository.get_account(account_id)
        if basis_interaction_id is not None:
            self._require_interaction_of(basis_interaction_id, account_id)
        self._require_evidence_interaction_of(non_evaluation_evidence, NonEvaluationEvidence, account_id)
        return transition_to_no_plausible_path(
            account,
            ground=ground,
            reason=reason,
            authorization=None,
            non_evaluation_evidence=non_evaluation_evidence,
        )


class AccountStateService:
    def __init__(self, repository: Repository, gate: HumanConfirmationGate, clock: Clock, ids: IdFactory) -> None:
        self._repository = repository
        self._gate = gate
        self._clock = clock
        self._ids = ids
        self._previews = AccountStatePreviewService(repository)

    # --- read-only previews: delegated to the one preview implementation -------

    def preview_move_to_conditionally_dormant(
        self,
        account_id: str,
        *,
        baec_id: str,
        judgment_id: int,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
    ) -> TransitionResult:
        return self._previews.preview_move_to_conditionally_dormant(
            account_id, baec_id=baec_id, judgment_id=judgment_id, non_evaluation_evidence=non_evaluation_evidence
        )

    def preview_move_to_active_opportunity(
        self, account_id: str, *, evaluation_evidence: EvaluationEvidence | None
    ) -> TransitionResult:
        return self._previews.preview_move_to_active_opportunity(account_id, evaluation_evidence=evaluation_evidence)

    def preview_move_to_no_plausible_path(
        self,
        account_id: str,
        *,
        ground: NoPlausiblePathGround | None,
        reason: str | None,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        basis_interaction_id: str | None = None,
    ) -> TransitionResult:
        return self._previews.preview_move_to_no_plausible_path(
            account_id,
            ground=ground,
            reason=reason,
            non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id,
        )

    # --- request opening --------------------------------------------------------

    def _register(
        self, session: InteractionSession, kind: RequestKind, origin: ProposalOrigin, payload, preview: TransitionResult
    ) -> ApprovalRequest:
        request = build_request(
            request_id=self._ids.new_token(),
            session_id=session.session_id,
            opened_at=self._clock.now(),
            kind=kind,
            origin=origin,
            payload=payload,
            preview=preview,
        )
        self._gate.register(session, request)
        return request

    @staticmethod
    def _require_session(session: object) -> None:
        if type(session) is not InteractionSession:
            raise ApplicationValidationError("a transition request needs an InteractionSession")

    def open_move_to_conditionally_dormant_request(
        self,
        session: InteractionSession,
        account_id: str,
        *,
        baec_id: str,
        judgment_id: int,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        self._require_session(session)
        preview = self.preview_move_to_conditionally_dormant(
            account_id, baec_id=baec_id, judgment_id=judgment_id, non_evaluation_evidence=non_evaluation_evidence
        )
        require_coherent_preview(preview)  # before the payload: the locked domain decides first
        payload = MoveToDormantPayload(
            account_id=account_id, baec_id=baec_id, judgment_id=judgment_id, non_evaluation_evidence=non_evaluation_evidence
        )
        return self._register(session, RequestKind.MOVE_TO_CONDITIONALLY_DORMANT, origin, payload, preview)

    def open_move_to_active_opportunity_request(
        self,
        session: InteractionSession,
        account_id: str,
        *,
        evaluation_evidence: EvaluationEvidence | None,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        self._require_session(session)
        preview = self.preview_move_to_active_opportunity(account_id, evaluation_evidence=evaluation_evidence)
        require_coherent_preview(preview)  # before the payload: the locked domain decides first
        payload = MoveToActivePayload(account_id=account_id, evaluation_evidence=evaluation_evidence)
        return self._register(session, RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY, origin, payload, preview)

    def open_move_to_no_plausible_path_request(
        self,
        session: InteractionSession,
        account_id: str,
        *,
        ground: NoPlausiblePathGround | None,
        reason: str | None,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        basis_interaction_id: str | None = None,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        self._require_session(session)
        preview = self.preview_move_to_no_plausible_path(
            account_id,
            ground=ground,
            reason=reason,
            non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id,
        )
        require_coherent_preview(preview)  # before the payload: the locked domain decides first
        payload = MoveToNoPlausiblePathPayload(
            account_id=account_id,
            ground=ground,
            reason=reason,
            non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id,
        )
        return self._register(session, RequestKind.MOVE_TO_NO_PLAUSIBLE_PATH, origin, payload, preview)

    # --- authoritative commands --------------------------------------------------

    def move_to_conditionally_dormant(self, approval: HumanApproval) -> TransitionResult:
        request = self._gate.redeem(approval, RequestKind.MOVE_TO_CONDITIONALLY_DORMANT)
        authorization = authority.authorize(request, approval)
        payload = request.payload
        return self._repository.persist_transition_to_conditionally_dormant(
            payload.account_id,
            baec_id=payload.baec_id,
            judgment_id=payload.judgment_id,
            authorization=authorization,
            non_evaluation_evidence=payload.non_evaluation_evidence,
            recorded_at=self._clock.now(),
        )

    def move_to_active_opportunity(self, approval: HumanApproval) -> TransitionResult:
        request = self._gate.redeem(approval, RequestKind.MOVE_TO_ACTIVE_OPPORTUNITY)
        authorization = authority.authorize(request, approval)
        payload = request.payload
        return self._repository.persist_transition_to_active_opportunity(
            payload.account_id,
            evaluation_evidence=payload.evaluation_evidence,
            authorization=authorization,
            recorded_at=self._clock.now(),
        )

    def move_to_no_plausible_path(self, approval: HumanApproval) -> TransitionResult:
        request = self._gate.redeem(approval, RequestKind.MOVE_TO_NO_PLAUSIBLE_PATH)
        authorization = authority.authorize(request, approval)
        payload = request.payload
        return self._repository.persist_transition_to_no_plausible_path(
            payload.account_id,
            ground=payload.ground,
            reason=payload.reason,
            authorization=authorization,
            non_evaluation_evidence=payload.non_evaluation_evidence,
            basis_interaction_id=payload.basis_interaction_id,
            recorded_at=self._clock.now(),
        )
