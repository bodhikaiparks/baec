from .enums import HUMAN_VERIFIED_CORRESPONDENCE
from .models import UpstreamEligibility, ValidationIssue, ValidationReport
from .reason_codes import ReasonCode


def validate_input_eligibility(value: UpstreamEligibility) -> ValidationReport:
    issues = []
    if not value.lineage_reconstructable:
        issues.append(ValidationIssue(ReasonCode.UPSTREAM_LINEAGE_UNRECONSTRUCTABLE))
    if value.outcome != HUMAN_VERIFIED_CORRESPONDENCE:
        issues.append(ValidationIssue(ReasonCode.OUTCOME_NOT_HUMAN_VERIFIED))
    if not value.baec_current:
        issues.append(ValidationIssue(ReasonCode.BAEC_NOT_CURRENT))
    if value.revalidation_required:
        issues.append(ValidationIssue(ReasonCode.BAEC_REVALIDATION_REQUIRED))
    if value.review_superseded:
        issues.append(ValidationIssue(ReasonCode.CORRESPONDENCE_REVIEW_SUPERSEDED))
    if not value.required_evidence_usable:
        issues.append(ValidationIssue(ReasonCode.REQUIRED_EVIDENCE_NOT_USABLE))
    if value.human_preparation_block:
        issues.append(ValidationIssue(ReasonCode.HUMAN_PREPARATION_BLOCK))
    if not value.preparation_request_present:
        issues.append(ValidationIssue(ReasonCode.PREPARATION_REQUEST_REQUIRED))

    # Locked input-family precedence is reason-code order, except unreconstructable
    # lineage is structural enough to lead when present.
    order = {
        ReasonCode.UPSTREAM_LINEAGE_UNRECONSTRUCTABLE: 0,
        ReasonCode.OUTCOME_NOT_HUMAN_VERIFIED: 1,
        ReasonCode.BAEC_NOT_CURRENT: 2,
        ReasonCode.BAEC_REVALIDATION_REQUIRED: 3,
        ReasonCode.CORRESPONDENCE_REVIEW_SUPERSEDED: 4,
        ReasonCode.REQUIRED_EVIDENCE_NOT_USABLE: 5,
        ReasonCode.HUMAN_PREPARATION_BLOCK: 6,
        ReasonCode.PREPARATION_REQUEST_REQUIRED: 7,
    }
    issues.sort(key=lambda i: order[i.reason_code])
    return ValidationReport(not issues, tuple(issues))
