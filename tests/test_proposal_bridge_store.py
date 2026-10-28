"""Phase 7C: the ProposalBridgeStore persistence substrate (proposals, review revisions, review decisions).

Persistence only. A stored revision is a persistence representation, not a validated review,
and nothing here issues, consumes, or executes a grant. All data is test-only fixture data.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from baec_app.data import proposal_bridge
from baec_app.data.database import (
    DATA_TABLES,
    PersistenceError,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    open_database,
)
from baec_app.data.proposal_bridge import (
    AiProposalRecord,
    ProposalBridgeStore,
    ReviewDecision,
    ReviewDecisionRecord,
    ReviewRevisionRecord,
    sha256_text,
)
from baec_app.data.records import RecordValidationError
from tests.bridge_builders import (  # noqa: F401
    ACC,
    OTHER_ACC,
    accepted_lineage,
    bridge,
    canonical,
    confirmed_baec,
    decision_record,
    grant_values,
    insert_row,
    link_values,
    proposal_content,
    proposal_record,
    revision_content,
    revision_record,
)
from tests.persistence_builders import tamper

MODULE = Path(proposal_bridge.__file__)


def dump(connection):
    return {t: connection.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in DATA_TABLES}


# --- round trips --------------------------------------------------------------------------------


def test_a_proposal_revision_chain_and_decisions_round_trip_exactly(bridge):
    proposal, first, accept = accepted_lineage(bridge)
    store = bridge.store
    assert store.get_proposal(proposal.proposal_id) == proposal
    second = revision_record(proposal, 2, first.review_revision_id)
    store.add_revision(second)
    assert store.list_revisions(proposal.proposal_id) == (first, second)
    assert store.get_revision(second.review_revision_id) == second
    reject = decision_record(proposal, None, ReviewDecision.REJECTED, "aidecision_reject")
    store.add_decision(reject)
    assert store.list_decisions(proposal.proposal_id) == (accept, reject)
    assert store.get_proposal(proposal.proposal_id) == proposal  # nothing about the proposal changed
    assert store.get_revision(first.review_revision_id) == first
    store.verify_bridge_integrity()


def test_the_proposal_snapshot_is_stored_byte_for_byte_with_its_digest(bridge):
    proposal = proposal_record(bridge.artifact)
    bridge.store.add_proposal(proposal)
    row = bridge.execute("SELECT origin, content, proposal_digest, artifact_id, artifact_digest FROM ai_proposals"
                         ).fetchone()
    assert row == ("AI_DRAFT", proposal.content, sha256_text(proposal.content), bridge.artifact.artifact_id,
                   bridge.artifact.artifact_digest)


def test_the_proposal_row_copies_no_provenance_the_artifact_already_anchors(bridge):
    columns = {row[1] for row in bridge.execute("PRAGMA table_info(ai_proposals)")}
    assert columns == {"proposal_id", "origin", "mapping_version", "content_version", "artifact_id",
                       "artifact_digest", "account_id", "interaction_id", "content", "proposal_digest",
                       "created_by", "created_at"}
    assert not columns & {"requested_model", "response_model", "prompt_version", "prompt_digest",
                          "validation_version", "ai_run_id", "attributed_speaker", "provenance"}


# --- record validation ------------------------------------------------------------------------


@pytest.mark.parametrize("change", [
    "not canonical", "digest", "float", "nan", "array", "origin", "mapping", "account", "artifact digest",
    "artifact missing",
])
def test_a_proposal_record_refuses_inconsistent_content(bridge, change):
    content = proposal_content(bridge.artifact)
    text = canonical(content)
    kwargs = {}
    if change == "not canonical":
        text = text.replace(",", ", ", 1)
    elif change == "digest":
        kwargs["proposal_digest"] = "0" * 64
    elif change == "float":
        text = canonical(dict(content, ratio=1.5))
    elif change == "nan":
        text = text[:-1] + ',"x":NaN}'
    elif change == "array":
        text = "[]"
    elif change == "origin":
        text = canonical(dict(content, origin="AI_MODEL"))
    elif change == "mapping":
        text = canonical(dict(content, mapping_version="baec-ai-proposal-mapping/v2"))
    elif change == "account":
        text = canonical(dict(content, account_id=OTHER_ACC))
    elif change == "artifact digest":
        text = canonical(dict(content, artifact={"artifact_id": bridge.artifact.artifact_id,
                                                 "artifact_digest": "1" * 64}))
    else:
        text = canonical({k: v for k, v in content.items() if k != "artifact"})
    fields = dict(dataclasses.asdict(proposal_record(bridge.artifact)), content=text,
                  proposal_digest=sha256_text(text))
    fields.update(kwargs)
    with pytest.raises(RecordValidationError):
        AiProposalRecord(**fields)


@pytest.mark.parametrize("field,value", [
    ("origin", "HUMAN_DRAFT"), ("mapping_version", "x"), ("content_version", "x"), ("proposal_id", "P-1"),
    ("artifact_digest", "F" * 64), ("created_by", " "), ("created_at", None),
])
def test_a_proposal_record_refuses_bad_fields(bridge, field, value):
    with pytest.raises(RecordValidationError):
        dataclasses.replace(proposal_record(bridge.artifact), **{field: value})


@pytest.mark.parametrize("change", ["buyer_role", "buyer_role missing", "number", "previous", "digest",
                                    "normalization version"])
def test_a_revision_record_refuses_inconsistent_content(bridge, change):
    proposal = proposal_record(bridge.artifact)
    content = revision_content(proposal, 1, None)
    if change == "buyer_role":
        content["buyer_role"] = "Materials manager"
    elif change == "buyer_role missing":
        del content["buyer_role"]
    elif change == "number":
        content["revision_number"] = 2
    elif change == "previous":
        content["previous_revision_id"] = "aireview_x"
    elif change == "normalization version":
        content["normalization_validation_version"] = "baec-extraction-validation/v2"
    text = canonical(content)
    digest = "0" * 64 if change == "digest" else sha256_text(text)
    with pytest.raises(RecordValidationError):
        ReviewRevisionRecord(**dict(dataclasses.asdict(revision_record(proposal)), content=text,
                                    review_content_digest=digest))


def test_revision_and_decision_records_refuse_impossible_shapes(bridge):
    proposal = proposal_record(bridge.artifact)
    with pytest.raises(RecordValidationError):
        revision_record(proposal, 1, "aireview_fixture0")
    with pytest.raises(RecordValidationError):
        revision_record(proposal, 0)
    with pytest.raises(RecordValidationError):
        decision_record(proposal, None, ReviewDecision.ACCEPTED)  # an acceptance names its revision
    with pytest.raises(RecordValidationError):
        decision_record(proposal, "aireview_fixture1", decision="ACCEPTED")


# --- writes fail closed --------------------------------------------------------------------------


def test_refused_writes_raise_conflicts_and_store_nothing(bridge):
    store = bridge.store
    before = dump(bridge.connection)
    with pytest.raises(RepositoryConflictError):
        store.add_proposal(proposal_record(bridge.artifact_v1))  # validator v1 is not on the allowlist
    with pytest.raises(RepositoryConflictError):
        store.add_proposal(proposal_record(bridge.artifact, account_id=OTHER_ACC))
    assert dump(bridge.connection) == before
    proposal = proposal_record(bridge.artifact)
    store.add_proposal(proposal)
    with pytest.raises(RepositoryConflictError):
        store.add_proposal(proposal)
    with pytest.raises(RepositoryConflictError):
        store.add_revision(revision_record(proposal, 2, "aireview_fixture1"))
    with pytest.raises(RepositoryConflictError):
        store.add_decision(decision_record(proposal, "aireview_fixture1"))
    for method in (store.add_proposal, store.add_revision, store.add_decision):
        with pytest.raises(RecordValidationError):
            method("not a record")


def test_the_store_requires_a_current_schema_connection_with_foreign_keys():
    connection = open_database()
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(PersistenceError):
            ProposalBridgeStore(connection)
    finally:
        connection.close()
    with pytest.raises(PersistenceError):
        ProposalBridgeStore("not a connection")


# --- loads fail closed -----------------------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "UPDATE ai_proposals SET content = replace(content, 'AI_DRAFT', 'AI_MODEL')",
    "UPDATE ai_proposals SET proposal_digest = '0000000000000000000000000000000000000000000000000000000000000000'",
    "UPDATE ai_proposals SET created_at = 'yesterday'",
])
def test_a_corrupted_proposal_is_never_returned(bridge, sql):
    proposal, revision, _ = accepted_lineage(bridge)
    tamper(bridge.connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        bridge.store.get_proposal(proposal.proposal_id)
    with pytest.raises(PersistenceIntegrityError):
        bridge.store.get_revision(revision.review_revision_id)
    with pytest.raises(PersistenceIntegrityError):
        bridge.store.verify_bridge_integrity()


@pytest.mark.parametrize("sql", [
    "UPDATE ai_proposal_review_revisions SET content = replace(content, 'BUYER_FACT', 'SELLER_OBSERVATION')",
    "UPDATE ai_proposal_review_revisions SET revision_number = 5",
    "UPDATE ai_proposal_review_decisions SET decision = 'MAYBE'",
])
def test_corrupted_review_history_is_never_returned(bridge, sql):
    proposal, _, _ = accepted_lineage(bridge)
    tamper(bridge.connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        bridge.store.list_revisions(proposal.proposal_id) and bridge.store.list_decisions(proposal.proposal_id)


def test_missing_rows_are_not_found(bridge):
    for call in (lambda: bridge.store.get_proposal("aiprop_none"), lambda: bridge.store.get_revision("aireview_none"),
                 lambda: bridge.store.list_revisions("aiprop_none"), lambda: bridge.store.list_decisions("aiprop_none")):
        with pytest.raises(RepositoryNotFoundError):
            call()


def test_integrity_verification_detects_contradictory_grant_history(bridge):
    _, revision, accept = accepted_lineage(bridge)
    grant = grant_values(revision, accept.decision_id)
    insert_row(bridge.connection, "human_authorization_grants", grant)
    bridge.store.verify_bridge_integrity()
    authorization_id = confirmed_baec(bridge, grant["baec_id"])
    insert_row(bridge.connection, "ai_proposal_confirmations", link_values(grant, authorization_id))
    with pytest.raises(PersistenceIntegrityError, match="without its consumption"):
        bridge.store.verify_bridge_integrity()  # a link with no consumption never survives a real commit
    tamper(bridge.connection, "DELETE FROM ai_proposal_confirmations")
    second = grant_values(revision, accept.decision_id, n=2, sequence=2)
    tamper(bridge.connection, f"INSERT INTO human_authorization_grants ({', '.join(second)}) "
                              f"VALUES ({', '.join('?' for _ in second)})", tuple(second.values()))
    with pytest.raises(PersistenceIntegrityError, match="more than one unretired grant"):
        bridge.store.verify_bridge_integrity()


# --- the store's surface: persistence of proposals and review history, nothing else ---------------


def test_the_store_exposes_no_grant_confirmation_consumption_or_domain_operation():
    public = {name for name, _ in inspect.getmembers(ProposalBridgeStore, inspect.isfunction)
              if not name.startswith("_")}
    assert public == {"add_proposal", "add_revision", "add_decision", "get_proposal", "get_revision",
                      "list_revisions", "list_decisions", "verify_bridge_integrity",
                      "confirmed_baec_for_artifact",  # Phase 7D: one read for eligibility rule E8
                      "list_proposals", "add_accepted_revision"}  # Phase 7E: listing, and revision + ACCEPTED atomically
    module_public = {name for name in vars(proposal_bridge) if not name.startswith("_")
                     and getattr(vars(proposal_bridge)[name], "__module__", None) == proposal_bridge.__name__}
    assert module_public == {"AiProposalRecord", "ProposalBridgeStore", "ReviewDecision", "ReviewDecisionRecord",
                             "ReviewRevisionRecord", "sha256_text"}


def test_the_store_writes_only_the_three_review_tables():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    inserted = {node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_insert"
                and isinstance(node.args[0], ast.Constant)}
    assert inserted == {"ai_proposals", "ai_proposal_review_revisions", "ai_proposal_review_decisions"}
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    sql_writes = [text for text in literals if any(verb in text.upper() for verb in ("INSERT INTO", "UPDATE ", "DELETE "))]
    assert sql_writes == ["INSERT INTO ", "INSERT INTO "]  # plain INSERTs only (_insert, add_accepted_revision)
    accepted = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == "add_accepted_revision")
    assert {n.value for n in ast.walk(accepted) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith("ai_")} == {"ai_proposal_review_revisions", "ai_proposal_review_decisions"}
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("UPDATE accounts", "account_state_transitions", "dormancy_judgments", "staleness_status",
                      "INSERT INTO baec_records", "INSERT INTO human_authorizations", "state_machine",
                      "baec_app.application", "baec_app.ai", "baec_app.mcp"):
        assert forbidden not in source, forbidden


def test_review_records_are_persistence_representations_not_validated_reviews():
    for record in (AiProposalRecord, ReviewRevisionRecord, ReviewDecisionRecord):
        assert dataclasses.is_dataclass(record) and record.__dataclass_params__.frozen
    doc = ReviewRevisionRecord.__doc__
    assert "persistence representation, not a validated review" in doc
    assert {d.value for d in ReviewDecision} == {"ACCEPTED", "REJECTED"}
