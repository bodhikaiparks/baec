"""Phase 7D: the baec-ai-proposal-mapping/v1 contract, pinned (design §6.3).

Semantic assertions on the content shape and one exact digest fixture. Changing the mapping algorithm
changes these pins and requires a new mapping version in a future approved increment.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from baec_app.ai import contracts, validation
from baec_app.ai.canonical import CANONICALIZATION_VERSION, canonical_json
from baec_app.application import ai_proposal_mapping as mapping_module
from baec_app.application.ai_proposal_mapping import (
    COMPATIBLE_IDENTITY,
    CRITERION_ORDER,
    ELIGIBILITY_CODES,
    MAPPING_VERSION,
    PROPOSAL_CONTENT_VERSION,
    AiProposalMappingService,
    ArtifactNotEligible,
    map_artifact_to_proposal_content,
    proposal_id_for,
)
from baec_app.data.database import (
    BRIDGE_ELIGIBLE_VALIDATION_VERSION,
    BRIDGE_MAPPING_VERSION,
    BRIDGE_PROPOSAL_CONTENT_VERSION,
)
from baec_app.domain.enums import BaecCriterion
from tests.mapping_builders import mapping  # noqa: F401

MODULE = Path(mapping_module.__file__)
# The exact content digest of the standard fixture artifact (tests/mapping_builders.py: THRESHOLD_TEXT, the
# ai_builders.output() extraction, prompt v2, FixedClock(START)). Pinned: any change to the mapping changes it.
GOLDEN_DIGEST = "b3197eb46e17fba854c2f2e38b12d7a8d5a8a78de9bd8f8132273ed833d55bf5"
GOLDEN_PROPOSAL_ID = "aiprop_22e659991620f89c2b3959389b4735084c3738988264babde494e8ddd1d4ec4b"

TOP_LEVEL = {"content_version", "mapping_version", "origin", "account_id", "interaction_id", "artifact",
             "ai_analysis_status", "ai_suggested_excerpts", "ai_normalized_condition",
             "ai_normalized_evaluation_link", "ai_criterion_hypotheses", "ai_uncertainties"}
ARTIFACT_BLOCK = {"artifact_id", "artifact_digest"}  # the stable source reference; the rest is reconstructed
# Reconstructable Phase 6 provenance: never copied into the snapshot.
RECONSTRUCTABLE = {"ai_run_id", "provider", "requested_model", "response_model", "prompt_version", "prompt_digest",
                   "validation_version", "output_schema_version", "request_digest", "artifact_created_at",
                   "input_digest", "output_digest"}
# Authoritative human decisions and authority: never produced by the mapping.
FORBIDDEN_KEYS = {"buyer_exact_statement", "buyer_role", "evidence_selections", "evidence_assertions", "findings",
                  "finding", "source_selection_id", "articulation_origin", "elicitation_mode", "stringency",
                  "normalization", "decision", "authorization", "authorized_by", "approval", "approved", "grant",
                  "grant_id", "baec_id", "classification", "account_state", "state", "captured_at", "rationale"}
FORBIDDEN_VALUES = {"BUYER_FACT", "SELLER_OBSERVATION", "EXTERNAL_EVIDENCE", "MET", "NOT_MET", "UNKNOWN",
                    "CONFIRMED_BAEC", "ACCEPTED", "ACTIVE_OPPORTUNITY", "CONDITIONALLY_DORMANT", "NO_PLAUSIBLE_PATH"}


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield "key", key
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    else:
        yield "value", value


# --- identities ---------------------------------------------------------------------------


def test_the_mapping_identities_are_pinned():
    assert MAPPING_VERSION == BRIDGE_MAPPING_VERSION == "baec-ai-proposal-mapping/v1"
    assert PROPOSAL_CONTENT_VERSION == BRIDGE_PROPOSAL_CONTENT_VERSION == "baec-ai-proposal-content/v1"


def test_the_compatibility_allowlist_is_exact_and_matches_the_phase6_identities():
    assert COMPATIBLE_IDENTITY == {
        "task_type": "baec_extraction", "task_version": "baec-extraction-task/v1",
        "output_schema_version": "baec-extraction-output/v1", "canonicalization_version": "baec-canonical-json/v1",
        "validation_version": "baec-extraction-validation/v2",
    }
    assert (contracts.TASK_TYPE, contracts.TASK_VERSION, contracts.OUTPUT_SCHEMA_VERSION, CANONICALIZATION_VERSION,
            validation.VALIDATION_VERSION) == tuple(COMPATIBLE_IDENTITY.values())
    assert COMPATIBLE_IDENTITY["validation_version"] == BRIDGE_ELIGIBLE_VALIDATION_VERSION
    assert "IMPLEMENTATION compatibility" in mapping_module.__doc__ and "not a BAEC research" in mapping_module.__doc__
    assert "model" not in COMPATIBLE_IDENTITY and "requested_model" not in COMPATIBLE_IDENTITY  # no default model


def test_criterion_order_aligns_ai_tokens_with_domain_names_only():
    assert tuple(token for token, _ in CRITERION_ORDER) == contracts.CRITERIA
    assert tuple(name for _, name in CRITERION_ORDER) == tuple(c.value for c in BaecCriterion)


def test_eligibility_codes_are_closed():
    assert ELIGIBILITY_CODES == ("artifact_not_found", "artifact_integrity_failure", "run_not_successful",
                                 "incompatible_identity", "validator_unavailable", "artifact_output_invalid",
                                 "no_possible_baec_language", "source_binding_invalid", "lineage_already_confirmed",
                                 "stored_proposal_mismatch")
    with pytest.raises(ValueError):
        ArtifactNotEligible("something else")


# --- the content shape and digest ------------------------------------------------------------


def test_the_standard_fixture_maps_to_the_pinned_content_digest_and_identifier(mapping):
    proposal = mapping.map(mapping.artifact()).proposal
    assert proposal.proposal_digest == GOLDEN_DIGEST == hashlib.sha256(proposal.content.encode("utf-8")).hexdigest()
    assert proposal.proposal_id == GOLDEN_PROPOSAL_ID == proposal_id_for(proposal.artifact_id)
    expected_id = "aiprop_" + hashlib.sha256(canonical_json(
        {"artifact_id": proposal.artifact_id, "mapping_version": MAPPING_VERSION}).encode("utf-8")).hexdigest()
    assert proposal.proposal_id == expected_id


def test_the_mapper_serializes_with_the_phase6_canonical_rules(mapping):
    from baec_app.application.ai_proposal_mapping import canonical_text
    for value in ({"b": [1, "é", None, True], "a": {"z": "x", "y": ""}}, {"k": "Ünïcode \u2028"}, []):
        assert canonical_text(value) == canonical_json(value)


def test_the_content_is_canonical_json_of_exactly_the_approved_shape(mapping):
    content_text = mapping.map(mapping.artifact()).proposal.content
    content = json.loads(content_text)
    assert canonical_json(content) == content_text
    assert set(content) == TOP_LEVEL and set(content["artifact"]) == ARTIFACT_BLOCK
    assert (content["content_version"], content["mapping_version"], content["origin"]) == (
        PROPOSAL_CONTENT_VERSION, MAPPING_VERSION, "AI_DRAFT")
    assert set(content["ai_uncertainties"]) == {"values", "provenance"}
    for key in ("ai_normalized_condition", "ai_normalized_evaluation_link"):
        assert set(content[key]) == {"value", "provenance"}


def test_the_content_holds_no_authoritative_field_or_value(mapping):
    content = json.loads(mapping.map(mapping.artifact()).proposal.content)
    items = list(walk(content))
    assert {key for kind, key in items if kind == "key"} & (FORBIDDEN_KEYS | RECONSTRUCTABLE) == set()
    assert {value for kind, value in items if kind == "value"} & FORBIDDEN_VALUES == set()
    provenance = [content[k]["provenance"] for k in ("ai_normalized_condition", "ai_normalized_evaluation_link",
                                                     "ai_uncertainties")]
    provenance += [h["provenance"] for h in content["ai_criterion_hypotheses"]]
    provenance += [e["ai_attributed_speaker"]["provenance"] for e in content["ai_suggested_excerpts"]]
    assert set(provenance) == {"AI_INFERENCE"}


def test_the_digest_covers_content_only_never_identifier_or_time(mapping):
    artifact_id = mapping.artifact()
    proposal = mapping.map(artifact_id).proposal
    assert proposal.proposal_id not in proposal.content
    from baec_app.data.repository import encode_datetime
    assert encode_datetime(proposal.created_at) not in proposal.content  # the mapping time is never content
    assert '"created_by"' not in proposal.content and "FIXTURE-reviewer" not in proposal.content


# --- purity and reachability -------------------------------------------------------------------


def _tree():
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    names = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_module_imports_no_provider_clock_randomness_network_or_authority():
    imported = _imports()
    assert imported == {
        "__future__", "hashlib", "json", "sqlite3", "dataclasses", "baec_app.ai.verification",
        "baec_app.application.context", "baec_app.application.errors", "baec_app.data.ai_provenance",
        "baec_app.data.database", "baec_app.data.proposal_bridge", "baec_app.data.records", "baec_app.data.repository",
    }
    assert {name for name in imported if name.startswith("baec_app.ai")} == {"baec_app.ai.verification"}


def test_the_pure_mapper_calls_nothing_impure():
    source = inspect.getsource(map_artifact_to_proposal_content)
    tree = ast.parse(source)
    called = {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
              for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert called == {"type", "TypeError", "_inference", "list", "canonical_text"}


def test_no_authority_or_domain_write_is_reachable_from_the_module():
    source = MODULE.read_text(encoding="utf-8")
    calls = {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
             for node in ast.walk(_tree()) if isinstance(node, ast.Call)}
    for forbidden in ("save_confirmed_baec", "save_classification_record", "persist_transition_to_conditionally_dormant",
                      "persist_transition_to_active_opportunity", "persist_transition_to_no_plausible_path",
                      "record_dormancy_judgment", "add_revision", "add_decision", "approve", "redeem", "authorize",
                      "register", "build_request", "record_run", "record_terminal_outcome", "extract_interaction",
                      "invoke"):
        assert forbidden not in calls, forbidden
    for forbidden in ("state_machine", "baec_rules", "authority", "approval", "facades", "classification",
                      "account_state", "dormancy", "requests", "proposals", "anthropic", "composition", "service",
                      "provider", "staleness", "HumanAuthorization", "BUYER_FACT", "SELLER_OBSERVATION"):
        assert forbidden not in _imports() and not any(forbidden in name.split(".")[-1] for name in _imports()
                                                       if name.startswith("baec_app")), forbidden
    assert "BUYER_FACT" not in source and "SELLER_OBSERVATION" not in source and "CriterionFinding" not in source
    public = {name for name, _ in inspect.getmembers(AiProposalMappingService, inspect.isfunction)
              if not name.startswith("_")}
    assert public == {"map_artifact"}


def test_the_mapping_is_not_exported_to_interfaces_or_mcp_yet():
    import baec_app.application as application
    assert not {"AiProposalMappingService", "map_artifact_to_proposal_content", "ArtifactNotEligible"} & set(
        application.__all__)
    root = Path(__file__).resolve().parents[1] / "baec_app"
    users = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([node.module or ""] + [a.name for a in node.names] if isinstance(node, ast.ImportFrom)
                     else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            if any("ai_proposal_mapping" in name for name in names):
                users.append(path.relative_to(root).as_posix())
    assert users == ["application/proposal_authorization.py",  # Phase 7F-B: lineage re-verification at execution
                     "application/proposal_review.py",  # Phase 7E: the review layer re-verifies through it
                     "application/public_demo_recording.py"]  # public demo: E1-E6 on the replayed recording
