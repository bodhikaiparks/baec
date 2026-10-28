"""Persisted human authorization grants and atomic BAEC confirmation execution (Phase 7F-B).

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §11-§16. AI may propose. Humans authorize. Domain rules
decide. Execution records.

* A Review Accepted is only a finalized human review. It authorizes nothing. Authorization is a second, separate,
  explicit human action (ProposalAuthorizationService.authorize_confirmation), which persists one grant for exactly
  one action, confirm_baec, on one immutable ACCEPTED review revision. Issuance recomputes the confirmation
  candidate from that revision and runs the locked classifier; only CONFIRMED_BAEC is eligible.
* A grant expires 15 minutes after issuance (an IMPLEMENTATION setting): now >= expires_at is expired. It is
  single-use. A new review revision or a rejection supersedes it at once, through persisted rows, even before it
  expires. After expiry, only another explicit human authorization issues a new grant, which names the one it
  replaces. Grants are never updated, extended, or deleted.
* ConfirmationExecutionService.execute takes a grant id and nothing else. Inside one transaction it re-checks the
  grant, its binding, its lifecycle, the revision, the lineage, the evidence, the human normalization, and the
  classifier, and then writes the confirmed BAEC, its HumanAuthorization, the confirmation link, and the
  consumption: all of them, or none. A failed execution leaves the grant unused. Confirmation changes no account
  state and is not an opportunity, a purchase-intent signal, or a contact.

Limitations, stated plainly. The actor label is self-asserted; there is no authentication. A grant shows only that
the prototype's human-interaction path issued it. Python code with unrestricted access to the local database or
process could bypass these conventions; the schema triggers and static boundaries are defense in depth, not a
security boundary. Human-reviewed normalization stays in the review lineage and is never written to the legacy
AI-derived baec_records.normalized_* columns.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from baec_app.application import authority
from baec_app.application.ai_proposal_mapping import (
    ArtifactNotEligible,
    map_artifact_to_proposal_content,
    verify_artifact,
)
from baec_app.application.context import Clock
from baec_app.application.errors import ApplicationError
from baec_app.application.human_normalization import HUMAN_NORMALIZATION_VALIDATION_VERSION
from baec_app.application.proposal_review import ReviewNotSaved, rebuild_reviewed_candidate
from baec_app.data.ai_provenance import AiProvenanceStore
from baec_app.data.authorization_grants import (
    GRANT_ID_PATTERN,
    AuthorizationGrantStore,
    GrantExecutionStore,
    GrantRecord,
)
from baec_app.data.database import (
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    transaction,
)
from baec_app.data.proposal_bridge import (
    AiProposalRecord,
    ProposalBridgeStore,
    ReviewDecision,
    ReviewRevisionRecord,
    sha256_text,
)
from baec_app.data.records import RecordValidationError
from baec_app.data.repository import Repository, decode_datetime, insert_confirmed_record
from baec_app.domain.baec_rules import classify_candidate, create_confirmed_baec_record
from baec_app.domain.enums import BaecClassification
from baec_app.domain.models import BaecCandidate

GRANT_ACTION = "CONFIRM_BAEC"  # the one action a Phase 7 grant can authorize
GRANT_STATUSES = ("ACTIVE", "EXPIRED", "SUPERSEDED", "CONSUMED")

AUTHORIZATION_FAILURE_CODES = (
    "actor_blank",
    "proposal_not_found",
    "proposal_integrity_failure",
    "proposal_rejected",
    "review_not_accepted",
    "lineage_already_confirmed",
    "evidence_invalid",
    "normalization_invalid",
    "not_confirmable",
    "active_grant_exists",
    "conflict",
)
EXECUTION_FAILURE_CODES = (
    "grant_id_invalid",
    "grant_not_found",
    "grant_already_consumed",
    "grant_superseded",
    "grant_binding_mismatch",
    "grant_clock_invalid",
    "grant_expired",
    "review_revision_superseded",
    "proposal_rejected",
    "review_not_accepted",
    "lineage_integrity_failure",
    "lineage_already_confirmed",
    "normalization_contract_mismatch",
    "evidence_invalid",
    "normalization_invalid",
    "not_confirmable",
    "conflict",
)


class AuthorizationRefused(ApplicationError):
    """No grant was issued. Carries one closed code and no source text."""

    def __init__(self, code: str) -> None:
        if code not in AUTHORIZATION_FAILURE_CODES:
            raise ValueError("unknown authorization failure code")
        super().__init__(code)
        self.code = code


class ExecutionRefused(ApplicationError):
    """Nothing was written and the grant was not used. Carries one closed code and no source text."""

    def __init__(self, code: str) -> None:
        if code not in EXECUTION_FAILURE_CODES:
            raise ValueError("unknown execution failure code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GrantView:
    """A grant and its status at one instant. ACTIVE grants may be executed; no other status may."""

    grant_id: str
    proposal_id: str
    review_revision_id: str
    actor_label: str  # self-asserted
    issued_at: datetime
    expires_at: datetime
    status: str


@dataclass(frozen=True)
class AuthorizationStatus:
    """What the human-interaction path needs to decide whether to offer 'Authorize BAEC confirmation'."""

    proposal_id: str
    review_state: str  # OPEN | REVIEW_ACCEPTED | REVIEW_REJECTED | CONFIRMED
    review_revision_id: str | None
    classification: BaecClassification | None  # recomputed from the immutable revision; None when not computable
    eligibility_failure: str | None  # the closed code that blocks a grant, or None when one may be issued
    grants: tuple[GrantView, ...]


@dataclass(frozen=True)
class ConfirmationResult:
    """Execution metadata only. A confirmed BAEC record now exists; nothing else changed."""

    baec_id: str
    proposal_id: str
    review_revision_id: str
    grant_id: str


def grant_status(lifecycle, grant: GrantRecord, now: datetime) -> str:
    """Status precedence: consumed, then superseded (even if also expired), then expired, else active."""
    if lifecycle.consumed_baec_id is not None:
        return "CONSUMED"
    if lifecycle.superseded:
        return "SUPERSEDED"
    return "EXPIRED" if now >= grant.expires_at else "ACTIVE"


def _new_grant_id() -> str:
    return "grant_" + secrets.token_hex(32)


def _new_baec_id() -> str:
    return "BAEC-" + uuid.uuid4().hex


class _SourceReads:
    """Exactly the two reads the eligibility rules need. Holds no repository and offers no write."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_account(self, account_id: str):
        return Repository(self._connection).get_account(account_id)

    def get_interaction(self, interaction_id: str):
        return Repository(self._connection).get_interaction(interaction_id)


@dataclass(frozen=True)
class _Lineage:
    proposal: AiProposalRecord
    revisions: tuple[ReviewRevisionRecord, ...]
    decisions: tuple
    interaction_text: str
    rejected: bool
    confirmed: bool


def _load_lineage(connection: sqlite3.Connection, proposal_id: str) -> _Lineage:
    """The stored proposal, re-verified against its re-verified artifact, with its review history. Reads only."""
    bridge, provenance, sources = ProposalBridgeStore(connection), AiProvenanceStore(connection), _SourceReads(connection)
    proposal = bridge.get_proposal(proposal_id)
    verified = verify_artifact(proposal.artifact_id, provenance=provenance, repository=sources)
    if map_artifact_to_proposal_content(verified) != proposal.content:
        raise PersistenceIntegrityError("the proposal snapshot no longer matches its artifact")
    decisions = bridge.list_decisions(proposal_id)
    return _Lineage(proposal, bridge.list_revisions(proposal_id), decisions, verified.interaction.text,
                    any(d.decision is ReviewDecision.REJECTED for d in decisions),
                    bridge.confirmed_baec_for_artifact(proposal.artifact_id) is not None)


def _accepted_decision(lineage: _Lineage, revision_id: str):
    return next((d for d in lineage.decisions if d.decision is ReviewDecision.ACCEPTED
                 and d.review_revision_id == revision_id), None)


def _confirmable(lineage: _Lineage, revision: ReviewRevisionRecord, refuse) -> BaecCandidate:
    """Rebuild the candidate from the immutable revision and require the locked classifier's CONFIRMED_BAEC."""
    try:
        candidate = rebuild_reviewed_candidate(lineage.proposal, revision, lineage.interaction_text)
    except ReviewNotSaved as error:
        raise refuse("normalization_invalid" if error.code == "normalization_invalid" else "evidence_invalid") from None
    if classify_candidate(candidate).classification is not BaecClassification.CONFIRMED_BAEC:
        raise refuse("not_confirmable")
    return candidate


class ProposalAuthorizationService:
    """Issues persisted grants for the human-interaction path. Never executes them."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock, new_grant_id=_new_grant_id,
                 new_baec_id=_new_baec_id) -> None:
        self._connection = connection
        self._grants = AuthorizationGrantStore(connection)  # verifies the schema and foreign keys
        self._clock = clock
        self._new_grant_id = new_grant_id
        self._new_baec_id = new_baec_id

    def authorization_status(self, proposal_id: str) -> AuthorizationStatus:
        """Reads only. Never issues, extends, or refreshes a grant."""
        now = self._clock.now()
        lineage = self._lineage(proposal_id)
        views = tuple(self._view(g, now) for g in self._grants.grants_for_proposal(proposal_id))
        revision = lineage.revisions[-1] if lineage.revisions else None
        if lineage.confirmed:
            return AuthorizationStatus(proposal_id, "CONFIRMED", revision and revision.review_revision_id,
                                       None, "lineage_already_confirmed", views)
        if lineage.rejected:
            return AuthorizationStatus(proposal_id, "REVIEW_REJECTED", None, None, "proposal_rejected", views)
        if revision is None or _accepted_decision(lineage, revision.review_revision_id) is None:
            return AuthorizationStatus(proposal_id, "OPEN", None, None, "review_not_accepted", views)
        try:
            _confirmable(lineage, revision, AuthorizationRefused)
            classification, failure = BaecClassification.CONFIRMED_BAEC, None
        except AuthorizationRefused as refusal:
            classification, failure = None, refusal.code
            if refusal.code == "not_confirmable":
                classification = classify_candidate(rebuild_reviewed_candidate(
                    lineage.proposal, revision, lineage.interaction_text)).classification
        return AuthorizationStatus(proposal_id, "REVIEW_ACCEPTED", revision.review_revision_id, classification,
                                   failure, views)

    def authorize_confirmation(self, proposal_id: str, *, actor_label: str) -> GrantView:
        """The explicit human action: persist one grant to confirm the BAEC of the latest ACCEPTED revision."""
        if type(actor_label) is not str or not actor_label.strip():
            raise AuthorizationRefused("actor_blank")
        now = self._clock.now()
        lineage = self._lineage(proposal_id)
        if lineage.confirmed:
            raise AuthorizationRefused("lineage_already_confirmed")
        if lineage.rejected:
            raise AuthorizationRefused("proposal_rejected")
        revision = lineage.revisions[-1] if lineage.revisions else None
        decision = revision and _accepted_decision(lineage, revision.review_revision_id)
        if decision is None:
            raise AuthorizationRefused("review_not_accepted")
        _confirmable(lineage, revision, AuthorizationRefused)
        previous = [g for g in self._grants.grants_for_revision(revision.review_revision_id)
                    if not self._grants.lifecycle(g.grant_id).superseded
                    and self._grants.lifecycle(g.grant_id).consumed_baec_id is None]
        if previous and now < previous[-1].expires_at:
            raise AuthorizationRefused("active_grant_exists")  # never a duplicate, never an extension
        replaced = previous[-1] if previous else None
        grant = GrantRecord.issue(
            grant_id=self._new_grant_id(), account_id=lineage.proposal.account_id,
            interaction_id=lineage.proposal.interaction_id, artifact_id=lineage.proposal.artifact_id,
            proposal_id=proposal_id, proposal_digest=lineage.proposal.proposal_digest,
            review_revision_id=revision.review_revision_id, review_content_digest=revision.review_content_digest,
            decision_id=decision.decision_id, issue_sequence=1 if replaced is None else replaced.issue_sequence + 1,
            supersedes_grant_id=None if replaced is None else replaced.grant_id, baec_id=self._new_baec_id(),
            actor_label=actor_label, issued_at=now,
        )
        try:
            self._grants.add_grant(grant)
        except RepositoryConflictError:
            raise AuthorizationRefused("conflict") from None
        return self._view(grant, now)

    def _lineage(self, proposal_id: str) -> _Lineage:
        try:
            return _load_lineage(self._connection, proposal_id)
        except RepositoryNotFoundError:
            raise AuthorizationRefused("proposal_not_found") from None
        except (PersistenceIntegrityError, ArtifactNotEligible):
            raise AuthorizationRefused("proposal_integrity_failure") from None

    def _view(self, grant: GrantRecord, now: datetime) -> GrantView:
        return GrantView(grant.grant_id, grant.proposal_id, grant.review_revision_id, grant.actor_label,
                         grant.issued_at, grant.expires_at, grant_status(self._grants.lifecycle(grant.grant_id), grant, now))


class ConfirmationExecutionService:
    """Executes one persisted grant: confirms exactly its BAEC, atomically. Takes a grant id and nothing else."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock) -> None:
        self._connection = connection
        self._grants = GrantExecutionStore(connection)  # execution-only: it cannot insert or re-issue a grant
        self._clock = clock

    def execute(self, grant_id: str) -> ConfirmationResult:
        if type(grant_id) is not str or not GRANT_ID_PATTERN.fullmatch(grant_id):
            raise ExecutionRefused("grant_id_invalid")
        try:
            with transaction(self._connection):  # BEGIN IMMEDIATE: concurrent executions serialize here
                return self._execute(grant_id)
        except RepositoryConflictError:
            raise ExecutionRefused("conflict") from None
        except sqlite3.IntegrityError:
            raise ExecutionRefused("conflict") from None

    def _execute(self, grant_id: str) -> ConfirmationResult:
        now = self._clock.now()  # read once, inside the transaction
        try:
            grant = self._grants.get_grant(grant_id)
        except RepositoryNotFoundError:
            raise ExecutionRefused("grant_not_found") from None
        except (RecordValidationError, ValueError):
            raise ExecutionRefused("grant_binding_mismatch") from None  # stored fields no longer form a grant
        lifecycle = self._grants.lifecycle(grant_id)
        if lifecycle.consumed_baec_id is not None:
            raise ExecutionRefused("grant_already_consumed")
        if lifecycle.superseded:
            raise ExecutionRefused("grant_superseded")  # before expiry, even when also expired
        if grant.action != GRANT_ACTION or not grant.binding_is_intact():
            raise ExecutionRefused("grant_binding_mismatch")
        if now < grant.issued_at:
            raise ExecutionRefused("grant_clock_invalid")
        if now >= grant.expires_at:
            raise ExecutionRefused("grant_expired")
        try:
            lineage = _load_lineage(self._connection, grant.proposal_id)
        except (RepositoryNotFoundError, PersistenceIntegrityError, ArtifactNotEligible):
            raise ExecutionRefused("lineage_integrity_failure") from None
        revision = next((r for r in lineage.revisions if r.review_revision_id == grant.review_revision_id), None)
        proposal = lineage.proposal
        if revision is None or (revision.proposal_id, revision.account_id, revision.interaction_id,
                                revision.artifact_id, revision.review_content_digest, revision.proposal_digest) != (
                grant.proposal_id, grant.account_id, grant.interaction_id, grant.artifact_id,
                grant.review_content_digest, grant.proposal_digest) \
                or sha256_text(revision.content) != grant.review_content_digest \
                or (proposal.account_id, proposal.interaction_id, proposal.artifact_id, proposal.proposal_digest) != (
                    grant.account_id, grant.interaction_id, grant.artifact_id, grant.proposal_digest):
            raise ExecutionRefused("grant_binding_mismatch")
        if lineage.revisions[-1].review_revision_id != revision.review_revision_id:
            raise ExecutionRefused("review_revision_superseded")
        accepted = _accepted_decision(lineage, revision.review_revision_id)
        if lineage.rejected:
            raise ExecutionRefused("proposal_rejected")
        if accepted is None or accepted.decision_id != grant.decision_id:
            raise ExecutionRefused("review_not_accepted")
        if lineage.confirmed:
            raise ExecutionRefused("lineage_already_confirmed")
        if revision.normalization_validation_version != HUMAN_NORMALIZATION_VALIDATION_VERSION:
            raise ExecutionRefused("normalization_contract_mismatch")
        candidate = _confirmable(lineage, revision, ExecutionRefused)
        captured_at = decode_datetime(json.loads(revision.content)["captured_at"])
        authorization = authority.authorize_grant(grant)  # who and when from the grant; never from the caller
        record = create_confirmed_baec_record(candidate, baec_id=grant.baec_id, captured_at=captured_at,
                                              confirmation=authorization)
        authorization_id = insert_confirmed_record(self._connection, record)
        self._grants.record_confirmation(grant, authorization_id=authorization_id, consumed_at=now)
        return ConfirmationResult(grant.baec_id, grant.proposal_id, grant.review_revision_id, grant.grant_id)
