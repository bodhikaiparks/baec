"""Phase 6D-C2: strict loading of baec-live-evaluation-report/v2 files and their deterministic comparison.

Evaluation rules (docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §10). Live-evaluation layer only.

load_report(path) trusts nothing it reads. The bytes must be UTF-8 JSON with no duplicate keys; every
object must have exactly its v1 fields with the right types; every value must pass the C1 allowlist; the
cases must be internally possible; every aggregate is recomputed from the cases, checks, audit, and
authority flag and must equal the stored one; and the report, re-serialized canonically, must reproduce
the file byte for byte. Nothing is repaired. Errors carry only a closed category, never content or paths.

Only report v2 is accepted. Report v1 recorded only the persisted status, so the part of audit validity that
compares it with the status the service returned in memory could not be recomputed; v1 was superseded before
any authorized live use (design §8, Phase 6D-C2 clarification). A v1 file fails with unsupported_version: it is
never migrated, upgraded, or compared. v2 records both closed statuses per case, and the loader re-derives
their agreement itself.

compare_reports(a, b) applies the locked rule: comparability first, then operational validity, behavioral
eligibility, hard checks passed, successful artifacts, and otherwise a tie. Tokens, latency, observational
results, timestamps, returned model IDs, and report digests are never consulted. It informs a human
decision and never sets a default or production model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from tests.live.harness import (
    CORE_CHECKS_BY_CORPUS,
    OPERATIONALLY_INVALID_STATUSES,
    CaseAudit,
    RunAudit,
)
from tests.live.report import (
    _FILENAME,
    REPORT_VERSION,
    VERSIONS,
    CorpusMetadata,
    LiveEvaluationReport,
    ReportAggregate,
    ReportCase,
    ReportCheck,
    ReportFailure,
    SourceMetadata,
    derive_aggregate,
    serialize,
)

LOAD_ERRORS = ("unreadable", "not_utf8", "invalid_json", "duplicate_key", "unsupported_version", "schema",
               "invalid_value", "duplicate_case", "duplicate_run", "duplicate_check", "impossible_case", "aggregate_mismatch",
               "noncanonical")
DECISIONS = ("prefer", "defer")
REASONS = ("not_comparable", "operationally_inconclusive", "none_eligible", "only_eligible_model",
           "more_hard_checks_passed", "more_successful_artifacts", "behaviorally_tied")
MISMATCHES = ("report_version", "source_commit", "source_working_tree_dirty", "corpus_version", "corpus_sha256",
              "corpus_case_count", "case_set") + tuple(f"version_{key}" for key in VERSIONS) + (
              "transport_timeout_seconds", "transport_max_retries", "same_requested_model")


class ReportLoadError(Exception):
    """A report file failed strict loading. The message is only a closed category from LOAD_ERRORS."""

    def __init__(self, category: str) -> None:
        if category not in LOAD_ERRORS:
            category = "invalid_value"
        super().__init__(category)
        self.category = category


def _fail(category: str):
    raise ReportLoadError(category)


# --- parsing --------------------------------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        _fail("duplicate_key")
    return dict(pairs)


def _no_constant(name: str):
    _fail("invalid_json")  # NaN, Infinity, -Infinity


def _object(value: object, fields: tuple[str, ...]) -> dict:
    if type(value) is not dict or set(value) != set(fields):
        _fail("schema")
    return value


def _int(value: object) -> int:
    if type(value) is not int:
        _fail("schema")
    return value


def _optional_int(value: object) -> int | None:
    return None if value is None else _int(value)


def _bool(value: object) -> bool:
    if type(value) is not bool:
        _fail("schema")
    return value


def _str(value: object) -> str:
    if type(value) is not str:
        _fail("schema")
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _list(value: object) -> list:
    if type(value) is not list:
        _fail("schema")
    return value


def _strings(value: object) -> tuple[str, ...]:
    return tuple(_str(item) for item in _list(value))


_CHECK = ("check_id", "kind", "passed", "code", "critical_class")
_CASE = ("case_id", "ai_run_id", "terminal_status", "service_terminal_status", "artifact_present", "failure_codes", "error_class", "elapsed_ms",
         "input_tokens", "output_tokens", "checks")
_AUDIT_CASE = ("case_id", "ai_run_id", "run_present", "terminal_result_present", "requested_model", "returned_model",
               "terminal_status", "remote_outcome", "request_spec_version", "prompt_version", "input_version",
               "output_schema_version", "canonicalization_version", "validation_version", "request_digest_present",
               "prompt_digest_present", "input_digest_present", "output_schema_digest_present", "output_present",
               "artifact_present", "excerpt_count", "failure_codes", "provenance_verified")
_AUDIT = ("cases_expected", "cases_attempted", "runs_present", "terminal_results_present", "requested_model_matches",
          "returned_model_ids_seen", "provenance_verified_cases", "authoritative_tables_unchanged",
          "temporary_database_cleaned", "failure", "cases")
_AGGREGATE = ("calls_attempted", "terminal_status_counts", "successful_artifacts", "hard_passed", "hard_total",
              "critical_failures", "observational_passed", "observational_total", "operational_failures",
              "operationally_valid", "total_input_tokens", "total_output_tokens", "total_elapsed_ms")
_TOP = ("report_version", "generated_at", "source", "corpus", "versions", "transport", "requested_model",
        "returned_model_ids", "cases", "audit", "authority_unchanged", "aggregate")
_AUDIT_STRINGS = ("case_id", "ai_run_id", "requested_model", "returned_model", "terminal_status", "remote_outcome",
                  "request_spec_version", "prompt_version", "input_version", "output_schema_version",
                  "canonicalization_version", "validation_version")
_AUDIT_FLAGS = ("run_present", "terminal_result_present", "request_digest_present", "prompt_digest_present",
                "input_digest_present", "output_schema_digest_present", "output_present", "artifact_present",
                "provenance_verified")


def _check(value: object) -> ReportCheck:
    data = _object(value, _CHECK)
    return ReportCheck(_str(data["check_id"]), _str(data["kind"]), _bool(data["passed"]), _str(data["code"]),
                       _optional_str(data["critical_class"]))


def _case(value: object) -> ReportCase:
    data = _object(value, _CASE)
    return ReportCase(
        case_id=_str(data["case_id"]), ai_run_id=_optional_str(data["ai_run_id"]),
        terminal_status=_str(data["terminal_status"]),
        service_terminal_status=_optional_str(data["service_terminal_status"]),
        artifact_present=_bool(data["artifact_present"]),
        failure_codes=_strings(data["failure_codes"]), error_class=_optional_str(data["error_class"]),
        elapsed_ms=_int(data["elapsed_ms"]), input_tokens=_optional_int(data["input_tokens"]),
        output_tokens=_optional_int(data["output_tokens"]),
        checks=tuple(_check(item) for item in _list(data["checks"])),
    )


def _audit_case(value: object) -> CaseAudit:
    data = _object(value, _AUDIT_CASE)
    fields = {name: _optional_str(data[name]) for name in _AUDIT_STRINGS}
    fields["case_id"] = _str(data["case_id"])
    fields.update({name: _bool(data[name]) for name in _AUDIT_FLAGS})
    return CaseAudit(**fields, excerpt_count=_int(data["excerpt_count"]), failure_codes=_strings(data["failure_codes"]))


def _audit(value: object) -> RunAudit:
    data = _object(value, _AUDIT)
    return RunAudit(
        cases_expected=_int(data["cases_expected"]), cases_attempted=_int(data["cases_attempted"]),
        runs_present=_int(data["runs_present"]), terminal_results_present=_int(data["terminal_results_present"]),
        requested_model_matches=_int(data["requested_model_matches"]),
        returned_model_ids_seen=_strings(data["returned_model_ids_seen"]),
        provenance_verified_cases=_int(data["provenance_verified_cases"]),
        authoritative_tables_unchanged=_bool(data["authoritative_tables_unchanged"]),
        temporary_database_cleaned=_bool(data["temporary_database_cleaned"]),
        cases=tuple(_audit_case(item) for item in _list(data["cases"])),
        failure=_optional_str(data["failure"]),
    )


def _aggregate(value: object) -> ReportAggregate:
    data = _object(value, _AGGREGATE)
    counts = data["terminal_status_counts"]
    critical = data["critical_failures"]
    if type(counts) is not dict or type(critical) is not dict:
        _fail("schema")
    return ReportAggregate(
        calls_attempted=_int(data["calls_attempted"]),
        terminal_status_counts=tuple((_str(k), _int(v)) for k, v in counts.items()),
        successful_artifacts=_int(data["successful_artifacts"]), hard_passed=_int(data["hard_passed"]),
        hard_total=_int(data["hard_total"]),
        critical_failures=tuple((_str(k), _strings(v)) for k, v in critical.items()),
        observational_passed=_int(data["observational_passed"]),
        observational_total=_int(data["observational_total"]),
        operational_failures=_strings(data["operational_failures"]),
        operationally_valid=_bool(data["operationally_valid"]),
        total_input_tokens=_int(data["total_input_tokens"]), total_output_tokens=_int(data["total_output_tokens"]),
        total_elapsed_ms=_int(data["total_elapsed_ms"]),
    )


def _report(value: object) -> LiveEvaluationReport:
    data = _object(value, _TOP)
    source = _object(data["source"], ("commit", "working_tree_clean"))
    corpus = _object(data["corpus"], ("version", "case_count", "sha256"))
    versions = _object(data["versions"], tuple(VERSIONS))
    transport = _object(data["transport"], ("timeout_seconds", "max_retries"))
    return LiveEvaluationReport(
        report_version=_str(data["report_version"]), generated_at=_str(data["generated_at"]),
        source=SourceMetadata(_str(source["commit"]), _bool(source["working_tree_clean"])),
        corpus=CorpusMetadata(_str(corpus["version"]), _int(corpus["case_count"]), _str(corpus["sha256"])),
        versions=tuple((key, _str(versions[key])) for key in VERSIONS),
        timeout_seconds=_int(transport["timeout_seconds"]), max_retries=_int(transport["max_retries"]),
        requested_model=_str(data["requested_model"]), returned_model_ids=_strings(data["returned_model_ids"]),
        cases=tuple(_case(item) for item in _list(data["cases"])), audit=_audit(data["audit"]),
        authority_unchanged=_bool(data["authority_unchanged"]), aggregate=_aggregate(data["aggregate"]),
    )


# --- verification --------------------------------------------------------------------------------------


def _audit_consistent(report: LiveEvaluationReport) -> bool:
    """The harness audit-consistency rule, recomputed from what the report itself records."""
    audit, cases = report.audit, report.cases
    if audit.failure is not None:
        return False
    recorded = [c for c in cases if c.ai_run_id is not None]
    terminal = [c for c in recorded if c.terminal_status not in ("incomplete", "not_started")]
    provenance_ok = sum(_provenance_ok(c) for c in cases)
    return (audit.cases_attempted == len(cases) == audit.cases_expected
            and audit.runs_present == len(recorded)
            and audit.terminal_results_present == len(terminal)
            and audit.requested_model_matches == audit.runs_present
            and audit.provenance_verified_cases == provenance_ok
            and audit.authoritative_tables_unchanged == report.authority_unchanged
            # what the service returned in memory agrees with what was persisted, wherever it returned a status
            and all(c.service_terminal_status in (None, c.terminal_status) for c in cases)
            and [c.failure_codes for c in audit.cases] == [c.failure_codes for c in cases]
            and [c.case_id for c in audit.cases] == [c.case_id for c in cases])


def _provenance_ok(case: ReportCase) -> bool:
    return all(k.passed for k in case.checks if k.check_id == "CORE-PROVENANCE")


def recompute_operational_failures(report: LiveEvaluationReport) -> tuple[str, ...]:
    found = {c.terminal_status for c in report.cases if c.terminal_status in OPERATIONALLY_INVALID_STATUSES}
    if any(not _provenance_ok(c) for c in report.cases):
        found.add("provenance_failure")
    if not report.authority_unchanged:
        found.add("authority_mutation")
    if not _audit_consistent(report):
        found.add("audit_failure")
    if not report.audit.temporary_database_cleaned:
        found.add("cleanup_failure")
    return tuple(sorted(found))


def verify_report(report: LiveEvaluationReport) -> None:
    """Every structural and aggregate invariant of a v1 report. Raises ReportLoadError; never repairs."""
    if type(report) is not LiveEvaluationReport:
        _fail("schema")
    if report.report_version != REPORT_VERSION:
        _fail("unsupported_version")
    cases = report.cases
    case_ids = [c.case_id for c in cases]
    if len(case_ids) != len(set(case_ids)):
        _fail("duplicate_case")
    run_ids = [c.ai_run_id for c in cases if c.ai_run_id is not None]
    if len(run_ids) != len(set(run_ids)):
        _fail("duplicate_run")
    core = [check["check_id"] for check in CORE_CHECKS_BY_CORPUS.get(report.corpus.version, ())]
    if not core or report.corpus.case_count != len(cases):
        _fail("impossible_case")
    for case in cases:
        check_ids = [k.check_id for k in case.checks]
        if len(check_ids) != len(set(check_ids)):
            _fail("duplicate_check")
        if check_ids[:len(core)] != core or any(not k.startswith(f"{case.case_id}-") for k in check_ids[len(core):]):
            _fail("impossible_case")  # every case carries its corpus's core checks, then only its own checks
        if any(k.kind != "hard" for k in case.checks[:len(core)]):
            _fail("impossible_case")
        if case.artifact_present and case.terminal_status != "success":
            _fail("impossible_case")
        if (case.ai_run_id is None) != (case.terminal_status == "not_started"):
            _fail("impossible_case")
        # a service terminal result means a recorded run; no service result means the case never concluded
        if case.service_terminal_status is None and case.terminal_status not in ("incomplete", "not_started"):
            _fail("impossible_case")
        if case.service_terminal_status is not None and case.ai_run_id is None:
            _fail("impossible_case")
        if case.terminal_status in ("incomplete", "not_started") and (case.input_tokens, case.output_tokens) != (None, None):
            _fail("impossible_case")
    if report.returned_model_ids != report.audit.returned_model_ids_seen:
        _fail("aggregate_mismatch")
    # the audit read each stored result independently: where it saw a status or an artifact, the case agrees
    for case, audited in zip(cases, report.audit.cases):
        if audited.terminal_status is not None and audited.terminal_status != case.terminal_status:
            _fail("aggregate_mismatch")
        if case.artifact_present and not audited.artifact_present:
            _fail("aggregate_mismatch")
    try:
        recomputed = derive_aggregate(cases, authority_unchanged=report.authority_unchanged,
                                      operational_failures=recompute_operational_failures(report))
    except ReportFailure:
        _fail("aggregate_mismatch")
    if recomputed != report.aggregate:
        _fail("aggregate_mismatch")


@dataclass(frozen=True)
class LoadedReport:
    report: LiveEvaluationReport
    sha256: str  # evidence identity of the exact file bytes; never a ranking signal
    file_name: str  # the safe basename only, never a directory


def load_report(path: Path | str) -> LoadedReport:
    """Strictly load one report file. Raises ReportLoadError with a closed category; never echoes content."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        _fail("unreadable")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _fail("not_utf8")
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except ReportLoadError:
        raise
    except ValueError:  # json.JSONDecodeError and other malformed input
        _fail("invalid_json")
    except RecursionError:
        _fail("invalid_json")
    version = value.get("report_version") if type(value) is dict else None
    if version != REPORT_VERSION:  # v1 is retired: never migrated, upgraded, or compared
        _fail("unsupported_version" if type(version) is str else "schema")
    try:
        report = _report(value)
    except ReportFailure:
        _fail("invalid_value")
    if serialize(report) != data:  # the exact canonical spelling, byte for byte: nothing else is accepted
        _fail("noncanonical")
    verify_report(report)
    name = path.name if _FILENAME.fullmatch(path.name) else "unnamed-report.json"
    return LoadedReport(report, hashlib.sha256(data).hexdigest(), name)


# --- comparison --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonResult:
    decision: str
    reason: str
    preferred_model: str | None
    compared_models: tuple[str, ...]  # sorted
    comparability_mismatches: tuple[str, ...]  # sorted, unique, closed codes

    def __post_init__(self) -> None:
        if (self.decision not in DECISIONS or self.reason not in REASONS
                or (self.decision == "prefer") != (self.preferred_model is not None)
                or self.preferred_model not in (None, *self.compared_models)
                or list(self.compared_models) != sorted(self.compared_models)
                or list(self.comparability_mismatches) != sorted(set(self.comparability_mismatches))
                or any(m not in MISMATCHES for m in self.comparability_mismatches)
                or bool(self.comparability_mismatches) != (self.reason == "not_comparable")):
            raise ValueError("inconsistent comparison result")


def comparability_mismatches(a: LiveEvaluationReport, b: LiveEvaluationReport) -> tuple[str, ...]:
    found = set()
    if a.report_version != b.report_version:
        found.add("report_version")
    if a.source.commit != b.source.commit:
        found.add("source_commit")
    if not (a.source.working_tree_clean and b.source.working_tree_clean):
        found.add("source_working_tree_dirty")
    if a.corpus.version != b.corpus.version:
        found.add("corpus_version")
    if a.corpus.sha256 != b.corpus.sha256:
        found.add("corpus_sha256")
    if a.corpus.case_count != b.corpus.case_count:
        found.add("corpus_case_count")
    if {c.case_id for c in a.cases} != {c.case_id for c in b.cases}:
        found.add("case_set")
    versions_a, versions_b = dict(a.versions), dict(b.versions)
    found |= {f"version_{key}" for key in VERSIONS if versions_a.get(key) != versions_b.get(key)}
    if a.timeout_seconds != b.timeout_seconds:
        found.add("transport_timeout_seconds")
    if a.max_retries != b.max_retries:
        found.add("transport_max_retries")
    if a.requested_model == b.requested_model:
        found.add("same_requested_model")
    return tuple(sorted(found))


def _report_of(value: LoadedReport | LiveEvaluationReport) -> LiveEvaluationReport:
    report = value.report if type(value) is LoadedReport else value
    verify_report(report)  # each report must pass strict validation on its own, whatever its origin
    return report


def compare_reports(a: LoadedReport | LiveEvaluationReport, b: LoadedReport | LiveEvaluationReport) -> ComparisonResult:
    """The locked comparison of two strictly verified reports. Argument-order invariant."""
    first, second = sorted((_report_of(a), _report_of(b)), key=lambda r: r.requested_model)
    models = tuple(sorted({first.requested_model, second.requested_model}))
    mismatches = comparability_mismatches(first, second)
    if mismatches:
        return ComparisonResult("defer", "not_comparable", None, models, mismatches)
    if not (first.aggregate.operationally_valid and second.aggregate.operationally_valid):
        return ComparisonResult("defer", "operationally_inconclusive", None, models, ())
    eligible = [r for r in (first, second) if not r.aggregate.critical_failures]
    if not eligible:
        return ComparisonResult("defer", "none_eligible", None, models, ())
    if len(eligible) == 1:
        return ComparisonResult("prefer", "only_eligible_model", eligible[0].requested_model, models, ())
    for field, reason in (("hard_passed", "more_hard_checks_passed"), ("successful_artifacts", "more_successful_artifacts")):
        value_first, value_second = getattr(first.aggregate, field), getattr(second.aggregate, field)
        if value_first != value_second:
            winner = first if value_first > value_second else second
            return ComparisonResult("prefer", reason, winner.requested_model, models, ())
    return ComparisonResult("defer", "behaviorally_tied", None, models, ())


def comparison_line(result: ComparisonResult) -> str:
    """A sanitized console line derived only from the closed codes and model IDs."""
    return (f"COMPARISON decision={result.decision} reason={result.reason} "
            f"preferred={result.preferred_model or '-'} models={','.join(result.compared_models)} "
            f"mismatches={','.join(result.comparability_mismatches) or '-'}")
