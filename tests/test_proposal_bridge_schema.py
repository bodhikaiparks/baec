"""Phase 7C: schema version 7 and the seven bridge tables, checked at the SQLite level.

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §13. Raw SQL is used on purpose:
these rules must hold even for code that bypasses every application and store check.
All data is test-only fixture data (tests/bridge_builders.py, design D15).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from baec_app.data import database
from baec_app.data.ai_provenance import AiProvenanceStore
from baec_app.data.database import (
    AI_PROVENANCE_TABLES,
    BRIDGE_APPEND_ONLY_TABLES,
    BRIDGE_INDEXES,
    BRIDGE_INTEGRITY_TRIGGERS,
    BRIDGE_REPLACE_GUARDED_KEYS,
    BRIDGE_TABLES,
    DATA_TABLES,
    SCHEMA_VERSION,
    DatabaseVersionError,
    PersistenceError,
    connect,
    initialize_schema,
    migrate_v6_to_v7,
    open_database,
    schema_version,
)
from baec_app.data.proposal_bridge import ProposalBridgeStore, ReviewDecision
from baec_app.data.repository import Repository, encode_datetime
from baec_app.data.seed import build_seed_database
from tests.bridge_builders import (  # noqa: F401
    ACC,
    ACTOR,
    GRANT_ISSUED,
    INT,
    INT_SAME_ACCOUNT,
    OTHER_ACC,
    T0,
    accepted_lineage,
    add_artifact,
    bridge,
    canonical,
    confirmed_baec,
    consumption_values,
    decision_record,
    grant_values,
    insert_row,
    link_values,
    proposal_content,
    proposal_record,
    revision_content,
    revision_record,
)
from tests.test_rc33_schema_guards import _minimal_unique_keys

# The exact version-6 schema (Phase 6, commit 3f780f3): 72 statements creating 72 objects. Version 7 is
# additive, so these pins must never change.
V6_STATEMENTS_DIGEST = "259827cebc199cfe1cc0d1bcc3493e1128161dbc094ade96ae5ad20ce7c224bd"
V6_OBJECTS_DIGEST = "2755a0ea693dc946c9a5ecc2b620f86bfb560247ab2757f5995c851515c5a70a"


def refused(connection, sql, parameters=(), match=None):
    with pytest.raises(sqlite3.IntegrityError, match=match):
        connection.execute(sql, parameters)


def refused_row(connection, table, values, match=None, verb="INSERT"):
    with pytest.raises(sqlite3.IntegrityError, match=match):
        insert_row(connection, table, values, verb)


def objects(connection):
    return sorted(connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall())


def v6_database(path=":memory:"):
    connection = connect(path)
    for statement in database._schema_statements_v6():
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 6")
    return connection


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- version 7 and the v6 pins --------------------------------------------------------------


def test_the_version_6_schema_is_byte_identical_and_version_7_only_adds():
    statements = database._schema_statements_v6()
    assert len(statements) == 72
    assert hashlib.sha256("\n;\n".join(statements).encode()).hexdigest() == V6_STATEMENTS_DIGEST
    reference = v6_database()
    try:
        assert len(objects(reference)) == 72
        assert hashlib.sha256(repr(objects(reference)).encode()).hexdigest() == V6_OBJECTS_DIGEST
    finally:
        reference.close()
    assert database._schema_statements() == statements + database._bridge_statements()


def test_a_fresh_database_is_created_directly_at_version_7_with_exactly_seven_more_tables():
    connection = open_database()
    try:
        assert SCHEMA_VERSION == schema_version(connection) == 7
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
        assert BRIDGE_TABLES == ("ai_proposals", "ai_proposal_review_revisions", "ai_proposal_review_decisions",
                                 "human_authorization_grants", "human_authorization_grant_supersessions",
                                 "ai_proposal_confirmations", "human_authorization_grant_consumptions")
        assert set(BRIDGE_TABLES) <= tables and len(tables) == 25
        assert DATA_TABLES[-7:] == BRIDGE_TABLES and DATA_TABLES[12:17] == AI_PROVENANCE_TABLES
        strict = {row[1]: row[5] for row in connection.execute("PRAGMA table_list") if row[1] in BRIDGE_TABLES}
        assert strict == {table: 1 for table in BRIDGE_TABLES}
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")}
        assert indexes == set(BRIDGE_INDEXES)
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        expected = ({f"{t}_no_{op}" for t in BRIDGE_APPEND_ONLY_TABLES for op in ("update", "delete")}
                    | {f"{t}_no_replace" for t in BRIDGE_REPLACE_GUARDED_KEYS} | set(BRIDGE_INTEGRITY_TRIGGERS))
        assert expected <= triggers and len(expected) == 38
    finally:
        connection.close()


def test_every_bridge_foreign_key_restricts_deletes():
    connection = open_database()
    try:
        for table in BRIDGE_TABLES:
            keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            assert keys, table
            assert {row[6] for row in keys} == {"RESTRICT"}, table
    finally:
        connection.close()


def test_replace_guards_cover_every_bridge_unique_key_including_the_partial_rejection_index():
    connection = open_database()
    try:
        assert set(BRIDGE_REPLACE_GUARDED_KEYS) == set(BRIDGE_TABLES) == set(BRIDGE_APPEND_ONLY_TABLES)
        for table, declared in BRIDGE_REPLACE_GUARDED_KEYS.items():
            partial = {row[1] for row in connection.execute(f"PRAGMA index_list({table})") if row[4] == 1}
            keys = _minimal_unique_keys(connection, table)
            if table == "ai_proposal_review_decisions":
                assert partial == {"ai_proposal_review_decisions_one_rejection"}
                keys -= {frozenset({"proposal_id"})}  # the partial index, guarded by its own condition
            else:
                assert partial == set()
            assert {frozenset(key) for key in declared} == keys, table
    finally:
        connection.close()


# --- migration v6 -> v7 -------------------------------------------------------------------------


def populated_v6(path):
    """A version-6 file holding the canonical seed's domain rows and a stored AI artifact."""
    source = build_seed_database()
    add_source = Repository(source)
    from baec_app.data.records import SourceInteraction
    from baec_app.domain.models import Account
    add_source.add_account(Account(ACC, "Fixture Harbor"))
    add_source.add_interaction(SourceInteraction(INT, ACC, T0, "Buyer: " + "If our supplier raises pricing by more "
                                                 "than 10% when our agreement renews, we'd evaluate other options."))
    add_artifact(AiProvenanceStore(source), "FIXTURE-ART-1", "FIXTURE-RUN-1", ACC, INT)
    target = v6_database(str(path))
    for table in DATA_TABLES[:17]:
        columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
        rows = source.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid").fetchall()
        target.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                           rows)
    source.close()
    return target


def dump_v6(connection):
    return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("schema_meta",) + DATA_TABLES[:17]}


def test_a_populated_version_6_database_migrates_to_7_preserving_every_row(tmp_path):
    connection = populated_v6(tmp_path / "v6.sqlite3")
    try:
        before, before_objects = dump_v6(connection), objects(connection)
        assert sum(len(rows) for rows in before.values()) > 40 and before["ai_artifacts"]
        migrate_v6_to_v7(connection)
        assert schema_version(connection) == 7
        assert dump_v6(connection) == before  # every Phase 1-6 row, field for field
        assert set(before_objects) <= set(objects(connection))  # no version-6 object changed
        fresh = open_database()
        try:
            assert objects(connection) == objects(fresh)  # identical to a database created at version 7
        finally:
            fresh.close()
        assert all(connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0 for t in BRIDGE_TABLES)
        repository = Repository(connection)
        assert {a.account_id for a in repository.list_accounts()} >= {ACC, "ACC-HARBOR"}
        assert repository.get_baec_record("BAEC-HARBOR-001").baec_id == "BAEC-HARBOR-001"
        assert AiProvenanceStore(connection).get_artifact("FIXTURE-ART-1").account_id == ACC
        ProposalBridgeStore(connection)  # opens normally at version 7
    finally:
        connection.close()


def test_the_migration_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "v6.sqlite3"
    connection = populated_v6(path)
    try:
        before, before_objects = dump_v6(connection), objects(connection)
        real = database._bridge_statements
        monkeypatch.setattr(database, "_bridge_statements", lambda: real() + ["CREATE TABLE broken ("])
        with pytest.raises(sqlite3.Error):
            migrate_v6_to_v7(connection)
        assert not connection.in_transaction
        assert schema_version(connection) == 6
        assert objects(connection) == before_objects and dump_v6(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize("version", [0, 1, 5, 7, 8, 99])
def test_only_version_6_is_migrated(tmp_path, version):
    path = tmp_path / "other.sqlite3"
    connection = v6_database(str(path))
    connection.execute(f"PRAGMA user_version = {version}")
    connection.close()
    digest = file_digest(path)
    connection = connect(str(path))
    try:
        with pytest.raises(DatabaseVersionError):
            migrate_v6_to_v7(connection)
    finally:
        connection.close()
    assert file_digest(path) == digest


@pytest.mark.parametrize("tamper", [
    "CREATE TABLE ai_proposals (x TEXT) STRICT",
    "DROP TRIGGER interactions_no_replace",
    "CREATE INDEX extra ON accounts (name)",
])
def test_a_version_6_file_with_any_other_schema_is_refused_unchanged(tmp_path, tamper):
    path = tmp_path / "v6.sqlite3"
    connection = v6_database(str(path))
    connection.execute(tamper)
    connection.close()
    digest = file_digest(path)
    connection = connect(str(path))
    try:
        with pytest.raises(DatabaseVersionError, match="exactly the version-6 schema"):
            migrate_v6_to_v7(connection)
        assert schema_version(connection) == 6
    finally:
        connection.close()
    assert file_digest(path) == digest


def test_the_migration_requires_foreign_key_enforcement():
    connection = v6_database()
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(PersistenceError):
            migrate_v6_to_v7(connection)
        assert schema_version(connection) == 6
    finally:
        connection.close()


def test_opening_a_version_6_file_is_refused_with_the_upgrade_path_and_never_migrates(tmp_path):
    path = tmp_path / "v6.sqlite3"
    v6_database(str(path)).close()
    digest = file_digest(path)
    with pytest.raises(DatabaseVersionError, match="migrate_v6_to_v7"):
        open_database(str(path))
    assert file_digest(path) == digest


def test_a_future_schema_version_fails_closed_everywhere(tmp_path):
    path = tmp_path / "future.sqlite3"
    connection = open_database(str(path))
    connection.execute("PRAGMA user_version = 8")
    with pytest.raises(DatabaseVersionError):
        ProposalBridgeStore(connection)
    connection.close()
    with pytest.raises(DatabaseVersionError):
        open_database(str(path))


def test_repeated_initialization_and_opening_never_duplicate_or_change_schema_objects(tmp_path):
    path = str(tmp_path / "v7.sqlite3")
    first = open_database(path)
    created = objects(first)
    with pytest.raises(DatabaseVersionError):
        initialize_schema(first)
    with pytest.raises(DatabaseVersionError):
        migrate_v6_to_v7(first)
    first.close()
    for _ in range(2):
        again = open_database(path)
        assert objects(again) == created
        again.close()


# --- ai_proposals ---------------------------------------------------------------------------


def proposal_row(bridge, artifact=None, **overrides):
    artifact = artifact or bridge.artifact
    record = proposal_record(artifact, account_id=artifact.account_id, interaction_id=artifact.interaction_id)
    values = {c: getattr(record, c) for c in ("proposal_id", "artifact_id", "artifact_digest", "account_id",
                                                "interaction_id", "content", "proposal_digest", "created_by",
                                                "origin", "mapping_version", "content_version")}
    values["created_at"] = encode_datetime(record.created_at)
    values.update(overrides)
    return values


def test_a_proposal_binds_to_an_existing_eligible_artifact_of_its_own_account_and_interaction(bridge):
    connection = bridge.connection
    refused_row(connection, "ai_proposals", proposal_row(bridge, artifact_id="FIXTURE-NO-SUCH-ARTIFACT"))
    refused_row(connection, "ai_proposals", proposal_row(bridge, account_id=OTHER_ACC), match="same account")
    refused_row(connection, "ai_proposals", proposal_row(bridge, interaction_id=INT_SAME_ACCOUNT))
    refused_row(connection, "ai_proposals", proposal_row(bridge, artifact_digest="0" * 64), match="eligible")
    refused_row(connection, "ai_proposals", proposal_row(bridge, bridge.artifact_v1), match="eligible")
    assert bridge.count("ai_proposals") == 0
    insert_row(connection, "ai_proposals", proposal_row(bridge))
    assert bridge.count("ai_proposals") == 1


def test_the_proposal_foreign_keys_bind_artifact_and_interaction_to_the_account(bridge):
    keys = {(row[2], tuple()) for row in bridge.execute("PRAGMA foreign_key_list(ai_proposals)")}
    assert {table for table, _ in keys} == {"ai_artifacts", "interactions"}
    bridge.execute("DROP TRIGGER ai_proposals_artifact_binding")  # prove the foreign keys alone refuse
    refused_row(bridge.connection, "ai_proposals", proposal_row(bridge, artifact_id="FIXTURE-NO-SUCH-ARTIFACT"),
                match="FOREIGN KEY")
    refused_row(bridge.connection, "ai_proposals", proposal_row(bridge, account_id=OTHER_ACC), match="FOREIGN KEY")


@pytest.mark.parametrize("column,value", [
    ("origin", "AI_MODEL"), ("origin", "HUMAN_DRAFT"), ("mapping_version", "baec-ai-proposal-mapping/v2"),
    ("content_version", "baec-ai-proposal-content/v2"), ("content", "not json"), ("content", "[1]"),
    ("proposal_digest", "ABC"), ("created_by", " \t"), ("proposal_id", "proposal-1"),
])
def test_proposal_columns_are_closed(bridge, column, value):
    refused_row(bridge.connection, "ai_proposals", proposal_row(bridge, **{column: value}))


def test_one_proposal_per_artifact_and_mapping_version(bridge):
    insert_row(bridge.connection, "ai_proposals", proposal_row(bridge))
    refused_row(bridge.connection, "ai_proposals", proposal_row(bridge, proposal_id="aiprop_fixture2"))
    assert bridge.count("ai_proposals") == 1


# --- review revisions -----------------------------------------------------------------------


def revision_row(revision, **overrides):
    values = {c: getattr(revision, c) for c in (
        "review_revision_id", "proposal_id", "proposal_digest", "account_id", "interaction_id", "artifact_id",
        "revision_number", "previous_revision_id", "content", "review_content_digest", "actor_label",
        "review_content_version", "normalization_validation_version")}
    values["created_at"] = encode_datetime(revision.created_at)
    values.update(overrides)
    return values


@pytest.fixture
def proposal(bridge):
    record = proposal_record(bridge.artifact)
    bridge.store.add_proposal(record)
    return record


@pytest.mark.parametrize("column,value", [
    ("proposal_digest", "0" * 64), ("account_id", OTHER_ACC), ("interaction_id", INT_SAME_ACCOUNT),
    ("artifact_id", "FIXTURE-ART-3"), ("proposal_id", "aiprop_other"),
])
def test_a_revision_is_bound_to_its_proposal(bridge, proposal, column, value):
    refused_row(bridge.connection, "ai_proposal_review_revisions", revision_row(revision_record(proposal),
                                                                                **{column: value}))
    assert bridge.count("ai_proposal_review_revisions") == 0


def test_revisions_form_one_gap_free_chain(bridge, proposal):
    connection = bridge.connection
    first = revision_record(proposal)
    insert_row(connection, "ai_proposal_review_revisions", revision_row(first))
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(revision_record(proposal, review_revision_id="aireview_dup")), match="latest")
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(revision_record(proposal, 3, first.review_revision_id)), match="latest")
    candidate = revision_record(proposal, 2, first.review_revision_id)
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(candidate, previous_revision_id=None))  # revision > 1 must name its previous
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(candidate, previous_revision_id="aireview_unknown"))
    second = revision_record(proposal, 2, first.review_revision_id)
    insert_row(connection, "ai_proposal_review_revisions", revision_row(second))
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(revision_record(proposal, 3, first.review_revision_id)), match="latest")
    assert bridge.count("ai_proposal_review_revisions") == 2


@pytest.mark.parametrize("column,value", [
    ("review_content_version", "baec-ai-review-content/v2"),
    ("normalization_validation_version", "baec-extraction-validation/v2"),
    ("content", "{"), ("review_content_digest", "x"), ("actor_label", ""), ("revision_number", 0),
])
def test_revision_columns_are_closed(bridge, proposal, column, value):
    refused_row(bridge.connection, "ai_proposal_review_revisions",
                revision_row(revision_record(proposal), **{column: value}))


# --- review decisions -----------------------------------------------------------------------


def decision_row(record, **overrides):
    values = dict(decision_id=record.decision_id, proposal_id=record.proposal_id,
                  review_revision_id=record.review_revision_id, account_id=record.account_id,
                  decision=record.decision.value, actor_label=record.actor_label,
                  decided_at=encode_datetime(record.decided_at))
    values.update(overrides)
    return values


@pytest.fixture
def revised(bridge, proposal):
    revision = revision_record(proposal)
    bridge.store.add_revision(revision)
    return proposal, revision


def test_decisions_are_a_closed_vocabulary_bound_to_their_proposal(bridge, revised):
    proposal, revision = revised
    connection = bridge.connection
    accept = decision_record(proposal, revision.review_revision_id)
    for overrides in ({"decision": "MAYBE"}, {"decision": "accepted"}, {"review_revision_id": None},
                      {"account_id": OTHER_ACC}, {"proposal_id": "aiprop_other"}, {"decision_id": "decision-1"}):
        refused_row(connection, "ai_proposal_review_decisions", decision_row(accept, **overrides))
    insert_row(connection, "ai_proposal_review_decisions", decision_row(accept))
    refused_row(connection, "ai_proposal_review_decisions", decision_row(accept, decision_id="aidecision_again"))


def test_only_the_latest_revision_can_be_accepted(bridge, revised):
    proposal, first = revised
    bridge.store.add_revision(revision_record(proposal, 2, first.review_revision_id))
    refused_row(bridge.connection, "ai_proposal_review_decisions",
                decision_row(decision_record(proposal, first.review_revision_id)), match="latest")


def test_a_rejection_is_terminal_and_happens_once(bridge, revised):
    proposal, revision = revised
    connection = bridge.connection
    reject = decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject")
    insert_row(connection, "ai_proposal_review_decisions", decision_row(reject))
    refused_row(connection, "ai_proposal_review_decisions",
                decision_row(reject, decision_id="aidecision_reject2"), match="rejection|cannot be replaced")
    connection.execute("DROP TRIGGER ai_proposal_review_decisions_no_replace")
    connection.execute("DROP TRIGGER ai_proposal_review_decisions_terminal")
    refused_row(connection, "ai_proposal_review_decisions", decision_row(reject, decision_id="aidecision_reject3"),
                match="UNIQUE")  # the partial unique index alone also refuses a second rejection


def test_nothing_follows_a_rejection(bridge, revised):
    proposal, revision = revised
    connection = bridge.connection
    insert_row(connection, "ai_proposal_review_decisions",
               decision_row(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject")))
    refused_row(connection, "ai_proposal_review_decisions",
                decision_row(decision_record(proposal, revision.review_revision_id)), match="rejection")
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(revision_record(proposal, 2, revision.review_revision_id)), match="rejected")


def full_dump(connection):
    return {t: connection.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
            for t in ("schema_meta",) + DATA_TABLES}


def test_a_rejection_is_audit_history_only(bridge):
    proposal, revision, accept = accepted_lineage(bridge)
    grant = grant_values(revision, accept.decision_id)
    insert_row(bridge.connection, "human_authorization_grants", grant)
    before = full_dump(bridge.connection)
    bridge.store.add_decision(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject"))
    after = full_dump(bridge.connection)
    changed = {t for t in after if after[t] != before[t]}
    assert changed == {"ai_proposal_review_decisions", "human_authorization_grant_supersessions"}
    assert after["human_authorization_grant_supersessions"] == [
        (grant["grant_id"], "PROPOSAL_REJECTED", None, "aidecision_reject", ACTOR,
         encode_datetime(T0 + timedelta(minutes=30)))]
    refused_row(bridge.connection, "human_authorization_grants",
                grant_values(revision, accept.decision_id, n=2, sequence=2, supersedes=grant["grant_id"]))


# --- grants and supersession --------------------------------------------------------------


@pytest.fixture
def lineage(bridge):
    return accepted_lineage(bridge)


@pytest.mark.parametrize("overrides", [
    {"account_id": OTHER_ACC}, {"interaction_id": INT_SAME_ACCOUNT}, {"artifact_id": "FIXTURE-ART-2"},
    {"review_content_digest": "0" * 64}, {"proposal_digest": "0" * 64}, {"proposal_id": "aiprop_other"},
], ids=["account", "interaction", "artifact", "content digest", "proposal digest", "proposal"])
def test_a_grant_is_bound_to_its_account_lineage_and_reviewed_content(bridge, lineage, overrides):
    _, revision, accept = lineage
    refused_row(bridge.connection, "human_authorization_grants", grant_values(revision, accept.decision_id,
                                                                              **overrides))
    assert bridge.count("human_authorization_grants") == 0


@pytest.mark.parametrize("overrides", [
    {"action": "CHANGE_ACCOUNT_STATE"}, {"action": "RECORD_DORMANCY_JUDGMENT"}, {"action": "confirm_baec"},
    {"ttl_seconds": 901}, {"grant_format": "baec-human-grant/v2"}, {"issuing_surface": "mcp"},
    {"grant_id": "grant_" + "G" * 64}, {"grant_id": "grant_123"}, {"baec_id": "B-1"},
    {"expires_at": encode_datetime(GRANT_ISSUED + timedelta(minutes=16))}, {"expires_at": "never"},
    {"issue_sequence": 2}, {"grant_digest": "nope"},
])
def test_grant_columns_are_closed(bridge, lineage, overrides):
    _, revision, accept = lineage
    refused_row(bridge.connection, "human_authorization_grants",
                grant_values(revision, accept.decision_id, **overrides))


def test_a_grant_requires_an_accepted_latest_revision(bridge, proposal):
    first = revision_record(proposal)
    bridge.store.add_revision(first)
    refused_row(bridge.connection, "human_authorization_grants", grant_values(first, "aidecision_none"))
    bridge.store.add_decision(decision_record(proposal, first.review_revision_id))
    second = revision_record(proposal, 2, first.review_revision_id)
    bridge.store.add_revision(second)
    refused_row(bridge.connection, "human_authorization_grants",
                grant_values(first, "aidecision_fixture1"), match="accepted latest revision")


def test_at_most_one_unretired_grant_per_revision_and_action(bridge, lineage):
    _, revision, accept = lineage
    connection = bridge.connection
    first = grant_values(revision, accept.decision_id)
    insert_row(connection, "human_authorization_grants", first)
    refused_row(connection, "human_authorization_grants", grant_values(revision, accept.decision_id, n=2),
                match="one unretired grant")
    refused_row(connection, "human_authorization_grants",
                grant_values(revision, accept.decision_id, n=2, sequence=3, supersedes=first["grant_id"]))
    reissue = grant_values(revision, accept.decision_id, n=2, sequence=2, supersedes=first["grant_id"])
    insert_row(connection, "human_authorization_grants", reissue)  # a re-issue names exactly its predecessor
    refused_row(connection, "human_authorization_grants",
                grant_values(revision, accept.decision_id, n=3, sequence=2, supersedes=first["grant_id"]))
    refused_row(connection, "human_authorization_grants", grant_values(revision, accept.decision_id, n=3,
                                                                       sequence=3, supersedes=first["grant_id"]))
    bridge.store.verify_bridge_integrity()


def test_a_new_revision_supersedes_unretired_grants_in_the_same_statement(bridge, lineage):
    proposal, revision, accept = lineage
    grant = grant_values(revision, accept.decision_id)
    insert_row(bridge.connection, "human_authorization_grants", grant)
    second = revision_record(proposal, 2, revision.review_revision_id)
    bridge.store.add_revision(second)
    assert bridge.execute("SELECT * FROM human_authorization_grant_supersessions").fetchall() == [
        (grant["grant_id"], "REVISION_SUPERSEDED", second.review_revision_id, None, ACTOR,
         encode_datetime(second.created_at))]
    assert bridge.execute("SELECT * FROM human_authorization_grants").fetchall()[0][0] == grant["grant_id"]


def test_invalid_supersessions_are_refused(bridge, lineage):
    proposal, revision, accept = lineage
    connection = bridge.connection
    grant = grant_values(revision, accept.decision_id)
    insert_row(connection, "human_authorization_grants", grant)
    other_proposal, other_revision, _ = accepted_lineage(bridge, bridge.artifact_same_account, "aiprop_fixture2", "2")
    base = dict(grant_id=grant["grant_id"], reason="REVISION_SUPERSEDED", superseding_revision_id=None,
                rejection_decision_id=None, actor_label=ACTOR, superseded_at=encode_datetime(T0))
    for overrides in (
        {"superseding_revision_id": revision.review_revision_id},  # not later than the granted revision
        {"superseding_revision_id": other_revision.review_revision_id},  # another proposal's revision
        {"reason": "PROPOSAL_REJECTED", "rejection_decision_id": "aidecision_fixture1"},  # an acceptance
        {"reason": "EXPIRED", "superseding_revision_id": revision.review_revision_id},
        {"reason": "PROPOSAL_REJECTED", "superseding_revision_id": revision.review_revision_id},
        {"grant_id": "grant_" + "f" * 64, "superseding_revision_id": revision.review_revision_id},
    ):
        refused_row(connection, "human_authorization_grant_supersessions", dict(base, **overrides))
    assert bridge.count("human_authorization_grant_supersessions") == 0
    assert other_proposal.proposal_id != proposal.proposal_id


def test_a_consumed_grant_cannot_be_superseded_or_succeeded(bridge, lineage):
    proposal, revision, accept = lineage
    connection = bridge.connection
    grant = grant_values(revision, accept.decision_id)
    insert_row(connection, "human_authorization_grants", grant)
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    insert_row(connection, "ai_proposal_confirmations", link_values(grant, authorization_id))
    insert_row(connection, "human_authorization_grant_consumptions", consumption_values(grant))
    refused_row(connection, "human_authorization_grants",
                grant_values(revision, accept.decision_id, n=2, sequence=2, supersedes=grant["grant_id"]))
    refused_row(connection, "ai_proposal_review_revisions",
                revision_row(revision_record(proposal, 2, revision.review_revision_id)), match="confirmed")
    refused_row(connection, "ai_proposal_review_decisions",
                decision_row(decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_r")))
    assert bridge.count("human_authorization_grant_supersessions") == 0
    bridge.store.verify_bridge_integrity()


# --- confirmations and consumptions --------------------------------------------------------


@pytest.fixture
def granted(bridge, lineage):
    _, revision, accept = lineage
    grant = grant_values(revision, accept.decision_id)
    insert_row(bridge.connection, "human_authorization_grants", grant)
    return grant


def test_a_full_lineage_links_one_baec_and_one_consumption(bridge, granted):
    connection = bridge.connection
    authorization_id = confirmed_baec(bridge, granted["baec_id"])
    insert_row(connection, "ai_proposal_confirmations", link_values(granted, authorization_id))
    insert_row(connection, "human_authorization_grant_consumptions", consumption_values(granted))
    bridge.store.verify_bridge_integrity()
    refused_row(connection, "human_authorization_grant_consumptions", consumption_values(granted))  # single use
    connection.execute("DROP TRIGGER human_authorization_grant_consumptions_no_replace")
    refused_row(connection, "human_authorization_grant_consumptions", consumption_values(granted),
                match="UNIQUE")  # the keys alone make a consumption single-use
    assert bridge.execute("SELECT state FROM accounts WHERE account_id = ?", (ACC,)).fetchone() == (None,)


def test_one_lineage_yields_at_most_one_confirmation(bridge, lineage, granted):
    _, revision, accept = lineage
    connection = bridge.connection
    insert_row(connection, "ai_proposal_confirmations",
               link_values(granted, confirmed_baec(bridge, granted["baec_id"])))
    # A second grant on the same lineage (a re-issue of the unconsumed first) and a second confirmed BAEC.
    second_issued = GRANT_ISSUED + timedelta(minutes=20)
    second = grant_values(revision, accept.decision_id, n=2, sequence=2, supersedes=granted["grant_id"],
                          issued_at=second_issued)
    refused_row(connection, "human_authorization_grants", second, match="open proposal")  # first layer
    connection.execute("DROP TRIGGER human_authorization_grants_accepted")  # simulate code that bypasses it
    insert_row(connection, "human_authorization_grants", second)
    second_link = link_values(second, confirmed_baec(bridge, second["baec_id"], authorized_at=second_issued))
    refused_row(connection, "ai_proposal_confirmations", second_link)
    connection.execute("DROP TRIGGER ai_proposal_confirmations_no_replace")  # the UNIQUE keys alone refuse
    refused_row(connection, "ai_proposal_confirmations", second_link, match="UNIQUE")
    assert bridge.count("ai_proposal_confirmations") == 1


def test_identical_text_in_different_lineages_is_never_deduplicated(bridge):
    connection = bridge.connection
    confirmed = []
    for n, artifact in enumerate((bridge.artifact, bridge.artifact_same_account, bridge.artifact_other), start=1):
        _, revision, accept = accepted_lineage(bridge, artifact, f"aiprop_fixture{n}", str(n))
        grant = grant_values(revision, accept.decision_id, n=n)
        insert_row(connection, "human_authorization_grants", grant)
        authorization_id = confirmed_baec(bridge, grant["baec_id"], interaction_id=artifact.interaction_id,
                                          account_id=artifact.account_id)
        insert_row(connection, "ai_proposal_confirmations", link_values(grant, authorization_id))
        insert_row(connection, "human_authorization_grant_consumptions", consumption_values(grant))
        confirmed.append(grant["baec_id"])
    statements = {row[0] for row in connection.execute(
        "SELECT buyer_exact_statement FROM baec_records WHERE baec_id IN (?, ?, ?)", tuple(confirmed))}
    assert len(statements) == 1 and bridge.count("ai_proposal_confirmations") == 3


@pytest.mark.parametrize("problem", ["other account", "actor", "time", "no grant authorization"])
def test_a_confirmation_must_carry_the_authorization_derived_from_its_grant(bridge, granted, problem):
    connection = bridge.connection
    if problem == "other account":
        authorization_id = confirmed_baec(bridge, granted["baec_id"], interaction_id="FIXTURE-INT-2",
                                          account_id=OTHER_ACC)
    elif problem == "actor":
        authorization_id = confirmed_baec(bridge, granted["baec_id"], actor="someone-else")
    elif problem == "time":
        authorization_id = confirmed_baec(bridge, granted["baec_id"], authorized_at=GRANT_ISSUED + timedelta(seconds=1))
    else:
        authorization_id = confirmed_baec(bridge, "BAEC-fixture-9")
    refused_row(connection, "ai_proposal_confirmations", link_values(granted, authorization_id))
    assert bridge.count("ai_proposal_confirmations") == 0


def test_a_superseded_grant_cannot_be_linked_even_before_it_expires(bridge, lineage, granted):
    proposal, revision, _ = lineage
    authorization_id = confirmed_baec(bridge, granted["baec_id"])
    bridge.store.add_revision(revision_record(proposal, 2, revision.review_revision_id))
    refused_row(bridge.connection, "ai_proposal_confirmations", link_values(granted, authorization_id),
                match="unretired grant")


@pytest.mark.parametrize("consumed_at", [GRANT_ISSUED - timedelta(seconds=1), GRANT_ISSUED + timedelta(minutes=15)])
def test_a_consumption_must_fall_inside_the_validity_window_and_pair_with_its_link(bridge, granted, consumed_at):
    connection = bridge.connection
    refused_row(connection, "human_authorization_grant_consumptions", consumption_values(granted))  # no link yet
    authorization_id = confirmed_baec(bridge, granted["baec_id"])
    insert_row(connection, "ai_proposal_confirmations", link_values(granted, authorization_id))
    refused_row(connection, "human_authorization_grant_consumptions", consumption_values(granted, consumed_at),
                match="validity window")
    refused_row(connection, "human_authorization_grant_consumptions",
                consumption_values(granted, executor_surface="streamlit"))
    insert_row(connection, "human_authorization_grant_consumptions",
               consumption_values(granted, GRANT_ISSUED + timedelta(minutes=15) - timedelta(microseconds=1)))


@pytest.mark.parametrize("fraction_us", [0, 123456, 999700], ids=["whole second", "sub-millisecond", ".9997 s"])
def test_the_validity_window_is_exact_to_the_microsecond_on_every_sqlite_version(bridge, lineage, fraction_us):
    # Regression: julianday() rounds to whole milliseconds, and SQLite 3.46 rounds a .9995 s+ fraction into the next
    # second, so a consumption one microsecond before expiry was refused there (and below a millisecond everywhere).
    _, revision, accept = lineage
    issued = GRANT_ISSUED + timedelta(microseconds=fraction_us)
    grant = grant_values(revision, accept.decision_id, issued_at=issued)
    connection = bridge.connection
    insert_row(connection, "human_authorization_grants", grant)
    authorization_id = confirmed_baec(bridge, grant["baec_id"], authorized_at=issued)
    insert_row(connection, "ai_proposal_confirmations", link_values(grant, authorization_id))
    expires = issued + timedelta(minutes=15)
    for outside in (issued - timedelta(microseconds=1), expires):
        refused_row(connection, "human_authorization_grant_consumptions", consumption_values(grant, outside),
                    match="validity window")
    insert_row(connection, "human_authorization_grant_consumptions",
               consumption_values(grant, expires - timedelta(microseconds=1)))


@pytest.mark.parametrize("consumed_at", [
    "now", "soon", "2026-09-01 10:05:00+00:00", "2026-09-01T10:05:00.1234567+00:00", "2026-09-01T24:00:00+00:00",
    "2026-02-30T10:05:00+00:00", "2026-09-01T10:05:00", "2026-09-01T10:05:00+15:00",
])
def test_a_consumption_time_that_is_not_an_exact_instant_is_refused(bridge, granted, consumed_at):
    connection = bridge.connection
    insert_row(connection, "ai_proposal_confirmations", link_values(granted, confirmed_baec(bridge, granted["baec_id"])))
    refused_row(connection, "human_authorization_grant_consumptions",
                {**consumption_values(granted), "consumed_at": consumed_at}, match="validity window")


# --- every bridge row is immutable history ---------------------------------------------------


@pytest.fixture
def every_table_populated(bridge):
    proposal, revision, accept = accepted_lineage(bridge)
    old_grant = grant_values(revision, accept.decision_id, n=7)
    insert_row(bridge.connection, "human_authorization_grants", old_grant)
    second = revision_record(proposal, 2, revision.review_revision_id)
    bridge.store.add_revision(second)  # supersedes old_grant
    accept2 = decision_record(proposal, second.review_revision_id, decision_id="aidecision_fixture2")
    bridge.store.add_decision(accept2)
    grant = grant_values(second, accept2.decision_id)
    insert_row(bridge.connection, "human_authorization_grants", grant)
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    insert_row(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id))
    insert_row(bridge.connection, "human_authorization_grant_consumptions", consumption_values(grant))
    assert all(bridge.count(t) >= 1 for t in BRIDGE_TABLES)
    bridge.store.verify_bridge_integrity()
    return bridge


MUTABLE_LOOKING = {
    "ai_proposals": ("content", "proposal_digest", "artifact_id", "mapping_version", "origin"),
    "ai_proposal_review_revisions": ("content", "review_content_digest", "revision_number", "actor_label"),
    "ai_proposal_review_decisions": ("decision", "review_revision_id"),
    "human_authorization_grants": ("expires_at", "review_content_digest", "issued_at", "account_id"),
    "human_authorization_grant_supersessions": ("reason", "superseding_revision_id"),
    "ai_proposal_confirmations": ("baec_id", "artifact_id", "grant_id"),
    "human_authorization_grant_consumptions": ("consumed_at", "baec_id"),
}


@pytest.mark.parametrize("table", BRIDGE_TABLES)
def test_no_bridge_row_can_be_updated_deleted_or_replaced(every_table_populated, table):
    bridge = every_table_populated
    connection = bridge.connection
    before = full_dump(connection)
    for column in MUTABLE_LOOKING[table]:
        refused(connection, f"UPDATE {table} SET {column} = {column}", match=f"{table} is append-only")
    refused(connection, f"DELETE FROM {table}", match=f"{table} is append-only")
    for name in BRIDGE_INTEGRITY_TRIGGERS:  # isolate the replace guard: it alone must refuse
        connection.execute(f"DROP TRIGGER {name}")
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    row = connection.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid LIMIT 1").fetchone()
    placeholders = ", ".join("?" for _ in columns)
    for verb in ("REPLACE", "INSERT OR REPLACE", "INSERT OR IGNORE"):
        refused(connection, f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", row,
                match=f"{table} rows cannot be replaced")
    first_key = BRIDGE_REPLACE_GUARDED_KEYS[table][0][0]
    refused(connection, f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                        f"ON CONFLICT ({first_key}) DO UPDATE SET {columns[-1]} = excluded.{columns[-1]}", row,
            match=f"{table} rows cannot be replaced")
    assert full_dump(connection) == before
