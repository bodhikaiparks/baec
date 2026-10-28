"""Stage E: exact Monitoring Plan version identity in memory.

Cross-version mixing must fail in the domain before persistence is involved;
persistence remains an independent second boundary.
"""

import hashlib
import inspect
import re
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from baec_app.domain.enums import ProvenanceCategory
from baec_app.engine2 import persistence as p
from baec_app.engine2.domain import (
    CorrespondenceCandidate,
    CorrespondenceOutcome,
    DimensionFinding,
    Observation,
    SignalCandidate,
    Supersession,
    SupersessionBasis,
    activate_monitoring_plan,
    capture_observation,
    plan_key,
)
from baec_app.engine2.errors import Engine2ValidationError, ReviewInconsistentError
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement
from tests.engine2 import builders as b
from tests.engine2.corpus_adapter import REPO

T = b.T0 + timedelta(days=30)
ACTOR = "REVIEWER-T"
F, O = DimensionFinding, CorrespondenceOutcome

V1_PLAN = b.plan()
V2_PLAN = b.plan(plan_version=2, authorization_reference="AUTH-MP-TEST-V2")


def _active(plan):
    return activate_monitoring_plan(plan, b.current())


def _obs(plan, oid, content=None):
    return b.observe(_active(plan), oid, b.item(f"PKT-{oid}", content=content or f"Notice {oid}: pricing rises 12%."))


def _bps(*observations):
    return compute_derived_measurement("DM-X", Transformation.BASIS_POINTS_TO_PERCENT, tuple(
        MeasurementInput(o, "basis_points", Decimal("1100"), "basis points") for o in observations), b.T0)


# --- 1, 2: Observation identity -----------------------------------------------------


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0, None])
def test_1_observation_refuses_invalid_plan_version(version):
    with pytest.raises(Engine2ValidationError):
        replace(_obs(V1_PLAN, "OBS-1"), plan_version=version)


def test_2_observation_requires_explicit_plan_version():
    params = inspect.signature(Observation).parameters
    assert params["plan_version"].default is inspect.Parameter.empty
    fields = {f: getattr(_obs(V1_PLAN, "OBS-1"), f) for f in Observation.__dataclass_fields__}
    fields.pop("plan_version")
    with pytest.raises(TypeError):
        Observation(**fields)
    assert _obs(V1_PLAN, "OBS-1").plan_version == 1 and _obs(V2_PLAN, "OBS-2").plan_version == 2


# --- 3-8: Signal and Correspondence Candidates ------------------------------------------


def test_3_version_1_observation_cannot_enter_version_2_signal_candidate():
    with pytest.raises(Engine2ValidationError, match="never mix"):
        SignalCandidate("SC", b.PLAN_ID, 2, (_obs(V1_PLAN, "OBS-1"),), ACTOR)


def test_4_mixed_version_observations_cannot_form_one_signal_candidate():
    mixed = (_obs(V1_PLAN, "OBS-1"), _obs(V2_PLAN, "OBS-2"))
    for version in (1, 2):
        with pytest.raises(Engine2ValidationError, match="never mix"):
            SignalCandidate("SC", b.PLAN_ID, version, mixed, ACTOR)


def test_5_version_1_signal_cannot_enter_version_2_candidate():
    signal = SignalCandidate("SC-1", b.PLAN_ID, 1, (_obs(V1_PLAN, "OBS-1"),), ACTOR)
    with pytest.raises(Engine2ValidationError, match="never mix"):
        CorrespondenceCandidate("CC", b.BAEC_ID, b.PLAN_ID, 2, (signal,))


def test_6_mixed_version_signals_cannot_form_one_candidate():
    s1 = SignalCandidate("SC-1", b.PLAN_ID, 1, (_obs(V1_PLAN, "OBS-1"),), ACTOR)
    s2 = SignalCandidate("SC-2", b.PLAN_ID, 2, (_obs(V2_PLAN, "OBS-2"),), ACTOR)
    for version in (1, 2):
        with pytest.raises(Engine2ValidationError, match="never mix"):
            CorrespondenceCandidate("CC", b.BAEC_ID, b.PLAN_ID, version, (s1, s2))


def test_7_same_plan_and_version_is_valid():
    o1, o2 = _obs(V2_PLAN, "OBS-1"), _obs(V2_PLAN, "OBS-2")
    signal = SignalCandidate("SC", b.PLAN_ID, 2, (o1, o2), ACTOR)
    candidate = CorrespondenceCandidate("CC", b.BAEC_ID, b.PLAN_ID, 2, (signal,))
    assert plan_key(candidate) == plan_key(signal) == plan_key(o1) == (b.PLAN_ID, 2)
    assert candidate.observation_ids == {"OBS-1", "OBS-2"}


def test_8_different_plan_ids_remain_invalid():
    other_plan = b.plan(monitoring_plan_id="MP-OTHER")
    with pytest.raises(Engine2ValidationError):
        SignalCandidate("SC", b.PLAN_ID, 1, (_obs(other_plan, "OBS-1"),), ACTOR)
    signal = SignalCandidate("SC", "MP-OTHER", 1, (_obs(other_plan, "OBS-1"),), ACTOR)
    with pytest.raises(Engine2ValidationError):
        CorrespondenceCandidate("CC", b.BAEC_ID, b.PLAN_ID, 1, (signal,))


# --- 9, 10: Supersession -----------------------------------------------------------------


def test_9_supersession_across_versions_fails():
    old, new = _obs(V1_PLAN, "OBS-1"), _obs(V2_PLAN, "OBS-2")
    sup = Supersession("OBS-1", "OBS-2", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION)
    for active in (_active(V1_PLAN), _active(V2_PLAN)):
        with pytest.raises(Engine2ValidationError):
            b.ledger(active, [old, new], supersessions=(sup,))
    with closing(p.open_engine2_database(":memory:")) as conn:
        _two_versions(conn)
        p.record_observation(conn, old, activation_id="ACT-V1", recorded_at=T, recorded_by=ACTOR)
        p.record_observation(conn, new, activation_id="ACT-V2", recorded_at=T, recorded_by=ACTOR)
        with pytest.raises(p.Engine2WriteRefused, match="same plan version"):
            p.record_supersession(conn, sup, recorded_at=T, recorded_by=ACTOR)


def test_10_same_version_supersession_is_valid():
    old, new = _obs(V2_PLAN, "OBS-1"), _obs(V2_PLAN, "OBS-2", "CORRECTION: pricing rises 7%.")
    sup = Supersession("OBS-1", "OBS-2", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION)
    ledger = b.ledger(_active(V2_PLAN), [old, new], supersessions=(sup,))
    assert ledger.usable_observation_ids == {"OBS-2"}


# --- 11, 12: Derived Measurements and the evidence ledger -----------------------------------


def test_11_derived_measurement_takes_its_version_from_inputs_and_cannot_mix():
    assert plan_key(_bps(_obs(V2_PLAN, "OBS-1"))) == (b.PLAN_ID, 2)
    with pytest.raises(Engine2ValidationError, match="never mix"):
        compute_derived_measurement("DM-X", Transformation.PERCENT_CHANGE_FROM_TWO_PRICES, (
            MeasurementInput(_obs(V1_PLAN, "OBS-1"), "baseline_price", Decimal("100"), "USD per pack"),
            MeasurementInput(_obs(V2_PLAN, "OBS-2"), "new_price", Decimal("112"), "USD per pack")), b.T0)
    v1 = _obs(V1_PLAN, "OBS-1")
    with pytest.raises(Engine2ValidationError, match="never mix"):  # a v1 measurement cannot be used in a v2 ledger
        b.ledger(_active(V2_PLAN), [_obs(V2_PLAN, "OBS-2")], measurements=(_bps(v1),))
    with pytest.raises(Engine2ValidationError):  # nor can a measurement whose input object is not the ledger's own
        b.ledger(_active(V1_PLAN), [_obs(V1_PLAN, "OBS-1", "Different text, same id.")], measurements=(_bps(v1),))


def test_12_evidence_ledger_refuses_cross_version_observations():
    with pytest.raises(Engine2ValidationError, match="never mix"):
        b.ledger(_active(V2_PLAN), [_obs(V2_PLAN, "OBS-2"), _obs(V1_PLAN, "OBS-1")])
    assert b.ledger(_active(V2_PLAN), [_obs(V2_PLAN, "OBS-2")]).plan.plan_version == 2


# --- 13: Human Review ------------------------------------------------------------------------


def test_13_review_cannot_consume_cross_version_evidence():
    v2_ledger = b.ledger(_active(V2_PLAN), [_obs(V2_PLAN, "OBS-T-01")])
    v1_signal = SignalCandidate("SC", b.PLAN_ID, 1, (_obs(V1_PLAN, "OBS-T-01"),), ACTOR)
    v1_candidate = CorrespondenceCandidate("CC", b.BAEC_ID, b.PLAN_ID, 1, (v1_signal,))
    with pytest.raises(ReviewInconsistentError, match="exact plan version"):
        b.review(v2_ledger, candidate=v1_candidate)
    assert b.review(v2_ledger).outcome is O.HUMAN_VERIFIED_CORRESPONDENCE  # same version is fine


# --- 14-16, 20: persistence alignment and version history ------------------------------------


def _two_versions(conn):
    p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
    for plan, activation in ((V1_PLAN, "ACT-V1"), (V2_PLAN, "ACT-V2")):
        p.record_monitoring_plan(conn, plan, recorded_at=T, recorded_by=ACTOR)
        p.record_plan_activation(conn, b.PLAN_ID, plan.plan_version, b.current(), activation_id=activation,
                                 recorded_at=T, recorded_by=ACTOR)


def _store(conn, review, activation):
    for o in review.ledger.observations:
        p.record_observation(conn, o, activation_id=activation, recorded_at=T, recorded_by=ACTOR)
    for s in review.candidate.signal_candidates:
        p.record_signal_candidate(conn, s, recorded_at=T, recorded_by=ACTOR)
    p.record_correspondence_candidate(conn, review.candidate, recorded_at=T, recorded_by=ACTOR)
    return p.record_review(conn, review, activation_id=activation, recorded_at=T, recorded_by=ACTOR)


def _review(plan, oid, review_id, **findings):
    active = _active(plan)
    obs = _obs(plan, oid)
    signal = SignalCandidate(f"SC-{review_id}", b.PLAN_ID, plan.plan_version, (obs,), ACTOR)
    candidate = CorrespondenceCandidate(f"CC-{review_id}", b.BAEC_ID, b.PLAN_ID, plan.plan_version, (signal,))
    return b.review(b.ledger(active, [obs]), review_id=review_id, candidate=candidate,
                    findings=b.findings(ev=(oid,), **findings))


def test_14_16_20_versions_coexist_round_trip_and_never_confuse():
    with closing(p.open_engine2_database(":memory:")) as conn:
        _two_versions(conn)
        r1 = _review(V1_PLAN, "OBS-V1", "REV-V1")
        assert _store(conn, r1, "ACT-V1") is O.HUMAN_VERIFIED_CORRESPONDENCE
        before = conn.execute("SELECT * FROM reviews WHERE review_id = 'REV-V1'").fetchall()
        r2 = _review(V2_PLAN, "OBS-V2", "REV-V2", THRESHOLD_MATCH=(F.CONTRADICTED, ("OBS-V2",)))
        assert _store(conn, r2, "ACT-V2") is O.NO_CORRESPONDENCE

        loaded1, loaded2 = p.load_review(conn, "REV-V1"), p.load_review(conn, "REV-V2")
        assert loaded1 == r1 and loaded2 == r2
        for review, version in ((loaded1, 1), (loaded2, 2)):
            assert review.ledger.plan.plan_version == version
            assert {o.plan_version for o in review.ledger.observations} == {version}
            assert plan_key(review.candidate) == (b.PLAN_ID, version)
            assert all(s.plan_version == version for s in review.candidate.signal_candidates)
        assert p.load_observation(conn, "OBS-V1").plan_version == 1
        assert p.load_signal_candidate(conn, "SC-REV-V2").plan_version == 2
        assert conn.execute("SELECT * FROM reviews WHERE review_id = 'REV-V1'").fetchall() == before
        assert loaded1.outcome is O.HUMAN_VERIFIED_CORRESPONDENCE
        # version-1 evidence cannot enter version-2 candidates, before any persistence call
        with pytest.raises(Engine2ValidationError, match="never mix"):
            SignalCandidate("SC-BAD", b.PLAN_ID, 2, (p.load_observation(conn, "OBS-V1"),), ACTOR)


def test_15_persistence_cannot_file_an_object_under_another_version():
    with closing(p.open_engine2_database(":memory:")) as conn:
        _two_versions(conn)
        v1_obs = _obs(V1_PLAN, "OBS-1")
        with pytest.raises(p.Engine2WriteRefused, match="belongs to"):
            p.record_observation(conn, v1_obs, activation_id="ACT-V2", recorded_at=T, recorded_by=ACTOR)
        p.record_observation(conn, v1_obs, activation_id="ACT-V1", recorded_at=T, recorded_by=ACTOR)
        assert conn.execute("SELECT plan_version FROM observations").fetchall() == [(1,)]
        r1 = _review(V1_PLAN, "OBS-9", "REV-9")
        for o in r1.ledger.observations:
            p.record_observation(conn, o, activation_id="ACT-V1", recorded_at=T, recorded_by=ACTOR)
        for s in r1.candidate.signal_candidates:
            p.record_signal_candidate(conn, s, recorded_at=T, recorded_by=ACTOR)
        p.record_correspondence_candidate(conn, r1.candidate, recorded_at=T, recorded_by=ACTOR)
        with pytest.raises(p.Engine2WriteRefused):  # a v1 review cannot be filed under the v2 activation
            p.record_review(conn, r1, activation_id="ACT-V2", recorded_at=T, recorded_by=ACTOR)
        for name in ("record_observation", "record_derived_measurement", "record_signal_candidate",
                     "record_correspondence_candidate", "record_review", "record_supersession"):
            assert "plan_version" not in inspect.signature(getattr(p, name)).parameters, name


# --- no "latest version" behavior ----------------------------------------------------------------


def test_production_code_has_no_latest_version_logic():
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted((REPO / "baec_app/engine2").glob("*.py")))
    for pattern in (r"\blatest\b", r"max\(\s*[^)]*plan_version", r"ORDER BY plan_version DESC", r"plan_version\s*=\s*1\b",
                    r"plan_version\s*:\s*int\s*=", r"plan_version\s*\+\s*1"):
        assert not re.search(pattern, source, re.I), pattern


def test_corpus_plan_fixture_supplies_its_version_explicitly():
    from tests.engine2.corpus_adapter import load_corpus, plan_for
    corpus = load_corpus()
    assert corpus["monitoring_plan"]["plan_version"] == 1
    assert plan_for(corpus, corpus["cases"][0]).plan_version == 1


def test_capture_stamps_the_active_plan_version():
    item = b.item()
    obs = capture_observation(_active(V2_PLAN), {b.SRC: b.source()}, item, observation_id="OBS-C",
                              provenance=ProvenanceCategory.EXTERNAL_EVIDENCE,
                              content_sha256=hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                              source_locator="x", evidence_event_id="EVT-C")
    assert plan_key(obs) == (b.PLAN_ID, 2)


# --- source refusals carry exact plan version identity (Stage E final revision) --------------------

from baec_app.engine2.domain import RejectedSourceItem, reject_source_item  # noqa: E402


def test_r1_r2_refusal_requires_plan_id_and_version():
    params = inspect.signature(RejectedSourceItem).parameters
    assert params["monitoring_plan_id"].default is inspect.Parameter.empty
    assert params["plan_version"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        RejectedSourceItem("PKT-1", "no Authorized Source")
    for blank in ("", "  ", None):
        with pytest.raises(Engine2ValidationError):
            RejectedSourceItem("PKT-1", blank, 1, "no Authorized Source")


@pytest.mark.parametrize("version", [0, -3, True, False, 1.0, 2.5, "1", None])
def test_r3_r4_refusal_rejects_invalid_versions(version):
    with pytest.raises(Engine2ValidationError):
        RejectedSourceItem("PKT-1", b.PLAN_ID, version, "no Authorized Source")


def test_r5_r6_r7_ledger_admits_only_same_plan_same_version_refusals():
    obs = _obs(V2_PLAN, "OBS-2")
    with pytest.raises(Engine2ValidationError, match="never mix"):
        b.ledger(_active(V2_PLAN), [obs], rejected_items=(RejectedSourceItem("PKT-1", b.PLAN_ID, 1, "refused"),))
    with pytest.raises(Engine2ValidationError, match="never mix"):
        b.ledger(_active(V2_PLAN), [obs], rejected_items=(RejectedSourceItem("PKT-1", "MP-OTHER", 2, "refused"),))
    ok = b.ledger(_active(V2_PLAN), [obs], rejected_items=(RejectedSourceItem("PKT-1", b.PLAN_ID, 2, "refused"),))
    assert plan_key(ok.rejected_items[0]) == (b.PLAN_ID, 2)


def test_r8_capture_refusal_inherits_the_active_plan_identity():
    from baec_app.engine2.errors import SourceNotAuthorizedError
    active = _active(V2_PLAN)
    item = b.item("PKT-U", source_id=None)
    with pytest.raises(SourceNotAuthorizedError) as refused:
        b.observe(active, it=item)
    refusal = reject_source_item(active, item, str(refused.value))
    assert plan_key(refusal) == (b.PLAN_ID, 2) and refusal.item_id == "PKT-U"
    assert "plan_version" not in inspect.signature(reject_source_item).parameters


def test_r9_r10_r11_persistence_uses_the_refusals_own_version():
    with closing(p.open_engine2_database(":memory:")) as conn:
        _two_versions(conn)
        v1 = RejectedSourceItem("PKT-1", b.PLAN_ID, 1, "refused under v1")
        v2 = RejectedSourceItem("PKT-1", b.PLAN_ID, 2, "refused under v2")
        with pytest.raises(p.Engine2WriteRefused, match="belongs to"):
            p.record_source_refusal(conn, v1, activation_id="ACT-V2", source_reference=None, recorded_at=T, recorded_by=ACTOR)
        p.record_source_refusal(conn, v1, activation_id="ACT-V1", source_reference=None, recorded_at=T, recorded_by=ACTOR)
        p.record_source_refusal(conn, v2, activation_id="ACT-V2", source_reference=None, recorded_at=T, recorded_by=ACTOR)
        rows = conn.execute("SELECT plan_version, reason FROM source_refusals ORDER BY plan_version").fetchall()
        assert rows == [(1, "refused under v1"), (2, "refused under v2")]
        assert p.load_source_refusal(conn, b.PLAN_ID, 1, "PKT-1") == v1
        assert p.load_source_refusal(conn, b.PLAN_ID, 2, "PKT-1") == v2
        assert "plan_version" not in inspect.signature(p.record_source_refusal).parameters


def _corpus_case(case_id):
    from tests.engine2.corpus_adapter import load_corpus
    corpus = load_corpus()
    return corpus, next(c for c in corpus["cases"] if c["case_id"] == case_id)


@pytest.mark.parametrize("case_id", ["HBR-CORR-040", "HBR-CORR-041"])
def test_r12_unauthorized_cases_stay_blocked_with_versioned_refusals(case_id):
    from tests.engine2.corpus_adapter import run_case
    from tests.engine2.test_engine2_persistence_conformance import persist_case
    corpus, case = _corpus_case(case_id)
    run = run_case(corpus, case)
    assert run.observations == [] and run.review is None and run.outcome is None
    assert [plan_key(r) for r in run.rejected] == [("MP-HBR-001", 1)] * len(case["source_packets"])
    conn, review_id = persist_case(case)
    with closing(conn):
        assert review_id is None
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert conn.execute("SELECT DISTINCT monitoring_plan_id, plan_version FROM source_refusals").fetchall() == [("MP-HBR-001", 1)]


def test_r13_case_057_creates_no_refusal():
    from tests.engine2.corpus_adapter import run_case
    from tests.engine2.test_engine2_persistence_conformance import persist_case
    corpus, case = _corpus_case("HBR-CORR-057")
    run = run_case(corpus, case)
    assert run.activation_error is not None and run.rejected == [] and run.observations == [] and run.outcome is None
    conn, review_id = persist_case(case)
    with closing(conn):
        assert review_id is None
        for table in ("source_refusals", "observations", "signal_candidates", "correspondence_candidates", "reviews"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        assert conn.execute("SELECT result FROM plan_activations").fetchall() == [("REFUSED",)]
