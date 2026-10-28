"""Stage E: strict AI proposal contract."""

import copy
from dataclasses import FrozenInstanceError

import pytest

from baec_app.engine3.enums import ArtifactAudience, ArtifactOrigin, ArtifactType
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.proposal_contract import (
    ARTIFACT_AUDIENCE,
    MAX_DISCOVERY_QUESTIONS,
    PROPOSAL_SCHEMA_VERSION,
    load_prerecorded_preparation_proposal,
)
from tests.engine3.stage_e_adapter import load_inputs

RAW = {r["recording_id"]: r["proposal"] for r in load_inputs()["recordings"]}


def _valid():
    return copy.deepcopy(RAW["E3E-P05"])


def test_valid_proposal_parses_with_fixed_origin_and_provenance():
    proposal = load_prerecorded_preparation_proposal(_valid())
    assert proposal.origin is ArtifactOrigin.AI_INFERENCE
    assert proposal.provenance == "PRERECORDED_SYNTHETIC"
    assert proposal.schema_version == PROPOSAL_SCHEMA_VERSION
    assert len(proposal.artifacts) == 4


def test_proposal_is_immutable():
    proposal = load_prerecorded_preparation_proposal(_valid())
    with pytest.raises(FrozenInstanceError):
        proposal.origin = ArtifactOrigin.HUMAN_EDITED


def test_proposal_digest_is_deterministic_and_content_bound():
    first = load_prerecorded_preparation_proposal(_valid()).digest
    assert first == load_prerecorded_preparation_proposal(_valid()).digest
    assert len(first) == 64
    changed = _valid()
    seg = changed["artifacts"][1]["segments"][2]
    seg["text"] += " "
    changed["artifacts"][1]["rendered_text"] += " "
    assert load_prerecorded_preparation_proposal(changed).digest != first
    rebound = _valid()
    rebound["prerecorded_run_id"] = "RUN-OTHER"
    assert load_prerecorded_preparation_proposal(rebound).digest != first


def test_audience_is_derived_from_artifact_type_only():
    assert ARTIFACT_AUDIENCE == {
        ArtifactType.INTERNAL_WHY_NOW_SUMMARY: ArtifactAudience.INTERNAL,
        ArtifactType.FOLLOW_UP_EMAIL: ArtifactAudience.BUYER_FACING,
        ArtifactType.CALL_OPENING: ArtifactAudience.BUYER_FACING,
        ArtifactType.DISCOVERY_QUESTION_SET: ArtifactAudience.BUYER_FACING,
    }
    audiences = [a.audience for a in load_prerecorded_preparation_proposal(_valid()).artifacts]
    assert audiences == [ArtifactAudience.INTERNAL] + [ArtifactAudience.BUYER_FACING] * 3


@pytest.mark.parametrize("key", ["schema_version", "origin", "bound_context_digest", "artifacts", "requested_action"])
def test_missing_envelope_field_fails_closed(key):
    raw = _valid()
    del raw[key]
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize(
    "key",
    ["disclosure_decisions", "grounding_items", "upstream", "context_state", "preparation_request",
     "authorized", "human_authorization", "confidence", "propensity_score", "send_at", "crm_task"],
)
def test_authority_fields_cannot_be_smuggled_into_a_proposal(key):
    raw = _valid()
    raw[key] = True
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize("key", ["audience", "origin", "disposition", "notes"])
def test_artifact_rejects_unknown_fields(key):
    raw = _valid()
    raw["artifacts"][1][key] = "INTERNAL"
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize(
    "field,value",
    [("specificity", True), ("specificity", "0"), ("question_has_premise", 1), ("grounding_refs", "G-AR-STATEMENT"),
     ("claim_type", "factual_assertion"), ("semantic_class", "INTENT"), ("declared_value", 9)],
)
def test_segment_types_are_exact(field, value):
    raw = _valid()
    raw["artifacts"][1]["segments"][0][field] = value
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


def test_segment_rejects_extra_and_missing_fields():
    raw = _valid()
    raw["artifacts"][1]["segments"][0]["buyer_intent"] = "HIGH"
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)
    raw = _valid()
    del raw["artifacts"][1]["segments"][0]["reference_use_mode"]
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize("rendered", ["", "Different text.", None])
def test_rendered_text_must_equal_segment_coverage(rendered):
    raw = _valid()
    if rendered is None:
        raw["artifacts"][1]["rendered_text"] = raw["artifacts"][1]["rendered_text"].replace(" ", "  ", 1)
    else:
        raw["artifacts"][1]["rendered_text"] = rendered
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


def test_empty_artifacts_and_segments_fail_closed():
    raw = _valid()
    raw["artifacts"] = []
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)
    raw = _valid()
    raw["artifacts"][1]["segments"] = []
    raw["artifacts"][1]["rendered_text"] = ""
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


def test_duplicate_artifact_or_segment_ids_fail_closed():
    raw = _valid()
    raw["artifacts"][2]["artifact_id"] = raw["artifacts"][1]["artifact_id"]
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)
    raw = _valid()
    raw["artifacts"][1]["segments"][1]["segment_id"] = raw["artifacts"][1]["segments"][0]["segment_id"]
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


def test_discovery_question_limit_is_five():
    assert MAX_DISCOVERY_QUESTIONS == 5
    raw = _valid()
    qs = raw["artifacts"][3]
    template = qs["segments"][1]
    while len(qs["segments"]) < 5:
        qs["segments"].append({**template, "segment_id": f"QX{len(qs['segments'])}"})
    qs["rendered_text"] = " ".join(s["text"] for s in qs["segments"])
    load_prerecorded_preparation_proposal(raw)  # five is permitted
    qs["segments"].append({**template, "segment_id": "QX6"})
    qs["rendered_text"] = " ".join(s["text"] for s in qs["segments"])
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize("origin", ["HUMAN_EDITED", "HUMAN", "BUYER_EVIDENCE", "ai_inference"])
def test_model_cannot_claim_non_ai_origin(origin):
    raw = _valid()
    raw["origin"] = origin
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize("digest", ["", "0" * 63, "A" * 64, "g" * 64, "current", None])
def test_bound_context_digest_must_be_exact_sha256(digest):
    raw = _valid()
    raw["bound_context_digest"] = digest
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


@pytest.mark.parametrize("value", ["SEND_EMAIL", "SCHEDULE_MEETING", "outreach_execution", None])
def test_requested_action_vocabulary_is_closed(value):
    raw = _valid()
    raw["requested_action"] = value
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(raw)


def test_parser_does_not_mutate_its_input():
    raw = _valid()
    snapshot = copy.deepcopy(raw)
    load_prerecorded_preparation_proposal(raw)
    assert raw == snapshot


@pytest.mark.parametrize("value", [None, [], "proposal", 7])
def test_non_object_input_fails_closed(value):
    with pytest.raises(Engine3ValidationError):
        load_prerecorded_preparation_proposal(value)
