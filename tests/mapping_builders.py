"""Shared setup for the Phase 7D mapping tests. Setup only; expectations live in the tests.

TEST-ONLY FIXTURES (Phase 7 design D15). Artifacts are produced in an ephemeral per-test
database by the real Phase 6 ExtractionService driven by the offline FakeProvider: no
network, no model. Every account, interaction, artifact, and model identifier carries the
reserved FIXTURE prefix. These artifacts are not, and must never be presented as, evidence
of any actual Anthropic model invocation; provider = 'anthropic' only fills the Phase 6
schema slot.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from baec_app.ai.composition import open_extraction_runtime
from baec_app.application.ai_proposal_mapping import AiProposalMappingService
from baec_app.data.database import open_database
from baec_app.data.proposal_bridge import ProposalBridgeStore
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.models import Account
from tests.ai_builders import START, THRESHOLD_TEXT, FakeProvider, Ids, as_text, output, response
from tests.application_builders import FixedClock

ACC, OTHER_ACC = "FIXTURE-ACC-1", "FIXTURE-ACC-2"
INT, INT_SAME_TEXT, INT_OTHER_ACCOUNT = "FIXTURE-INT-1", "FIXTURE-INT-2", "FIXTURE-INT-3"
MODEL = "FIXTURE-model-not-a-real-invocation"
ACTOR = "FIXTURE-reviewer"
MAPPED_AT = START + timedelta(hours=2)


class Mapping:
    """An ephemeral schema-v7 database with fixture sources, and helpers to produce and map artifacts."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.connection = open_database(path)
        repository = Repository(self.connection)
        repository.add_account(Account(ACC, "Fixture Harbor"))
        repository.add_account(Account(OTHER_ACC, "Fixture Other"))
        # Identical source text in three interactions: identical words never merge lineages.
        for interaction_id, account_id in ((INT, ACC), (INT_SAME_TEXT, ACC), (INT_OTHER_ACCOUNT, OTHER_ACC)):
            repository.add_interaction(SourceInteraction(interaction_id, account_id, START - timedelta(days=1),
                                                         THRESHOLD_TEXT))
        self.run_ids, self.artifact_ids = Ids("FIXTURE-RUN"), Ids("FIXTURE-ART")
        self.clock = FixedClock(MAPPED_AT)
        self.service = AiProposalMappingService(self.connection, clock=self.clock)
        self.bridge = ProposalBridgeStore(self.connection)

    def extract(self, interaction_id=INT, account_id=ACC, reply=None, **output_changes):
        """Run the real extraction service offline; returns its ExtractionExecutionResult."""
        if reply is None:
            reply = response(as_text(output(interaction_id=interaction_id, **output_changes)), model=MODEL)
        runtime = open_extraction_runtime(self.path, provider=FakeProvider(reply), clock=FixedClock(START),
                                          run_ids=self.run_ids, artifact_ids=self.artifact_ids)
        try:
            return runtime.service.extract_interaction(account_id=account_id, interaction_id=interaction_id,
                                                       model=MODEL)
        finally:
            runtime.close()

    def artifact(self, interaction_id=INT, account_id=ACC, **output_changes) -> str:
        result = self.extract(interaction_id, account_id, **output_changes)
        assert result.artifact_id is not None, result.status
        return result.artifact_id

    def map(self, artifact_id: str):
        return self.service.map_artifact(artifact_id, created_by=ACTOR)

    def count(self, table: str) -> int:
        return self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def mapping(tmp_path):
    m = Mapping(str(tmp_path / "mapping.sqlite3"))
    yield m
    m.connection.close()
