from __future__ import annotations

from .enums import ArtifactAudience, ArtifactOrigin, ClaimSemanticClass, ClaimType, ReferenceUseMode
from .models import ClaimField, GroundingItem, PreparationArtifact, ValidationIssue
from .reason_codes import ReasonCode


def _ungrounded_code(segment) -> ReasonCode:
    if segment.claim_field is ClaimField.THRESHOLD:
        return ReasonCode.THRESHOLD_INVENTED
    if segment.claim_field is ClaimField.DATE:
        return ReasonCode.DATE_INVENTED
    if segment.claim_field is ClaimField.ROLE_OR_RELATIONSHIP:
        return ReasonCode.ROLE_OR_RELATIONSHIP_INVENTED
    if segment.claim_field is ClaimField.SELLER_CAPABILITY:
        return ReasonCode.SELLER_CAPABILITY_CLAIM_UNGROUNDED
    if segment.claim_field is ClaimField.COMPETITOR:
        return ReasonCode.COMPETITOR_CLAIM_UNGROUNDED
    if segment.claim_field is ClaimField.EVENT_MAGNITUDE:
        return ReasonCode.EVENT_MAGNITUDE_UNGROUNDED
    return ReasonCode.FACTUAL_ASSERTION_UNGROUNDED


def claim_issues(
    artifact: PreparationArtifact,
    grounding_by_id: dict[str, GroundingItem],
) -> tuple[ValidationIssue, ...]:
    issues = []
    for segment in artifact.segments:
        known_refs = tuple(ref for ref in segment.grounding_refs if ref in grounding_by_id)

        if segment.source_instruction_ref is not None:
            issues.append(ValidationIssue(ReasonCode.SOURCE_CONTENT_TREATED_AS_INSTRUCTION, segment.segment_id))
            continue

        if segment.claim_type is ClaimType.QUESTION and segment.question_has_premise and not known_refs:
            issues.append(ValidationIssue(ReasonCode.QUESTION_CONTAINS_UNSUPPORTED_PREMISE, segment.segment_id))
            continue

        if segment.claim_type in (ClaimType.FACTUAL_ASSERTION, ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE):
            if not known_refs:
                code = _ungrounded_code(segment)
                if artifact.origin is ArtifactOrigin.HUMAN_EDITED:
                    code = ReasonCode.HUMAN_EDIT_UNGROUNDED
                issues.append(ValidationIssue(code, segment.segment_id))
                continue

        if known_refs and segment.declared_value is not None:
            items = [grounding_by_id[r] for r in known_refs]
            if any(item.approximate for item in items) and segment.reference_use_mode is ReferenceUseMode.DIRECT:
                if all(segment.declared_value != item.exact_value for item in items):
                    issues.append(
                        ValidationIssue(ReasonCode.APPROXIMATE_EVIDENCE_OVERSTATED_AS_EXACT, segment.segment_id)
                    )
                    continue

            # Exact factual-value claims must match at least one cited item.
            if (
                segment.claim_type is ClaimType.FACTUAL_ASSERTION
                and segment.claim_field is not ClaimField.NONE
                and segment.reference_use_mode is ReferenceUseMode.DIRECT
                and all(segment.declared_value != item.exact_value for item in items)
            ):
                issues.append(ValidationIssue(_ungrounded_code(segment), segment.segment_id))

    return tuple(issues)


def state_and_intent_issues(
    artifact: PreparationArtifact,
    prohibited_score_requested: bool,
) -> tuple[ValidationIssue, ...]:
    issues = []
    for segment in artifact.segments:
        if segment.claim_type is ClaimType.QUESTION and segment.question_has_premise:
            # Unsupported premises are handled before state classification.
            pass
        if segment.prescribes_buyer_choice:
            issues.append(ValidationIssue(ReasonCode.BUYER_COMMERCIAL_CHOICE_PRESCRIBED, segment.segment_id))
            continue
        if segment.semantic_class is ClaimSemanticClass.BUYER_CURRENT_EVALUATION_STATE:
            issues.append(ValidationIssue(ReasonCode.CURRENT_EVALUATION_ASSERTED, segment.segment_id))
        elif segment.semantic_class is ClaimSemanticClass.BUYER_PURCHASE_OR_SWITCH_INTENT:
            issues.append(ValidationIssue(ReasonCode.PURCHASE_OR_SWITCH_INTENT_ASSERTED, segment.segment_id))
        elif segment.semantic_class is ClaimSemanticClass.OPPORTUNITY_STATUS:
            issues.append(ValidationIssue(ReasonCode.OPPORTUNITY_STATUS_ASSERTED, segment.segment_id))
        elif (
            segment.semantic_class is ClaimSemanticClass.DEADLINE_OR_URGENCY
            and not segment.grounding_refs
        ):
            issues.append(ValidationIssue(ReasonCode.URGENCY_INVENTED, segment.segment_id))

    if prohibited_score_requested:
        issues.append(ValidationIssue(ReasonCode.PROHIBITED_SCORE_OR_PROBABILITY))
    return tuple(issues)
