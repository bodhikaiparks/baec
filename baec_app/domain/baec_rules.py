"""Deterministic BAEC classification rules (RC-02, RC-10, RC-13).

This module computes; models.py prevents contradictory records. No AI, no
clock, and no randomness is involved: the same candidate always produces the
same result.

Classification and human confirmation are kept apart:

* classify_candidate never sees a HumanAuthorization.
* In a ClassificationResult, CONFIRMED_BAEC means only that the candidate
  satisfies the deterministic criteria. A saved confirmed BAEC record
  additionally requires human confirmation.
* Human authorization never changes a classification. It cannot turn a
  NOT_BAEC or INSUFFICIENT_EVIDENCE candidate into a confirmed record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import (
    ArticulationOrigin,
    BaecClassification,
    BaecCriterion,
    ClassificationReasonKind,
    CriterionFinding,
    StalenessStatus,
)
from .models import (
    BaecCandidate,
    BaecRecord,
    DomainValidationError,
    HumanAuthorization,
)

_CRITERION_KINDS = frozenset(
    {
        ClassificationReasonKind.CRITERION_NOT_MET,
        ClassificationReasonKind.CRITERION_UNKNOWN,
    }
)

# Which reason kinds each non-confirmed classification may carry.
_ALLOWED_REASON_KINDS = {
    BaecClassification.NOT_BAEC: frozenset(
        {
            ClassificationReasonKind.CRITERION_NOT_MET,
            ClassificationReasonKind.ORIGIN_SELLER_SEEDED,
        }
    ),
    BaecClassification.INSUFFICIENT_EVIDENCE: frozenset(
        {
            ClassificationReasonKind.CRITERION_UNKNOWN,
            ClassificationReasonKind.ORIGIN_UNCERTAIN,
        }
    ),
}

_REASON_PHRASES = {
    ClassificationReasonKind.CRITERION_NOT_MET: "criterion not met",
    ClassificationReasonKind.CRITERION_UNKNOWN: "criterion not established",
    ClassificationReasonKind.ORIGIN_SELLER_SEEDED: (
        "the seller supplied the core prospective condition (SELLER_SEEDED)"
    ),
    ClassificationReasonKind.ORIGIN_UNCERTAIN: (
        "the evidence does not establish who supplied the core prospective "
        "condition (UNCERTAIN)"
    ),
}


@dataclass(frozen=True)
class ClassificationReason:
    """One structured reason. Criterion reasons name their criterion."""

    kind: ClassificationReasonKind
    criterion: BaecCriterion | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ClassificationReasonKind):
            raise DomainValidationError(
                "ClassificationReason.kind must be ClassificationReasonKind"
            )
        if self.kind in _CRITERION_KINDS:
            if not isinstance(self.criterion, BaecCriterion):
                raise DomainValidationError(
                    f"{self.kind.value} requires a BaecCriterion"
                )
        elif self.criterion is not None:
            raise DomainValidationError(
                f"{self.kind.value} must not carry a criterion"
            )

    def describe(self) -> str:
        phrase = _REASON_PHRASES[self.kind]
        if self.criterion is not None:
            return f"{phrase}: {self.criterion.value}"
        return phrase


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of classify_candidate.

    CONFIRMED_BAEC here means the candidate satisfies the deterministic
    criteria. It does not mean a human has confirmed anything.
    """

    classification: BaecClassification
    reasons: tuple[ClassificationReason, ...] = ()
    reason_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.classification, BaecClassification):
            raise DomainValidationError(
                "ClassificationResult.classification must be BaecClassification"
            )
        if not isinstance(self.reasons, tuple) or not all(
            isinstance(r, ClassificationReason) for r in self.reasons
        ):
            raise DomainValidationError(
                "ClassificationResult.reasons must be a tuple of "
                "ClassificationReason"
            )
        if self.classification is BaecClassification.CONFIRMED_BAEC:
            if self.reasons or self.reason_text is not None:
                raise DomainValidationError(
                    "CONFIRMED_BAEC carries no reasons and no reason_text"
                )
        else:
            if not self.reasons:
                raise DomainValidationError(
                    f"{self.classification.value} requires at least one reason"
                )
            allowed = _ALLOWED_REASON_KINDS[self.classification]
            for reason in self.reasons:
                if reason.kind not in allowed:
                    raise DomainValidationError(
                        f"{self.classification.value} cannot carry reason "
                        f"{reason.kind.value}"
                    )
            # reason_text is never caller-written: it must be exactly the
            # canonical text generated from the classification and reasons.
            if self.reason_text != _reason_text(self.classification, self.reasons):
                raise DomainValidationError(
                    "reason_text must equal the canonical text generated "
                    "from the classification and reasons"
                )


def _reason_text(
    classification: BaecClassification, reasons: tuple[ClassificationReason, ...]
) -> str:
    return (
        f"{classification.value}: "
        + "; ".join(reason.describe() for reason in reasons)
        + "."
    )


def classify_candidate(candidate: BaecCandidate) -> ClassificationResult:
    """Apply the four criteria and articulation origin to a candidate.

    Precedence (first match wins):
      1. Any criterion NOT_MET, or origin SELLER_SEEDED  -> NOT_BAEC
      2. All four MET and origin BUYER_GENERATED         -> CONFIRMED_BAEC
      3. Otherwise                                       -> INSUFFICIENT_EVIDENCE

    Reasons are listed in criterion order (C1 to C4), then origin. Only the
    reasons that determine the outcome are listed: a NOT_BAEC result lists
    its disqualifiers, not any unknowns alongside them.
    """
    if not isinstance(candidate, BaecCandidate):
        raise DomainValidationError("classify_candidate requires a BaecCandidate")

    origin = candidate.articulation_origin
    # Iterating the enum fixes the order regardless of how the candidate's
    # assessments tuple happens to be ordered.
    findings = [(c, candidate.assessment_for(c).finding) for c in BaecCriterion]

    disqualifiers = [
        ClassificationReason(ClassificationReasonKind.CRITERION_NOT_MET, c)
        for c, finding in findings
        if finding is CriterionFinding.NOT_MET
    ]
    if origin is ArticulationOrigin.SELLER_SEEDED:
        disqualifiers.append(
            ClassificationReason(ClassificationReasonKind.ORIGIN_SELLER_SEEDED)
        )
    if disqualifiers:
        reasons = tuple(disqualifiers)
        return ClassificationResult(
            BaecClassification.NOT_BAEC,
            reasons,
            _reason_text(BaecClassification.NOT_BAEC, reasons),
        )

    unresolved = [
        ClassificationReason(ClassificationReasonKind.CRITERION_UNKNOWN, c)
        for c, finding in findings
        if finding is CriterionFinding.UNKNOWN
    ]
    if origin is ArticulationOrigin.UNCERTAIN:
        unresolved.append(
            ClassificationReason(ClassificationReasonKind.ORIGIN_UNCERTAIN)
        )
    if unresolved:
        reasons = tuple(unresolved)
        return ClassificationResult(
            BaecClassification.INSUFFICIENT_EVIDENCE,
            reasons,
            _reason_text(BaecClassification.INSUFFICIENT_EVIDENCE, reasons),
        )

    # No disqualifier and nothing unresolved: all four MET, BUYER_GENERATED.
    return ClassificationResult(BaecClassification.CONFIRMED_BAEC)


def create_confirmed_baec_record(
    candidate: BaecCandidate,
    *,
    baec_id: str,
    captured_at: datetime,
    confirmation: HumanAuthorization,
) -> BaecRecord:
    """Save a candidate as a confirmed BAEC.

    The classification is recomputed here. The confirmation cannot upgrade a
    candidate that does not satisfy the criteria. A newly confirmed record is
    always created as CURRENT.

    The confirmation is a domain requirement, not proof that a human acted;
    see HumanAuthorization.
    """
    result = classify_candidate(candidate)
    if result.classification is not BaecClassification.CONFIRMED_BAEC:
        raise DomainValidationError(
            "cannot create a confirmed BAEC record; human confirmation does "
            f"not change classification. {result.reason_text}"
        )
    return BaecRecord(
        baec_id=baec_id,
        captured_at=captured_at,
        candidate=candidate,
        classification=BaecClassification.CONFIRMED_BAEC,
        staleness_status=StalenessStatus.CURRENT,
        confirmation=confirmation,
    )


def create_nonconfirmed_classification_record(
    candidate: BaecCandidate,
    *,
    baec_id: str,
    captured_at: datetime,
) -> BaecRecord:
    """Preserve a NOT_BAEC or INSUFFICIENT_EVIDENCE candidate with its reason.

    Takes no authorization. A candidate that satisfies the criteria is
    refused: it can be saved only through create_confirmed_baec_record.
    """
    result = classify_candidate(candidate)
    if result.classification is BaecClassification.CONFIRMED_BAEC:
        raise DomainValidationError(
            "candidate satisfies the BAEC criteria; it can be saved only as "
            "a confirmed BAEC record with human confirmation"
        )
    return BaecRecord(
        baec_id=baec_id,
        captured_at=captured_at,
        candidate=candidate,
        classification=result.classification,
        classification_reason=result.reason_text,
    )
