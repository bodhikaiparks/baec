"""Enum vocabularies are fixed and strict (RC-12, RC-20, RC-21)."""

from enum import Enum

import pytest

from baec_app.domain import enums

EXPECTED_MEMBERS = {
    "AccountState": ["ACTIVE_OPPORTUNITY", "CONDITIONALLY_DORMANT", "NO_PLAUSIBLE_PATH"],
    "BaecCriterion": [
        "PRESENT_NON_EVALUATION",
        "PROSPECTIVE_CONDITION",
        "BUYER_ARTICULATION",
        "EVALUATION_LINKAGE",
    ],
    "CriterionFinding": ["MET", "NOT_MET", "UNKNOWN"],
    "ArticulationOrigin": ["BUYER_GENERATED", "SELLER_SEEDED", "UNCERTAIN"],
    "BaecClassification": ["CONFIRMED_BAEC", "NOT_BAEC", "INSUFFICIENT_EVIDENCE"],
    "ElicitationMode": ["SPONTANEOUS", "CEE_ELICITED", "UNKNOWN"],
    "ProvenanceCategory": [
        "BUYER_FACT",
        "SELLER_OBSERVATION",
        "EXTERNAL_EVIDENCE",
        "AI_INFERENCE",
        "UNKNOWN",
    ],
    "ThresholdComparator": [
        "GREATER_THAN",
        "AT_LEAST",
        "LESS_THAN",
        "AT_MOST",
        "EXACTLY",
        "APPROXIMATELY",
        "QUALITATIVE_ONLY",
        "NONE_STATED",
    ],
    "StalenessStatus": ["CURRENT", "REVIEW_DUE", "STALE", "RETIRED"],
    "ReviewAnswer": ["YES", "NO", "UNKNOWN", "NOT_YET"],
    "AuthorizationAction": [
        "CONFIRM_BAEC",
        "RECORD_DORMANCY_JUDGMENT",
        "CHANGE_ACCOUNT_STATE",
    ],
    "ClassificationReasonKind": [
        "CRITERION_NOT_MET",
        "ORIGIN_SELLER_SEEDED",
        "CRITERION_UNKNOWN",
        "ORIGIN_UNCERTAIN",
    ],
    "NoPlausiblePathGround": [
        "NO_PLAUSIBLE_BAEC",
        "CONDITION_TOO_IMPLAUSIBLE",
        "CLEARLY_SELLER_UNADDRESSABLE",
        "OTHER",
    ],
    "TransitionUnresolvedKind": ["ADDRESSABILITY_UNKNOWN"],
    "TransitionRejectionKind": [
        "SAME_STATE",
        "AUTHORIZATION_MISSING",
        "AUTHORIZATION_WRONG_ACTION",
        "AUTHORIZATION_WRONG_SUBJECT",
        "AUTHORIZATION_WRONG_TARGET",
        "NON_EVALUATION_EVIDENCE_MISSING",
        "NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT",
        "BAEC_MISSING",
        "BAEC_NOT_CONFIRMED",
        "BAEC_WRONG_ACCOUNT",
        "BAEC_NOT_CURRENT",
        "JUDGMENT_MISSING",
        "JUDGMENT_WRONG_BAEC",
        "PLAUSIBILITY_NOT_YES",
        "ADDRESSABILITY_NO",
        "ADDRESSABILITY_NOT_YET",
        "EVALUATION_EVIDENCE_MISSING",
        "EVALUATION_EVIDENCE_WRONG_ACCOUNT",
        "GROUND_MISSING",
        "REASON_MISSING",
    ],
}

ENUM_NAMES = sorted(EXPECTED_MEMBERS)


def test_no_unlisted_enums_exist():
    """Adding an enum to the domain must be reflected here deliberately."""
    defined = sorted(
        name
        for name, obj in vars(enums).items()
        if isinstance(obj, type) and issubclass(obj, Enum) and obj is not Enum
    )
    assert defined == ENUM_NAMES


@pytest.mark.parametrize("name", ENUM_NAMES)
def test_enum_members_are_exactly_as_approved(name):
    """Member lists and their order are fixed; RC-12 and RC-20 allow no others."""
    assert [member.name for member in getattr(enums, name)] == EXPECTED_MEMBERS[name]


@pytest.mark.parametrize("name", ENUM_NAMES)
def test_enum_values_are_readable_strings_equal_to_names(name):
    for member in getattr(enums, name):
        assert member.value == member.name


@pytest.mark.parametrize("name", ENUM_NAMES)
def test_enums_are_strict_not_string_subclasses(name):
    """A raw string must never pass as a validated domain value."""
    for member in getattr(enums, name):
        assert not isinstance(member, str)
        assert member != member.value


@pytest.mark.parametrize("name", ENUM_NAMES)
def test_unsupported_value_is_rejected(name):
    with pytest.raises(ValueError):
        getattr(enums, name)("HOT_LEAD")


def test_rc20_rc21_exactly_three_account_states_and_no_unclassified_member():
    """RC-20: three states. RC-21: unclassified is None, not a member."""
    assert len(enums.AccountState) == 3
    for forbidden in ("UNCLASSIFIED", "NONE", "UNKNOWN", "PENDING"):
        assert forbidden not in enums.AccountState.__members__


@pytest.mark.parametrize(
    "check",
    [
        lambda: enums.ClassificationReasonKind.ORIGIN_UNCERTAIN != "ORIGIN_UNCERTAIN",
    ],
    ids=["raw string not equal to reason kind"],
)
def test_raw_string_does_not_equal_enum_member(check):
    assert check()
