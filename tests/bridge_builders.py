"""Shared setup for the Phase 7C bridge persistence tests. Setup only; expectations live in the tests.

TEST-ONLY FIXTURES (Phase 7 design D15). Every account, interaction, run, artifact,
and model identifier here carries the reserved FIXTURE prefix. The AI runs and
artifacts are deterministic synthetic rows written straight into an ephemeral
per-test database: they are not, and must never be presented as, evidence of
any actual Anthropic model invocation. The Phase 6 schema requires provider =
'anthropic' on every run; that column value here labels the schema slot, not a call.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from baec_app.data.ai_provenance import AiProvenanceStore, AiRunOutputRecord, AiRunStatus, AiTerminalOutcome
from baec_app.data.database import (
    BRIDGE_MAPPING_VERSION,
    BRIDGE_NORMALIZATION_VALIDATION_VERSION,
    BRIDGE_PROPOSAL_CONTENT_VERSION,
    BRIDGE_REVIEW_CONTENT_VERSION,
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
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository, encode_datetime
from baec_app.domain.baec_rules import create_confirmed_baec_record
from baec_app.domain.enums import AuthorizationAction
from baec_app.domain.models import Account, HumanAuthorization
from tests.ai_provenance_builders import RAW_OUTPUT, artifact_record, excerpt_records, result_record, run_record
from tests.builders import HARBOR_QUOTE, candidate, excerpt

FIXTURE_PREFIX = "FIXTURE-"
ACC, OTHER_ACC = "FIXTURE-ACC-1", "FIXTURE-ACC-2"
INT, INT_SAME_ACCOUNT, INT_OTHER = "FIXTURE-INT-1", "FIXTURE-INT-3", "FIXTURE-INT-2"
MODEL = "FIXTURE-synthetic-model-not-a-real-invocation"
ACTOR = "FIXTURE-reviewer"
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
# Identical source text in three interactions: identical words never merge lineages.
SOURCE_TEXT = "Seller: What would make evaluating alternatives worthwhile?\nBuyer: " + HARBOR_QUOTE
CRITERIA = ("PRESENT_NON_EVALUATION", "PROSPECTIVE_CONDITION", "BUYER_ARTICULATION", "EVALUATION_LINKAGE")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def add_artifact(store: AiProvenanceStore, artifact_id: str, run_id: str, account_id: str, interaction_id: str,
                 validation_version: str = "baec-extraction-validation/v2"):
    store.record_run(run_record(run_id, account_id=account_id, interaction_id=interaction_id,
                                requested_model=MODEL, validation_version=validation_version))
    artifact = artifact_record(run_id, artifact_id, account_id=account_id, interaction_id=interaction_id)
    store.record_terminal_outcome(AiTerminalOutcome(
        result=result_record(AiRunStatus.SUCCESS, run_id, response_model=MODEL),
        output=AiRunOutputRecord.of(run_id, RAW_OUTPUT),
        artifact=artifact,
        excerpts=excerpt_records(artifact_id, (HARBOR_QUOTE,), interaction_id),
    ))
    return artifact


class Bridge:
    """An ephemeral schema-v7 database holding the fixture accounts, interactions, and artifacts."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.connection = open_database(path)
        self.repository = Repository(self.connection)
        self.ai = AiProvenanceStore(self.connection)
        self.store = ProposalBridgeStore(self.connection)
        self.repository.add_account(Account(ACC, "Fixture Harbor"))
        self.repository.add_account(Account(OTHER_ACC, "Fixture Other"))
        for interaction_id, account_id in ((INT, ACC), (INT_OTHER, OTHER_ACC), (INT_SAME_ACCOUNT, ACC)):
            self.repository.add_interaction(SourceInteraction(interaction_id, account_id, T0 - timedelta(days=1),
                                                              SOURCE_TEXT))
        self.artifact = add_artifact(self.ai, "FIXTURE-ART-1", "FIXTURE-RUN-1", ACC, INT)
        self.artifact_same_account = add_artifact(self.ai, "FIXTURE-ART-3", "FIXTURE-RUN-3", ACC, INT_SAME_ACCOUNT)
        self.artifact_other = add_artifact(self.ai, "FIXTURE-ART-2", "FIXTURE-RUN-2", OTHER_ACC, INT_OTHER)
        self.artifact_v1 = add_artifact(self.ai, "FIXTURE-ART-V1", "FIXTURE-RUN-V1", ACC, INT,
                                        validation_version="baec-extraction-validation/v1")

    def execute(self, sql: str, parameters: tuple = ()):
        return self.connection.execute(sql, parameters)

    def count(self, table: str) -> int:
        return self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def bridge(tmp_path):
    b = Bridge(str(tmp_path / "bridge.sqlite3"))
    yield b
    b.connection.close()


# --- proposals ------------------------------------------------------------------------------


def proposal_content(artifact, account_id=ACC, interaction_id=INT, **overrides) -> dict:
    """A proposal snapshot in the approved baec-ai-proposal-content/v1 shape (test data, not a mapper)."""
    content = {
        "content_version": BRIDGE_PROPOSAL_CONTENT_VERSION,
        "mapping_version": BRIDGE_MAPPING_VERSION,
        "origin": "AI_DRAFT",
        "account_id": account_id,
        "interaction_id": interaction_id,
        "artifact": {"artifact_id": artifact.artifact_id, "artifact_digest": artifact.artifact_digest},
        "ai_analysis_status": "possible_baec_language",
        "ai_suggested_excerpts": [{"excerpt_id": "e1", "text": HARBOR_QUOTE,
                                   "ai_attributed_speaker": {"value": "buyer", "provenance": "AI_INFERENCE"}}],
        "ai_normalized_condition": {"value": "Supplier pricing rises by more than 10% at renewal.",
                                    "provenance": "AI_INFERENCE"},
        "ai_normalized_evaluation_link": {"value": None, "provenance": "AI_INFERENCE"},
        "ai_criterion_hypotheses": [
            {"criterion": c, "ai_status": "supported", "excerpt_refs": ["e1"], "explanation": "Fixture text.",
             "provenance": "AI_INFERENCE"} for c in CRITERIA
        ],
        "ai_uncertainties": {"values": [], "provenance": "AI_INFERENCE"},
    }
    content.update(overrides)
    return content


def proposal_record(artifact, proposal_id="aiprop_fixture1", account_id=ACC, interaction_id=INT, content=None,
                    **overrides) -> AiProposalRecord:
    text = canonical(content if content is not None else proposal_content(artifact, account_id, interaction_id))
    values = dict(proposal_id=proposal_id, artifact_id=artifact.artifact_id, artifact_digest=artifact.artifact_digest,
                  account_id=account_id, interaction_id=interaction_id, content=text,
                  proposal_digest=sha256_text(text), created_by=ACTOR, created_at=T0)
    values.update(overrides)
    return AiProposalRecord(**values)


# --- review revisions and decisions -----------------------------------------------------------


def revision_content(proposal: AiProposalRecord, number: int, previous: str | None, **overrides) -> dict:
    """A review revision in the approved baec-ai-review-content/v1 shape (a persistence representation only)."""
    content = {
        "review_content_version": BRIDGE_REVIEW_CONTENT_VERSION,
        "normalization_validation_version": BRIDGE_NORMALIZATION_VALIDATION_VERSION,
        "proposal_id": proposal.proposal_id,
        "proposal_digest": proposal.proposal_digest,
        "account_id": proposal.account_id,
        "interaction_id": proposal.interaction_id,
        "artifact_id": proposal.artifact_id,
        "revision_number": number,
        "previous_revision_id": previous,
        "evidence_selections": [{"selection_id": "s1", "text": HARBOR_QUOTE, "provenance": "BUYER_FACT",
                                 "suggested_excerpt_id": "e1"}],
        "source_selection_id": "s1",
        "buyer_exact_statement": HARBOR_QUOTE,
        "buyer_role": None,
        "findings": [{"criterion": c, "finding": "MET", "evidence_selection_ids": ["s1"]} for c in CRITERIA],
        "articulation_origin": "BUYER_GENERATED",
        "elicitation_mode": "CEE_ELICITED",
        "stringency": None,
        "normalization": {
            "condition": {"disposition": "KEEP_AI", "ai_value": "Supplier pricing rises by more than 10% at renewal.",
                          "final_value": "Supplier pricing rises by more than 10% at renewal."},
            "evaluation_link": {"disposition": "NONE", "ai_value": None, "final_value": None},
        },
        "captured_at": encode_datetime(T0 - timedelta(days=1)),
    }
    content.update(overrides)
    return content


def revision_record(proposal: AiProposalRecord, number=1, previous=None, review_revision_id=None, content=None,
                    created_at=None, **overrides) -> ReviewRevisionRecord:
    text = canonical(content if content is not None else revision_content(proposal, number, previous))
    values = dict(review_revision_id=review_revision_id or f"aireview_fixture{number}",
                  proposal_id=proposal.proposal_id, proposal_digest=proposal.proposal_digest,
                  account_id=proposal.account_id, interaction_id=proposal.interaction_id,
                  artifact_id=proposal.artifact_id, revision_number=number, previous_revision_id=previous,
                  content=text, review_content_digest=sha256_text(text), actor_label=ACTOR,
                  created_at=created_at or T0 + timedelta(minutes=number))
    values.update(overrides)
    return ReviewRevisionRecord(**values)


def decision_record(proposal: AiProposalRecord, revision_id: str | None, decision=ReviewDecision.ACCEPTED,
                    decision_id="aidecision_fixture1", **overrides) -> ReviewDecisionRecord:
    values = dict(decision_id=decision_id, proposal_id=proposal.proposal_id, review_revision_id=revision_id,
                  account_id=proposal.account_id, decision=decision, actor_label=ACTOR,
                  decided_at=T0 + timedelta(minutes=30))
    values.update(overrides)
    return ReviewDecisionRecord(**values)


def accepted_lineage(bridge: Bridge, artifact=None, proposal_id="aiprop_fixture1", suffix="1"):
    """A stored proposal with revision 1 and its ACCEPTED decision. Returns (proposal, revision, decision)."""
    artifact = artifact or bridge.artifact
    proposal = proposal_record(artifact, proposal_id, artifact.account_id, artifact.interaction_id)
    bridge.store.add_proposal(proposal)
    revision = revision_record(proposal, review_revision_id=f"aireview_fixture{suffix}")
    bridge.store.add_revision(revision)
    decision = decision_record(proposal, revision.review_revision_id, decision_id=f"aidecision_fixture{suffix}")
    bridge.store.add_decision(decision)
    return proposal, revision, decision


# --- raw grant / confirmation / consumption rows (no production API writes these in 7C) --------

GRANT_ISSUED = T0 + timedelta(hours=1)


def grant_id(n: int) -> str:
    return "grant_" + f"{n:064x}"


def grant_values(revision: ReviewRevisionRecord, decision_id: str, *, n=1, sequence=1, supersedes=None,
                 issued_at=GRANT_ISSUED, **overrides) -> dict:
    values = dict(
        grant_id=grant_id(n), grant_format="baec-human-grant/v1", action="CONFIRM_BAEC",
        issuing_surface="streamlit-review/v1", account_id=revision.account_id, interaction_id=revision.interaction_id,
        artifact_id=revision.artifact_id, proposal_id=revision.proposal_id, proposal_digest=revision.proposal_digest,
        review_revision_id=revision.review_revision_id, review_content_digest=revision.review_content_digest,
        decision_id=decision_id, issue_sequence=sequence, supersedes_grant_id=supersedes,
        baec_id=f"BAEC-fixture-{n}", actor_label=ACTOR, issued_at=encode_datetime(issued_at), ttl_seconds=900,
        expires_at=encode_datetime(issued_at + timedelta(minutes=15)), grant_digest=sha256_text(f"grant-{n}"),
    )
    values.update(overrides)
    return values


def insert_row(connection, table: str, values: dict, verb: str = "INSERT") -> None:
    columns = tuple(values)
    connection.execute(f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                       tuple(values[c] for c in columns))


def confirmed_baec(bridge: Bridge, baec_id: str, *, actor=ACTOR, authorized_at=GRANT_ISSUED,
                   interaction_id=INT, account_id=ACC) -> int:
    """A CURRENT confirmed BAEC whose authorization matches a grant; returns its authorization_id."""
    record = create_confirmed_baec_record(
        candidate(source_excerpt=excerpt(source_id=interaction_id), criterion_evidence=excerpt(source_id=interaction_id),
                  source_interaction_id=interaction_id, account_id=account_id, buyer_exact_statement=HARBOR_QUOTE),
        baec_id=baec_id, captured_at=T0,
        confirmation=HumanAuthorization(actor, authorized_at, AuthorizationAction.CONFIRM_BAEC, baec_id),
    )
    bridge.repository.save_confirmed_baec(record)
    return bridge.execute("SELECT confirmation_authorization_id FROM baec_records WHERE baec_id = ?",
                          (baec_id,)).fetchone()[0]


def link_values(grant: dict, authorization_id: int, **overrides) -> dict:
    values = dict(baec_id=grant["baec_id"], artifact_id=grant["artifact_id"], proposal_id=grant["proposal_id"],
                  review_revision_id=grant["review_revision_id"], grant_id=grant["grant_id"],
                  authorization_id=authorization_id, account_id=grant["account_id"],
                  interaction_id=grant["interaction_id"])
    values.update(overrides)
    return values


def consumption_values(grant: dict, consumed_at=GRANT_ISSUED + timedelta(minutes=5), **overrides) -> dict:
    values = dict(grant_id=grant["grant_id"], baec_id=grant["baec_id"], executor_surface="mcp-write/confirm_baec/v1",
                  consumed_at=encode_datetime(consumed_at))
    values.update(overrides)
    return values
