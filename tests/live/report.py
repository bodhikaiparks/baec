"""Phase 6D-C1: the sanitized live evaluation report, its directory gate, and the live lifecycle.

Evaluation rules (docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §7-§9). This is the live-evaluation layer
only: production code gains no Git or filesystem dependency, and nothing here imports the production
validator, the grounding module, or the SDK.

baec-live-evaluation-report/v2 is built from the immutable outcomes and audit that run_evaluation projected
from the persisted records while the temporary database existed. The builder therefore never needs the
database, and the report survives its deletion. Every string in a report is checked against an allowlist:
an identifier, a version label, a closed code, an exception class name, or a sanitized model ID. Source text,
model output, excerpts, prompts, digests of runs, paths, exception messages, and credentials cannot be
represented. Aggregates are derived from the per-case structures, never maintained in parallel.

Lifecycle (run_live): live gate, report-directory gate, a clean source tree, the explicitly selected
corpus and its SHA-256, serial cases
and per-case provenance, authority comparison, persisted audit, database closed and deleted, report built
from the immutable evidence, written exclusively and atomically, verified, and only its file name and
SHA-256 printed. A report failure is an operational failure of the run; it never preserves the database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from baec_app.ai.canonical import CANONICALIZATION_VERSION, canonical_json
from baec_app.ai.contracts import INPUT_VERSION, OUTPUT_SCHEMA_VERSION, REQUEST_SPEC_VERSION, TASK_VERSION
from baec_app.ai.prompts import PROMPT_VERSION
from baec_app.data.ai_provenance import AiRemoteOutcome, AiRunStatus
from baec_app.data.database import AI_PARSE_FAILURE_CODES, AI_SEMANTIC_FAILURE_CODES
from tests.live.harness import (
    CASE_STATUSES,
    CHECK_CODES,
    CHECK_KINDS,
    CORPUS_PATHS,
    CRITICAL_CLASSES,
    OPERATIONALLY_INVALID_STATUSES,
    TERMINAL_FAILURE,
    UNRECOGNIZED_MODEL_ID,
    CaseAudit,
    CaseOutcome,
    EvaluationReport,
    LiveGateClosed,
    RunAudit,
    live_gate,
    run_evaluation,
    validate_corpus,
)

REPORT_VERSION = "baec-live-evaluation-report/v2"
# Superseded before any authorized live use (design §8, Phase 6D-C2 clarification): v1 did not record the
# service-returned status, so its audit could not be recomputed. Never written, never upgraded, never compared.
RETIRED_REPORT_VERSIONS = ("baec-live-evaluation-report/v1",)
REPORT_DIR_VARIABLE = "BAEC_LIVE_REPORT_DIR"
CORPUS_VARIABLE = "BAEC_LIVE_CORPUS"  # the explicit corpus version of a live run; there is no default
REPO_ROOT = Path(__file__).resolve().parents[2]
# Duplicated from production with equality tests, so this layer never imports the validator or the SDK.
VALIDATION_VERSION = "baec-extraction-validation/v2"
TIMEOUT_SECONDS = 180
MAX_RETRIES = 0
VERSIONS = {
    "task": TASK_VERSION, "prompt": PROMPT_VERSION, "input": INPUT_VERSION, "output_schema": OUTPUT_SCHEMA_VERSION,
    "request_spec": REQUEST_SPEC_VERSION, "canonicalization": CANONICALIZATION_VERSION, "validation": VALIDATION_VERSION,
}
# What each recorded run must carry, as read back by the persisted audit.
_AUDITED_VERSIONS = {"request_spec": "request_spec_version", "prompt": "prompt_version", "input": "input_version",
                     "output_schema": "output_schema_version", "canonicalization": "canonicalization_version",
                     "validation": "validation_version"}

FAILURE_CODES = frozenset(AI_PARSE_FAILURE_CODES) | frozenset(AI_SEMANTIC_FAILURE_CODES)
OPERATIONAL_FAILURES = frozenset(OPERATIONALLY_INVALID_STATUSES) | {
    "provenance_failure", "authority_mutation", "audit_failure", "cleanup_failure", "report_failure"}
REPORT_CRITICAL_CLASSES = frozenset(CRITICAL_CLASSES) | {TERMINAL_FAILURE}
REMOTE_OUTCOMES = frozenset(o.value for o in AiRemoteOutcome)
# Terminal statuses the extraction service itself can return (interrupted is an operator action in the data
# layer only). Pinned to the AI layer's RunStatus by an equality test.
SERVICE_TERMINAL_STATUSES = tuple(s.value for s in AiRunStatus if s is not AiRunStatus.INTERRUPTED)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_CASE_ID = re.compile(r"C\d{2}")
_CHECK_ID = re.compile(r"CORE-[A-Z]+(?:-[A-Z]+)*|C\d{2}-[A-Z0-9]+(?:-[A-Z0-9]+)*")
_VERSION_LABEL = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*/v\d+")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
_GENERATED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.json")


class ReportFailure(Exception):
    """The sanitized report could not be built, serialized, written, or verified. Messages are generic."""


def _require(condition: bool) -> None:
    if not condition:
        raise ReportFailure("the report contains a value outside its allowlist or is inconsistent")


def _matches(pattern: re.Pattern, value: object) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _count(value: object, *, optional: bool = False) -> bool:
    if value is None:
        return optional
    return type(value) is int and value >= 0


def _model(value: object) -> bool:
    return value == UNRECOGNIZED_MODEL_ID or _matches(_MODEL_ID, value)


def _codes(value: object) -> bool:
    return type(value) is tuple and all(c in FAILURE_CODES for c in value) and list(value) == sorted(set(value))


# --- report structures --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceMetadata:
    commit: str
    working_tree_clean: bool

    def __post_init__(self) -> None:
        _require(_matches(_COMMIT, self.commit) and type(self.working_tree_clean) is bool)


@dataclass(frozen=True)
class CorpusMetadata:
    version: str
    case_count: int
    sha256: str

    def __post_init__(self) -> None:
        _require(_matches(_VERSION_LABEL, self.version) and _count(self.case_count) and _matches(_SHA256, self.sha256))


@dataclass(frozen=True)
class ReportCheck:
    check_id: str
    kind: str
    passed: bool
    code: str
    critical_class: str | None

    def __post_init__(self) -> None:
        _require(_matches(_CHECK_ID, self.check_id) and self.kind in CHECK_KINDS and type(self.passed) is bool
                 and self.code in CHECK_CODES
                 and (self.critical_class is None or self.critical_class in REPORT_CRITICAL_CLASSES))


@dataclass(frozen=True)
class ReportCase:
    case_id: str
    ai_run_id: str | None
    terminal_status: str  # read back from the persisted AI provenance
    service_terminal_status: str | None  # what the service returned in memory; None if it returned no terminal result
    artifact_present: bool
    failure_codes: tuple[str, ...]
    error_class: str | None
    elapsed_ms: int
    input_tokens: int | None
    output_tokens: int | None
    checks: tuple[ReportCheck, ...]

    def __post_init__(self) -> None:
        _require(_matches(_CASE_ID, self.case_id)
                 and (self.ai_run_id is None or _matches(_IDENTIFIER, self.ai_run_id))
                 and self.terminal_status in CASE_STATUSES and type(self.artifact_present) is bool
                 and (self.service_terminal_status is None
                      or self.service_terminal_status in SERVICE_TERMINAL_STATUSES)
                 and _codes(self.failure_codes)
                 and (self.error_class is None or _matches(_CLASS_NAME, self.error_class))
                 and _count(self.elapsed_ms) and _count(self.input_tokens, optional=True)
                 and _count(self.output_tokens, optional=True)
                 and type(self.checks) is tuple and all(type(c) is ReportCheck for c in self.checks))
        # the persisted failure-code cardinality (Phase 6D design §5.1)
        if self.terminal_status == "parse_failure":
            _require(len(self.failure_codes) == 1)
        elif self.terminal_status == "semantic_validation_failure":
            _require(len(self.failure_codes) >= 1)
        else:
            _require(self.failure_codes == ())

    @property
    def hard(self) -> tuple[ReportCheck, ...]:
        return tuple(c for c in self.checks if c.kind == "hard")

    @property
    def observational(self) -> tuple[ReportCheck, ...]:
        return tuple(c for c in self.checks if c.kind == "observational")


@dataclass(frozen=True)
class ReportAggregate:
    calls_attempted: int
    terminal_status_counts: tuple[tuple[str, int], ...]
    successful_artifacts: int
    hard_passed: int
    hard_total: int
    critical_failures: tuple[tuple[str, tuple[str, ...]], ...]  # behavioral class -> its case IDs (one or more)
    observational_passed: int
    observational_total: int
    operational_failures: tuple[str, ...]
    operationally_valid: bool
    total_input_tokens: int
    total_output_tokens: int
    total_elapsed_ms: int

    def __post_init__(self) -> None:
        counts = (self.calls_attempted, self.successful_artifacts, self.hard_passed, self.hard_total,
                  self.observational_passed, self.observational_total, self.total_input_tokens,
                  self.total_output_tokens, self.total_elapsed_ms)
        _require(all(_count(value) for value in counts))
        _require(all(s in CASE_STATUSES and _count(n) for s, n in self.terminal_status_counts))
        # behavioral case evidence only: every class names at least one case; operational causes never appear here
        _require(all(c in REPORT_CRITICAL_CLASSES and type(ids) is tuple and len(ids) >= 1
                     and all(_matches(_CASE_ID, i) for i in ids) for c, ids in self.critical_failures))
        _require(all(f in OPERATIONAL_FAILURES for f in self.operational_failures)
                 and self.operationally_valid == (not self.operational_failures))


@dataclass(frozen=True)
class LiveEvaluationReport:
    report_version: str
    generated_at: str
    source: SourceMetadata
    corpus: CorpusMetadata
    versions: tuple[tuple[str, str], ...]
    timeout_seconds: int
    max_retries: int
    requested_model: str
    returned_model_ids: tuple[str, ...]
    cases: tuple[ReportCase, ...]
    audit: RunAudit
    authority_unchanged: bool
    aggregate: ReportAggregate

    def __post_init__(self) -> None:
        _require(self.report_version == REPORT_VERSION and _matches(_GENERATED_AT, self.generated_at)
                 and type(self.source) is SourceMetadata and type(self.corpus) is CorpusMetadata
                 and tuple(k for k, _ in self.versions) == tuple(VERSIONS)
                 and all(_matches(_VERSION_LABEL, v) for _, v in self.versions)
                 and _count(self.timeout_seconds) and _count(self.max_retries) and _model(self.requested_model)
                 and all(_model(m) for m in self.returned_model_ids)
                 and type(self.cases) is tuple and all(type(c) is ReportCase for c in self.cases)
                 and type(self.authority_unchanged) is bool and type(self.aggregate) is ReportAggregate)
        _validate_audit(self.audit)


def _validate_audit(audit: object) -> None:
    _require(type(audit) is RunAudit)
    counts = (audit.cases_expected, audit.cases_attempted, audit.runs_present, audit.terminal_results_present,
              audit.requested_model_matches, audit.provenance_verified_cases)
    _require(all(_count(value) for value in counts) and all(_model(m) for m in audit.returned_model_ids_seen)
             and type(audit.authoritative_tables_unchanged) is bool and type(audit.temporary_database_cleaned) is bool
             and (audit.failure is None or _matches(_CLASS_NAME, audit.failure)))
    for case in audit.cases:
        _require(type(case) is CaseAudit and _matches(_CASE_ID, case.case_id)
                 and (case.ai_run_id is None or _matches(_IDENTIFIER, case.ai_run_id))
                 and all(m is None or _model(m) for m in (case.requested_model, case.returned_model))
                 and (case.terminal_status is None or case.terminal_status in CASE_STATUSES)
                 and (case.remote_outcome is None or case.remote_outcome in REMOTE_OUTCOMES)
                 and all(getattr(case, field) is None or _matches(_VERSION_LABEL, getattr(case, field))
                         for field in _AUDITED_VERSIONS.values())
                 and _count(case.excerpt_count) and _codes(case.failure_codes))


# --- building ----------------------------------------------------------------------------------------


def _report_case(outcome: CaseOutcome) -> ReportCase:
    return ReportCase(
        case_id=outcome.case_id,
        ai_run_id=None if outcome.ai_run_id == "-" else outcome.ai_run_id,
        terminal_status=outcome.status,
        service_terminal_status=outcome.reported_status,  # captured in memory before the database was deleted
        artifact_present=outcome.artifact_present,
        failure_codes=outcome.failure_codes,  # the persisted codes, read back in _evaluate_case
        error_class=outcome.error,
        elapsed_ms=round(outcome.elapsed_seconds * 1000),
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        checks=tuple(ReportCheck(c.check_id, c.kind, c.passed, c.code, c.critical) for c in outcome.checks),
    )


def derive_aggregate(cases: tuple[ReportCase, ...], *, authority_unchanged: bool,
                     operational_failures: tuple[str, ...]) -> ReportAggregate:
    """Every aggregate from the per-case structures. Tokens, time, and observations are descriptive only.

    critical_failures holds behavioral, case-level classes only. Operational causes (authority mutation,
    audit, cleanup, or report failure, and the operationally invalid statuses) are in operational_failures.
    """
    statuses: dict[str, int] = {}
    critical: dict[str, list[str]] = {}
    for case in cases:
        statuses[case.terminal_status] = statuses.get(case.terminal_status, 0) + 1
        for check in case.hard:
            if not check.passed and check.critical_class:
                critical.setdefault(check.critical_class, []).append(case.case_id)
    return ReportAggregate(
        calls_attempted=len(cases),
        terminal_status_counts=tuple(sorted(statuses.items())),
        successful_artifacts=sum(c.artifact_present for c in cases),
        hard_passed=sum(k.passed for c in cases for k in c.hard),
        hard_total=sum(len(c.hard) for c in cases),
        critical_failures=tuple((name, tuple(sorted(set(ids)))) for name, ids in sorted(critical.items())),
        observational_passed=sum(k.passed for c in cases for k in c.observational),
        observational_total=sum(len(c.observational) for c in cases),
        operational_failures=tuple(sorted(operational_failures)),
        operationally_valid=not operational_failures,
        total_input_tokens=sum(c.input_tokens or 0 for c in cases),
        total_output_tokens=sum(c.output_tokens or 0 for c in cases),
        total_elapsed_ms=sum(c.elapsed_ms for c in cases),
    )


def _utc_timestamp(moment: datetime) -> str:
    _require(type(moment) is datetime and moment.utcoffset() == timezone.utc.utcoffset(None))
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report(evaluation: EvaluationReport, *, source: SourceMetadata, corpus_sha256: str,
                 generated_at: datetime) -> LiveEvaluationReport:
    """The sanitized report of one evaluation. Needs no database: only the immutable outcomes and audit."""
    _require(type(evaluation) is EvaluationReport and evaluation.audit is not None)
    audit = evaluation.audit
    for case in audit.cases:  # every recorded run carries exactly the versions this report states
        for key, field in _AUDITED_VERSIONS.items():
            value = getattr(case, field)
            _require(value is None or value == VERSIONS[key])
    cases = tuple(_report_case(o) for o in evaluation.outcomes)
    aggregate = derive_aggregate(cases, authority_unchanged=evaluation.authority_unchanged,
                                 operational_failures=evaluation.operational_failures)
    # the derived aggregates must agree with the evaluation they summarize; any disagreement fails closed
    _require((aggregate.calls_attempted, aggregate.successful_artifacts, aggregate.hard_passed, aggregate.hard_total,
              aggregate.observational_passed, aggregate.observational_total, aggregate.total_input_tokens,
              aggregate.total_output_tokens)
             == (evaluation.calls_attempted, evaluation.successful_artifacts, evaluation.hard_passed,
                 evaluation.hard_total, evaluation.observational_passed, evaluation.observational_total,
                 evaluation.total_input_tokens, evaluation.total_output_tokens))
    behavioral = tuple(sorted({c for o in evaluation.outcomes for c in o.critical_failures}))
    _require(tuple(name for name, _ in aggregate.critical_failures) == behavioral)
    return LiveEvaluationReport(
        report_version=REPORT_VERSION,
        generated_at=_utc_timestamp(generated_at),
        source=source,
        corpus=CorpusMetadata(evaluation.corpus_version, audit.cases_expected, corpus_sha256),
        versions=tuple(VERSIONS.items()),
        timeout_seconds=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
        requested_model=evaluation.model,
        returned_model_ids=audit.returned_model_ids_seen,
        cases=cases,
        audit=audit,
        authority_unchanged=evaluation.authority_unchanged,
        aggregate=aggregate,
    )


# --- serialization -----------------------------------------------------------------------------------


def _audit_object(audit: RunAudit) -> dict:
    return {
        "cases_expected": audit.cases_expected, "cases_attempted": audit.cases_attempted,
        "runs_present": audit.runs_present, "terminal_results_present": audit.terminal_results_present,
        "requested_model_matches": audit.requested_model_matches,
        "returned_model_ids_seen": list(audit.returned_model_ids_seen),
        "provenance_verified_cases": audit.provenance_verified_cases,
        "authoritative_tables_unchanged": audit.authoritative_tables_unchanged,
        "temporary_database_cleaned": audit.temporary_database_cleaned, "failure": audit.failure,
        "cases": [{
            "case_id": c.case_id, "ai_run_id": c.ai_run_id, "run_present": c.run_present,
            "terminal_result_present": c.terminal_result_present, "requested_model": c.requested_model,
            "returned_model": c.returned_model, "terminal_status": c.terminal_status,
            "remote_outcome": c.remote_outcome, "request_spec_version": c.request_spec_version,
            "prompt_version": c.prompt_version, "input_version": c.input_version,
            "output_schema_version": c.output_schema_version, "canonicalization_version": c.canonicalization_version,
            "validation_version": c.validation_version, "request_digest_present": c.request_digest_present,
            "prompt_digest_present": c.prompt_digest_present, "input_digest_present": c.input_digest_present,
            "output_schema_digest_present": c.output_schema_digest_present, "output_present": c.output_present,
            "artifact_present": c.artifact_present, "excerpt_count": c.excerpt_count,
            "failure_codes": list(c.failure_codes), "provenance_verified": c.provenance_verified,
        } for c in audit.cases],
    }


def to_json_object(report: LiveEvaluationReport) -> dict:
    aggregate = report.aggregate
    return {
        "report_version": report.report_version,
        "generated_at": report.generated_at,
        "source": {"commit": report.source.commit, "working_tree_clean": report.source.working_tree_clean},
        "corpus": {"version": report.corpus.version, "case_count": report.corpus.case_count,
                   "sha256": report.corpus.sha256},
        "versions": dict(report.versions),
        "transport": {"timeout_seconds": report.timeout_seconds, "max_retries": report.max_retries},
        "requested_model": report.requested_model,
        "returned_model_ids": list(report.returned_model_ids),
        "cases": [{
            "case_id": c.case_id, "ai_run_id": c.ai_run_id, "terminal_status": c.terminal_status,
            "service_terminal_status": c.service_terminal_status, "artifact_present": c.artifact_present,
            "failure_codes": list(c.failure_codes),
            "error_class": c.error_class, "elapsed_ms": c.elapsed_ms, "input_tokens": c.input_tokens,
            "output_tokens": c.output_tokens,
            "checks": [{"check_id": k.check_id, "kind": k.kind, "passed": k.passed, "code": k.code,
                        "critical_class": k.critical_class} for k in c.checks],
        } for c in report.cases],
        "audit": _audit_object(report.audit),
        "authority_unchanged": report.authority_unchanged,
        "aggregate": {
            "calls_attempted": aggregate.calls_attempted,
            "terminal_status_counts": dict(aggregate.terminal_status_counts),
            "successful_artifacts": aggregate.successful_artifacts,
            "hard_passed": aggregate.hard_passed, "hard_total": aggregate.hard_total,
            "critical_failures": {name: list(ids) for name, ids in aggregate.critical_failures},
            "observational_passed": aggregate.observational_passed,
            "observational_total": aggregate.observational_total,
            "operational_failures": list(aggregate.operational_failures),
            "operationally_valid": aggregate.operationally_valid,
            "total_input_tokens": aggregate.total_input_tokens,
            "total_output_tokens": aggregate.total_output_tokens,
            "total_elapsed_ms": aggregate.total_elapsed_ms,
        },
    }


def serialize(report: LiveEvaluationReport) -> bytes:
    """The canonical (baec-canonical-json/v1) UTF-8 bytes of the report."""
    _require(type(report) is LiveEvaluationReport)
    return canonical_json(to_json_object(report)).encode("utf-8")


# --- writing ---------------------------------------------------------------------------------------


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]", "-", value)


def report_filename(report: LiveEvaluationReport) -> str:
    """Derived only from sanitized metadata: corpus version, requested model, commit prefix, generated_at."""
    stamp = report.generated_at.replace("-", "").replace(":", "")
    name = f"{_slug(report.corpus.version)}__{_slug(report.requested_model)}__{report.source.commit[:12]}__{stamp}.json"
    _require(_matches(_FILENAME, name))
    return name


def write_report(report: LiveEvaluationReport, directory: Path) -> tuple[str, str]:
    """Write the report exclusively and atomically; return (file name, SHA-256 of the final bytes).

    The bytes go to a temporary sibling file, are flushed and fsynced, and are then published with a hard
    link, which is atomic and refuses an existing name: an evidence report is never overwritten. The
    temporary file is always removed. The final file is read back and must equal the bytes written.
    """
    data = serialize(report)
    name = report_filename(report)
    final = Path(directory) / name
    try:
        descriptor, temporary_name = tempfile.mkstemp(dir=directory, prefix=".baec-report-", suffix=".tmp")
    except OSError:
        raise ReportFailure("the report could not be written") from None
    temporary = Path(temporary_name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, final)  # atomic publication; FileExistsError if a report of that name exists
        published = True
        if final.read_bytes() != data:
            raise ReportFailure("the published report does not match its bytes")
    except BaseException as error:
        if published:
            final.unlink(missing_ok=True)  # never leave an unverified report behind
        if isinstance(error, ReportFailure) or not isinstance(error, Exception):
            raise
        raise ReportFailure("the report could not be written") from None
    finally:
        temporary.unlink(missing_ok=True)
    return name, hashlib.sha256(data).hexdigest()


# --- the live gate for the report directory and the source metadata -----------------------------------


def run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    """Git, for the live layer only. Output is parsed for fixed facts and never printed."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=False)


Git = Callable[..., subprocess.CompletedProcess]


def report_destination(environ: Mapping[str, str], *, repo_root: Path = REPO_ROOT, git: Git = run_git) -> Path:
    """The configured report directory, or LiveGateClosed. Checked before any provider attempt.

    It must exist, be a directory, and be writable (a probe file is created and removed). Inside the
    repository it must already be git-ignored, so a report can never dirty the tree. Nothing is created,
    and .gitignore is never changed. Messages are generic: they never contain the path.
    """
    raw = environ.get(REPORT_DIR_VARIABLE)
    if not raw:
        raise LiveGateClosed(f"no report directory: set {REPORT_DIR_VARIABLE} to an existing writable directory")
    directory = Path(raw).expanduser()
    if not directory.is_absolute() or not directory.is_dir():
        raise LiveGateClosed(f"{REPORT_DIR_VARIABLE} must name an existing directory by absolute path")
    directory = directory.resolve()
    root = Path(repo_root).resolve()
    if directory == root or root in directory.parents:
        try:
            ignored = git(["check-ignore", "-q", str(directory / "baec-live-evaluation-report.json")], cwd=root)
        except (OSError, subprocess.SubprocessError):
            raise LiveGateClosed("git is unavailable, so an in-repository report directory cannot be checked") from None
        if ignored.returncode != 0:
            raise LiveGateClosed(f"{REPORT_DIR_VARIABLE} is inside the repository and is not git-ignored")
    try:
        descriptor, probe = tempfile.mkstemp(dir=directory, prefix=".baec-report-probe-")
        os.close(descriptor)
        os.unlink(probe)
    except OSError:
        raise LiveGateClosed(f"{REPORT_DIR_VARIABLE} is not writable") from None
    return directory


def source_metadata(*, repo_root: Path = REPO_ROOT, git: Git = run_git) -> SourceMetadata:
    """The 40-hex commit and the clean-tree flag, resolved from Git, or LiveGateClosed."""
    try:
        head = git(["rev-parse", "HEAD"], cwd=Path(repo_root))
        status = git(["status", "--porcelain"], cwd=Path(repo_root))
    except (OSError, subprocess.SubprocessError):
        raise LiveGateClosed("git is unavailable, so the source commit cannot be determined") from None
    commit = head.stdout.strip()
    if head.returncode != 0 or status.returncode != 0 or not _COMMIT.fullmatch(commit):
        raise LiveGateClosed("the source commit and working-tree state cannot be determined")
    return SourceMetadata(commit, status.stdout.strip() == "")


def live_corpus_version(environ: Mapping[str, str]) -> str:
    """The corpus version named explicitly by BAEC_LIVE_CORPUS, or LiveGateClosed. Never inferred or "latest"."""
    version = environ.get(CORPUS_VARIABLE)
    if version not in CORPUS_PATHS:
        raise LiveGateClosed(f"no corpus selected: set {CORPUS_VARIABLE} to an exact corpus version")
    return version


# --- the live lifecycle ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveRun:
    evaluation: EvaluationReport  # report_failed marks a report failure, which makes the run operationally invalid
    report_file: str | None
    report_sha256: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_live(environ: Mapping[str, str], *, provider=None, emit: Callable[[str], None] = print, clock=None,
             now: Callable[[], datetime] = _now, corpus_path: Path | None = None, repo_root: Path = REPO_ROOT,
             git: Git = run_git) -> LiveRun:
    """The whole live run. Every gate, including a clean source tree, is checked before any database,
    runtime, or provider exists.

    provider=None uses the production default; offline tests inject a fake.
    """
    model = live_gate(environ)
    directory = report_destination(environ, repo_root=repo_root, git=git)
    source = source_metadata(repo_root=repo_root, git=git)
    if not source.working_tree_clean:  # a dirty-tree report would fail the comparability precondition (6D-C2)
        raise LiveGateClosed("the source working tree is not clean: commit or remove changes before a live run")
    version = live_corpus_version(environ)
    data = Path(CORPUS_PATHS[version] if corpus_path is None else corpus_path).read_bytes()
    corpus = validate_corpus(json.loads(data))
    if corpus.corpus_version != version:
        raise LiveGateClosed(f"the corpus does not declare the version selected by {CORPUS_VARIABLE}")
    corpus_sha256 = hashlib.sha256(data).hexdigest()  # of the exact bytes this run used
    evaluation = run_evaluation(corpus, model=model, provider=provider, emit=emit, clock=clock)  # DB deleted
    try:
        report = build_report(evaluation, source=source, corpus_sha256=corpus_sha256, generated_at=now())
        name, digest = write_report(report, directory)
    except Exception as error:  # noqa: BLE001 - an operational failure, recorded by class name only
        failed = replace(evaluation, report_failed=True)
        emit(f"REPORT-FAILURE class={type(error).__name__} operationally_valid={failed.operationally_valid} "
             f"operational_failures={','.join(failed.operational_failures)}")
        return LiveRun(failed, None, None)
    emit(f"report_file={name}")
    emit(f"report_sha256={digest}")
    return LiveRun(evaluation, name, digest)
