"""RC-33 schema guards: raw SQL cannot rewrite stored source evidence (Phase 3 hardening).

These tests bypass the repository on purpose. Each refusal must leave every
table exactly as it was, and the repository must still load the original
objects. "Guards off" switches off foreign keys and CHECK constraints but
leaves the triggers in place; tamper() in persistence_builders drops them.
"""

import sqlite3
from dataclasses import replace
from decimal import Decimal

import pytest

from baec_app.data.database import (
    AI_APPEND_ONLY_TABLES,
    AI_REPLACE_GUARDED_KEYS,
    APPEND_ONLY_TABLES,
    BAEC_RECORD_MUTABLE_COLUMNS,
    BAEC_RECORD_PROTECTED_COLUMNS,
    BRIDGE_APPEND_ONLY_TABLES,
    BRIDGE_INTEGRITY_TRIGGERS,
    BRIDGE_REPLACE_GUARDED_KEYS,
    REPLACE_GUARDED_KEYS,
    DatabaseVersionError,
    create_working_copy,
    open_database,
)
from baec_app.data.repository import Repository
from baec_app.data.seed import build_canonical_seed_database, build_seed_database
from baec_app.domain.baec_rules import create_confirmed_baec_record
from baec_app.domain.enums import StalenessStatus, ThresholdComparator
from baec_app.domain.models import AiDerivedText, StringencyExpression
from tests.builders import AO, HARBOR_QUOTE, NOW, NP, candidate, confirm_auth
from tests.persistence_builders import (  # noqa: F401
    connection,
    counts,
    dump,
    move,
    no_leaked_connections,
    repo,
    save_judgment,
)

EXPECTED_TRIGGERS = sorted(
    [f"{table}_no_{op}" for table in APPEND_ONLY_TABLES for op in ("update", "delete")]
    + [f"{table}_no_replace" for table in REPLACE_GUARDED_KEYS]
    + ["baec_records_protected_no_update", "baec_records_no_delete"]
    + ["interaction_evidence_verbatim"]
    # Schema version 5 (Phase 6B): the AI provenance tables' triggers.
    + [f"{table}_no_{op}" for table in AI_APPEND_ONLY_TABLES for op in ("update", "delete")]
    + [f"{table}_no_replace" for table in AI_REPLACE_GUARDED_KEYS]
    + ["ai_run_results_model_binding", "ai_run_outputs_require_result",
       "ai_artifacts_require_success", "ai_artifact_excerpts_verbatim"]
    # Schema version 6 (Phase 6D-B1): the closed failure-code backstop.
    + ["ai_run_results_failure_codes_closed"]
    # Schema version 7 (Phase 7C): the bridge tables' triggers.
    + [f"{table}_no_{op}" for table in BRIDGE_APPEND_ONLY_TABLES for op in ("update", "delete")]
    + [f"{table}_no_replace" for table in BRIDGE_REPLACE_GUARDED_KEYS]
    + list(BRIDGE_INTEGRITY_TRIGGERS)
)


def full_record(baec_id="B-1"):
    """A confirmed record in which every baec_records column holds a value."""
    cand = candidate(
        buyer_exact_statement=HARBOR_QUOTE,
        buyer_role="Materials manager",
        stringency=StringencyExpression("more than 10%", ThresholdComparator.GREATER_THAN, Decimal("10")),
    )
    record = create_confirmed_baec_record(
        cand, baec_id=baec_id, captured_at=NOW, confirmation=confirm_auth(baec_id)
    )
    return replace(record, normalized_condition=AiDerivedText("Normalized wording.", NOW, "example-model"))


def populate(repository):
    """Store at least one row in every guarded table; return the saved record."""
    saved = full_record()
    repository.save_confirmed_baec(saved)
    save_judgment(repository)
    assert move(repository, AO).allowed
    assert move(repository, NP).allowed  # leaving Active stores non-evaluation evidence
    return saved


def guards_off(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA ignore_check_constraints = ON")


def guards_on(conn):
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    conn.execute("PRAGMA foreign_keys = ON")


def refused(conn, sql, parameters=(), *, match, other_guards):
    """Run raw SQL that must be refused by a trigger and change nothing."""
    before = dump(conn)
    if not other_guards:
        guards_off(conn)
    try:
        with pytest.raises(sqlite3.IntegrityError, match=match):
            conn.execute(sql, parameters)
    finally:
        guards_on(conn)
    assert dump(conn) == before


GUARD_MODES = pytest.mark.parametrize("other_guards", [True, False], ids=["guards-on", "fk-and-checks-off"])


# --- protected baec_records columns -------------------------------------------


def test_rc33_baec_record_columns_are_partitioned_into_protected_and_mutable(connection):
    columns = [row[1] for row in connection.execute("PRAGMA table_info(baec_records)")]
    assert BAEC_RECORD_MUTABLE_COLUMNS == ("staleness_status",)
    assert not set(BAEC_RECORD_PROTECTED_COLUMNS) & set(BAEC_RECORD_MUTABLE_COLUMNS)
    assert sorted(BAEC_RECORD_PROTECTED_COLUMNS + BAEC_RECORD_MUTABLE_COLUMNS) == sorted(columns)
    assert len(BAEC_RECORD_PROTECTED_COLUMNS) == len(set(BAEC_RECORD_PROTECTED_COLUMNS)) == 15


@GUARD_MODES
@pytest.mark.parametrize("form", ["changed", "no-op"])
@pytest.mark.parametrize("column", BAEC_RECORD_PROTECTED_COLUMNS)
def test_rc33_raw_update_of_a_protected_baec_record_column_is_refused(repo, connection, column, form, other_guards):
    saved = full_record()
    repo.save_confirmed_baec(saved)
    stored = connection.execute(f"SELECT {column} FROM baec_records").fetchone()[0]
    if form == "no-op":
        value = column
    else:  # a real change: NULL a stored value, or fill a stored NULL (classification_reason)
        value = "'tampered'" if stored is None else "NULL"
    refused(connection, f"UPDATE baec_records SET {column} = {value}", match="immutable", other_guards=other_guards)
    assert repo.get_baec_record("B-1") == saved


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE baec_records SET buyer_exact_statement = NULL WHERE baec_id = 'BAEC-HARBOR-001'",
        "UPDATE baec_records SET captured_at = '2020-01-01T00:00:00.000000+00:00' WHERE baec_id = 'BAEC-HARBOR-001'",
    ],
    ids=["statement-nulled", "capture-time-moved"],
)
@GUARD_MODES
def test_rc33_reported_gap_buyer_statement_and_capture_time_cannot_be_rewritten(sql, other_guards):
    """Regression: at phase-3-persistence both edits loaded without error."""
    seeded = build_seed_database()
    try:
        repository = Repository(seeded)
        before = repository.get_baec_record("BAEC-HARBOR-001")
        assert before.candidate.buyer_exact_statement is not None
        refused(seeded, sql, match="immutable", other_guards=other_guards)
        assert repository.get_baec_record("BAEC-HARBOR-001") == before
    finally:
        seeded.close()


@GUARD_MODES
def test_rc33_upsert_cannot_change_a_protected_baec_record_column(repo, connection, other_guards):
    saved = full_record()
    repo.save_confirmed_baec(saved)
    row = connection.execute("SELECT * FROM baec_records").fetchone()
    placeholders = ", ".join("?" * len(row))
    sql = (
        f"INSERT INTO baec_records VALUES ({placeholders}) "
        "ON CONFLICT (baec_id) DO UPDATE SET buyer_exact_statement = NULL"
    )
    refused(connection, sql, row, match="cannot be replaced|immutable", other_guards=other_guards)
    assert repo.get_baec_record("B-1") == saved


def test_rc33_staleness_status_is_the_only_mutable_baec_record_column(repo, connection):
    """Deliberate exemption, reserved for a later lifecycle design; loads still fail closed."""
    saved = full_record()
    repo.save_confirmed_baec(saved)
    connection.execute("UPDATE baec_records SET staleness_status = ?", (StalenessStatus.STALE.value,))
    assert repo.get_baec_record("B-1") == replace(saved, staleness_status=StalenessStatus.STALE)


@GUARD_MODES
def test_rc33_baec_record_rows_cannot_be_deleted(repo, connection, other_guards):
    saved = full_record()
    repo.save_confirmed_baec(saved)
    refused(connection, "DELETE FROM baec_records", match="cannot be deleted", other_guards=other_guards)
    assert repo.get_baec_record("B-1") == saved


# --- no stored row can be replaced --------------------------------------------


@GUARD_MODES
@pytest.mark.parametrize("verb", ["REPLACE", "INSERT OR REPLACE", "INSERT OR IGNORE"])
@pytest.mark.parametrize("table", list(REPLACE_GUARDED_KEYS))
def test_rc33_stored_rows_cannot_be_replaced(repo, connection, table, verb, other_guards):
    saved = populate(repo)
    history = repo.get_transition_history("ACC-1")
    rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    assert rows
    for row in rows:
        placeholders = ", ".join("?" * len(row))
        refused(
            connection,
            f"{verb} INTO {table} VALUES ({placeholders})",
            row,
            match=f"{table} rows cannot be replaced",
            other_guards=other_guards,
        )
    assert repo.get_baec_record("B-1") == saved
    assert repo.get_transition_history("ACC-1") == history


SECONDARY_KEY_CASES = {
    # table: (column to give a fresh value, fresh value)
    "baec_records": ("baec_id", "B-HIJACK"),  # reuses confirmation_authorization_id
    "dormancy_judgments": ("judgment_id", 999),  # reuses authorization_id
    "account_state_transitions": ("transition_id", 999),  # reuses authorization_id
    "interaction_evidence": ("evidence_id", 999),  # reuses (interaction_id, provenance, text)
    "criterion_assessments": ("criterion", "TAMPERED"),  # reuses (baec_id, position)
}


@pytest.mark.parametrize("table", list(SECONDARY_KEY_CASES))
def test_rc33_replace_through_a_secondary_unique_key_is_refused(repo, connection, table):
    """REPLACE also deletes rows that collide on a non-primary UNIQUE key."""
    populate(repo)
    column, fresh = SECONDARY_KEY_CASES[table]
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    row = list(connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchone())
    row[columns.index(column)] = fresh
    placeholders = ", ".join("?" * len(row))
    refused(
        connection,
        f"REPLACE INTO {table} VALUES ({placeholders})",
        tuple(row),
        match=f"{table} rows cannot be replaced",
        other_guards=False,
    )


def test_rc33_interaction_without_evidence_cannot_be_replaced(repo, connection):
    """Regression: at phase-3-persistence this rewrote the text with foreign keys on."""
    assert connection.execute("SELECT COUNT(*) FROM interaction_evidence").fetchone()[0] == 0
    original = repo.get_interaction("INT-1")
    refused(
        connection,
        "REPLACE INTO interactions VALUES ('INT-1', 'ACC-1', '2026-01-15T12:00:00.000000+00:00', 'rewritten')",
        match="interactions rows cannot be replaced",
        other_guards=True,
    )
    assert repo.get_interaction("INT-1") == original


def _minimal_unique_keys(conn, table):
    keys = set()
    pk = [row for row in conn.execute(f"PRAGMA table_info({table})") if row[5] > 0]
    if pk:
        keys.add(frozenset(row[1] for row in pk))
    for index in conn.execute(f"PRAGMA index_list({table})"):
        if index[2] == 1:  # unique
            keys.add(frozenset(row[2] for row in conn.execute(f"PRAGMA index_info({index[1]})")))
    return {key for key in keys if not any(other < key for other in keys)}


def test_rc33_replace_guards_cover_every_unique_key(connection):
    """A UNIQUE key added later without a guard fails here."""
    assert set(REPLACE_GUARDED_KEYS) == set(APPEND_ONLY_TABLES) | {"baec_records"}
    for table, declared in REPLACE_GUARDED_KEYS.items():
        assert {frozenset(key) for key in declared} == _minimal_unique_keys(connection, table), table


# --- guards travel with the database ------------------------------------------


def _trigger_names(conn):
    return sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'"))


def test_rc33_guards_are_present_in_working_copies_and_the_canonical_seed():
    canonical = build_canonical_seed_database()
    try:
        working = create_working_copy(canonical)
        try:
            assert _trigger_names(canonical) == EXPECTED_TRIGGERS
            assert _trigger_names(working) == EXPECTED_TRIGGERS
            assert len(EXPECTED_TRIGGERS) == 34 + 19 + 1 + 38
            row = working.execute("SELECT * FROM interactions ORDER BY rowid").fetchone()
            refused(
                working,
                "REPLACE INTO interactions VALUES (?, ?, ?, ?)",
                (row[0], row[1], row[2], "rewritten"),
                match="interactions rows cannot be replaced",
                other_guards=False,
            )
        finally:
            working.close()
    finally:
        canonical.close()


def test_database_created_with_schema_version_2_is_refused(tmp_path):
    """Version 2 lacked the RC-33 guards; such a file must be rebuilt."""
    path = str(tmp_path / "v2.sqlite3")
    first = open_database(path)
    first.execute("PRAGMA user_version = 2")
    first.close()
    with pytest.raises(DatabaseVersionError, match="rebuild the database from the seed files"):
        open_database(path)
