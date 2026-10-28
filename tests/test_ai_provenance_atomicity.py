"""Phase 6B: terminal atomicity (design §10.5).

A run is committed on its own before the attempt. A terminal outcome is committed
all at once or not at all: a failure anywhere in the bundle leaves the run
incomplete, with no result, output, artifact, or excerpt.
"""

from dataclasses import replace

import pytest

from baec_app.data.ai_provenance import AiArtifactExcerptRecord, AiAttributedSpeaker, AiProvenanceStore, AiRunStatus
from baec_app.data.database import RepositoryConflictError, connect
from tests.ai_provenance_builders import ai_rows, db, outcome_for, run_record, success_outcome  # noqa: F401

ONLY_THE_RUN = {"ai_runs": 1, "ai_run_results": 0, "ai_run_outputs": 0, "ai_artifacts": 0, "ai_artifact_excerpts": 0}


def install_failure(connection, table, when="1"):
    """A test-only trigger that makes one insert inside the terminal transaction fail."""
    connection.execute(
        f"CREATE TRIGGER test_injected_failure BEFORE INSERT ON {table} WHEN {when} "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )


def remove_failure(connection):
    connection.execute("DROP TRIGGER test_injected_failure")


def assert_run_left_incomplete(path, connection, store):
    assert not connection.in_transaction
    assert ai_rows(connection) == ONLY_THE_RUN
    other = connect(path)  # what another connection sees after the rollback
    try:
        assert ai_rows(other) == ONLY_THE_RUN
    finally:
        other.close()
    assert [run.ai_run_id for run in store.list_incomplete_runs()] == ["RUN-1"]


@pytest.mark.parametrize(
    "table,when",
    [
        ("ai_run_results", "1"),
        ("ai_run_outputs", "1"),
        ("ai_artifacts", "1"),
        ("ai_artifact_excerpts", "NEW.excerpt_id = 'e1'"),
        ("ai_artifact_excerpts", "NEW.excerpt_id = 'e3'"),
    ],
    ids=["at-result", "at-output", "at-artifact", "at-first-excerpt", "at-last-excerpt"],
)
def test_a_failure_anywhere_in_a_success_bundle_rolls_the_whole_outcome_back(db, table, when):
    path, connection, store = db
    store.record_run(run_record())
    install_failure(connection, table, when)
    with pytest.raises(RepositoryConflictError, match="injected failure"):
        store.record_terminal_outcome(success_outcome())
    assert_run_left_incomplete(path, connection, store)
    remove_failure(connection)
    store.record_terminal_outcome(success_outcome())  # nothing partial was left behind to block it
    assert store.get_result("RUN-1").status is AiRunStatus.SUCCESS
    assert len(store.list_artifact_excerpts("ART-1")) == 3


@pytest.mark.parametrize("table", ["ai_run_results", "ai_run_outputs"])
def test_a_failure_in_a_failure_bundle_leaves_no_result_or_output(db, table):
    path, connection, store = db
    store.record_run(run_record())
    install_failure(connection, table)
    with pytest.raises(RepositoryConflictError, match="injected failure"):
        store.record_terminal_outcome(outcome_for(AiRunStatus.SEMANTIC_VALIDATION_FAILURE))
    assert_run_left_incomplete(path, connection, store)


def test_a_python_error_after_some_inserts_rolls_everything_back(db, monkeypatch):
    path, connection, store = db
    store.record_run(run_record())
    real = AiProvenanceStore._insert_excerpt
    calls = []

    def fail_on_second(self, excerpt):
        calls.append(excerpt.excerpt_id)
        if len(calls) == 2:
            raise RuntimeError("process failure mid-bundle")
        return real(self, excerpt)

    monkeypatch.setattr(AiProvenanceStore, "_insert_excerpt", fail_on_second)
    with pytest.raises(RuntimeError, match="mid-bundle"):
        store.record_terminal_outcome(success_outcome())
    assert calls == ["e1", "e2"]  # the result, output, artifact and one excerpt had been inserted
    assert_run_left_incomplete(path, connection, store)


def test_a_refused_success_bundle_is_never_partially_successful(db, monkeypatch):
    """The DB-level fidelity trigger refuses the last excerpt; the store's Python check is bypassed to reach it."""
    path, connection, store = db
    store.record_run(run_record())
    outcome = success_outcome()
    bad = AiArtifactExcerptRecord("ART-1", "e3", "INT-1", "never said this", AiAttributedSpeaker.UNCLEAR)
    sneaky = replace(outcome, excerpts=outcome.excerpts[:-1] + (bad,))
    original = AiProvenanceStore._interaction_text
    monkeypatch.setattr(AiProvenanceStore, "_interaction_text",
                        lambda self, interaction_id: "never said this " + original(self, interaction_id))
    with pytest.raises(RepositoryConflictError, match="must occur verbatim"):
        store.record_terminal_outcome(sneaky)
    assert_run_left_incomplete(path, connection, store)


def test_the_run_commit_is_independent_of_a_later_failed_terminal_transaction(db):
    path, connection, store = db
    store.record_run(run_record())
    install_failure(connection, "ai_run_results")
    with pytest.raises(RepositoryConflictError):
        store.record_terminal_outcome(outcome_for(AiRunStatus.API_ERROR))
    remove_failure(connection)
    reopened = connect(path)
    try:
        assert AiProvenanceStore(reopened).get_run("RUN-1") == run_record()
    finally:
        reopened.close()


def test_mark_interrupted_is_atomic_too(db):
    from datetime import timedelta

    from tests.ai_provenance_builders import REQUESTED

    path, connection, store = db
    store.record_run(run_record())
    install_failure(connection, "ai_run_results")
    with pytest.raises(RepositoryConflictError):
        store.mark_interrupted("RUN-1", REQUESTED + timedelta(hours=2), minimum_age=timedelta(hours=1))
    assert_run_left_incomplete(path, connection, store)
