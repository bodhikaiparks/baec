"""Application surfaces for future interface code.

HumanCommandFacade is for the future UI only. It holds the gate and the
command services; every authoritative method takes exactly one approval.

ProposalFacade is a deterministic, read-only surface: reads, previews, and
DETERMINISTIC proposals. It holds no gate, no command service, and no
writable repository; it is built on a separately supplied query-only
connection (see composition.py). In Phase 4 it is not connected to Claude,
MCP, a model, or any external input.

Neither facade reimplements a domain or repository rule; they delegate.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from baec_app.application.account_state import AccountStatePreviewService, AccountStateService, require_coherent_preview
from baec_app.application.approval import HumanApproval, HumanConfirmationGate
from baec_app.application.classification import ClassificationService, preview_classification
from baec_app.application.context import InteractionSession
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.application.errors import NotConfirmable, ProposalNotAuthoritative
from baec_app.application.proposals import (
    ConfirmationProposal,
    DormancyJudgmentProposal,
    MoveToActiveProposal,
    MoveToDormantProposal,
    MoveToNoPlausiblePathProposal,
    PROPOSAL_TYPES,
    ProposalOrigin,
    require_phase4_origin,
)
from baec_app.application.requests import ApprovalRequest, ClassificationPreview
from baec_app.domain.enums import NoPlausiblePathGround, ReviewAnswer
from baec_app.domain.models import BaecCandidate, BaecRecord, EvaluationEvidence, NonEvaluationEvidence
from baec_app.domain.state_machine import TransitionResult

if TYPE_CHECKING:
    from baec_app.data.records import PersistedDormancyJudgment
    from baec_app.data.repository import Repository


class ReadService:
    """Read-only pass-through to the repository. Errors propagate unchanged."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def get_account(self, account_id):
        return self._repository.get_account(account_id)

    def list_accounts(self):
        return self._repository.list_accounts()

    def get_interaction(self, interaction_id):
        return self._repository.get_interaction(interaction_id)

    def list_interactions(self, account_id):
        return self._repository.list_interactions(account_id)

    def get_baec_record(self, baec_id):
        return self._repository.get_baec_record(baec_id)

    def list_baec_records(self, account_id):
        return self._repository.list_baec_records(account_id)

    def list_dormancy_judgments(self, baec_id):
        return self._repository.list_dormancy_judgments(baec_id)

    def get_transition_history(self, account_id):
        return self._repository.get_transition_history(account_id)


class HumanCommandFacade:
    """The future UI's surface: requests, previews, the gate, and approval-only commands."""

    def __init__(
        self,
        gate: HumanConfirmationGate,
        reads: ReadService,
        classification: ClassificationService,
        dormancy: DormancyJudgmentService,
        account_state: AccountStateService,
    ) -> None:
        self.gate = gate
        self.reads = reads
        self._classification = classification
        self._dormancy = dormancy
        self._account_state = account_state

    # --- classification ---------------------------------------------------------

    def preview_classification(self, candidate: BaecCandidate) -> ClassificationPreview:
        return self._classification.preview(candidate)

    def save_nonconfirmed_classification(self, candidate: BaecCandidate, *, captured_at: datetime) -> BaecRecord:
        return self._classification.save_nonconfirmed(candidate, captured_at=captured_at)

    def request_baec_confirmation(
        self,
        session: InteractionSession,
        candidate: BaecCandidate,
        *,
        captured_at: datetime,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        return self._classification.open_confirmation_request(session, candidate, captured_at=captured_at, origin=origin)

    # --- dormancy judgment ---------------------------------------------------------

    def request_dormancy_judgment(
        self,
        session: InteractionSession,
        baec_id: str,
        *,
        plausibility: ReviewAnswer,
        addressability: ReviewAnswer,
        notes: str | None = None,
        origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT,
    ) -> ApprovalRequest:
        return self._dormancy.open_request(
            session, baec_id, plausibility=plausibility, addressability=addressability, notes=notes, origin=origin
        )

    # --- account state -----------------------------------------------------------

    def preview_move_to_conditionally_dormant(self, account_id: str, *, baec_id: str, judgment_id: int,
                                              non_evaluation_evidence: NonEvaluationEvidence | None = None) -> TransitionResult:
        return self._account_state.preview_move_to_conditionally_dormant(
            account_id, baec_id=baec_id, judgment_id=judgment_id, non_evaluation_evidence=non_evaluation_evidence
        )

    def preview_move_to_active_opportunity(self, account_id: str, *, evaluation_evidence: EvaluationEvidence | None) -> TransitionResult:
        return self._account_state.preview_move_to_active_opportunity(account_id, evaluation_evidence=evaluation_evidence)

    def preview_move_to_no_plausible_path(self, account_id: str, *, ground: NoPlausiblePathGround | None, reason: str | None,
                                          non_evaluation_evidence: NonEvaluationEvidence | None = None,
                                          basis_interaction_id: str | None = None) -> TransitionResult:
        return self._account_state.preview_move_to_no_plausible_path(
            account_id, ground=ground, reason=reason, non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id,
        )

    def request_move_to_conditionally_dormant(self, session: InteractionSession, account_id: str, *, baec_id: str,
                                              judgment_id: int, non_evaluation_evidence: NonEvaluationEvidence | None = None,
                                              origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT) -> ApprovalRequest:
        return self._account_state.open_move_to_conditionally_dormant_request(
            session, account_id, baec_id=baec_id, judgment_id=judgment_id,
            non_evaluation_evidence=non_evaluation_evidence, origin=origin,
        )

    def request_move_to_active_opportunity(self, session: InteractionSession, account_id: str, *,
                                           evaluation_evidence: EvaluationEvidence | None,
                                           origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT) -> ApprovalRequest:
        return self._account_state.open_move_to_active_opportunity_request(
            session, account_id, evaluation_evidence=evaluation_evidence, origin=origin
        )

    def request_move_to_no_plausible_path(self, session: InteractionSession, account_id: str, *,
                                          ground: NoPlausiblePathGround | None, reason: str | None,
                                          non_evaluation_evidence: NonEvaluationEvidence | None = None,
                                          basis_interaction_id: str | None = None,
                                          origin: ProposalOrigin = ProposalOrigin.HUMAN_DRAFT) -> ApprovalRequest:
        return self._account_state.open_move_to_no_plausible_path_request(
            session, account_id, ground=ground, reason=reason, non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id, origin=origin,
        )

    # --- proposals into requests -----------------------------------------------------

    def request_from_proposal(self, session: InteractionSession, proposal: object) -> ApprovalRequest:
        """Turn a proposal into a request through the normal request path. Grants nothing.

        An AI_DRAFT is refused here explicitly (AiDraftNotPermitted), before any dispatch,
        even on an object that bypassed the proposal constructors: AI drafts reach a domain
        write only through the Phase 7 persisted review and grant.
        """
        kind = type(proposal)
        if kind in PROPOSAL_TYPES:
            require_phase4_origin(proposal.origin, f"{kind.__name__}.origin")
        if kind is ConfirmationProposal:
            return self.request_baec_confirmation(
                session, proposal.candidate, captured_at=proposal.captured_at, origin=proposal.origin
            )
        if kind is DormancyJudgmentProposal:
            return self.request_dormancy_judgment(
                session, proposal.baec_id, plausibility=proposal.plausibility,
                addressability=proposal.addressability, notes=proposal.notes, origin=proposal.origin,
            )
        if kind is MoveToDormantProposal:
            return self.request_move_to_conditionally_dormant(
                session, proposal.account_id, baec_id=proposal.baec_id, judgment_id=proposal.judgment_id,
                non_evaluation_evidence=proposal.non_evaluation_evidence, origin=proposal.origin,
            )
        if kind is MoveToActiveProposal:
            return self.request_move_to_active_opportunity(
                session, proposal.account_id, evaluation_evidence=proposal.evaluation_evidence, origin=proposal.origin
            )
        if kind is MoveToNoPlausiblePathProposal:
            return self.request_move_to_no_plausible_path(
                session, proposal.account_id, ground=proposal.ground, reason=proposal.reason,
                non_evaluation_evidence=proposal.non_evaluation_evidence,
                basis_interaction_id=proposal.basis_interaction_id, origin=proposal.origin,
            )
        raise ProposalNotAuthoritative("only a Phase 4 proposal object can become a request")

    # --- authoritative commands: an approval and nothing else ----------------------------

    def confirm_baec(self, approval: HumanApproval) -> BaecRecord:
        return self._classification.confirm(approval)

    def record_dormancy_judgment(self, approval: HumanApproval) -> PersistedDormancyJudgment:
        return self._dormancy.record(approval)

    def move_to_conditionally_dormant(self, approval: HumanApproval) -> TransitionResult:
        return self._account_state.move_to_conditionally_dormant(approval)

    def move_to_active_opportunity(self, approval: HumanApproval) -> TransitionResult:
        return self._account_state.move_to_active_opportunity(approval)

    def move_to_no_plausible_path(self, approval: HumanApproval) -> TransitionResult:
        return self._account_state.move_to_no_plausible_path(approval)


class ProposalFacade:
    """Deterministic read, preview, and propose surface. Never writes; holds no authority.

    It proposes only what deterministic application code can check: BAEC
    confirmation and account-state transitions. It never proposes a dormancy
    judgment: plausibility and addressability are human judgments, so a
    DormancyJudgmentProposal is created by UI code as a HUMAN_DRAFT.
    """

    def __init__(self, reads: ReadService, previews: AccountStatePreviewService) -> None:
        self.reads = reads
        self._previews = previews

    def preview_classification(self, candidate: BaecCandidate) -> ClassificationPreview:
        return preview_classification(candidate)

    def preview_move_to_conditionally_dormant(self, account_id: str, *, baec_id: str, judgment_id: int,
                                              non_evaluation_evidence: NonEvaluationEvidence | None = None) -> TransitionResult:
        return self._previews.preview_move_to_conditionally_dormant(
            account_id, baec_id=baec_id, judgment_id=judgment_id, non_evaluation_evidence=non_evaluation_evidence
        )

    def preview_move_to_active_opportunity(self, account_id: str, *, evaluation_evidence: EvaluationEvidence | None) -> TransitionResult:
        return self._previews.preview_move_to_active_opportunity(account_id, evaluation_evidence=evaluation_evidence)

    def preview_move_to_no_plausible_path(self, account_id: str, *, ground: NoPlausiblePathGround | None, reason: str | None,
                                          non_evaluation_evidence: NonEvaluationEvidence | None = None,
                                          basis_interaction_id: str | None = None) -> TransitionResult:
        return self._previews.preview_move_to_no_plausible_path(
            account_id, ground=ground, reason=reason, non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id,
        )

    # --- deterministic proposals ------------------------------------------------------
    # DETERMINISTIC means this object was produced by application code from the
    # caller's inputs after the locked checks; it grants nothing.

    def propose_confirmation(self, candidate: BaecCandidate, *, captured_at: datetime) -> ConfirmationProposal:
        preview = self.preview_classification(candidate)
        if not preview.confirmable:
            raise NotConfirmable(preview.result)
        return ConfirmationProposal(candidate=candidate, captured_at=captured_at, origin=ProposalOrigin.DETERMINISTIC)

    def propose_move_to_conditionally_dormant(self, account_id: str, *, baec_id: str, judgment_id: int,
                                              non_evaluation_evidence: NonEvaluationEvidence | None = None) -> MoveToDormantProposal:
        require_coherent_preview(self.preview_move_to_conditionally_dormant(
            account_id, baec_id=baec_id, judgment_id=judgment_id, non_evaluation_evidence=non_evaluation_evidence
        ))
        return MoveToDormantProposal(
            account_id=account_id, baec_id=baec_id, judgment_id=judgment_id,
            non_evaluation_evidence=non_evaluation_evidence, origin=ProposalOrigin.DETERMINISTIC,
        )

    def propose_move_to_active_opportunity(self, account_id: str, *, evaluation_evidence: EvaluationEvidence | None) -> MoveToActiveProposal:
        require_coherent_preview(self.preview_move_to_active_opportunity(account_id, evaluation_evidence=evaluation_evidence))
        return MoveToActiveProposal(
            account_id=account_id, evaluation_evidence=evaluation_evidence, origin=ProposalOrigin.DETERMINISTIC
        )

    def propose_move_to_no_plausible_path(self, account_id: str, *, ground: NoPlausiblePathGround | None, reason: str | None,
                                          non_evaluation_evidence: NonEvaluationEvidence | None = None,
                                          basis_interaction_id: str | None = None) -> MoveToNoPlausiblePathProposal:
        require_coherent_preview(self.preview_move_to_no_plausible_path(
            account_id, ground=ground, reason=reason, non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id,
        ))
        return MoveToNoPlausiblePathProposal(
            account_id=account_id, ground=ground, reason=reason, non_evaluation_evidence=non_evaluation_evidence,
            basis_interaction_id=basis_interaction_id, origin=ProposalOrigin.DETERMINISTIC,
        )
