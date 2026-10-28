"""Phase 6D-D: corpus v2 and its adversarial evaluation coverage, offline only (no network, no credential).

docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §7.1 and §11. Corpus v1 stays byte-for-byte Phase 6C evidence. These
are deterministic software tests on synthetic cases through a fake provider: they show what the evaluation checks
and the production pipeline do with given outputs, not how any model behaves.
"""

import ast
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from baec_app.ai import service as service_module
from baec_app.ai.contracts import BaecExtractionOutput
from baec_app.ai.grounding import GROUNDING_FAILURE_CODES
from tests.ai_builders import FakeProvider, response
from tests.live.compare import compare_reports, load_report
from tests.live.harness import (
    CORE_CHECKS,
    CORE_NUMBERS_V2,
    CORE_TERMINAL_SUCCESS,
    CORPUS_PATH,
    CORPUS_PATH_V2,
    CORPUS_PATHS,
    CORPUS_V2_CASE_IDS,
    CORPUS_VERSION,
    CORPUS_VERSION_V2,
    CorpusError,
    LiveGateClosed,
    evaluate_check,
    load_corpus,
    load_corpus_version,
    run_evaluation,
    validate_corpus,
)
from tests.live.report import build_report, live_corpus_version, write_report
from tests.test_live_harness import OPUS, SONNET, _excerpt, _hyp, good_output
from tests.test_live_report import NOT_JSON, NOW, SOURCE, V1_SHA256, fake_git, live, live_env

REPO_ROOT = Path(__file__).resolve().parents[1]
V2_SHA256 = "95a7bd5449a37f1ca47a5486432991aee37f60c80f040cfe3b1dd8e69daf17fc"
UNCERTAINTY = "Whether this reflects the whole buying group is not stated."
GOOD_V2 = {
    "C15": ("If the renewal price goes up significantly, we would start looking at other suppliers.",
            "A significant renewal price increase."),
    "C16": ("If on-time delivery is less than 95% for a full quarter, we would reopen the provider evaluation.",
            "On-time delivery less than 95% for a full quarter."),
    "C17": ("If the annual service fee is more than $12,500, or it rises by more than 8.5 percent, we would ask other "
            "providers for quotes.", "An annual service fee more than $12,500 or a rise of more than 8.5 percent."),
}


def good_v2(case_id: str, interaction_id: str, **changes) -> dict:
    """A well-behaved output: v1's for C01-C14, and a grounded possible-BAEC reading for C15-C17."""
    if case_id not in GOOD_V2:
        value = good_output(case_id, interaction_id)
    else:
        excerpt, normalized = GOOD_V2[case_id]
        value = {"analysis_status": "possible_baec_language", "source_excerpts": [_excerpt("e1", interaction_id, excerpt)],
                 "normalized_condition": normalized, "normalized_evaluation_link": None,
                 "criterion_hypotheses": _hyp("supported", ["e1"]), "uncertainties": [UNCERTAINTY]}
    value.update(changes)
    return value


def provider_v2(overrides=None, model=SONNET):
    """A fake provider answering every v2 case; overrides map a case to a response, an exception, or output changes."""
    overrides = overrides or {}

    def reply(spec):
        interaction_id = json.loads(spec.messages[0]["content"])["interaction_id"]
        case_id = interaction_id.removeprefix("INT-SYN-")
        override = overrides.get(case_id)
        if isinstance(override, BaseException):
            raise override
        if override is not None and not isinstance(override, dict):
            return override
        return response(json.dumps(good_v2(case_id, interaction_id, **(override or {}))), model=model)

    return FakeProvider(reply)


def evaluate_v2(overrides=None, model=SONNET):
    return run_evaluation(load_corpus_version(CORPUS_VERSION_V2), model=model, provider=provider_v2(overrides, model),
                          emit=[].append)


def outcome(evaluation, case_id):
    return next(o for o in evaluation.outcomes if o.case_id == case_id)


def failed(evaluation, case_id):
    return {c.check_id: c.critical for c in outcome(evaluation, case_id).checks if c.kind == "hard" and not c.passed}


def critical(evaluation, case_id):
    """Only the failed hard checks that carry a critical class (artifact-requiring checks also fail, uncritically,
    whenever production rejects an output and no artifact exists)."""
    return {k: v for k, v in failed(evaluation, case_id).items() if v is not None}


def without_production_grounding(monkeypatch):
    """Simulate a production guard that missed the corruption, so the evaluation's own checks are what remains."""
    real = service_module.validate_extraction
    monkeypatch.setattr(service_module, "validate_extraction",
                        lambda *a, **k: tuple(c for c in real(*a, **k) if c not in GROUNDING_FAILURE_CODES))


# --- corpus identity -------------------------------------------------------------------------------------


def test_corpus_v1_is_byte_identical_while_v2_exists_independently():
    assert hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest() == V1_SHA256
    assert hashlib.sha256(CORPUS_PATH_V2.read_bytes()).hexdigest() == V2_SHA256 != V1_SHA256
    assert CORPUS_PATH != CORPUS_PATH_V2 and CORPUS_PATHS == {CORPUS_VERSION: CORPUS_PATH, CORPUS_VERSION_V2: CORPUS_PATH_V2}
    v1 = load_corpus_version(CORPUS_VERSION)
    assert v1.corpus_version == CORPUS_VERSION and len(v1.cases) == 14


def test_corpus_v2_identity_is_pinned():
    corpus = load_corpus_version(CORPUS_VERSION_V2)
    assert corpus.corpus_version == "baec-extraction-live-corpus/v2"
    assert tuple(c.case_id for c in corpus.cases) == CORPUS_V2_CASE_IDS == tuple(f"C{n:02d}" for n in range(1, 18))
    assert [c.criticality for c in corpus.cases[14:]] == ["critical", "critical", "standard"]


def _v2_data():
    return json.loads(CORPUS_PATH_V2.read_text(encoding="utf-8"))


@pytest.mark.parametrize("change", ["missing C17", "duplicate C16", "reordered", "extra C18"])
def test_a_v2_corpus_with_a_wrong_case_set_is_rejected(change):
    data = _v2_data()
    cases = data["cases"]
    if change == "missing C17":
        cases.pop()
    elif change == "duplicate C16":
        cases.append(dict(cases[15]))
    elif change == "reordered":
        cases[0], cases[1] = cases[1], cases[0]
    else:
        extra = json.loads(json.dumps(cases[16]))
        extra.update(case_id="C18", account_id="ACC-SYN-C18", interaction_id="INT-SYN-C18")
        for check in extra["checks"]:
            check["check_id"] = check["check_id"].replace("C17-", "C18-")
        cases.append(extra)
    with pytest.raises(CorpusError):
        validate_corpus(data)


def test_c01_to_c14_are_carried_forward_without_legacy_terminal_success():
    v1, v2 = load_corpus_version(CORPUS_VERSION), load_corpus_version(CORPUS_VERSION_V2)
    for old, new in zip(v1.cases, v2.cases[:14]):
        assert (new.case_id, new.account_id, new.interaction_id, new.interaction_text, new.criticality) == (
            old.case_id, old.account_id, old.interaction_id, old.interaction_text, old.criticality)
        assert new.checks == tuple(k for k in old.checks if k["type"] != "terminal_success")  # IDs unchanged
    v1_purposes = [c["purpose"] for c in json.loads(CORPUS_PATH.read_text())["cases"]]
    assert [c["purpose"] for c in _v2_data()["cases"][:14]] == v1_purposes


# --- explicit corpus selection -------------------------------------------------------------------------------


def test_a_corpus_is_only_ever_selected_explicitly():
    assert load_corpus().corpus_version == CORPUS_VERSION  # the historical default path is v1, never "latest"
    with pytest.raises(CorpusError):
        load_corpus_version("baec-extraction-live-corpus/latest")
    for env in ({}, {"BAEC_LIVE_CORPUS": ""}, {"BAEC_LIVE_CORPUS": "v2"}, {"BAEC_LIVE_CORPUS": "latest"}):
        with pytest.raises(LiveGateClosed):
            live_corpus_version(env)
    assert live_corpus_version({"BAEC_LIVE_CORPUS": CORPUS_VERSION_V2}) == CORPUS_VERSION_V2


def test_a_live_run_without_a_corpus_selection_makes_no_attempt(tmp_path):
    env = live_env(tmp_path)
    del env["BAEC_LIVE_CORPUS"]
    provider = provider_v2()
    with pytest.raises(LiveGateClosed):
        live(tmp_path, env=env, provider=provider)
    assert provider.prepared == provider.invoked == [] and list(tmp_path.iterdir()) == []


def test_a_corpus_file_must_declare_the_selected_version(tmp_path):
    provider = provider_v2()
    with pytest.raises(LiveGateClosed):
        live(tmp_path, env=live_env(tmp_path, BAEC_LIVE_CORPUS=CORPUS_VERSION_V2), provider=provider,
             corpus_path=CORPUS_PATH)  # the v1 file under a v2 selection
    assert provider.invoked == []


# --- version rules ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["hard", "observational"])
def test_v2_forbids_legacy_terminal_success_checks(kind):
    data = _v2_data()
    data["cases"][0]["checks"].append({"check_id": "C01-H9", "type": "terminal_success", "kind": kind, "critical": None})
    with pytest.raises(CorpusError):
        validate_corpus(data)


def test_v2_retires_clear_case_parse_failure_and_v1_keeps_it():
    data = _v2_data()
    data["cases"][0]["checks"][0]["critical"] = "clear_case_parse_failure"
    with pytest.raises(CorpusError):
        validate_corpus(data)
    v1 = load_corpus_version(CORPUS_VERSION)
    assert any(k.get("critical") == "clear_case_parse_failure" for c in v1.cases for k in c.checks)
    assert not any(k.get("critical") == "clear_case_parse_failure" for c in load_corpus_version(CORPUS_VERSION_V2).cases
                   for k in c.checks)


def test_v1_rules_are_unchanged_and_the_new_check_type_is_v2_only():
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert validate_corpus(json.loads(json.dumps(data))).corpus_version == CORPUS_VERSION
    data["cases"][0]["checks"].append({"check_id": "C01-H9", "type": "free_text_forbids_numeric_content",
                                       "kind": "hard", "critical": None})
    with pytest.raises(CorpusError):
        validate_corpus(data)


# --- universal CORE-TERMINAL-SUCCESS --------------------------------------------------------------------------


def test_every_v2_case_receives_core_terminal_success_and_a_clean_run_passes_everything():
    evaluation = evaluate_v2()
    for case_outcome in evaluation.outcomes:
        core = [c for c in case_outcome.checks if c.check_id == "CORE-TERMINAL-SUCCESS"]
        assert len(core) == 1 and core[0].passed and core[0].kind == "hard"
        assert not any(c.check_id.endswith(("-H1",)) and c.code.startswith("status_") for c in case_outcome.checks)
    assert evaluation.hard_passed == evaluation.hard_total and evaluation.critical_failures == ()
    assert evaluation.successful_artifacts == 17 and evaluation.operationally_valid


@pytest.mark.parametrize("override", [NOT_JSON, response("No.", stop_reason="refusal", model=SONNET),
                                      {"normalized_condition": "A renewal increase of more than 7%."}],
                         ids=["parse_failure", "refusal", "semantic_validation_failure"])
@pytest.mark.parametrize("case_id", ["C01", "C04", "C12", "C15"])
def test_a_behavioral_terminal_failure_is_terminal_failure_and_never_clear_case(case_id, override):
    evaluation = evaluate_v2({case_id: override})
    assert failed(evaluation, case_id).get("CORE-TERMINAL-SUCCESS") == "terminal_failure"
    assert "terminal_failure" in evaluation.critical_failures
    assert "clear_case_parse_failure" not in evaluation.critical_failures


def test_an_operational_failure_carries_no_behavioral_class():
    from baec_app.ai.provider import ProviderApiError

    evaluation = evaluate_v2({"C05": ProviderApiError("overloaded", "req_x")})
    assert outcome(evaluation, "C05").status == "api_error"
    assert failed(evaluation, "C05").get("CORE-TERMINAL-SUCCESS", "absent") is None
    assert "terminal_failure" not in evaluation.critical_failures and not evaluation.operationally_valid


def test_the_v1_criticality_gap_is_closed_in_v2():
    """Phase 6C finding (§9.5 item 3): C04 could fail validation yet pass every hard check; C12 had no critical label."""
    semantic = {"normalized_condition": "Something about 7 suppliers."}
    v1 = run_evaluation(load_corpus_version(CORPUS_VERSION), model=SONNET, emit=[].append,
                        provider=provider_v2({"C04": semantic, "C12": semantic}))
    for case_id in ("C04", "C12"):
        assert outcome(v1, case_id).status == "semantic_validation_failure"
    assert failed(v1, "C04") == {}  # v1: all hard checks still pass
    assert failed(v1, "C12") == {"C12-H1": None}  # v1: a failure with no critical class
    assert v1.critical_failures == ()  # so v1 left the model eligible
    v2 = evaluate_v2({"C04": semantic, "C12": semantic})
    for case_id in ("C04", "C12"):
        assert failed(v2, case_id)["CORE-TERMINAL-SUCCESS"] == "terminal_failure"
    assert v2.critical_failures == ("terminal_failure",)  # v2: ineligible


def test_v1_still_represents_the_historical_clear_case_parse_failure():
    v1 = run_evaluation(load_corpus_version(CORPUS_VERSION), model=SONNET, emit=[].append,
                        provider=provider_v2({"C01": NOT_JSON}))
    assert "clear_case_parse_failure" in v1.critical_failures and "terminal_failure" not in v1.critical_failures


# --- C15: unsupported numeric invention across all free text ----------------------------------------------------

C15_CHECK = {"check_id": "C15-H2", "type": "free_text_forbids_numeric_content", "kind": "hard",
             "critical": "threshold_corruption"}


def c15_output(**changes) -> BaecExtractionOutput:
    value = good_v2("C15", "INT-SYN-C15")
    hypotheses = changes.pop("explanation", None)
    if hypotheses is not None:
        value["criterion_hypotheses"][2]["explanation"] = hypotheses
    value.update(changes)
    return BaecExtractionOutput.model_validate(value)


def c15(output):
    return evaluate_check(C15_CHECK, result=None, output=output, provenance_ok=True, source_text="")


SURFACES = {
    "normalized_condition": dict(normalized_condition="A renewal price increase of more than 5%."),
    "normalized_evaluation_link": dict(normalized_evaluation_link="Reopened within two weeks."),
    "explanation": dict(explanation="All 4 criteria are supported."),
    "uncertainty": dict(uncertainties=[UNCERTAINTY, "Whether one supplier or several would be considered."]),
}


@pytest.mark.parametrize("surface", SURFACES)
def test_c15_catches_numeric_invention_on_each_free_text_surface(surface):
    result = c15(c15_output(**SURFACES[surface]))
    assert (result.passed, result.critical, result.code) == (False, "threshold_corruption", "numeric_content_in_free_text")


def test_c15_passes_numeric_free_model_text_and_ignores_excerpts_ids_and_enums():
    assert c15(c15_output()).passed
    assert c15(c15_output(normalized_condition="A significant increase that anyone would notice often.")).passed
    numeric_excerpt = {"excerpt_id": "e2026", "source_interaction_id": "INT-SYN-C15", "text": "95% for 12 weeks",
                       "attributed_speaker": "buyer"}
    output = c15_output(source_excerpts=[_excerpt("e1", "INT-SYN-C15", GOOD_V2["C15"][0]), numeric_excerpt],
                        criterion_hypotheses=_hyp("supported", ["e1", "e2026"]))
    assert c15(output).passed  # verbatim excerpts, identifiers, and references are not model-authored free text
    assert c15(None).passed  # no artifact: nothing was authored (CORE-TERMINAL-SUCCESS owns that)


@pytest.mark.parametrize("word", ["one", "Twenty-one", "THOUSAND", "nineteen", "7"])
def test_c15_number_detection_is_whole_word_and_case_insensitive(word):
    assert not c15(c15_output(normalized_condition=f"A rise of {word} at renewal.")).passed


@pytest.mark.parametrize("surface", SURFACES)
def test_c15_end_to_end_production_and_evaluation_each_reject_it(surface, monkeypatch):
    with_production = evaluate_v2({"C15": SURFACES[surface] if surface != "explanation" else None})
    assert outcome(with_production, "C15").status in ("success", "semantic_validation_failure")
    without_production_grounding(monkeypatch)
    changes = dict(SURFACES[surface])
    if surface == "explanation":
        value = good_v2("C15", "INT-SYN-C15")
        value["criterion_hypotheses"][2]["explanation"] = changes.pop("explanation")
        changes = {"criterion_hypotheses": value["criterion_hypotheses"]}
    evaluation = evaluate_v2({"C15": changes})
    assert outcome(evaluation, "C15").status == "success"  # the guard was bypassed: an artifact exists
    expected = {"C15-H2": "threshold_corruption"}
    if surface == "normalized_condition":  # a digit in a normalization: CORE-NUMBERS catches it too
        expected["CORE-NUMBERS"] = "threshold_corruption"
    assert failed(evaluation, "C15") == expected  # explanations, uncertainties, and number words: only C15-H2


# --- C16: comparator mutation ----------------------------------------------------------------------------------


def c16_link(normalization):
    return {"normalized_condition": normalization}


@pytest.mark.parametrize("normalization", ["On-time delivery less than 95% for a full quarter.",
                                           "On-time delivery below 95% for a full quarter.",
                                           "On-time delivery under 95 percent for a full quarter."],
                         ids=["exact", "below (same class)", "under (same class)"])
def test_c16_accepts_the_same_comparator_class(normalization):
    evaluation = evaluate_v2({"C16": c16_link(normalization)})
    assert outcome(evaluation, "C16").status == "success" and failed(evaluation, "C16") == {}


@pytest.mark.parametrize("normalization", ["On-time delivery at most 95% for a full quarter.",
                                           "On-time delivery of 95% or less for a full quarter.",
                                           "On-time delivery no more than 95% for a full quarter.",
                                           "On-time delivery up to 95% for a full quarter."],
                         ids=["at most", "or less", "no more than", "up to"])
def test_c16_a_comparator_class_change_with_the_same_magnitude_fails_both_layers(normalization, monkeypatch):
    assert critical(evaluate_v2({"C16": c16_link(normalization)}), "C16") == {"CORE-TERMINAL-SUCCESS": "terminal_failure"}
    without_production_grounding(monkeypatch)
    evaluation = evaluate_v2({"C16": c16_link(normalization)})
    # the evaluation catches it on its own: the class changed (H3) and no LESS_THAN comparator remains (H4)
    assert failed(evaluation, "C16") == {"C16-H3": "threshold_corruption", "C16-H4": "threshold_corruption"}


def test_c16_a_comparator_dropped_from_an_excerpt_is_a_hard_failure():
    value = good_v2("C16", "INT-SYN-C16")
    value["source_excerpts"].append(_excerpt("e2", "INT-SYN-C16", "95% for a full quarter"))
    evaluation = evaluate_v2({"C16": {"source_excerpts": value["source_excerpts"]}})
    assert failed(evaluation, "C16") == {"C16-H2": "threshold_corruption"}


@pytest.mark.parametrize("normalization", ["On-time delivery of 95% for a full quarter.",
                                           "On-time delivery at 95 percent for a full quarter."],
                         ids=["95%", "95 percent"])
def test_c16_a_comparator_dropped_from_the_normalization_is_a_hard_failure_without_production(normalization,
                                                                                             monkeypatch):
    """Phase 6D-D clarification: the evaluation detects a dropped comparator independently of production grounding."""
    dropped = c16_link(normalization)
    assert critical(evaluate_v2({"C16": dropped}), "C16") == {"CORE-TERMINAL-SUCCESS": "terminal_failure"}
    without_production_grounding(monkeypatch)
    evaluation = evaluate_v2({"C16": dropped})
    assert outcome(evaluation, "C16").status == "success"  # production did not stop it here
    assert failed(evaluation, "C16") == {"C16-H4": "threshold_corruption"}
    observed = {c.check_id: c.passed for c in outcome(evaluation, "C16").checks if c.kind == "observational"}
    assert observed == {"C16-O1": False}  # still retained as independent, non-ranking evidence


# --- C17: legitimate formatting ---------------------------------------------------------------------------------

APPROVED = {
    "comma removed": "An annual service fee more than $12500 or a rise of more than 8.5 percent.",
    "trailing decimal zero": "An annual service fee more than $12,500 or a rise of more than 8.50%.",
    "dollar words and percent sign": "An annual service fee more than 12,500 dollars or a rise of more than 8.5%.",
    "currency marker spacing": "An annual service fee more than $ 12,500 or a rise of more than 8.5 percent.",
    "percent words": "An annual service fee more than $12,500 or a rise of more than 8.5 per cent.",
}


@pytest.mark.parametrize("normalization", APPROVED.values(), ids=APPROVED.keys())
def test_c17_approved_formatting_passes_every_hard_check(normalization):
    evaluation = evaluate_v2({"C17": {"normalized_condition": normalization}})
    assert outcome(evaluation, "C17").status == "success" and failed(evaluation, "C17") == {}
    assert evaluation.critical_failures == ()


SEMANTIC_CHANGES = {
    "comparator": ("An annual service fee of at least $12,500 or a rise of more than 8.5 percent.",
                   {"C17-H3": "threshold_corruption"}),
    "magnitude": ("An annual service fee more than $13,000 or a rise of more than 8.5 percent.",
                  {"CORE-NUMBERS": "threshold_corruption"}),
    "decimal magnitude": ("An annual service fee more than $12,500 or a rise of more than 8.05 percent.",
                          {"CORE-NUMBERS": "threshold_corruption"}),
    "off by one": ("An annual service fee more than $12,501 or a rise of more than 8.5 percent.",
                   {"CORE-NUMBERS": "threshold_corruption"}),
}


@pytest.mark.parametrize("change", SEMANTIC_CHANGES)
def test_c17_a_real_semantic_change_still_fails(change, monkeypatch):
    normalization, evaluation_only = SEMANTIC_CHANGES[change]
    assert critical(evaluate_v2({"C17": {"normalized_condition": normalization}}), "C17") == {
        "CORE-TERMINAL-SUCCESS": "terminal_failure"}  # production grounding rejects it first
    without_production_grounding(monkeypatch)
    assert failed(evaluate_v2({"C17": {"normalized_condition": normalization}}), "C17") == evaluation_only


def test_c17_a_currency_kind_change_is_rejected_by_production():
    normalization = "An annual service fee more than USD 12,500 or a rise of more than 8.5 percent."
    assert critical(evaluate_v2({"C17": {"normalized_condition": normalization}}), "C17") == {
        "CORE-TERMINAL-SUCCESS": "terminal_failure"}


# --- CORE-NUMBERS: v2 compares literals by value; v1 keeps its historical literal digits ------------------------


def _numbers(check, source, normalization):
    output = BaecExtractionOutput.model_validate(good_v2("C17", "INT-SYN-C17", normalized_condition=normalization))
    return evaluate_check(check, result=None, output=output, provenance_ok=True, source_text=source).passed


EQUIVALENT = [("$12,500", "$12500"), ("$12500", "$12,500"), ("8.5%", "8.50%"), ("8.50%", "8.5%"), ("05", "5"),
              ("10.00", "10"), ("1,000,000", "1000000"), ("05.0", "5")]
DIFFERENT = [("12,500", "12,501"), ("8.5", "8.05"), ("95", "96"), ("12,500", "1,2500"), ("10", "100")]


@pytest.mark.parametrize("source,model", EQUIVALENT)
def test_v2_core_numbers_accepts_representation_only_literal_formatting(source, model):
    assert CORE_NUMBERS_V2["check_id"] == "CORE-NUMBERS" and CORE_NUMBERS_V2["critical"] == "threshold_corruption"
    assert _numbers(CORE_NUMBERS_V2, f"A fee of {source}.", f"A fee of {model}.")


@pytest.mark.parametrize("source,model", DIFFERENT)
def test_v2_core_numbers_still_rejects_a_true_magnitude_change(source, model):
    assert not _numbers(CORE_NUMBERS_V2, f"A fee of {source}.", f"A fee of {model}.")


@pytest.mark.parametrize("scaled", ["12.5k", "12.5 thousand", "12.5K"])
def test_v2_core_numbers_never_converts_scale_forms(scaled):
    """Literal formatting only: 12.5k or 12.5 thousand is never read as 12,500 (no scale, words, or arithmetic)."""
    assert not _numbers(CORE_NUMBERS_V2, "A fee of $12,500.", f"A fee of {scaled}.")


@pytest.mark.parametrize("source,model", [("$12,500", "$12500"), ("8.5%", "8.50%"), ("10%", "10.0%")])
def test_v1_core_numbers_keeps_its_historical_literal_digit_semantics(source, model):
    v1_numbers = CORE_CHECKS[2]
    assert v1_numbers["type"] == "normalization_numbers_from_source"
    assert not _numbers(v1_numbers, f"A fee of {source}.", f"A fee of {model}.")  # historical v1 behavior


def test_a_v1_corpus_run_keeps_historical_core_numbers_end_to_end():
    normalization = {"normalized_condition": "A price increase of more than 10.0% at the next renewal."}
    v1 = run_evaluation(load_corpus_version(CORPUS_VERSION), model=SONNET, emit=[].append,
                        provider=provider_v2({"C01": normalization}))
    assert outcome(v1, "C01").status == "success"  # production accepts 10.0% for 10%
    assert failed(v1, "C01") == {"CORE-NUMBERS": "threshold_corruption"}  # v1 history: unchanged
    v2 = evaluate_v2({"C01": normalization})
    assert failed(v2, "C01") == {}  # v2: representation-only formatting is not corruption


# --- report v2 integration ---------------------------------------------------------------------------------------


def test_a_v2_live_run_writes_and_loads_a_v2_report_with_the_actual_digest(tmp_path):
    lines = []
    run, provider, _ = live(tmp_path, env=live_env(tmp_path, BAEC_LIVE_CORPUS=CORPUS_VERSION_V2), lines=lines,
                            provider=provider_v2({"C15": {"normalized_condition": "A rise of 5%."}}))
    assert len(provider.invoked) == 17 and run.report_file is not None
    loaded = load_report(tmp_path / run.report_file).report
    assert (loaded.report_version, loaded.corpus.version, loaded.corpus.case_count) == (
        "baec-live-evaluation-report/v2", CORPUS_VERSION_V2, 17)
    assert loaded.corpus.sha256 == hashlib.sha256(CORPUS_PATH_V2.read_bytes()).hexdigest() == V2_SHA256
    c15 = next(c for c in loaded.cases if c.case_id == "C15")
    assert c15.terminal_status == "semantic_validation_failure"
    assert {k.check_id: k.critical_class for k in c15.checks if not k.passed and k.critical_class} == {
        "CORE-TERMINAL-SUCCESS": "terminal_failure"}
    c17 = next(c for c in loaded.cases if c.case_id == "C17")
    assert c17.terminal_status == "success" and all(k.passed for k in c17.checks)
    assert dict(loaded.aggregate.critical_failures) == {"terminal_failure": ("C15",)}


def test_evaluation_only_hard_failures_appear_in_report_evidence(tmp_path, monkeypatch):
    without_production_grounding(monkeypatch)
    overrides = {"C15": {"uncertainties": ["Whether two suppliers would bid."]},
                 "C16": {"normalized_condition": "On-time delivery at most 95% for a full quarter."}}
    report = build_report(evaluate_v2(overrides), source=SOURCE, corpus_sha256=V2_SHA256, generated_at=NOW)
    name, _ = write_report(report, tmp_path)
    loaded = load_report(tmp_path / name).report
    assert dict(loaded.aggregate.critical_failures) == {"threshold_corruption": ("C15", "C16")}


def test_a_v2_corpus_report_is_never_comparable_with_a_v1_corpus_report():
    v1 = build_report(run_evaluation(load_corpus_version(CORPUS_VERSION), model=SONNET, emit=[].append,
                                     provider=provider_v2()), source=SOURCE, corpus_sha256=V1_SHA256, generated_at=NOW)
    v2 = build_report(evaluate_v2(model=OPUS), source=SOURCE, corpus_sha256=V2_SHA256, generated_at=NOW)
    for result in (compare_reports(v1, v2), compare_reports(v2, v1)):
        assert (result.decision, result.reason) == ("defer", "not_comparable")
        assert {"corpus_version", "corpus_sha256", "corpus_case_count", "case_set"} <= set(result.comparability_mismatches)


def test_observational_checks_never_decide_eligibility_in_v2():
    corpus = load_corpus_version(CORPUS_VERSION_V2)
    assert all(k["critical"] is None for c in corpus.cases for k in c.checks if k["kind"] == "observational")
    hard_new = {k["check_id"] for c in corpus.cases[14:] for k in c.checks if k["kind"] == "hard"}
    assert {"C15-H2", "C16-H2", "C16-H3", "C17-H3"} <= hard_new


# --- independence from production grounding ----------------------------------------------------------------------


def test_the_live_evaluation_layer_never_imports_the_production_grounding():
    for path in sorted((REPO_ROOT / "tests" / "live").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not {m for m in imported if m and ("grounding" in m or m == "baec_app.ai.validation")}, path
