"""Deterministic numeric and stringency grounding for validation v2 (Phase 6D design §4).

IMPLEMENTATION SAFEGUARD (supports RC-18, itself an IMPLEMENTATION rule). It is not
a research rule and not BAEC validation: it decides only whether an AI artifact
exists. One tokenizer is applied identically to the source interaction text and to
each model-authored field. Only the approved equivalences are canonicalized; nothing
is derived by arithmetic, unit conversion, or currency conversion.

Grounding is per interaction, not per speaker (design §12): a magnitude is grounded
when any occurrence in the source interaction carries the same value, numeric kind,
unit, and comparator. Failure codes are fixed tokens that never carry a value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

# --- closed vocabularies ---------------------------------------------------------------------

PLAIN = "plain"
PERCENT = "percent"
PERCENTAGE_POINT = "percentage_point"
UNSPECIFIED_DOLLAR = "currency:unspecified_dollar"
USD = "currency:USD"
EUR = "currency:EUR"
GBP = "currency:GBP"
NUMERIC_KINDS = (PLAIN, PERCENT, PERCENTAGE_POINT, UNSPECIFIED_DOLLAR, USD, EUR, GBP)

NO_UNIT = "none"
UNITS = ("day", "business_day", "week", "month", "quarter", "year", "hour")

# Comparator classes mirror the domain ThresholdComparator names (design §4.6); the AI
# layer may not import the domain, so a test pins these to the enum.
COMPARATOR_CLASSES = ("GREATER_THAN", "AT_LEAST", "LESS_THAN", "AT_MOST", "EXACTLY", "APPROXIMATELY")
NO_COMPARATOR = "NONE"
CONFLICTING = "CONFLICTING"
UNRESOLVED_EXPRESSIONS = ("past", "beyond", "within", "over")
_UNRESOLVED = "unresolved:"

GROUNDING_SCOPES = ("normalization", "explanation", "uncertainty")
GROUNDING_ENDINGS = ("number_unsupported", "numeric_kind_changed", "unit_changed", "compound_unsupported",
                     "comparator_changed", "comparator_unresolved")
GROUNDING_FAILURE_CODES = tuple(sorted(f"{scope}_{ending}" for scope in GROUNDING_SCOPES for ending in GROUNDING_ENDINGS))

# Exact-echo multiplier words (design §4.3): accepted only when the source has the same word.
MULTIPLIER_WORDS = ("half", "double", "twice", "triple", "dozen")
# A scale word after a magnitude ("12.5 thousand", "3 million") makes a compound: exact echo, never converted.
SCALE_WORDS = ("hundred", "thousand", "million", "billion", "trillion")

_UNITS_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9}
_TEENS_WORDS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
               "ninety": 90}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
           "Jan.", "Feb.", "Mar.", "Apr.", "Jun.", "Jul.", "Aug.", "Sep.", "Sept.", "Oct.", "Nov.", "Dec.")
_UNIT_ADJECTIVES = {"day": "day", "business-day": "business_day", "week": "week", "month": "month",
                    "quarter": "quarter", "year": "year", "hour": "hour"}

# --- chunks ------------------------------------------------------------------------------------

_OPENING = "([{\"'“‘"
_CLOSING = ".,;:!?)]}\"'”’"


@dataclass(frozen=True)
class _Chunk:
    """A whitespace-delimited run with its surrounding punctuation removed."""

    core: str
    raw_start: int
    raw_end: int
    core_start: int
    core_end: int
    lead: bool  # opening punctuation before the core
    trail: bool  # closing punctuation after the core

    @property
    def word(self) -> str:
        return self.core.lower()


def _chunks(text: str) -> list[_Chunk]:
    chunks = []
    for match in re.finditer(r"\S+", text):
        raw = match.group()
        start, end = 0, len(raw)
        while start < end and raw[start] in _OPENING:
            start += 1
        while end > start and raw[end - 1] in _CLOSING:
            if raw[start:end] == "U.S.":  # the abbreviation keeps its final period
                break
            end -= 1
        if start < end:
            chunks.append(_Chunk(raw[start:end], match.start(), match.end(), match.start() + start,
                                 match.start() + end, start > 0, end < len(raw)))
    return chunks


def _adjacent(chunks: list[_Chunk], a: int, b: int) -> bool:
    """Chunk b immediately follows chunk a with only whitespace between them."""
    return 0 <= a and b == a + 1 and b < len(chunks) and not chunks[a].trail and not chunks[b].lead


# --- tokens ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Magnitude:
    value: Decimal
    kind: str
    unit: str
    comparator: str  # a class, NONE, CONFLICTING, or "unresolved:<expression>"


@dataclass(frozen=True)
class Range:
    """The one approved compound canonical form: (low, high, kind, unit), order-preserving."""

    low: Decimal
    high: Decimal
    kind: str
    unit: str
    comparator: str


@dataclass(frozen=True)
class Compound:
    surface: str  # compared by exact echo only


@dataclass(frozen=True)
class Multiplier:
    word: str  # compared by exact echo only (case-insensitive whole word)


Token = Magnitude | Range | Compound | Multiplier

# --- lexicons as chunk matchers -----------------------------------------------------------------

Matcher = Callable[[_Chunk], bool]


def _ci(*words: str) -> Matcher:
    return lambda chunk: chunk.word in words


def _exact(*forms: str) -> Matcher:
    return lambda chunk: chunk.core in forms


_POUND_SENTINEL = "pound"
_KIND_SUFFIXES: tuple[tuple[tuple[Matcher, ...], str], ...] = (
    ((_ci("united"), _ci("states"), _ci("dollar", "dollars")), USD),
    ((_ci("percentage"), _ci("point", "points")), PERCENTAGE_POINT),
    ((_ci("per"), _ci("cent")), PERCENT),
    ((_exact("U.S."), _ci("dollar", "dollars")), USD),
    ((_exact("US"), _ci("dollar", "dollars")), USD),
    ((_ci("british"), _ci("pound", "pounds")), GBP),
    ((_ci("pound", "pounds"), _ci("sterling")), GBP),
    ((_ci("percent"),), PERCENT),
    ((_exact("USD"),), USD),
    ((_exact("EUR"),), EUR),
    ((_exact("GBP"),), GBP),
    ((_ci("euro", "euros"),), EUR),
    ((_ci("dollar", "dollars"),), UNSPECIFIED_DOLLAR),
    ((_ci("pound", "pounds"),), _POUND_SENTINEL),  # unresolved: currency or weight
)
# Currency markers before a magnitude, as their own chunk: they bind across horizontal ASCII whitespace only.
_KIND_PREFIXES = {"USD": USD, "EUR": EUR, "GBP": GBP, "US$": USD, "$": UNSPECIFIED_DOLLAR, "\u20ac": EUR, "\u00a3": GBP}
_HORIZONTAL_GAP = re.compile(r"[ \t]+")


def _unit_word(*forms: str) -> Matcher:
    """A unit word, optionally carrying the postfix "+" ("5 weeks+")."""
    return lambda chunk: chunk.word in forms or (chunk.word.endswith("+") and chunk.word[:-1] in forms)


_UNIT_SUFFIXES: tuple[tuple[tuple[Matcher, ...], str], ...] = (
    ((_ci("business"), _unit_word("day", "days")), "business_day"),
    ((_unit_word("day", "days"),), "day"),
    ((_unit_word("week", "weeks"),), "week"),
    ((_unit_word("month", "months"),), "month"),
    ((_unit_word("quarter", "quarters"),), "quarter"),
    ((_unit_word("year", "years"),), "year"),
    ((_unit_word("hour", "hours"),), "hour"),
)
_CURRENCY_SYMBOLS = {"$": UNSPECIFIED_DOLLAR, "US$": USD, "€": EUR, "£": GBP}

_PREFIX_COMPARATORS: tuple[tuple[tuple[str, ...], str], ...] = tuple(sorted(
    [
        ((">",), "GREATER_THAN"), (("more", "than"), "GREATER_THAN"), (("greater", "than"), "GREATER_THAN"),
        (("above",), "GREATER_THAN"), (("exceeds",), "GREATER_THAN"), (("exceeding",), "GREATER_THAN"),
        (("in", "excess", "of"), "GREATER_THAN"),
        ((">=",), "AT_LEAST"), (("≥",), "AT_LEAST"), (("at", "least"), "AT_LEAST"),
        (("no", "less", "than"), "AT_LEAST"), (("not", "less", "than"), "AT_LEAST"), (("minimum", "of"), "AT_LEAST"),
        (("<",), "LESS_THAN"), (("less", "than"), "LESS_THAN"), (("fewer", "than"), "LESS_THAN"),
        (("below",), "LESS_THAN"), (("under",), "LESS_THAN"),
        (("<=",), "AT_MOST"), (("≤",), "AT_MOST"), (("at", "most"), "AT_MOST"), (("no", "more", "than"), "AT_MOST"),
        (("not", "more", "than"), "AT_MOST"), (("maximum", "of"), "AT_MOST"), (("up", "to"), "AT_MOST"),
        (("=",), "EXACTLY"), (("exactly",), "EXACTLY"),
        (("about",), "APPROXIMATELY"), (("around",), "APPROXIMATELY"), (("roughly",), "APPROXIMATELY"),
        (("approximately",), "APPROXIMATELY"), (("~",), "APPROXIMATELY"),
    ] + [((word,), _UNRESOLVED + word) for word in UNRESOLVED_EXPRESSIONS],
    key=lambda entry: -len(entry[0]),  # the longest deterministic match wins
))
_POSTFIX_COMPARATORS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("or", "more"), "AT_LEAST"), (("or", "greater"), "AT_LEAST"), (("or", "higher"), "AT_LEAST"),
    (("or", "above"), "AT_LEAST"), (("or", "longer"), "AT_LEAST"), (("and", "up"), "AT_LEAST"),
    (("or", "less"), "AT_MOST"), (("or", "fewer"), "AT_MOST"), (("or", "lower"), "AT_MOST"),
    (("or", "below"), "AT_MOST"), (("or", "shorter"), "AT_MOST"),
)
_ATTACHED_COMPARATORS = {">=": "AT_LEAST", "<=": "AT_MOST", "≥": "AT_LEAST", "≤": "AT_MOST",
                         ">": "GREATER_THAN", "<": "LESS_THAN", "=": "EXACTLY", "~": "APPROXIMATELY"}

# --- digit forms --------------------------------------------------------------------------------

_NUM = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
# A range end is a simple magnitude without a structural leading zero (fail-closed choice: "05-10" is a compound).
_RANGE_END = r"(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*|0)(?:\.[0-9]+)?"
_CURRENCY = r"US\$|\$|€|£"
_COMPARATOR_SYMBOL = r">=|<=|≥|≤|>|<|=|~"
_UNIT_ADJECTIVE = r"business-day|day|week|month|quarter|year|hour"
_SIMPLE = re.compile(
    rf"(?P<cmp>{_COMPARATOR_SYMBOL})?(?P<sign>[+\-−])?(?P<cur>{_CURRENCY})?(?P<num>{_NUM})(?P<pct>%)?"
    rf"(?:-(?P<unit>{_UNIT_ADJECTIVE})|(?P<plus>\+))?"
)
_RANGE = re.compile(
    rf"(?P<cmp>{_COMPARATOR_SYMBOL})?(?P<cur>{_CURRENCY})?(?P<low>{_RANGE_END})[-–](?P<high>{_RANGE_END})"
    rf"(?P<pct>%)?(?:-(?P<unit>{_UNIT_ADJECTIVE}))?"
)


@dataclass
class _Head:
    start: int  # first chunk index
    end: int  # one past the last chunk index
    low: Decimal
    high: Decimal | None = None  # set for a compact range
    kind: str = PLAIN
    unit: str = NO_UNIT
    comparators: tuple[str, ...] = ()
    conflict: bool = False
    bare_integer: bool = False  # a plain digit run with nothing attached, for month-name dates


def _has_digit(text: str) -> bool:
    return any(character.isdigit() for character in text)


def _digit_head(chunk: _Chunk, index: int) -> _Head | Compound:
    core = chunk.core
    match = _RANGE.fullmatch(core)
    if match:
        head = _Head(index, index + 1, Decimal(match["low"].replace(",", "")), Decimal(match["high"].replace(",", "")))
    else:
        match = _SIMPLE.fullmatch(core)
        if match is None:
            return Compound(core)  # dates, times, ratios, ordinals, codes, scale suffixes, non-ASCII digits
        value = Decimal(match["num"].replace(",", ""))
        if match["sign"] in ("-", "−"):
            value = -value
        head = _Head(index, index + 1, value)
        head.bare_integer = core.isascii() and core.isdigit()
        if match["plus"]:
            head.comparators += ("AT_LEAST",)
    if match["cmp"]:
        head.comparators += (_ATTACHED_COMPARATORS[match["cmp"]],)
    if match["cur"] and match["pct"]:
        head.conflict = True
    elif match["cur"]:
        head.kind = _CURRENCY_SYMBOLS[match["cur"]]
    elif match["pct"]:
        head.kind = PERCENT
    if match["unit"]:
        head.unit = _UNIT_ADJECTIVES[match["unit"].lower()]
    return head


# --- number words -------------------------------------------------------------------------------


def _small(word: str) -> int | None:
    for table in (_UNITS_WORDS, _TEENS_WORDS, _TENS_WORDS):
        if word in table:
            return table[word]
    tens, _, units = word.partition("-")
    if tens in _TENS_WORDS and units in _UNITS_WORDS:
        return _TENS_WORDS[tens] + _UNITS_WORDS[units]
    return None


def _hundreds(chunks: list[_Chunk], index: int) -> tuple[int, int] | None:
    """hundreds := small | units " hundred" [ " and"? " " small ]. Returns (value, next index)."""
    if index >= len(chunks):
        return None
    word = chunks[index].word
    value = _small(word)
    if value is None:
        return None
    if word in _UNITS_WORDS and _adjacent(chunks, index, index + 1) and chunks[index + 1].word == "hundred":
        value, last = value * 100, index + 1
        rest = last + 1
        if _adjacent(chunks, last, rest) and chunks[rest].word == "and" and _adjacent(chunks, rest, rest + 1):
            rest += 1
        if _adjacent(chunks, rest - 1, rest) and (tail := _small(chunks[rest].word)) is not None:
            return value + tail, rest + 1
        return value, last + 1
    return value, index + 1


def _number_words(chunks: list[_Chunk], index: int) -> tuple[int, int] | None:
    """number := hundreds | hundreds " thousand" [ " and"? " " hundreds ] (closed grammar, design §4.3)."""
    first = _hundreds(chunks, index)
    if first is None:
        return None
    value, following = first
    last = following - 1
    if _adjacent(chunks, last, following) and chunks[following].word == "thousand":
        value, last = value * 1000, following
        rest = last + 1
        if _adjacent(chunks, last, rest) and chunks[rest].word == "and" and _adjacent(chunks, rest, rest + 1):
            rest += 1
        if _adjacent(chunks, rest - 1, rest) and (tail := _hundreds(chunks, rest)) is not None:
            return value + tail[0], tail[1]
        return value, last + 1
    return value, following


def _word_head(chunks: list[_Chunk], index: int) -> _Head | Multiplier | None:
    word = chunks[index].word
    if word in MULTIPLIER_WORDS:
        return Multiplier(word)
    number, _, unit = word.rpartition("-")  # hyphenated unit adjective: "ten-week", "twenty-one-day"
    if number and unit in _UNIT_ADJECTIVES and (value := _small(number)) is not None:
        return _Head(index, index + 1, Decimal(value), unit=_UNIT_ADJECTIVES[unit])
    parsed = _number_words(chunks, index)
    if parsed is None:
        return None
    return _Head(index, parsed[1], Decimal(parsed[0]))


# --- attachments --------------------------------------------------------------------------------


def _match_after(chunks: list[_Chunk], last: int, matchers: tuple[Matcher, ...]) -> int | None:
    """The index of the final chunk when matchers match the chunks right after last, adjacently."""
    position = last
    for matcher in matchers:
        if not _adjacent(chunks, position, position + 1) or not matcher(chunks[position + 1]):
            return None
        position += 1
    return position


def _match_before(chunks: list[_Chunk], first: int, words: tuple[str, ...]) -> bool:
    """words occupy the chunks ending right before first, adjacently."""
    start = first - len(words)
    if start < 0:
        return False
    return all(chunks[start + offset].word == word for offset, word in enumerate(words)) and all(
        _adjacent(chunks, position, position + 1) for position in range(start, first)
    )


def _finish(text: str, chunks: list[_Chunk], head: _Head) -> tuple[Token, int]:
    last = head.end - 1
    surface_start = chunks[head.start].core_start
    if head.bare_integer:  # month name plus day or year: a date compound, exact echo only
        if _adjacent(chunks, last, last + 1) and chunks[last + 1].core in _MONTHS:
            return Compound(text[surface_start:chunks[last + 1].core_end]), last + 2
        if _adjacent(chunks, head.start - 1, head.start) and chunks[head.start - 1].core in _MONTHS:
            return Compound(text[chunks[head.start - 1].core_start:chunks[last].core_end]), last + 1
    if _adjacent(chunks, last, last + 1) and chunks[last + 1].word in SCALE_WORDS:
        return Compound(text[surface_start:chunks[last + 1].core_end]), last + 2
    kind, unit, comparators, conflict, pound = head.kind, head.unit, list(head.comparators), head.conflict, False
    # numeric kind after the magnitude: "%" (one space or no-break space), percent words, currency words
    gap_ok = last + 1 < len(chunks) and text[chunks[last].raw_end:chunks[last + 1].raw_start] in (" ", " ")
    if gap_ok and _adjacent(chunks, last, last + 1) and chunks[last + 1].core == "%":
        conflict, kind, last = conflict or kind != PLAIN, PERCENT, last + 1
    else:
        for matchers, suffix_kind in _KIND_SUFFIXES:
            end = _match_after(chunks, last, matchers)
            if end is not None:
                if suffix_kind == _POUND_SENTINEL:
                    pound = True
                else:
                    conflict, kind = conflict or kind != PLAIN, suffix_kind
                last = end
                break
    # a measurement or time unit from the closed lexicon (singular and plural folded), possibly with "+"
    for matchers, unit_name in _UNIT_SUFFIXES:
        end = _match_after(chunks, last, matchers)
        if end is not None:
            conflict, unit, last = conflict or unit != NO_UNIT, unit_name, end
            if chunks[end].word.endswith("+"):
                comparators.append("AT_LEAST")  # postfix "+" after the bound unit: "5 weeks+"
            break
    # a postfix comparator after the magnitude and its unit
    for words, comparator in _POSTFIX_COMPARATORS:
        end = _match_after(chunks, last, tuple(_ci(word) for word in words))
        if end is not None:
            comparators.append(comparator)
            last = end
            break
    # a currency marker before the magnitude ("USD 10", "$ 10"; never across a line break), then a prefix
    # comparator before both
    first = head.start
    if _adjacent(chunks, first - 1, first) and chunks[first - 1].core in _KIND_PREFIXES \
            and _HORIZONTAL_GAP.fullmatch(text[chunks[first - 1].raw_end:chunks[first].raw_start]):
        conflict, kind, first = conflict or kind != PLAIN, _KIND_PREFIXES[chunks[first - 1].core], first - 1
    for words, comparator in _PREFIX_COMPARATORS:
        if _match_before(chunks, first, words):
            comparators.append(comparator)
            break
    surface = text[surface_start:chunks[last].core_end]
    if conflict or pound:
        return Compound(surface), last + 1
    comparator = NO_COMPARATOR if not comparators else comparators[0] if len(comparators) == 1 else CONFLICTING
    if head.high is not None:
        return Range(head.low, head.high, kind, unit, comparator), last + 1
    return Magnitude(head.low, kind, unit, comparator), last + 1


def tokenize(text: str) -> tuple[Token, ...]:
    """Every numeric token of text, in order. Deterministic; applied identically to source and model text."""
    chunks = _chunks(text)
    tokens: list[Token] = []
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        head: _Head | Compound | Multiplier | None
        if _has_digit(chunk.core):
            head = _digit_head(chunk, index)
        else:
            head = _word_head(chunks, index)
        if head is None:
            index += 1
        elif isinstance(head, (Compound, Multiplier)):
            tokens.append(head)
            index += 1
        else:
            token, index = _finish(text, chunks, head)
            tokens.append(token)
    return tuple(tokens)


# --- the grounding decision (design §4.7) -------------------------------------------------------


def _magnitude_failure(model: Magnitude, source: list[Magnitude]) -> str | None:
    same_value = [s for s in source if s.value == model.value]
    if not same_value:
        return "number_unsupported"
    same_kind = [s for s in same_value if s.kind == model.kind]
    if not same_kind:
        return "numeric_kind_changed"
    same_unit = [s for s in same_kind if s.unit == model.unit]
    if not same_unit:
        return "unit_changed"
    if model.comparator != CONFLICTING and any(s.comparator == model.comparator for s in same_unit):
        return None
    # An unresolved or conflicting expression on either side is never given a class: preserving it exactly is
    # the only way through, so rewriting or dropping it is unresolved. Only classified changes are "changed".
    if _unresolved(model.comparator) or any(_unresolved(s.comparator) for s in same_unit):
        return "comparator_unresolved"
    return "comparator_changed"


def _unresolved(comparator: str) -> bool:
    return comparator == CONFLICTING or comparator.startswith(_UNRESOLVED)


def grounding_failures(source_text: str, fields: Iterable[tuple[str, str]]) -> set[str]:
    """Grounding codes for the model-authored fields, each given as (scope, text), against one source text."""
    source = tokenize(source_text)
    magnitudes = [t for t in source if type(t) is Magnitude]
    ranges = {t for t in source if type(t) is Range}
    compounds = {t.surface for t in source if type(t) is Compound}
    multipliers = {t.word for t in source if type(t) is Multiplier}
    codes: set[str] = set()
    for scope, text in fields:
        if scope not in GROUNDING_SCOPES:
            raise ValueError("unknown grounding scope")
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
                codes.add(f"{scope}_{ending}")
    return codes
