"""Closed MCP wire contracts (Phase 5).

Every model is a Pydantic v2 model with strict=True, extra="forbid", and
frozen=True. Under the installed MCP SDK (2.2.0) strict validation runs in
Python mode on decoded JSON, so enum and timestamp fields are plain strings
here (Phase 5 checkpoint decision, Option A):

* enum fields accept exactly the domain enum values, generated from the
  domain enums so they cannot drift;
* timestamps are strings that must carry an explicit offset (or Z) and parse
  as a real aware ISO-8601 datetime; validation returns the original string;
* threshold numbers are decimal strings, never JSON numbers, so "10" and
  "10.0" stay distinct.

Conversion between these wire values and domain objects happens only in
adapters.py. This module imports no application code.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from baec_app.domain import enums


def _values_of(enum_class: type[Enum]):
    """A Literal of exactly the enum's string values, in definition order."""
    return Literal[tuple(member.value for member in enum_class)]


AccountStateValue = _values_of(enums.AccountState)
ArticulationOriginValue = _values_of(enums.ArticulationOrigin)
AuthorizationActionValue = _values_of(enums.AuthorizationAction)
BaecClassificationValue = _values_of(enums.BaecClassification)
BaecCriterionValue = _values_of(enums.BaecCriterion)
ClassificationReasonKindValue = _values_of(enums.ClassificationReasonKind)
CriterionFindingValue = _values_of(enums.CriterionFinding)
ElicitationModeValue = _values_of(enums.ElicitationMode)
NoPlausiblePathGroundValue = _values_of(enums.NoPlausiblePathGround)
ProvenanceCategoryValue = _values_of(enums.ProvenanceCategory)
ReviewAnswerValue = _values_of(enums.ReviewAnswer)
StalenessStatusValue = _values_of(enums.StalenessStatus)
ThresholdComparatorValue = _values_of(enums.ThresholdComparator)
TransitionRejectionKindValue = _values_of(enums.TransitionRejectionKind)
TransitionUnresolvedKindValue = _values_of(enums.TransitionUnresolvedKind)

ENUM_VALUE_TYPES = {
    enums.AccountState: AccountStateValue,
    enums.ArticulationOrigin: ArticulationOriginValue,
    enums.AuthorizationAction: AuthorizationActionValue,
    enums.BaecClassification: BaecClassificationValue,
    enums.BaecCriterion: BaecCriterionValue,
    enums.ClassificationReasonKind: ClassificationReasonKindValue,
    enums.CriterionFinding: CriterionFindingValue,
    enums.ElicitationMode: ElicitationModeValue,
    enums.NoPlausiblePathGround: NoPlausiblePathGroundValue,
    enums.ProvenanceCategory: ProvenanceCategoryValue,
    enums.ReviewAnswer: ReviewAnswerValue,
    enums.StalenessStatus: StalenessStatusValue,
    enums.ThresholdComparator: ThresholdComparatorValue,
    enums.TransitionRejectionKind: TransitionRejectionKindValue,
    enums.TransitionUnresolvedKind: TransitionUnresolvedKindValue,
}

# --- constrained scalar types ---------------------------------------------------

IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


def _no_dot_dot(value: str) -> str:
    if ".." in value:
        raise ValueError("identifier must not contain '..'")
    return value


# Accounts, interactions and BAEC records. Never normalized: a value either
# matches exactly or is refused.
Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN), AfterValidator(_no_dot_dot)]

DecimalString = Annotated[str, StringConstraints(pattern=r"^-?\d+(\.\d+)?$")]

_TIMESTAMP_SHAPE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
)


def _aware_iso8601(value: str) -> str:
    """Explicit offset or Z, a real calendar datetime, and aware; returns the original string."""
    if not _TIMESTAMP_SHAPE.match(value):
        raise ValueError("timestamp must be ISO-8601 date and time with an explicit offset or Z")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("timestamp is not a valid ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


Timestamp = Annotated[str, AfterValidator(_aware_iso8601)]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# --- input contracts (used by the 5C preview tools) -----------------------------------


class EvidenceExcerptIn(WireModel):
    text: str
    provenance: ProvenanceCategoryValue
    source_id: Identifier


class CriterionAssessmentIn(WireModel):
    criterion: BaecCriterionValue
    finding: CriterionFindingValue
    evidence: list[EvidenceExcerptIn]
    rationale: str | None = None


class StringencyIn(WireModel):
    verbatim_text: str
    comparator: ThresholdComparatorValue | None
    numeric_value: DecimalString | None = None
    unit: str | None = None
    qualitative_term: str | None = None
    recurrence_text: str | None = None
    timing_text: str | None = None


class BaecCandidateIn(WireModel):
    account_id: Identifier
    source_interaction_id: Identifier
    source_excerpt: EvidenceExcerptIn
    assessments: list[CriterionAssessmentIn]
    articulation_origin: ArticulationOriginValue
    elicitation_mode: ElicitationModeValue
    buyer_exact_statement: str | None = None
    buyer_role: str | None = None
    stringency: StringencyIn | None = None


class EvaluationEvidenceIn(WireModel):
    account_id: Identifier
    evidence: EvidenceExcerptIn
    observed_at: Timestamp


class NonEvaluationEvidenceIn(WireModel):
    account_id: Identifier
    evidence: EvidenceExcerptIn
    observed_at: Timestamp


class PreviewClassificationArgs(WireModel):
    candidate: BaecCandidateIn


class PreviewDormantArgs(WireModel):
    account_id: Identifier
    baec_id: Identifier
    judgment_id: int
    non_evaluation_evidence: NonEvaluationEvidenceIn | None = None


class PreviewActiveArgs(WireModel):
    account_id: Identifier
    evaluation_evidence: EvaluationEvidenceIn | None


class PreviewNoPlausiblePathArgs(WireModel):
    account_id: Identifier
    ground: NoPlausiblePathGroundValue | None
    reason: str | None
    non_evaluation_evidence: NonEvaluationEvidenceIn | None = None
    basis_interaction_id: Identifier | None = None


# --- output contracts -------------------------------------------------------------


class AccountView(WireModel):
    account_id: str
    name: str
    state: AccountStateValue | None


class AccountList(WireModel):
    accounts: list[AccountView]


class InteractionView(WireModel):
    interaction_id: str
    account_id: str
    occurred_at: Timestamp
    text: str


class InteractionList(WireModel):
    interactions: list[InteractionView]


class EvidenceExcerptView(WireModel):
    text: str
    provenance: ProvenanceCategoryValue
    source_id: str


class CriterionAssessmentView(WireModel):
    criterion: BaecCriterionValue
    finding: CriterionFindingValue
    evidence: list[EvidenceExcerptView]
    rationale: str | None


class StringencyView(WireModel):
    verbatim_text: str
    comparator: ThresholdComparatorValue | None
    numeric_value: DecimalString | None
    unit: str | None
    qualitative_term: str | None
    recurrence_text: str | None
    timing_text: str | None


class BaecCandidateView(WireModel):
    account_id: str
    source_interaction_id: str
    source_excerpt: EvidenceExcerptView
    assessments: list[CriterionAssessmentView]
    articulation_origin: ArticulationOriginValue
    elicitation_mode: ElicitationModeValue
    buyer_exact_statement: str | None
    buyer_role: str | None
    stringency: StringencyView | None


class AuthorizationView(WireModel):
    authorized_by: str
    authorized_at: Timestamp
    action: AuthorizationActionValue
    subject_id: str
    target_state: AccountStateValue | None


class AiDerivedTextView(WireModel):
    """AI-derived wording, kept separate from source text (RC-32, RC-33)."""

    text: str
    generated_at: Timestamp
    model: str | None


class BaecRecordView(WireModel):
    baec_id: str
    captured_at: Timestamp
    candidate: BaecCandidateView
    classification: BaecClassificationValue
    classification_reason: str | None
    staleness_status: StalenessStatusValue | None
    confirmation: AuthorizationView | None
    ai_derived_normalized_condition: AiDerivedTextView | None


class BaecRecordList(WireModel):
    baec_records: list[BaecRecordView]


class DormancyJudgmentView(WireModel):
    judgment_id: int
    baec_id: str
    plausibility: ReviewAnswerValue
    addressability: ReviewAnswerValue
    notes: str | None
    authorization: AuthorizationView


class DormancyJudgmentList(WireModel):
    dormancy_judgments: list[DormancyJudgmentView]


class StateEvidenceView(WireModel):
    account_id: str
    evidence: EvidenceExcerptView
    observed_at: Timestamp


class TransitionHistoryEntryView(WireModel):
    transition_id: int
    account_id: str
    from_state: AccountStateValue | None
    to_state: AccountStateValue
    authorization: AuthorizationView
    recorded_at: Timestamp
    baec_id: str | None
    judgment_id: int | None
    evaluation_evidence: StateEvidenceView | None
    non_evaluation_evidence: StateEvidenceView | None
    ground: NoPlausiblePathGroundValue | None
    reason: str | None
    basis_interaction_id: str | None
    unresolved: list[TransitionUnresolvedKindValue]


class TransitionHistoryList(WireModel):
    transitions: list[TransitionHistoryEntryView]


class ClassificationReasonView(WireModel):
    kind: ClassificationReasonKindValue
    criterion: BaecCriterionValue | None


class ClassificationPreviewView(WireModel):
    classification: BaecClassificationValue
    reasons: list[ClassificationReasonView]
    reason_text: str | None
    confirmable: bool


class TransitionPreviewView(WireModel):
    account_id: str
    from_state: AccountStateValue | None
    to_state: AccountStateValue
    allowed: bool
    rejections: list[TransitionRejectionKindValue]
    rejection_text: str | None
    unresolved: list[TransitionUnresolvedKindValue]
    ground: NoPlausiblePathGroundValue | None
    reason: str | None
