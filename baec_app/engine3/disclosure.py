from __future__ import annotations

from .enums import (
    ArtifactAudience,
    ClaimType,
    ExternalReferenceDisposition,
    ReferenceUseMode,
    SourceIdentityDisclosure,
)
from .models import GroundingItem, PreparationArtifact, ValidationIssue
from .reason_codes import ReasonCode


def disclosure_issues(
    artifact: PreparationArtifact,
    grounding_by_id: dict[str, GroundingItem],
) -> tuple[ValidationIssue, ...]:
    if artifact.audience is ArtifactAudience.INTERNAL:
        return ()

    issues = []
    for segment in artifact.segments:
        for ref in segment.grounding_refs:
            item = grounding_by_id.get(ref)
            if item is None:
                continue
            if item.is_buyer_statement and item.disposition in (
                ExternalReferenceDisposition.INTERNAL_ONLY,
                ExternalReferenceDisposition.UNKNOWN,
            ):
                issues.append(ValidationIssue(ReasonCode.BUYER_STATEMENT_NOT_PERMITTED, segment.segment_id))
                continue
            if item.disposition is ExternalReferenceDisposition.INTERNAL_ONLY:
                issues.append(ValidationIssue(ReasonCode.FACT_INTERNAL_ONLY, segment.segment_id))
                continue
            if item.disposition is ExternalReferenceDisposition.UNKNOWN:
                issues.append(ValidationIssue(ReasonCode.FACT_PERMISSION_UNKNOWN, segment.segment_id))
                continue
            if (
                segment.reference_use_mode is ReferenceUseMode.DIRECT
                and item.disposition is ExternalReferenceDisposition.PARAPHRASE_ALLOWED
            ):
                code = (
                    ReasonCode.DIRECT_QUOTE_EXCEEDS_PARAPHRASE_PERMISSION
                    if segment.claim_type is ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE
                    else ReasonCode.SPECIFICITY_EXCEEDS_PERMISSION
                )
                issues.append(ValidationIssue(code, segment.segment_id))
            if segment.specificity > item.approved_specificity:
                issues.append(ValidationIssue(ReasonCode.SPECIFICITY_EXCEEDS_PERMISSION, segment.segment_id))
            if item.adversity_related and not item.adversity_context_reviewed:
                issues.append(ValidationIssue(ReasonCode.ADVERSITY_CONTEXT_REVIEW_REQUIRED, segment.segment_id))

        for ref in segment.source_identity_refs:
            item = grounding_by_id.get(ref)
            if item is None or item.source_identity_disclosure is not SourceIdentityDisclosure.ALLOWED:
                issues.append(ValidationIssue(ReasonCode.SOURCE_IDENTITY_NOT_PERMITTED, segment.segment_id))

    # Buyer statement with no permission is best represented by a grounding item;
    # if the item is absent entirely, claim grounding handles it.
    return tuple(issues)
