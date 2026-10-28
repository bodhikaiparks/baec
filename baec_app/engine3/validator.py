from __future__ import annotations

from .claims import claim_issues, state_and_intent_issues
from .context import validate_context
from .disclosure import disclosure_issues
from .eligibility import validate_input_eligibility
from .enums import ActionAttempt
from .models import ValidationCase, ValidationIssue, ValidationReport
from .reason_codes import ReasonCode


def _action_issues(action: ActionAttempt) -> tuple[ValidationIssue, ...]:
    if action is ActionAttempt.OUTREACH_EXECUTION:
        return (ValidationIssue(ReasonCode.OUTREACH_EXECUTION_NOT_SUPPORTED),)
    if action is ActionAttempt.CRM_ACTION:
        return (ValidationIssue(ReasonCode.CRM_ACTION_NOT_SUPPORTED),)
    if action is ActionAttempt.ACCOUNT_STATE_CHANGE:
        return (ValidationIssue(ReasonCode.ACCOUNT_STATE_CHANGE_NOT_SUPPORTED),)
    return ()


def validate_case(case: ValidationCase) -> ValidationReport:
    # Global precedence: stale context before eligibility; then disclosure,
    # claim grounding, buyer-state/intent, action, research boundary.
    context = validate_context(case.context)
    if not context.allowed:
        return context

    eligibility = validate_input_eligibility(case.upstream)
    if not eligibility.allowed:
        return eligibility

    artifact = case.artifact
    if artifact is not None:
        grounding = {g.grounding_item_id: g for g in case.grounding_items}

        issues = disclosure_issues(artifact, grounding)
        if issues:
            return ValidationReport(False, issues)

        issues = claim_issues(artifact, grounding)
        if issues:
            return ValidationReport(False, issues)

        issues = state_and_intent_issues(artifact, case.prohibited_score_requested)
        if issues:
            return ValidationReport(False, issues)
    elif case.prohibited_score_requested:
        return ValidationReport(False, (ValidationIssue(ReasonCode.PROHIBITED_SCORE_OR_PROBABILITY),))

    issues = _action_issues(case.action_attempt)
    if issues:
        return ValidationReport(False, issues)

    if case.empirical_validation_claim:
        return ValidationReport(False, (ValidationIssue(ReasonCode.EMPIRICAL_VALIDATION_CLAIM_PROHIBITED),))

    return ValidationReport(True, ())
