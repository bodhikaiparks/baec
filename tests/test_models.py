"""Validation rules for the domain models (Step 4)."""

from datetime import datetime

import pytest

from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
)
from baec_app.domain.models import (
    Account,
    CriterionAssessment,
    DomainValidationError,
    DormancyJudgment,
    EvaluationEvidence,
    EvidenceExcerpt,
    NonEvaluationEvidence,
)
from tests.builders import (
    HARBOR_QUOTE,
    NOW,
    authorization,
    candidate,
    cases,
    confirm_auth,
    excerpt,
    external_excerpt,
    record,
)

A = AuthorizationAction
C = BaecClassification
F = CriterionFinding
K = BaecCriterion
O = ArticulationOrigin
P = ProvenanceCategory
S = StalenessStatus
C1 = K.PRESENT_NON_EVALUATION
CURRENT = dict(staleness_status=S.CURRENT)


def confirmed(cand, **overrides):
    kwargs = dict(confirmation=confirm_auth(), staleness_status=S.CURRENT)
    kwargs.update(overrides)
    return record(cand, C.CONFIRMED_BAEC, **kwargs)


# --- EvidenceExcerpt (RC-32) --------------------------------------------------


@cases(
    [
        ("EvidenceExcerpt still allows EXTERNAL_EVIDENCE", external_excerpt),
        ("EvidenceExcerpt exposes source_id", lambda: external_excerpt().source_id == "SIG-3"),
    ]
)
def test_rc32_evidence_excerpt_accepts(check):
    assert check()


@cases(
    [
        ("AI inference as evidence", lambda: excerpt("x", P.AI_INFERENCE)),
        ("unknown provenance as evidence", lambda: excerpt("x", P.UNKNOWN)),
        ("raw string instead of enum", lambda: excerpt("x", "BUYER_FACT")),
    ]
)
def test_rc32_evidence_excerpt_rejects(check):
    """RC-32: AI inference and unknown-origin text are never evidence."""
    with pytest.raises(DomainValidationError):
        check()


@cases(
    [
        (
            "old field name source_interaction_id",
            lambda: EvidenceExcerpt(text="x", provenance=P.BUYER_FACT, source_interaction_id="INT-1"),
        ),
    ]
)
def test_evidence_excerpt_old_field_name_is_gone(check):
    with pytest.raises(TypeError):
        check()


# --- CriterionAssessment (RC-02, RC-05) ---------------------------------------


@cases(
    [
        ("UNKNOWN without evidence", lambda: CriterionAssessment(K.EVALUATION_LINKAGE, F.UNKNOWN)),
        (
            "criterion on seller observation",
            lambda: CriterionAssessment(
                C1, F.MET, (excerpt("Buyer said no review is under way", P.SELLER_OBSERVATION),)
            ),
        ),
    ]
)
def test_rc05_criterion_assessment_accepts(check):
    """RC-05: UNKNOWN is a legitimate finding and needs no evidence."""
    assert check()


@cases(
    [
        ("MET without evidence", lambda: CriterionAssessment(K.EVALUATION_LINKAGE, F.MET)),
        (
            "criterion MET on external evidence",
            lambda: CriterionAssessment(K.PROSPECTIVE_CONDITION, F.MET, (external_excerpt(),)),
        ),
        (
            "criterion NOT_MET on external evidence",
            lambda: CriterionAssessment(C1, F.NOT_MET, (external_excerpt(),)),
        ),
        (
            "external evidence mixed with buyer fact",
            lambda: CriterionAssessment(C1, F.MET, (excerpt(), external_excerpt())),
        ),
    ]
)
def test_rc02_criterion_assessment_rejects(check):
    """RC-02/RC-13: a finding needs evidence; external evidence cannot establish C1-C4."""
    with pytest.raises(DomainValidationError):
        check()


def test_not_met_also_requires_evidence():
    with pytest.raises(DomainValidationError):
        CriterionAssessment(C1, F.NOT_MET)


# --- BaecCandidate ------------------------------------------------------------


@cases(
    [
        ("candidate with no verbatim quote", candidate),
        ("buyer_role None", lambda: candidate().buyer_role is None),
        ("buyer_role given", lambda: candidate(buyer_role="Materials Manager")),
        ("assessment_for valid criterion", lambda: candidate().assessment_for(C1).criterion is C1),
    ]
)
def test_candidate_accepts(check):
    assert check()


@cases(
    [
        ("candidate with 3 criteria", lambda: _with_assessments(lambda a: a[:3])),
        ("candidate with duplicate criterion", lambda: _with_assessments(lambda a: a[:3] + a[:1])),
        ("buyer_role empty string", lambda: candidate(buyer_role="")),
        ("buyer_role non-string", lambda: candidate(buyer_role=5)),
        ("assessment_for raw string", lambda: candidate().assessment_for("PRESENT_NON_EVALUATION")),
        ("assessment_for None", lambda: candidate().assessment_for(None)),
    ]
)
def test_rc02_candidate_rejects(check):
    """RC-02: exactly one assessment per criterion; unknown role stays None."""
    with pytest.raises(DomainValidationError):
        check()


def _with_assessments(change):
    from dataclasses import replace

    base = candidate()
    return replace(base, assessments=change(base.assessments))


# --- BaecRecord classification consistency (RC-13, RC-31, RC-29) ---------------


@cases(
    [
        ("confirmed record", lambda: confirmed(candidate())),
        (
            "NOT_BAEC (seller-seeded) with reason",
            lambda: record(candidate(O.SELLER_SEEDED), C.NOT_BAEC, "seller supplied the condition"),
        ),
        (
            "NOT_BAEC with one NOT_MET",
            lambda: record(candidate(findings={C1: F.NOT_MET}), C.NOT_BAEC, "buyer is already evaluating"),
        ),
        (
            "INSUFFICIENT with one UNKNOWN",
            lambda: record(candidate(findings={C1: F.UNKNOWN}), C.INSUFFICIENT_EVIDENCE, "C1 not established"),
        ),
        (
            "INSUFFICIENT with all MET, origin UNCERTAIN",
            lambda: record(candidate(O.UNCERTAIN), C.INSUFFICIENT_EVIDENCE, "origin not established"),
        ),
        ("CONFIRMED with REVIEW_DUE", lambda: confirmed(candidate(), staleness_status=S.REVIEW_DUE)),
    ]
)
def test_rc13_record_accepts_consistent_classification(check):
    assert check()


@cases(
    [
        ("confirmed without confirmation", lambda: confirmed(candidate(), confirmation=None)),
        ("confirmed, seller-seeded", lambda: confirmed(candidate(O.SELLER_SEEDED))),
        ("confirmed, uncertain origin", lambda: confirmed(candidate(O.UNCERTAIN))),
        ("confirmed, one criterion UNKNOWN", lambda: confirmed(candidate(findings={C1: F.UNKNOWN}))),
        ("CONFIRMED but one NOT_MET", lambda: confirmed(candidate(findings={C1: F.NOT_MET}))),
        (
            "confirmation for wrong action",
            lambda: confirmed(
                candidate(),
                confirmation=authorization(A.CHANGE_ACCOUNT_STATE, "B-1", AccountState.CONDITIONALLY_DORMANT),
            ),
        ),
        ("confirmation for wrong subject", lambda: confirmed(candidate(), confirmation=confirm_auth("B-2"))),
        ("NOT_BAEC without reason", lambda: record(candidate(O.SELLER_SEEDED), C.NOT_BAEC)),
        ("NOT_BAEC but all MET and buyer-generated", lambda: record(candidate(), C.NOT_BAEC, "r")),
        (
            "NOT_BAEC but only UNKNOWN / uncertain",
            lambda: record(candidate(O.UNCERTAIN, {C1: F.UNKNOWN}), C.NOT_BAEC, "r"),
        ),
        (
            "INSUFFICIENT_EVIDENCE with confirmation",
            lambda: record(
                candidate(O.UNCERTAIN), C.INSUFFICIENT_EVIDENCE, "origin unclear", confirmation=confirm_auth()
            ),
        ),
        (
            "INSUFFICIENT but a criterion NOT_MET",
            lambda: record(candidate(findings={C1: F.NOT_MET}), C.INSUFFICIENT_EVIDENCE, "r"),
        ),
        (
            "INSUFFICIENT but seller-seeded",
            lambda: record(candidate(O.SELLER_SEEDED), C.INSUFFICIENT_EVIDENCE, "r"),
        ),
        (
            "INSUFFICIENT but all MET and buyer-generated",
            lambda: record(candidate(), C.INSUFFICIENT_EVIDENCE, "r"),
        ),
        (
            "naive timestamp",
            lambda: record(candidate(O.SELLER_SEEDED), C.NOT_BAEC, "r", captured_at=datetime(2026, 1, 15)),
        ),
    ]
)
def test_rc13_rc31_record_rejects_contradictory_classification(check):
    """RC-13: classification must match criteria and origin. RC-31: confirmed needs a human."""
    with pytest.raises(DomainValidationError):
        check()


@cases(
    [
        ("CONFIRMED with staleness None", lambda: confirmed(candidate(), staleness_status=None)),
        (
            "NOT_BAEC with staleness",
            lambda: record(candidate(O.SELLER_SEEDED), C.NOT_BAEC, "r", **CURRENT),
        ),
        (
            "INSUFFICIENT with staleness",
            lambda: record(candidate(O.UNCERTAIN), C.INSUFFICIENT_EVIDENCE, "r", **CURRENT),
        ),
        ("staleness raw string", lambda: confirmed(candidate(), staleness_status="CURRENT")),
    ]
)
def test_rc29_record_staleness_rules(check):
    """RC-29: only confirmed BAECs carry a staleness status, and it must be explicit."""
    with pytest.raises(DomainValidationError):
        check()


# --- HumanAuthorization (RC-31) -----------------------------------------------


@cases(
    [
        (
            "state-change authorization with target",
            lambda: authorization(A.CHANGE_ACCOUNT_STATE, "ACC-1", AccountState.ACTIVE_OPPORTUNITY),
        ),
    ]
)
def test_rc31_authorization_accepts(check):
    assert check()


@cases(
    [
        ("state-change authorization without target", lambda: authorization(A.CHANGE_ACCOUNT_STATE, "ACC-1")),
        (
            "state-change authorization with raw-string target",
            lambda: authorization(A.CHANGE_ACCOUNT_STATE, "ACC-1", "ACTIVE_OPPORTUNITY"),
        ),
        (
            "CONFIRM_BAEC authorization carrying a target",
            lambda: authorization(A.CONFIRM_BAEC, "B-1", AccountState.ACTIVE_OPPORTUNITY),
        ),
        (
            "RECORD_DORMANCY_JUDGMENT authorization carrying a target",
            lambda: authorization(A.RECORD_DORMANCY_JUDGMENT, "B-1", AccountState.CONDITIONALLY_DORMANT),
        ),
    ]
)
def test_rc31_authorization_target_state_rules(check):
    """RC-31: a state-change authorization names its destination; others must not."""
    with pytest.raises(DomainValidationError):
        check()


# --- DormancyJudgment (RC-22) -------------------------------------------------


@cases(
    [
        (
            "dormancy judgment",
            lambda: DormancyJudgment(
                "B-1", ReviewAnswer.YES, ReviewAnswer.UNKNOWN, authorization(A.RECORD_DORMANCY_JUDGMENT, "B-1")
            ),
        ),
    ]
)
def test_rc22_dormancy_judgment_accepts(check):
    assert check()


@cases(
    [
        (
            "dormancy judgment with wrong-action auth",
            lambda: DormancyJudgment("B-1", ReviewAnswer.YES, ReviewAnswer.YES, confirm_auth()),
        ),
    ]
)
def test_rc22_dormancy_judgment_rejects(check):
    with pytest.raises(DomainValidationError):
        check()


# --- EvaluationEvidence (RC-27) and NonEvaluationEvidence (RC-34) --------------


def _seller_note(text, source_id):
    return excerpt(text, P.SELLER_OBSERVATION, source_id)


@cases(
    [
        ("evaluation evidence, buyer fact", lambda: EvaluationEvidence("ACC-1", excerpt(), NOW)),
        (
            "evaluation evidence, seller observation",
            lambda: EvaluationEvidence("ACC-1", _seller_note("Buyer said they opened an RFP", "INT-2"), NOW),
        ),
        ("non-evaluation evidence, buyer fact", lambda: NonEvaluationEvidence("ACC-1", excerpt(), NOW)),
        (
            "non-evaluation evidence, seller observation",
            lambda: NonEvaluationEvidence("ACC-1", _seller_note("Buyer said the review was closed", "INT-3"), NOW),
        ),
    ]
)
def test_rc27_rc34_evaluation_state_evidence_accepts(check):
    assert check()


@cases(
    [
        (
            "evaluation evidence from external signal",
            lambda: EvaluationEvidence("ACC-1", external_excerpt(), NOW),
        ),
        (
            "non-evaluation evidence from external signal",
            lambda: NonEvaluationEvidence("ACC-1", external_excerpt(), NOW),
        ),
        ("non-evaluation evidence as plain string", lambda: NonEvaluationEvidence("ACC-1", "they stopped", NOW)),
        (
            "non-evaluation evidence naive timestamp",
            lambda: NonEvaluationEvidence("ACC-1", excerpt(), datetime(2026, 1, 15)),
        ),
    ]
)
def test_rc27_rc34_evaluation_state_evidence_rejects(check):
    """RC-27: a signal is not evaluation evidence. RC-34: same guard for non-evaluation."""
    with pytest.raises(DomainValidationError):
        check()


# --- Account (RC-20, RC-21) ---------------------------------------------------


@cases([("account with state None", lambda: Account("ACC-1", "Harbor Surgical Center").state is None)])
def test_rc21_account_may_be_unclassified(check):
    assert check()


@cases([("account with raw-string state", lambda: Account("ACC-1", "Harbor", "ACTIVE_OPPORTUNITY"))])
def test_rc20_account_rejects_unsupported_state(check):
    with pytest.raises(DomainValidationError):
        check()


@pytest.mark.parametrize("state", list(AccountState))
def test_rc20_account_accepts_each_of_the_three_states(state):
    assert Account("ACC-1", "Harbor", state).state is state
