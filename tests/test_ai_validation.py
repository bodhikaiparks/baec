"""Phase 6C-B: application semantic validation (design §8, D8). Every rule is an implementation choice."""

import copy

import pytest

from baec_app.ai.contracts import BaecExtractionOutput
from baec_app.ai.validation import SEMANTIC_FAILURE_CODES, validate_extraction
from tests.ai_builders import THRESHOLD_TEXT, hypotheses, output

E1 = "If our supplier raises pricing by more than 10% at renewal, we would reopen the evaluation."
E2 = "We would not switch for anything under 10%."


def codes(value):
    return validate_extraction(BaecExtractionOutput.model_validate(value), source_interaction_id="INT-T",
                               source_text=THRESHOLD_TEXT)


def changed(**changes):
    return output(**changes)


def excerpt(excerpt_id="e1", text=E1, interaction="INT-T", speaker="buyer"):
    return {"excerpt_id": excerpt_id, "source_interaction_id": interaction, "text": text, "attributed_speaker": speaker}


def with_hypothesis(index, **fields):
    value = output()
    value["criterion_hypotheses"][index].update(fields)
    return value


CASES = {
    "excerpt_id_blank": changed(source_excerpts=[excerpt(" ")], criterion_hypotheses=hypotheses(status="unclear", refs=())),
    "source_interaction_mismatch": changed(source_excerpts=[excerpt(interaction="INT-N"), excerpt("e2", E2)]),
    "excerpt_not_verbatim": changed(source_excerpts=[excerpt(text="raises pricing by more than 11%"), excerpt("e2", E2)]),
    "excerpt_not_verbatim (case)": changed(source_excerpts=[excerpt(text=E1.upper()), excerpt("e2", E2)]),
    "excerpt_not_verbatim (whitespace)": changed(source_excerpts=[excerpt(text=E1 + " "), excerpt("e2", E2)]),
    "excerpt_blank": changed(source_excerpts=[excerpt(text="  "), excerpt("e2", E2)]),
    "duplicate_excerpt_id": changed(source_excerpts=[excerpt(), excerpt("e1", E2)]),
    "duplicate_excerpt_text": changed(source_excerpts=[excerpt(), excerpt("e2", E1)]),
    "too_many_excerpts": changed(source_excerpts=[excerpt(f"e{i}", text) for i, text in enumerate(
        [w for w in dict.fromkeys(THRESHOLD_TEXT.split()) if w][:21], 1)]),
    "criterion_set_invalid (missing)": changed(criterion_hypotheses=hypotheses()[:3]),
    "criterion_set_invalid (duplicate)": changed(criterion_hypotheses=hypotheses()[:3] + hypotheses()[:1]),
    "unknown_excerpt_reference": with_hypothesis(0, excerpt_refs=["e1", "e9"]),
    "duplicate_excerpt_reference": with_hypothesis(0, excerpt_refs=["e1", "e1"]),
    "too_many_excerpt_references": with_hypothesis(0, status="unclear", excerpt_refs=["e1", "e2"] * 11),
    "supported_without_excerpt": with_hypothesis(0, excerpt_refs=[]),
    "possible_language_without_excerpt": changed(source_excerpts=[], criterion_hypotheses=hypotheses(status="unclear", refs=())),
    "normalization_blank": changed(normalized_condition=" \t"),
    "normalization_too_long": changed(normalized_evaluation_link="x" * 501),
    "explanation_blank": with_hypothesis(1, explanation="   "),
    "explanation_too_long": with_hypothesis(1, explanation="y" * 501),
    # Digit-free placeholders: "u1"-style text is a compound that validation v2 would also reject.
    "too_many_uncertainties": changed(uncertainties=[f"Open point {letter}." for letter in "abcdefghijk"]),
    "uncertainty_blank": changed(uncertainties=["ok", "\n"]),
    "uncertainty_too_long": changed(uncertainties=["z" * 501]),
}


@pytest.mark.parametrize("label", CASES)
def test_each_rule_reports_its_stable_code(label):
    code = label.split(" ")[0]
    found = codes(CASES[label])
    assert code in found, found
    assert set(found) <= set(SEMANTIC_FAILURE_CODES)


def test_every_semantic_code_is_exercised():
    assert {label.split(" ")[0] for label in CASES} == set(SEMANTIC_FAILURE_CODES)
    assert len(SEMANTIC_FAILURE_CODES) == 20


def test_a_valid_extraction_has_no_codes():
    assert codes(output()) == ()


def test_no_clear_and_insufficient_context_may_have_zero_excerpts():
    for status in ("no_clear_baec_language", "insufficient_context"):
        assert codes(changed(analysis_status=status, source_excerpts=[],
                             criterion_hypotheses=hypotheses(status="unclear", refs=()))) == ()


def test_null_normalizations_and_exact_limits_are_valid():
    assert codes(changed(normalized_condition=None, normalized_evaluation_link=None)) == ()
    assert codes(changed(normalized_condition="é" * 500, uncertainties=["u"] * 10)) == ()  # code points, not bytes
    assert codes(with_hypothesis(0, excerpt_refs=["e1", "e2"])) == ()


def test_supported_needs_a_valid_excerpt_not_just_a_known_id():
    value = changed(source_excerpts=[excerpt(text="not in the source"), excerpt("e2", E2)])
    value["criterion_hypotheses"][0]["excerpt_refs"] = ["e1"]  # resolves, but e1 is invalid
    found = codes(value)
    assert "supported_without_excerpt" in found and "unknown_excerpt_reference" not in found


def test_codes_are_sorted_unique_and_carry_no_source_values():
    value = changed(normalized_condition=" ", uncertainties=[" ", " ", "z" * 501],
                    source_excerpts=[excerpt(text="SECRET-NOT-IN-SOURCE"), excerpt("e2", E2)])
    found = codes(value)
    assert list(found) == sorted(set(found))
    assert all("SECRET" not in code and " " not in code for code in found)


def test_validation_is_deterministic_and_does_not_mutate_its_input():
    value = CASES["duplicate_excerpt_text"]
    parsed = BaecExtractionOutput.model_validate(value)
    snapshot = copy.deepcopy(parsed.model_dump())
    first = validate_extraction(parsed, source_interaction_id="INT-T", source_text=THRESHOLD_TEXT)
    assert first == validate_extraction(parsed, source_interaction_id="INT-T", source_text=THRESHOLD_TEXT)
    assert parsed.model_dump() == snapshot


def test_validation_requires_the_parsed_contract():
    with pytest.raises(TypeError):
        validate_extraction(output(), source_interaction_id="INT-T", source_text=THRESHOLD_TEXT)
