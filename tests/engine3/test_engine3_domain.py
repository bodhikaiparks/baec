from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from baec_app.engine3.canonical import canonical_json, canonical_sha256
from baec_app.engine3.enums import (
    ActionAttempt,
    ArtifactAudience,
    ArtifactOrigin,
    ClaimSemanticClass,
    ClaimType,
    ExternalReferenceDisposition,
    PreparationChannel,
    PreparationGoal,
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
from baec_app.engine3.validator import validate_case


def test_stage_c_enum_vocabularies_are_exact():
    assert [x.value for x in PreparationChannel] == ["EMAIL", "CALL", "EMAIL_AND_CALL"]
    assert [x.value for x in PreparationGoal] == [
        "CHECK_CONDITION_STILL_RELEVANT",
        "ASK_WHETHER_TO_REVISIT_PRIOR_DISCUSSION",
        "ASK_WHAT_HAS_CHANGED",
        "UNDERSTAND_WHETHER_EVALUATION_REOPENED",
    ]
    assert [x.value for x in ExternalReferenceDisposition] == [
        "DIRECT_REFERENCE_ALLOWED", "PARAPHRASE_ALLOWED", "INTERNAL_ONLY", "UNKNOWN"
    ]
    assert [x.value for x in SourceIdentityDisclosure] == ["ALLOWED", "NOT_ALLOWED", "UNKNOWN"]


def test_domain_records_are_frozen():
    item = GroundingItem("G", "fact")
    with pytest.raises(FrozenInstanceError):
        item.exact_value = "changed"
    case = ValidationCase()
    with pytest.raises(FrozenInstanceError):
        case.empirical_validation_claim = True


def test_canonical_hash_is_exact_and_stable():
    obj = {
        "decimal": Decimal("9.00"),
        "time": datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc),
        "text": "  Exact Text\\r\\n",
        "enum": PreparationChannel.EMAIL,
    }
    first = canonical_sha256(obj)
    assert first == canonical_sha256(obj)
    assert '"decimal":"9.00"' in canonical_json(obj)
    assert "  Exact Text\\\\r\\\\n" in canonical_json(obj)


def _artifact(item, *, use=ReferenceUseMode.DIRECT, source=False):
    segment = DraftSegment(
        "S", "fact", ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.OTHER_FACT,
        grounding_refs=("G",), reference_use_mode=use,
        source_identity_refs=("G",) if source else (),
        claim_field=ClaimField.OTHER,
    )
    return PreparationArtifact("A", ArtifactAudience.BUYER_FACING, ArtifactOrigin.AI_INFERENCE, (segment,))


@pytest.mark.parametrize(
    "disposition,use,allowed",
    [
        (ExternalReferenceDisposition.DIRECT_REFERENCE_ALLOWED, ReferenceUseMode.DIRECT, True),
        (ExternalReferenceDisposition.DIRECT_REFERENCE_ALLOWED, ReferenceUseMode.PARAPHRASE, True),
        (ExternalReferenceDisposition.PARAPHRASE_ALLOWED, ReferenceUseMode.PARAPHRASE, True),
        (ExternalReferenceDisposition.PARAPHRASE_ALLOWED, ReferenceUseMode.DIRECT, False),
        (ExternalReferenceDisposition.INTERNAL_ONLY, ReferenceUseMode.DIRECT, False),
        (ExternalReferenceDisposition.UNKNOWN, ReferenceUseMode.PARAPHRASE, False),
    ],
)
def test_disclosure_matrix(disposition, use, allowed):
    item = GroundingItem("G", "fact", disposition=disposition)
    result = validate_case(ValidationCase(grounding_items=(item,), artifact=_artifact(item, use=use)))
    assert result.allowed is allowed


def test_source_identity_is_independent_from_fact_permission():
    item = GroundingItem(
        "G", "fact",
        disposition=ExternalReferenceDisposition.DIRECT_REFERENCE_ALLOWED,
        source_identity_disclosure=SourceIdentityDisclosure.NOT_ALLOWED,
    )
    result = validate_case(ValidationCase(grounding_items=(item,), artifact=_artifact(item, source=True)))
    assert result.primary_reason_code == "E3C-DISC-003"


def test_possible_correspondence_never_enters_v1():
    result = validate_case(ValidationCase(upstream=UpstreamEligibility("POSSIBLE_CORRESPONDENCE")))
    assert result.primary_reason_code == "E3C-IN-001"


@pytest.mark.parametrize(
    "action,code",
    [
        (ActionAttempt.OUTREACH_EXECUTION, "E3C-ACT-001"),
        (ActionAttempt.CRM_ACTION, "E3C-ACT-002"),
        (ActionAttempt.ACCOUNT_STATE_CHANGE, "E3C-ACT-003"),
    ],
)
def test_action_boundary_is_hard_stop(action, code):
    result = validate_case(ValidationCase(action_attempt=action))
    assert result.primary_reason_code == code


def test_context_change_beats_wording_validation():
    bad = DraftSegment("S", "ready to switch", ClaimType.FACTUAL_ASSERTION,
                       ClaimSemanticClass.BUYER_PURCHASE_OR_SWITCH_INTENT)
    art = PreparationArtifact("A", ArtifactAudience.BUYER_FACING, ArtifactOrigin.AI_INFERENCE, (bad,))
    case = ValidationCase(context=ContextState(evidence_changed=True), artifact=art)
    assert validate_case(case).primary_reason_code == "E3C-CTX-001"
