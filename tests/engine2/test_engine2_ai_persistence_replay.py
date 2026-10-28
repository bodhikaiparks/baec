"""Stage G replay persistence: curated Stage F replays round-trip through the AI audit store.

Faithful replay here is persistence and pipeline validation using predetermined outputs. It is
not AI accuracy, model accuracy, or model performance.
"""

from contextlib import closing
from datetime import datetime

import pytest

from baec_app.engine2 import ai_persistence as a
from baec_app.engine2.ai import ModelResponse, validate_proposal_output
from tests.engine2.ai_replay import ai_case, faithful_output, overreaching_output
from tests.engine2.corpus_adapter import load_corpus, run_case
from tests.engine2.test_engine2_ai_evaluation import BLOCKED, CLASSIFIED

CORPUS = load_corpus()
CASES = {c["case_id"]: c for c in CORPUS["cases"]}
T = datetime.fromisoformat("2027-06-02T00:00:00+00:00")
ACTOR = "REVIEWER-AI-AUDIT"


def _persist(conn, ai_input, raw, attempt_id, artifact_id):
    a.record_ai_input(conn, ai_input, recorded_at=T, recorded_by=ACTOR)
    a.record_ai_attempt(conn, attempt_id=attempt_id, input_digest=ai_input.input_digest, provider="replay",
                        model="fixture-replay/v1", requested_at=T, requested_by=ACTOR)
    a.record_ai_response(conn, attempt_id, ModelResponse(raw, "replay", "fixture-replay/v1"), received_at=T, recorded_by=ACTOR)
    return a.record_ai_validation(conn, attempt_id, artifact_id=artifact_id, created_at=T, validated_at=T, validated_by=ACTOR)


@pytest.mark.parametrize("case_id", CLASSIFIED)
def test_faithful_replay_round_trips_exactly(case_id):
    ai_input, ids = ai_case(CORPUS, run_case(CORPUS, CASES[case_id]))
    raw = faithful_output(ai_input, CASES[case_id], ids)
    direct = validate_proposal_output(ModelResponse(raw, "replay", "fixture-replay/v1"), ai_input,
                                      artifact_id="AIP-1", created_at=T)
    with closing(a.open_ai_audit_database()) as conn:
        record = _persist(conn, ai_input, raw, "ATT-1", "AIP-1")
        assert record.status == "ACCEPTED"
        assert a.load_ai_input(conn, ai_input.input_digest) == ai_input
        assert a.load_ai_response(conn, "ATT-1").raw_text == raw
        assert a.load_ai_proposal(conn, "AIP-1") == direct == record.proposal
        assert a.load_ai_validation(conn, "ATT-1").proposal == direct
        a.verify_ai_database(conn)


def test_all_eighteen_faithful_replays_share_one_store():
    with closing(a.open_ai_audit_database()) as conn:
        for n, case_id in enumerate(CLASSIFIED):
            ai_input, ids = ai_case(CORPUS, run_case(CORPUS, CASES[case_id]))
            assert _persist(conn, ai_input, faithful_output(ai_input, CASES[case_id], ids), f"ATT-{n}", f"AIP-{n}").status == "ACCEPTED"
        counts = a.verify_ai_database(conn)
    assert len(CLASSIFIED) == 18 and counts["ai_proposals"] == 18 and counts["ai_inputs"] == 18


@pytest.mark.parametrize("case_id", CLASSIFIED)
def test_structurally_valid_overreaching_replay_is_persisted_not_judged(case_id):
    ai_input, _ = ai_case(CORPUS, run_case(CORPUS, CASES[case_id]))
    with closing(a.open_ai_audit_database()) as conn:
        record = _persist(conn, ai_input, overreaching_output(ai_input), "ATT-1", "AIP-1")
        assert record.status == "ACCEPTED"
        assert all(p.proposal.value == "PROPOSED_SUPPORTED" for p in a.load_ai_proposal(conn, "AIP-1").dimension_proposals)


@pytest.mark.parametrize("case_id", sorted(BLOCKED | {"HBR-CORR-041"}))
def test_blocked_cases_create_no_ai_audit_rows(case_id):
    run = run_case(CORPUS, CASES[case_id])
    assert run.candidate is None  # no Stage F input can exist, so nothing is ever recorded
    with closing(a.open_ai_audit_database()) as conn:
        counts = a.verify_ai_database(conn)
    assert all(v == 0 for k, v in counts.items() if k != "engine2_ai_meta")
