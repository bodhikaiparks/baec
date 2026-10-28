"""Test-only adapter from locked Stage D synthetic fixtures to Engine 3 domain records."""

from __future__ import annotations

import json
from pathlib import Path

from baec_app.engine3.enums import (
    ActionAttempt,
    ArtifactAudience,
    ArtifactOrigin,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    ReferenceUseMode,
    SourceIdentityDisclosure,
)
from baec_app.engine3.models import (
    ClaimField,
    ContextState,
    DraftSegment,
    GroundingItem,
    PreparationArtifact,
    UpstreamEligibility,
    ValidationCase,
)

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixtures" / "ENGINE3_STAGE_D_CONFORMANCE_INPUTS.json"
ORACLE_PATH = HERE / "fixtures" / "ENGINE3_STAGE_C_CORPUS_VALIDATION_ORACLE_LOCKED.json"


def load_inputs():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_oracle():
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


def _grounding(d):
    return GroundingItem(
        grounding_item_id=d["grounding_item_id"],
        exact_value=d["exact_value"],
        disposition=ExternalReferenceDisposition(d["disposition"]),
        source_identity_disclosure=SourceIdentityDisclosure(d["source_identity_disclosure"]),
        is_buyer_statement=d["is_buyer_statement"],
        adversity_related=d["adversity_related"],
        adversity_context_reviewed=d["adversity_context_reviewed"],
        approximate=d["approximate"],
        approved_specificity=d["approved_specificity"],
    )


def _segment(d):
    return DraftSegment(
        segment_id=d["segment_id"],
        text=d["text"],
        claim_type=ClaimType(d["claim_type"]),
        semantic_class=ClaimSemanticClass(d["semantic_class"]),
        grounding_refs=tuple(d["grounding_refs"]),
        reference_use_mode=ReferenceUseMode(d["reference_use_mode"]),
        source_identity_refs=tuple(d["source_identity_refs"]),
        claim_field=ClaimField(d["claim_field"]),
        declared_value=d["declared_value"],
        specificity=d["specificity"],
        question_has_premise=d["question_has_premise"],
        source_instruction_ref=d["source_instruction_ref"],
        prescribes_buyer_choice=d["prescribes_buyer_choice"],
    )


def build_case(d):
    upstream = UpstreamEligibility(**d["upstream"])
    context = ContextState(**d["context"])
    grounding = tuple(_grounding(x) for x in d["grounding_items"])
    artifact = None
    if d["artifact"] is not None:
        a = d["artifact"]
        artifact = PreparationArtifact(
            artifact_id=a["artifact_id"],
            audience=ArtifactAudience(a["audience"]),
            origin=ArtifactOrigin(a["origin"]),
            segments=tuple(_segment(x) for x in a["segments"]),
        )
    return ValidationCase(
        upstream=upstream,
        context=context,
        grounding_items=grounding,
        artifact=artifact,
        action_attempt=ActionAttempt(d["action_attempt"]),
        prohibited_score_requested=d["prohibited_score_requested"],
        empirical_validation_claim=d["empirical_validation_claim"],
    )
