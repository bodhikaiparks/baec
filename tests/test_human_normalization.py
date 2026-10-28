"""Phase 7F-A: baec-human-normalization-validation/v1 (design §11), checked deterministically.

A numeric and comparator grounding contract for final human-reviewed normalizations, against the
human-selected verbatim evidence only. Passing it proves nothing about non-numeric meaning, BAEC status,
evidence provenance, findings, stringency, buyer role, or account state.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from baec_app.ai import grounding
from baec_app.application import human_normalization as module
from baec_app.application.human_normalization import (
    FAILURE_ENDINGS,
    HUMAN_NORMALIZATION_FAILURE_CODES,
    HUMAN_NORMALIZATION_VALIDATION_VERSION,
    MAX_NORMALIZATION_LENGTH,
    NORMALIZATION_FIELDS,
    human_normalization_validation_version,
    validate_final_normalization,
)

MODULE = Path(module.__file__)
SOURCE = "Buyer: If our supplier raises pricing by more than 10% at renewal, we would reopen the evaluation."


def check(condition, evidence=(SOURCE,), link=None):
    return validate_final_normalization(normalized_condition=condition, normalized_evaluation_link=link,
                                        evidence_texts=tuple(evidence))


def condition_codes(*endings):
    return tuple(sorted(f"human_normalization_condition_{e}" for e in endings))


# --- identity and code set ------------------------------------------------------------------------


def test_the_contract_identity_is_pinned_and_distinct_from_ai_validation():
    assert HUMAN_NORMALIZATION_VALIDATION_VERSION == "baec-human-normalization-validation/v1"
    assert human_normalization_validation_version() == HUMAN_NORMALIZATION_VALIDATION_VERSION
    from baec_app.data.database import BRIDGE_NORMALIZATION_VALIDATION_VERSION
    assert BRIDGE_NORMALIZATION_VALIDATION_VERSION == HUMAN_NORMALIZATION_VALIDATION_VERSION  # the 7C schema label
    assert HUMAN_NORMALIZATION_VALIDATION_VERSION != "baec-extraction-validation/v2"


def test_the_failure_codes_are_closed_field_prefixed_and_never_phase6_codes():
    from baec_app.ai.validation import SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION
    assert NORMALIZATION_FIELDS == (("normalized_condition", "condition"),
                                    ("normalized_evaluation_link", "evaluation_link"))
    assert FAILURE_ENDINGS == ("blank", "too_long", "evidence_missing", "number_unsupported", "numeric_kind_changed",
                               "currency_changed", "unit_changed", "compound_unsupported", "comparator_changed",
                               "comparator_unresolved")
    assert len(HUMAN_NORMALIZATION_FAILURE_CODES) == 20
    assert HUMAN_NORMALIZATION_FAILURE_CODES == tuple(sorted(
        f"human_normalization_{field}_{ending}" for field in ("condition", "evaluation_link")
        for ending in ("blank", "too_long", "evidence_missing", "number_unsupported", "numeric_kind_changed",
                       "currency_changed", "unit_changed", "compound_unsupported", "comparator_changed",
                       "comparator_unresolved")))
    assert all(c.startswith(("human_normalization_condition_", "human_normalization_evaluation_link_"))
               for c in HUMAN_NORMALIZATION_FAILURE_CODES)
    phase6 = set().union(*SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION.values())
    assert not set(HUMAN_NORMALIZATION_FAILURE_CODES) & phase6


# --- structure: null, blank, length, evidence ----------------------------------------------------------


def test_null_values_are_valid_and_check_nothing():
    assert check(None) == ()
    assert check(None, evidence=()) == ()
    assert validate_final_normalization(normalized_condition="A price increase.", normalized_evaluation_link=None,
                                        evidence_texts=(SOURCE,)) == ()
    assert validate_final_normalization(normalized_condition=None, normalized_evaluation_link="It would reopen.",
                                        evidence_texts=(SOURCE,)) == ()


@pytest.mark.parametrize("value", ["", " ", "\t\n", "  "], ids=["empty", "space", "tab-newline", "unicode"])
def test_a_blank_non_null_value_is_refused(value):
    assert check(value) == condition_codes("blank")
    assert check(None, link=value) == ("human_normalization_evaluation_link_blank",)


def test_500_code_points_pass_and_501_are_refused_without_truncation():
    at_limit = "é" * MAX_NORMALIZATION_LENGTH  # 500 code points, 1000 UTF-8 bytes
    assert MAX_NORMALIZATION_LENGTH == 500 and check(at_limit) == ()
    assert check(at_limit + "e") == condition_codes("too_long")
    assert check(None, link=at_limit + "e") == ("human_normalization_evaluation_link_too_long",)


def test_a_non_null_value_needs_selected_evidence():
    assert check("A price increase at renewal.", evidence=()) == condition_codes("evidence_missing")


def test_wrong_types_are_refused_loudly():
    with pytest.raises(TypeError):
        check(10)
    with pytest.raises(TypeError):
        validate_final_normalization(normalized_condition=None, normalized_evaluation_link=None,
                                     evidence_texts=[SOURCE])
    with pytest.raises(TypeError):
        check("x", evidence=(SOURCE, b"bytes"))


def test_non_numeric_text_passes_this_limited_contract():
    assert check("The buyer would reconsider after a large price rise at renewal.") == ()
    assert check("Completely unrelated prose about weather.") == ()  # not a semantic-entailment check


def test_the_human_text_is_never_rewritten():
    value = "  More than 10% at renewal.  "
    assert check(value) == ()
    assert value == "  More than 10% at renewal.  "


# --- magnitude --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("Pricing rising by more than 10% at renewal.", ()),
    ("Pricing rising by more than 12% at renewal.", condition_codes("number_unsupported")),
    ("Pricing rising by more than 11% at renewal.", condition_codes("number_unsupported")),
    ("Pricing rising by more than 9.5% at renewal.", condition_codes("number_unsupported")),
    ("Pricing rising by more than ten percent at renewal.", ()),  # the tokenizer's own number-word equivalence
], ids=["supported", "invented", "changed magnitude", "different decimal", "tokenizer number word"])
def test_a_retained_number_must_be_supported(value, expected):
    assert check(value) == expected


def test_calculated_and_rounded_numbers_are_refused():
    assert check("A new price of $110.", evidence=("Today we pay $100 and it would rise by 10%.",)) == \
        condition_codes("number_unsupported")
    assert check("An increase of 10%.", evidence=("An increase of 9.6%.",)) == condition_codes("number_unsupported")


def test_an_omitted_numeric_expression_is_not_required():
    assert check("A price increase at renewal.") == ()


# --- numeric kind and currency ------------------------------------------------------------------------


@pytest.mark.parametrize("value,evidence,expected", [
    ("An increase of 10 units.", "An increase of 10%.", condition_codes("numeric_kind_changed")),
    ("An increase of 10%.", "An increase of 10 units.", condition_codes("numeric_kind_changed")),
    ("An increase of 10 percentage points.", "An increase of 10%.", condition_codes("numeric_kind_changed")),
    ("A fee of €10.", "A fee of $10.", condition_codes("currency_changed")),
    ("A fee of 10.", "A fee of $10.", condition_codes("currency_changed")),
    ("A fee of $10.", "A fee of 10.", condition_codes("currency_changed")),
    ("A fee of $10.", "A fee of 10 dollars.", ()),
], ids=["percent to count", "count to percent", "percent to points", "changed currency", "currency removed",
        "currency introduced", "dollar forms the tokenizer equates"])
def test_numeric_kind_and_currency_are_preserved(value, evidence, expected):
    assert check(value, evidence=(evidence,)) == expected


# --- units ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("Delays of 2 weeks.", ()),
    ("Delays of two weeks.", ()),
    ("Delays of 2 months.", condition_codes("unit_changed")),
    ("Delays of 14 days.", condition_codes("number_unsupported")),
], ids=["same unit", "number word", "changed unit", "converted unit"])
def test_units_are_preserved_and_never_converted(value, expected):
    assert check(value, evidence=("Buyer: if deliveries slip by 2 weeks we would look elsewhere.",)) == expected


# --- comparators --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("source,value,expected", [
    ("more than 10%", "more than 10%", ()),
    ("> 10%", "more than 10%", ()),
    ("more than 10%", ">= 10%", condition_codes("comparator_changed")),
    ("at least 10%", "> 10%", condition_codes("comparator_changed")),
    ("less than 10%", "<= 10%", condition_codes("comparator_changed")),
    ("no more than 10%", "< 10%", condition_codes("comparator_changed")),
    ("10%", "more than 10%", condition_codes("comparator_changed")),
    ("more than 10%", "10%", condition_codes("comparator_changed")),
    ("about 10%", "exactly 10%", condition_codes("comparator_changed")),
], ids=["same", "symbol and words", "gt to ge", "ge to gt", "lt to le", "le to lt", "introduced", "dropped",
        "approximately to exactly"])
def test_classified_comparators_must_be_identical(source, value, expected):
    assert check(f"A price change of {value}.", evidence=(f"Buyer: a price change of {source} would matter.",)) == \
        expected


@pytest.mark.parametrize("word", grounding.UNRESOLVED_EXPRESSIONS)
def test_an_unresolved_comparator_word_is_preserved_exactly_when_its_expression_is_retained(word):
    evidence = (f"Buyer: we would look elsewhere if delays run {word} 30 days.",)
    assert check(f"Delays {word} 30 days.", evidence=evidence) == ()
    assert check("Delays of 30 days.", evidence=evidence) == condition_codes("comparator_unresolved")
    assert check("Delays of more than 30 days.", evidence=evidence) == condition_codes("comparator_unresolved")
    for other in grounding.UNRESOLVED_EXPRESSIONS:
        if other != word:
            assert check(f"Delays {other} 30 days.", evidence=evidence) == condition_codes("comparator_unresolved")
    assert check("Long delivery delays.", evidence=evidence) == ()  # the expression omitted entirely


def test_an_unresolved_word_cannot_be_introduced_over_a_classified_source():
    assert check("Delays past 30 days.", evidence=("Delays of more than 30 days.",)) == \
        condition_codes("comparator_unresolved")


# --- compounds, ranges, multipliers -----------------------------------------------------------------------


@pytest.mark.parametrize("source,value,expected", [
    ("a 10-15% increase", "a 10-15% increase", ()),
    ("a 10-15% increase", "a 10-20% increase", condition_codes("compound_unsupported")),
    ("3 million dollars", "3 million dollars", ()),
    ("3 million dollars", "4 million dollars", condition_codes("compound_unsupported")),
    ("at renewal on January 5", "at renewal on January 6", condition_codes("compound_unsupported")),
    ("if costs double", "if costs double", ()),
    ("if costs double", "if costs triple", condition_codes("number_unsupported")),
], ids=["range", "changed range", "scale word", "changed scale", "changed date", "multiplier", "changed multiplier"])
def test_ranges_compounds_and_multipliers_pass_only_by_exact_echo(source, value, expected):
    assert check(f"Buyer: {value}.", evidence=(f"Buyer: {source}.",)) == expected


# --- coherent same-excerpt support (critical) ----------------------------------------------------------------


def test_a_number_and_a_comparator_from_different_excerpts_never_combine():
    evidence = ("Pricing could rise 10% at renewal.", "Anything more than that would matter.")
    assert check("A rise of more than 10% at renewal.", evidence=evidence) == condition_codes("comparator_changed")
    assert check("A rise of 10% at renewal.", evidence=evidence) == ()


@pytest.mark.parametrize("value,evidence,ending", [
    ("A rise of more than 10%.", ("A rise of 10%.", "Anything more than 5% would matter."), "comparator_changed"),
    ("A fee of $40.", ("A fee of 40.", "A surcharge of $5."), "currency_changed"),
    ("Delays of 3 weeks.", ("Delays of 3.", "A buffer of 2 weeks."), "unit_changed"),
    ("Delays past 30 days.", ("Delays of 30 days.", "Anything past 10 days."), "comparator_unresolved"),
], ids=["comparator on another number", "currency on another number", "unit on another number",
        "unresolved word on another number"])
def test_attributes_of_another_excerpts_token_never_complete_a_match(value, evidence, ending):
    """Each attribute must come from the same candidate token as the value, never from a different token."""
    assert check(value, evidence=evidence) == condition_codes(ending)


def test_a_number_and_a_currency_or_unit_from_different_excerpts_never_combine():
    assert check("A fee of $40.", evidence=("A fee of 40.", "Paid in $ only.")) == condition_codes("currency_changed")
    assert check("Delays of 3 weeks.", evidence=("Delays of 3.", "Measured in weeks.")) == \
        condition_codes("unit_changed")


def test_different_expressions_may_each_be_supported_by_different_excerpts():
    evidence = ("Pricing more than 10% higher at renewal.", "Delays past 30 days.")
    assert check("Pricing more than 10% higher, or delays past 30 days.", evidence=evidence) == ()


def test_selected_excerpts_are_never_joined_into_one_token_stream():
    # Joined, "more than" + "10%" would tokenize as one GREATER_THAN magnitude; tokenized separately it cannot.
    assert grounding.tokenize("more than " + "10%")[0].comparator == "GREATER_THAN"
    assert check("more than 10%", evidence=("more than", "10%")) == condition_codes("comparator_changed")
    assert check("more than 10%", evidence=("more than 10%",)) == ()


def test_support_elsewhere_in_the_interaction_never_rescues_an_unselected_expression():
    interaction = "Buyer: Today we are fine.\nBuyer: If pricing rises by more than 10% at renewal, we would look."
    selected = ("Today we are fine.",)
    assert check("More than 10% at renewal.", evidence=(interaction,)) == ()  # the whole text would support it
    assert "more than 10%" in interaction and check("More than 10% at renewal.", evidence=selected) == \
        condition_codes("number_unsupported")
    assert check("More than 10% at renewal.", evidence=selected, link="More than 10% at renewal.") == (
        "human_normalization_condition_number_unsupported", "human_normalization_evaluation_link_number_unsupported")


def test_failures_are_reported_per_field():
    codes = validate_final_normalization(normalized_condition="More than 12% at renewal.",
                                         normalized_evaluation_link="Delays of 30 days.",
                                         evidence_texts=(SOURCE,))
    assert codes == ("human_normalization_condition_number_unsupported",
                     "human_normalization_evaluation_link_number_unsupported")


# --- consistency with the Phase 6 tokenizer and decision -----------------------------------------------------


@pytest.mark.parametrize("value", [
    "more than 10%", ">= 10%", "10%", "12%", "$10", "10 dollars", "2 weeks", "2 months", "past 30 days",
    "within 30 days", "a 10-15% increase", "3 million", "double", "ten percent", "about 10%",
])
@pytest.mark.parametrize("source", ["more than 10% at renewal", "$10 per unit", "2 weeks late", "past 30 days",
                                    "a 10-15% increase", "3 million", "costs double"])
def test_a_single_excerpt_verdict_matches_phase6_grounding(source, value):
    """With one selected excerpt, every pass/fail and ending agrees with the unchanged Phase 6 decision.

    The only difference by design: a kind change involving a currency is reported as currency_changed here,
    where Phase 6 reports numeric_kind_changed.
    """
    ours = {code.removeprefix("human_normalization_condition_") for code in check(value, evidence=(source,))}
    phase6 = {code.removeprefix("normalization_")
              for code in grounding.grounding_failures(source, [("normalization", value)])}
    assert {("numeric_kind_changed" if e == "currency_changed" else e) for e in ours} == phase6


def test_tokens_come_only_from_the_phase6_tokenizer():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert defined == {"human_normalization_validation_version", "_magnitude_failure", "_grounding_endings",
                       "validate_final_normalization"}
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "tokenize" in called and not {"compile", "findall", "split"} & called
    assert "import re" not in MODULE.read_text(encoding="utf-8")


# --- purity and boundaries ---------------------------------------------------------------------------------------


def _imports():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    return names


def test_the_module_imports_only_the_grounding_primitives():
    assert _imports() == {"__future__", "baec_app.ai.grounding"}
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    from_grounding = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                      and node.module == "baec_app.ai.grounding" for alias in node.names}
    assert from_grounding == {"CONFLICTING", "EUR", "GBP", "UNRESOLVED_EXPRESSIONS", "UNSPECIFIED_DOLLAR", "USD",
                              "Compound", "Magnitude", "Multiplier", "Range", "tokenize"}
    assert not {name for name in from_grounding if name.startswith("_")}  # public primitives only


def test_the_module_reaches_no_database_ai_service_mcp_authority_ui_or_ambient_state():
    text = MODULE.read_text(encoding="utf-8")
    code = text.split('"""', 2)[2]  # everything after the module docstring
    for forbidden in ("baec_app.data", "proposal_bridge", "sqlite3", "baec_app.ai.service", "baec_app.ai.provider",
                      "baec_app.ai.composition", "baec_app.ai.validation", "baec_app.ai.verification", "anthropic",
                      "baec_app.mcp", "state_machine", "authority", "approval", "streamlit", "os.environ",
                      "getenv", "datetime", "random", "uuid", "time.", "socket", "urllib"):
        assert forbidden not in code, forbidden


def test_the_validator_is_not_exported_or_used_by_production_yet():
    import baec_app.application as application
    assert not {name for name in application.__all__ if "ormaliz" in name}
    root = Path(__file__).resolve().parents[1] / "baec_app"
    users = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and "human_normalization" in node.module:
                users.append(path.name)
            elif isinstance(node, ast.Import) and any("human_normalization" in a.name for a in node.names):
                users.append(path.name)
    assert users == ["proposal_authorization.py",  # Phase 7F-B: the contract version a revision must carry
                     "proposal_review.py"]  # Phase 7E: the review layer, before a revision is saved


def test_validation_is_deterministic_and_has_no_persisted_effects(tmp_path):
    from baec_app.data.database import open_database
    from tests.persistence_builders import dump
    connection = open_database(str(tmp_path / "untouched.sqlite3"))
    try:
        before = dump(connection)
        results = {check("More than 10% at renewal, or delays past 30 days.", evidence=(SOURCE, "Delays past 30 days."))
                   for _ in range(25)}
        assert results == {()} and dump(connection) == before
    finally:
        connection.close()


def test_the_scope_limitation_is_stated_plainly():
    doc = " ".join(module.__doc__.split())
    assert "does not prove general semantic entailment of non-numeric prose" in doc
    assert "does not establish that a normalization is a BAEC" in doc
    for decided_elsewhere in ("BUYER_FACT", "SELLER_OBSERVATION", "criterion finding", "stringency judgment",
                              "buyer role", "account state"):
        assert decided_elsewhere in doc
    assert inspect.signature(validate_final_normalization).parameters.keys() == {
        "normalized_condition", "normalized_evaluation_link", "evidence_texts"}
    # Approved closed-code semantics.
    assert ("compound_unsupported means only that a Range or Compound numeric structure in the final normalization "
            "is not supported by exact structural equality with a corresponding expression in one selected evidence "
            "text") in doc
    assert "not a general semantic mismatch, a BAEC-classification failure, an evidence-provenance failure, or a " \
           "human-review failure" in doc
    assert "evidence_missing means a non-null normalization was supplied with no selected evidence" in doc
    # The source-binding limitation, pinned and demonstrated: the supplied strings are trusted as the selection.
    assert ("trusts the supplied evidence_texts as the selected evidence set. It does not independently prove that "
            "those strings are verbatim excerpts of the stored source interaction") in doc
    assert "belong to the Phase 7E human-review layer" in doc
    assert "A validator pass is therefore not proof of authentic source evidence" in doc
    fabricated = ("A string that appears in no stored interaction: more than 10% at renewal.",)
    assert check("More than 10% at renewal.", evidence=fabricated) == ()  # passes: binding is 7E's job
