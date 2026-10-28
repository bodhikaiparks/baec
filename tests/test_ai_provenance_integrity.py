"""Phase 6B: stored AI provenance that has been corrupted is never returned.

Corruption is simulated with the established tamper() helper, which switches every
guard off for one statement and restores all of them afterwards.
"""

from dataclasses import replace

import pytest

from baec_app.data.ai_provenance import AiRunStatus
from baec_app.data.database import PersistenceIntegrityError, RepositoryNotFoundError
from tests.ai_provenance_builders import ai_rows, db, outcome_for, run_record, success_outcome  # noqa: F401
from tests.persistence_builders import tamper


@pytest.fixture
def stored(db):
    path, connection, store = db
    store.record_run(run_record())
    store.record_terminal_outcome(success_outcome())
    store.record_run(run_record("RUN-2"))
    store.record_terminal_outcome(outcome_for(AiRunStatus.SEMANTIC_VALIDATION_FAILURE, "RUN-2"))
    triggers = connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name").fetchall()
    yield connection, store
    # every protection is back after each tamper
    assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name").fetchall() == triggers
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def every_read(store, run_id="RUN-1", artifact_id="ART-1"):
    return [lambda: store.get_result(run_id), lambda: store.get_output(run_id),
            lambda: store.get_artifact(artifact_id), lambda: store.get_artifact_for_run(run_id),
            lambda: store.list_artifact_excerpts(artifact_id)]


def assert_fails_closed(reads):
    for read in reads:
        with pytest.raises(PersistenceIntegrityError):
            read()


CORRUPTIONS = {
    "raw output changed without its digest": "UPDATE ai_run_outputs SET raw_output_text = raw_output_text || ' ' WHERE ai_run_id = 'RUN-1'",
    "raw output digest changed": f"UPDATE ai_run_outputs SET output_digest = '{'0' * 64}' WHERE ai_run_id = 'RUN-1'",
    "result output digest changed": f"UPDATE ai_run_results SET output_digest = '{'1' * 64}' WHERE ai_run_id = 'RUN-1'",
    "result forgets its output": "UPDATE ai_run_results SET output_digest = NULL WHERE ai_run_id = 'RUN-1'",
    "raw output deleted": "DELETE FROM ai_run_outputs WHERE ai_run_id = 'RUN-1'",
    "artifact content changed without its digest": "UPDATE ai_artifacts SET canonical_result = '{\"analysis_status\":\"no_clear_baec_language\"}'",
    "artifact digest changed": f"UPDATE ai_artifacts SET artifact_digest = '{'2' * 64}'",
    "artifact made non-JSON with a matching digest": None,  # built below
    "excerpt text not in the interaction": "UPDATE ai_artifact_excerpts SET text = 'more than 11%' WHERE excerpt_id = 'e2'",
    "excerpt rebound to another interaction": "UPDATE ai_artifact_excerpts SET interaction_id = 'INT-3' WHERE excerpt_id = 'e1'",
    "excerpt made whitespace-only": "UPDATE ai_artifact_excerpts SET text = ' ' WHERE excerpt_id = 'e1'",
    "artifact rebound to another interaction": "UPDATE ai_artifacts SET interaction_id = 'INT-3'",
    "artifact rebound to another account": "UPDATE ai_artifacts SET account_id = 'ACC-2', interaction_id = 'INT-2'",
    "artifact task changed": "UPDATE ai_artifacts SET task_type = 'another-task'",
    "artifact task version changed": "UPDATE ai_artifacts SET task_version = 'v2'",
    "excerpt speaker outside the vocabulary": "UPDATE ai_artifact_excerpts SET attributed_speaker = 'customer' WHERE excerpt_id = 'e1'",
    "artifact schema changed": "UPDATE ai_artifacts SET output_schema_version = 'v9'",
    "result turned into a semantic failure": "UPDATE ai_run_results SET status = 'semantic_validation_failure', failure_codes = '[\"x\"]' WHERE ai_run_id = 'RUN-1'",
    "result response model changed": "UPDATE ai_run_results SET response_model = 'claude-other-model' WHERE ai_run_id = 'RUN-1'",
    "result completed before the request": "UPDATE ai_run_results SET completed_at = '2020-01-01T00:00:00.000000+00:00' WHERE ai_run_id = 'RUN-1'",
    "result status unknown": "UPDATE ai_run_results SET status = 'request_not_sent' WHERE ai_run_id = 'RUN-1'",
    "successful run loses its artifact": "DELETE FROM ai_artifacts",
    "run rebound to another account": "UPDATE ai_runs SET account_id = 'ACC-2' WHERE ai_run_id = 'RUN-1'",
    "run digest corrupted": "UPDATE ai_runs SET request_digest = 'not-a-digest' WHERE ai_run_id = 'RUN-1'",
    "run timestamp corrupted": "UPDATE ai_runs SET requested_at = 'yesterday' WHERE ai_run_id = 'RUN-1'",
}


@pytest.mark.parametrize("label", CORRUPTIONS)
def test_corrupted_successful_provenance_fails_closed(stored, label):
    connection, store = stored
    sql = CORRUPTIONS[label]
    if sql is None:
        from baec_app.data.ai_provenance import sha256_text

        sql = f"UPDATE ai_artifacts SET canonical_result = 'not json', artifact_digest = '{sha256_text('not json')}'"
    tamper(connection, sql)
    reads = every_read(store)
    if label.startswith(("artifact", "excerpt")):
        # Result and output reads check only that the artifact exists; the artifact reads verify it.
        reads = reads[2:]
    if label == "successful run loses its artifact":
        reads = [lambda: store.get_result("RUN-1"), lambda: store.get_output("RUN-1")]
    if label.startswith("run "):
        reads.append(lambda: store.get_run("RUN-1"))
        reads.append(lambda: store.list_incomplete_runs() or store.get_run("RUN-1"))
    assert_fails_closed(reads)


def test_a_semantic_failure_that_gains_an_artifact_fails_closed(stored):
    connection, store = stored
    tamper(connection, "INSERT INTO ai_artifacts SELECT 'ART-X', 'RUN-2', task_type, task_version, output_schema_version, "
                       "account_id, interaction_id, canonical_result, artifact_digest, created_at FROM ai_artifacts")
    assert_fails_closed([lambda: store.get_result("RUN-2"), lambda: store.get_artifact("ART-X"),
                         lambda: store.get_artifact_for_run("RUN-2")])


def test_duplicate_excerpt_text_cannot_be_written_even_with_guards_off(stored):
    connection, store = stored
    with pytest.raises(Exception, match="UNIQUE"):
        tamper(connection, "UPDATE ai_artifact_excerpts SET text = 'more than 10%' WHERE excerpt_id = 'e1'")
    assert [e.text for e in store.list_artifact_excerpts("ART-1")][1] == "more than 10%"


def test_a_second_result_cannot_be_smuggled_in_even_with_guards_off(stored):
    connection, store = stored
    with pytest.raises(Exception):
        tamper(connection, "INSERT INTO ai_run_results (ai_run_id, status, remote_outcome, completed_at) "
                           "VALUES ('RUN-1', 'interrupted', 'unknown', '2026-04-02T00:00:00.000000+00:00')")
    assert store.get_result("RUN-1").status is AiRunStatus.SUCCESS


def test_a_unicode_whitespace_only_excerpt_that_sql_permits_still_fails_closed_on_load(db):
    """Accepted layering: the SQL trigger trims ASCII whitespace only, so it lets a Unicode-whitespace-only
    excerpt through; the store refuses it on write and fails closed on load."""
    from datetime import timedelta

    from baec_app.data.ai_provenance import AiArtifactExcerptRecord, AiAttributedSpeaker
    from baec_app.data.records import RecordValidationError, SourceInteraction
    from baec_app.data.repository import Repository
    from tests.ai_provenance_builders import REQUESTED

    _, connection, store = db
    Repository(connection).add_interaction(
        SourceInteraction("INT-U", "ACC-1", REQUESTED - timedelta(days=1), "Buyer:\u2003\u00a0maybe later.")
    )
    store.record_run(run_record(interaction_id="INT-U"))
    outcome = success_outcome()
    artifact = replace(outcome.artifact, interaction_id="INT-U")
    store.record_terminal_outcome(replace(outcome, artifact=artifact, excerpts=(
        AiArtifactExcerptRecord("ART-1", "e1", "INT-U", "maybe later.", AiAttributedSpeaker.BUYER),)))
    with pytest.raises(RecordValidationError):  # the store refuses it on write
        AiArtifactExcerptRecord("ART-1", "e2", "INT-U", "\u2003\u00a0", AiAttributedSpeaker.BUYER)
    # SQL-level protection permits it: no guard needs to be switched off.
    connection.execute("INSERT INTO ai_artifact_excerpts VALUES ('ART-1', 'e2', 'INT-U', ?, 'buyer')", ("\u2003\u00a0",))
    with pytest.raises(PersistenceIntegrityError):
        store.list_artifact_excerpts("ART-1")
    with pytest.raises(PersistenceIntegrityError):
        store.get_artifact("ART-1")


def test_an_incomplete_run_with_a_corrupted_row_fails_closed_in_the_listing(db):
    _, connection, store = db
    store.record_run(run_record())
    tamper(connection, "UPDATE ai_runs SET provider = 'openai'")
    with pytest.raises(PersistenceIntegrityError):
        store.list_incomplete_runs()


def test_missing_and_corrupt_stay_distinguishable(stored):
    connection, store = stored
    with pytest.raises(RepositoryNotFoundError):
        store.get_artifact("ART-404")
    tamper(connection, "UPDATE ai_artifacts SET artifact_digest = '" + "3" * 64 + "'")
    with pytest.raises(PersistenceIntegrityError):
        store.get_artifact("ART-1")
    assert ai_rows(connection)["ai_artifacts"] == 1
