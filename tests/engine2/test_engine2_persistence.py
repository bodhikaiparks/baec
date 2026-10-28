"""Stage D persistence: schema, isolation, append-only history, integrity, tampering, and history."""

import inspect
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from baec_app.engine2 import persistence as p
from baec_app.engine2.classification import HumanCorrespondenceReview
from baec_app.engine2.domain import (
    CorrespondenceCandidate,
    CorrespondenceOutcome,
    DimensionFinding,
    RejectedSourceItem,
    SignalCandidate,
    Supersession,
    SupersessionBasis,
)
from baec_app.engine2.errors import MonitoringPlanActivationError
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement
from tests.engine2 import builders as b
from baec_app.domain.enums import StalenessStatus

T = b.T0 + timedelta(days=30)
ACTOR = "REVIEWER-T"
ACT = "ACT-T-1"
O = CorrespondenceOutcome
F = DimensionFinding


@pytest.fixture
def conn():
    with closing(p.open_engine2_database(":memory:")) as connection:
        yield connection


def _base(conn, plan=None):
    p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
    plan = plan or b.plan()
    p.record_monitoring_plan(conn, plan, recorded_at=T, recorded_by=ACTOR)
    return p.record_plan_activation(conn, plan.monitoring_plan_id, plan.plan_version, b.current(), activation_id=ACT,
                                    recorded_at=T, recorded_by=ACTOR)


def _store_review(conn, review: HumanCorrespondenceReview, activation=ACT):
    for o in review.ledger.observations:
        if not p._exists(conn, "observations", observation_id=o.observation_id):
            p.record_observation(conn, o, activation_id=activation, recorded_at=T, recorded_by=ACTOR)
    for s in review.ledger.supersessions:
        if not p._exists(conn, "supersessions", superseded_observation_id=s.superseded_observation_id):
            p.record_supersession(conn, s, recorded_at=T, recorded_by=ACTOR)
    for m in review.ledger.measurements:
        if not p._exists(conn, "derived_measurements", measurement_id=m.measurement_id):
            p.record_derived_measurement(conn, m, recorded_at=T, recorded_by=ACTOR)
    for s in review.candidate.signal_candidates:
        if not p._exists(conn, "signal_candidates", signal_candidate_id=s.signal_candidate_id):
            p.record_signal_candidate(conn, s, recorded_at=T, recorded_by=ACTOR)
    if not p._exists(conn, "correspondence_candidates", candidate_id=review.candidate.candidate_id):
        p.record_correspondence_candidate(conn, review.candidate, recorded_at=T, recorded_by=ACTOR)
    return p.record_review(conn, review, activation_id=activation, recorded_at=T, recorded_by=ACTOR)


def _simple_review(conn, **changes):
    active = _base(conn)
    return b.review(b.ledger(active), **changes)


def _tamper(conn, sql, params=(), *, checks=True, fks=True):
    """Simulate direct database tampering: bypass the append-only triggers (and optionally constraints)."""
    table = sql.split()[1] if sql.startswith("UPDATE") else sql.split()[2]
    for op in ("update", "delete"):
        conn.execute(f"DROP TRIGGER IF EXISTS {table}_no_{op}")
    if not checks:
        conn.execute("PRAGMA ignore_check_constraints = ON")
    if not fks:
        conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(sql, params)
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    conn.execute("PRAGMA foreign_keys = ON")


# --- schema, version, isolation ---------------------------------------------------


def test_new_database_is_engine2_schema_version_1(conn):
    assert dict(conn.execute("SELECT key, value FROM engine2_meta")) == {"engine": "BAEC_ENGINE_2", "schema_version": "1"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == set(p.TABLES)
    assert not tables & {"baec_records", "accounts", "interactions", "account_state_transitions"}


def test_engine1_database_is_refused_and_untouched(tmp_path):
    from baec_app.data.database import open_database
    path = tmp_path / "engine1.sqlite3"
    open_database(str(path)).close()
    before = path.read_bytes()
    with pytest.raises(p.Engine2SchemaError):
        p.open_engine2_database(str(path))
    assert path.read_bytes() == before


@pytest.mark.parametrize("tamper", ["PRAGMA user_version = 2", "UPDATE engine2_meta SET value = '2' WHERE key = 'schema_version'",
                                    "UPDATE engine2_meta SET value = 'BAEC_ENGINE_1' WHERE key = 'engine'"])
def test_unsupported_schema_version_fails_closed(tmp_path, tamper):
    path = str(tmp_path / "engine2.sqlite3")
    with closing(p.open_engine2_database(path)) as c:
        c.execute("DROP TRIGGER engine2_meta_no_update")
        c.execute(tamper)
    with pytest.raises(p.Engine2SchemaError):
        p.open_engine2_database(path)


def test_file_database_round_trips_after_reopen(tmp_path):
    path = str(tmp_path / "engine2.sqlite3")
    with closing(p.open_engine2_database(path)) as c:
        review = _simple_review(c)
        _store_review(c, review)
    with closing(p.open_engine2_database(path)) as c:
        assert p.load_review(c, "REV-T") == review


# --- write paths and domain boundaries -------------------------------------------------


def test_plan_and_sources_round_trip_exactly(conn):
    plan = b.plan(plan_fact_ids=frozenset({"PLAN-FACT-T"}))
    _base(conn, plan)
    assert p.load_monitoring_plan(conn, b.PLAN_ID, 1) == plan
    assert p.load_authorized_sources(conn) == {b.SRC: b.source()}


def test_refused_activation_is_recorded_and_blocks_capture(conn):
    p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
    p.record_monitoring_plan(conn, b.plan(), recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(MonitoringPlanActivationError):
        p.record_plan_activation(conn, b.PLAN_ID, 1, None, activation_id="ACT-REFUSED", recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(MonitoringPlanActivationError):
        p.load_active_plan(conn, "ACT-REFUSED")
    assert p.audit_events(conn)[-1].event_type == "PLAN_ACTIVATION_REFUSED"
    obs = b.observe(b.active())
    with pytest.raises(p.Engine2WriteRefused):
        p.record_observation(conn, obs, activation_id="ACT-REFUSED", recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(sqlite3.IntegrityError):  # the trigger is a backstop below the API
        conn.execute("INSERT INTO source_refusals VALUES ('MP-TEST-001', 1, 'X', 'ACT-REFUSED', NULL, 'r', 't', 'a')")
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_observation_round_trips_byte_for_byte(conn):
    active = _base(conn)
    text = "  Notice\r\nPrix augmentés de 12% — renouvellement \t"
    obs = b.observe(active, it=b.item(content=text))
    p.record_observation(conn, obs, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    loaded = p.load_observation(conn, obs.observation_id)
    assert loaded == obs and loaded.exact_evidence_content == text


def test_observation_from_a_source_the_plan_does_not_authorize_is_refused(conn):
    active = _base(conn)
    stray = b.observe(active)
    from dataclasses import replace
    other = replace(stray, source_id="SRC-OTHER", authorization_reference="SRC-OTHER")
    with pytest.raises(p.Engine2WriteRefused):
        p.record_observation(conn, other, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)


def test_source_refusal_is_audit_only(conn):
    _base(conn)
    refusal = RejectedSourceItem("PKT-X", b.PLAN_ID, 1, "no Authorized Source")
    p.record_source_refusal(conn, refusal, activation_id=ACT, source_reference=None, recorded_at=T, recorded_by=ACTOR)
    assert p.load_source_refusal(conn, b.PLAN_ID, 1, "PKT-X") == refusal
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert p.audit_events(conn)[-1].event_type == "SOURCE_ITEM_REFUSED"


def test_timestamps_and_actor_are_required_inputs(conn):
    with pytest.raises(p.Engine2WriteRefused):
        p.record_authorized_source(conn, b.source(), recorded_at=datetime(2027, 1, 1), recorded_by=ACTOR)
    with pytest.raises(p.Engine2WriteRefused):
        p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=" ")


# --- outcome snapshot (pattern B) -------------------------------------------------------


def test_outcome_is_computed_on_write_and_verified_on_read(conn):
    review = _simple_review(conn, sufficiency=b.SufficiencyFinding.UNKNOWN)
    assert _store_review(conn, review) is O.POSSIBLE_CORRESPONDENCE
    assert conn.execute("SELECT outcome_snapshot FROM reviews").fetchone()[0] == "POSSIBLE_CORRESPONDENCE"
    assert p.load_review(conn, "REV-T").outcome is O.POSSIBLE_CORRESPONDENCE


def test_no_api_accepts_an_outcome_or_deletes_history():
    for name, fn in inspect.getmembers(p, inspect.isfunction):
        if fn.__module__ != p.__name__:
            continue
        params = set(inspect.signature(fn).parameters)
        assert not params & {"outcome", "outcome_snapshot", "correspondence_outcome"}, name
        assert not any(word in name for word in ("delete", "remove", "purge", "truncate", "drop")), name


# --- append-only, duplicates, transactions ------------------------------------------------


def _full(conn):
    active = _base(conn)
    base = b.observe(active, "OBS-A", b.item("PKT-A", content="Baseline USD 100.00 per pack."))
    new = b.observe(active, "OBS-B", b.item("PKT-B", content="New price USD 112.00 per pack."))
    fix = b.observe(active, "OBS-C", b.item("PKT-C", content="CORRECTION: baseline USD 102.40 per pack."))
    dm = compute_derived_measurement("DM-1", Transformation.PERCENT_CHANGE_FROM_TWO_PRICES, (
        MeasurementInput(base, "baseline_price", Decimal("100.00"), "USD per pack"),
        MeasurementInput(new, "new_price", Decimal("112.00"), "USD per pack")), b.T0)
    for o in (base, new, fix):
        p.record_observation(conn, o, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    p.record_derived_measurement(conn, dm, recorded_at=T, recorded_by=ACTOR)
    p.record_supersession(conn, Supersession("OBS-A", "OBS-C", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),
                          recorded_at=T, recorded_by=ACTOR)
    review = b.review(b.ledger(active, [base, new, fix], measurements=(dm,), supersessions=(
        Supersession("OBS-A", "OBS-C", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),)),
        findings=b.findings(ev=("OBS-B",)))
    _store_review(conn, review)
    return active


@pytest.mark.parametrize("table,where", [
    ("observations", "observation_id = 'OBS-A'"), ("supersessions", "superseded_observation_id = 'OBS-A'"),
    ("derived_measurements", "measurement_id = 'DM-1'"), ("reviews", "review_id = 'REV-T'"),
    ("audit_events", "event_id = 1"), ("review_findings", "review_id = 'REV-T'"),
])
def test_authoritative_rows_refuse_update_and_delete(conn, table, where):
    _full(conn)
    column = conn.execute(f"SELECT name FROM pragma_table_info('{table}') WHERE pk = 0 LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(f"UPDATE {table} SET {column} = {column} WHERE {where}")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(f"DELETE FROM {table} WHERE {where}")


def test_insert_or_replace_cannot_overwrite_history(conn):
    _full(conn)
    with pytest.raises(sqlite3.DatabaseError, match="cannot be replaced"):
        conn.execute("INSERT OR REPLACE INTO observations SELECT * FROM observations WHERE observation_id = 'OBS-A'")
    assert p.load_observation(conn, "OBS-A").exact_evidence_content == "Baseline USD 100.00 per pack."


def test_duplicate_ids_fail_closed_even_with_different_content(conn):
    active = _base(conn)
    obs = b.observe(active)
    p.record_observation(conn, obs, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(p.DuplicateRecordError):
        p.record_observation(conn, obs, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    other = b.observe(active, it=b.item(content="Different text, same id."))
    with pytest.raises(p.DuplicateRecordError):
        p.record_observation(conn, other, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(p.DuplicateRecordError):
        p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
    assert p.load_observation(conn, obs.observation_id) == obs


def _fail_on(event_type, original):
    def wrapper(connection, kind, *args):
        if kind == event_type:
            raise RuntimeError("injected failure after the authoritative rows were written")
        return original(connection, kind, *args)
    return wrapper


def test_observation_and_its_audit_event_commit_together(conn, monkeypatch):
    active = _base(conn)
    monkeypatch.setattr(p, "_insert_audit_event", _fail_on("OBSERVATION_RECORDED", p._insert_audit_event))
    with pytest.raises(RuntimeError):
        p.record_observation(conn, b.observe(active), activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert not conn.in_transaction


def test_review_rows_roll_back_together(conn, monkeypatch):
    review = _simple_review(conn, baec_revalidation_required=True, revalidation_triggers=("entity changed",))
    monkeypatch.setattr(p, "_insert_audit_event", _fail_on("HUMAN_REVIEW_RECORDED", p._insert_audit_event))
    with pytest.raises(RuntimeError):
        _store_review(conn, review)
    for table in ("reviews", "review_findings", "review_finding_evidence", "review_checks",
                  "review_revalidation_triggers", "review_ledger_observations"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1  # earlier, separate commit


def test_supersession_and_measurement_require_stored_internal_references(conn):
    active = _base(conn)
    with pytest.raises(p.RecordNotFoundError):
        p.record_supersession(conn, Supersession("OBS-X", "OBS-Y", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),
                              recorded_at=T, recorded_by=ACTOR)
    ghost = b.observe(active, "OBS-MISSING", b.item("PKT-M"))  # valid in memory, never stored
    dm = compute_derived_measurement("DM-X", Transformation.BASIS_POINTS_TO_PERCENT,
                                     (MeasurementInput(ghost, "basis_points", Decimal("1100"), "basis points"),), b.T0)
    with pytest.raises(p.RecordNotFoundError):
        p.record_derived_measurement(conn, dm, recorded_at=T, recorded_by=ACTOR)
    sc = SignalCandidate("SC-X", b.PLAN_ID, 1, (ghost,), ACTOR)
    with pytest.raises(p.RecordNotFoundError):
        p.record_signal_candidate(conn, sc, recorded_at=T, recorded_by=ACTOR)
    cc = CorrespondenceCandidate("CC-X", b.BAEC_ID, b.PLAN_ID, 1, (SignalCandidate("SC-NOT-STORED", b.PLAN_ID, 1, (ghost,), ACTOR),))
    with pytest.raises(p.RecordNotFoundError):
        p.record_correspondence_candidate(conn, cc, recorded_at=T, recorded_by=ACTOR)
    assert active is not None and conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0] == 0


def test_review_requires_its_ledger_to_be_stored_unchanged(conn):
    review = _simple_review(conn)
    with pytest.raises(p.RecordNotFoundError):  # candidate not stored yet
        p.record_review(conn, review, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


# --- tampering fails closed ---------------------------------------------------------------


def test_tampered_observation_text_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE observations SET exact_evidence_content = exact_evidence_content || '!' WHERE observation_id = 'OBS-B'")
    with pytest.raises(p.Engine2IntegrityError):
        p.load_observation(conn, "OBS-B")
    with pytest.raises(p.Engine2IntegrityError):
        p.load_review(conn, "REV-T")


def test_tampered_observation_hash_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE observations SET content_sha256 = ? WHERE observation_id = 'OBS-B'", ("0" * 64,))
    with pytest.raises(p.Engine2IntegrityError):
        p.verify_database(conn)


def test_tampered_measurement_output_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE derived_measurements SET output_value = '12.5' WHERE measurement_id = 'DM-1'")
    with pytest.raises(p.Engine2IntegrityError):
        p.load_derived_measurement(conn, "DM-1")


def test_tampered_outcome_snapshot_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE reviews SET outcome_snapshot = 'NO_CORRESPONDENCE' WHERE review_id = 'REV-T'")
    with pytest.raises(p.Engine2IntegrityError, match="snapshot"):
        p.load_review(conn, "REV-T")


def test_invalid_dimension_vocabulary_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE review_findings SET dimension = 'PRICE_MATCH' WHERE review_id = 'REV-T' AND position = 0",
            checks=False, fks=False)
    with pytest.raises(p.Engine2IntegrityError):
        p.load_review(conn, "REV-T")


def test_invalid_sufficiency_vocabulary_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE reviews SET sufficiency = 'UNRESOLVED' WHERE review_id = 'REV-T'", checks=False)
    with pytest.raises(p.Engine2IntegrityError):
        p.load_review(conn, "REV-T")


def test_orphaned_internal_reference_fails_closed(conn):
    _full(conn)
    _tamper(conn, "DELETE FROM observations WHERE observation_id = 'OBS-B'", fks=False)
    with pytest.raises(p.Engine2IntegrityError, match="orphan"):
        p.verify_database(conn)
    with pytest.raises(p.Engine2IntegrityError):
        p.load_review(conn, "REV-T")


def test_removed_revalidation_trigger_fails_closed(conn):
    review = _simple_review(conn, baec_revalidation_required=True, revalidation_triggers=("the referenced entity has changed",))
    _store_review(conn, review)
    assert p.load_review(conn, "REV-T") == review
    _tamper(conn, "DELETE FROM review_revalidation_triggers WHERE review_id = 'REV-T'")
    with pytest.raises(p.Engine2IntegrityError):
        p.load_review(conn, "REV-T")


def test_changed_evidence_reference_fails_closed(conn):
    _full(conn)
    _tamper(conn, "UPDATE review_finding_evidence SET evidence_ref = 'OBS-A' WHERE review_id = 'REV-T' AND position = 0")
    with pytest.raises(p.Engine2IntegrityError):  # OBS-A is superseded in this review's ledger
        p.load_review(conn, "REV-T")


# --- historical reconstruction -------------------------------------------------------------


def test_correction_history_and_multiple_reviews_coexist(conn):
    active = _base(conn)
    first = b.observe(active, "OBS-1", b.item("PKT-1", content="Renewal pricing rises 12%."))
    p.record_observation(conn, first, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    early = b.review(b.ledger(active, [first]), review_id="REV-EARLY", findings=b.findings(ev=("OBS-1",)))
    assert _store_review(conn, early) is O.HUMAN_VERIFIED_CORRESPONDENCE
    early_rows = conn.execute("SELECT * FROM reviews WHERE review_id = 'REV-EARLY'").fetchall()

    fix = b.observe(active, "OBS-2", b.item("PKT-2", content="CORRECTION: renewal pricing rises 7%."))
    p.record_observation(conn, fix, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    sup = Supersession("OBS-1", "OBS-2", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION)
    p.record_supersession(conn, sup, recorded_at=T, recorded_by=ACTOR)
    later_ledger = b.ledger(active, [first, fix], supersessions=(sup,))
    later = b.review(later_ledger, review_id="REV-LATER", candidate=early.candidate,
                     findings=b.findings(ev=("OBS-2",), THRESHOLD_MATCH=(F.CONTRADICTED, ("OBS-2",))))
    assert _store_review(conn, later) is O.NO_CORRESPONDENCE

    assert p.review_ids_for_candidate(conn, "CC-T") == ["REV-EARLY", "REV-LATER"]
    assert p.load_review(conn, "REV-EARLY") == early and p.load_review(conn, "REV-EARLY").outcome is O.HUMAN_VERIFIED_CORRESPONDENCE
    assert p.load_review(conn, "REV-LATER").outcome is O.NO_CORRESPONDENCE
    assert conn.execute("SELECT * FROM reviews WHERE review_id = 'REV-EARLY'").fetchall() == early_rows
    assert p.load_observation(conn, "OBS-1") == first and p.load_observation(conn, "OBS-2") == fix
    reloaded = p.load_review(conn, "REV-LATER").ledger
    assert reloaded.usable_observation_ids == {"OBS-2"} and "OBS-1" in reloaded.observation_ids


def test_retracted_evidence_stays_historical(conn):
    active = _base(conn)
    a = b.observe(active, "OBS-1")
    r = b.observe(active, "OBS-2", b.item("PKT-2", content="Please disregard our earlier notice."))
    for o in (a, r):
        p.record_observation(conn, o, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    p.record_supersession(conn, Supersession("OBS-1", "OBS-2", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION, True),
                          recorded_at=T, recorded_by=ACTOR)
    assert p.load_supersession(conn, "OBS-1").retraction is True
    assert p.load_observation(conn, "OBS-1") == a


def test_past_but_authoritative_evidence_round_trips_as_usable(conn):
    active = _base(conn)
    old = b.observe(active, "OBS-1", b.item(content="Archived: pricing rose 12% at the 2025 renewal."))
    p.record_observation(conn, old, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
    review = b.review(b.ledger(active, [old]), findings=b.findings(
        ev=("OBS-1",), TIMING_MATCH=(F.CONTRADICTED, ("OBS-1",)), RENEWAL_OR_EFFECTIVE_DATE_MATCH=(F.CONTRADICTED, ("OBS-1",))))
    assert _store_review(conn, review) is O.NO_CORRESPONDENCE
    assert p.load_review(conn, "REV-T").ledger.usable_observation_ids == {"OBS-1"}


def test_measurement_history_remains_but_loses_usability(conn):
    _full(conn)
    review = p.load_review(conn, "REV-T")
    assert p.load_derived_measurement(conn, "DM-1").output_value == Decimal("12")
    assert "DM-1" in review.ledger.measurement_ids and "DM-1" not in review.ledger.usable_measurement_ids


def test_audit_history_is_ordered_and_minimal(conn):
    _full(conn)
    events = p.audit_events(conn)
    assert [e.event_id for e in events] == list(range(1, len(events) + 1))
    assert {e.event_type for e in events} >= {"AUTHORIZED_SOURCE_RECORDED", "MONITORING_PLAN_RECORDED",
                                              "PLAN_ACTIVATION_RECORDED", "OBSERVATION_RECORDED", "SUPERSESSION_RECORDED",
                                              "DERIVED_MEASUREMENT_RECORDED", "SIGNAL_CANDIDATE_RECORDED",
                                              "CORRESPONDENCE_CANDIDATE_RECORDED", "HUMAN_REVIEW_RECORDED"}
    assert all(e.recorded_at == T and e.actor == ACTOR for e in events)
    review_event = events[-1]
    assert review_event.details == {"baec_revalidation_required": False, "candidate_id": "CC-T",
                                    "outcome": "HUMAN_VERIFIED_CORRESPONDENCE", "plan_version": 1,
                                    "reviewer": "REVIEWER-T"}
    for e in events:  # no evidence text duplicated into audit details
        assert "Baseline USD" not in str(e.details)


# --- Monitoring Plan version history (Stage D fixtures; not part of the Stage B corpus) ------------

V1, V2 = "ACT-V1", "ACT-V2"
SRC2 = "SRC-TEST-02"


def _two_versions(conn):
    """Version 1 authorizes SRC only; version 2 authorizes SRC2 only and narrows timing context."""
    from dataclasses import replace
    p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
    p.record_authorized_source(conn, replace(b.source(SRC2), source_type="supplier notice"), recorded_at=T, recorded_by=ACTOR)
    v1 = b.plan()
    v2 = b.plan(plan_version=2, authorized_source_ids=frozenset({SRC2}), timing_context="at the 2027 renewal",
                authorization_reference="AUTH-MP-TEST-V2", authorized_at=T)
    p.record_monitoring_plan(conn, v1, recorded_at=T, recorded_by=ACTOR)
    snapshot_v1 = _plan_rows(conn, 1)
    p.record_monitoring_plan(conn, v2, recorded_at=T + timedelta(days=1), recorded_by=ACTOR)
    return v1, v2, snapshot_v1


def _plan_rows(conn, version):
    rows = {}
    for table in ("monitoring_plans", "plan_target_entities", "plan_dimensions", "plan_references", "plan_authorized_sources"):
        rows[table] = conn.execute(f"SELECT * FROM {table} WHERE monitoring_plan_id = ? AND plan_version = ? ORDER BY 1, 2, 3",
                                   (b.PLAN_ID, version)).fetchall()
    return rows


def test_a_b_c_two_immutable_versions_coexist_and_load_independently(conn):
    v1, v2, snapshot_v1 = _two_versions(conn)
    assert p.plan_versions(conn, b.PLAN_ID) == [1, 2]
    assert _plan_rows(conn, 1) == snapshot_v1  # byte/field-identical after version 2 exists
    assert p.load_monitoring_plan(conn, b.PLAN_ID, 1) == v1
    assert p.load_monitoring_plan(conn, b.PLAN_ID, 2) == v2
    with pytest.raises(p.RecordNotFoundError):
        p.load_monitoring_plan(conn, b.PLAN_ID, 3)


def test_d_activation_is_version_specific(conn):
    _two_versions(conn)
    active_v1 = p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(), activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    assert active_v1.plan.plan_version == 1
    obs = b.observe(b.active(b.plan()))  # captured from SRC under version 1's rules
    with pytest.raises(p.RecordNotFoundError):  # nothing activates version 2 implicitly
        p.load_active_plan(conn, V2)
    with pytest.raises(MonitoringPlanActivationError):
        p.record_plan_activation(conn, b.PLAN_ID, 2, b.current(status=StalenessStatus.STALE), activation_id=V2,
                                 recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(p.Engine2WriteRefused):
        p.record_observation(conn, obs, activation_id=V2, recorded_at=T, recorded_by=ACTOR)
    p.record_observation(conn, obs, activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    assert p.observation_plan_version(conn, obs.observation_id) == (b.PLAN_ID, 1)


def test_e_source_authorization_is_version_specific(conn):
    _two_versions(conn)
    p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(), activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    p.record_plan_activation(conn, b.PLAN_ID, 2, b.current(), activation_id=V2, recorded_at=T, recorded_by=ACTOR)
    from_src = b.observe(b.active(b.plan()), "OBS-SRC1")
    from baec_app.engine2.errors import SourceNotAuthorizedError
    v2_active = p.load_active_plan(conn, V2)
    with pytest.raises(SourceNotAuthorizedError):  # in memory: SRC is authorized for v1 only
        b.observe(v2_active, "OBS-SRC2")
    from dataclasses import replace
    forged = replace(from_src, observation_id="OBS-FORGED", plan_version=2)  # bypasses capture
    with pytest.raises(p.Engine2WriteRefused, match="not authorized"):
        p.record_observation(conn, forged, activation_id=V2, recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(p.Engine2WriteRefused, match="v1, but activation"):
        p.record_observation(conn, from_src, activation_id=V2, recorded_at=T, recorded_by=ACTOR)
    p.record_observation(conn, from_src, activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(sqlite3.IntegrityError):  # the composite foreign key is the backstop
        conn.execute("INSERT INTO observations SELECT 'OBS-RAW', monitoring_plan_id, 2, 'ACT-V2', source_id, source_type, "
                     "observed_at, published_at, effective_at, exact_evidence_content, source_locator, provenance, "
                     "content_sha256, acquisition_method, authorization_reference, evidence_event_id, recorded_at, recorded_by "
                     "FROM observations WHERE observation_id = 'OBS-SRC1'")


def test_f_and_j_version_1_evidence_cannot_join_version_2_records(conn):
    _two_versions(conn)
    p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(), activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    p.record_plan_activation(conn, b.PLAN_ID, 2, b.current(), activation_id=V2, recorded_at=T, recorded_by=ACTOR)
    obs = b.observe(b.active(b.plan()))
    p.record_observation(conn, obs, activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    from baec_app.engine2.errors import Engine2ValidationError
    with pytest.raises(Engine2ValidationError, match="never mix"):  # refused in memory, before persistence
        SignalCandidate("SC-V2", b.PLAN_ID, 2, (obs,), ACTOR)
    p.record_signal_candidate(conn, SignalCandidate("SC-V1", b.PLAN_ID, 1, (obs,), ACTOR), recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(Engine2ValidationError, match="never mix"):
        CorrespondenceCandidate("CC-V2", b.BAEC_ID, b.PLAN_ID, 2, (p.load_signal_candidate(conn, "SC-V1"),))
    dm = compute_derived_measurement("DM-V1", Transformation.BASIS_POINTS_TO_PERCENT,
                                     (MeasurementInput(obs, "basis_points", Decimal("1100"), "basis points"),), b.T0)
    assert dm.plan_version == 1  # derived from its input; it cannot be filed under version 2
    p.record_derived_measurement(conn, dm, recorded_at=T, recorded_by=ACTOR)
    assert conn.execute("SELECT plan_version FROM derived_measurements WHERE measurement_id = 'DM-V1'").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):  # direct SQL mixing is refused by composite keys
        conn.execute("INSERT INTO signal_candidates VALUES ('SC-RAW', 'MP-TEST-001', 2, 'x', 't', 'a')")
        conn.execute("INSERT INTO signal_candidate_observations VALUES ('SC-RAW', 'MP-TEST-001', 2, 0, ?)", (obs.observation_id,))


def test_g_h_version_1_review_is_unchanged_by_version_2(conn):
    _two_versions(conn)
    active_v1 = p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(), activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    review_v1 = b.review(b.ledger(active_v1), review_id="REV-V1")
    assert _store_review(conn, review_v1, activation=V1) is O.HUMAN_VERIFIED_CORRESPONDENCE
    rows_before = conn.execute("SELECT * FROM reviews WHERE review_id = 'REV-V1'").fetchall()

    active_v2 = p.record_plan_activation(conn, b.PLAN_ID, 2, b.current(), activation_id=V2, recorded_at=T, recorded_by=ACTOR)
    from dataclasses import replace
    item2 = replace(b.item("PKT-V2", content="Supplier notice: pricing rises 9% at renewal."), source_id=SRC2)
    from baec_app.engine2.domain import capture_observation
    from baec_app.domain.enums import ProvenanceCategory
    obs2 = capture_observation(active_v2, {SRC2: p.load_authorized_source(conn, SRC2)}, item2, observation_id="OBS-V2",
                               provenance=ProvenanceCategory.EXTERNAL_EVIDENCE,
                               content_sha256=__import__("hashlib").sha256(item2.content.encode()).hexdigest(),
                               source_locator="PKT-V2", evidence_event_id="EVT-V2")
    led2 = b.ledger(active_v2, [obs2])
    signal2 = SignalCandidate("SC-V2", b.PLAN_ID, 2, (obs2,), ACTOR)
    review_v2 = b.review(led2, review_id="REV-V2",
                         candidate=CorrespondenceCandidate("CC-V2", b.BAEC_ID, b.PLAN_ID, 2, (signal2,)),
                         findings=b.findings(ev=("OBS-V2",), THRESHOLD_MATCH=(F.CONTRADICTED, ("OBS-V2",))))
    assert _store_review(conn, review_v2, activation=V2) is O.NO_CORRESPONDENCE

    reloaded = p.load_review(conn, "REV-V1")
    assert reloaded == review_v1 and reloaded.ledger.plan.plan_version == 1
    assert reloaded.ledger.plan.timing_context == "when our agreement renews"
    assert reloaded.outcome is O.HUMAN_VERIFIED_CORRESPONDENCE
    assert conn.execute("SELECT * FROM reviews WHERE review_id = 'REV-V1'").fetchall() == rows_before
    assert p.load_review(conn, "REV-V2").ledger.plan.plan_version == 2
    p.verify_database(conn)


def test_i_duplicate_plan_version_is_refused(conn):
    _two_versions(conn)
    with pytest.raises(p.DuplicateRecordError):
        p.record_monitoring_plan(conn, b.plan(timing_context="a different version-1 text"), recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(sqlite3.DatabaseError, match="cannot be replaced"):
        conn.execute("INSERT OR REPLACE INTO monitoring_plans SELECT * FROM monitoring_plans WHERE plan_version = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE monitoring_plans SET timing_context = 'x' WHERE plan_version = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM monitoring_plans WHERE plan_version = 1")
    assert p.load_monitoring_plan(conn, b.PLAN_ID, 1) == b.plan()


def test_j_tampered_review_version_fails_closed(conn):
    _two_versions(conn)
    active_v1 = p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(), activation_id=V1, recorded_at=T, recorded_by=ACTOR)
    _store_review(conn, b.review(b.ledger(active_v1), review_id="REV-V1"), activation=V1)
    _tamper(conn, "UPDATE reviews SET plan_version = 2 WHERE review_id = 'REV-V1'", fks=False)
    with pytest.raises(p.Engine2IntegrityError):
        p.load_review(conn, "REV-V1")
