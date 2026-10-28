"""Connection, schema, versioning, and transactions (Phase 3)."""

import sqlite3

import pytest

from baec_app.data import database
from baec_app.data.database import (
    AI_APPEND_ONLY_TABLES,
    AI_PROVENANCE_TABLES,
    AI_REPLACE_GUARDED_KEYS,
    ALL_TABLES,
    APPEND_ONLY_TABLES,
    BRIDGE_APPEND_ONLY_TABLES,
    BRIDGE_INTEGRITY_TRIGGERS,
    BRIDGE_REPLACE_GUARDED_KEYS,
    BRIDGE_TABLES,
    REPLACE_GUARDED_KEYS,
    DatabaseVersionError,
    connect,
    create_working_copy,
    initialize_schema,
    make_read_only,
    open_database,
    require_current_schema,
    schema_version,
    transaction,
)
from baec_app.domain import enums
from tests.persistence_builders import connection, no_leaked_connections  # noqa: F401


# Schema version 5 (Phase 6B): the AI provenance tables' append-only, replace, and integrity triggers.
AI_TRIGGERS = (
    [f"{table}_no_{op}" for table in AI_APPEND_ONLY_TABLES for op in ("update", "delete")]
    + [f"{table}_no_replace" for table in AI_REPLACE_GUARDED_KEYS]
    + ["ai_run_results_model_binding", "ai_run_outputs_require_result",
       "ai_artifacts_require_success", "ai_artifact_excerpts_verbatim"]
    # Schema version 6 (Phase 6D-B1): the closed failure-code backstop.
    + ["ai_run_results_failure_codes_closed"]
)
# Schema version 7 (Phase 7C): the bridge tables' append-only, replace, and integrity triggers.
BRIDGE_TRIGGERS = (
    [f"{table}_no_{op}" for table in BRIDGE_APPEND_ONLY_TABLES for op in ("update", "delete")]
    + [f"{table}_no_replace" for table in BRIDGE_REPLACE_GUARDED_KEYS]
    + list(BRIDGE_INTEGRITY_TRIGGERS)
)


def test_sqlite_version_check_refuses_versions_without_strict_tables():
    """Since Phase 6D-B1 the minimum is 3.38.0, for built-in JSON functions; 3.37.x is refused too."""
    assert database.MINIMUM_SQLITE_VERSION == (3, 38, 0)
    for too_old in ((3, 37, 2), (3, 37, 0), (3, 36, 0), (3, 31, 1), (2, 8, 17)):
        with pytest.raises(DatabaseVersionError):
            database.check_sqlite_version(too_old)
    database.check_sqlite_version((3, 38, 0))
    database.check_sqlite_version((3, 50, 4))
    database.check_sqlite_version()  # the running SQLite must itself be new enough


def test_connect_checks_the_sqlite_version_before_opening(monkeypatch):
    monkeypatch.setattr(database, "MINIMUM_SQLITE_VERSION", (99, 0, 0))
    with pytest.raises(DatabaseVersionError):
        connect()


def test_foreign_keys_are_enforced_on_every_connection(connection):
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_schema_has_exactly_the_thirteen_approved_tables(connection):
    """The thirteen Phase 3 tables plus, since schema version 5, the five AI provenance tables and, since schema
    version 7, the seven Phase 7 bridge tables (name kept for ID continuity)."""
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    assert sorted(names) == sorted(ALL_TABLES)
    assert len(names) == 13 + len(AI_PROVENANCE_TABLES) + len(BRIDGE_TABLES) == 25


def test_every_table_is_strict(connection):
    strict = {row[1]: row[5] for row in connection.execute("PRAGMA table_list") if row[1] in ALL_TABLES}
    assert strict == {table: 1 for table in ALL_TABLES}


def test_append_only_triggers_cover_exactly_the_approved_tables(connection):
    triggers = sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
    expected = sorted(
        [f"{table}_no_{op}" for table in APPEND_ONLY_TABLES for op in ("update", "delete")]
        + [f"{table}_no_replace" for table in REPLACE_GUARDED_KEYS]
        + ["baec_records_protected_no_update", "baec_records_no_delete"]
        + ["interaction_evidence_verbatim"]
        + AI_TRIGGERS
        + BRIDGE_TRIGGERS
    )
    assert triggers == expected
    assert len(APPEND_ONLY_TABLES) == 10
    for unlocked in ("accounts", "baec_records", "schema_meta"):
        assert unlocked not in APPEND_ONLY_TABLES


ENUM_COLUMNS = [
    ("accounts", "state", enums.AccountState),
    ("human_authorizations", "action", enums.AuthorizationAction),
    ("human_authorizations", "target_state", enums.AccountState),
    ("baec_records", "articulation_origin", enums.ArticulationOrigin),
    ("baec_records", "elicitation_mode", enums.ElicitationMode),
    ("baec_records", "classification", enums.BaecClassification),
    ("baec_records", "staleness_status", enums.StalenessStatus),
    ("criterion_assessments", "criterion", enums.BaecCriterion),
    ("criterion_assessments", "finding", enums.CriterionFinding),
    ("stringency_expressions", "comparator", enums.ThresholdComparator),
    ("dormancy_judgments", "plausibility", enums.ReviewAnswer),
    ("dormancy_judgments", "addressability", enums.ReviewAnswer),
    ("account_state_transitions", "from_state", enums.AccountState),
    ("account_state_transitions", "to_state", enums.AccountState),
    ("account_state_transitions", "ground", enums.NoPlausiblePathGround),
]


@pytest.mark.parametrize("table,column,enum_class", ENUM_COLUMNS, ids=lambda v: getattr(v, "__name__", v))
def test_enum_value_lists_in_the_schema_match_the_enum_members(connection, table, column, enum_class):
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
    expected = "CHECK ({} IN ({}))".format(column, ", ".join(f"'{m.value}'" for m in enum_class))
    assert expected in sql


def test_interaction_evidence_accepts_only_buyer_fact_and_seller_observation(connection):
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE name = 'interaction_evidence'").fetchone()[0]
    assert "CHECK (provenance IN ('BUYER_FACT', 'SELLER_OBSERVATION'))" in sql
    assert "EXTERNAL_EVIDENCE" not in sql and "AI_INFERENCE" not in sql


def test_strict_tables_refuse_a_value_of_the_wrong_storage_type(connection):
    """A backstop only: STRICT does not replace repository and domain validation."""
    connection.execute("INSERT INTO accounts (account_id, name) VALUES ('A', 'Synthetic')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO interactions (interaction_id, account_id, occurred_at, text) VALUES ('I', 'A', ?, 'x')",
            (b"\x00\x01",),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO human_authorizations (authorization_id, authorized_by, authorized_at, action, subject_id) "
            "VALUES ('not-a-number', 'x', 'x', 'CONFIRM_BAEC', 'x')"
        )


def test_schema_version_is_set_and_a_mismatch_is_refused(connection):
    assert schema_version(connection) == database.SCHEMA_VERSION == 7
    require_current_schema(connection)
    connection.execute("PRAGMA user_version = 99")
    with pytest.raises(DatabaseVersionError):
        require_current_schema(connection)


def test_opening_a_database_file_with_another_schema_version_is_refused(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    first = open_database(path)
    first.execute("PRAGMA user_version = 8")  # 7 until Phase 7C made 7 current
    first.close()
    with pytest.raises(DatabaseVersionError):
        open_database(path)


def test_database_created_with_the_earlier_schema_version_is_refused(tmp_path):
    """Version 1 lacked the composite keys on transition history; such a file must be rebuilt."""
    path = str(tmp_path / "v1.sqlite3")
    first = open_database(path)
    first.execute("PRAGMA user_version = 1")
    first.close()
    with pytest.raises(DatabaseVersionError, match="rebuild the database from the seed files"):
        open_database(path)


def test_initialize_schema_refuses_a_database_that_is_not_empty(connection):
    with pytest.raises(DatabaseVersionError):
        initialize_schema(connection)


def test_opening_a_foreign_database_with_tables_but_no_version_is_refused(tmp_path):
    path = str(tmp_path / "foreign.sqlite3")
    raw = connect(path)
    raw.execute("CREATE TABLE something (x TEXT)")
    raw.close()
    with pytest.raises(DatabaseVersionError):
        open_database(path)


def test_development_database_file_persists_across_connections(tmp_path):
    path = str(tmp_path / "dev.sqlite3")
    first = open_database(path)
    first.execute("INSERT INTO accounts (account_id, name) VALUES ('A', 'Synthetic')")
    first.close()
    second = open_database(path)
    try:
        assert second.execute("SELECT name FROM accounts").fetchall() == [("Synthetic",)]
    finally:
        second.close()


def test_transaction_commits_on_success_and_rolls_back_on_error(connection):
    with transaction(connection):
        connection.execute("INSERT INTO accounts (account_id, name) VALUES ('A', 'Synthetic')")
    with pytest.raises(RuntimeError):
        with transaction(connection):
            connection.execute("INSERT INTO accounts (account_id, name) VALUES ('B', 'Synthetic')")
            raise RuntimeError("forced failure")
    assert connection.execute("SELECT account_id FROM accounts").fetchall() == [("A",)]


def test_transactions_are_immediate_write_transactions(connection):
    statements = []
    connection.set_trace_callback(statements.append)
    with transaction(connection):
        pass
    connection.set_trace_callback(None)
    assert statements[0] == "BEGIN IMMEDIATE"


def test_read_only_connection_refuses_writes(connection):
    make_read_only(connection)
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO accounts (account_id, name) VALUES ('A', 'Synthetic')")


def test_working_copy_is_complete_independent_and_writable(connection):
    connection.execute("INSERT INTO accounts (account_id, name) VALUES ('A', 'Synthetic')")
    make_read_only(connection)
    copy = create_working_copy(connection)
    try:
        assert schema_version(copy) == 7
        assert copy.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        copy.execute("INSERT INTO accounts (account_id, name) VALUES ('B', 'Synthetic')")
        assert copy.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        triggers = copy.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
        assert triggers == 34 + len(AI_TRIGGERS) + len(BRIDGE_TRIGGERS) == 92
    finally:
        copy.close()


# --- transaction(): a failed COMMIT never leaves the connection inside the transaction -----


def _deferred_constraint_connection():
    """Test-only tables whose foreign key is checked only at COMMIT."""
    connection = database.connect()
    connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY, "
        "parent_id INTEGER REFERENCES parent (id) DEFERRABLE INITIALLY DEFERRED)"
    )
    return connection


def test_a_commit_failure_rolls_back_and_leaves_the_connection_reusable():
    connection = _deferred_constraint_connection()
    try:
        body_completed = []
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with database.transaction(connection):
                connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 99)")  # deferred: accepted here
                connection.execute("INSERT INTO parent (id) VALUES (7)")
                body_completed.append(True)
        assert body_completed == [True]  # the body finished; COMMIT is what failed
        assert not connection.in_transaction  # the helper rolled back
        assert connection.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM parent").fetchone()[0] == 0
        with database.transaction(connection):  # the same connection is immediately reusable
            connection.execute("INSERT INTO parent (id) VALUES (99)")
            connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 99)")
        assert not connection.in_transaction
        assert connection.execute("SELECT id, parent_id FROM child").fetchall() == [(1, 99)]
    finally:
        connection.close()


def test_a_body_failure_still_rolls_back_and_re_raises_the_original_error():
    connection = _deferred_constraint_connection()
    try:
        with pytest.raises(RuntimeError, match="body failed"):
            with database.transaction(connection):
                connection.execute("INSERT INTO parent (id) VALUES (1)")
                raise RuntimeError("body failed")
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM parent").fetchone()[0] == 0
    finally:
        connection.close()
