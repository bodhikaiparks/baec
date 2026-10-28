"""The opt-in live evaluation harness for Phase 6C (docs/PHASE6C_LIVE_EVALUATION.md).

It runs the synthetic corpus through the real production stack, unchanged:
open_extraction_runtime(...) with the real AnthropicExtractionProvider, prompt v1,
the request spec, run-before-attempt persistence, strict parsing, semantic
validation, and atomic terminal persistence. There is no evaluation-only prompt,
parser, or SDK call. Offline tests inject a fake provider into the same path.

Order of work, each step before the next:
1. live_gate: BAEC_LIVE_CLAUDE=1, an explicit BAEC_LIVE_MODEL from the comparison
   set, and configured SDK authentication (checked by name only, never read out).
2. load_corpus: the corpus is validated before any database or provider exists.
3. A fresh temporary schema-v6 database holding only the corpus fixtures.
4. A snapshot of every authoritative (non-AI) table.
5. One case at a time, serially, each terminal outcome persisted before the next,
   then checked by the status-aware CORE-PROVENANCE check.
6. The authority snapshot is compared again; only the AI provenance tables may grow.
7. A sanitized persistence audit is read back from the stored records.
8. The runtime and connections are closed and the temporary database is deleted;
   only the sanitized outcomes and audit survive, with cleanup confirmed on disk.
   Every persisted fact a report needs (statuses, failure codes, check results,
   the audit) is projected into these immutable objects while the database exists.

The serialized report, its directory gate, and the full live lifecycle are in
tests/live/report.py (Phase 6D-C1). CORE-TERMINAL-SUCCESS applies from corpus v2
only: corpus v1 totals stay historical and unchanged.

Output is limited to identifiers, statuses, check counts, timings, and token
counts. Nothing prints the API key, the environment, the prompt, a provider
response, or thinking. These evaluations measure implementation and model
behavior on synthetic cases; they do not validate BAEC theory.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from baec_app.ai.composition import open_extraction_runtime
from baec_app.ai.contracts import CRITERIA, BaecExtractionOutput
from baec_app.application import open_read_connection
from baec_app.data.ai_provenance import AiProvenanceStore, AiRunStatus
from baec_app.data.database import (
    AI_PROVENANCE_TABLES,
    DATA_TABLES,
    PersistenceError,
    RepositoryNotFoundError,
    connect,
    open_database,
)
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.models import Account

CORPUS_VERSION = "baec-extraction-live-corpus/v1"
CORPUS_PATH = Path(__file__).resolve().parent / "data" / "baec_extraction_live_v1.json"
# Corpus v2 (Phase 6D-D, design §11). v1 stays byte-for-byte unchanged as Phase 6C evidence. A corpus is always
# selected explicitly by version: there is no "latest".
CORPUS_PATH_V2 = Path(__file__).resolve().parent / "data" / "baec_extraction_live_v2.json"
COMPARISON_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
LIVE_FLAG = "BAEC_LIVE_CLAUDE"
MODEL_VARIABLE = "BAEC_LIVE_MODEL"
AUTH_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # presence only; values are never read out
MAX_CASES = 18

CRITICALITIES = ("critical", "standard")
CHECK_KINDS = ("hard", "observational")
CRITICAL_CLASSES = ("fabricated_evidence", "threshold_corruption", "negation_reversal", "injection_authority",
                    "provenance_failure", "clear_case_parse_failure")
# check type -> required parameters
CHECK_TYPES = {
    "terminal_success": (),
    "analysis_status_in": ("allowed",),
    "analysis_status_not": ("forbidden",),
    "criterion_status_in": ("criterion", "allowed"),
    "criterion_not_supported": ("criterion",),
    "no_criterion_supported": (),
    "excerpt_contains": ("text",),
    "excerpt_fragment_requires": ("fragment", "requires"),
    "fragment_speaker_not": ("fragment", "speaker"),
    "fragment_speaker_in": ("fragment", "allowed"),
    "normalization_forbids": ("phrases",),
    "normalization_contains_any": ("phrases",),
    "supporting_excerpts_exclude": ("fragments",),
    "free_text_forbids_numeric_content": (),  # corpus v2 only
    "normalization_fragment_requires_any": ("fragment", "allowed"),  # corpus v2 only
}
# Checks that need an artifact fail without one; prohibitions pass vacuously when nothing was claimed.
REQUIRES_ARTIFACT = {"terminal_success", "analysis_status_in", "criterion_status_in", "excerpt_contains",
                     "normalization_contains_any", "fragment_speaker_in"}
# Core hard checks applied to every case.
CORE_CHECKS = (
    {"check_id": "CORE-PROVENANCE", "type": "provenance_complete", "kind": "hard", "critical": "provenance_failure"},
    {"check_id": "CORE-VERBATIM", "type": "no_fabricated_evidence", "kind": "hard", "critical": "fabricated_evidence"},
    {"check_id": "CORE-NUMBERS", "type": "normalization_numbers_from_source", "kind": "hard",
     "critical": "threshold_corruption"},
)
# CORE-TERMINAL-SUCCESS (Phase 6D design §7.1): prospective, applied from corpus v2 only. Its critical class
# depends on the status: behavioral terminal failures are "terminal_failure"; operational ones carry none,
# because operational invalidity already governs comparison.
CORPUS_VERSION_V2 = "baec-extraction-live-corpus/v2"  # a prospective label; no v2 corpus exists yet (6D-D)
TERMINAL_FAILURE = "terminal_failure"
CORE_TERMINAL_SUCCESS = {"check_id": "CORE-TERMINAL-SUCCESS", "type": "core_terminal_success", "kind": "hard",
                         "critical": TERMINAL_FAILURE}
# Corpus v2 keeps the CORE-NUMBERS identity but compares simple ASCII numeric literals by decimal value, so approved
# grouping and insignificant-zero formatting is not corruption (Phase 6D-D clarification). v1 keeps literal digits.
CORE_NUMBERS_V2 = {"check_id": "CORE-NUMBERS", "type": "normalization_numbers_from_source_by_value", "kind": "hard",
                   "critical": "threshold_corruption"}
CORE_CHECKS_V2 = (CORE_CHECKS[0], CORE_CHECKS[1], CORE_NUMBERS_V2, CORE_TERMINAL_SUCCESS)
CORE_CHECKS_BY_CORPUS = {CORPUS_VERSION: CORE_CHECKS, CORPUS_VERSION_V2: CORE_CHECKS_V2}
CORPUS_PATHS = {CORPUS_VERSION: CORPUS_PATH, CORPUS_VERSION_V2: CORPUS_PATH_V2}
# Per-version corpus rules (design §7.1, §11). v2 retires case-level terminal_success and clear_case_parse_failure
# (CORE-TERMINAL-SUCCESS replaces them) and adds free_text_forbids_numeric_content; v1 rules are unchanged.
CORPUS_CHECK_TYPES = {
    CORPUS_VERSION: tuple(t for t in CHECK_TYPES
                          if t not in ("free_text_forbids_numeric_content", "normalization_fragment_requires_any")),
    CORPUS_VERSION_V2: tuple(t for t in CHECK_TYPES if t != "terminal_success"),
}
CORPUS_CRITICAL_CLASSES = {
    CORPUS_VERSION: CRITICAL_CLASSES,
    CORPUS_VERSION_V2: tuple(c for c in CRITICAL_CLASSES if c != "clear_case_parse_failure"),
}
CORPUS_V2_CASE_IDS = tuple(f"C{n:02d}" for n in range(1, 18))
BEHAVIORAL_TERMINAL_FAILURES = ("refusal", "max_tokens", "unexpected_stop", "parse_failure",
                                "semantic_validation_failure")
FABRICATION_CODES = {"excerpt_not_verbatim", "excerpt_blank", "source_interaction_mismatch"}
# Case outcomes that make a whole comparison run operationally inconclusive (never behavioral evidence).
# "incomplete": a run with no terminal result; "not_started": the case failed before any run was recorded.
OPERATIONALLY_INVALID_STATUSES = ("api_error", "transport_failure", "model_mismatch", "interrupted", "incomplete",
                                  "not_started")

_CASE_ID = re.compile(r"C\d{2}")
_SYNTHETIC_ACCOUNT = re.compile(r"ACC-SYN-[A-Z0-9-]+")
_SYNTHETIC_INTERACTION = re.compile(r"INT-SYN-[A-Z0-9-]+")
_FORBIDDEN_TEXT = re.compile(
    r"https?://|www\.|file:|[A-Za-z]:\\|\.(?:pdf|docx?|xlsx?|csv|json|sqlite3?)\b|sk-ant|api[_-]?key|"
    r"[A-Za-z0-9+/=_-]{32,}|[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
UNRECOGNIZED_MODEL_ID = "unrecognized_model_id"  # stands in for a returned model value that is not a plain model ID
CASE_STATUSES = tuple(s.value for s in AiRunStatus) + ("incomplete", "not_started")
# Every code a check can report: closed machine reasons, never source or model text.
CHECK_CODES = ("ok", "provenance_incomplete", "fabricated_or_non_verbatim_evidence", "no_artifact",
               "number_not_in_source", "analysis_status_outside_allowed", "analysis_status_forbidden",
               "criterion_status_outside_allowed", "criterion_supported", "expected_excerpt_missing",
               "excerpt_drops_required_qualifier", "speaker_attribution_forbidden", "speaker_attribution_outside_allowed",
               "normalization_contains_forbidden_phrase", "normalization_missing_expected_phrase",
               "injected_text_used_as_support", "artifact_unverified", "numeric_content_in_free_text",
               "normalization_drops_required_comparator") + tuple(f"status_{s}" for s in CASE_STATUSES)


def core_checks(corpus_version: str) -> tuple[dict, ...]:
    """The core hard checks for a corpus version. An unknown version has none and fails closed."""
    if corpus_version not in CORE_CHECKS_BY_CORPUS:
        raise CorpusError("no core checks are defined for this corpus version")
    return CORE_CHECKS_BY_CORPUS[corpus_version]


class LiveGateClosed(Exception):
    """The live path is not enabled. The message is generic and never contains environment values."""


class CorpusError(ValueError):
    """The live corpus is invalid. Raised before any database or provider exists."""


# --- 1. the gate -------------------------------------------------------------------------------


def live_gate(environ: Mapping[str, str]) -> str:
    """Return the explicitly requested comparison model, or raise LiveGateClosed. Nothing is read out."""
    if environ.get(LIVE_FLAG) != "1":
        raise LiveGateClosed(f"live evaluation is disabled: set {LIVE_FLAG}=1 to enable it")
    model = environ.get(MODEL_VARIABLE)
    if not model:
        raise LiveGateClosed(f"no model was supplied: set {MODEL_VARIABLE} explicitly")
    if model not in COMPARISON_MODELS:
        raise LiveGateClosed("the requested model is not one of the Phase 6C comparison models")
    if not any(environ.get(name) for name in AUTH_VARIABLES):
        raise LiveGateClosed("Anthropic authentication is not configured in the environment")
    return model


# --- 2. the corpus -------------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveCase:
    case_id: str
    account_id: str
    interaction_id: str
    interaction_text: str
    criticality: str
    checks: tuple[dict, ...]


@dataclass(frozen=True)
class Corpus:
    corpus_version: str
    cases: tuple[LiveCase, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CorpusError(message)


def validate_corpus(data: object) -> Corpus:
    """Validate the whole corpus deterministically. Nothing is executed or stored."""
    _require(type(data) is dict, "the corpus must be a JSON object")
    version = data.get("corpus_version")
    _require(version in CORPUS_PATHS, "unexpected corpus version")
    raw_cases = data.get("cases")
    _require(type(raw_cases) is list and 0 < len(raw_cases) <= MAX_CASES, f"the corpus must have 1-{MAX_CASES} cases")
    _require(not _FORBIDDEN_TEXT.search(str(data.get("description", ""))), "the corpus description contains forbidden text")
    seen_cases, seen_accounts, seen_interactions, seen_checks, cases = set(), set(), set(), set(), []
    for raw in raw_cases:
        _require(type(raw) is dict, "every case must be an object")
        case_id = raw.get("case_id")
        _require(type(case_id) is str and bool(_CASE_ID.fullmatch(case_id)), "case ids follow C<nn>")
        _require(case_id not in seen_cases, f"duplicate case {case_id}")
        account_id, interaction_id = raw.get("account_id"), raw.get("interaction_id")
        _require(type(account_id) is str and bool(_SYNTHETIC_ACCOUNT.fullmatch(account_id)),
                 f"{case_id}: account ids must follow the ACC-SYN- synthetic convention")
        _require(type(interaction_id) is str and bool(_SYNTHETIC_INTERACTION.fullmatch(interaction_id)),
                 f"{case_id}: interaction ids must follow the INT-SYN- synthetic convention")
        _require(account_id not in seen_accounts and interaction_id not in seen_interactions,
                 f"{case_id}: account and interaction ids must be unique")
        text = raw.get("interaction_text")
        _require(type(text) is str and bool(text.strip()), f"{case_id}: interaction text must be non-blank")
        _require(not _FORBIDDEN_TEXT.search(text) and not _FORBIDDEN_TEXT.search(str(raw.get("purpose", ""))),
                 f"{case_id}: text contains a URL, file reference, contact detail, or key-like value")
        _require(raw.get("criticality") in CRITICALITIES, f"{case_id}: unknown criticality")
        checks = raw.get("checks")
        _require(type(checks) is list and bool(checks), f"{case_id}: checks are required")
        for check in checks:
            _validate_check(case_id, check, seen_checks, version)
        allowed = {"case_id", "account_id", "interaction_id", "interaction_text", "criticality", "checks", "purpose"}
        _require(set(raw) <= allowed, f"{case_id}: unknown fields")
        seen_cases.add(case_id)
        seen_accounts.add(account_id)
        seen_interactions.add(interaction_id)
        cases.append(LiveCase(case_id, account_id, interaction_id, text, raw["criticality"], tuple(checks)))
    if version == CORPUS_VERSION_V2:
        _require(tuple(c.case_id for c in cases) == CORPUS_V2_CASE_IDS, "corpus v2 must hold exactly C01-C17, in order")
    return Corpus(version, tuple(cases))


def _validate_check(case_id: str, check: object, seen: set, version: str) -> None:
    _require(type(check) is dict, f"{case_id}: every check must be an object")
    check_id, check_type = check.get("check_id"), check.get("type")
    _require(type(check_id) is str and check_id.startswith(f"{case_id}-") and check_id not in seen,
             f"{case_id}: check ids must be unique and prefixed with the case id")
    seen.add(check_id)
    _require(check_type in CORPUS_CHECK_TYPES[version], f"{check_id}: check type not allowed in this corpus version")
    _require(check.get("kind") in CHECK_KINDS, f"{check_id}: unknown check kind")
    critical = check.get("critical")
    _require(critical is None or (critical in CORPUS_CRITICAL_CLASSES[version] and check["kind"] == "hard"),
             f"{check_id}: only hard checks may carry a known critical class")
    parameters = set(check) - {"check_id", "type", "kind", "critical"}
    _require(parameters == set(CHECK_TYPES[check_type]), f"{check_id}: parameters must be exactly {CHECK_TYPES[check_type]}")
    if "criterion" in check:
        _require(check["criterion"] in CRITERIA, f"{check_id}: unknown criterion")


def load_corpus(path: Path = CORPUS_PATH) -> Corpus:
    return validate_corpus(json.loads(Path(path).read_text(encoding="utf-8")))


def load_corpus_version(version: str) -> Corpus:
    """The corpus of exactly this version, from its own file. Never a newer or older one."""
    if version not in CORPUS_PATHS:
        raise CorpusError("unknown corpus version")
    corpus = load_corpus(CORPUS_PATHS[version])
    if corpus.corpus_version != version:
        raise CorpusError("the corpus file declares another version")
    return corpus


# --- 3-4. a fresh database and the authority snapshot ---------------------------------------------


AUTHORITY_TABLES = tuple(table for table in DATA_TABLES if table not in AI_PROVENANCE_TABLES) + ("schema_meta",)
FIXTURE_TIME = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def build_evaluation_database(path: Path, corpus: Corpus) -> None:
    """A new schema-v6 file holding only the corpus fixtures; every AI table starts empty."""
    if Path(path).exists():
        raise FileExistsError("the evaluation database must be new")
    connection = open_database(str(path))
    try:
        repository = Repository(connection)
        for case in corpus.cases:
            repository.add_account(Account(case.account_id, f"Synthetic account {case.case_id}"))
            repository.add_interaction(SourceInteraction(case.interaction_id, case.account_id, FIXTURE_TIME,
                                                         case.interaction_text))
    finally:
        connection.close()


def authority_snapshot(path: Path) -> dict[str, list[tuple]]:
    """Every row of every authoritative (non-AI) table, read on a query-only connection."""
    connection = open_read_connection(path)
    try:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in AUTHORITY_TABLES}
    finally:
        connection.close()


# --- 5. checks ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    kind: str
    critical: str | None
    passed: bool
    code: str  # a machine reason, never source or model text


# free_text_forbids_numeric_content (design §11): a separate, simpler implementation than the production grounding
# tokenizer, which the harness never imports. A digit, or a whole word of the closed number grammar, is numeric.
NUMBER_GRAMMAR_WORDS = frozenset((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
))
_WORD = re.compile(r"[A-Za-z]+")


# CORE-NUMBERS for corpus v2: a simple ASCII literal, comma-grouped (d{1,3}(,ddd)+) or plain, with optional decimals,
# compared by exact decimal value (12,500 == 12500, 8.5 == 8.50, 05 == 5). Nothing else: no number words, scale,
# arithmetic, units, currency, comparators, or compounds. Independent of the production grounding implementation.
_LITERAL = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)|\d+(?:\.\d+)?")


def _literal_values(text: str) -> set[Decimal]:
    return {Decimal(literal.replace(",", "")) for literal in _LITERAL.findall(text)}


def _has_numeric_content(text: str) -> bool:
    return any(character.isdigit() for character in text) or any(
        word.lower() in NUMBER_GRAMMAR_WORDS for word in _WORD.findall(text))


def _free_text(output: BaecExtractionOutput) -> list[str]:
    """Every model-authored free-text field: both normalizations, every explanation, every uncertainty."""
    return (_normalizations(output) + [h.explanation for h in output.criterion_hypotheses]
            + list(output.uncertainties))


def _normalizations(output: BaecExtractionOutput) -> list[str]:
    return [text for text in (output.normalized_condition, output.normalized_evaluation_link) if text is not None]


def evaluate_check(check: dict, *, result, output: BaecExtractionOutput | None, provenance_ok: bool,
                   source_text: str) -> CheckResult:
    kind, critical, check_type = check["kind"], check["critical"], check["type"]

    def outcome(passed: bool, code: str) -> CheckResult:
        return CheckResult(check["check_id"], kind, critical, passed, "ok" if passed else code)

    if check_type == "provenance_complete":
        return outcome(provenance_ok, "provenance_incomplete")
    if check_type == "no_fabricated_evidence":
        codes = () if result is None else result.failure_codes
        return outcome(not FABRICATION_CODES & set(codes), "fabricated_or_non_verbatim_evidence")
    if check_type == "terminal_success":
        status = "incomplete" if result is None else result.status.value
        return outcome(status == AiRunStatus.SUCCESS.value, f"status_{status}")
    if check_type == "core_terminal_success":
        if result is None:  # incomplete or not started: operational, never a behavioral critical
            return CheckResult(check["check_id"], kind, None, False, "status_incomplete")
        status = result.status.value
        if status == AiRunStatus.SUCCESS.value:
            if output is not None and provenance_ok:
                return CheckResult(check["check_id"], kind, critical, True, "ok")
            return CheckResult(check["check_id"], kind, None, False, "artifact_unverified")  # CORE-PROVENANCE owns it
        behavioral = status in BEHAVIORAL_TERMINAL_FAILURES
        return CheckResult(check["check_id"], kind, critical if behavioral else None, False, f"status_{status}")
    if output is None:
        return outcome(check_type not in REQUIRES_ARTIFACT, "no_artifact")
    hypotheses = {h.criterion: h for h in output.criterion_hypotheses}
    excerpts = output.source_excerpts
    if check_type == "normalization_numbers_from_source_by_value":
        source_values = _literal_values(source_text)
        invented = [v for text in _normalizations(output) for v in _literal_values(text) if v not in source_values]
        return outcome(not invented, "number_not_in_source")
    if check_type == "normalization_numbers_from_source":
        source_numbers = set(_NUMBER.findall(source_text))
        invented = [n for text in _normalizations(output) for n in _NUMBER.findall(text) if n not in source_numbers]
        return outcome(not invented, "number_not_in_source")
    if check_type == "analysis_status_in":
        return outcome(output.analysis_status in check["allowed"], "analysis_status_outside_allowed")
    if check_type == "analysis_status_not":
        return outcome(output.analysis_status not in check["forbidden"], "analysis_status_forbidden")
    if check_type == "criterion_status_in":
        hypothesis = hypotheses.get(check["criterion"])
        return outcome(hypothesis is not None and hypothesis.status in check["allowed"], "criterion_status_outside_allowed")
    if check_type == "criterion_not_supported":
        hypothesis = hypotheses.get(check["criterion"])
        return outcome(hypothesis is None or hypothesis.status != "supported", "criterion_supported")
    if check_type == "no_criterion_supported":
        return outcome(all(h.status != "supported" for h in output.criterion_hypotheses), "criterion_supported")
    if check_type == "excerpt_contains":
        return outcome(any(check["text"] in e.text for e in excerpts), "expected_excerpt_missing")
    if check_type == "excerpt_fragment_requires":
        return outcome(all(check["requires"] in e.text for e in excerpts if check["fragment"] in e.text),
                       "excerpt_drops_required_qualifier")
    if check_type == "fragment_speaker_not":
        return outcome(all(e.attributed_speaker != check["speaker"] for e in excerpts if check["fragment"] in e.text),
                       "speaker_attribution_forbidden")
    if check_type == "fragment_speaker_in":
        matching = [e for e in excerpts if check["fragment"] in e.text]
        return outcome(bool(matching) and all(e.attributed_speaker in check["allowed"] for e in matching),
                       "speaker_attribution_outside_allowed")
    if check_type == "normalization_forbids":
        lowered = [text.lower() for text in _normalizations(output)]
        return outcome(not any(p.lower() in text for p in check["phrases"] for text in lowered),
                       "normalization_contains_forbidden_phrase")
    if check_type == "normalization_contains_any":
        lowered = [text.lower() for text in _normalizations(output)]
        return outcome(any(p.lower() in text for p in check["phrases"] for text in lowered),
                       "normalization_missing_expected_phrase")
    if check_type == "normalization_fragment_requires_any":
        lowered = [text.lower() for text in _normalizations(output)]
        return outcome(all(any(p.lower() in text for p in check["allowed"]) for text in lowered
                           if check["fragment"].lower() in text), "normalization_drops_required_comparator")
    if check_type == "free_text_forbids_numeric_content":
        return outcome(not any(_has_numeric_content(text) for text in _free_text(output)), "numeric_content_in_free_text")
    if check_type == "supporting_excerpts_exclude":
        supporting = {ref for h in output.criterion_hypotheses if h.status == "supported" for ref in h.excerpt_refs}
        used = [e.text for e in excerpts if e.excerpt_id in supporting]
        return outcome(not any(f in text for f in check["fragments"] for text in used), "injected_text_used_as_support")
    raise CorpusError(f"unknown check type {check_type}")


# --- 5. running the cases --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    ai_run_id: str
    status: str
    artifact_present: bool
    checks: tuple[CheckResult, ...]
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None  # the class name of an unexpected local failure; never a message or traceback
    failure_codes: tuple[str, ...] = ()  # read back from the persisted terminal result, never from memory
    reported_status: str | None = None  # the status the service returned in memory, cross-checked by the audit

    @property
    def provenance_ok(self) -> bool:
        return all(c.passed for c in self.checks if c.check_id == "CORE-PROVENANCE")

    @property
    def hard(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.kind == "hard")

    @property
    def observational(self) -> tuple[CheckResult, ...]:
        """Retained and reported, but never counted toward eligibility or ranking (design §7.3)."""
        return tuple(c for c in self.checks if c.kind == "observational")

    @property
    def critical_failures(self) -> tuple[str, ...]:
        return tuple(sorted({c.critical for c in self.hard if not c.passed and c.critical}))


@dataclass(frozen=True)
class EvaluationReport:
    model: str
    corpus_version: str
    outcomes: tuple[CaseOutcome, ...]
    authority_unchanged: bool
    audit: RunAudit | None = None  # required for a valid run: a missing audit fails closed
    report_failed: bool = False  # the sanitized report could not be built, written, or verified

    @property
    def calls_attempted(self) -> int:
        return len(self.outcomes)

    @property
    def successful_artifacts(self) -> int:
        return sum(o.artifact_present for o in self.outcomes)

    @property
    def hard_passed(self) -> int:
        return sum(c.passed for o in self.outcomes for c in o.hard)

    @property
    def hard_failed(self) -> int:
        return sum(not c.passed for o in self.outcomes for c in o.hard)

    @property
    def hard_total(self) -> int:
        return sum(len(o.hard) for o in self.outcomes)

    @property
    def observational_passed(self) -> int:
        return sum(c.passed for o in self.outcomes for c in o.observational)

    @property
    def observational_total(self) -> int:
        return sum(len(o.observational) for o in self.outcomes)

    @property
    def critical_failures(self) -> tuple[str, ...]:
        found = {f for o in self.outcomes for f in o.critical_failures}
        if not self.authority_unchanged:
            found.add("authority_mutation")  # report-level: an authoritative table changed during the run
        return tuple(sorted(found))

    @property
    def operational_failures(self) -> tuple[str, ...]:
        """Why this run cannot be compared: transport, API, model, persistence, or authority problems."""
        found = {o.status for o in self.outcomes if o.status in OPERATIONALLY_INVALID_STATUSES}
        if any(not o.provenance_ok for o in self.outcomes):
            found.add("provenance_failure")
        if not self.authority_unchanged:
            found.add("authority_mutation")
        if not self._audit_consistent():
            found.add("audit_failure")
        if self.audit is not None and not self.audit.temporary_database_cleaned:
            found.add("cleanup_failure")
        if self.report_failed:
            found.add("report_failure")
        return tuple(sorted(found))

    def _audit_consistent(self) -> bool:
        """The persisted audit exists, was built, and agrees with what the run observed."""
        audit = self.audit
        if audit is None or audit.failure is not None:
            return False
        recorded = [o for o in self.outcomes if o.ai_run_id != "-"]
        terminal = [o for o in recorded if o.status not in ("incomplete", "not_started")]
        return (audit.cases_attempted == len(self.outcomes) == audit.cases_expected
                and audit.runs_present == len(recorded)
                and audit.terminal_results_present == len(terminal)
                and audit.requested_model_matches == audit.runs_present
                and audit.provenance_verified_cases == sum(o.provenance_ok for o in self.outcomes)
                and audit.authoritative_tables_unchanged == self.authority_unchanged
                # what the service said in memory must agree with what was persisted
                and all(o.reported_status in (None, o.status) for o in self.outcomes)
                # the failure codes read for the case and those read by the audit are the same persisted codes
                and [c.failure_codes for c in audit.cases] == [o.failure_codes for o in self.outcomes]
                and [c.case_id for c in audit.cases] == [o.case_id for o in self.outcomes])

    @property
    def operationally_valid(self) -> bool:
        return not self.operational_failures

    @property
    def total_input_tokens(self) -> int:
        return sum(o.input_tokens or 0 for o in self.outcomes)

    @property
    def total_output_tokens(self) -> int:
        return sum(o.output_tokens or 0 for o in self.outcomes)

    @property
    def total_elapsed_seconds(self) -> float:
        return sum(o.elapsed_seconds for o in self.outcomes)


def excerpt_rows_match_artifact(output: BaecExtractionOutput, stored) -> bool:
    """The persisted excerpt rows are exactly the artifact's v1 source_excerpts, matched by excerpt ID.

    Same count, same IDs (no duplicates on either side), and for each ID the same source interaction,
    exact text, and attributed speaker. Order is not relied on. This belongs to the harness, which
    knows the v1 contract; Phase 6B verifies only generic record integrity. Returns a bare boolean:
    no excerpt text is ever reported.
    """
    expected = {e.excerpt_id: (e.source_interaction_id, e.text, e.attributed_speaker) for e in output.source_excerpts}
    persisted = {e.excerpt_id: (e.interaction_id, e.text, e.attributed_speaker.value) for e in stored}
    return len(expected) == len(output.source_excerpts) and len(persisted) == len(stored) and expected == persisted


def verify_provenance(store: AiProvenanceStore, ai_run_id: str | None):
    """CORE-PROVENANCE: persisted provenance is complete and coherent for the terminal status observed.

    Returns (ok, result, output). Always required: the run, and its terminal result, both loading
    with their 6B integrity checks. A recorded output digest requires the output, digest-verified.
    success additionally requires the artifact and every excerpt, verified and bound to the run,
    and an artifact that parses as the v1 contract, whose source_excerpts are exactly the persisted
    excerpt rows (see excerpt_rows_match_artifact). Any other terminal status requires no artifact,
    and must have none. A legitimate non-success status is never a provenance failure by itself.
    """
    if ai_run_id is None:
        return False, None, None  # the case never reached a recorded run
    try:
        store.get_run(ai_run_id)
        result = store.get_result(ai_run_id)  # also enforces the 6B success/artifact and output pairing
    except Exception:  # noqa: BLE001 - missing or corrupt: reported only as a provenance failure
        return False, None, None
    try:
        if result.output_digest is not None:
            store.get_output(ai_run_id)
        if result.status is AiRunStatus.SUCCESS:
            artifact = store.get_artifact_for_run(ai_run_id)
            stored = store.list_artifact_excerpts(artifact.artifact_id)
            output = BaecExtractionOutput.model_validate_json(artifact.canonical_result)
            if not excerpt_rows_match_artifact(output, stored):
                return False, result, None  # the persisted excerpt projection no longer matches the artifact
            return True, result, output
        try:
            store.get_artifact_for_run(ai_run_id)
        except RepositoryNotFoundError:
            return True, result, None  # no artifact is exactly what a non-success outcome must have
        return False, result, None
    except Exception:  # noqa: BLE001
        return False, result, None


# --- 6. the persistence audit ------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseAudit:
    """One attempted case, read back from the persisted schema-v6 records before cleanup.

    Identifiers, version labels, statuses, model IDs, booleans, and a count only: never stored
    text, canonical results, excerpts, prompts, or digest values.
    """

    case_id: str
    ai_run_id: str | None
    run_present: bool
    terminal_result_present: bool
    requested_model: str | None
    returned_model: str | None
    terminal_status: str | None
    remote_outcome: str | None
    request_spec_version: str | None
    prompt_version: str | None
    input_version: str | None
    output_schema_version: str | None
    canonicalization_version: str | None
    validation_version: str | None
    request_digest_present: bool
    prompt_digest_present: bool
    input_digest_present: bool
    output_schema_digest_present: bool
    output_present: bool
    artifact_present: bool
    excerpt_count: int
    provenance_verified: bool
    failure_codes: tuple[str, ...] = ()  # closed machine tokens from the persisted terminal result


@dataclass(frozen=True)
class RunAudit:
    """The whole-run audit. It is all that survives the temporary database."""

    cases_expected: int
    cases_attempted: int
    runs_present: int
    terminal_results_present: int
    requested_model_matches: int
    returned_model_ids_seen: tuple[str, ...]
    provenance_verified_cases: int
    authoritative_tables_unchanged: bool
    temporary_database_cleaned: bool
    cases: tuple[CaseAudit, ...]
    failure: str | None = None  # the class name if the audit could not be built; never a message


def _digest_present(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value) is not None


def _model_id(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _MODEL_ID.fullmatch(value) else UNRECOGNIZED_MODEL_ID


def _row_present(connection, table: str, ai_run_id: str) -> bool:
    return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE ai_run_id = ?", (ai_run_id,)).fetchone()[0] == 1


def _audit_case(store: AiProvenanceStore, connection, case_id: str, ai_run_id: str | None) -> CaseAudit:
    """Read one case back through the store. Unreadable or corrupt records are reported, never assumed.

    A stored record the store refuses (PersistenceError) yields False/None for what it would have
    supplied. Any other failure (a closed or missing database) propagates: the audit itself failed.
    """
    fields = dict(case_id=case_id, ai_run_id=ai_run_id, run_present=False, terminal_result_present=False,
                  requested_model=None, returned_model=None, terminal_status=None, remote_outcome=None,
                  request_spec_version=None, prompt_version=None, input_version=None, output_schema_version=None,
                  canonicalization_version=None, validation_version=None, request_digest_present=False,
                  prompt_digest_present=False, input_digest_present=False, output_schema_digest_present=False, output_present=False,
                  artifact_present=False, excerpt_count=0, provenance_verified=False)
    if ai_run_id is None:
        return CaseAudit(**fields)  # no run was ever recorded for this case
    fields.update(run_present=_row_present(connection, "ai_runs", ai_run_id),
                  terminal_result_present=_row_present(connection, "ai_run_results", ai_run_id))
    try:
        run = store.get_run(ai_run_id)
    except PersistenceError:
        return CaseAudit(**fields)
    fields.update(requested_model=run.requested_model, request_spec_version=run.request_spec_version,
                  prompt_version=run.prompt_version, input_version=run.input_version,
                  output_schema_version=run.output_schema_version,
                  canonicalization_version=run.canonicalization_version,
                  validation_version=run.validation_version,
                  request_digest_present=_digest_present(run.request_digest),
                  prompt_digest_present=_digest_present(run.prompt_digest),
                  input_digest_present=_digest_present(run.input_digest),
                  output_schema_digest_present=_digest_present(run.output_schema_digest))
    try:
        result = store.get_result(ai_run_id)
    except PersistenceError:
        return CaseAudit(**fields)
    fields.update(returned_model=_model_id(result.response_model), terminal_status=result.status.value,
                  remote_outcome=result.remote_outcome.value, failure_codes=result.failure_codes)
    artifact, excerpts_match = None, True
    try:
        if result.output_digest is not None:
            fields["output_present"] = _digest_present(store.get_output(ai_run_id).output_digest)
        try:
            artifact = store.get_artifact_for_run(ai_run_id)
        except RepositoryNotFoundError:
            pass
        if artifact is not None:
            stored = store.list_artifact_excerpts(artifact.artifact_id)
            fields.update(artifact_present=True, excerpt_count=len(stored))
            cited = BaecExtractionOutput.model_validate_json(artifact.canonical_result).source_excerpts
            # independently of CORE-PROVENANCE: every excerpt the artifact cites is stored, and nothing else is
            keys = [(e.excerpt_id, e.source_interaction_id, e.text, e.attributed_speaker) for e in cited]
            rows = [(e.excerpt_id, e.interaction_id, e.text, e.attributed_speaker.value) for e in stored]
            excerpts_match = sorted(keys) == sorted(rows) and len({k[0] for k in keys}) == len(keys)
    except (PersistenceError, ValueError):  # ValueError: a stored canonical result that no longer parses
        return CaseAudit(**fields)
    success = result.status is AiRunStatus.SUCCESS
    fields["provenance_verified"] = (
        verify_provenance(store, ai_run_id)[0]
        and all(fields[f"{name}_digest_present"] for name in ("request", "prompt", "input", "output_schema"))
        and fields["output_present"] == (result.output_digest is not None)
        and fields["artifact_present"] == success
        and excerpts_match
        and (artifact is None or (artifact.output_schema_version == run.output_schema_version
                                  and artifact.task_version == run.task_version))
    )
    return CaseAudit(**fields)


def audit_persisted_cases(store: AiProvenanceStore, connection, outcomes) -> tuple[CaseAudit, ...]:
    """Audit every attempted case from the stored records. Only case and run identifiers come from the run."""
    return tuple(_audit_case(store, connection, o.case_id, None if o.ai_run_id == "-" else o.ai_run_id)
                 for o in outcomes)


def summarize_audit(cases: tuple[CaseAudit, ...], *, model: str, cases_expected: int, authority_unchanged: bool,
                    cleaned: bool, failure: str | None) -> RunAudit:
    return RunAudit(
        cases_expected=cases_expected,
        cases_attempted=len(cases) if failure is None else 0,
        runs_present=sum(c.run_present for c in cases),
        terminal_results_present=sum(c.terminal_result_present for c in cases),
        requested_model_matches=sum(c.requested_model == model for c in cases),
        returned_model_ids_seen=tuple(sorted({c.returned_model for c in cases if c.returned_model is not None})),
        provenance_verified_cases=sum(c.provenance_verified for c in cases),
        authoritative_tables_unchanged=authority_unchanged,
        temporary_database_cleaned=cleaned,
        cases=cases,
        failure=failure,
    )


def _delete_directory(directory: Path) -> bool:
    """Delete the temporary evaluation directory. True only once it is confirmed gone from disk."""
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()


def _evaluate_case(case: LiveCase, ai_run_id: str | None, store: AiProvenanceStore, elapsed: float,
                   error: str | None = None, *, corpus_version: str = CORPUS_VERSION,
                   reported_status: str | None = None) -> CaseOutcome:
    provenance_ok, result, output = verify_provenance(store, ai_run_id)
    checks = tuple(
        evaluate_check(check, result=result, output=output, provenance_ok=provenance_ok, source_text=case.interaction_text)
        for check in core_checks(corpus_version) + case.checks
    )
    if result is not None:
        status = result.status.value
    else:
        status = "incomplete" if ai_run_id is not None else "not_started"
    return CaseOutcome(case.case_id, ai_run_id or "-", status, output is not None, checks, elapsed,
                       None if result is None else result.input_tokens,
                       None if result is None else result.output_tokens, error,
                       failure_codes=() if result is None else result.failure_codes,  # the persisted codes
                       reported_status=reported_status)


def case_line(model: str, corpus_version: str, outcome: CaseOutcome) -> str:
    hard, observational = outcome.hard, outcome.observational
    return (f"{model} {corpus_version} {outcome.case_id} {outcome.ai_run_id} {outcome.status} "
            f"artifact={'yes' if outcome.artifact_present else 'no'} "
            f"hard={sum(c.passed for c in hard)}/{len(hard)} "
            f"failed={','.join(c.check_id for c in hard if not c.passed) or '-'} "
            f"obs={sum(c.passed for c in observational)}/{len(observational)} "
            f"obs_failed={','.join(c.check_id for c in observational if not c.passed) or '-'} "
            f"codes={','.join(outcome.failure_codes) or '-'} "
            f"elapsed={outcome.elapsed_seconds:.2f}s in={outcome.input_tokens} out={outcome.output_tokens}"
            + (f" error={outcome.error}" if outcome.error else ""))


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def audit_lines(audit: RunAudit | None) -> list[str]:
    if audit is None:
        return ["AUDIT-SUMMARY audit_failure=missing"]
    lines = [
        f"AUDIT {c.case_id} {c.ai_run_id or '-'} run={_yes(c.run_present)} result={_yes(c.terminal_result_present)} "
        f"requested={c.requested_model or '-'} returned={c.returned_model or '-'} status={c.terminal_status or '-'} "
        f"remote={c.remote_outcome or '-'} spec={c.request_spec_version or '-'} prompt={c.prompt_version or '-'} "
        f"input={c.input_version or '-'} schema={c.output_schema_version or '-'} "
        f"canonicalization={c.canonicalization_version or '-'} validation={c.validation_version or '-'} "
        f"digests=request:{_yes(c.request_digest_present)},prompt:{_yes(c.prompt_digest_present)},"
        f"input:{_yes(c.input_digest_present)},schema:{_yes(c.output_schema_digest_present)} "
        f"output={_yes(c.output_present)} artifact={_yes(c.artifact_present)} excerpts={c.excerpt_count} "
        f"codes={','.join(c.failure_codes) or '-'} verified={_yes(c.provenance_verified)}"
        for c in audit.cases
    ]
    lines.append(
        f"AUDIT-SUMMARY cases_expected={audit.cases_expected} cases_attempted={audit.cases_attempted} "
        f"runs_present={audit.runs_present} terminal_results_present={audit.terminal_results_present} "
        f"requested_model_matches={audit.requested_model_matches} "
        f"returned_model_ids_seen={','.join(audit.returned_model_ids_seen) or '-'} "
        f"provenance_verified_cases={audit.provenance_verified_cases} "
        f"authoritative_tables_unchanged={audit.authoritative_tables_unchanged} "
        f"temporary_database_cleaned={audit.temporary_database_cleaned} audit_failure={audit.failure or '-'}"
    )
    return lines


def summary_lines(report: EvaluationReport) -> list[str]:
    return audit_lines(report.audit) + [
        f"SUMMARY model={report.model} corpus={report.corpus_version} calls={report.calls_attempted} "
        f"artifacts={report.successful_artifacts} hard_passed={report.hard_passed} hard_failed={report.hard_failed} "
        f"observational_passed={report.observational_passed}/{report.observational_total} "
        f"critical={','.join(report.critical_failures) or '-'} authority_unchanged={report.authority_unchanged} "
        f"operationally_valid={report.operationally_valid} "
        f"operational_failures={','.join(report.operational_failures) or '-'} "
        f"input_tokens={report.total_input_tokens} output_tokens={report.total_output_tokens} "
        f"elapsed={report.total_elapsed_seconds:.2f}s",
    ]


def run_evaluation(corpus: Corpus, *, model: str, provider=None, emit: Callable[[str], None] = print,
                   clock=None) -> EvaluationReport:
    """Run every case once, serially, through the production runtime on a fresh temporary database.

    provider=None uses the production default (the real Anthropic provider); offline tests inject a fake.
    """
    if type(corpus) is not Corpus:
        raise CorpusError("run_evaluation requires a validated Corpus")
    if CORE_TERMINAL_SUCCESS in core_checks(corpus.corpus_version) and any(
            check["type"] == "terminal_success" for case in corpus.cases for check in case.checks):
        raise CorpusError("CORE-TERMINAL-SUCCESS replaces case-level terminal_success: never count it twice")
    directory = Path(tempfile.mkdtemp(prefix="baec-live-eval-"))
    try:
        path = directory / "evaluation.sqlite3"
        build_evaluation_database(path, corpus)
        before = authority_snapshot(path)
        outcomes = []
        runtime = open_extraction_runtime(path, provider=provider, clock=clock)
        inspection = connect(str(path))
        try:
            store = AiProvenanceStore(inspection)
            for case in corpus.cases:  # serial: each terminal outcome is persisted before the next call
                started = time.monotonic()
                already_incomplete = {run.ai_run_id for run in store.list_incomplete_runs()}
                try:
                    result = runtime.service.extract_interaction(account_id=case.account_id,
                                                                 interaction_id=case.interaction_id, model=model)
                except Exception as error:  # noqa: BLE001 - unexpected: recorded by class name, never retried
                    new = [run.ai_run_id for run in store.list_incomplete_runs() if run.ai_run_id not in already_incomplete]
                    outcome = _evaluate_case(case, new[0] if new else None, store, time.monotonic() - started,
                                             error=type(error).__name__, corpus_version=corpus.corpus_version)
                else:
                    outcome = _evaluate_case(case, result.ai_run_id, store, time.monotonic() - started,
                                             corpus_version=corpus.corpus_version,
                                             reported_status=result.status.value)
                outcomes.append(outcome)
                emit(case_line(model, corpus.corpus_version, outcome))
            authority_unchanged = authority_snapshot(path) == before
            try:  # read back from the stored records while the temporary database still exists
                audited, audit_failure = audit_persisted_cases(store, inspection, outcomes), None
            except Exception as error:  # noqa: BLE001 - an infrastructure failure: recorded by class name only
                audited, audit_failure = (), type(error).__name__
        finally:
            inspection.close()
            runtime.close()
    finally:
        cleaned = _delete_directory(directory)
    audit = summarize_audit(audited, model=model, cases_expected=len(corpus.cases),
                            authority_unchanged=authority_unchanged, cleaned=cleaned, failure=audit_failure)
    report = EvaluationReport(model, corpus.corpus_version, tuple(outcomes), authority_unchanged, audit)
    for line in summary_lines(report):
        emit(line)
    return report


# --- comparison --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonDecision:
    outcome: str  # "prefer" | "defer" | "none_eligible"
    preferred: str | None
    eligible: tuple[str, ...]
    reason: str


def compare_models(reports: Mapping[str, EvaluationReport]) -> ComparisonDecision:
    """The locked comparison rule. It informs a human decision; it never sets a default model.

    First, operational validity: if any run had an API or transport failure, a model mismatch, an
    interrupted or incomplete case, a provenance failure, or an authority mutation, the whole
    comparison is deferred as operationally inconclusive. Nothing (hard checks, artifacts, tokens,
    time) overrides that, and nothing is retried. Then, for valid runs: a model with any critical
    failure is ineligible; among eligible models more hard checks passed wins, then more successful
    semantically valid artifacts. Tokens and time are descriptive only and never rank. Equal results
    are a tie: defer, carrying both forward.
    """
    if any(not report.operationally_valid for report in reports.values()):
        return ComparisonDecision("defer", None, (), "operationally_inconclusive")
    eligible = tuple(sorted(model for model, report in reports.items() if not report.critical_failures))
    if not eligible:
        return ComparisonDecision("none_eligible", None, (), "every model has a critical failure")
    if len(eligible) == 1:
        return ComparisonDecision("prefer", eligible[0], eligible, "the only model without a critical failure")
    ranked = sorted(eligible, key=lambda m: (reports[m].hard_passed, reports[m].successful_artifacts), reverse=True)
    best, second = reports[ranked[0]], reports[ranked[1]]
    if (best.hard_passed, best.successful_artifacts) == (second.hard_passed, second.successful_artifacts):
        return ComparisonDecision("defer", None, eligible, "materially indistinguishable on v1: carry both forward")
    basis = "hard checks passed" if best.hard_passed != second.hard_passed else "successful artifacts"
    return ComparisonDecision("prefer", ranked[0], eligible, f"more {basis}")
