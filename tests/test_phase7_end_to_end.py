"""Phase 7H: the full Phase 7 chain, end to end, on an ephemeral synthetic schema-v7 database.

    persisted synthetic interaction
    -> verified Phase 6 AI artifact (the real ExtractionService, offline FakeProvider: no model, no network)
    -> deterministic AI_DRAFT mapping (AiProposalMappingService)
    -> explicit human review (the real Streamlit page, driven by AppTest)
    -> immutable ACCEPTED review revision
    -> separate explicit human authorization (the page's "Authorize BAEC confirmation" action)
    -> persisted 15-minute single-use grant
    -> one-tool MCP execution (the Phase 7G server, in-process MCP client)
    -> atomic confirmed BAEC

No production seam exists for these tests. The page reads its database path from session state, as in every 7E
AppTest; the MCP runtime takes an injected clock, as in every 7G test. AppTest proves traversal of the prototype's
human-interaction boundary, not authenticated real-world identity: the reviewer label is self-asserted.

These are software tests on synthetic, test-only fixtures. They do not validate the BAEC construct, establish
prospective validity, or show that confirmed BAECs predict purchases or opportunity creation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta

import anyio
import pytest
from mcp import Client

from baec_app.application.proposal_authorization import ConfirmationExecutionService, ProposalAuthorizationService
from baec_app.data.authorization_grants import AuthorizationGrantStore
from baec_app.data.database import DATA_TABLES, connect
from baec_app.data.proposal_bridge import sha256_text
from baec_app.mcp_write.composition import open_write_runtime
from tests.application_builders import FixedClock
from tests.mapping_builders import INT, Mapping
from tests.persistence_builders import dump
from tests.review_builders import REVIEWER, SUGGESTED, decisions
from tests.test_review_page import Page, snapshot

CONDITION = "Pricing rising by more than 10% at renewal."
ERROR_PREFIX = "Error executing tool confirm_baec: "

# Every table the whole chain may write, from the AI artifact onward. Nothing else may change.
CHAIN_WRITES = {
    "ai_proposals",                                                    # the AI_DRAFT
    "ai_proposal_review_revisions", "ai_proposal_review_decisions",    # explicit human review
    "human_authorization_grants",                                      # separate explicit human authorization
    "baec_records", "human_authorizations", "interaction_evidence",    # the confirmed BAEC and its audit
    "criterion_assessments", "criterion_evidence",
    "ai_proposal_confirmations", "human_authorization_grant_consumptions",
}
STATE_TABLES = ("accounts", "account_state_transitions", "dormancy_judgments", "evaluation_evidence",
                "non_evaluation_evidence")


def call(path, clock, *argument_sets):
    async def main():
        with open_write_runtime(path, clock=clock) as runtime:
            async with Client(runtime.server) as client:
                return [await client.call_tool("confirm_baec", arguments) for arguments in argument_sets]

    return anyio.run(main)


def refusal(result):
    assert result.is_error and result.structured_content is None
    return json.loads(result.content[0].text.removeprefix(ERROR_PREFIX))


def counts(path):
    connection = connect(path)
    try:
        return {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in DATA_TABLES}
    finally:
        connection.close()


@pytest.fixture
def chain(tmp_path):
    """A persisted synthetic interaction and one verified Phase 6 artifact, produced offline. Nothing mapped yet."""
    m = Mapping(str(tmp_path / "phase7-e2e.sqlite3"))
    result = m.extract()
    assert result.status.value == "success" and result.artifact_id is not None
    m.artifact_id = result.artifact_id
    yield m
    m.connection.close()


# --- the flagship ----------------------------------------------------------------------------------------------


def test_the_full_phase7_chain_confirms_exactly_one_baec_through_human_review_human_authorization_and_mcp(chain):
    path = chain.path
    before_mapping = snapshot(path)

    # Deterministic AI_DRAFT mapping.
    proposal = chain.map(chain.artifact_id).proposal
    assert proposal.origin == "AI_DRAFT"
    chain.connection.close()
    after_mapping = snapshot(path)
    assert counts(path)["human_authorization_grants"] == 0 and counts(path)["baec_records"] == 0

    # Explicit human review on the real page: every authoritative value set by the human.
    page = Page(path, proposal.proposal_id)
    page.fill(condition=CONDITION).accept()
    assert page.errors() == []
    page.run()
    assert counts(path)["human_authorization_grants"] == 0  # Review Accepted is not Authorization Granted

    # The separate, explicit human authorization action on the same page.
    [button] = [b for b in page.at.button if b.key == f"authorize_{proposal.proposal_id}"]
    button.click()
    page.run()
    granted = page.at.success[0].value
    assert granted.startswith("Authorization Granted — grant_") and "Not executed: no BAEC has been confirmed." in granted
    grant_id = re.search(r"grant_[0-9a-f]{64}", granted).group(0)
    assert counts(path)["baec_records"] == 0  # Authorization Granted is not BAEC Confirmed
    after_authorization = snapshot(path)

    reader = connect(path)
    grant = AuthorizationGrantStore(reader).get_grant(grant_id)
    reader.close()
    assert grant.actor_label == REVIEWER and grant.expires_at - grant.issued_at == timedelta(minutes=15)

    # One-tool MCP execution of exactly that grant, then a replay.
    clock = FixedClock(grant.issued_at + timedelta(seconds=30))
    first, replay = call(path, clock, {"grant_id": grant_id}, {"grant_id": grant_id})
    assert first.structured_content == {"status": "confirmed", "baec_id": grant.baec_id,
                                        "proposal_id": proposal.proposal_id,
                                        "review_revision_id": grant.review_revision_id, "grant_id": grant_id}
    assert refusal(replay) == {"status": "refused", "code": "grant_already_consumed", "grant_id": grant_id}
    final = snapshot(path)

    # Exactly one of everything along the chain.
    final_counts = counts(path)
    assert {t: final_counts[t] for t in (
        "ai_runs", "ai_artifacts", "ai_proposals", "ai_proposal_review_revisions", "ai_proposal_review_decisions",
        "human_authorization_grants", "baec_records", "human_authorizations", "ai_proposal_confirmations",
        "human_authorization_grant_consumptions")} == {
        "ai_runs": 1, "ai_artifacts": 1, "ai_proposals": 1, "ai_proposal_review_revisions": 1,
        "ai_proposal_review_decisions": 1, "human_authorization_grants": 1, "baec_records": 1,
        "human_authorizations": 1, "ai_proposal_confirmations": 1, "human_authorization_grant_consumptions": 1}

    # Only the chain's own tables changed; every earlier layer is byte-identical.
    assert {t for t in DATA_TABLES if final[t] != before_mapping[t]} == CHAIN_WRITES
    for table in ("interactions", "ai_runs", "ai_run_results", "ai_run_outputs", "ai_artifacts",
                  "ai_artifact_excerpts", "accounts"):
        assert final[table] == before_mapping[table], table  # source, AI artifact, and account state unchanged
    assert final["ai_proposals"] == after_mapping["ai_proposals"]  # the AI proposal unchanged
    for table in ("ai_proposal_review_revisions", "ai_proposal_review_decisions", "human_authorization_grants"):
        assert final[table] == after_authorization[table], table  # the accepted revision and the grant unchanged
    for table in STATE_TABLES[1:]:
        assert final[table] == [], table  # zero state-transition, dormancy, or state-evidence rows
    assert not [t for t in final if re.search("outreach|message|contact|email", t)]  # no outreach storage at all

    # The audit chain, reconstructed all the way back from the confirmed BAEC.
    connection = connect(path)
    try:
        row = connection.execute("""
            SELECT b.baec_id, b.classification, b.staleness_status, b.normalized_text, b.normalized_generated_at,
                   b.normalized_model, b.buyer_exact_statement, b.source_interaction_id, b.buyer_role,
                   h.authorized_by, h.authorized_at, h.action, h.subject_id, h.target_state,
                   c.grant_id, g.review_revision_id, g.review_content_digest, g.proposal_digest, g.issued_at,
                   r.content, r.proposal_id, p.proposal_digest, p.origin, p.artifact_id, p.artifact_digest,
                   a.artifact_digest, a.ai_run_id, a.interaction_id, u.interaction_id, i.interaction_id,
                   used.baec_id
            FROM baec_records AS b
            JOIN human_authorizations AS h ON h.authorization_id = b.confirmation_authorization_id
            JOIN ai_proposal_confirmations AS c ON c.baec_id = b.baec_id AND c.authorization_id = h.authorization_id
            JOIN human_authorization_grants AS g ON g.grant_id = c.grant_id AND g.baec_id = b.baec_id
            JOIN human_authorization_grant_consumptions AS used ON used.grant_id = g.grant_id
            JOIN ai_proposal_review_revisions AS r ON r.review_revision_id = g.review_revision_id
            JOIN ai_proposals AS p ON p.proposal_id = r.proposal_id AND p.proposal_id = c.proposal_id
            JOIN ai_artifacts AS a ON a.artifact_id = p.artifact_id
            JOIN ai_runs AS u ON u.ai_run_id = a.ai_run_id
            JOIN interactions AS i ON i.interaction_id = p.interaction_id
        """).fetchall()
    finally:
        connection.close()
    assert len(row) == 1
    (baec_id, classification, staleness, n_text, n_at, n_model, statement, source, role,
     actor, authorized_at, action, subject, target, link_grant, revision_id, content_digest, proposal_digest,
     issued_at, content, revision_proposal, stored_proposal_digest, origin, artifact_id, proposal_artifact_digest,
     artifact_digest, run_id, artifact_interaction, run_interaction, interaction, consumed_baec) = row[0]
    assert (baec_id, classification, staleness, role) == (grant.baec_id, "CONFIRMED_BAEC", "CURRENT", None)
    assert (n_text, n_at, n_model) == (None, None, None)  # normalized_* stays NULL: no normalization misuse
    assert statement == SUGGESTED and source == interaction == INT
    assert (actor, action, subject, target) == (REVIEWER, "CONFIRM_BAEC", baec_id, None)
    assert authorized_at == issued_at  # authorized when the human issued the grant
    assert link_grant == grant_id and revision_id == grant.review_revision_id and consumed_baec == baec_id
    assert content_digest == sha256_text(content)  # the grant binds exactly the reviewed content
    assert revision_proposal == proposal.proposal_id and proposal_digest == stored_proposal_digest
    assert origin == "AI_DRAFT" and artifact_id == chain.artifact_id
    assert proposal_artifact_digest == artifact_digest and artifact_interaction == run_interaction == INT
    # The final human normalization is recoverable through the review lineage only.
    normalization = json.loads(content)["normalization"]["condition"]
    assert normalization["final_value"] == CONDITION and normalization["disposition"] == "EDITED"


# --- the design's 7H scenario: grant -> edit -> supersession -> new grant -> MCP -> repeat ------------------------


def test_an_edit_supersedes_the_first_grant_and_only_a_new_human_authorization_confirms(chain):
    from baec_app.application.proposal_review import ProposalReviewService
    from tests.review_builders import REVIEWED_AT

    proposal = chain.map(chain.artifact_id).proposal
    pid = proposal.proposal_id
    ids = iter(range(1, 100))
    review = ProposalReviewService(chain.connection, clock=FixedClock(REVIEWED_AT),
                                   new_id=lambda prefix: f"{prefix}e2e{next(ids):04d}")
    clock = FixedClock(REVIEWED_AT + timedelta(minutes=5))
    grant_ids = iter(range(1, 100))
    auth = ProposalAuthorizationService(chain.connection, clock=clock,
                                        new_grant_id=lambda: "grant_" + f"{next(grant_ids):064x}")
    statements = []
    chain.connection.set_trace_callback(statements.append)

    review.accept_review(pid, decisions(final_normalized_condition=CONDITION), actor_label=REVIEWER)
    first = auth.authorize_confirmation(pid, actor_label=REVIEWER).grant_id
    review.accept_review(pid, decisions(final_normalized_condition="More than 10% at renewal."),
                         actor_label=REVIEWER)  # an edit: revision 2
    second = auth.authorize_confirmation(pid, actor_label=REVIEWER).grant_id  # a new, explicit human action
    clock.advance(minutes=1)
    stale, fresh, again = call(chain.path, clock, {"grant_id": first}, {"grant_id": second}, {"grant_id": second})
    assert refusal(stale)["code"] == "grant_superseded"  # still chronologically unexpired
    assert fresh.structured_content["status"] == "confirmed" and fresh.structured_content["grant_id"] == second
    assert refusal(again)["code"] == "grant_already_consumed"
    chain.connection.set_trace_callback(None)

    revision_2 = chain.connection.execute(
        "SELECT review_revision_id FROM ai_proposal_review_revisions WHERE revision_number = 2").fetchone()[0]
    assert fresh.structured_content["review_revision_id"] == revision_2
    assert chain.connection.execute("SELECT reason FROM human_authorization_grant_supersessions "
                                    "WHERE grant_id = ?", (first,)).fetchone() == ("REVISION_SUPERSEDED",)
    # The human path wrote no state, dormancy, or staleness: checked on every SQL statement it executed.
    for statement in statements:
        upper = statement.upper()
        for table in STATE_TABLES:
            assert not re.search(rf"\b(INSERT INTO|UPDATE|DELETE FROM)\s+{table.upper()}\b", upper), statement
        assert "STALENESS_STATUS =" not in upper and "UPDATE BAEC_RECORDS" not in upper, statement


# --- state-machine isolation across the whole chain ---------------------------------------------------------------


def test_the_mcp_execution_itself_issues_no_state_dormancy_or_staleness_sql(chain):
    """Every statement the MCP-composed executor runs during a successful confirmation, captured by SQLite."""
    proposal = chain.map(chain.artifact_id).proposal
    from baec_app.application.proposal_review import ProposalReviewService
    from tests.review_builders import REVIEWED_AT

    ids = iter(range(1, 100))
    ProposalReviewService(chain.connection, clock=FixedClock(REVIEWED_AT),
                          new_id=lambda prefix: f"{prefix}iso{next(ids):04d}").accept_review(
        proposal.proposal_id, decisions(final_normalized_condition=CONDITION), actor_label=REVIEWER)
    clock = FixedClock(REVIEWED_AT + timedelta(minutes=5))
    grant_id = ProposalAuthorizationService(chain.connection, clock=clock).authorize_confirmation(
        proposal.proposal_id, actor_label=REVIEWER).grant_id
    clock.advance(minutes=1)
    accounts = dump(chain.connection)["accounts"]
    statements = []

    async def main():
        with open_write_runtime(chain.path, clock=clock) as runtime:
            runtime._connection.set_trace_callback(statements.append)
            async with Client(runtime.server) as client:
                return await client.call_tool("confirm_baec", {"grant_id": grant_id})

    result = anyio.run(main)
    assert result.structured_content["status"] == "confirmed"
    inserts = sorted({m.group(1) for s in statements for m in [re.search(r"INSERT INTO (\w+)", s)] if m})
    assert inserts == ["ai_proposal_confirmations", "baec_records", "criterion_assessments", "criterion_evidence",
                       "human_authorization_grant_consumptions", "human_authorizations", "interaction_evidence",
                       "stringency_expressions"]  # the reviewed stringency ("more than 10%") belongs to the record
    assert not [s for s in statements if re.search(r"\b(UPDATE|DELETE FROM|REPLACE INTO)\b", s)]
    assert dump(chain.connection)["accounts"] == accounts
