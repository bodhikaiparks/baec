"""Application layer: the human-authorization boundary above the repository.

Future interface code imports from this package only. The command facade is
for a trusted human-interaction adapter (the future UI); the proposal facade
is a deterministic read/preview/propose surface on a query-only connection
and is not connected to any model or MCP input in Phase 4. See
docs/PHASE4_APPLICATION_BOUNDARY_DESIGN.md.
"""

from baec_app.application.approval import HumanApproval
from baec_app.application.composition import build_command_facade, build_proposal_facade, open_read_connection
from baec_app.application.context import Actor, Clock, IdFactory, InteractionSession, SystemClock, UuidIdFactory
from baec_app.application.errors import (
    AiDraftNotPermitted,
    ApplicationError,
    ApplicationValidationError,
    ApprovalAlreadyUsed,
    ApprovalKindMismatch,
    ApprovalNotRecognized,
    CanonicalizationError,
    DigestMismatch,
    HumanActionError,
    NotConfirmable,
    ProposalNotAuthoritative,
    ReadDatabaseUnavailable,
    ReadOnlyConnectionRequired,
    ReferenceMismatch,
    RequestAlreadyApproved,
    RequestAlreadyRegistered,
    RequestNotCoherent,
    RequestNotRecognized,
    SessionNotRecognized,
)
from baec_app.application.facades import HumanCommandFacade, ProposalFacade, ReadService
from baec_app.application.proposals import (
    ConfirmationProposal,
    DormancyJudgmentProposal,
    MoveToActiveProposal,
    MoveToDormantProposal,
    MoveToNoPlausiblePathProposal,
    ProposalOrigin,
)
from baec_app.application.requests import ApprovalRequest, ClassificationPreview, RequestKind

# Read-model result types and read-path errors returned or raised through the
# public read surface (ReadService, open_read_connection). Re-exported so
# interface code never imports the data layer; definitions are unchanged.
from baec_app.data.database import DatabaseVersionError, PersistenceIntegrityError, RepositoryNotFoundError
from baec_app.data.records import PersistedDormancyJudgment, SourceInteraction, TransitionHistoryEntry

__all__ = [
    "Actor",
    "AiDraftNotPermitted",
    "ApplicationError",
    "ApplicationValidationError",
    "ApprovalAlreadyUsed",
    "ApprovalKindMismatch",
    "ApprovalNotRecognized",
    "ApprovalRequest",
    "CanonicalizationError",
    "ClassificationPreview",
    "Clock",
    "ConfirmationProposal",
    "DatabaseVersionError",
    "DigestMismatch",
    "DormancyJudgmentProposal",
    "HumanActionError",
    "HumanApproval",
    "HumanCommandFacade",
    "IdFactory",
    "InteractionSession",
    "MoveToActiveProposal",
    "MoveToDormantProposal",
    "MoveToNoPlausiblePathProposal",
    "NotConfirmable",
    "PersistedDormancyJudgment",
    "PersistenceIntegrityError",
    "ProposalFacade",
    "ProposalNotAuthoritative",
    "ProposalOrigin",
    "ReadDatabaseUnavailable",
    "ReadOnlyConnectionRequired",
    "ReadService",
    "ReferenceMismatch",
    "RepositoryNotFoundError",
    "RequestAlreadyApproved",
    "RequestAlreadyRegistered",
    "RequestKind",
    "RequestNotCoherent",
    "RequestNotRecognized",
    "SessionNotRecognized",
    "SourceInteraction",
    "SystemClock",
    "TransitionHistoryEntry",
    "UuidIdFactory",
    "build_command_facade",
    "build_proposal_facade",
    "open_read_connection",
]
