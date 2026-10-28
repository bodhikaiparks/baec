"""Phase 6D-B2: validation v2 numeric, numeric-kind, unit, compound, and comparator grounding.

docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §4. An implementation safeguard (supports RC-18,
itself IMPLEMENTATION): it decides only whether an AI artifact exists. These are deterministic
software tests on synthetic text, not evidence about any model or about BAEC theory.
"""

import ast
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from baec_app.ai import grounding
from baec_app.ai.contracts import BaecExtractionOutput
from baec_app.ai.grounding import (
    COMPARATOR_CLASSES,
    GROUNDING_FAILURE_CODES,
    NUMERIC_KINDS,
    UNITS,
    UNRESOLVED_EXPRESSIONS,
    Compound,
    Magnitude,
    Multiplier,
    Range,
    grounding_failures,
    tokenize,
)
from baec_app.ai.provenance import RemoteOutcome, RunStatus, TerminalResult
from baec_app.ai.validation import (
    SEMANTIC_FAILURE_CODES,
    SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION,
    VALIDATION_VERSION,
    VALIDATION_VERSION_V1,
    validate_extraction,
)
from baec_app.data.ai_provenance import AiRunStatus
from baec_app.data.database import AI_GROUNDING_FAILURE_CODES, RepositoryVerificationError
from baec_app.domain.enums import ThresholdComparator
from tests.ai_builders import THRESHOLD_TEXT, FakeProvider, ai_counts, as_text, hypotheses, output, response, world  # noqa: F401
from tests.ai_provenance_builders import COMPLETED, ai_rows, db, digest, outcome_for, result_record, run_record  # noqa: F401

V1 = "baec-extraction-validation/v1"
V2 = "baec-extraction-validation/v2"
REPO = Path(__file__).resolve().parents[1]


def failures(source: str, model: str, scope: str = "normalization") -> set[str]:
    return grounding_failures(source, [(scope, model)])


def failure(source: str, model: str) -> str | None:
    """The single normalization failure ending for one model text, or None when grounded."""
    found = failures(source, model)
    assert len(found) <= 1, found
    return next(iter(found)).removeprefix("normalization_") if found else None


def only(text: str):
    (token,) = tokenize(text)
    return token


# --- version and vocabulary ------------------------------------------------------------------------------


def test_the_current_validator_is_v2_and_v1_stays_a_recognized_historical_version():
    assert VALIDATION_VERSION == V2 and VALIDATION_VERSION_V1 == V1
    assert tuple(SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION) == (V1, V2)
    assert len(SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V1]) == 20
    assert len(SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V2]) == 38


def test_the_18_grounding_codes_are_exactly_three_scopes_by_six_endings_in_both_layers():
    expected = {f"{scope}_{ending}" for scope in ("normalization", "explanation", "uncertainty")
                for ending in ("number_unsupported", "numeric_kind_changed", "unit_changed", "compound_unsupported",
                               "comparator_changed", "comparator_unresolved")}
    assert set(GROUNDING_FAILURE_CODES) == set(AI_GROUNDING_FAILURE_CODES) == expected and len(expected) == 18
    assert list(GROUNDING_FAILURE_CODES) == sorted(GROUNDING_FAILURE_CODES)
    assert not expected & set(SEMANTIC_FAILURE_CODES)
    assert set(SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V2]) == set(SEMANTIC_FAILURE_CODES) | expected


def test_the_closed_kinds_units_and_comparator_classes_are_exactly_the_designs():
    assert NUMERIC_KINDS == ("plain", "percent", "percentage_point", "currency:unspecified_dollar", "currency:USD",
                             "currency:EUR", "currency:GBP")
    assert UNITS == ("day", "business_day", "week", "month", "quarter", "year", "hour")
    assert UNRESOLVED_EXPRESSIONS == ("past", "beyond", "within", "over")
    domain = {member.value for member in ThresholdComparator}
    assert set(COMPARATOR_CLASSES) <= domain
    assert set(COMPARATOR_CLASSES) == domain - {"QUALITATIVE_ONLY", "NONE_STATED"}


PREFIX_LEXICON = {
    "GREATER_THAN": (">", "more than", "greater than", "above", "exceeds", "exceeding", "in excess of"),
    "AT_LEAST": (">=", "≥", "at least", "no less than", "not less than", "minimum of"),
    "LESS_THAN": ("<", "less than", "fewer than", "below", "under"),
    "AT_MOST": ("<=", "≤", "at most", "no more than", "not more than", "maximum of", "up to"),
    "EXACTLY": ("=", "exactly"),
    "APPROXIMATELY": ("about", "around", "roughly", "approximately", "~"),
}
POSTFIX_LEXICON = {
    "AT_LEAST": ("or more", "or greater", "or higher", "or above", "or longer", "and up"),
    "AT_MOST": ("or less", "or fewer", "or lower", "or below", "or shorter"),
}


def test_the_comparator_lexicon_is_exactly_the_approved_one():
    prefix = {(" ".join(words), cls) for words, cls in grounding._PREFIX_COMPARATORS
              if not cls.startswith("unresolved:")}
    assert prefix == {(form, cls) for cls, forms in PREFIX_LEXICON.items() for form in forms}
    assert {(" ".join(words), cls) for words, cls in grounding._POSTFIX_COMPARATORS} == {
        (form, cls) for cls, forms in POSTFIX_LEXICON.items() for form in forms}
    unresolved = {words for words, cls in grounding._PREFIX_COMPARATORS if cls.startswith("unresolved:")}
    assert unresolved == {(word,) for word in UNRESOLVED_EXPRESSIONS}


# --- magnitudes ----------------------------------------------------------------------------------------------

MAGNITUDE = {
    # (source, model, expected ending or None)
    "unsupported integer": ("a rise of 10% at renewal", "a rise of 12% at renewal", "number_unsupported"),
    "unsupported decimal": ("a rise of 10.5%", "a rise of 10.6%", "number_unsupported"),
    "comma grouping": ("a $12,500 increase", "a $12500 increase", None),
    "comma grouping reversed": ("a $12500 increase", "a $12,500 increase", None),
    "comma grouping in words": ("a $12,500 increase", "a 12,500 dollar increase", None),
    "equal decimal 10.0": ("10 units", "10.0 units", None),
    "equal decimal 10.00": ("10.0 units", "10.00 units", None),
    "leading zero": ("05 sites", "5 sites", None),
    "leading zero decimal": ("05.0 sites", "5 sites", None),
    "leading zero reversed": ("5 sites", "05 sites", None),
    "signed positive": ("+5 sites", "5 sites", None),
    "signed positive reversed": ("5 sites", "+5 sites", None),
    "negative distinct": ("-5 sites", "5 sites", "number_unsupported"),
    "negative distinct reversed": ("5 sites", "-5 sites", "number_unsupported"),
    "unicode minus equals hyphen minus": ("−5 sites", "-5 sites", None),
    "word ten": ("ten sites", "10 sites", None),
    "word twenty-one": ("21 sites", "twenty-one sites", None),
    "word hundreds": ("105 sites", "one hundred and five sites", None),
    "word hundreds without and": ("one hundred five sites", "105 sites", None),
    "word thousands": ("2,500 sites", "two thousand five hundred sites", None),
    "word case-insensitive": ("Ten sites", "10 sites", None),
    "word percent": ("ten percent", "10%", None),
    "word unit": ("four weeks", "4 weeks", None),
    "meta number one": ("no numbers here", "one condition is stated", "number_unsupported"),
    "ordinal word is not a number": ("no numbers here", "the first condition", None),
    "vague word is not a number": ("prices rise significantly", "prices rise significantly", None),
    "vague word never becomes a number": ("prices rise significantly", "prices rise 10%", "number_unsupported"),
    "scale suffix not converted": ("a 12.5k increase", "a 12,500 increase", "number_unsupported"),
    "scale suffix not derived": ("a 12,500 increase", "a 12.5k increase", "compound_unsupported"),
    "scale word not converted": ("12.5 thousand dollars", "$12,500", "number_unsupported"),
    "scale word exact echo": ("12.5 thousand dollars", "12.5 thousand dollars", None),
    "unit conversion": ("4 weeks", "28 days", "number_unsupported"),
    "sum": ("5% and 3%", "8%", "number_unsupported"),
    "difference": ("from 15% to 10%", "a 5% drop", "number_unsupported"),
    "annualization": ("$1,000 a month", "$12,000 a year", "number_unsupported"),
    "inferred percentage": ("from $100 to $110", "a 10% rise", "number_unsupported"),
    "multiplier echoed": ("prices double", "if prices double", None),
    "multiplier introduced": ("prices rise", "prices double", "number_unsupported"),
}


@pytest.mark.parametrize("case", MAGNITUDE.values(), ids=MAGNITUDE.keys())
def test_magnitudes_ground_only_through_approved_equivalences(case):
    source, model, expected = case
    assert failure(source, model) == expected


def test_number_word_grammar_is_closed():
    assert only("twenty-five").value == 25 and only("nineteen").value == 19 and only("zero").value == 0
    assert only("nine hundred and ninety-nine thousand").value == 999000
    for outside in ("first", "second", "a", "an", "couple", "few", "several", "dozens", "twenty five"):
        tokens = tokenize(outside)
        assert all(not isinstance(t, Magnitude) or outside == "twenty five" for t in tokens), outside
    assert [t.value for t in tokenize("twenty five")] == [20, 5]  # tens "-" units only
    assert type(only("twelve hundred")) is Compound  # outside the grammar: exact echo only
    assert type(only("3 million")) is Compound and type(only("ten million")) is Compound


# --- numeric kind ------------------------------------------------------------------------------------------

KIND = {
    "plain to percent": ("10 more sites", "10% more sites"),
    "percent to plain": ("10% more", "10 more"),
    "percent to percentage point": ("a 10% rise", "a 10 percentage point rise"),
    "percentage point to percent": ("a 5 percentage point rise", "a 5 percent rise"),
    "unspecified dollar to USD": ("a $10 fee", "a USD 10 fee"),
    "USD to unspecified dollar": ("a USD 10 fee", "a $10 fee"),
    "USD to EUR": ("a 10 USD fee", "a 10 EUR fee"),
    "EUR to GBP": ("an EUR 10 fee", "a GBP 10 fee"),
    "unspecified dollar to EUR": ("a $10 fee", "a €10 fee"),
    "percent to dollar": ("a 10% fee", "a $10 fee"),
}


@pytest.mark.parametrize("case", KIND.values(), ids=KIND.keys())
def test_a_changed_numeric_kind_is_numeric_kind_changed_never_unit_changed(case):
    source, model = case
    assert failure(source, model) == "numeric_kind_changed"


def test_percent_spellings_are_one_kind_and_percentage_point_is_another():
    for form in ("10%", "10 %", "10 %", "10 percent", "10 Percent", "10 per cent"):
        assert only(form) == Magnitude(Decimal(10), "percent", "none", "NONE"), form
    for form in ("10 percentage points", "10 percentage point"):
        assert only(form).kind == "percentage_point"
    assert only("10  %").kind == "plain"  # two spaces: "%" is not bound (one space or no-break space only)


CURRENCY_FORMS = {
    "€10": "currency:EUR", "EUR 10": "currency:EUR", "10 EUR": "currency:EUR", "10 euro": "currency:EUR",
    "10 euros": "currency:EUR", "10 Euros": "currency:EUR",
    "£10": "currency:GBP", "GBP 10": "currency:GBP", "10 GBP": "currency:GBP", "10 British pound": "currency:GBP",
    "10 British pounds": "currency:GBP", "10 pound sterling": "currency:GBP", "10 pounds sterling": "currency:GBP",
    "USD 10": "currency:USD", "10 USD": "currency:USD", "US$10": "currency:USD", "10 US dollar": "currency:USD",
    "10 US dollars": "currency:USD", "10 U.S. dollar": "currency:USD", "10 U.S. dollars": "currency:USD",
    "10 United States dollar": "currency:USD", "10 United States dollars": "currency:USD",
    "$10": "currency:unspecified_dollar", "10 dollar": "currency:unspecified_dollar",
    "10 dollars": "currency:unspecified_dollar", "10 Dollars": "currency:unspecified_dollar",
}


@pytest.mark.parametrize("form", CURRENCY_FORMS)
def test_every_approved_currency_form_sets_its_kind(form):
    assert only(form) == Magnitude(Decimal(10), CURRENCY_FORMS[form], "none", "NONE")


@pytest.mark.parametrize("form", ["10 usd", "us 10", "10 us dollars", "10 eur", "Usd 10"])
def test_lowercase_codes_and_ordinary_us_are_never_currency(form):
    assert all(t.kind == "plain" for t in tokenize(form) if isinstance(t, Magnitude))


@pytest.mark.parametrize("form", ["10 pounds", "10 pound", "10 Pounds"])
def test_bare_pounds_stay_unresolved_and_pass_only_by_exact_echo(form):
    assert type(only(form)) is Compound
    assert failure(form, form) is None
    assert failure(form, "£10") == "number_unsupported"  # never silently read as GBP
    assert failure("£10", form) == "compound_unsupported"


SPACED_MARKERS = {
    "$ 10": ("$10", "currency:unspecified_dollar", 10), "$   10": ("$10", "currency:unspecified_dollar", 10),
    "$\t10": ("$10", "currency:unspecified_dollar", 10),
    "\u20ac 10": ("\u20ac10", "currency:EUR", 10), "\u00a3 10": ("\u00a310", "currency:GBP", 10),
    "US$ 10": ("US$10", "currency:USD", 10), "USD 10": ("USD 10", "currency:USD", 10),
    "EUR 10": ("EUR 10", "currency:EUR", 10), "GBP 10": ("GBP 10", "currency:GBP", 10),
    "$ 12,500": ("$12,500", "currency:unspecified_dollar", 12500),
    "\u20ac 12,500": ("\u20ac12,500", "currency:EUR", 12500), "\u00a3 12,500": ("\u00a312,500", "currency:GBP", 12500),
}


@pytest.mark.parametrize("spaced", SPACED_MARKERS)
def test_a_currency_marker_binds_across_horizontal_whitespace(spaced):
    attached, kind, value = SPACED_MARKERS[spaced]
    assert only(spaced) == only(attached) == Magnitude(Decimal(value), kind, "none", "NONE")
    plain = f"{value:,}" if "," in spaced else str(value)
    assert failure(spaced, attached) is None  # formatting whitespace alone changes nothing
    assert failure(attached, spaced) is None
    assert failure(spaced, plain) == "numeric_kind_changed"
    assert failure(f"more than {spaced}", f"more than {attached}") is None


@pytest.mark.parametrize("text", ["$\n10", "USD\n10", "\u20ac\r\n10", "$\n\n10"])
def test_a_currency_marker_never_binds_across_a_line_break(text):
    assert only(text).kind == "plain"


@pytest.mark.parametrize("text", ["us 10", "pound 10", "pounds 10", "usd 10", "Us$ 10"])
def test_spaced_prefix_binding_adds_nothing_outside_the_closed_lexicon(text):
    assert all(t.kind == "plain" for t in tokenize(text) if isinstance(t, Magnitude))


def test_currency_is_never_converted_and_specificity_never_changes():
    assert failure("a fee of €10", "a fee of $10") == "numeric_kind_changed"
    assert failure("a fee of EUR 10", "a fee of USD 11") == "number_unsupported"
    assert failure("$12,500", "12,500 dollars") is None and failure("€40", "40 euros") is None
    assert failure("EUR 40", "€40") is None


# --- units ---------------------------------------------------------------------------------------------------

UNIT = {
    "plural to singular adjective": ("within 4 weeks", "within a 4-week window", "comparator_unresolved"),  # drops "within"
    "singular plural folding": ("a 4-week window", "4 weeks", None),
    "number word with unit": ("four weeks", "4 weeks", None),
    "word adjective": ("a ten-week lead time", "10 weeks", None),
    "changed unit": ("4 weeks", "4 days", "unit_changed"),
    "removed unit": ("4 weeks", "4", "unit_changed"),
    "introduced unit": ("4 sites", "4 weeks", "unit_changed"),
    "business day is its own unit": ("5 business days", "5 days", "unit_changed"),
    "business days fold": ("5 business days", "5 business day", None),
    "conversion": ("1 year", "12 months", "number_unsupported"),
    "quarter is a unit": ("2 quarters", "2 quarter", None),
    "hours": ("48 hours", "48 days", "unit_changed"),
}


@pytest.mark.parametrize("case", UNIT.values(), ids=UNIT.keys())
def test_a_grounded_unit_is_part_of_the_numeric_meaning(case):
    source, model, expected = case
    assert failure(source, model) == expected


# --- comparators ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("form,cls", [(f, c) for c, forms in PREFIX_LEXICON.items() for f in forms])
def test_every_prefix_expression_binds_its_class(form, cls):
    assert only(f"{form} 10%").comparator == cls
    assert only(f"{form.upper()} 10%").comparator == cls  # case-insensitive


@pytest.mark.parametrize("form,cls", [(f, c) for c, forms in POSTFIX_LEXICON.items() for f in forms])
def test_every_postfix_expression_binds_its_class(form, cls):
    assert only(f"10% {form}").comparator == cls
    assert only(f"4 weeks {form}").comparator == cls


CLASS_EXAMPLES = {"NONE": "{}", "GREATER_THAN": "more than {}", "AT_LEAST": "at least {}", "LESS_THAN": "less than {}",
                  "AT_MOST": "at most {}", "EXACTLY": "exactly {}", "APPROXIMATELY": "about {}"}


@pytest.mark.parametrize("source_class", CLASS_EXAMPLES)
@pytest.mark.parametrize("model_class", CLASS_EXAMPLES)
def test_the_comparator_mutation_matrix(source_class, model_class):
    source = "If the price rises " + CLASS_EXAMPLES[source_class].format("10%") + " we would look."
    model = "A price rise of " + CLASS_EXAMPLES[model_class].format("10%") + "."
    expected = None if source_class == model_class else "comparator_changed"  # added, dropped, or changed
    assert failure(source, model) == expected


@pytest.mark.parametrize("prefix,postfix", [("at least", "or more"), ("at least", "and up"), ("at most", "or less"),
                                            ("at least", "+")])
def test_prefix_and_postfix_forms_of_one_class_are_equivalent(prefix, postfix):
    model = "5+ weeks" if postfix == "+" else f"5 weeks {postfix}"
    assert failure(f"{prefix} 5 weeks", model) is None


def test_the_longest_comparator_match_wins():
    assert only("no less than 5").comparator == "AT_LEAST"
    assert only("not less than 5").comparator == "AT_LEAST"
    assert only("no more than 5").comparator == "AT_MOST"
    assert only("not more than 5").comparator == "AT_MOST"
    assert failure("no less than 5 sites", "less than 5 sites") == "comparator_changed"


def test_prefix_plus_is_a_sign_and_postfix_plus_is_at_least():
    assert only("+5") == Magnitude(Decimal(5), "plain", "none", "NONE")
    assert only("5+").comparator == "AT_LEAST" and only("5+ weeks") == only("5 weeks+") == only("at least 5 weeks")
    assert type(only("5+3")) is Compound  # a "+" followed by a digit is a compound, not a comparator
    assert failure("5+ sites", "5 sites") == "comparator_changed"
    assert failure("+5 sites", "5 sites") is None


def test_comparators_bind_only_when_adjacent():
    assert only("more than the 10").comparator == "NONE"
    assert only("more than (10)").comparator == "NONE"
    assert only("more than: 10").comparator == "NONE"
    assert only("more than $10").comparator == "GREATER_THAN"
    assert only("more than USD 10") == Magnitude(Decimal(10), "currency:USD", "none", "GREATER_THAN")


def test_two_bound_expressions_are_conflicting_and_unresolved():
    assert only("about 5 or more").comparator == "CONFLICTING"
    assert failure("about 5 or more", "about 5 or more") == "comparator_unresolved"
    assert failure("about 5", "about 5 or more") == "comparator_unresolved"


SCOPES = ("normalization", "explanation", "uncertainty")


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("word", UNRESOLVED_EXPRESSIONS)
def test_unresolved_expressions_pass_only_by_exact_preservation(word, scope):
    source = f"if delivery slips {word} 30 days"
    assert only(f"{word} 10%").comparator == f"unresolved:{word}"  # never mapped to a class
    assert failures(source, f"delivery {word} 30 days", scope) == set()
    assert failures(source, f"delivery {word.upper()} 30 days", scope) == set()


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("word", UNRESOLVED_EXPRESSIONS)
def test_rewriting_an_unresolved_expression_to_a_classified_one_is_unresolved(word, scope):
    source = f"if delivery slips {word} 30 days"
    for classified in ("more than", "at least", "less than", "at most", "exactly", "about"):
        assert failures(source, f"delivery {classified} 30 days", scope) == {f"{scope}_comparator_unresolved"}
    assert failures("if delivery slips more than 30 days", f"delivery {word} 30 days", scope) == {
        f"{scope}_comparator_unresolved"}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("word", UNRESOLVED_EXPRESSIONS)
def test_rewriting_an_unresolved_expression_to_another_is_unresolved(word, scope):
    source = f"if delivery slips {word} 30 days"
    for other in (w for w in UNRESOLVED_EXPRESSIONS if w != word):
        assert failures(source, f"delivery {other} 30 days", scope) == {f"{scope}_comparator_unresolved"}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("word", UNRESOLVED_EXPRESSIONS)
def test_dropping_an_unresolved_expression_is_unresolved(word, scope):
    assert failures(f"if delivery slips {word} 30 days", "delivery 30 days", scope) == {
        f"{scope}_comparator_unresolved"}
    assert failures(f"an increase {word} 10%", "an increase of 10%", scope) == {f"{scope}_comparator_unresolved"}


@pytest.mark.parametrize("scope", SCOPES)
def test_classified_comparator_changes_stay_comparator_changed(scope):
    changed = {f"{scope}_comparator_changed"}
    assert failures("more than 10%", "at least 10%", scope) == changed  # changed class
    assert failures("more than 10%", "10%", scope) == changed  # classified comparator dropped
    assert failures("a 10% rise", "a rise of more than 10%", scope) == changed  # introduced where NONE


def test_over_is_never_read_as_greater_than():
    assert failure("an increase over 10%", "an increase of more than 10%") == "comparator_unresolved"
    assert failure("an increase of more than 10%", "an increase over 10%") == "comparator_unresolved"
    assert failure("an increase over 10%", "an increase over 10%") is None


# --- compounds -----------------------------------------------------------------------------------------------

COMPOUND = {
    "date permutation": ("by 3/15", "by 15/3", "compound_unsupported"),
    "date exact echo": ("by 3/15", "by 3/15", None),
    "component digits never ground": ("by 3/15", "by the 15th", "compound_unsupported"),
    "component digit as magnitude": ("by 3/15", "within 15 days", "number_unsupported"),
    "date leading zero structural": ("by 03/05", "by 3/5", "compound_unsupported"),
    "iso date rewrite": ("on 2027-03-15", "on 2027-3-15", "compound_unsupported"),
    "month name order": ("on March 15", "on 15 March", "compound_unsupported"),
    "month name echo": ("on March 15, 2027", "on March 15", None),
    "ratio reversed": ("a 3:1 ratio", "a 1:3 ratio", "compound_unsupported"),
    "time changed": ("at 9:30", "at 9:45", "compound_unsupported"),
    "version changed": ("version 1.2.3", "version 1.2.4", "compound_unsupported"),
    "ordinal changed": ("the 2nd quarter", "the 3rd quarter", "compound_unsupported"),
    "period label changed": ("in Q3", "in Q4", "compound_unsupported"),
    "period label echo": ("in FY27", "in FY27", None),
    "24/7 echo": ("24/7 support", "24/7 support", None),
    "24/7 not split": ("24/7 support", "24 hours", "number_unsupported"),
    "non-ascii digits": ("１０ units", "10 units", "number_unsupported"),
    "non-ascii digits echo": ("１０ units", "１０ units", None),
    "range hyphen equals en dash": ("a 10-15% rise", "a 10–15% rise", None),
    "range with unit": ("10-15 weeks", "10–15 weeks", None),
    "range reversed": ("a 10-15% rise", "a 15-10% rise", "compound_unsupported"),
    "range kind changed": ("a 10-15% rise", "a 10-15 rise", "compound_unsupported"),
    "range end alone": ("a 10-15% rise", "a 10% rise", "number_unsupported"),
    "range leading zero structural": ("5-10 sites", "05-10 sites", "compound_unsupported"),
    "range comma grouping": ("1,000-2,000 units", "1000-2000 units", None),
}


@pytest.mark.parametrize("case", COMPOUND.values(), ids=COMPOUND.keys())
def test_compounds_are_exact_echo_except_the_approved_compact_range(case):
    source, model, expected = case
    assert failure(source, model) == expected


def test_compound_shapes_are_tokenized_without_splitting_their_digits():
    for text in ("3/15", "3/15/2027", "2027-03-15", "9:30", "3:1", "2nd", "21st", "Q3", "H1", "FY27", "24/7", "1.2.3",
                 "12.5k", "12.5K", "3m", "2bn", "5x", "B2B"):
        assert type(only(text)) is Compound, text
    assert only("10-15%") == Range(Decimal(10), Decimal(15), "percent", "none", "NONE")
    assert only("4-week") == Magnitude(Decimal(4), "plain", "week", "NONE")
    assert type(only("half")) is Multiplier


# --- field coverage and codes ------------------------------------------------------------------------------


def codes(value) -> tuple[str, ...]:
    return validate_extraction(BaecExtractionOutput.model_validate(value), source_interaction_id="INT-T",
                               source_text=THRESHOLD_TEXT)


def with_explanation(text):
    value = output()
    value["criterion_hypotheses"][2]["explanation"] = text
    return value


FIELDS = {
    "normalized_condition": (output(normalized_condition="A price increase of more than 12% at renewal."),
                             "normalization_number_unsupported"),
    "normalized_evaluation_link": (output(normalized_evaluation_link="Within 30 days the buyer would reopen it."),
                                   "normalization_number_unsupported"),
    "explanation": (with_explanation("All 4 criteria are met."), "explanation_number_unsupported"),
    "uncertainty": (output(uncertainties=["Whether 10 percentage points was meant."]),
                    "uncertainty_numeric_kind_changed"),
}


@pytest.mark.parametrize("field", FIELDS)
def test_each_model_authored_field_is_grounded_independently(field):
    value, code = FIELDS[field]
    assert codes(value) == (code,)


def test_a_grounded_extraction_has_no_codes():
    assert codes(output()) == ()


def test_excerpts_identifiers_and_references_are_outside_the_grounding_guard():
    value = output(excerpts=[{"excerpt_id": "e12345", "source_interaction_id": "INT-T",
                              "text": "If our supplier raises pricing by more than 10% at renewal, we would reopen the evaluation.",
                              "attributed_speaker": "buyer"},
                             {"excerpt_id": "e2026", "source_interaction_id": "INT-T",
                              "text": "We would not switch for anything under 10%.", "attributed_speaker": "buyer"}],
                   criterion_hypotheses=hypotheses(refs=("e12345", "e2026")))
    assert codes(value) == ()


def test_several_violations_give_sorted_unique_bounded_codes_with_no_values():
    value = output(normalized_condition="A rise of 37% on 3/15.",
                   normalized_evaluation_link="At least 10% at renewal.",
                   uncertainties=["Whether 37% or 41% applies.", "Within 9 weeks."])
    value["criterion_hypotheses"][0]["explanation"] = "Over 10% is stated."
    found = codes(value)
    assert found == ("explanation_comparator_unresolved", "normalization_comparator_changed",
                     "normalization_compound_unsupported", "normalization_number_unsupported",
                     "uncertainty_number_unsupported")
    assert list(found) == sorted(set(found)) and set(found) <= set(GROUNDING_FAILURE_CODES)
    for code in found:
        assert not any(character.isdigit() for character in code) and ":" not in code
        for value_text in ("37", "41", "3/15", "10", "renewal", "INT-T", "e1"):
            assert value_text not in code


def test_tokenization_is_deterministic():
    text = THRESHOLD_TEXT + " Over 10%, about 5 or more, 3/15, €40, ten-week, 10-15%."
    assert tokenize(text) == tokenize(text) == tokenize(str(text))


# --- version-specific persistence -----------------------------------------------------------------------------


def test_a_v2_run_persists_grounding_codes_and_reads_them_back(db):
    _, _, store = db
    store.record_run(run_record(validation_version=V2))
    outcome = outcome_for(AiRunStatus.SEMANTIC_VALIDATION_FAILURE)
    object.__setattr__(outcome.result, "failure_codes", ("excerpt_blank", "normalization_number_unsupported"))
    store.record_terminal_outcome(outcome)
    assert store.get_result("RUN-1").failure_codes == ("excerpt_blank", "normalization_number_unsupported")


def test_a_v1_run_still_cannot_persist_a_grounding_code(db):
    _, connection, store = db
    store.record_run(run_record(validation_version=V1))
    outcome = outcome_for(AiRunStatus.SEMANTIC_VALIDATION_FAILURE)
    object.__setattr__(outcome.result, "failure_codes", ("normalization_number_unsupported",))
    with pytest.raises(RepositoryVerificationError):
        store.record_terminal_outcome(outcome)
    assert ai_rows(connection)["ai_run_results"] == 0


RAW_RUN = (
    "INSERT INTO ai_runs (ai_run_id, provider, task_type, task_version, account_id, interaction_id, requested_model, "
    "sdk_name, sdk_version, prompt_version, prompt_digest, input_version, input_digest, output_schema_version, "
    "output_schema_digest, canonicalization_version, request_spec_version, request_digest, validation_version, "
    "requested_at) VALUES ('RAW-1', 'anthropic', 't', 'v1', 'ACC-1', 'INT-1', 'm', 'anthropic', '1', 'p', ?, 'i', ?, "
    "'o', ?, 'c', 's', ?, ?, '2026-04-01T09:00:00.000000+00:00')"
)
RAW_SEMANTIC = (
    "INSERT INTO ai_run_results (ai_run_id, status, remote_outcome, stop_reason, failure_codes, output_digest, "
    "completed_at) VALUES ('RAW-1', 'semantic_validation_failure', 'response_received', 'end_turn', ?, ?, "
    "'2026-04-01T09:00:20.000000+00:00')"
)


@pytest.mark.parametrize("code", GROUNDING_FAILURE_CODES)
def test_sql_accepts_each_grounding_code_under_v2_and_refuses_it_under_v1(db, code):
    _, connection, _ = db
    connection.execute(RAW_RUN, (*[digest(x) for x in "pior"], V1))
    with pytest.raises(sqlite3.IntegrityError, match="closed vocabulary"):
        connection.execute(RAW_SEMANTIC, (f'["{code}"]', digest("out")))
    connection.execute(RAW_RUN.replace("'RAW-1'", "'RAW-2'"), (*[digest(x) for x in "pior"], V2))
    connection.execute(RAW_SEMANTIC.replace("'RAW-1'", "'RAW-2'"), (f'["{code}"]', digest("out")))
    assert ai_rows(connection)["ai_run_results"] == 1


@pytest.mark.parametrize("codes_", [("normalization_number_unsupported",),
                                    ("excerpt_blank", "explanation_comparator_changed")])
def test_record_construction_and_the_ai_terminal_result_accept_v2_codes(codes_):
    assert replace(result_record(AiRunStatus.SEMANTIC_VALIDATION_FAILURE), failure_codes=codes_).failure_codes == codes_
    terminal = TerminalResult(status=RunStatus.SEMANTIC_VALIDATION_FAILURE, remote_outcome=RemoteOutcome.RESPONSE_RECEIVED,
                              completed_at=COMPLETED, failure_codes=codes_)
    assert terminal.failure_codes == codes_


# --- the service: the C09-class regression ---------------------------------------------------------------


def test_a_c09_class_invented_number_ends_as_a_semantic_failure_with_no_artifact(world):
    """Implementation regression for the C09 class (design §4.8): synthetic, not a rerun of Phase 6C.

    The source has no 12%; the model-authored normalization introduces it.
    """
    assert "12" not in THRESHOLD_TEXT
    before = world.domain_dump()
    text = as_text(output(normalized_condition="A price increase of more than 12% at renewal."))
    provider = FakeProvider(response(text))
    result = world.run(provider)
    assert result.status is RunStatus.SEMANTIC_VALIDATION_FAILURE
    assert result.artifact_id is None and result.structured_output is None
    assert len(provider.invoked) == 1 and len(provider.prepared) == 1  # one attempt, never retried
    store = world.store
    assert store.get_run(result.ai_run_id).validation_version == V2
    stored = store.get_result(result.ai_run_id)
    assert stored.failure_codes == ("normalization_number_unsupported",)
    assert store.get_output(result.ai_run_id).raw_output_text == text  # raw output kept under the existing rules
    counts = ai_counts(world)
    assert counts["ai_artifacts"] == 0 and counts["ai_artifact_excerpts"] == 0 and counts["ai_runs"] == 1
    assert world.domain_dump() == before  # no authoritative or domain state changed


@pytest.mark.parametrize("change,code", [
    (dict(normalized_condition="A price increase of at least 10% at renewal."), "normalization_comparator_changed"),
    (dict(normalized_condition="A price increase of more than 10 percentage points."), "normalization_numeric_kind_changed"),
    (dict(normalized_evaluation_link="The buyer would reopen it within 2 weeks."), "normalization_number_unsupported"),
    (dict(uncertainties=["Whether the 10% applies by 3/15."]), "uncertainty_compound_unsupported"),
], ids=["comparator", "kind", "evaluation link", "uncertainty compound"])
def test_every_grounding_failure_is_recorded_and_builds_no_artifact(world, change, code):
    result = world.run(FakeProvider(response(as_text(output(**change)))))
    assert result.status is RunStatus.SEMANTIC_VALIDATION_FAILURE and result.artifact_id is None
    assert code in world.store.get_result(result.ai_run_id).failure_codes
    assert ai_counts(world)["ai_artifacts"] == 0


def test_a_grounded_output_still_succeeds_under_v2(world):
    result = world.run(FakeProvider(response(as_text(output()))))
    assert result.status is RunStatus.SUCCESS and result.artifact_id is not None
    assert world.store.get_run(result.ai_run_id).validation_version == V2


# --- defense in depth: the live harness keeps its own independent check ---------------------------------------


def test_the_live_harness_never_imports_the_production_grounding():
    tree = ast.parse((REPO / "tests" / "live" / "harness.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not {name for name in imported if name and ("grounding" in name or name == "baec_app.ai.validation")}
