"""Phase 6C-B: the v1 identifiers, the strict output contract, and the pinned prompt and schema."""

import json

import anthropic
import pydantic
import pytest

from baec_app.ai import contracts
from baec_app.ai.canonical import canonical_digest, sha256_text
from baec_app.ai.contracts import BaecExtractionOutput, CriterionHypothesis, SourceExcerpt
from baec_app.ai.prompts import PROMPT_VERSION, PROMPT_VERSION_V1, SYSTEM_PROMPT_V1
from tests.ai_builders import as_text, output

PROMPT_V1_DIGEST = "b1782f1ce0afdd96eb335cece03912b9aa53c3a5ac3c71a2017f1cbe9ba5eadd"
OUTPUT_SCHEMA_V1_DIGEST = "95af33f4d10e4db13400bb3e97a7db9fe5e272e46ad91fcebb448eae44a48e9c"


def test_the_locked_v1_identifiers():
    assert (contracts.TASK_TYPE, contracts.TASK_VERSION) == ("baec_extraction", "baec-extraction-task/v1")
    # prompt v1 is the frozen historical identity; v2 is current since Phase 6D-E4
    assert (PROMPT_VERSION_V1, PROMPT_VERSION) == ("baec-extraction-prompt/v1", "baec-extraction-prompt/v2")
    assert contracts.INPUT_VERSION == "baec-extraction-input/v1"
    assert contracts.OUTPUT_SCHEMA_VERSION == "baec-extraction-output/v1"
    assert contracts.REQUEST_SPEC_VERSION == "baec-ai-request-spec/v1"
    assert (contracts.PROVIDER, contracts.API_METHOD, contracts.MAX_TOKENS) == ("anthropic", "messages.create", 4096)
    assert not hasattr(contracts, "DEFAULT_MODEL") and not hasattr(contracts, "MODEL")


def test_the_v1_prompt_is_pinned_to_its_digest():
    """A substantive prompt edit needs a new PROMPT_VERSION; this pin fails until it gets one."""
    assert sha256_text(SYSTEM_PROMPT_V1) == PROMPT_V1_DIGEST
    assert SYSTEM_PROMPT_V1.startswith(
        "You extract possible BAEC-relevant source language from one sales interaction, for later human review.")


def test_the_prompt_is_exactly_the_approved_document_wording():
    from pathlib import Path

    document = (Path(__file__).resolve().parents[1] / "docs" / "PHASE6C_ANTHROPIC_SDK_CHARACTERIZATION.md").read_text(encoding="utf-8")
    approved = document[document.index("## 9. Prompt"):].split("```\n", 2)[1].rstrip("\n")
    assert SYSTEM_PROMPT_V1 == approved


def test_the_v1_output_schema_is_pinned_and_carries_no_descriptions():
    schema = anthropic.transform_schema(BaecExtractionOutput)
    assert canonical_digest(schema) == OUTPUT_SCHEMA_V1_DIGEST
    assert "description" not in json.dumps(schema)  # docstrings would otherwise be sent to the model


def test_every_model_is_strict_closed_and_frozen():
    for model in (BaecExtractionOutput, SourceExcerpt, CriterionHypothesis):
        config = model.model_config
        assert (config["strict"], config["extra"], config["frozen"]) == (True, "forbid", True)
    parsed = BaecExtractionOutput.model_validate_json(as_text(output()))
    with pytest.raises(pydantic.ValidationError):
        parsed.analysis_status = "no_clear_baec_language"


def test_the_exact_field_set_and_no_forbidden_fields():
    assert list(BaecExtractionOutput.model_fields) == [
        "analysis_status", "source_excerpts", "normalized_condition", "normalized_evaluation_link",
        "criterion_hypotheses", "uncertainties"]
    assert list(SourceExcerpt.model_fields) == ["excerpt_id", "source_interaction_id", "text", "attributed_speaker"]
    assert list(CriterionHypothesis.model_fields) == ["criterion", "status", "excerpt_refs", "explanation"]
    every = set(BaecExtractionOutput.model_fields) | set(SourceExcerpt.model_fields) | set(CriterionHypothesis.model_fields)
    for forbidden in ("confidence", "score", "probability", "purchase", "intent", "reasoning", "thought",
                      "recommendation", "classification", "finding"):
        assert not any(forbidden in name for name in every)


def test_machine_token_vocabularies_are_exact():
    schema = anthropic.transform_schema(BaecExtractionOutput)
    assert schema["properties"]["analysis_status"]["enum"] == [
        "possible_baec_language", "no_clear_baec_language", "insufficient_context"]
    assert schema["$defs"]["SourceExcerpt"]["properties"]["attributed_speaker"]["enum"] == ["buyer", "seller", "unclear"]
    assert schema["$defs"]["CriterionHypothesis"]["properties"]["criterion"]["enum"] == list(contracts.CRITERIA)
    assert schema["$defs"]["CriterionHypothesis"]["properties"]["status"]["enum"] == ["supported", "not_supported", "unclear"]


def _with(path, value):
    data = output()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


REFUSED = {
    "wrong-case status": _with(("analysis_status",), "Possible_BAEC_Language"),
    "uppercase status": _with(("analysis_status",), "POSSIBLE_BAEC_LANGUAGE"),
    "padded status": _with(("analysis_status",), " possible_baec_language"),
    "domain-style criterion": _with(("criterion_hypotheses", 0, "criterion"), "PRESENT_NON_EVALUATION"),
    "domain-style status": _with(("criterion_hypotheses", 0, "status"), "MET"),
    "wrong-case speaker": _with(("source_excerpts", 0, "attributed_speaker"), "Buyer"),
    "unknown speaker": _with(("source_excerpts", 0, "attributed_speaker"), "customer"),
    "extra top-level field": dict(output(), confidence=0.9),
    "extra nested field": _with(("source_excerpts", 0, "verified"), True),
    "number for text": _with(("normalized_condition",), 10),
    "string for list": _with(("uncertainties",), "none"),
    "integer for ref": _with(("criterion_hypotheses", 0, "excerpt_refs"), [1]),
}


@pytest.mark.parametrize("value", REFUSED.values(), ids=REFUSED.keys())
def test_non_exact_shapes_and_tokens_are_refused_never_normalized(value):
    with pytest.raises(pydantic.ValidationError):
        BaecExtractionOutput.model_validate_json(json.dumps(value))


def test_missing_required_fields_are_refused_but_null_normalizations_are_allowed():
    data = output()
    del data["uncertainties"]
    with pytest.raises(pydantic.ValidationError):
        BaecExtractionOutput.model_validate_json(json.dumps(data))
    parsed = BaecExtractionOutput.model_validate_json(as_text(output(normalized_condition=None, normalized_evaluation_link=None)))
    assert parsed.normalized_condition is None


def test_d8_limits_are_not_parse_rules():
    """Blank, oversized, or incoherent content parses; validation.py decides it (a semantic failure)."""
    data = output(uncertainties=[" "] * 11, normalized_condition="x" * 600, source_excerpts=[])
    data["criterion_hypotheses"] = data["criterion_hypotheses"][:2]
    BaecExtractionOutput.model_validate_json(json.dumps(data))
