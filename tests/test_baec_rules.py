"""Deterministic classification rules and record factories (Step 5, RC-13)."""

from itertools import product

import pytest

from baec_app.domain.baec_rules import (
    ClassificationReason,
    ClassificationResult,
    classify_candidate,
    create_confirmed_baec_record,
    create_nonconfirmed_classification_record,
)
from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    ClassificationReasonKind,
    CriterionFinding,
    StalenessStatus,
)
from baec_app.domain.models import DomainValidationError
from tests.builders import NOW, authorization, candidate, cases, confirm_auth

C = BaecClassification
F = CriterionFinding
K = BaecCriterion
O = ArticulationOrigin
R = ClassificationReasonKind
CR = ClassificationReason
M, N, U = F.MET, F.NOT_MET, F.UNKNOWN
CRITERIA = list(K)
C1, C2, C3, C4 = CRITERIA


def by_criterion(findings):
    return dict(zip(CRITERIA, findings))


def classify(origin=O.BUYER_GENERATED, findings=(M, M, M, M), reverse=False):
    return classify_candidate(candidate(origin, by_criterion(findings), reverse=reverse))


def kinds(result):
    return [(reason.kind, reason.criterion) for reason in result.reasons]


def confirmed(cand, **overrides):
    kwargs = dict(baec_id="B-1", captured_at=NOW, confirmation=confirm_auth())
    kwargs.update(overrides)
    return create_confirmed_baec_record(cand, **kwargs)


def nonconfirmed(cand):
    return create_nonconfirmed_classification_record(cand, baec_id="B-1", captured_at=NOW)


# --- classification and reasons (RC-13, RC-10) --------------------------------


def _harbor():
    result = classify()
    return result.classification is C.CONFIRMED_BAEC and result.reasons == () and result.reason_text is None


@cases(
    [
        ("Harbor candidate -> CONFIRMED_BAEC, reasons=(), reason_text=None", _harbor),
        (
            "seller-seeded -> NOT_BAEC",
            lambda: classify(O.SELLER_SEEDED).classification is C.NOT_BAEC
            and kinds(classify(O.SELLER_SEEDED)) == [(R.ORIGIN_SELLER_SEEDED, None)],
        ),
        (
            "uncertain origin -> INSUFFICIENT_EVIDENCE",
            lambda: classify(O.UNCERTAIN).classification is C.INSUFFICIENT_EVIDENCE
            and kinds(classify(O.UNCERTAIN)) == [(R.ORIGIN_UNCERTAIN, None)],
        ),
        (
            "C1 not met (active evaluation) -> NOT_BAEC",
            lambda: classify(findings=(N, M, M, M)).classification is C.NOT_BAEC
            and kinds(classify(findings=(N, M, M, M))) == [(R.CRITERION_NOT_MET, C1)],
        ),
        (
            "C4 unknown (missing linkage) -> INSUFFICIENT_EVIDENCE",
            lambda: classify(findings=(M, M, M, U)).classification is C.INSUFFICIENT_EVIDENCE
            and kinds(classify(findings=(M, M, M, U))) == [(R.CRITERION_UNKNOWN, C4)],
        ),
        (
            "NOT_MET outranks UNKNOWN; all disqualifiers listed C1,C4,origin; unknown C2 omitted",
            lambda: classify(O.SELLER_SEEDED, (N, U, M, N)).classification is C.NOT_BAEC
            and kinds(classify(O.SELLER_SEEDED, (N, U, M, N)))
            == [(R.CRITERION_NOT_MET, C1), (R.CRITERION_NOT_MET, C4), (R.ORIGIN_SELLER_SEEDED, None)],
        ),
        (
            "multiple unknowns listed in order, origin last",
            lambda: kinds(classify(O.UNCERTAIN, (U, M, U, M)))
            == [(R.CRITERION_UNKNOWN, C1), (R.CRITERION_UNKNOWN, C3), (R.ORIGIN_UNCERTAIN, None)],
        ),
        (
            "NOT_MET + UNCERTAIN origin -> NOT_BAEC, uncertain not listed",
            lambda: kinds(classify(O.UNCERTAIN, (M, N, M, M))) == [(R.CRITERION_NOT_MET, C2)],
        ),
        (
            "reason order independent of assessment order",
            lambda: classify(O.UNCERTAIN, (U, M, U, M), reverse=True) == classify(O.UNCERTAIN, (U, M, U, M)),
        ),
        (
            "same input twice gives identical result",
            lambda: classify(findings=(N, M, M, M)) == classify(findings=(N, M, M, M)),
        ),
    ]
)
def test_rc13_classification_precedence_and_reasons(check):
    """RC-13 precedence; RC-10 origin handling. Reasons in C1-C4 order, origin last."""
    assert check()


@cases([("classify a non-candidate", lambda: classify_candidate("not a candidate"))])
def test_classify_rejects_non_candidate(check):
    with pytest.raises(DomainValidationError):
        check()


EXPECTED_REASON_TEXT = [
    (O.SELLER_SEEDED, (M, M, M, M), "NOT_BAEC: the seller supplied the core prospective condition (SELLER_SEEDED)."),
    (
        O.UNCERTAIN,
        (M, M, M, M),
        "INSUFFICIENT_EVIDENCE: the evidence does not establish who supplied the core "
        "prospective condition (UNCERTAIN).",
    ),
    (O.BUYER_GENERATED, (N, M, M, M), "NOT_BAEC: criterion not met: PRESENT_NON_EVALUATION."),
    (O.BUYER_GENERATED, (M, M, M, U), "INSUFFICIENT_EVIDENCE: criterion not established: EVALUATION_LINKAGE."),
    (
        O.SELLER_SEEDED,
        (N, U, M, N),
        "NOT_BAEC: criterion not met: PRESENT_NON_EVALUATION; criterion not met: "
        "EVALUATION_LINKAGE; the seller supplied the core prospective condition (SELLER_SEEDED).",
    ),
]


@pytest.mark.parametrize("origin,findings,text", EXPECTED_REASON_TEXT)
def test_rc13_reason_text_is_exact_and_deterministic(origin, findings, text):
    assert classify(origin, findings).reason_text == text


# --- exhaustive 243 cases (RC-13) ---------------------------------------------

ALL_COMBINATIONS = [(findings, origin) for findings in product((M, N, U), repeat=4) for origin in O]


def rc13_oracle(findings, origin):
    """Expected classification and reasons, written directly from RC-13.

    Deliberately independent of baec_rules: it uses no production helper.

    RC-13 rule 1: any criterion NOT_MET, or origin SELLER_SEEDED -> NOT_BAEC.
    RC-13 rule 2: otherwise, all four MET and BUYER_GENERATED -> CONFIRMED_BAEC.
    RC-13 rule 3: otherwise -> INSUFFICIENT_EVIDENCE.
    """
    names = [finding.name for finding in findings]
    seeded = origin.name == "SELLER_SEEDED"
    if names.count("NOT_MET") > 0 or seeded:
        reasons = [("CRITERION_NOT_MET", c.name) for c, n in zip(CRITERIA, names) if n == "NOT_MET"]
        if seeded:
            reasons.append(("ORIGIN_SELLER_SEEDED", None))
        return "NOT_BAEC", reasons
    if names.count("MET") == 4 and origin.name == "BUYER_GENERATED":
        return "CONFIRMED_BAEC", []
    reasons = [("CRITERION_UNKNOWN", c.name) for c, n in zip(CRITERIA, names) if n == "UNKNOWN"]
    if origin.name == "UNCERTAIN":
        reasons.append(("ORIGIN_UNCERTAIN", None))
    return "INSUFFICIENT_EVIDENCE", reasons


def _combo_id(combo):
    findings, origin = combo
    return "-".join(f.name for f in findings) + "+" + origin.name


@pytest.mark.parametrize("combo", ALL_COMBINATIONS, ids=_combo_id)
def test_rc13_exhaustive_classification_matches_oracle(combo):
    """Every one of the 3^4 x 3 = 243 combinations: rules, model, and factories agree."""
    findings, origin = combo
    expected_class, expected_reasons = rc13_oracle(findings, origin)
    cand = candidate(origin, by_criterion(findings))

    result = classify_candidate(cand)
    assert result.classification.name == expected_class
    assert [
        (reason.kind.name, reason.criterion.name if reason.criterion else None) for reason in result.reasons
    ] == expected_reasons

    if expected_class == "CONFIRMED_BAEC":
        saved = confirmed(cand)
        assert saved.classification is C.CONFIRMED_BAEC
        assert saved.staleness_status is StalenessStatus.CURRENT
        with pytest.raises(DomainValidationError):
            nonconfirmed(cand)
    else:
        saved = nonconfirmed(cand)
        assert saved.classification.name == expected_class
        assert saved.classification_reason == result.reason_text
        assert saved.confirmation is None
        assert saved.staleness_status is None
        with pytest.raises(DomainValidationError):
            confirmed(cand)


def test_rc13_exhaustive_totals():
    """243 combinations: 1 CONFIRMED_BAEC, 211 NOT_BAEC, 31 INSUFFICIENT_EVIDENCE."""
    assert len(ALL_COMBINATIONS) == 243
    assert len(set(ALL_COMBINATIONS)) == 243
    expected = {"CONFIRMED_BAEC": 1, "NOT_BAEC": 211, "INSUFFICIENT_EVIDENCE": 31}

    from_oracle = {name: 0 for name in expected}
    from_rules = {name: 0 for name in expected}
    for findings, origin in ALL_COMBINATIONS:
        from_oracle[rc13_oracle(findings, origin)[0]] += 1
        from_rules[classify(origin, findings).classification.name] += 1
    assert from_oracle == expected
    assert from_rules == expected


# --- factories keep classification apart from confirmation (RC-31) -------------


def _confirmed_record_shape():
    saved = confirmed(candidate())
    return (
        saved.staleness_status is StalenessStatus.CURRENT
        and saved.confirmation is not None
        and saved.classification_reason is None
    )


@cases([("confirmed record created as CURRENT with confirmation, no reason", _confirmed_record_shape)])
def test_rc29_new_confirmed_record_is_current(check):
    assert check()


@cases(
    [
        (
            "valid authorization cannot upgrade seller-seeded candidate",
            lambda: confirmed(candidate(O.SELLER_SEEDED)),
        ),
        (
            "valid authorization cannot upgrade insufficient candidate",
            lambda: confirmed(candidate(findings={C1: U})),
        ),
        ("confirmed record with no confirmation", lambda: confirmed(candidate(), confirmation=None)),
        (
            "confirmed record with wrong-action authorization",
            lambda: confirmed(
                candidate(),
                confirmation=authorization(
                    AuthorizationAction.CHANGE_ACCOUNT_STATE, "B-1", AccountState.CONDITIONALLY_DORMANT
                ),
            ),
        ),
        (
            "confirmed record with authorization for another BAEC",
            lambda: confirmed(candidate(), confirmation=confirm_auth("B-2")),
        ),
        ("qualifying candidate saved as non-confirmed", lambda: nonconfirmed(candidate())),
    ]
)
def test_rc31_authorization_never_changes_classification(check):
    """RC-31/RC-10: human confirmation is required but cannot upgrade a candidate."""
    with pytest.raises(DomainValidationError):
        check()


@cases(
    [
        (
            "non-confirmed factory has no confirmation parameter",
            lambda: create_nonconfirmed_classification_record(
                candidate(O.SELLER_SEEDED), baec_id="B-1", captured_at=NOW, confirmation=confirm_auth()
            ),
        ),
    ]
)
def test_rc31_nonconfirmed_factory_cannot_accept_confirmation(check):
    with pytest.raises(TypeError):
        check()


def test_classify_candidate_takes_no_authorization():
    with pytest.raises(TypeError):
        classify_candidate(candidate(), confirm_auth())


# --- ClassificationReason / ClassificationResult integrity --------------------


def _not_baec():
    return classify(O.SELLER_SEEDED, (N, M, M, M))


def _insufficient():
    return classify(O.UNCERTAIN, (U, M, M, M))


@cases(
    [
        (
            "canonical NOT_BAEC result rebuilds",
            lambda: ClassificationResult(C.NOT_BAEC, _not_baec().reasons, _not_baec().reason_text) == _not_baec(),
        ),
        (
            "canonical INSUFFICIENT result rebuilds",
            lambda: ClassificationResult(
                C.INSUFFICIENT_EVIDENCE, _insufficient().reasons, _insufficient().reason_text
            )
            == _insufficient(),
        ),
    ]
)
def test_classification_result_accepts_canonical(check):
    assert check()


@cases(
    [
        ("criterion reason without criterion", lambda: CR(R.CRITERION_NOT_MET)),
        ("origin reason with criterion", lambda: CR(R.ORIGIN_UNCERTAIN, C4)),
        ("CONFIRMED result with reason_text", lambda: ClassificationResult(C.CONFIRMED_BAEC, (), "x")),
        ("NOT_BAEC result with no reasons", lambda: ClassificationResult(C.NOT_BAEC, (), "x")),
        (
            "INSUFFICIENT result with no reason_text",
            lambda: ClassificationResult(C.INSUFFICIENT_EVIDENCE, (CR(R.ORIGIN_UNCERTAIN),)),
        ),
        (
            "NOT_BAEC carrying CRITERION_UNKNOWN",
            lambda: ClassificationResult(
                C.NOT_BAEC, (CR(R.CRITERION_UNKNOWN, C4),), "NOT_BAEC: criterion not established: EVALUATION_LINKAGE."
            ),
        ),
        (
            "NOT_BAEC carrying ORIGIN_UNCERTAIN",
            lambda: ClassificationResult(C.NOT_BAEC, (CR(R.ORIGIN_UNCERTAIN),), "x"),
        ),
        (
            "NOT_BAEC with one valid and one mismatched kind",
            lambda: ClassificationResult(C.NOT_BAEC, (CR(R.ORIGIN_SELLER_SEEDED), CR(R.ORIGIN_UNCERTAIN)), "x"),
        ),
        (
            "INSUFFICIENT carrying CRITERION_NOT_MET",
            lambda: ClassificationResult(C.INSUFFICIENT_EVIDENCE, (CR(R.CRITERION_NOT_MET, C4),), "x"),
        ),
        (
            "INSUFFICIENT carrying ORIGIN_SELLER_SEEDED",
            lambda: ClassificationResult(C.INSUFFICIENT_EVIDENCE, (CR(R.ORIGIN_SELLER_SEEDED),), "x"),
        ),
        (
            "tampered text: arbitrary sentence",
            lambda: ClassificationResult(C.NOT_BAEC, _not_baec().reasons, "Buyer is a hot lead."),
        ),
        (
            "tampered text: one character changed",
            lambda: ClassificationResult(C.NOT_BAEC, _not_baec().reasons, _not_baec().reason_text[:-1] + "!"),
        ),
        (
            "tampered text: trailing space",
            lambda: ClassificationResult(C.NOT_BAEC, _not_baec().reasons, _not_baec().reason_text + " "),
        ),
        (
            "tampered text: text from the other classification",
            lambda: ClassificationResult(C.NOT_BAEC, _not_baec().reasons, _insufficient().reason_text),
        ),
        (
            "tampered text: text omits one of the reasons",
            lambda: ClassificationResult(
                C.NOT_BAEC, _not_baec().reasons, "NOT_BAEC: criterion not met: PRESENT_NON_EVALUATION."
            ),
        ),
    ]
)
def test_rc13_classification_result_rejects_contradictions(check):
    """RC-13: reasons must fit the classification; reason text is never caller-written."""
    with pytest.raises(DomainValidationError):
        check()
