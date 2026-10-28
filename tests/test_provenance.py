"""Evidence fidelity: thresholds, verbatim quotes, single-source evidence, immutability."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal

import pytest

from baec_app.domain import baec_rules, models, state_machine
from baec_app.domain.enums import CriterionFinding, ProvenanceCategory, ThresholdComparator
from baec_app.domain.models import CriterionAssessment, DomainValidationError, StringencyExpression
from tests.builders import HARBOR_QUOTE, candidate, cases, confirmed_record, excerpt

P = ProvenanceCategory
T = ThresholdComparator
SE = StringencyExpression
QUOTE = "If pricing goes up more than 10% at renewal, we would look."


def _note():
    return excerpt('Call note: MM said, "' + QUOTE + '" No review under way.', P.SELLER_OBSERVATION)


def _paraphrase():
    return excerpt("Call note: MM would look around if pricing rose a lot at renewal.", P.SELLER_OBSERVATION)


# --- threshold preservation (RC-18) -------------------------------------------


@cases(
    [
        (
            "'more than 10%' as GREATER_THAN 10",
            lambda: SE("more than 10%", T.GREATER_THAN, Decimal("10"), "%", timing_text="when our agreement renews"),
        ),
        ("'around 10%' as APPROXIMATELY", lambda: SE("around 10%", T.APPROXIMATELY, Decimal("10"), "%")),
        (
            "'significant' qualitative",
            lambda: SE("a significant increase", T.QUALITATIVE_ONLY, qualitative_term="significant"),
        ),
        (
            "NONE_STATED with recurrence only",
            lambda: SE(
                "recurring monthly problem, not one late delivery", T.NONE_STATED, recurrence_text="recurring monthly"
            ),
        ),
        (
            "unnormalizable wording, comparator None",
            lambda: SE("somewhere in the double digits, depending", None),
        ),
    ]
)
def test_rc18_threshold_wording_is_accepted_as_stated(check):
    assert check()


@cases(
    [
        (
            "number invented for 'significant'",
            lambda: SE("a significant increase", T.QUALITATIVE_ONLY, Decimal("10"), qualitative_term="significant"),
        ),
        ("numeric comparator without number", lambda: SE("more than", T.GREATER_THAN)),
        ("float instead of Decimal", lambda: SE("more than 10%", T.GREATER_THAN, 10.0)),
        ("number with comparator None", lambda: SE("double digits", None, Decimal("10"))),
    ]
)
def test_rc18_no_threshold_may_be_invented(check):
    """RC-18: a number exists only if the buyer stated one."""
    with pytest.raises(DomainValidationError):
        check()


def test_rc18_more_than_is_not_stored_as_at_least():
    stored = SE("more than 15%", T.GREATER_THAN, Decimal("15"), "%")
    assert stored.comparator is T.GREATER_THAN
    assert stored.comparator is not T.AT_LEAST
    assert stored.numeric_value == Decimal("15")
    assert stored.verbatim_text == "more than 15%"


def test_rc18_qualitative_threshold_has_no_number():
    stored = SE("a significant increase", T.QUALITATIVE_ONLY, qualitative_term="significant")
    assert stored.numeric_value is None
    assert stored.qualitative_term == "significant"


def test_rc18_none_stated_cannot_carry_number_or_term():
    with pytest.raises(DomainValidationError):
        SE("recurring", T.NONE_STATED, Decimal("3"))
    with pytest.raises(DomainValidationError):
        SE("recurring", T.NONE_STATED, qualitative_term="significant")


def test_rc18_candidate_without_stringency_information_has_none():
    assert candidate().stringency is None


# --- verbatim buyer statement (RC-33) -----------------------------------------


@cases(
    [
        ("candidate with verbatim quote", lambda: candidate(buyer_exact_statement=HARBOR_QUOTE)),
        (
            "exact statement present in buyer-fact excerpt",
            lambda: candidate(buyer_exact_statement="we'd evaluate other options"),
        ),
        (
            "seller-observation excerpt containing exact statement",
            lambda: candidate(source_excerpt=_note(), buyer_exact_statement=QUOTE),
        ),
        (
            "seller paraphrase with buyer_exact_statement=None",
            lambda: candidate(source_excerpt=_paraphrase()).buyer_exact_statement is None,
        ),
    ]
)
def test_rc33_buyer_statement_accepted_when_verbatim_or_absent(check):
    assert check()


@cases(
    [
        ("empty-string buyer quote", lambda: candidate(buyer_exact_statement=" ")),
        (
            "purported exact statement absent from excerpt",
            lambda: candidate(buyer_exact_statement="We would switch suppliers immediately."),
        ),
        (
            "near match differing only in case",
            lambda: candidate(buyer_exact_statement="We'd Evaluate Other Options"),
        ),
        (
            "near match differing only in spacing",
            lambda: candidate(buyer_exact_statement="we'd  evaluate other options"),
        ),
        (
            "seller paraphrase passed off as a quote",
            lambda: candidate(source_excerpt=_paraphrase(), buyer_exact_statement=QUOTE),
        ),
    ]
)
def test_rc33_buyer_statement_is_never_fabricated(check):
    """RC-33: an exact statement must appear verbatim in the captured source."""
    with pytest.raises(DomainValidationError):
        check()


# --- one candidate, one interaction (RC-32) ----------------------------


def _one_later_excerpt_among_valid():
    base = candidate()
    late = excerpt("later call", P.SELLER_OBSERVATION, "INT-7")
    last = base.assessments[3]
    changed = base.assessments[:3] + (
        CriterionAssessment(last.criterion, CriterionFinding.MET, (excerpt(), late)),
    )
    return replace(base, assessments=changed)


@cases(
    [
        (
            "source_excerpt as seller observation, same interaction",
            lambda: candidate(
                source_excerpt=excerpt(
                    "Rep note: buyer would look if pricing jumped at renewal", P.SELLER_OBSERVATION
                )
            ),
        ),
    ]
)
def test_rc32_candidate_source_excerpt_accepts(check):
    assert check()


@cases(
    [
        ("source_excerpt as plain string", lambda: candidate(source_excerpt="plain text")),
        (
            "source_excerpt is external evidence",
            lambda: candidate(source_excerpt=excerpt("x", P.EXTERNAL_EVIDENCE)),
        ),
        (
            "source_excerpt from a different interaction",
            lambda: candidate(source_excerpt=excerpt("x", source_id="INT-2")),
        ),
        (
            "criterion evidence from a later interaction",
            lambda: candidate(criterion_evidence=excerpt("x", source_id="INT-2")),
        ),
        (
            "candidate interaction id differs from all its evidence",
            lambda: candidate(source_interaction_id="INT-9"),
        ),
        ("one later-interaction excerpt among valid ones", _one_later_excerpt_among_valid),
    ]
)
def test_rc32_candidate_evidence_comes_from_its_own_interaction(check):
    """RC-32: later interactions and external evidence cannot establish C1-C4."""
    with pytest.raises(DomainValidationError):
        check()


# --- immutability (RC-33) -----------------------------------------------------


def _overwrite_buyer_statement():
    saved = confirmed_record()
    saved.candidate.buyer_exact_statement = "edited"


@cases([("overwrite buyer statement", _overwrite_buyer_statement)])
def test_rc33_saved_buyer_statement_cannot_be_overwritten(check):
    with pytest.raises(FrozenInstanceError):
        check()


def _domain_dataclasses():
    found = []
    for module in (models, baec_rules, state_machine):
        for obj in vars(module).values():
            if isinstance(obj, type) and is_dataclass(obj) and obj.__module__ == module.__name__:
                found.append(obj)
    return found


@pytest.mark.parametrize("cls", _domain_dataclasses(), ids=lambda c: c.__name__)
def test_rc33_every_domain_dataclass_is_frozen(cls):
    assert cls.__dataclass_params__.frozen


def test_rc33_source_fields_on_a_saved_record_cannot_be_reassigned():
    saved = confirmed_record()
    for target, name in (
        (saved, "captured_at"),
        (saved, "candidate"),
        (saved.candidate, "source_excerpt"),
        (saved.candidate, "source_interaction_id"),
        (saved.candidate.source_excerpt, "text"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, "edited")


def test_rc33_normalized_condition_is_separate_from_source_evidence():
    names = {f.name for f in fields(models.BaecRecord)}
    assert "normalized_condition" in names
    assert "normalized_condition" not in {f.name for f in fields(models.BaecCandidate)}
