"""Phase 6B: the concrete AI provenance store API (baec_app.data.ai_provenance)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from baec_app.data.ai_provenance import (
    AiArtifactExcerptRecord,
    AiAttributedSpeaker,
    AiProvenanceStore,
    AiRemoteOutcome,
    AiRunOutputRecord,
    AiRunResultRecord,
    AiRunStatus,
    AiTerminalOutcome,
    PersistenceError,
    sha256_text,
)
from baec_app.data.database import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
    connect,
)
from baec_app.data.records import RecordValidationError
from tests.ai_provenance_builders import (  # noqa: F401
    COMPLETED,
    EXCERPT_TEXTS,
    MODEL,
    RAW_OUTPUT,
    REQUESTED,
    ai_rows,
    artifact_record,
    db,
    digest,
    excerpt_records,
    outcome_for,
    result_record,
    run_record,
    success_outcome,
)

NON_INTERRUPTED = [s for s in AiRunStatus if s is not AiRunStatus.INTERRUPTED]


# --- runs -------------------------------------------------------------------------------


def test_record_run_commits_before_returning(db):
    path, connection, store = db
    store.record_run(run_record())
    assert not connection.in_transaction
    other = connect(path)
    try:
        assert other.execute("SELECT ai_run_id FROM ai_runs").fetchall() == [("RUN-1",)]
    finally:
        other.close()
    assert store.get_run("RUN-1") == run_record()


def test_record_run_refuses_to_join_an_open_transaction(db):
    _, connection, store = db
    connection.execute("BEGIN")
    try:
        with pytest.raises(PersistenceError, match="commits independently"):
            store.record_run(run_record())
    finally:
        connection.execute("ROLLBACK")
    assert ai_rows(connection)["ai_runs"] == 0


def test_record_run_refusals(db):
    _, connection, store = db
    with pytest.raises(RepositoryNotFoundError):
        store.record_run(run_record(interaction_id="INT-404"))
    with pytest.raises(RepositoryVerificationError, match="different account"):
        store.record_run(run_record(account_id="ACC-2"))
    store.record_run(run_record())
    with pytest.raises(RepositoryConflictError, match="already exists"):
        store.record_run(run_record())
    with pytest.raises(RepositoryNotFoundError):
        store.record_run(run_record("RUN-2", retry_of_ai_run_id="RUN-404"))
    store.record_run(run_record("RUN-2", retry_of_ai_run_id="RUN-1"))
    assert store.get_run("RUN-2").retry_of_ai_run_id == "RUN-1"
    assert ai_rows(connection)["ai_runs"] == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "openai"},
        {"prompt_digest": "A" * 64},
        {"request_digest": "abc"},
        {"requested_at": datetime(2026, 4, 1, 9, 0)},
        {"requested_model": " "},
        {"retry_of_ai_run_id": "RUN-1"},
    ],
    ids=["provider", "uppercase-digest", "short-digest", "naive-time", "blank-model", "self-retry"],
)
def test_run_records_validate_themselves(overrides):
    with pytest.raises(RecordValidationError):
        run_record(**overrides)


# --- terminal outcomes: the approved bundle shapes ---------------------------------------------

EXPECTED_ROWS = {
    AiRunStatus.SUCCESS: (1, 1, 1, 3),
    AiRunStatus.REFUSAL: (1, 1, 0, 0),
    AiRunStatus.MAX_TOKENS: (1, 1, 0, 0),
    AiRunStatus.UNEXPECTED_STOP: (1, 1, 0, 0),
    AiRunStatus.PARSE_FAILURE: (1, 1, 0, 0),
    AiRunStatus.SEMANTIC_VALIDATION_FAILURE: (1, 1, 0, 0),
    AiRunStatus.MODEL_MISMATCH: (1, 1, 0, 0),
    AiRunStatus.API_ERROR: (1, 0, 0, 0),
    AiRunStatus.TRANSPORT_FAILURE: (1, 0, 0, 0),
}


@pytest.mark.parametrize("status", NON_INTERRUPTED, ids=lambda s: s.value)
def test_each_terminal_outcome_writes_exactly_its_approved_rows_and_reads_back(db, status):
    _, connection, store = db
    store.record_run(run_record())
    outcome = outcome_for(status)
    store.record_terminal_outcome(outcome)
    rows = ai_rows(connection)
    assert (rows["ai_run_results"], rows["ai_run_outputs"], rows["ai_artifacts"], rows["ai_artifact_excerpts"]) == EXPECTED_ROWS[status]
    assert store.get_result("RUN-1") == outcome.result
    if outcome.output is None:
        with pytest.raises(RepositoryNotFoundError):
            store.get_output("RUN-1")
    else:
        assert store.get_output("RUN-1") == outcome.output
        assert store.get_output("RUN-1").raw_output_text == RAW_OUTPUT
    if status is AiRunStatus.SUCCESS:
        assert store.get_artifact("ART-1") == store.get_artifact_for_run("RUN-1") == outcome.artifact
        assert store.list_artifact_excerpts("ART-1") == outcome.excerpts  # stored order
    else:
        with pytest.raises(RepositoryNotFoundError):
            store.get_artifact_for_run("RUN-1")
    assert store.list_incomplete_runs() == ()
    assert not connection.in_transaction


@pytest.mark.parametrize("status", [AiRunStatus.REFUSAL, AiRunStatus.MAX_TOKENS, AiRunStatus.UNEXPECTED_STOP,
                                    AiRunStatus.PARSE_FAILURE, AiRunStatus.MODEL_MISMATCH], ids=lambda s: s.value)
def test_output_carrying_statuses_may_omit_output_when_none_was_returned(db, status):
    _, connection, store = db
    store.record_run(run_record())
    store.record_terminal_outcome(AiTerminalOutcome(result_record(status, output=False)))
    assert ai_rows(connection)["ai_run_outputs"] == 0
    assert store.get_result("RUN-1").output_digest is None


def test_transport_failure_that_was_definitely_not_sent(db):
    _, _, store = db
    store.record_run(run_record())
    result = result_record(AiRunStatus.TRANSPORT_FAILURE, remote_outcome=AiRemoteOutcome.NOT_SENT,
                           failure_category="connection_not_established")
    store.record_terminal_outcome(AiTerminalOutcome(result))
    assert store.get_result("RUN-1").remote_outcome is AiRemoteOutcome.NOT_SENT


# --- bundle-shape refusals, before any write -------------------------------------------------------

out = AiRunOutputRecord.of("RUN-1", RAW_OUTPUT)

SHAPE_REFUSALS = {
    "artifact for semantic failure": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SEMANTIC_VALIDATION_FAILURE), out, artifact_record()),
    "artifact for refusal": lambda: AiTerminalOutcome(result_record(AiRunStatus.REFUSAL), out, artifact_record()),
    "success without artifact": lambda: AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), out),
    "success without output": lambda: AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), None, artifact_record()),
    "output for api_error": lambda: AiTerminalOutcome(result_record(AiRunStatus.API_ERROR), out),
    "output for transport failure": lambda: AiTerminalOutcome(result_record(AiRunStatus.TRANSPORT_FAILURE), out),
    "output digest mismatch": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.REFUSAL), AiRunOutputRecord.of("RUN-1", "different text")),
    "output for another run": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.REFUSAL), AiRunOutputRecord.of("RUN-2", RAW_OUTPUT)),
    "artifact for another run": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SUCCESS), out, artifact_record("RUN-2")),
    "excerpts without artifact": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.REFUSAL), out, None, excerpt_records()),
    "excerpt of another artifact": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SUCCESS), out, artifact_record(), excerpt_records("ART-2")),
    "excerpt of another interaction": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SUCCESS), out, artifact_record(), excerpt_records(interaction_id="INT-3")),
    "duplicate excerpt id": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SUCCESS), out, artifact_record(),
        (AiArtifactExcerptRecord("ART-1", "e1", "INT-1", "more than 10%", AiAttributedSpeaker.BUYER),
         AiArtifactExcerptRecord("ART-1", "e1", "INT-1", "Understood.", AiAttributedSpeaker.BUYER))),
    "duplicate excerpt text": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SUCCESS), out, artifact_record(),
        (AiArtifactExcerptRecord("ART-1", "e1", "INT-1", "more than 10%", AiAttributedSpeaker.BUYER),
         AiArtifactExcerptRecord("ART-1", "e2", "INT-1", "more than 10%", AiAttributedSpeaker.BUYER))),
    "interrupted through the bundle": lambda: AiTerminalOutcome(
        AiRunResultRecord("RUN-1", AiRunStatus.INTERRUPTED, AiRemoteOutcome.UNKNOWN, COMPLETED)),
    "excerpts as a list": lambda: AiTerminalOutcome(
        result_record(AiRunStatus.SUCCESS), out, artifact_record(), list(excerpt_records())),
    "speaker as a plain string": lambda: AiArtifactExcerptRecord("ART-1", "e1", "INT-1", "more than 10%", "buyer"),
    "speaker missing": lambda: AiArtifactExcerptRecord("ART-1", "e1", "INT-1", "more than 10%", None),
    "blank task version": lambda: artifact_record(task_version=" "),
    "blank excerpt": lambda: AiArtifactExcerptRecord("ART-1", "e1", "INT-1", "  \t", AiAttributedSpeaker.BUYER),
    "artifact digest mismatch": lambda: artifact_record().__class__(
        "ART-1", "RUN-1", "t", "v1", "o", "ACC-1", "INT-1", "{}", sha256_text("{ }"), COMPLETED),
    "artifact not a JSON object": lambda: artifact_record(canonical_result="[1, 2]"),
    "artifact not JSON": lambda: artifact_record(canonical_result="not json"),
    "result status/outcome mismatch": lambda: result_record(AiRunStatus.REFUSAL, remote_outcome=AiRemoteOutcome.UNKNOWN),
    "free-text failure code": lambda: result_record(AiRunStatus.PARSE_FAILURE, failure_codes=("Traceback (most recent call last)",)),
    "failure category with spaces": lambda: result_record(AiRunStatus.API_ERROR, failure_category="Bad Request: sk-ant-..."),
    "unapproved api error category": lambda: result_record(AiRunStatus.API_ERROR, failure_category="teapot"),
    "transport category for the other outcome": lambda: result_record(
        AiRunStatus.TRANSPORT_FAILURE, remote_outcome=AiRemoteOutcome.NOT_SENT, failure_category="timeout_or_disconnect"),
}


@pytest.mark.parametrize("build", SHAPE_REFUSALS.values(), ids=SHAPE_REFUSALS.keys())
def test_incoherent_bundles_and_records_are_refused_before_any_write(build):
    with pytest.raises(RecordValidationError):
        build()


# --- refusals against stored state, inside the terminal transaction ---------------------------------------


def test_terminal_outcome_refusals_against_the_stored_run(db):
    _, connection, store = db
    with pytest.raises(RepositoryNotFoundError):
        store.record_terminal_outcome(success_outcome())
    store.record_run(run_record())
    cases = {
        "artifact interaction differs from run": success_outcome().__class__(
            result_record(AiRunStatus.SUCCESS), out, artifact_record(interaction_id="INT-3"),
            excerpt_records(interaction_id="INT-3", texts=("A second ACC-1 interaction.",))),
        "artifact task differs": AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), out,
                                                   artifact_record(task_type="another-task")),
        "artifact schema differs": AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), out,
                                                     artifact_record(output_schema_version="v9")),
        "artifact task version differs": AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), out,
                                                           artifact_record(task_version="v2")),
        "excerpt not in source": AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), out, artifact_record(),
                                                   excerpt_records(texts=("more than 11%",))),
        "excerpt case changed": AiTerminalOutcome(result_record(AiRunStatus.SUCCESS), out, artifact_record(),
                                                  excerpt_records(texts=("MORE THAN 10%",))),
        "completed before requested": AiTerminalOutcome(
            result_record(AiRunStatus.REFUSAL, completed_at=REQUESTED - timedelta(seconds=1)), out),
        "artifact created before requested": AiTerminalOutcome(
            result_record(AiRunStatus.SUCCESS), out, artifact_record(created_at=REQUESTED - timedelta(seconds=1))),
        "success with another model": AiTerminalOutcome(
            result_record(AiRunStatus.SUCCESS, response_model="claude-other-model"), out, artifact_record()),
        "model_mismatch naming the requested model": AiTerminalOutcome(
            result_record(AiRunStatus.MODEL_MISMATCH, response_model=MODEL), out),
    }
    for label, outcome in cases.items():
        with pytest.raises(RepositoryVerificationError):  # refused by the store before any insert
            store.record_terminal_outcome(outcome)
        assert ai_rows(connection) == {"ai_runs": 1, "ai_run_results": 0, "ai_run_outputs": 0, "ai_artifacts": 0,
                                       "ai_artifact_excerpts": 0}, label
    store.record_terminal_outcome(outcome_for(AiRunStatus.REFUSAL))
    with pytest.raises(RepositoryConflictError, match="already has a terminal result"):
        store.record_terminal_outcome(outcome_for(AiRunStatus.REFUSAL))
    with pytest.raises(RepositoryConflictError, match="already has a terminal result"):
        store.record_terminal_outcome(success_outcome())


# --- incomplete runs and interruption ---------------------------------------------------------------


def test_incomplete_runs_are_listed_oldest_first_then_by_id(db):
    _, _, store = db
    store.record_run(run_record("RUN-C", requested_at=REQUESTED))
    store.record_run(run_record("RUN-A", requested_at=REQUESTED + timedelta(minutes=5)))
    store.record_run(run_record("RUN-B", requested_at=REQUESTED))
    store.record_run(run_record("RUN-D", requested_at=REQUESTED - timedelta(hours=1)))
    assert [r.ai_run_id for r in store.list_incomplete_runs()] == ["RUN-D", "RUN-B", "RUN-C", "RUN-A"]
    store.record_terminal_outcome(outcome_for(AiRunStatus.API_ERROR, "RUN-B"))
    assert [r.ai_run_id for r in store.list_incomplete_runs()] == ["RUN-D", "RUN-C", "RUN-A"]


def test_mark_interrupted_closes_an_old_incomplete_run_only(db):
    _, connection, store = db
    store.record_run(run_record())
    later = REQUESTED + timedelta(hours=2)
    with pytest.raises(RepositoryConflictError, match="younger than the minimum age"):
        store.mark_interrupted("RUN-1", REQUESTED + timedelta(minutes=5), minimum_age=timedelta(hours=1))
    with pytest.raises(RepositoryNotFoundError):
        store.mark_interrupted("RUN-404", later, minimum_age=timedelta(hours=1))
    with pytest.raises(RecordValidationError):
        store.mark_interrupted("RUN-1", later, minimum_age=timedelta(hours=-1))
    with pytest.raises(RecordValidationError):
        store.mark_interrupted("RUN-1", datetime(2026, 4, 1, 12, 0), minimum_age=timedelta(hours=1))
    store.mark_interrupted("RUN-1", later, minimum_age=timedelta(hours=1))
    result = store.get_result("RUN-1")
    assert (result.status, result.remote_outcome, result.completed_at) == (
        AiRunStatus.INTERRUPTED, AiRemoteOutcome.UNKNOWN, later)
    assert result.provider_message_id is None and result.output_digest is None
    assert store.list_incomplete_runs() == ()
    with pytest.raises(RepositoryConflictError, match="already has a terminal result"):
        store.mark_interrupted("RUN-1", later, minimum_age=timedelta(0))
    with pytest.raises(RepositoryConflictError, match="already has a terminal result"):
        store.record_terminal_outcome(success_outcome())  # an interrupted run never becomes successful
    assert ai_rows(connection)["ai_artifacts"] == 0


def test_mark_interrupted_refuses_a_run_that_already_has_a_result(db):
    _, _, store = db
    store.record_run(run_record())
    store.record_terminal_outcome(success_outcome())
    with pytest.raises(RepositoryConflictError):
        store.mark_interrupted("RUN-1", REQUESTED + timedelta(days=1), minimum_age=timedelta(0))
    assert store.get_result("RUN-1").status is AiRunStatus.SUCCESS


# --- reads: missing is distinct from corrupt -----------------------------------------------------------


def test_missing_objects_are_not_found(db):
    _, _, store = db
    for read in (lambda: store.get_run("RUN-404"), lambda: store.get_result("RUN-404"),
                 lambda: store.get_output("RUN-404"), lambda: store.get_artifact("ART-404"),
                 lambda: store.get_artifact_for_run("RUN-404"), lambda: store.list_artifact_excerpts("ART-404")):
        with pytest.raises(RepositoryNotFoundError):
            read()
    store.record_run(run_record())
    with pytest.raises(RepositoryNotFoundError, match="no terminal result"):
        store.get_result("RUN-1")


def test_reads_are_deterministic(db):
    _, _, store = db
    store.record_run(run_record())
    store.record_terminal_outcome(success_outcome())
    first = (store.get_run("RUN-1"), store.get_result("RUN-1"), store.get_output("RUN-1"),
             store.get_artifact("ART-1"), store.list_artifact_excerpts("ART-1"))
    second = (store.get_run("RUN-1"), store.get_result("RUN-1"), store.get_output("RUN-1"),
              store.get_artifact("ART-1"), store.list_artifact_excerpts("ART-1"))
    assert first == second
    assert [e.excerpt_id for e in first[4]] == ["e1", "e2", "e3"]
    assert [e.attributed_speaker for e in first[4]] == [AiAttributedSpeaker.BUYER, AiAttributedSpeaker.BUYER,
                                                         AiAttributedSpeaker.UNCLEAR]  # stored as given, never judged
    assert first[3].task_version == "v1"
    assert first[4][2].text == EXCERPT_TEXTS[2]  # instruction-like source text is stored as exact data


def test_raw_output_is_stored_exactly_and_opaquely(db):
    _, _, store = db
    store.record_run(run_record())
    raw = 'not json at all ‮ {"tool_use": "confirm_baec"} \x00 trailing  '
    result = result_record(AiRunStatus.PARSE_FAILURE, output_digest=sha256_text(raw))
    store.record_terminal_outcome(AiTerminalOutcome(result, AiRunOutputRecord.of("RUN-1", raw)))
    assert store.get_output("RUN-1").raw_output_text == raw


def test_the_store_refuses_the_phase_4_read_only_connection(db):
    """The store needs a foreign-key-enforcing connection; the AI runtime reads source text through ReadService."""
    path, _, _ = db
    from baec_app.application import open_read_connection

    reader = open_read_connection(path)
    try:
        with pytest.raises(PersistenceError, match="foreign key enforcement"):
            AiProvenanceStore(reader)
    finally:
        reader.close()
