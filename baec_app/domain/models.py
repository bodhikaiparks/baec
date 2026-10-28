"""Domain models for BAEC Trigger Intelligence.

Every model is a frozen dataclass that validates itself when created, so an
object that breaks a rule cannot exist. Rule references (RC-xx) point to
docs/RESEARCH_CONTRACT.md.

Field comments use three markers:
    SOURCE   evidence as captured; never AI-written, never altered
    DERIVED  interpretation, normalization, or judgment
    ID       identifier or metadata

This module stores and validates. It does not compute classifications
(baec_rules.py) or decide state transitions (state_machine.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    ThresholdComparator,
)


class DomainValidationError(ValueError):
    """Raised when a domain object would break a research-contract rule."""


# --- validation helpers -----------------------------------------------------


def _require_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field} must be a non-empty string")


def _require_optional_text(value: object, field: str) -> None:
    if value is not None:
        _require_text(value, field)


def _require_instance(value: object, expected: type, field: str) -> None:
    # Strict: a raw string such as "MET" is not accepted in place of an enum.
    if not isinstance(value, expected):
        raise DomainValidationError(
            f"{field} must be {expected.__name__}, got {type(value).__name__}"
        )


def _require_aware(value: object, field: str) -> None:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field} must be timezone-aware")


_NUMERIC_COMPARATORS = frozenset(
    {
        ThresholdComparator.GREATER_THAN,
        ThresholdComparator.AT_LEAST,
        ThresholdComparator.LESS_THAN,
        ThresholdComparator.AT_MOST,
        ThresholdComparator.EXACTLY,
        ThresholdComparator.APPROXIMATELY,
    }
)

_EVIDENCE_PROVENANCE = frozenset(
    {
        ProvenanceCategory.BUYER_FACT,
        ProvenanceCategory.SELLER_OBSERVATION,
        ProvenanceCategory.EXTERNAL_EVIDENCE,
    }
)

# C1-C4 concern what the buyer communicated in an interaction. A later
# external report cannot establish any of them.
_CRITERION_PROVENANCE = frozenset(
    {ProvenanceCategory.BUYER_FACT, ProvenanceCategory.SELLER_OBSERVATION}
)

_EVALUATION_PROVENANCE = frozenset(
    {ProvenanceCategory.BUYER_FACT, ProvenanceCategory.SELLER_OBSERVATION}
)


# --- evidence ---------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceExcerpt:
    """One piece of evidence with its provenance (RC-32, RC-33).

    AI inference and unknown-origin text cannot be evidence.
    """

    text: str  # SOURCE
    provenance: ProvenanceCategory  # ID
    source_id: str  # ID; an interaction, or later an external source

    def __post_init__(self) -> None:
        _require_text(self.text, "EvidenceExcerpt.text")
        _require_instance(
            self.provenance, ProvenanceCategory, "EvidenceExcerpt.provenance"
        )
        _require_text(self.source_id, "EvidenceExcerpt.source_id")
        if self.provenance not in _EVIDENCE_PROVENANCE:
            raise DomainValidationError(
                "evidence provenance cannot be "
                f"{self.provenance.value}; AI inference and unknown-origin "
                "text are not evidence"
            )


@dataclass(frozen=True)
class CriterionAssessment:
    """Finding for one constitutive criterion (RC-02, RC-05, RC-13)."""

    criterion: BaecCriterion  # ID
    finding: CriterionFinding  # DERIVED
    evidence: tuple[EvidenceExcerpt, ...] = ()  # SOURCE
    rationale: str | None = None  # DERIVED

    def __post_init__(self) -> None:
        _require_instance(
            self.criterion, BaecCriterion, "CriterionAssessment.criterion"
        )
        _require_instance(
            self.finding, CriterionFinding, "CriterionAssessment.finding"
        )
        _require_instance(self.evidence, tuple, "CriterionAssessment.evidence")
        for item in self.evidence:
            _require_instance(
                item, EvidenceExcerpt, "CriterionAssessment.evidence item"
            )
            if item.provenance not in _CRITERION_PROVENANCE:
                raise DomainValidationError(
                    f"{self.criterion.value}: criterion evidence must be "
                    "BUYER_FACT or SELLER_OBSERVATION, got "
                    f"{item.provenance.value}"
                )
        _require_optional_text(self.rationale, "CriterionAssessment.rationale")
        if self.finding is not CriterionFinding.UNKNOWN and not self.evidence:
            raise DomainValidationError(
                f"{self.criterion.value}: a finding of {self.finding.value} "
                "requires at least one evidence excerpt"
            )


# --- stringency -------------------------------------------------------------


@dataclass(frozen=True)
class StringencyExpression:
    """What the buyer said about how much change is required (RC-17, RC-18).

    This object exists only when the buyer gave some stringency, recurrence,
    or timing information. If there is none, the candidate's stringency is
    None.

    comparator meanings:
        a numeric member   buyer stated a number; numeric_value is required
        QUALITATIVE_ONLY   buyer used a word such as "significant", no number
        NONE_STATED        no threshold stated; recurrence or timing may exist
        None               threshold wording exists but cannot be safely
                           normalized; it is preserved in verbatim_text only
    """

    verbatim_text: str  # SOURCE
    comparator: ThresholdComparator | None  # DERIVED
    numeric_value: Decimal | None = None  # DERIVED (only ever the buyer's number)
    unit: str | None = None  # DERIVED
    qualitative_term: str | None = None  # SOURCE
    recurrence_text: str | None = None  # SOURCE
    timing_text: str | None = None  # SOURCE

    def __post_init__(self) -> None:
        _require_text(self.verbatim_text, "StringencyExpression.verbatim_text")
        if self.comparator is not None:
            _require_instance(
                self.comparator,
                ThresholdComparator,
                "StringencyExpression.comparator",
            )
        if self.numeric_value is not None:
            # Decimal only: floats cannot represent values like 0.1 exactly.
            _require_instance(
                self.numeric_value, Decimal, "StringencyExpression.numeric_value"
            )
            if not self.numeric_value.is_finite():
                raise DomainValidationError("numeric_value must be finite")
        _require_optional_text(self.unit, "StringencyExpression.unit")
        _require_optional_text(
            self.qualitative_term, "StringencyExpression.qualitative_term"
        )
        _require_optional_text(
            self.recurrence_text, "StringencyExpression.recurrence_text"
        )
        _require_optional_text(self.timing_text, "StringencyExpression.timing_text")

        if self.comparator in _NUMERIC_COMPARATORS:
            if self.numeric_value is None:
                raise DomainValidationError(
                    f"comparator {self.comparator.value} requires the "
                    "buyer's stated number"
                )
        elif self.numeric_value is not None:
            label = self.comparator.value if self.comparator else "None"
            raise DomainValidationError(
                f"comparator {label} cannot carry a number; no threshold "
                "may be invented"
            )

        if self.comparator is ThresholdComparator.QUALITATIVE_ONLY:
            if self.qualitative_term is None:
                raise DomainValidationError(
                    "QUALITATIVE_ONLY requires the buyer's qualitative term"
                )
        if self.comparator is ThresholdComparator.NONE_STATED:
            if self.qualitative_term is not None:
                raise DomainValidationError(
                    "NONE_STATED cannot carry a qualitative term; use "
                    "QUALITATIVE_ONLY"
                )
        if self.unit is not None and self.numeric_value is None:
            raise DomainValidationError("unit requires a numeric_value")


# --- AI-derived content -----------------------------------------------------


@dataclass(frozen=True)
class AiDerivedText:
    """Text written by AI, kept visibly separate from evidence (RC-33)."""

    text: str  # DERIVED
    generated_at: datetime  # ID
    model: str | None = None  # ID

    def __post_init__(self) -> None:
        _require_text(self.text, "AiDerivedText.text")
        _require_aware(self.generated_at, "AiDerivedText.generated_at")
        _require_optional_text(self.model, "AiDerivedText.model")


# --- candidate --------------------------------------------------------------


@dataclass(frozen=True)
class BaecCandidate:
    """A possible BAEC before classification.

    buyer_exact_statement is None when no verbatim quote exists. A paraphrase
    or seller note belongs in source_excerpt (marked SELLER_OBSERVATION) and
    must never be presented as
    the buyer's exact words. buyer_role is None when the source does not
    establish the role; a placeholder such as "Unknown" must not be stored
    as though it were evidence.
    """

    account_id: str  # ID
    source_interaction_id: str  # ID
    source_excerpt: EvidenceExcerpt  # SOURCE
    assessments: tuple[CriterionAssessment, ...]  # DERIVED
    articulation_origin: ArticulationOrigin  # DERIVED
    elicitation_mode: ElicitationMode  # DERIVED
    buyer_exact_statement: str | None = None  # SOURCE
    buyer_role: str | None = None  # SOURCE; None = role not established
    stringency: StringencyExpression | None = None

    def __post_init__(self) -> None:
        _require_text(self.account_id, "BaecCandidate.account_id")
        _require_text(
            self.source_interaction_id, "BaecCandidate.source_interaction_id"
        )
        _require_optional_text(self.buyer_role, "BaecCandidate.buyer_role")
        _require_instance(
            self.source_excerpt, EvidenceExcerpt, "BaecCandidate.source_excerpt"
        )
        if self.source_excerpt.provenance not in _CRITERION_PROVENANCE:
            raise DomainValidationError(
                "candidate source_excerpt must be BUYER_FACT or "
                f"SELLER_OBSERVATION, got {self.source_excerpt.provenance.value}"
            )
        _require_optional_text(
            self.buyer_exact_statement, "BaecCandidate.buyer_exact_statement"
        )
        # Exact, case-sensitive substring only. No normalization or fuzzy
        # matching: if the captured source does not contain the wording, the
        # statement must stay None.
        if (
            self.buyer_exact_statement is not None
            and self.buyer_exact_statement not in self.source_excerpt.text
        ):
            raise DomainValidationError(
                "buyer_exact_statement must appear verbatim in "
                "source_excerpt.text; otherwise leave it as None"
            )
        _require_instance(self.assessments, tuple, "BaecCandidate.assessments")
        for item in self.assessments:
            _require_instance(
                item, CriterionAssessment, "BaecCandidate.assessments item"
            )
        _require_instance(
            self.articulation_origin,
            ArticulationOrigin,
            "BaecCandidate.articulation_origin",
        )
        _require_instance(
            self.elicitation_mode, ElicitationMode, "BaecCandidate.elicitation_mode"
        )
        if self.stringency is not None:
            _require_instance(
                self.stringency, StringencyExpression, "BaecCandidate.stringency"
            )
        criteria = [a.criterion for a in self.assessments]
        if len(criteria) != len(BaecCriterion) or set(criteria) != set(BaecCriterion):
            raise DomainValidationError(
                "a candidate needs exactly one assessment for each of the "
                "four criteria"
            )
        # One candidate rests on one interaction. Later interactions and
        # external evidence cannot establish its criteria. Corroboration
        # across the buying center is a separate, deferred concept.
        excerpts = [("source_excerpt", self.source_excerpt)] + [
            (a.criterion.value, e) for a in self.assessments for e in a.evidence
        ]
        for label, excerpt in excerpts:
            if excerpt.source_id != self.source_interaction_id:
                raise DomainValidationError(
                    f"{label}: evidence source {excerpt.source_id!r} does not "
                    "match the candidate's source interaction "
                    f"{self.source_interaction_id!r}"
                )

    def assessment_for(self, criterion: BaecCriterion) -> CriterionAssessment:
        _require_instance(criterion, BaecCriterion, "assessment_for criterion")
        for item in self.assessments:
            if item.criterion is criterion:
                return item
        raise DomainValidationError(f"no assessment for {criterion.value}")


# --- human authorization ----------------------------------------------------


@dataclass(frozen=True)
class HumanAuthorization:
    """Record that a human approved one action on one subject (RC-31).

    This expresses a domain requirement. It is NOT the security boundary:
    nothing here proves a human acted. Later application, service, and MCP
    server code must establish that the authorization came from an actual
    human action. A model-supplied value is never proof of approval.

    A CHANGE_ACCOUNT_STATE authorization names its destination state and is
    valid only for that destination. It is not bound to the originating
    state. target_state is forbidden for every other action.
    """

    authorized_by: str  # ID
    authorized_at: datetime  # ID
    action: AuthorizationAction  # ID
    subject_id: str  # ID
    target_state: AccountState | None = None  # ID

    def __post_init__(self) -> None:
        _require_text(self.authorized_by, "HumanAuthorization.authorized_by")
        _require_aware(self.authorized_at, "HumanAuthorization.authorized_at")
        _require_instance(
            self.action, AuthorizationAction, "HumanAuthorization.action"
        )
        _require_text(self.subject_id, "HumanAuthorization.subject_id")
        if self.action is AuthorizationAction.CHANGE_ACCOUNT_STATE:
            if not isinstance(self.target_state, AccountState):
                raise DomainValidationError(
                    "a CHANGE_ACCOUNT_STATE authorization must name its "
                    "target_state"
                )
        elif self.target_state is not None:
            raise DomainValidationError(
                f"{self.action.value} authorization must not carry a "
                "target_state"
            )


def _require_authorization(
    authorization: object,
    action: AuthorizationAction,
    subject_id: str,
    field: str,
) -> None:
    _require_instance(authorization, HumanAuthorization, field)
    if authorization.action is not action:
        raise DomainValidationError(
            f"{field} must authorize {action.value}, not "
            f"{authorization.action.value}"
        )
    if authorization.subject_id != subject_id:
        raise DomainValidationError(
            f"{field} is for subject {authorization.subject_id!r}, "
            f"not {subject_id!r}"
        )


# --- saved record -----------------------------------------------------------


@dataclass(frozen=True)
class BaecRecord:
    """A saved, classified candidate.

    The rules layer computes the classification. This model refuses records
    whose classification contradicts their own criteria and origin. A
    candidate with all four criteria MET and BUYER_GENERATED origin can be
    saved only as CONFIRMED_BAEC; until a human confirms it, it is not a
    record. Records classified NOT_BAEC or
    INSUFFICIENT_EVIDENCE are kept, with their reason, rather than discarded.
    """

    baec_id: str  # ID
    captured_at: datetime  # ID
    candidate: BaecCandidate
    classification: BaecClassification  # DERIVED
    classification_reason: str | None = None  # DERIVED
    normalized_condition: AiDerivedText | None = None  # DERIVED
    staleness_status: StalenessStatus | None = None  # DERIVED; confirmed only
    confirmation: HumanAuthorization | None = None

    def __post_init__(self) -> None:
        _require_text(self.baec_id, "BaecRecord.baec_id")
        _require_aware(self.captured_at, "BaecRecord.captured_at")
        _require_instance(self.candidate, BaecCandidate, "BaecRecord.candidate")
        _require_instance(
            self.classification, BaecClassification, "BaecRecord.classification"
        )
        _require_optional_text(
            self.classification_reason, "BaecRecord.classification_reason"
        )
        if self.normalized_condition is not None:
            _require_instance(
                self.normalized_condition,
                AiDerivedText,
                "BaecRecord.normalized_condition",
            )
        if self.staleness_status is not None:
            _require_instance(
                self.staleness_status,
                StalenessStatus,
                "BaecRecord.staleness_status",
            )

        origin = self.candidate.articulation_origin
        findings = [a.finding for a in self.candidate.assessments]
        all_met = all(f is CriterionFinding.MET for f in findings)
        any_not_met = any(f is CriterionFinding.NOT_MET for f in findings)
        confirmable = all_met and origin is ArticulationOrigin.BUYER_GENERATED
        disqualified = any_not_met or origin is ArticulationOrigin.SELLER_SEEDED

        if self.classification is BaecClassification.CONFIRMED_BAEC:
            if not confirmable:
                raise DomainValidationError(
                    "CONFIRMED_BAEC requires all four criteria MET and "
                    "BUYER_GENERATED origin"
                )
            if self.confirmation is None:
                raise DomainValidationError(
                    "CONFIRMED_BAEC requires a human confirmation"
                )
            _require_authorization(
                self.confirmation,
                AuthorizationAction.CONFIRM_BAEC,
                self.baec_id,
                "BaecRecord.confirmation",
            )
            if self.staleness_status is None:
                raise DomainValidationError(
                    "CONFIRMED_BAEC requires an explicit staleness_status"
                )
            return

        if self.classification is BaecClassification.NOT_BAEC:
            if not disqualified:
                raise DomainValidationError(
                    "NOT_BAEC requires at least one criterion NOT_MET or "
                    "SELLER_SEEDED origin"
                )
        else:  # INSUFFICIENT_EVIDENCE
            if disqualified:
                raise DomainValidationError(
                    "INSUFFICIENT_EVIDENCE cannot be used when a criterion "
                    "is NOT_MET or origin is SELLER_SEEDED; that is NOT_BAEC"
                )
            if confirmable:
                raise DomainValidationError(
                    "INSUFFICIENT_EVIDENCE cannot be used when all four "
                    "criteria are MET and origin is BUYER_GENERATED"
                )

        if self.confirmation is not None:
            raise DomainValidationError(
                f"{self.classification.value} records must not carry a "
                "confirmation"
            )
        if self.classification_reason is None:
            raise DomainValidationError(
                f"{self.classification.value} requires a classification_reason"
            )
        if self.staleness_status is not None:
            raise DomainValidationError(
                f"{self.classification.value} records must not carry a "
                "staleness_status"
            )


# --- account-state inputs ---------------------------------------------------


@dataclass(frozen=True)
class DormancyJudgment:
    """Human answers on plausibility and addressability (RC-22).

    Holds the answers only. Whether they permit Conditionally Dormant is
    decided in state_machine.py. These judgments are separate from BAEC
    validity (RC-16).
    """

    baec_id: str  # ID
    plausibility: ReviewAnswer  # DERIVED (human judgment)
    addressability: ReviewAnswer  # DERIVED (human judgment)
    authorization: HumanAuthorization
    notes: str | None = None  # DERIVED

    def __post_init__(self) -> None:
        _require_text(self.baec_id, "DormancyJudgment.baec_id")
        _require_instance(
            self.plausibility, ReviewAnswer, "DormancyJudgment.plausibility"
        )
        _require_instance(
            self.addressability, ReviewAnswer, "DormancyJudgment.addressability"
        )
        _require_optional_text(self.notes, "DormancyJudgment.notes")
        _require_authorization(
            self.authorization,
            AuthorizationAction.RECORD_DORMANCY_JUDGMENT,
            self.baec_id,
            "DormancyJudgment.authorization",
        )


@dataclass(frozen=True)
class EvaluationEvidence:
    """Evidence that the buyer actually initiated or reopened evaluation.

    Accepts only BUYER_FACT or SELLER_OBSERVATION. External evidence (a
    signal) is rejected, which is one layer of RC-27. This restriction is a
    conservative implementation rule, not a manuscript definition.
    """

    account_id: str  # ID
    evidence: EvidenceExcerpt  # SOURCE
    observed_at: datetime  # ID

    def __post_init__(self) -> None:
        _require_text(self.account_id, "EvaluationEvidence.account_id")
        _require_instance(
            self.evidence, EvidenceExcerpt, "EvaluationEvidence.evidence"
        )
        _require_aware(self.observed_at, "EvaluationEvidence.observed_at")
        if self.evidence.provenance not in _EVALUATION_PROVENANCE:
            raise DomainValidationError(
                "evaluation evidence must be BUYER_FACT or "
                f"SELLER_OBSERVATION, got {self.evidence.provenance.value}"
            )


@dataclass(frozen=True)
class NonEvaluationEvidence:
    """Evidence that the buyer is not, or is no longer, evaluating.

    Required to move an account out of ACTIVE_OPPORTUNITY, so that state is
    not overwritten merely because an old BAEC exists or a reason was typed.
    Accepts only BUYER_FACT or SELLER_OBSERVATION. This is an implementation
    guard that preserves the meaning of the three states; the manuscript
    does not prescribe a form of evidence.
    """

    account_id: str  # ID
    evidence: EvidenceExcerpt  # SOURCE
    observed_at: datetime  # ID

    def __post_init__(self) -> None:
        _require_text(self.account_id, "NonEvaluationEvidence.account_id")
        _require_instance(
            self.evidence, EvidenceExcerpt, "NonEvaluationEvidence.evidence"
        )
        _require_aware(self.observed_at, "NonEvaluationEvidence.observed_at")
        if self.evidence.provenance not in _EVALUATION_PROVENANCE:
            raise DomainValidationError(
                "non-evaluation evidence must be BUYER_FACT or "
                f"SELLER_OBSERVATION, got {self.evidence.provenance.value}"
            )


@dataclass(frozen=True)
class Account:
    """An account and its state. state is None until classified (RC-21)."""

    account_id: str  # ID
    name: str  # ID
    state: AccountState | None = None  # DERIVED

    def __post_init__(self) -> None:
        _require_text(self.account_id, "Account.account_id")
        _require_text(self.name, "Account.name")
        if self.state is not None:
            _require_instance(self.state, AccountState, "Account.state")
