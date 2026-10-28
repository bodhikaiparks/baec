"""V0.4 Phase 3B: Engine 2 on PostgreSQL behaves exactly as Engine 2 on SQLite.

The released Engine 2 code (baec_app/engine2/persistence.py) runs unchanged on both backends: on SQLite over its own
in-memory database, on PostgreSQL over Engine2Connection inside one tenant transaction (one fresh synthetic
organization per scenario). Each scenario runs the same calls on both and compares, exactly:

  * every return value (rebuilt domain objects) and every refusal (exception type and message);
  * every stored row of all 30 tables, in insertion order (organization_id, workspace, and seq are PostgreSQL's
    tenancy and ordering columns and are not compared), and the database-assigned audit event ids;
  * the audit history (audit_events) and verify_database's table counts.

Trigger parity is checked with direct SQL below the API: as a role with every DML privilege (the migration role,
still under forced row-level security), each of the 92 SQLite triggers' PostgreSQL equivalents must refuse with the
same message. The application role is refused earlier, by missing privileges, which is stricter and tested too.
All data is synthetic.
"""

from __future__ import annotations

import sqlite3
import threading
import unittest.mock as mock
from contextlib import closing, contextmanager
from datetime import datetime, timedelta

import pytest

from baec_app.domain.enums import StalenessStatus
from baec_app.engine2 import persistence as p
from baec_app.engine2.classification import DimensionAssessment
from baec_app.engine2.domain import (
    CorrespondenceCandidate,
    CorrespondenceDimension,
    DimensionFinding,
    SignalCandidate,
    Supersession,
    SupersessionBasis,
)
from baec_app.engine2.errors import MonitoringPlanActivationError
from baec_console.postgres.database import act_for
from baec_console.postgres.engine2 import Engine2Connection
from tests.engine2 import builders as b
from tests.engine2 import test_engine2_persistence_conformance as corpus
from tests.engine2.test_engine2_persistence import ACT, ACTOR, T, _fail_on, _full, _store_review

from .conftest import AT, fresh, provision, psycopg

with closing(p.open_engine2_database(":memory:")) as _template:
    COLUMNS = {t: [r[1] for r in _template.execute(f"PRAGMA table_info({t})")] for t in p.TABLES}
REFUSED_ACT = "ACT-REFUSED"


def state(conn) -> dict:
    """Every stored row (insertion order), the audit history, and verify_database's counts."""
    tables = {}
    for table in p.TABLES:
        order = "key" if table == "engine2_meta" else "rowid"
        tables[table] = [tuple(r) for r in conn.execute(f"SELECT {', '.join(COLUMNS[table])} FROM {table} "
                                                         f"ORDER BY {order}")]
    return {"tables": tables, "audit": p.audit_events(conn), "verify": p.verify_database(conn)}


def outcome(fn):
    """("ok", value) or ("refused", kind, message). A database refusal compares by its message's first line: SQLite
    raises sqlite3 errors and PostgreSQL psycopg errors, and Engine 2 catches neither."""
    try:
        return ("ok", fn())
    except (sqlite3.DatabaseError, psycopg.Error) as error:
        return ("refused", "database", str(error).splitlines()[0])
    except Exception as error:  # noqa: BLE001 - the refusal itself is what is compared
        return ("refused", type(error).__qualname__, str(error))


@pytest.fixture
def organization(storage) -> str:
    org = fresh("org-e2")
    provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    return org


@pytest.fixture
def backends(database, organization):
    """Run `scenario(conn)` on SQLite and on PostgreSQL; returns both results."""
    def run(scenario, *, org: str | None = None, workspace: str = "local"):
        with closing(p.open_engine2_database(":memory:")) as conn:
            on_sqlite = scenario(conn)
        with database.tenant(org or organization, workspace) as connection:
            on_postgres = scenario(Engine2Connection(connection))
        return on_sqlite, on_postgres
    return run


def _same(backends, scenario, **kw):
    on_sqlite, on_postgres = backends(scenario, **kw)
    assert on_postgres == on_sqlite
    return on_sqlite


# --- scenarios --------------------------------------------------------------------------------------------------------


def _lifecycle(conn) -> dict:
    """Monitoring setup, sources, observations, correction and retraction, measurement, signals, correspondence review
    (three outcomes over two candidates), every read API, and the refusals the API makes."""
    steps = []
    step = lambda label, fn: steps.append((label, outcome(fn)))  # noqa: E731
    active = _full(conn)  # source, plan, activation, three observations, measurement, correction, review REV-T
    step("active", lambda: p.load_active_plan(conn, ACT))
    step("sources", lambda: p.load_authorized_sources(conn))
    step("plan", lambda: p.load_monitoring_plan(conn, b.PLAN_ID, 1))
    step("versions", lambda: p.plan_versions(conn, b.PLAN_ID))
    step("review", lambda: p.load_review(conn, "REV-T"))
    for oid in ("OBS-A", "OBS-B", "OBS-C"):
        step(f"observation {oid}", lambda oid=oid: p.load_observation(conn, oid))
        step(f"plan of {oid}", lambda oid=oid: p.observation_plan_version(conn, oid))
    step("supersession", lambda: p.load_supersession(conn, "OBS-A"))
    step("measurement", lambda: p.load_derived_measurement(conn, "DM-1"))
    step("candidate", lambda: p.load_correspondence_candidate(conn, "CC-T"))
    step("signal", lambda: p.load_signal_candidate(conn, "SC-T"))
    # A second, later review of the same candidate with a contradicted dimension: both stay on record.
    obs = [p.load_observation(conn, i) for i in ("OBS-A", "OBS-B", "OBS-C")]
    led = b.ledger(active, obs, measurements=(p.load_derived_measurement(conn, "DM-1"),),
                   supersessions=(p.load_supersession(conn, "OBS-A"),))
    contradicted = b.findings(ev=("OBS-B",), ENTITY_MATCH=(DimensionFinding.CONTRADICTED, ("OBS-B",)))
    later = b.review(led, review_id="REV-T2", findings=contradicted, reviewed_at=b.T0 + timedelta(hours=1))
    step("second review", lambda: p.record_review(conn, later, activation_id=ACT, recorded_at=T + timedelta(hours=1),
                                                  recorded_by=ACTOR))
    unresolved = b.findings(ev=("OBS-B",), TIMING_MATCH=(DimensionFinding.UNRESOLVED, ("OBS-B",)))
    third = b.review(led, review_id="REV-T3", findings=unresolved, reviewed_at=b.T0 + timedelta(hours=2),
                     baec_revalidation_required=True, revalidation_triggers=("supplier changed", "new contract"))
    step("third review", lambda: p.record_review(conn, third, activation_id=ACT, recorded_at=T + timedelta(hours=2),
                                                 recorded_by=ACTOR))
    step("reviews of candidate", lambda: p.review_ids_for_candidate(conn, "CC-T"))
    step("outcomes", lambda: [p.load_review(conn, r).outcome for r in p.review_ids_for_candidate(conn, "CC-T")])
    # Retraction of OBS-B by a new observation, and a second signal/candidate carrying dimensions.
    retract = b.observe(active, "OBS-D", b.item("PKT-D", content="RETRACTED: the earlier notice was issued in error."))
    step("retracting observation", lambda: p.record_observation(conn, retract, activation_id=ACT, recorded_at=T,
                                                                recorded_by=ACTOR))
    step("retraction", lambda: p.record_supersession(conn, Supersession(
        "OBS-B", "OBS-D", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION, True), recorded_at=T, recorded_by=ACTOR))
    step("load retraction", lambda: p.load_supersession(conn, "OBS-B"))
    signal = SignalCandidate("SC-D", b.PLAN_ID, 1, (retract,), "REVIEWER-T",
                             frozenset({CorrespondenceDimension.TIMING_MATCH, CorrespondenceDimension.ENTITY_MATCH}))
    step("signal with dimensions", lambda: p.record_signal_candidate(conn, signal, recorded_at=T, recorded_by=ACTOR))
    step("load signal with dimensions", lambda: p.load_signal_candidate(conn, "SC-D"))
    step("candidate 2", lambda: p.record_correspondence_candidate(
        conn, CorrespondenceCandidate("CC-D", b.BAEC_ID, b.PLAN_ID, 1, (signal,)), recorded_at=T, recorded_by=ACTOR))
    # Refusals made by the API.
    step("duplicate source", lambda: p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR))
    step("duplicate observation, other content", lambda: p.record_observation(
        conn, b.observe(active, "OBS-A", b.item("PKT-Z", content="Different text, same id.")), activation_id=ACT,
        recorded_at=T, recorded_by=ACTOR))
    step("unauthorized source", lambda: p.record_observation(
        conn, b.observe(active, "OBS-X", b.item("PKT-X", source_id="SRC-UNKNOWN")), activation_id=ACT,
        recorded_at=T, recorded_by=ACTOR))
    step("naive timestamp", lambda: p.record_observation(conn, b.observe(active, "OBS-N", b.item("PKT-N")),
                                                         activation_id=ACT, recorded_at=datetime(2027, 1, 1),
                                                         recorded_by=ACTOR))
    step("blank actor", lambda: p.record_observation(conn, b.observe(active, "OBS-N", b.item("PKT-N")),
                                                     activation_id=ACT, recorded_at=T, recorded_by="  "))
    step("supersede missing", lambda: p.record_supersession(conn, Supersession(
        "OBS-MISSING", "OBS-A", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION), recorded_at=T, recorded_by=ACTOR))
    step("supersede twice", lambda: p.record_supersession(conn, Supersession(
        "OBS-A", "OBS-D", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION), recorded_at=T, recorded_by=ACTOR))
    step("duplicate review", lambda: p.record_review(conn, later, activation_id=ACT, recorded_at=T, recorded_by=ACTOR))
    step("missing records", lambda: [outcome(lambda f=f: f()) for f in (
        lambda: p.load_observation(conn, "OBS-NONE"), lambda: p.load_review(conn, "REV-NONE"),
        lambda: p.load_active_plan(conn, "ACT-NONE"), lambda: p.load_monitoring_plan(conn, b.PLAN_ID, 9),
        lambda: p.load_supersession(conn, "OBS-C"), lambda: p.load_correspondence_candidate(conn, "CC-NONE"))])
    # Plan version 2 activation refused by a stale Engine 1 reading: recorded, then nothing can be captured under it.
    v2 = b.plan(plan_version=2, authorization_reference="AUTH-MP-TEST-V2")
    step("plan v2", lambda: p.record_monitoring_plan(conn, v2, recorded_at=T, recorded_by=ACTOR))
    step("refused activation", lambda: p.record_plan_activation(
        conn, b.PLAN_ID, 2, b.current(status=StalenessStatus.STALE), activation_id=REFUSED_ACT, recorded_at=T,
        recorded_by=ACTOR))
    step("load refused activation", lambda: p.load_active_plan(conn, REFUSED_ACT))
    step("capture under refused", lambda: p.record_observation(
        conn, b.observe(active, "OBS-R", b.item("PKT-R")), activation_id=REFUSED_ACT, recorded_at=T, recorded_by=ACTOR))
    step("v1 evidence into v2 signal", lambda: p.record_signal_candidate(
        conn, SignalCandidate("SC-MIX", b.PLAN_ID, 2, (p.load_observation(conn, "OBS-C"),), "REVIEWER-T"),
        recorded_at=T, recorded_by=ACTOR))
    return {"steps": steps, "state": state(conn)}


def test_the_full_engine2_lifecycle_and_its_refusals_are_identical(backends):
    result = _same(backends, _lifecycle)
    kinds = {label: o[1] for label, o in result["steps"] if o[0] == "refused"}
    assert kinds["duplicate source"] == kinds["supersede twice"] == kinds["duplicate review"] == "DuplicateRecordError"
    assert kinds["naive timestamp"] == kinds["blank actor"] == kinds["capture under refused"] == "Engine2WriteRefused"
    assert kinds["refused activation"] == kinds["load refused activation"] == "MonitoringPlanActivationError"
    assert kinds["supersede missing"] == "RecordNotFoundError"
    assert kinds["v1 evidence into v2 signal"] == "Engine2ValidationError"
    assert "database" not in kinds.values()  # every refusal came from Engine 2 itself, never from a database error
    outcomes = dict(result["steps"])["outcomes"][1]
    assert len(set(outcomes)) == 3  # verified, no, and possible correspondence
    assert all(result["state"]["tables"][t] for t in p.TABLES if t not in (
        "source_refusals", "review_ledger_refusals", "review_check_evidence", "review_check_dimensions"))


@pytest.mark.parametrize("case", corpus.CASES, ids=[c["case_id"] for c in corpus.CASES])
def test_every_locked_corpus_case_persists_identically(backends, monkeypatch, case):
    """The 64 locked Stage B cases, persisted by the existing Stage D harness on each backend."""
    def scenario(conn):
        monkeypatch.setattr(p, "open_engine2_database", lambda _path: conn)
        _, review_id = corpus.persist_case(case)
        monkeypatch.undo()
        loaded = outcome(lambda: p.load_review(conn, review_id)) if review_id else None
        return {"review_id": review_id, "review": loaded, "state": state(conn)}
    result = _same(backends, scenario)
    if result["review_id"]:
        assert result["review"][1].outcome.value == case["expected_correspondence_outcome"]


def test_corpus_cases_together_reach_every_engine2_table(backends, monkeypatch):
    reached = set()
    for case in corpus.CASES:
        with closing(p.open_engine2_database(":memory:")) as conn:
            monkeypatch.setattr(p, "open_engine2_database", lambda _path, conn=conn: conn)
            corpus.persist_case(case)
            monkeypatch.undo()
            reached |= {t for t, n in p.verify_database(conn).items() if n}
    assert set(p.TABLES) - reached == {"signal_candidate_dimensions"}  # reached by the lifecycle scenario instead


@pytest.mark.parametrize("event_type,write", [
    ("OBSERVATION_RECORDED", "observation"), ("HUMAN_REVIEW_RECORDED", "review"),
    ("SUPERSESSION_RECORDED", "supersession"), ("MONITORING_PLAN_RECORDED", "plan"),
])
def test_a_write_that_fails_after_its_rows_leaves_nothing_on_either_backend(backends, monkeypatch, event_type, write):
    def scenario(conn):
        p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
        if write != "plan":
            p.record_monitoring_plan(conn, b.plan(), recorded_at=T, recorded_by=ACTOR)
            active = p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(), activation_id=ACT, recorded_at=T,
                                              recorded_by=ACTOR)
            first, second = b.observe(active, "OBS-1"), b.observe(active, "OBS-2", b.item("PKT-2", content="Later."))
            if write != "observation":
                for o in (first, second):
                    p.record_observation(conn, o, activation_id=ACT, recorded_at=T, recorded_by=ACTOR)
        monkeypatch.setattr(p, "_insert_audit_event", _fail_on(event_type, p._insert_audit_event))
        try:
            failed = outcome({
                "plan": lambda: p.record_monitoring_plan(conn, b.plan(), recorded_at=T, recorded_by=ACTOR),
                "observation": lambda: p.record_observation(conn, first, activation_id=ACT, recorded_at=T,
                                                            recorded_by=ACTOR),
                "supersession": lambda: p.record_supersession(conn, Supersession(
                    "OBS-1", "OBS-2", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION), recorded_at=T,
                    recorded_by=ACTOR),
                "review": lambda: _store_review(conn, b.review(b.ledger(active, [first, second]),
                                                               findings=b.findings(ev=("OBS-1",)))),
            }[write])
        finally:
            monkeypatch.undo()
        return {"failed": failed, "open": conn.in_transaction, "state": state(conn)}
    result = _same(backends, scenario)
    assert result["failed"][:2] == ("refused", "RuntimeError") and result["open"] is False


# --- trigger parity below the API ------------------------------------------------------------------------------------


def _signal_dimensions(conn):
    active = _full(conn)
    signal = SignalCandidate("SC-D", b.PLAN_ID, 1, (p.load_observation(conn, "OBS-B"),), "REVIEWER-T",
                             frozenset({CorrespondenceDimension.TIMING_MATCH}))
    p.record_signal_candidate(conn, signal, recorded_at=T, recorded_by=ACTOR)
    return active


def _populate(conn, table: str) -> None:
    """A dataset in which `table` has rows: a corpus case that reaches it, or the lifecycle fixture."""
    if table in ("engine2_meta", "signal_candidate_dimensions"):
        _signal_dimensions(conn)
        return
    for case in corpus.CASES:
        with closing(p.open_engine2_database(":memory:")) as probe:
            with mock.patch.object(p, "open_engine2_database", lambda _path: probe):
                corpus.persist_case(case)
            if p.verify_database(probe)[table]:
                with mock.patch.object(p, "open_engine2_database", lambda _path: conn):
                    corpus.persist_case(case)
                return
    raise AssertionError(f"no dataset reaches {table}")


@contextmanager
def _privileged(migrated, organization, workspace="local"):
    """The migration role (every DML privilege, still under forced row-level security), acting for `organization`."""
    with psycopg.connect(migrated["migrator"], autocommit=True) as connection:
        with connection.transaction(force_rollback=True):
            act_for(connection, organization, workspace)
            yield Engine2Connection(connection)


def _raw(conn, sql: str, args: tuple = ()):
    """One statement; on PostgreSQL inside a savepoint, so a refusal does not abort the transaction."""
    def run():
        if isinstance(conn, Engine2Connection):
            with conn.db.transaction():
                return conn.execute(sql, args).fetchall()
        return conn.execute(sql, args).fetchall()
    return outcome(run)


def _direct_sql(conn, table: str) -> dict:
    columns = ", ".join(COLUMNS[table])
    first = COLUMNS[table][-1]
    return {
        "update": _raw(conn, f"UPDATE {table} SET {first} = {first}"),
        "delete": _raw(conn, f"DELETE FROM {table}"),
        "replace": _raw(conn, f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table} LIMIT 1"),
        "rows": len(conn.execute(f"SELECT 1 FROM {table}").fetchall()),
    }


@pytest.mark.parametrize("table", p.TABLES)
def test_each_append_only_and_no_replace_trigger_refuses_identically(migrated, storage, database, table):
    """90 of the 92 SQLite triggers: <table>_no_update, _no_delete, and _no_replace, on every table."""
    with closing(p.open_engine2_database(":memory:")) as conn:
        _populate(conn, table)
        on_sqlite = _direct_sql(conn, table)
    org = fresh("org-e2-sql")
    provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    with database.tenant(org, "local") as connection:
        _populate(Engine2Connection(connection), table)
    with _privileged(migrated, org) as conn:
        on_postgres = _direct_sql(conn, table)
    assert on_postgres == on_sqlite
    assert on_sqlite["rows"] >= 1
    assert on_sqlite["update"] == ("refused", "database", f"{table} is append-only")
    assert on_sqlite["delete"] == ("refused", "database", f"{table} is append-only")
    assert on_sqlite["replace"] == ("refused", "database", f"{table} rows cannot be replaced")


def _refused_activation(conn):
    p.record_authorized_source(conn, b.source(), recorded_at=T, recorded_by=ACTOR)
    p.record_monitoring_plan(conn, b.plan(), recorded_at=T, recorded_by=ACTOR)
    with pytest.raises(MonitoringPlanActivationError):
        p.record_plan_activation(conn, b.PLAN_ID, 1, b.current(status=StalenessStatus.STALE), activation_id=REFUSED_ACT,
                                 recorded_at=T, recorded_by=ACTOR)


_UNDER_REFUSED = {
    "observations": ("INSERT INTO observations (observation_id, monitoring_plan_id, plan_version, activation_id, "
                     "source_id, source_type, observed_at, exact_evidence_content, source_locator, provenance, "
                     "content_sha256, acquisition_method, authorization_reference, evidence_event_id, recorded_at, "
                     "recorded_by) VALUES ('OBS-SQL', ?, 1, ?, ?, 'notice', '2027-01-01T00:00:00+00:00', 'text', "
                     "'loc', 'EXTERNAL_EVIDENCE', ?, 'manual', 'AUTH', 'EVT', '2027-01-01T00:00:00+00:00', 'sql')",
                     (b.PLAN_ID, REFUSED_ACT, b.SRC, "0" * 64)),
    "source_refusals": ("INSERT INTO source_refusals (monitoring_plan_id, plan_version, item_id, activation_id, reason, "
                        "recorded_at, recorded_by) VALUES (?, 1, 'ITEM-SQL', ?, 'r', '2027-01-01T00:00:00+00:00', "
                        "'sql')", (b.PLAN_ID, REFUSED_ACT)),
}


@pytest.mark.parametrize("table", sorted(_UNDER_REFUSED))
def test_the_needs_activation_triggers_refuse_identically(migrated, storage, database, table):
    """The other 2 SQLite triggers: evidence and source refusals only under an ACTIVATED plan version."""
    sql, args = _UNDER_REFUSED[table]
    with closing(p.open_engine2_database(":memory:")) as conn:
        _refused_activation(conn)
        on_sqlite = _raw(conn, sql, args)
    org = fresh("org-e2-act")
    provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    with database.tenant(org, "local") as connection:
        _refused_activation(Engine2Connection(connection))
    with _privileged(migrated, org) as conn:
        on_postgres = _raw(conn, sql, args)
    assert on_postgres == on_sqlite == ("refused", "database", f"{table} requires an ACTIVATED Monitoring Plan")


def test_the_application_role_cannot_update_or_delete_engine2_rows_at_all(storage, database):
    """Below the triggers: the application role has no UPDATE, DELETE, or TRUNCATE privilege on any Engine 2 table,
    and only reads engine2_meta. Its duplicate inserts still reach the no_replace trigger, with SQLite's message."""
    for table in p.TABLES:
        org = fresh("org-e2-app")
        provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
        with database.tenant(org, "local") as connection:
            conn = Engine2Connection(connection)
            _populate(conn, table)
            found = _direct_sql(conn, table)
            assert found["rows"] >= 1, table
            assert found["update"][2].startswith("permission denied"), (table, found)
            assert found["delete"][2].startswith("permission denied"), (table, found)
            expected = "permission denied" if table == "engine2_meta" else f"{table} rows cannot be replaced"
            assert found["replace"][2].startswith(expected), (table, found)
            assert _raw(conn, f"TRUNCATE {table}")[2].startswith("permission denied"), table


def test_truncate_is_refused_even_for_the_owner(migrated, storage):
    org = fresh("org-e2-trunc")
    provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    with _privileged(migrated, org) as conn:
        _signal_dimensions(conn)
        before = state(conn)
        for table in p.TABLES:
            refused = _raw(conn, f"TRUNCATE {table}")
            # A table other tables reference is refused by PostgreSQL itself before any trigger runs.
            assert refused[:2] == ("refused", "database") and refused[2] in (
                f"{table} is append-only", "cannot truncate a table referenced in a foreign key constraint"), table
        assert state(conn) == before


_TAMPER = {
    "observation text": ("UPDATE observations SET exact_evidence_content = 'tampered' WHERE observation_id = 'OBS-A'",
                         lambda c: p.load_observation(c, "OBS-A")),
    "observation hash": ("UPDATE observations SET content_sha256 = '" + "a" * 64 + "' WHERE observation_id = 'OBS-B'",
                         lambda c: p.load_observation(c, "OBS-B")),
    "outcome snapshot": ("UPDATE reviews SET outcome_snapshot = 'NO_CORRESPONDENCE' WHERE review_id = 'REV-T'",
                         lambda c: p.load_review(c, "REV-T")),
    "measurement output": ("UPDATE derived_measurements SET output_value = '99' WHERE measurement_id = 'DM-1'",
                           lambda c: p.load_derived_measurement(c, "DM-1")),
    "audit details": ("UPDATE audit_events SET event_type = 'UNKNOWN_EVENT' WHERE event_id = 1",
                      lambda c: p.audit_events(c)),
}


@pytest.mark.parametrize("name", sorted(_TAMPER))
def test_tampered_rows_fail_closed_identically(migrated, storage, database, name):
    """Direct tampering with the triggers bypassed (SQLite: dropped; PostgreSQL: a superuser with
    session_replication_role = replica, which also skips foreign keys). Loading refuses with the same error."""
    sql, load = _TAMPER[name]
    with closing(p.open_engine2_database(":memory:")) as conn:
        _full(conn)
        table = sql.split()[1]
        conn.execute(f"DROP TRIGGER {table}_no_update")
        on_sqlite_write = outcome(lambda: conn.execute(sql))
        on_sqlite = outcome(lambda: load(conn))
    org = fresh("org-e2-tamper")
    provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    with database.tenant(org, "local") as connection:
        _full(Engine2Connection(connection))
    with psycopg.connect(migrated["admin"], autocommit=True) as admin, admin.transaction():
        admin.execute("SET LOCAL session_replication_role = replica")
        act_for(admin, org, "local")
        # A superuser bypasses row-level security, so the tamper names this organization's workspace itself.
        scoped = sql + " AND organization_id = %s AND workspace = 'local'"
        on_postgres_write = outcome(lambda: admin.execute(scoped.replace("UPDATE ", "UPDATE baec_engine2.", 1), (org,)))
    with database.tenant(org, "local") as connection:
        on_postgres = outcome(lambda: load(Engine2Connection(connection)))
    assert on_postgres == on_sqlite
    if name == "audit details":
        assert on_sqlite_write[0] == on_postgres_write[0] == "refused"  # CHECK holds even with triggers off
        assert on_sqlite[0] == "ok"
    else:
        assert on_sqlite_write[0] == on_postgres_write[0] == "ok"
        assert on_sqlite[:2] == ("refused", "Engine2IntegrityError")


# --- ids, isolation, and concurrency ---------------------------------------------------------------------------------


def _sources(conn, n: int, prefix: str = "SRC") -> list[int]:
    from dataclasses import replace
    for i in range(n):
        p.record_authorized_source(conn, replace(b.source(), source_id=f"{prefix}-{i}"), recorded_at=T,
                                   recorded_by=ACTOR)
    return [e.event_id for e in p.audit_events(conn)]


def test_audit_event_ids_are_numbered_per_workspace_exactly_as_in_one_sqlite_file(storage, database):
    """Two organizations and two workspaces interleave writes; each one's ids are 1..n, as each SQLite file's are."""
    orgs = [fresh("org-e2-ids") for _ in range(2)]
    for org in orgs:
        provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    for round_ in range(3):
        for org in orgs:
            for workspace in ("local", "synthetic"):
                with database.tenant(org, workspace) as connection:
                    _sources(Engine2Connection(connection), 1, prefix=f"SRC-{round_}")
    for org in orgs:
        for workspace in ("local", "synthetic"):
            with database.tenant(org, workspace) as connection:
                assert [e.event_id for e in p.audit_events(Engine2Connection(connection))] == [1, 2, 3]


def test_concurrent_writers_in_one_workspace_get_distinct_consecutive_ids(storage, database):
    org = fresh("org-e2-race")
    provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    errors = []

    def writer(index: int) -> None:
        try:
            for i in range(5):
                with database.tenant(org, "local") as connection:
                    _sources(Engine2Connection(connection), 1, prefix=f"SRC-{index}-{i}")
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with database.tenant(org, "local") as connection:
        assert [e.event_id for e in p.audit_events(Engine2Connection(connection))] == list(range(1, 16))


def test_another_organization_or_workspace_sees_no_engine2_rows_and_may_reuse_ids(storage, database, migrated):
    a, b_org = fresh("org-e2-a"), fresh("org-e2-b")
    for org in (a, b_org):
        provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    with database.tenant(a, "local") as connection:
        _signal_dimensions(Engine2Connection(connection))
    for org, workspace in ((b_org, "local"), (a, "synthetic")):
        with database.tenant(org, workspace) as connection:
            conn = Engine2Connection(connection)
            assert all(n == 0 for t, n in p.verify_database(conn).items() if t != "engine2_meta"), (org, workspace)
            assert outcome(lambda: p.load_observation(conn, "OBS-A"))[1] == "RecordNotFoundError"
            _signal_dimensions(conn)  # the same Engine 2 ids are free here
    with database.tenant(a, "local") as connection:  # the first organization's rows are unchanged
        assert p.verify_database(Engine2Connection(connection))["observations"] == 3


def test_without_a_workspace_or_with_a_forged_tenant_engine2_is_closed(storage, database, app):
    a, b_org = fresh("org-e2-a"), fresh("org-e2-b")
    for org in (a, b_org):
        provision(storage, org, fresh("user"), "Engine 2 tester (synthetic)")
    with database.tenant(a, "local") as connection:
        _full(Engine2Connection(connection))
    with database.tenant(a) as connection:  # an organization but no workspace
        with pytest.raises(p.Engine2SchemaError, match="needs a tenant transaction"):
            Engine2Connection(connection)
        assert connection.execute("SELECT count(*) FROM baec_engine2.observations").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="workspace_scope"):
            with connection.transaction():
                connection.execute("INSERT INTO baec_engine2.authorized_sources (source_id, source_type, "
                                   "owner_or_publisher, legitimate_basis, access_method, permitted_capture, "
                                   "authorized_by, authorized_at, recorded_at, recorded_by) "
                                   "VALUES ('S', 't', 'o', 'l', 'a', 'p', 'b', 'x', 'x', 'x')")
    with database.tenant(b_org, "local") as connection:  # forging another tenant's columns
        for column, value in (("organization_id", a), ("workspace", "synthetic")):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
                with connection.transaction():
                    connection.execute(f"INSERT INTO baec_engine2.authorized_sources ({column}, source_id, "
                                       "source_type, owner_or_publisher, legitimate_basis, access_method, "
                                       "permitted_capture, authorized_by, authorized_at, recorded_at, recorded_by) "
                                       "VALUES (%s, 'S', 't', 'o', 'l', 'a', 'p', 'b', 'x', 'x', 'x')", (value,))
        assert connection.execute("SELECT count(*) FROM baec_engine2.observations "
                                  "WHERE organization_id = %s", (a,)).fetchone()[0] == 0
    with app.transaction():  # no tenant at all
        assert app.execute("SELECT count(*) FROM baec_engine2.audit_events").fetchone()[0] == 0


def test_unsupported_sqlite_statements_are_refused_not_emulated(database, organization):
    with database.tenant(organization, "local") as connection:
        conn = Engine2Connection(connection)
        for sql in ("PRAGMA foreign_keys = OFF", "PRAGMA ignore_check_constraints = ON", "PRAGMA table_info(x)"):
            with pytest.raises(p.Engine2SchemaError, match="unsupported on PostgreSQL"):
                conn.execute(sql)
        with pytest.raises(p.Engine2SchemaError, match="without an open Engine 2 write"):
            conn.execute("COMMIT")
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        p.require_engine2_schema(conn)


def test_review_reconstruction_uses_only_findings_never_a_stored_outcome(backends):
    """The stored outcome is a snapshot the classifier recomputes on load, on both backends."""
    def scenario(conn):
        _full(conn)
        review = p.load_review(conn, "REV-T")
        return review.outcome, [a.finding for a in review.findings if isinstance(a, DimensionAssessment)]
    _same(backends, scenario)
