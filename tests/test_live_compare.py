"""Phase 6D-C2: strict report loading and deterministic report comparison, offline only.

docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §10. Every report here is synthetic: built offline from the
fake provider, or a sanitized fixture encoding documented aggregates. Nothing is a persisted Phase 6C report
and no model is run. Evaluation rules: software tests, not evidence about models or BAEC theory.
"""

import dataclasses
import functools
import hashlib
import json
from pathlib import Path

import pytest

from baec_app.ai.canonical import canonical_json
from baec_app.ai.provenance import RunStatus
from baec_app.ai.service import ExtractionService
from tests.live.compare import (
    DECISIONS,
    comparability_mismatches,
    LOAD_ERRORS,
    MISMATCHES,
    REASONS,
    ComparisonResult,
    LoadedReport,
    ReportLoadError,
    compare_reports,
    comparison_line,
    load_report,
    recompute_operational_failures,
    verify_report,
)
from baec_app.ai.provider import ProviderApiError, ProviderTransportError
from tests.ai_builders import response
from tests.live import harness
from tests.live.harness import load_corpus, run_evaluation
from tests.live.report import (
    REPORT_VERSION,
    RETIRED_REPORT_VERSIONS,
    SERVICE_TERMINAL_STATUSES,
    ReportFailure,
    build_report,
    derive_aggregate,
    serialize,
    write_report,
)
from tests.test_live_harness import OPUS, SONNET, scripted
from tests.test_live_report import NOT_JSON, NOW, SOURCE, V1_SHA256, BEHAVIORAL, v2_corpus

# --- builders -----------------------------------------------------------------------------------------


def _evaluate(model, overrides=None, corpus=None):
    return run_evaluation(corpus or load_corpus(), model=model, provider=scripted(model, overrides), emit=[].append)


@functools.lru_cache(maxsize=None)
def good(model=SONNET):
    """A well-behaved, operationally valid report for one model (14 artifacts, 87/87, no critical failure)."""
    return build_report(_evaluate(model), source=SOURCE, corpus_sha256=V1_SHA256, generated_at=NOW)


def rebuild(report, cases=None, *, authority_unchanged=None, audit_changes=None, **changes):
    """A consistent report after a synthetic edit: the audit follows the cases and the aggregate is derived."""
    cases = report.cases if cases is None else tuple(cases)
    authority = report.authority_unchanged if authority_unchanged is None else authority_unchanged
    audit_cases = tuple(dataclasses.replace(a, case_id=c.case_id, failure_codes=c.failure_codes,
                                            terminal_status=c.terminal_status, artifact_present=c.artifact_present)
                        for a, c in zip(report.audit.cases, cases))
    audit = dataclasses.replace(report.audit, cases=audit_cases, authoritative_tables_unchanged=authority,
                                **(audit_changes or {}))
    draft = dataclasses.replace(report, cases=cases, audit=audit, authority_unchanged=authority, **changes)
    aggregate = derive_aggregate(cases, authority_unchanged=authority,
                                 operational_failures=recompute_operational_failures(draft))
    return dataclasses.replace(draft, aggregate=aggregate)


def edit(report, case_id, checks=None, **fields):
    """Cases with one case changed; checks maps check_id -> (passed, code).

    A changed terminal_status also sets the same service_terminal_status unless one is given: a consistent
    synthetic run. A service/persisted disagreement is only ever created on purpose.
    """
    if "terminal_status" in fields:
        fields.setdefault("service_terminal_status", fields["terminal_status"])
    def change(case):
        if case.case_id != case_id:
            return case
        new_checks = tuple(dataclasses.replace(k, passed=checks[k.check_id][0], code=checks[k.check_id][1])
                           if checks and k.check_id in checks else k for k in case.checks)
        return dataclasses.replace(case, checks=new_checks, **fields)
    return tuple(change(c) for c in report.cases)


def with_critical(report, case_id="C09"):
    """CORE-NUMBERS fails on one success case: a behavioral threshold_corruption critical failure."""
    return rebuild(report, edit(report, case_id, {"CORE-NUMBERS": (False, "number_not_in_source")}))


def with_noncritical_hard_failure(report):
    return rebuild(report, edit(report, "C03", {"C03-H2": (False, "analysis_status_forbidden")}))


def with_fewer_artifacts(report):
    """One success becomes a refusal: one fewer artifact, no hard check changes (C04 has no artifact check)."""
    return rebuild(report, edit(report, "C04", terminal_status="refusal", artifact_present=False))


def operationally_invalid(report):
    return rebuild(report, authority_unchanged=False)


def written(report, directory: Path) -> Path:
    name, _ = write_report(report, directory)
    return directory / name


# --- the sanitized Phase 6C fixture ---------------------------------------------------------------------

SEMANTIC_CASES = ("C01", "C02", "C03", "C04", "C05", "C07", "C08", "C12", "C14")
NO_ARTIFACT_CHECKS = {"C01": ("C01-H2", "C01-H3", "C01-H4", "C01-H5", "C01-H6"), "C02": ("C02-H2", "C02-H3"),
                      "C08": ("C08-H2", "C08-H3")}


def _spread(total, count):
    return [total // count + (1 if i < total % count else 0) for i in range(count)]


def _with_totals(report, input_tokens, output_tokens, elapsed_ms):
    n = len(report.cases)
    cases = tuple(dataclasses.replace(c, input_tokens=i, output_tokens=o, elapsed_ms=e) for c, i, o, e in
                  zip(report.cases, _spread(input_tokens, n), _spread(output_tokens, n), _spread(elapsed_ms, n)))
    return rebuild(report, cases)


def phase6c_fixtures():
    """Synthetic, sanitized reports encoding the aggregates documented in docs/PHASE6C_LIVE_EVALUATION.md §9.2-§9.4.

    They are NOT the original Phase 6C reports (none were persisted) and reconstruct no model output. The
    failing cases follow the documented findings: nine semantic failures including C04, C12, and C14 (§9.5),
    six clear-case parse failures, and one threshold corruption at C09 (CORE-NUMBERS, §9.5 finding 1).
    Version labels are the current ones, because the fixtures exercise the current report-v1 loader.
    """
    sonnet = _with_totals(good(SONNET), 32147, 7838, 68630)
    opus = good(OPUS)
    cases = list(opus.cases)
    for index, case in enumerate(cases):
        if case.case_id in SEMANTIC_CASES:
            # the hard terminal_success check is H1 in every failing case except C04, which has none (§9.5 item 3)
            failed = {} if case.case_id == "C04" else {f"{case.case_id}-H1": (False, "status_semantic_validation_failure")}
            failed.update({check_id: (False, "no_artifact") for check_id in NO_ARTIFACT_CHECKS.get(case.case_id, ())})
            cases[index] = edit(opus, case.case_id, failed, terminal_status="semantic_validation_failure",
                                artifact_present=False, failure_codes=("criterion_set_invalid",))[index]
    opus = rebuild(opus, cases)
    opus = _with_totals(with_critical(opus, "C09"), 32147, 6013, 93530)
    return sonnet, opus


def test_the_sanitized_fixtures_match_the_documented_phase6c_aggregates():
    sonnet, opus = phase6c_fixtures()
    for report in (sonnet, opus):
        verify_report(report)
        assert report.aggregate.operationally_valid and report.aggregate.calls_attempted == 14
        assert report.aggregate.hard_total == 87
    assert (sonnet.aggregate.successful_artifacts, sonnet.aggregate.hard_passed) == (14, 87)
    assert sonnet.aggregate.critical_failures == ()
    assert (sonnet.aggregate.total_input_tokens, sonnet.aggregate.total_output_tokens) == (32147, 7838)
    assert (opus.aggregate.successful_artifacts, opus.aggregate.hard_passed) == (5, 69)
    assert dict(opus.aggregate.terminal_status_counts) == {"success": 5, "semantic_validation_failure": 9}
    assert dict(opus.aggregate.critical_failures) == {
        "clear_case_parse_failure": ("C01", "C02", "C03", "C05", "C07", "C08"), "threshold_corruption": ("C09",)}
    assert (opus.aggregate.total_input_tokens, opus.aggregate.total_output_tokens) == (32147, 6013)


def test_the_phase6c_rule_is_reproduced_from_sanitized_report_files(tmp_path):
    sonnet, opus = (load_report(written(r, tmp_path)) for r in phase6c_fixtures())
    for result in (compare_reports(sonnet, opus), compare_reports(opus, sonnet)):
        assert result == ComparisonResult("prefer", "only_eligible_model", SONNET, (OPUS, SONNET), ())


# --- the loader ------------------------------------------------------------------------------------------


def test_a_written_report_loads_back_exactly(tmp_path):
    report = good()
    name, digest = write_report(report, tmp_path)
    loaded = load_report(tmp_path / name)
    assert loaded == LoadedReport(report, digest, name)
    assert loaded.sha256 == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()


SCENARIOS = {
    "parse failure": {"C01": NOT_JSON},
    "refusal": {"C02": BEHAVIORAL["refusal"]},
    "semantic failure": {"C01": BEHAVIORAL["semantic_validation_failure"]},
    "unexpected local error": {"C03": RuntimeError("boom")},
    "api error": {"C04": ProviderApiError("overloaded", "req_x")},
    "transport failure": {"C05": ProviderTransportError("timeout_or_disconnect", "unknown")},
    "model mismatch": {"C06": response("{}", model=OPUS)},
}


@pytest.mark.parametrize("overrides", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_every_written_c1_report_loads(tmp_path, overrides):
    report = build_report(_evaluate(SONNET, overrides), source=SOURCE, corpus_sha256=V1_SHA256, generated_at=NOW)
    assert load_report(written(report, tmp_path)).report == report


def test_an_authority_mutation_report_loads_and_is_operationally_invalid(tmp_path, monkeypatch):
    captured, real = {}, harness.open_extraction_runtime
    monkeypatch.setattr(harness, "open_extraction_runtime",
                        lambda path, **kw: captured.setdefault("path", path) and real(path, **kw))

    def breach(spec, content):
        if content["interaction_id"] == "INT-SYN-C05":
            writer = harness.connect(str(captured["path"]))
            try:
                writer.execute("UPDATE accounts SET name = 'changed' WHERE account_id = 'ACC-SYN-C05'")
            finally:
                writer.close()

    evaluation = run_evaluation(load_corpus(), model=SONNET, provider=scripted(SONNET, record=breach), emit=[].append)
    report = build_report(evaluation, source=SOURCE, corpus_sha256=V1_SHA256, generated_at=NOW)
    loaded = load_report(written(report, tmp_path)).report
    assert loaded == report and loaded.aggregate.operational_failures == ("authority_mutation",)
    assert compare_reports(loaded, good(OPUS)).reason == "operationally_inconclusive"


def test_a_v2_corpus_report_loads(tmp_path):
    report = build_report(_evaluate(SONNET, corpus=v2_corpus()), source=SOURCE, corpus_sha256=V1_SHA256,
                          generated_at=NOW)
    assert load_report(written(report, tmp_path)).report.corpus.version == "baec-extraction-live-corpus/v2"


def _loaded_json(tmp_path):
    path = written(good(), tmp_path)
    return path, json.loads(path.read_text())


def _write_json(path, data):
    path.write_bytes(canonical_json(data).encode("utf-8"))  # canonical spelling: only the semantic edit differs


def _expect(path, *categories):
    with pytest.raises(ReportLoadError) as raised:
        load_report(path)
    assert raised.value.category in categories and str(raised.value) == raised.value.category
    return raised.value


def test_duplicate_keys_are_rejected(tmp_path):
    path, _ = _loaded_json(tmp_path)
    text = path.read_text().replace('"authority_unchanged":true', '"authority_unchanged":true,"authority_unchanged":true')
    path.write_text(text)
    _expect(path, "duplicate_key")


@pytest.mark.parametrize("respell", ["indented", "reordered", "escaped", "spaced", "trailing newline", "float"])
def test_noncanonical_bytes_are_rejected(tmp_path, respell):
    path, data = _loaded_json(tmp_path)
    canonical = path.read_bytes()
    if respell == "indented":
        text = json.dumps(data, sort_keys=True, indent=2)
    elif respell == "reordered":
        text = json.dumps(dict(reversed(list(data.items()))), separators=(",", ":"))  # same content, reversed keys
    elif respell == "escaped":
        text = canonical.decode().replace('"report_version":"baec', '"report_version":"\\u0062aec')
    elif respell == "spaced":
        text = json.dumps(data, sort_keys=True)
    elif respell == "trailing newline":
        text = canonical.decode() + "\n"
    else:
        text = canonical.decode().replace('"max_retries":0', '"max_retries":0.0')
    assert text.encode() != canonical
    path.write_text(text)
    _expect(path, "noncanonical", "schema")


@pytest.mark.parametrize("where", ["top", "case", "check", "audit", "audit case", "aggregate", "source"])
def test_unknown_fields_are_rejected(tmp_path, where):
    path, data = _loaded_json(tmp_path)
    target = {"top": data, "case": data["cases"][0], "check": data["cases"][0]["checks"][0], "audit": data["audit"],
              "audit case": data["audit"]["cases"][0], "aggregate": data["aggregate"], "source": data["source"]}[where]
    target["note"] = "extra"
    _write_json(path, data)
    _expect(path, "schema")


@pytest.mark.parametrize("field", ["generated_at", "corpus", "aggregate", "checks"])
def test_missing_fields_are_rejected(tmp_path, field):
    path, data = _loaded_json(tmp_path)
    del (data["cases"][0] if field == "checks" else data)[field]
    _write_json(path, data)
    _expect(path, "schema")


@pytest.mark.parametrize("content", [b"", b"not json", b"\xff\xfe", b"[1, 2]", b'{"a": NaN}', b"[" * 5000])
def test_malformed_files_fail_with_a_bounded_category(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    error = _expect(path, "invalid_json", "not_utf8", "schema")
    assert str(tmp_path) not in str(error) and "bad.json" not in str(error)


def test_errors_and_results_never_contain_paths_or_content(tmp_path):
    error = _expect(tmp_path / "missing.json", "unreadable")
    assert str(tmp_path) not in repr(error)
    report = good()
    odd = tmp_path / "secret name with spaces.json"
    odd.write_bytes(serialize(report))
    loaded = load_report(odd)
    assert loaded.file_name == "unnamed-report.json" and str(tmp_path) not in repr(loaded)
    assert set(LOAD_ERRORS) >= {"unreadable", "noncanonical", "aggregate_mismatch"}


# --- tampering ---------------------------------------------------------------------------------------------


def _case(data, case_id):
    return next(c for c in data["cases"] if c["case_id"] == case_id)


def _check(case, check_id):
    return next(k for k in case["checks"] if k["check_id"] == check_id)


TAMPER = {
    "aggregate count": lambda d: d["aggregate"].update(hard_passed=d["aggregate"]["hard_passed"] - 1),
    "status": lambda d: _case(d, "C06").update(terminal_status="refusal"),
    "status and its count": lambda d: (_case(d, "C06").update(terminal_status="refusal", artifact_present=False),
                                       d["aggregate"].update(terminal_status_counts={"refusal": 1, "success": 13},
                                                             successful_artifacts=13)),
    "artifact flag": lambda d: _case(d, "C06").update(artifact_present=False),
    "hard check result": lambda d: _check(_case(d, "C01"), "C01-H2").update(passed=False, code="no_artifact"),
    "critical case ids": lambda d: d["aggregate"].update(critical_failures={"threshold_corruption": ["C01"]}),
    "authority flag": lambda d: d.update(authority_unchanged=False),
    "audit result": lambda d: d["audit"].update(provenance_verified_cases=13),
    "audit failure": lambda d: d["audit"].update(failure="RuntimeError"),
    "operational failure list": lambda d: d["aggregate"].update(operational_failures=["cleanup_failure"],
                                                                operationally_valid=False),
    "operationally valid flag": lambda d: d["aggregate"].update(operationally_valid=False),
    "token total": lambda d: d["aggregate"].update(total_input_tokens=1),
    "elapsed total": lambda d: d["aggregate"].update(total_elapsed_ms=1),
    "case token": lambda d: _case(d, "C01").update(input_tokens=-1),
    "corpus hash": lambda d: d["corpus"].update(sha256="NOT-A-HASH"),
    "source commit format": lambda d: d["source"].update(commit="0123456789ABCDEF0123456789ABCDEF01234567"),
    "validation version format": lambda d: d["versions"].update(validation="baec extraction validation v2"),
    "duplicate case": lambda d: d["cases"].append(dict(d["cases"][0])),
    "duplicate check id": lambda d: _case(d, "C01")["checks"].append(dict(_case(d, "C01")["checks"][-1])),
    "unknown check kind": lambda d: _check(_case(d, "C01"), "C01-H2").update(kind="advisory"),
    "unknown critical class": lambda d: _check(_case(d, "C01"), "C01-H2").update(critical_class="style"),
    "unknown operational class": lambda d: d["aggregate"].update(operational_failures=["weather"],
                                                                 operationally_valid=False),
    "unknown terminal status": lambda d: _case(d, "C06").update(terminal_status="partial"),
    "operational cause as critical": lambda d: d["aggregate"].update(critical_failures={"authority_mutation": ["C01"]}),
    "empty critical case list": lambda d: d["aggregate"].update(critical_failures={"terminal_failure": []}),
    "critical without case evidence": lambda d: d["aggregate"].update(critical_failures={"fabricated_evidence": ["C02"]}),
    "artifact without success": lambda d: (_case(d, "C06").update(terminal_status="refusal"),
                                           d["aggregate"].update(terminal_status_counts={"refusal": 1, "success": 13})),
    "report version": lambda d: d.update(report_version="baec-live-evaluation-report/v9"),
    "duplicate run id": lambda d: _case(d, "C02").update(ai_run_id=_case(d, "C01")["ai_run_id"]),
    "foreign check id": lambda d: _check(_case(d, "C01"), "C01-H2").update(check_id="C02-H2"),
    "returned model": lambda d: d.update(returned_model_ids=["claude says hello"]),
    "error text": lambda d: _case(d, "C01").update(error_class="an error message"),
    "bool as count": lambda d: d["aggregate"].update(calls_attempted=True),
}


@pytest.mark.parametrize("tamper", TAMPER)
def test_a_tampered_report_never_loads(tmp_path, tamper):
    path, data = _loaded_json(tmp_path)
    TAMPER[tamper](data)
    _write_json(path, data)
    _expect(path, *LOAD_ERRORS)


def test_duplicates_are_rejected_with_their_own_category():
    report = good()
    first, second = report.cases[0], report.cases[1]
    duplicates = {
        "duplicate_case": report.cases + (first,),
        "duplicate_run": (first, dataclasses.replace(second, ai_run_id=first.ai_run_id)) + report.cases[2:],
        "duplicate_check": (dataclasses.replace(first, checks=first.checks + (first.checks[-1],)),) + report.cases[1:],
    }
    for category, cases in duplicates.items():
        with pytest.raises(ReportLoadError) as raised:
            verify_report(_bypass(report, cases=cases))
        assert raised.value.category == category


def test_an_artifact_without_success_is_impossible_even_when_otherwise_consistent():
    report = good()
    consistent = rebuild(report, edit(report, "C06", terminal_status="refusal"))  # audit and aggregate follow the edit
    assert consistent.cases[5].artifact_present and consistent.audit.cases[5].terminal_status == "refusal"
    with pytest.raises(ReportLoadError) as raised:
        verify_report(consistent)
    assert raised.value.category == "impossible_case"


AGGREGATE_FIELDS = ("calls_attempted", "terminal_status_counts", "successful_artifacts", "hard_passed", "hard_total",
                    "critical_failures", "observational_passed", "observational_total", "operational_failures",
                    "operationally_valid", "total_input_tokens", "total_output_tokens", "total_elapsed_ms")
AGGREGATE_EDITS = {
    "terminal_status_counts": {"success": 13, "refusal": 1}, "critical_failures": {"terminal_failure": ["C01"]},
    "operational_failures": ["audit_failure"], "operationally_valid": False,
}


@pytest.mark.parametrize("field", AGGREGATE_FIELDS)
def test_every_stored_aggregate_is_recomputed_never_trusted(tmp_path, field):
    path, data = _loaded_json(tmp_path)
    aggregate = data["aggregate"]
    aggregate[field] = AGGREGATE_EDITS.get(field, aggregate[field] + 1 if type(aggregate[field]) is int else None)
    if field == "operational_failures":
        aggregate["operationally_valid"] = False  # keep the stored pair self-consistent: only recomputation can tell
    _write_json(path, data)
    # a lone operationally_valid flip contradicts the stored failure list and is refused even before recomputation
    _expect(path, "aggregate_mismatch", *(("invalid_value",) if field == "operationally_valid" else ()))


def test_an_unrederivable_audit_failure_fails_closed(tmp_path):
    report = good()
    stored = dataclasses.replace(report.aggregate, operational_failures=("audit_failure",), operationally_valid=False)
    path = tmp_path / "report.json"
    path.write_bytes(serialize(dataclasses.replace(report, aggregate=stored)))
    _expect(path, "aggregate_mismatch")  # v1 cannot re-derive an in-memory status disagreement: it is refused


# --- comparability -------------------------------------------------------------------------------------------


def _bypass(report, **fields):
    copy = dataclasses.replace(report)
    for name, value in fields.items():
        object.__setattr__(copy, name, value)  # a value the constructor would refuse, to reach the guard
    return copy


def _versions(report, key, value):
    return rebuild(report, versions=tuple((k, value if k == key else v) for k, v in report.versions))


def _renamed(report, old, new):
    cases = tuple(dataclasses.replace(c, case_id=new, checks=tuple(dataclasses.replace(
        k, check_id=k.check_id.replace(f"{old}-", f"{new}-")) for k in c.checks)) if c.case_id == old else c
        for c in report.cases)
    return rebuild(report, cases)


def _fewer_cases(report):
    corpus = load_corpus()
    smaller = dataclasses.replace(corpus, cases=corpus.cases[:13])
    return build_report(_evaluate(OPUS, corpus=smaller), source=SOURCE, corpus_sha256=V1_SHA256, generated_at=NOW)


OTHER_COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
MISMATCH_CASES = {
    "source_commit": lambda r: rebuild(r, source=dataclasses.replace(r.source, commit=OTHER_COMMIT)),
    "source_working_tree_dirty": lambda r: rebuild(r, source=dataclasses.replace(r.source, working_tree_clean=False)),
    "corpus_version": lambda r: build_report(_evaluate(OPUS, corpus=v2_corpus()), source=SOURCE,
                                             corpus_sha256=V1_SHA256, generated_at=NOW),
    "corpus_sha256": lambda r: rebuild(r, corpus=dataclasses.replace(r.corpus, sha256="0" * 64)),
    "corpus_case_count": _fewer_cases,
    "case_set": lambda r: _renamed(r, "C14", "C15"),
    **{f"version_{key}": (lambda key: lambda r: _versions(r, key, f"baec-changed-{key.replace('_', '-')}/v9"))(key)
       for key in ("task", "prompt", "input", "output_schema", "request_spec", "canonicalization", "validation")},
    "transport_timeout_seconds": lambda r: rebuild(r, timeout_seconds=60),
    "transport_max_retries": lambda r: rebuild(r, max_retries=2),
    "same_requested_model": lambda r: good(SONNET),
}


@pytest.mark.parametrize("code", MISMATCH_CASES)
def test_each_comparability_precondition_defers_as_not_comparable(code):
    other = MISMATCH_CASES[code](good(OPUS))
    for result in (compare_reports(good(SONNET), other), compare_reports(other, good(SONNET))):
        assert (result.decision, result.reason, result.preferred_model) == ("defer", "not_comparable", None)
        assert code in result.comparability_mismatches
        assert set(result.comparability_mismatches) <= {code, "case_set", "corpus_case_count", "corpus_sha256"}


def test_every_mismatch_code_is_exercised():
    assert set(MISMATCH_CASES) | {"report_version"} == set(MISMATCHES)


def test_a_differing_report_version_is_refused_before_comparability_and_also_named_by_it():
    other = _bypass(good(OPUS), report_version="baec-live-evaluation-report/v9")
    with pytest.raises(ReportLoadError) as raised:
        compare_reports(good(SONNET), other)
    assert raised.value.category == "unsupported_version"
    assert "report_version" in comparability_mismatches(good(SONNET), other)  # the guard behind verification


def test_several_mismatches_are_sorted_unique_and_deterministic():
    other = rebuild(good(OPUS), source=dataclasses.replace(SOURCE, commit=OTHER_COMMIT, working_tree_clean=False),
                    timeout_seconds=60, max_retries=3)
    first, second = compare_reports(good(SONNET), other), compare_reports(other, good(SONNET))
    assert first == second
    assert first.comparability_mismatches == ("source_commit", "source_working_tree_dirty", "transport_max_retries",
                                              "transport_timeout_seconds")


def test_returned_model_ids_need_not_match():
    sonnet, opus = good(SONNET), good(OPUS)
    assert sonnet.returned_model_ids != opus.returned_model_ids
    assert compare_reports(sonnet, opus).reason == "behaviorally_tied"


# --- ranking ---------------------------------------------------------------------------------------------

BRANCHES = {
    "operationally invalid A": (lambda: operationally_invalid(good(SONNET)), lambda: good(OPUS),
                                ("defer", "operationally_inconclusive", None)),
    "operationally invalid B beats a critical A": (lambda: with_critical(good(SONNET)),
                                                   lambda: operationally_invalid(good(OPUS)),
                                                   ("defer", "operationally_inconclusive", None)),
    "only A eligible": (lambda: good(SONNET), lambda: with_critical(good(OPUS)),
                        ("prefer", "only_eligible_model", SONNET)),
    "only B eligible": (lambda: with_critical(good(SONNET)), lambda: with_noncritical_hard_failure(good(OPUS)),
                        ("prefer", "only_eligible_model", OPUS)),
    "neither eligible": (lambda: with_critical(good(SONNET)), lambda: with_critical(good(OPUS), "C06"),
                         ("defer", "none_eligible", None)),
    "hard checks decide": (lambda: with_fewer_artifacts(good(SONNET)), lambda: with_noncritical_hard_failure(good(OPUS)),
                           ("prefer", "more_hard_checks_passed", SONNET)),
    "artifacts decide": (lambda: good(SONNET), lambda: with_fewer_artifacts(good(OPUS)),
                         ("prefer", "more_successful_artifacts", SONNET)),
    "behavioral tie": (lambda: good(SONNET), lambda: good(OPUS), ("defer", "behaviorally_tied", None)),
}


@pytest.mark.parametrize("branch", BRANCHES)
def test_the_ranking_rule_in_both_argument_orders(branch):
    make_a, make_b, (decision, reason, preferred) = BRANCHES[branch]
    a, b = make_a(), make_b()
    forward, backward = compare_reports(a, b), compare_reports(b, a)
    assert forward == backward
    assert (forward.decision, forward.reason, forward.preferred_model) == (decision, reason, preferred)
    assert forward.compared_models == (OPUS, SONNET) and forward.comparability_mismatches == ()


def test_operational_validity_is_decided_before_eligibility_and_scores():
    better = with_noncritical_hard_failure(good(OPUS))
    assert compare_reports(operationally_invalid(good(SONNET)), better).reason == "operationally_inconclusive"


def test_eligibility_is_decided_before_hard_checks():
    critical_but_more = with_critical(good(SONNET))  # 86 hard passed, but a critical failure
    eligible_but_fewer = with_noncritical_hard_failure(with_fewer_artifacts(good(OPUS)))  # 86 passed, 13 artifacts
    eligible_but_fewer = rebuild(eligible_but_fewer, edit(eligible_but_fewer, "C05", {"C05-H2": (False, "analysis_status_forbidden")}))
    assert critical_but_more.aggregate.hard_passed > eligible_but_fewer.aggregate.hard_passed
    result = compare_reports(critical_but_more, eligible_but_fewer)
    assert (result.reason, result.preferred_model) == ("only_eligible_model", OPUS)


def test_hard_checks_are_decided_before_artifacts():
    more_hard = with_fewer_artifacts(good(SONNET))  # 87 hard, 13 artifacts
    more_artifacts = with_noncritical_hard_failure(good(OPUS))  # 86 hard, 14 artifacts
    result = compare_reports(more_artifacts, more_hard)
    assert (result.reason, result.preferred_model) == ("more_hard_checks_passed", SONNET)


def test_the_result_is_a_closed_structure():
    result = compare_reports(good(SONNET), good(OPUS))
    assert result.decision in DECISIONS and result.reason in REASONS
    for broken in (dict(decision="maybe"), dict(reason="vibes"), dict(preferred_model=SONNET),
                   dict(compared_models=(SONNET, OPUS)), dict(comparability_mismatches=("other",))):
        with pytest.raises(ValueError):
            dataclasses.replace(result, **broken)
    assert comparison_line(result) == (f"COMPARISON decision=defer reason=behaviorally_tied preferred=- "
                                       f"models={OPUS},{SONNET} mismatches=-")


def test_an_unverifiable_report_is_never_compared():
    tampered = dataclasses.replace(good(OPUS), aggregate=dataclasses.replace(good(OPUS).aggregate, hard_passed=1))
    with pytest.raises(ReportLoadError):
        compare_reports(good(SONNET), tampered)


# --- non-ranking fields are inert -----------------------------------------------------------------------------


def _drastic(report, which):
    if which == "tokens":
        return rebuild(report, tuple(dataclasses.replace(c, input_tokens=10 ** 9, output_tokens=0) for c in report.cases))
    if which == "latency":
        return rebuild(report, tuple(dataclasses.replace(c, elapsed_ms=10 ** 8) for c in report.cases))
    if which == "observations":
        return rebuild(report, tuple(dataclasses.replace(c, checks=tuple(
            dataclasses.replace(k, passed=not k.passed, code="analysis_status_forbidden" if k.passed else "ok")
            if k.kind == "observational" else k for k in c.checks)) for c in report.cases))
    if which == "generated_at":
        return rebuild(report, generated_at="1999-01-01T00:00:00Z")
    ids = ("unrecognized_model_id", "zz-other-model")
    return rebuild(report, returned_model_ids=ids, audit_changes={"returned_model_ids_seen": ids})


@pytest.mark.parametrize("which", ["tokens", "latency", "observations", "generated_at", "returned_model_ids"])
@pytest.mark.parametrize("branch", ["only A eligible", "hard checks decide", "artifacts decide", "behavioral tie"])
def test_non_ranking_fields_are_inert(which, branch):
    make_a, make_b, _ = BRANCHES[branch]
    a, b = make_a(), make_b()
    baseline = compare_reports(a, b)
    for changed in ((_drastic(a, which), b), (a, _drastic(b, which)), (_drastic(a, which), _drastic(b, which))):
        assert compare_reports(*changed) == baseline
    changed_a = _drastic(a, which)
    if which in ("tokens", "latency", "observations"):
        assert changed_a.aggregate != a.aggregate  # the field really changed, and was ignored


def test_report_digests_are_not_consulted(tmp_path):
    a = load_report(written(good(SONNET), tmp_path))
    b = load_report(written(good(OPUS), tmp_path))
    assert compare_reports(a, b) == compare_reports(dataclasses.replace(a, sha256="f" * 64), b)


# --- C1 is untouched -------------------------------------------------------------------------------------------


def test_the_comparison_layer_imports_no_production_validator_and_writes_nothing():
    source = (Path(__file__).resolve().parents[1] / "tests" / "live" / "compare.py").read_text(encoding="utf-8")
    for forbidden in ("baec_app.ai.validation", "grounding", "anthropic_provider", "import anthropic", "subprocess",
                      "write_bytes", "write_text", "os.link", "print("):
        assert forbidden not in source, forbidden


# --- report v2: the service-returned status (Phase 6D-C2 clarification) -------------------------------------

V1 = "baec-live-evaluation-report/v1"
V2 = "baec-live-evaluation-report/v2"


def test_the_service_status_vocabulary_is_the_services_own():
    assert SERVICE_TERMINAL_STATUSES == tuple(s.value for s in RunStatus)  # never interrupted, incomplete, not_started
    assert REPORT_VERSION == V2 and RETIRED_REPORT_VERSIONS == (V1,)


def test_the_v2_writer_emits_service_terminal_status(tmp_path):
    report = build_report(_evaluate(SONNET, {"C01": NOT_JSON, "C03": RuntimeError("boom")}), source=SOURCE,
                          corpus_sha256=V1_SHA256, generated_at=NOW)
    data = json.loads(written(report, tmp_path).read_text())
    assert data["report_version"] == V2
    statuses = {c["case_id"]: (c["terminal_status"], c["service_terminal_status"]) for c in data["cases"]}
    assert statuses["C01"] == ("parse_failure", "parse_failure")
    assert statuses["C03"] == ("incomplete", None)  # the service raised: no terminal result, nothing invented
    assert all(t == s for case_id, (t, s) in statuses.items() if case_id != "C03")


def test_no_live_code_path_writes_v1():
    source = (Path(__file__).resolve().parents[1] / "tests" / "live" / "report.py").read_text(encoding="utf-8")
    assert source.count(V1) == 1 and f"RETIRED_REPORT_VERSIONS = (\"{V1}\",)" in source
    assert "report_version=REPORT_VERSION" in source


@pytest.mark.parametrize("value", ["interrupted", "incomplete", "not_started", "partial", "", "SUCCESS"])
def test_service_terminal_status_is_a_closed_value(value):
    case = good().cases[0]
    with pytest.raises(ReportFailure):
        dataclasses.replace(case, service_terminal_status=value)


@pytest.mark.parametrize("change,category", [
    (dict(service_terminal_status=None), "impossible_case"),  # a concluded case with no service result
], ids=["concluded case without a service status"])
def test_null_service_status_rules(change, category):
    report = good()
    with pytest.raises(ReportLoadError) as raised:
        verify_report(_bypass(report, cases=edit(report, "C06", **change)))
    assert raised.value.category == category


def test_a_service_status_requires_a_recorded_run():
    report = build_report(_evaluate(SONNET, {"C03": RuntimeError("boom")}), source=SOURCE, corpus_sha256=V1_SHA256,
                          generated_at=NOW)
    not_started = tuple(dataclasses.replace(c, ai_run_id=None, terminal_status="not_started", service_terminal_status="refusal")
                        if c.case_id == "C03" else c for c in report.cases)
    with pytest.raises(ReportLoadError) as raised:
        verify_report(_bypass(report, cases=not_started))
    assert raised.value.category == "impossible_case"


@pytest.fixture
def misreported(monkeypatch):
    """A genuine run in which the service reports semantic_validation_failure for a case persisted as success."""
    real = ExtractionService.extract_interaction

    def misreporting(self, **kwargs):
        result = real(self, **kwargs)
        if kwargs["interaction_id"] == "INT-SYN-C02":
            return dataclasses.replace(result, status=RunStatus.SEMANTIC_VALIDATION_FAILURE)
        return result

    monkeypatch.setattr(ExtractionService, "extract_interaction", misreporting)
    return build_report(_evaluate(SONNET), source=SOURCE, corpus_sha256=V1_SHA256, generated_at=NOW)


def test_the_writer_encodes_a_service_persisted_disagreement_as_audit_failure(misreported, tmp_path):
    case = next(c for c in misreported.cases if c.case_id == "C02")
    assert (case.terminal_status, case.service_terminal_status) == ("success", "semantic_validation_failure")
    assert misreported.aggregate.operational_failures == ("audit_failure",)
    assert misreported.aggregate.operationally_valid is False
    loaded = load_report(written(misreported, tmp_path))  # genuine evidence of the disagreement loads
    assert compare_reports(loaded, good(OPUS)).reason == "operationally_inconclusive"


def test_a_consistent_status_rewrite_is_caught_by_the_audits_independent_read(tmp_path):
    """Both statuses, the artifact flag, and the counts edited together: only the audit's own read disagrees."""
    path, data = _loaded_json(tmp_path)
    _case(data, "C06").update(terminal_status="refusal", service_terminal_status="refusal", artifact_present=False)
    data["aggregate"].update(terminal_status_counts={"refusal": 1, "success": 13}, successful_artifacts=13)
    _write_json(path, data)
    _expect(path, "aggregate_mismatch")
    assert _case(data, "C06")["terminal_status"] != next(
        c for c in data["audit"]["cases"] if c["case_id"] == "C06")["terminal_status"]


def test_deleting_the_audit_failure_cannot_make_a_disagreeing_report_valid(misreported, tmp_path):
    path = written(misreported, tmp_path)
    data = json.loads(path.read_text())
    data["aggregate"].update(operational_failures=[], operationally_valid=True)  # both status facts left intact
    _write_json(path, data)
    _expect(path, "aggregate_mismatch")


@pytest.mark.parametrize("change", ["refusal", "semantic_validation_failure", None])
def test_changing_a_service_status_without_its_consequences_is_rejected(tmp_path, change):
    path, data = _loaded_json(tmp_path)
    _case(data, "C01")["service_terminal_status"] = change  # the stored aggregate still claims a valid run
    _write_json(path, data)
    _expect(path, "aggregate_mismatch", "impossible_case")


def _v1_bytes():
    """A deterministic report in the retired v1 format: the v2 report without service_terminal_status."""
    data = json.loads(serialize(good()))
    data["report_version"] = V1
    for case in data["cases"]:
        del case["service_terminal_status"]
    return canonical_json(data).encode("utf-8")


def test_a_v1_report_is_refused_without_migration_or_leakage(tmp_path):
    path = tmp_path / "baec-extraction-live-corpus-v1__old.json"
    path.write_bytes(_v1_bytes())
    error = _expect(path, "unsupported_version")
    assert str(error) == "unsupported_version" and str(tmp_path) not in repr(error)
    assert path.read_bytes() == _v1_bytes()  # never rewritten or upgraded


def test_v1_and_v2_are_never_compared(tmp_path):
    v1 = _bypass(good(OPUS), report_version=V1)
    for pair in ((good(SONNET), v1), (v1, good(SONNET))):
        with pytest.raises(ReportLoadError) as raised:
            compare_reports(*pair)
        assert raised.value.category == "unsupported_version"


def test_a_v2_report_loads_with_its_service_statuses(tmp_path):
    report = good()
    loaded = load_report(written(report, tmp_path)).report
    assert loaded == report and all(c.service_terminal_status == c.terminal_status for c in loaded.cases)


def test_service_status_matters_only_through_operational_validity():
    a, b = good(SONNET), with_fewer_artifacts(good(OPUS))
    assert compare_reports(a, b).reason == "more_successful_artifacts"
    disagreeing = rebuild(b, edit(b, "C06", service_terminal_status="refusal"))
    assert "audit_failure" in disagreeing.aggregate.operational_failures
    assert compare_reports(a, disagreeing).reason == "operationally_inconclusive"
