"""Test-only adapter for Stage E prerecorded proposal fixtures.

Proposals are parsed by the production contract. Contexts and recorded human
edits are caller-supplied test inputs and are built here.
"""

from __future__ import annotations

import json
from pathlib import Path

from baec_app.engine3.enums import ExternalReferenceDisposition, SourceIdentityDisclosure
from baec_app.engine3.models import ContextState, GroundingItem, UpstreamEligibility
from baec_app.engine3.offline_evaluation import EvaluationContext, RecordedHumanEdit
from baec_app.engine3.proposal_contract import parse_artifact

HERE = Path(__file__).resolve().parent
INPUTS_PATH = HERE / "fixtures" / "ENGINE3_STAGE_E_PRERECORDED_PROPOSALS.json"
ORACLE_PATH = HERE / "fixtures" / "ENGINE3_STAGE_E_OFFLINE_EVALUATION_ORACLE.json"


def load_inputs():
    return json.loads(INPUTS_PATH.read_text(encoding="utf-8"))


def load_oracle():
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


def _grounding(d):
    return GroundingItem(
        grounding_item_id=d["grounding_item_id"],
        exact_value=d["exact_value"],
        disposition=ExternalReferenceDisposition(d["disposition"]),
        source_identity_disclosure=SourceIdentityDisclosure(d["source_identity_disclosure"]),
        is_buyer_statement=d["is_buyer_statement"],
        adversity_related=d["adversity_related"],
        adversity_context_reviewed=d["adversity_context_reviewed"],
        approximate=d["approximate"],
        approved_specificity=d["approved_specificity"],
    )


def build_context(d) -> EvaluationContext:
    return EvaluationContext(
        upstream=UpstreamEligibility(**d["upstream"]),
        context_state=ContextState(**d["context_state"]),
        grounding_items=tuple(_grounding(x) for x in d["grounding_items"]),
    )


def build_human_edit(d, parent_proposal_digest: str) -> RecordedHumanEdit:
    return RecordedHumanEdit(
        edit_id=d["edit_id"],
        parent_proposal_digest=parent_proposal_digest,
        parent_artifact_id=d["parent_artifact_id"],
        edited=parse_artifact(d["edited"]),
    )
