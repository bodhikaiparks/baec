"""Shared setup for the Phase 7E human-review tests. Setup only; expectations live in the tests.

TEST-ONLY FIXTURES (design D15): an ephemeral schema-v7 database per test, a FIXTURE-prefixed artifact produced
offline by the real Phase 6 service with the FakeProvider, and its mapped AI_DRAFT. Never real model output.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from baec_app.application.ai_proposal_mapping import proposal_id_for
from baec_app.application.proposal_review import (
    NO_STRINGENCY_STATED,
    CriterionDecision,
    EvidenceSelection,
    ProposalReviewService,
    ReviewDecisions,
)
from baec_app.domain.enums import (
    ArticulationOrigin,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    ProvenanceCategory,
    ThresholdComparator,
)
from baec_app.domain.models import StringencyExpression
from tests.application_builders import FixedClock
from tests.mapping_builders import MAPPED_AT, Mapping

SUGGESTED = "If our supplier raises pricing by more than 10% at renewal, we would reopen the evaluation."
MANUAL = "No, we're not looking at alternatives at the moment."
STATEMENT = "we would reopen the evaluation"  # a smaller span inside SUGGESTED: needs its own selection to be used
REVIEWED_AT = MAPPED_AT + timedelta(hours=1)
REVIEWER = "FIXTURE-reviewer"


class Review(Mapping):
    """A Mapping database holding one mapped AI_DRAFT, plus a review service over the same connection."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.artifact_id = self.artifact()
        self.proposal = self.map(self.artifact_id).proposal
        self.pid = self.proposal.proposal_id
        assert self.pid == proposal_id_for(self.artifact_id)
        self.review_clock = FixedClock(REVIEWED_AT)
        counter = iter(range(1, 10_000))
        self.service = ProposalReviewService(self.connection, clock=self.review_clock,
                                             new_id=lambda prefix: f"{prefix}fixture{next(counter):04d}")


@pytest.fixture
def review(tmp_path):
    r = Review(str(tmp_path / "review.sqlite3"))
    yield r
    r.connection.close()


def selections(buyer=ProvenanceCategory.BUYER_FACT):
    return (EvidenceSelection("s1", SUGGESTED, buyer, "e1"),
            EvidenceSelection("s2", MANUAL, ProvenanceCategory.BUYER_FACT, None))


def findings(**overrides):
    evidence = {BaecCriterion.PRESENT_NON_EVALUATION: ("s2",)}
    return tuple(CriterionDecision(c, overrides.get(c.value, CriterionFinding.MET), evidence.get(c, ("s1",)))
                 for c in BaecCriterion)


def decisions(**changes) -> ReviewDecisions:
    values = dict(
        evidence_selections=selections(), source_selection_id="s1", buyer_exact_statement=SUGGESTED,
        findings=findings(), articulation_origin=ArticulationOrigin.BUYER_GENERATED,
        elicitation_mode=ElicitationMode.CEE_ELICITED,
        stringency=StringencyExpression("more than 10%", ThresholdComparator.GREATER_THAN, Decimal("10"), "%"),
        final_normalized_condition="Pricing rising by more than 10% at renewal.",
        final_normalized_evaluation_link=None,
    )
    values.update(changes)
    return ReviewDecisions(**values)


__all__ = ["NO_STRINGENCY_STATED", "REVIEWER", "Review", "decisions", "findings", "review", "selections"]
