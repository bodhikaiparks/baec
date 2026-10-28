from .models import ContextState, ValidationIssue, ValidationReport
from .reason_codes import ReasonCode


def validate_context(value: ContextState) -> ValidationReport:
    checks = (
        (value.evidence_changed, ReasonCode.EVIDENCE_CONTEXT_CHANGED),
        (value.disclosure_changed, ReasonCode.DISCLOSURE_CONTEXT_CHANGED),
        (value.baec_currentness_changed, ReasonCode.BAEC_CURRENTNESS_CHANGED),
        (value.review_changed, ReasonCode.CORRESPONDENCE_REVIEW_CHANGED),
        (value.request_changed, ReasonCode.PREPARATION_REQUEST_CHANGED),
        (not value.digest_match, ReasonCode.CONTEXT_DIGEST_MISMATCH),
    )
    issues = tuple(ValidationIssue(code) for triggered, code in checks if triggered)
    return ValidationReport(not issues, issues)
