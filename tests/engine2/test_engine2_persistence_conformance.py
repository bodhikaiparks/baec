"""Stage D conformance: all 64 locked Stage B cases survive a round trip through Engine 2 persistence.

Each case is built from corpus inputs by the Stage C adapter, persisted into a
fresh in-memory Engine 2 database, reloaded through the domain constructors,
and classified again from the reloaded review. Oracle fields are read only
afterwards, for comparison.
"""

from collections import Counter
from contextlib import closing
from datetime import datetime

import pytest

from baec_app.engine2 import persistence as p
from baec_app.engine2.domain import CorrespondenceOutcome
from baec_app.engine2.errors import MonitoringPlanActivationError
from tests.engine2.corpus_adapter import authorized_sources, currentness_for, load_corpus, plan_for, run_case

CORPUS = load_corpus()
CASES = CORPUS["cases"]
T = datetime.fromisoformat("2027-06-01T00:00:00+00:00")  # fixed recording time; no wall clock
ACTOR = "REVIEWER-B (synthetic human alias)"
ACTIVATION = "ACT-MP-HBR-001-1"


def persist_case(case):
    """Persist everything admissible for one case. Returns (connection, review_id or None)."""
    conn = p.open_engine2_database(":memory:")
    for source in authorized_sources(CORPUS).values():
        p.record_authorized_source(conn, source, recorded_at=T, recorded_by=ACTOR)
    plan = plan_for(CORPUS, case)
    p.record_monitoring_plan(conn, plan, recorded_at=T, recorded_by=ACTOR)
    try:
        p.record_plan_activation(conn, plan.monitoring_plan_id, plan.plan_version, currentness_for(CORPUS, case),
                                 activation_id=ACTIVATION, recorded_at=T, recorded_by=ACTOR)
    except MonitoringPlanActivationError:
        return conn, None
    run = run_case(CORPUS, case)
    packets = {pk["packet_id"]: pk for pk in case["source_packets"]}
    for o in run.observations:
        p.record_observation(conn, o, activation_id=ACTIVATION, recorded_at=T, recorded_by=ACTOR)
    for r in run.rejected:
        p.record_source_refusal(conn, r, activation_id=ACTIVATION, source_reference=packets[r.item_id]["source_id"],
                                recorded_at=T, recorded_by=ACTOR)
    for s in run.ledger.supersessions:
        p.record_supersession(conn, s, recorded_at=T, recorded_by=ACTOR)
    for m in run.measurements:
        p.record_derived_measurement(conn, m, recorded_at=T, recorded_by=ACTOR)
    if run.review is None:
        return conn, None
    for s in run.candidate.signal_candidates:
        p.record_signal_candidate(conn, s, recorded_at=T, recorded_by=ACTOR)
    p.record_correspondence_candidate(conn, run.candidate, recorded_at=T, recorded_by=ACTOR)
    p.record_review(conn, run.review, activation_id=ACTIVATION, recorded_at=T, recorded_by=ACTOR)
    return conn, run.review.review_id


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_case_survives_persistence_round_trip(case):
    conn, review_id = persist_case(case)
    with closing(conn):
        _check_round_trip(case, conn, review_id)


def _check_round_trip(case, conn, review_id):
    counts = p.verify_database(conn)
    expected = case["expected_correspondence_outcome"]
    if expected is None:
        assert review_id is None and counts["reviews"] == 0 and counts["observations"] == 0
        if case["expected_processing_disposition"].startswith("Monitoring Plan not activated"):
            with pytest.raises(MonitoringPlanActivationError):
                p.load_active_plan(conn, ACTIVATION)
            assert counts["source_refusals"] == 0
            assert [e.event_type for e in p.audit_events(conn)][-1] == "PLAN_ACTIVATION_REFUSED"
        else:
            assert counts["source_refusals"] == len(case["source_packets"])
        return
    review = p.load_review(conn, review_id)
    run = run_case(CORPUS, case)
    assert review == run.review  # exact reconstruction of every field and ledger member
    assert review.outcome is CorrespondenceOutcome(expected)
    stored = conn.execute("SELECT outcome_snapshot FROM reviews WHERE review_id = ?", (review_id,)).fetchone()[0]
    assert stored == expected
    for o in case["raw_observations_expected"]:
        assert p.load_observation(conn, o["observation_id"]).exact_evidence_content == o["exact_evidence_content"]
    assert counts["source_refusals"] == len(case["packets_not_admitted"])


def test_persisted_outcome_distribution_matches_the_locked_corpus():
    derived = Counter()
    for case in CASES:
        conn, review_id = persist_case(case)
        with closing(conn):
            derived[p.load_review(conn, review_id).outcome.value if review_id else None] += 1
    assert derived == {"NO_CORRESPONDENCE": 20, "POSSIBLE_CORRESPONDENCE": 32, "HUMAN_VERIFIED_CORRESPONDENCE": 9, None: 3}
