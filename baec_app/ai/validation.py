"""Application semantic validation of a parsed extraction (design §8, D8; Phase 6D design §4).

Runs after the strict Pydantic parse. Every rule here is an IMPLEMENTATION CHOICE,
not a manuscript or Research Contract rule, and none of them is BAEC validation:
they check the AI artifact's own structure and its exact fidelity to the source
text. Failure codes are fixed machine tokens that carry no source text or values.
"""

from __future__ import annotations

from collections import Counter

from baec_app.ai.contracts import CRITERIA, BaecExtractionOutput
from baec_app.ai.grounding import GROUNDING_FAILURE_CODES, grounding_failures

MAX_EXCERPTS = 20
MAX_EXCERPT_REFS = 20
MAX_UNCERTAINTIES = 10
MAX_TEXT_LENGTH = 500  # Unicode code points

# The rules in this module, recorded on every run before the provider attempt
# (Phase 6D design §6). /v1 (from Phase 6D-B1) is the D8 rules alone; /v2 (from
# Phase 6D-B2) adds numeric, numeric-kind, unit, compound, and comparator grounding.
# Phase 6C runs used the D8 rules as a legacy, pre-versioned validator, and no
# identifier labels them. An identifier is never reused with another meaning.
VALIDATION_VERSION_V1 = "baec-extraction-validation/v1"
VALIDATION_VERSION = "baec-extraction-validation/v2"

SEMANTIC_FAILURE_CODES = (
    "excerpt_id_blank",
    "source_interaction_mismatch",
    "excerpt_not_verbatim",
    "excerpt_blank",
    "duplicate_excerpt_id",
    "duplicate_excerpt_text",
    "too_many_excerpts",
    "criterion_set_invalid",
    "unknown_excerpt_reference",
    "duplicate_excerpt_reference",
    "too_many_excerpt_references",
    "supported_without_excerpt",
    "possible_language_without_excerpt",
    "normalization_blank",
    "normalization_too_long",
    "explanation_blank",
    "explanation_too_long",
    "too_many_uncertainties",
    "uncertainty_blank",
    "uncertainty_too_long",
)
# The closed semantic vocabulary of each validator version that has ever been recorded.
SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION = {
    VALIDATION_VERSION_V1: tuple(sorted(SEMANTIC_FAILURE_CODES)),
    VALIDATION_VERSION: tuple(sorted(SEMANTIC_FAILURE_CODES + GROUNDING_FAILURE_CODES)),
}


def _blank(text: str) -> bool:
    return not text.strip()


def validate_extraction(
    output: BaecExtractionOutput, *, source_interaction_id: str, source_text: str
) -> tuple[str, ...]:
    """Return the sorted, de-duplicated failure codes; an empty tuple means the extraction is valid."""
    if type(output) is not BaecExtractionOutput:
        raise TypeError("validate_extraction requires a BaecExtractionOutput")
    codes: set[str] = set()

    # Source excerpts: each must be an exact, case-sensitive, non-blank substring of this interaction.
    excerpts = output.source_excerpts
    if len(excerpts) > MAX_EXCERPTS:
        codes.add("too_many_excerpts")
    id_counts = Counter(excerpt.excerpt_id for excerpt in excerpts)
    text_counts = Counter((excerpt.source_interaction_id, excerpt.text) for excerpt in excerpts)
    valid_ids: set[str] = set()
    for excerpt in excerpts:
        problems = set()
        if _blank(excerpt.excerpt_id):
            problems.add("excerpt_id_blank")
        if excerpt.source_interaction_id != source_interaction_id:
            problems.add("source_interaction_mismatch")
        if _blank(excerpt.text):
            problems.add("excerpt_blank")
        elif excerpt.text not in source_text:
            problems.add("excerpt_not_verbatim")
        if id_counts[excerpt.excerpt_id] > 1:
            problems.add("duplicate_excerpt_id")
        if text_counts[(excerpt.source_interaction_id, excerpt.text)] > 1:
            problems.add("duplicate_excerpt_text")
        codes |= problems
        if not problems:
            valid_ids.add(excerpt.excerpt_id)
    known_ids = set(id_counts)

    # Criterion hypotheses: exactly one per criterion; references resolve, are unique, and are bounded.
    hypotheses = output.criterion_hypotheses
    if sorted(h.criterion for h in hypotheses) != sorted(CRITERIA):
        codes.add("criterion_set_invalid")
    for hypothesis in hypotheses:
        refs = hypothesis.excerpt_refs
        if len(refs) > MAX_EXCERPT_REFS:
            codes.add("too_many_excerpt_references")
        if len(set(refs)) != len(refs):
            codes.add("duplicate_excerpt_reference")
        if any(ref not in known_ids for ref in refs):
            codes.add("unknown_excerpt_reference")
        if hypothesis.status == "supported" and not any(ref in valid_ids for ref in refs):
            codes.add("supported_without_excerpt")
        if _blank(hypothesis.explanation):
            codes.add("explanation_blank")
        if len(hypothesis.explanation) > MAX_TEXT_LENGTH:
            codes.add("explanation_too_long")

    # Overall status: a possible-language reading must cite at least one valid excerpt.
    if output.analysis_status == "possible_baec_language" and not valid_ids:
        codes.add("possible_language_without_excerpt")

    # AI-derived normalizations: optional, but never blank when present, and bounded.
    for normalization in (output.normalized_condition, output.normalized_evaluation_link):
        if normalization is not None:
            if _blank(normalization):
                codes.add("normalization_blank")
            if len(normalization) > MAX_TEXT_LENGTH:
                codes.add("normalization_too_long")

    # Uncertainties: bounded, each non-blank and bounded.
    if len(output.uncertainties) > MAX_UNCERTAINTIES:
        codes.add("too_many_uncertainties")
    for uncertainty in output.uncertainties:
        if _blank(uncertainty):
            codes.add("uncertainty_blank")
        if len(uncertainty) > MAX_TEXT_LENGTH:
            codes.add("uncertainty_too_long")

    # Validation v2 grounding: every model-authored free-text field, against this interaction's text only.
    # Excerpt text (checked verbatim above), identifiers, and closed enum values are not grounded.
    fields = [("normalization", text) for text in (output.normalized_condition, output.normalized_evaluation_link)
              if text is not None]
    fields += [("explanation", hypothesis.explanation) for hypothesis in hypotheses]
    fields += [("uncertainty", uncertainty) for uncertainty in output.uncertainties]
    codes |= grounding_failures(source_text, fields)

    return tuple(sorted(codes))
