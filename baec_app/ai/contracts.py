"""The v1 extraction identifiers and the strict structured-output contract.

The output models enforce shape only: required fields, exact scalar types, exact
lowercase machine tokens, and no extra fields. Wrong-case or near-match tokens are
refused, never normalized. The implementation coherence rules and limits (design
D8) are checked afterwards by validation.py, so a D8 violation is a semantic
validation failure, not a parse failure.

These are AI-owned types. None of them is a domain type, and nothing here maps an
AI status to a BAEC classification or a criterion finding.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

TASK_TYPE = "baec_extraction"
TASK_VERSION = "baec-extraction-task/v1"
INPUT_VERSION = "baec-extraction-input/v1"
OUTPUT_SCHEMA_VERSION = "baec-extraction-output/v1"
REQUEST_SPEC_VERSION = "baec-ai-request-spec/v1"
PROVIDER = "anthropic"
API_METHOD = "messages.create"
MAX_TOKENS = 4096

AnalysisStatus = Literal["possible_baec_language", "no_clear_baec_language", "insufficient_context"]
AttributedSpeaker = Literal["buyer", "seller", "unclear"]  # AI inference about who spoke; never verified
Criterion = Literal["present_non_evaluation", "prospective_condition", "buyer_articulation", "evaluation_linkage"]
HypothesisStatus = Literal["supported", "not_supported", "unclear"]

CRITERIA: tuple[str, ...] = ("present_non_evaluation", "prospective_condition", "buyer_articulation", "evaluation_linkage")

_CLOSED = ConfigDict(strict=True, extra="forbid", frozen=True)


# The output models carry comments, not docstrings: Pydantic would copy a docstring into the
# JSON schema as a description, which is sent to the model and covered by output_schema_digest.


# Text the AI copied from the source interaction. Its speaker and relevance are AI inference.
class SourceExcerpt(BaseModel):
    model_config = _CLOSED

    excerpt_id: str
    source_interaction_id: str
    text: str
    attributed_speaker: AttributedSpeaker


# An AI hypothesis about one constitutive criterion. Not a domain CriterionAssessment.
class CriterionHypothesis(BaseModel):
    model_config = _CLOSED

    criterion: Criterion
    status: HypothesisStatus
    excerpt_refs: list[str]
    explanation: str


# baec-extraction-output/v1: one AI extraction for human review.
class BaecExtractionOutput(BaseModel):
    model_config = _CLOSED

    analysis_status: AnalysisStatus
    source_excerpts: list[SourceExcerpt]
    normalized_condition: str | None
    normalized_evaluation_link: str | None
    criterion_hypotheses: list[CriterionHypothesis]
    uncertainties: list[str]
