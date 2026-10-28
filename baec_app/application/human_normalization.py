"""baec-human-normalization-validation/v1: the Phase 7 contract for final human-reviewed normalizations.

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §11 (Phase 7F-A). A deterministic IMPLEMENTATION
safeguard supporting RC-18. It decides only whether a final, human-reviewed normalized_condition and
normalized_evaluation_link may be stored, judged against the human-selected verbatim evidence passed in.

What it is not. It is not AI extraction validation (it never carries the baec-extraction-validation/v2
identity or its codes), not BAEC classification, not evidence-provenance determination, not human
authorization, and not review persistence. It is a numeric and comparator grounding contract: it does not
prove general semantic entailment of non-numeric prose, does not establish that a normalization is a BAEC,
and determines no BUYER_FACT, SELLER_OBSERVATION, criterion finding, stringency judgment, buyer role, or
account state.

Rules, in order, for each non-null value:
* structural: a string; non-blank (str.strip); at most 500 Unicode code points; never truncated or rewritten;
  a non-null value needs at least one selected evidence text to be grounded against;
* numeric grounding: every numeric token of the value, from the unchanged baec_app.ai.grounding.tokenize, must
  be supported by ONE token of ONE selected evidence text. Each evidence text is tokenized on its own and texts
  are never joined, so no evidence token spans two selections. For a magnitude, value, numeric kind (currency
  included), unit, and comparator are checked as successive filters over the same candidate tokens: support
  exists only if one candidate token survives every filter, so a value from one selection is never paired
  with a kind, unit, or comparator from another. Different numeric expressions may be supported by different
  selections. Ranges, compounds, and multiplier words pass only by exact equality with an evidence token.
  Nothing is calculated, derived, converted, or rounded: the only equivalences are those the tokenizer itself
  defines. An expression omitted from the value entirely produces no token and is not checked.

Approved closed-code clarifications (Phase 7F-A):
* compound_unsupported means only that a Range or Compound numeric structure in the final normalization is
  not supported by exact structural equality with a corresponding expression in one selected evidence text.
  It is not a general semantic mismatch, a BAEC-classification failure, an evidence-provenance failure, or a
  human-review failure.
* evidence_missing means a non-null normalization was supplied with no selected evidence. The design requires
  selected evidence for a non-null value; this names that failure.

Source-binding limitation, stated plainly: baec-human-normalization-validation/v1 trusts the supplied
evidence_texts as the selected evidence set. It does not independently prove that those strings are verbatim
excerpts of the stored source interaction. Source-interaction binding and verbatim-selection integrity belong
to the Phase 7E human-review layer. A validator pass is therefore not proof of authentic source evidence.

The comparator decision is this module's own small function because the Phase 6 one is private (design
§11.4); tokens come only from the Phase 6 tokenizer, which is never copied or modified. It reads no database,
environment, clock, randomness, network, or model, and the same inputs always give the same result.
"""

from __future__ import annotations

from baec_app.ai.grounding import (
    CONFLICTING,
    EUR,
    GBP,
    UNRESOLVED_EXPRESSIONS,
    UNSPECIFIED_DOLLAR,
    USD,
    Compound,
    Magnitude,
    Multiplier,
    Range,
    tokenize,
)

HUMAN_NORMALIZATION_VALIDATION_VERSION = "baec-human-normalization-validation/v1"
MAX_NORMALIZATION_LENGTH = 500  # Unicode code points

# The two validated fields, in report order: (field name, code infix).
NORMALIZATION_FIELDS = (("normalized_condition", "condition"), ("normalized_evaluation_link", "evaluation_link"))
FAILURE_ENDINGS = ("blank", "too_long", "evidence_missing", "number_unsupported", "numeric_kind_changed",
                   "currency_changed", "unit_changed", "compound_unsupported", "comparator_changed",
                   "comparator_unresolved")
HUMAN_NORMALIZATION_FAILURE_CODES = tuple(sorted(
    f"human_normalization_{infix}_{ending}" for _, infix in NORMALIZATION_FIELDS for ending in FAILURE_ENDINGS
))

_CURRENCY_KINDS = frozenset({UNSPECIFIED_DOLLAR, USD, EUR, GBP})
_UNRESOLVED_COMPARATORS = frozenset(f"unresolved:{word}" for word in UNRESOLVED_EXPRESSIONS) | {CONFLICTING}


def human_normalization_validation_version() -> str:
    """The identity of this contract, recorded with every review revision it validated."""
    return HUMAN_NORMALIZATION_VALIDATION_VERSION


def _magnitude_failure(value: Magnitude, candidates: list[Magnitude]) -> str | None:
    """Successive filters over the same candidate tokens (design §11.3, coherent same-excerpt support)."""
    same_value = [c for c in candidates if c.value == value.value]
    if not same_value:
        return "number_unsupported"
    same_kind = [c for c in same_value if c.kind == value.kind]
    if not same_kind:
        currency = value.kind in _CURRENCY_KINDS or any(c.kind in _CURRENCY_KINDS for c in same_value)
        return "currency_changed" if currency else "numeric_kind_changed"
    same_unit = [c for c in same_kind if c.unit == value.unit]
    if not same_unit:
        return "unit_changed"
    if value.comparator != CONFLICTING and any(c.comparator == value.comparator for c in same_unit):
        return None
    # An unresolved word (past, beyond, within, over) or a conflict on either side is never given a class:
    # only its exact echo passes. Every other difference is a classified comparator change.
    if value.comparator in _UNRESOLVED_COMPARATORS or any(c.comparator in _UNRESOLVED_COMPARATORS for c in same_unit):
        return "comparator_unresolved"
    return "comparator_changed"


def _grounding_endings(text: str, evidence_tokens: list[tuple]) -> set[str]:
    magnitudes = [t for tokens in evidence_tokens for t in tokens if type(t) is Magnitude]
    ranges = {t for tokens in evidence_tokens for t in tokens if type(t) is Range}
    compounds = {t.surface for tokens in evidence_tokens for t in tokens if type(t) is Compound}
    multipliers = {t.word for tokens in evidence_tokens for t in tokens if type(t) is Multiplier}
    endings = set()
    for token in tokenize(text):
        if type(token) is Magnitude:
            ending = _magnitude_failure(token, magnitudes)
        elif type(token) is Range:
            ending = None if token in ranges else "compound_unsupported"
        elif type(token) is Compound:
            ending = None if token.surface in compounds else "compound_unsupported"
        else:
            ending = None if token.word in multipliers else "number_unsupported"
        if ending is not None:
            endings.add(ending)
    return endings


def validate_final_normalization(*, normalized_condition: str | None, normalized_evaluation_link: str | None,
                                 evidence_texts: tuple[str, ...]) -> tuple[str, ...]:
    """The sorted, unique failure codes of the two final values; an empty tuple means both may be stored.

    Each code names its field (human_normalization_condition_* or human_normalization_evaluation_link_*).
    A null value is valid and checks nothing. evidence_texts are the human-selected verbatim evidence texts
    only, never the whole interaction; each is tokenized separately.
    """
    if type(evidence_texts) is not tuple or any(type(text) is not str for text in evidence_texts):
        raise TypeError("evidence_texts must be a tuple of the selected evidence texts")
    evidence_tokens = [tokenize(text) for text in evidence_texts]  # one token stream per selection, never joined
    codes = set()
    for (field, infix), value in zip(NORMALIZATION_FIELDS, (normalized_condition, normalized_evaluation_link)):
        if value is None:
            continue
        if type(value) is not str:
            raise TypeError(f"{field} must be a string or None")
        endings = set()
        if not value.strip():
            endings.add("blank")
        else:
            if len(value) > MAX_NORMALIZATION_LENGTH:
                endings.add("too_long")
            if not evidence_texts:
                endings.add("evidence_missing")
            else:
                endings |= _grounding_endings(value, evidence_tokens)
        codes |= {f"human_normalization_{infix}_{ending}" for ending in endings}
    return tuple(sorted(codes))
