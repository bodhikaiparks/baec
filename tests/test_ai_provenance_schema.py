"""Phase 6B: the five AI provenance tables, checked at the SQLite level (introduced in schema versions 5 and 6;
the current repository schema is version 7, which left them unchanged)."""

import ast
import hashlib
import sqlite3
from pathlib import Path

import pytest

from baec_app.application import open_read_connection
from baec_app.data import ai_provenance
from baec_app.data.ai_provenance import AiAttributedSpeaker, AiProvenanceStore, AiRemoteOutcome, AiRunStatus, sha256_text
from baec_app.data.database import (
    AI_APPEND_ONLY_TABLES,
    AI_PROVENANCE_TABLES,
    AI_REMOTE_OUTCOMES,
    AI_REPLACE_GUARDED_KEYS,
    AI_RUN_STATUSES,
    AI_SPEAKER_LABELS,
    ALL_TABLES,
    BRIDGE_TABLES,
    SCHEMA_VERSION,
    DatabaseVersionError,
    open_database,
    schema_version,
    table_counts,
)
from baec_app.data.seed import build_canonical_seed_database, build_seed_database
from tests.ai_provenance_builders import RAW_OUTPUT, ai_rows, db, digest, outcome_for, run_record, success_outcome  # noqa: F401
from tests.test_rc33_schema_guards import _minimal_unique_keys

AI_TABLES = ("ai_runs", "ai_run_results", "ai_run_outputs", "ai_artifacts", "ai_artifact_excerpts")
PHASE3_TABLES = (
    "schema_meta", "accounts", "interactions", "interaction_evidence", "human_authorizations", "baec_records",
    "criterion_assessments", "criterion_evidence", "stringency_expressions", "dormancy_judgments",
    "evaluation_evidence", "non_evaluation_evidence", "account_state_transitions",
)


def _tables(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )}


# --- version, tables, and no migration -------------------------------------------------


def test_a_new_database_is_schema_version_5_with_exactly_the_previous_tables_plus_five():
    """Schema version 6 since Phase 6D-B1, and 7 since Phase 7C, which added only the seven bridge tables
    (name kept for ID continuity)."""
    connection = open_database()
    try:
        assert SCHEMA_VERSION == schema_version(connection) == 7
        assert AI_PROVENANCE_TABLES == AI_TABLES
        assert _tables(connection) == set(PHASE3_TABLES) | set(AI_TABLES) | set(BRIDGE_TABLES) == set(ALL_TABLES)
        strict = {row[1]: row[5] for row in connection.execute("PRAGMA table_list") if row[1] in AI_TABLES}
        assert strict == {table: 1 for table in AI_TABLES}
        AiProvenanceStore(connection)  # opens normally
    finally:
        connection.close()


def _file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a_schema_v4_database_is_refused_everywhere_and_never_modified(tmp_path):
    """No migration: a version-4 file is refused by every opener and left byte-identical."""
    path = tmp_path / "v4.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE accounts (account_id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT) STRICT")
    connection.execute("PRAGMA user_version = 4")
    connection.commit()
    connection.close()
    before = _file_digest(path)
    with pytest.raises(DatabaseVersionError):
        open_database(str(path))
    with pytest.raises(DatabaseVersionError):
        open_read_connection(path)
    checker = sqlite3.connect(path)
    try:
        assert checker.execute("PRAGMA user_version").fetchone()[0] == 4
        assert _tables(checker) == {"accounts"}
    finally:
        checker.close()
    assert _file_digest(path) == before


def test_the_store_refuses_a_connection_at_another_schema_version_or_without_foreign_keys(tmp_path):
    connection = open_database()
    try:
        connection.execute("PRAGMA user_version = 4")
        with pytest.raises(DatabaseVersionError):
            AiProvenanceStore(connection)
        connection.execute("PRAGMA user_version = 6")
        connection.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(ai_provenance.PersistenceError):
            AiProvenanceStore(connection)
    finally:
        connection.close()
    with pytest.raises(ai_provenance.PersistenceError):
        AiProvenanceStore("not a connection")


def test_the_canonical_seed_is_schema_v5_with_empty_ai_tables_and_unchanged_demo_data():
    """Schema version 6 since Phase 6D-B1, and 7 since Phase 7C (name kept for ID continuity)."""
    canonical = build_canonical_seed_database()
    built = build_seed_database()
    try:
        assert schema_version(canonical) == 7
        counts = table_counts(canonical)
        assert {table: counts[table] for table in AI_TABLES} == {table: 0 for table in AI_TABLES}
        states = canonical.execute("SELECT account_id, state FROM accounts ORDER BY rowid").fetchall()
        assert states == [("ACC-HARBOR", "CONDITIONALLY_DORMANT"), ("ACC-SUMMIT", "NO_PLAUSIBLE_PATH"),
                          ("ACC-MERIDIAN", "ACTIVE_OPPORTUNITY")]
        assert table_counts(built) == counts
    finally:
        canonical.close()
        built.close()


# --- vocabulary ------------------------------------------------------------------------


def test_status_and_outcome_vocabularies_are_exactly_the_approved_ones():
    assert tuple(s.value for s in AiRunStatus) == AI_RUN_STATUSES == (
        "success", "refusal", "max_tokens", "unexpected_stop", "api_error", "transport_failure",
        "parse_failure", "semantic_validation_failure", "model_mismatch", "interrupted",
    )
    assert "request_not_sent" not in AI_RUN_STATUSES
    assert tuple(o.value for o in AiRemoteOutcome) == AI_REMOTE_OUTCOMES == ("response_received", "not_sent", "unknown")
    assert tuple(s.value for s in AiAttributedSpeaker) == AI_SPEAKER_LABELS == ("buyer", "seller", "unclear")


def test_the_approved_column_sets_and_no_deferred_provider_fields(db):
    _, connection, _ = db
    columns = {table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] for table in AI_TABLES}
    assert columns == {
        "ai_runs": ["ai_run_id", "provider", "task_type", "task_version", "account_id", "interaction_id",
                    "requested_model", "sdk_name", "sdk_version", "prompt_version", "prompt_digest", "input_version",
                    "input_digest", "output_schema_version", "output_schema_digest", "canonicalization_version",
                    "request_spec_version", "request_digest", "validation_version", "requested_at",
                    "retry_of_ai_run_id"],
        "ai_run_results": ["ai_run_id", "status", "remote_outcome", "provider_message_id", "response_model",
                           "stop_reason", "provider_request_id", "input_tokens", "output_tokens",
                           "cache_creation_input_tokens", "cache_read_input_tokens", "failure_category",
                           "failure_codes", "output_digest", "completed_at"],
        "ai_run_outputs": ["ai_run_id", "raw_output_text", "output_digest"],
        "ai_artifacts": ["artifact_id", "ai_run_id", "task_type", "task_version", "output_schema_version",
                         "account_id", "interaction_id", "canonical_result", "artifact_digest", "created_at"],
        "ai_artifact_excerpts": ["artifact_id", "excerpt_id", "interaction_id", "text", "attributed_speaker"],
    }
    every = {column for names in columns.values() for column in names}
    for forbidden in ("api_key", "authorization", "headers", "api_version_header", "beta_headers", "thinking",
                      "reasoning", "traceback", "prompt_text"):
        assert not any(forbidden in column for column in every)


# --- append-only and replace guards -----------------------------------------------------


def _populated(db):
    path, connection, store = db
    store.record_run(run_record())
    store.record_terminal_outcome(success_outcome())
    assert ai_rows(connection) == {"ai_runs": 1, "ai_run_results": 1, "ai_run_outputs": 1, "ai_artifacts": 1,
                                   "ai_artifact_excerpts": 3}
    return connection


@pytest.mark.parametrize("table", AI_TABLES)
def test_ai_tables_refuse_update_and_delete(db, table):
    connection = _populated(db)
    column = next(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} is append-only"):
        connection.execute(f"UPDATE {table} SET {column} = {column}")
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} is append-only"):
        connection.execute(f"DELETE FROM {table}")
    assert ai_rows(connection)[table] >= 1


@pytest.mark.parametrize("verb", ["REPLACE", "INSERT OR REPLACE", "INSERT OR IGNORE"])
@pytest.mark.parametrize("table", AI_TABLES)
def test_ai_rows_cannot_be_replaced_or_silently_skipped(db, table, verb):
    connection = _populated(db)
    before = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    for row in before:
        with pytest.raises(sqlite3.IntegrityError, match=f"{table} rows cannot be replaced"):
            connection.execute(f"{verb} INTO {table} VALUES ({', '.join('?' * len(row))})", row)
    assert connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == before


def test_replace_guards_cover_every_unique_key_of_the_ai_tables(db):
    _, connection, _ = db
    assert AI_APPEND_ONLY_TABLES == AI_TABLES and set(AI_REPLACE_GUARDED_KEYS) == set(AI_TABLES)
    for table, declared in AI_REPLACE_GUARDED_KEYS.items():
        assert {frozenset(key) for key in declared} == _minimal_unique_keys(connection, table), table


# --- run constraints ---------------------------------------------------------------------

RUN_SQL = (
    "INSERT INTO ai_runs VALUES (?, 'anthropic', 'baec-evidence-extraction', 'v1', ?, ?, 'm', 'anthropic', '1', "
    "'p', ?, 'i', ?, 'o', ?, 'c', 's', ?, 'baec-extraction-validation/v1', '2026-04-01T09:00:00.000000+00:00', NULL)"
)


def _raw_run(connection, run_id="RAW-1", account="ACC-1", interaction="INT-1", digests=None):
    digests = digests or [digest(x) for x in ("p", "i", "o", "r")]
    connection.execute(RUN_SQL, (run_id, account, interaction, *digests))


def test_a_runs_interaction_must_belong_to_its_account_at_the_database_level(db):
    _, connection, _ = db
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _raw_run(connection, account="ACC-2", interaction="INT-1")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _raw_run(connection, interaction="INT-404")
    _raw_run(connection)


@pytest.mark.parametrize(
    "bad", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63, "", "z" * 64],
    ids=["uppercase", "short", "long", "non-hex", "space", "empty", "z"],
)
def test_digest_columns_require_lowercase_sha256_hex(db, bad):
    _, connection, _ = db
    for position in range(4):
        digests = [digest(x) for x in ("p", "i", "o", "r")]
        digests[position] = bad
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _raw_run(connection, digests=digests)


def test_provider_is_anthropic_and_a_run_is_never_its_own_retry(db):
    _, connection, _ = db
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(RUN_SQL.replace("'anthropic', 'baec", "'openai', 'baec"),
                           ("RAW-1", "ACC-1", "INT-1", *[digest(x) for x in "pior"]))
    _raw_run(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO ai_runs SELECT 'RAW-2', provider, task_type, task_version, account_id, "
                           "interaction_id, requested_model, sdk_name, sdk_version, prompt_version, prompt_digest, "
                           "input_version, input_digest, output_schema_version, output_schema_digest, "
                           "canonicalization_version, request_spec_version, request_digest, validation_version, "
                           "requested_at, 'RAW-2' "
                           "FROM ai_runs WHERE ai_run_id = 'RAW-1'")


# --- result constraints --------------------------------------------------------------------

RESULT_SQL = (
    "INSERT INTO ai_run_results (ai_run_id, status, remote_outcome, provider_message_id, response_model, "
    "stop_reason, failure_category, failure_codes, output_digest, completed_at, input_tokens) "
    "VALUES ('RAW-1', ?, ?, ?, ?, ?, ?, ?, ?, '2026-04-01T09:00:20.000000+00:00', ?)"
)
D = digest("out")


def _result(status, outcome, message=None, model=None, stop=None, category=None, codes=None, out=None, tokens=None):
    return (status, outcome, message, model, stop, category, codes, out, tokens)


VALID_RESULTS = {
    "success": _result("success", "response_received", "msg", "m", "end_turn", out=D, tokens=5),
    "refusal": _result("refusal", "response_received", stop="refusal"),
    "max_tokens with output": _result("max_tokens", "response_received", stop="max_tokens", out=D),
    "unexpected_stop": _result("unexpected_stop", "response_received", stop="pause_turn"),
    "api_error": _result("api_error", "response_received", category="rate_limited"),
    "transport not sent": _result("transport_failure", "not_sent", category="connection_not_established"),
    "transport unknown": _result("transport_failure", "unknown", category="timeout_or_disconnect"),
    "parse_failure": _result("parse_failure", "response_received", codes='["invalid_json"]'),
    "semantic failure": _result("semantic_validation_failure", "response_received", codes='["excerpt_not_verbatim"]',
                                out=D),
    "model_mismatch": _result("model_mismatch", "response_received", model="other"),
    "interrupted": _result("interrupted", "unknown"),
}
INVALID_RESULTS = {
    "unknown status": _result("request_not_sent", "not_sent"),
    "unknown outcome": _result("refusal", "maybe", stop="refusal"),
    "response status not received": _result("refusal", "unknown", stop="refusal"),
    "parse failure not sent": _result("parse_failure", "not_sent"),
    "transport received": _result("transport_failure", "response_received", category="timeout_or_disconnect"),
    "transport category mismatch": _result("transport_failure", "not_sent", category="timeout_or_disconnect"),
    "transport without category": _result("transport_failure", "unknown"),
    "interrupted not unknown": _result("interrupted", "not_sent"),
    "interrupted with output": _result("interrupted", "unknown", out=D),
    "interrupted with model": _result("interrupted", "unknown", model="m"),
    "transport with usage": _result("transport_failure", "unknown", category="timeout_or_disconnect", tokens=1),
    "api_error with output": _result("api_error", "response_received", category="other", out=D),
    "api_error unknown category": _result("api_error", "response_received", category="teapot"),
    "api_error without category": _result("api_error", "response_received"),
    "success without output": _result("success", "response_received", "msg", "m", "end_turn"),
    "success without message": _result("success", "response_received", None, "m", "end_turn", out=D),
    "success wrong stop": _result("success", "response_received", "msg", "m", "max_tokens", out=D),
    "success null stop": _result("success", "response_received", "msg", "m", None, out=D),
    "success with category": _result("success", "response_received", "msg", "m", "end_turn", category="other", out=D),
    "refusal wrong stop": _result("refusal", "response_received", stop="end_turn"),
    "refusal null stop": _result("refusal", "response_received"),
    "max_tokens null stop": _result("max_tokens", "response_received"),
    "unexpected_stop that is expected": _result("unexpected_stop", "response_received", stop="end_turn"),
    "unexpected_stop null stop": _result("unexpected_stop", "response_received"),
    "semantic failure without codes": _result("semantic_validation_failure", "response_received", out=D),
    "semantic failure without output": _result("semantic_validation_failure", "response_received",
                                               codes='["excerpt_not_verbatim"]'),
    "codes on refusal": _result("refusal", "response_received", stop="refusal", codes='["x"]'),
    "model_mismatch without model": _result("model_mismatch", "response_received"),
    "malformed output digest": _result("refusal", "response_received", stop="refusal", out="ABC"),
    "negative tokens": _result("refusal", "response_received", stop="refusal", tokens=-1),
}


@pytest.mark.parametrize("values", VALID_RESULTS.values(), ids=VALID_RESULTS.keys())
def test_every_approved_status_and_outcome_shape_is_accepted_by_sqlite(db, values):
    _, connection, _ = db
    _raw_run(connection)  # requested_model 'm'
    connection.execute(RESULT_SQL, values)


@pytest.mark.parametrize("values", INVALID_RESULTS.values(), ids=INVALID_RESULTS.keys())
def test_invalid_status_outcome_combinations_are_refused_by_sqlite(db, values):
    _, connection, _ = db
    _raw_run(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(RESULT_SQL, values)
    assert ai_rows(connection)["ai_run_results"] == 0


def test_success_and_model_mismatch_are_bound_to_the_requested_model(db):
    _, connection, _ = db
    _raw_run(connection)  # requested_model 'm'
    with pytest.raises(sqlite3.IntegrityError, match="response model disagrees"):
        connection.execute(RESULT_SQL, _result("success", "response_received", "msg", "other", "end_turn", out=D))
    with pytest.raises(sqlite3.IntegrityError, match="response model disagrees"):
        connection.execute(RESULT_SQL, _result("model_mismatch", "response_received", model="m"))


def test_a_run_has_at_most_one_terminal_result(db):
    _, connection, _ = db
    _raw_run(connection)
    connection.execute(RESULT_SQL, VALID_RESULTS["refusal"])
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(RESULT_SQL, VALID_RESULTS["interrupted"])


# --- outputs, artifacts and excerpts --------------------------------------------------------


def test_output_requires_a_result_naming_the_same_digest(db):
    _, connection, _ = db
    _raw_run(connection)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY|requires a result"):
        connection.execute("INSERT INTO ai_run_outputs VALUES ('RAW-1', 'x', ?)", (sha256_text("x"),))
    connection.execute(RESULT_SQL, VALID_RESULTS["refusal"])  # no output digest recorded
    with pytest.raises(sqlite3.IntegrityError, match="requires a result"):
        connection.execute("INSERT INTO ai_run_outputs VALUES ('RAW-1', 'x', ?)", (sha256_text("x"),))


ARTIFACT_SQL = (
    "INSERT INTO ai_artifacts VALUES ('ART-RAW', 'RAW-1', 'baec-evidence-extraction', 'v1', 'o', ?, ?, '{}', ?, "
    "'2026-04-01T09:00:20.000000+00:00')"
)


@pytest.mark.parametrize("status", ["refusal", "max_tokens with output", "semantic failure", "parse_failure",
                                    "model_mismatch", "interrupted", "api_error", "transport unknown"])
def test_an_artifact_for_a_non_success_result_is_refused(db, status):
    """Refused because of the status itself: statuses that carry output get their output row first."""
    _, connection, _ = db
    _raw_run(connection)
    connection.execute(RESULT_SQL, VALID_RESULTS[status])
    if VALID_RESULTS[status][7] is not None:
        connection.execute("INSERT INTO ai_run_outputs VALUES ('RAW-1', 'out', ?)", (D,))
    with pytest.raises(sqlite3.IntegrityError, match="requires a successful run"):
        connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))


def test_an_artifact_requires_a_result_and_the_successful_runs_output(db):
    _, connection, _ = db
    _raw_run(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))  # no result at all
    out = sha256_text(RAW_OUTPUT)
    connection.execute(RESULT_SQL, _result("success", "response_received", "msg", "m", "end_turn", out=out))
    with pytest.raises(sqlite3.IntegrityError, match="requires a successful run"):
        connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))  # output missing
    connection.execute("INSERT INTO ai_run_outputs VALUES ('RAW-1', ?, ?)", (RAW_OUTPUT, out))
    connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))


def _successful_raw_artifact(connection):
    out = sha256_text(RAW_OUTPUT)
    _raw_run(connection)
    connection.execute(RESULT_SQL, _result("success", "response_received", "msg", "m", "end_turn", out=out))
    connection.execute("INSERT INTO ai_run_outputs VALUES ('RAW-1', ?, ?)", (RAW_OUTPUT, out))


def test_an_artifact_is_bound_to_its_runs_account_interaction_task_and_schema(db):
    """One composite foreign key binds account, interaction, task type, task version, and output schema version."""
    _, connection, _ = db
    _successful_raw_artifact(connection)
    for account, interaction in (("ACC-1", "INT-3"), ("ACC-2", "INT-2")):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(ARTIFACT_SQL, (account, interaction, sha256_text("{}")))
    for original, changed in (("'o', ?", "'other-schema', ?"), ("'v1', 'o'", "'v2', 'o'"),
                              ("'baec-evidence-extraction', 'v1'", "'another-task', 'v1'")):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(ARTIFACT_SQL.replace(original, changed), ("ACC-1", "INT-1", sha256_text("{}")))
    connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))
    with pytest.raises(sqlite3.IntegrityError):  # at most one artifact per run
        connection.execute(ARTIFACT_SQL.replace("'ART-RAW'", "'ART-RAW-2'"), ("ACC-1", "INT-1", sha256_text("{}")))


EXCERPT_SQL = "INSERT INTO ai_artifact_excerpts VALUES ('ART-RAW', ?, ?, ?, 'buyer')"


@pytest.mark.parametrize(
    "interaction,text",
    [
        ("INT-1", "more than 11%"),
        ("INT-1", "MORE THAN 10%"),
        ("INT-1", ""),
        ("INT-1", "   "),
        ("INT-1", "\n\t"),
        ("INT-1", "more than 10%."),
        ("INT-3", "A second ACC-1 interaction."),
        ("INT-2", "Something else."),
    ],
    ids=["absent", "case-changed", "empty", "spaces", "control-whitespace", "extended", "other-interaction-same-account",
         "other-account"],
)
def test_excerpts_must_be_non_blank_exact_substrings_of_their_artifacts_interaction(db, interaction, text):
    _, connection, _ = db
    _successful_raw_artifact(connection)
    connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(EXCERPT_SQL, ("e1", interaction, text))
    connection.execute(EXCERPT_SQL, ("e1", "INT-1", "more than 10%"))


def test_excerpt_ids_are_unique_per_artifact_and_text_is_never_duplicated_under_another_id(db):
    _, connection, _ = db
    _successful_raw_artifact(connection)
    connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))
    connection.execute(EXCERPT_SQL, ("e1", "INT-1", "more than 10%"))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(EXCERPT_SQL, ("e1", "INT-1", "Understood."))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(EXCERPT_SQL, ("e2", "INT-1", "more than 10%"))
    connection.execute(EXCERPT_SQL, ("e2", "INT-1", "Understood."))


@pytest.mark.parametrize("speaker", ["Buyer", "BUYER", "customer", "", " buyer", None, 1])
def test_excerpt_speaker_is_exactly_buyer_seller_or_unclear(db, speaker):
    _, connection, _ = db
    _successful_raw_artifact(connection)
    connection.execute(ARTIFACT_SQL, ("ACC-1", "INT-1", sha256_text("{}")))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO ai_artifact_excerpts VALUES ('ART-RAW', 'e1', 'INT-1', 'more than 10%', ?)",
                           (speaker,))
    for index, valid in enumerate(("buyer", "seller", "unclear"), 1):
        text = ("more than 10%", "Understood.", "Seller:")[index - 1]
        connection.execute("INSERT INTO ai_artifact_excerpts VALUES ('ART-RAW', ?, 'INT-1', ?, ?)",
                           (f"e{index}", text, valid))


def test_an_artifact_may_have_zero_excerpts(db):
    _, connection, store = db
    store.record_run(run_record())
    outcome = success_outcome(texts=())
    store.record_terminal_outcome(outcome)
    assert store.list_artifact_excerpts("ART-1") == ()


# --- the persistence module's own boundaries --------------------------------------------------

MODULE = Path(ai_provenance.__file__)


def _code_strings(tree):
    docstrings = {
        id(node.body[0].value) for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
        and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_the_store_never_uses_replace_ignore_or_upsert_sql():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for text in _code_strings(tree):
        upper = text.upper()
        for forbidden in ("REPLACE", "OR IGNORE", "ON CONFLICT", "UPSERT", "UPDATE ", "DELETE "):
            assert forbidden not in upper, text
        if "INSERT" in upper:
            assert text.startswith("INSERT INTO "), text


def test_the_store_imports_only_the_data_layer_and_the_standard_library():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"__future__", "hashlib", "json", "os", "re", "sqlite3", "contextlib", "dataclasses", "datetime",
                        "enum", "pathlib", "typing", "baec_app.data.database", "baec_app.data.records",
                        "baec_app.data.repository"}  # os and pathlib: open_ai_provenance_store (6C-B)
    source = MODULE.read_text(encoding="utf-8")
    for name in ("HumanAuthorization", "HumanApproval", "HumanConfirmationGate", "HumanCommandFacade",
                 "ProposalOrigin", "baec_app.ai", "baec_app.mcp", "anthropic", "baec_app.application"):
        assert name not in source.replace('"anthropic"', "").replace("'anthropic'", ""), name


def test_the_data_package_surface_is_not_broadened():
    package = Path(ai_provenance.__file__).parent / "__init__.py"
    assert package.read_text(encoding="utf-8") == ""
