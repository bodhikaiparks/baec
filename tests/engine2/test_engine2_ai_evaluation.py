"""Stage F curated offline evaluation of the AI proposal boundary (replay only; no model call).

Faithful replay is pipeline validation using predetermined outputs, not AI accuracy, model
accuracy, or model performance. Overreaching replay shows the metrics expose false decisive proposals.

Scored against Stage B dimension expectations, never against purchase. Metrics stay
separate per case and per category; there is no combined number.
"""

import re
from collections import defaultdict
from datetime import datetime

import pytest

from baec_app.engine2.ai import ModelResponse, ProposalRejected, propose_correspondence, validate_proposal_output
from tests.engine2.ai_replay import ReplayModel, ai_case, ai_input_for, evaluate, faithful_output, overreaching_output
from tests.engine2.corpus_adapter import load_corpus, run_case

CORPUS = load_corpus()
CASES = {c["case_id"]: c for c in CORPUS["cases"]}
WHEN = datetime.fromisoformat("2027-06-02T00:00:00+00:00")

# Each case tests a distinct reasoning boundary for an AI proposer.
CURATED = {
    "HBR-CORR-003": "clear evidence compatible with HUMAN_VERIFIED",
    "HBR-CORR-005": "exactly 10% contradicts 'more than 10%'",
    "HBR-CORR-010": "10.01% supports the strict comparator",
    "HBR-CORR-004": "wrong supplier",
    "HBR-CORR-032": "timing mismatch (mid-term increase)",
    "HBR-CORR-011": "approximate figures stay unresolved",
    "HBR-CORR-048": "conflicting current sources",
    "HBR-CORR-008": "corrected source (12% to 7%)",
    "HBR-CORR-047": "retracted source with no replacement",
    "HBR-CORR-040": "unauthorized source: blocked before any AI input",
    "HBR-CORR-057": "plan activation refused: blocked before any AI input",
    "HBR-CORR-055": "BAEC_REVALIDATION_REQUIRED with otherwise perfect evidence",
    "HBR-CORR-020": "Derived Measurement supplies the percentage",
    "HBR-CORR-024": "Derived Measurement invalidated by a corrected input",
    "HBR-CORR-051": "valid multi-source composition",
    "HBR-CORR-025": "invalid multi-source composition (other account)",
    "HBR-CORR-007": "provenance limitation (unsourced seller note)",
    "HBR-CORR-018": "pricing-scope ambiguity",
    "HBR-CORR-042": "duplicated evidence as one event",
    "HBR-CORR-062": "tax increase is not a supplier price increase",
}
BLOCKED = {"HBR-CORR-040", "HBR-CORR-057"}
CLASSIFIED = [c for c in CURATED if c not in BLOCKED]


def _run_replay(case_id, builder):
    run = run_case(CORPUS, CASES[case_id])
    ai_input, to_neutral = ai_case(CORPUS, run)
    model = ReplayModel({ai_input.input_digest: builder(ai_input, to_neutral)})
    return propose_correspondence(model, ai_input, artifact_id=f"AIP-{len(model.outputs)}", created_at=WHEN)


def test_curated_subset_size_and_coverage():
    assert 16 <= len(CURATED) <= 24 and set(CURATED) <= set(CASES)
    assert len(set(CURATED.values())) == len(CURATED)


@pytest.mark.parametrize("case_id", sorted(BLOCKED))
def test_blocked_cases_have_no_ai_input(case_id):
    run = run_case(CORPUS, CASES[case_id])
    assert run.candidate is None and run.review is None and run.outcome is None


@pytest.mark.parametrize("case_id", CLASSIFIED)
def test_faithful_replay_is_accepted_and_agrees(case_id):
    case = CASES[case_id]
    proposal = _run_replay(case_id, lambda i, ids: faithful_output(i, case, ids))
    m = evaluate(proposal, case)
    assert m.accepted and m.evidence_refs_valid and m.excerpts_valid
    assert m.agreement == 11 and m.false_decisive == 0 and m.plan_version_violations == 0
    assert m.unresolved_preserved == m.unresolved_total
    assert m.checks_identified == m.checks_expected


@pytest.mark.parametrize("case_id", CLASSIFIED)
def test_overreaching_replay_is_exposed_by_the_metrics(case_id):
    case = CASES[case_id]
    non_supported = sum(f["finding"] != "SUPPORTED" for f in case["expected_dimension_findings"].values())
    try:
        proposal = _run_replay(case_id, lambda i, ids: overreaching_output(i))
    except ProposalRejected:
        return  # the validator itself refused it (no usable support): also safe
    m = evaluate(proposal, case)
    assert m.false_decisive == non_supported
    assert m.unresolved_preserved == 0 or m.unresolved_total == 0


def summarize():
    """Per-category deterministic results for the report. Separate metrics; no combined number."""
    rows = defaultdict(lambda: defaultdict(int))
    for case_id in CLASSIFIED:
        case = CASES[case_id]
        for label, builder in (("faithful", lambda i, ids, c=case: faithful_output(i, c, ids)),
                               ("overreaching", lambda i, ids: overreaching_output(i))):
            r = rows[(case["category"], label)]
            r["cases"] += 1
            try:
                m = evaluate(_run_replay(case_id, builder), case)
            except ProposalRejected:
                r["rejected"] += 1
                continue
            r["accepted"] += 1
            r["agreement"] += m.agreement
            r["dimensions"] += 11
            r["false_decisive"] += m.false_decisive
            r["unresolved_preserved"] += m.unresolved_preserved
            r["unresolved_total"] += m.unresolved_total
            r["plan_version_violations"] += m.plan_version_violations
            r["checks_identified"] += len(m.checks_identified)
            r["checks_expected"] += len(m.checks_expected)
    return {k: dict(v) for k, v in sorted(rows.items())}


def test_summary_is_deterministic():
    assert summarize() == summarize()


def test_no_model_output_reaches_validation_without_a_matching_input():
    case = CASES["HBR-CORR-003"]
    a, ids = ai_case(CORPUS, run_case(CORPUS, case))
    b_ = ai_input_for(CORPUS, run_case(CORPUS, CASES["HBR-CORR-005"]))
    with pytest.raises(ProposalRejected, match="different input snapshot|BAEC or candidate"):
        validate_proposal_output(ModelResponse(faithful_output(a, case, ids), "replay", "m"), b_, artifact_id="X",
                                 created_at=WHEN)


# --- input leakage audit over every curated AI input ---------------------------------------------------

NEUTRAL_ID = re.compile(r"^(OBS|MEAS|ITEM|EVENT|SIG|CANDIDATE)-\d+$")
LEAK_TOKENS = ("HBR-CORR", "expected_correspondence_outcome", "expected_dimension_findings", "expected_human_sufficiency",
               "expected_baec_revalidation_required", "BAEC_REVALIDATION_REQUIRED", "HUMAN_VERIFIED_CORRESPONDENCE",
               "POSSIBLE_CORRESPONDENCE", "NO_CORRESPONDENCE", "sufficiency", "coverage_tags", "rationale", "oracle")
CATEGORIES = ("HARBOR_REFERENCE", "THRESHOLD_COMPARATOR", "DERIVED_MEASUREMENT", "ENTITY_APPLICABILITY", "TIMING",
              "SOURCE_PROVENANCE", "CORRECTION_CONFLICT", "MULTI_SOURCE", "BAEC_REVALIDATION", "SEMANTIC_AMBIGUITY",
              "ORACLE_INTEGRITY")


@pytest.mark.parametrize("case_id", CLASSIFIED)
def test_curated_ai_input_leaks_no_case_identity_or_oracle(case_id):
    case = CASES[case_id]
    ai_input = ai_input_for(CORPUS, run_case(CORPUS, case))
    text, payload = ai_input.canonical_json, ai_input.payload
    number = case_id.split("-")[-1]
    for token in LEAK_TOKENS + CATEGORIES + (case_id,):
        assert token not in text, token
    assert not re.search(rf"(?<![0-9]){number}(?![0-9])", "".join(
        [ai_input.candidate_id] + [o["observation_id"] + o["evidence_event_id"] for o in payload["observations"]]
        + [m["measurement_id"] for m in payload["derived_measurements"]]
        + [r["item_id"] + r["reason"] for r in payload["source_refusals"]]
        + [s["signal_candidate_id"] for s in payload["candidate"]["signal_candidates"]]))
    model_visible = ([ai_input.candidate_id] + [o["observation_id"] for o in payload["observations"]]
                     + [o["evidence_event_id"] for o in payload["observations"]]
                     + [m["measurement_id"] for m in payload["derived_measurements"]]
                     + [r["item_id"] for r in payload["source_refusals"]]
                     + [s["signal_candidate_id"] for s in payload["candidate"]["signal_candidates"]])
    assert all(NEUTRAL_ID.match(i) for i in model_visible), model_visible
    for o in payload["observations"]:
        assert not re.search(r"\b(OBS|PKT|EVT|DM)-\d{3}-\d{2}\b", o["content"])


@pytest.mark.parametrize("case_id", CLASSIFIED)
def test_faithful_replay_generated_text_has_no_topic_violation(case_id):
    from baec_app.engine2.ai import generated_text_violation
    proposal = _run_replay(case_id, lambda i, ids, c=CASES[case_id]: faithful_output(i, c, ids))
    texts = ([p.rationale for p in proposal.dimension_proposals] + [c.concern for c in proposal.cross_cutting_observations]
             + list(proposal.unresolved_questions))
    assert all(generated_text_violation(x) is None for x in texts)
