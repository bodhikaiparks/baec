"""The only place in the application layer that constructs HumanAuthorization.

Every field comes from a redeemed request and the approval the gate issued
for it: who and when from the approval, what from the stored request. A
caller cannot supply or override any of them.

The resulting HumanAuthorization still expresses a domain requirement, not
proof of identity: authorized_by is the session's self-asserted actor.

Phase 7F-B adds one second, narrow construction path: authorize_grant builds the
CONFIRM_BAEC authorization for a persisted human authorization grant whose digest
still covers its binding. Who and when come from the grant (its self-asserted
actor label and its issuance time), the subject from the BAEC id the grant names.
The executor supplies nothing. This path never goes through the Phase 4 gate.
"""

from __future__ import annotations

from baec_app.application.approval import HumanApproval
from baec_app.application.errors import ApplicationValidationError, RequestNotRecognized
from baec_app.application.requests import ApprovalRequest
from baec_app.data.authorization_grants import GrantRecord
from baec_app.domain.enums import AuthorizationAction
from baec_app.domain.models import HumanAuthorization


def authorize(request: ApprovalRequest, approval: HumanApproval) -> HumanAuthorization:
    """Build the authorization for a request that has just been redeemed with this approval."""
    if type(request) is not ApprovalRequest:
        raise ApplicationValidationError("authorize requires the redeemed ApprovalRequest")
    if type(approval) is not HumanApproval:
        raise ApplicationValidationError("authorize requires the HumanApproval that was redeemed")
    if approval.request_id != request.request_id or approval.digest != request.digest:
        raise RequestNotRecognized("the approval was not issued for this request")
    # Session binding is enforced by the gate registry, not the content digest;
    # keep it when turning the redeemed request into a domain authorization.
    if approval.session_id != request.session_id:
        raise RequestNotRecognized("the approval was issued in a different session from the request")
    return HumanAuthorization(
        authorized_by=approval.actor_id,
        authorized_at=approval.approved_at,
        action=request.action,
        subject_id=request.subject_id,
        target_state=request.target_state,
    )


def authorize_grant(grant: GrantRecord) -> HumanAuthorization:
    """The CONFIRM_BAEC authorization recorded by one persisted grant (Phase 7F-B). Every field comes from the grant."""
    if type(grant) is not GrantRecord:
        raise ApplicationValidationError("authorize_grant requires a persisted GrantRecord")
    if grant.action != AuthorizationAction.CONFIRM_BAEC.value or not grant.binding_is_intact():
        raise RequestNotRecognized("the grant does not authorize this confirmation")
    return HumanAuthorization(
        authorized_by=grant.actor_label,
        authorized_at=grant.issued_at,
        action=AuthorizationAction.CONFIRM_BAEC,
        subject_id=grant.baec_id,
    )
