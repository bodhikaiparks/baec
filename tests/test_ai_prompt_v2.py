"""Phase 6D-E4: prompt v2 aligns the extraction prompt with validation-v2 grounding, offline only.

docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md, Phase 6D-E4 clarification. The E2 live verification showed that
10 of the 11 grounding codes arose in explanation and uncertainty text that prompt v1 never grounded. Prompt v2
states the grounding rules for every model-authored free-text field; validation v2 is unchanged. These tests check
what the prompt communicates and what the request carries. They make no claim about how any model behaves, and they
do not reconstruct the historical E2 model outputs.
"""

import hashlib
import re
from pathlib import Path

import pytest

from baec_app.ai.canonical import CANONICALIZATION_VERSION, canonical_digest, sha256_text
from baec_app.ai.contracts import INPUT_VERSION, MAX_TOKENS, OUTPUT_SCHEMA_VERSION, REQUEST_SPEC_VERSION, TASK_VERSION
from baec_app.ai.prompts import PROMPT_VERSION, PROMPT_VERSION_V1, SYSTEM_PROMPT, SYSTEM_PROMPT_V1, SYSTEM_PROMPT_V2
from baec_app.ai.service import canonical_input
from baec_app.ai.validation import SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION, VALIDATION_VERSION
from tests.ai_builders import MODEL, THRESHOLD_TEXT, FakeProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_V1_DIGEST = "b1782f1ce0afdd96eb335cece03912b9aa53c3a5ac3c71a2017f1cbe9ba5eadd"
PROMPT_V2_DIGEST = "d6296491be408c9a4b9b9561e6e207ff8890eb0f0460ce0c7300ab2cf0ed8845"
REQUEST_V1_DIGEST = "298bf6aac27bb69df99412d63c9586c79697d7342f5bb1a00dbabdb56b9c20a8"  # historical golden
REQUEST_V2_DIGEST = "e142cb5c71c9b173931f04901cc18b1352d97ed78a3eba916c86850e5e71cdde"  # current golden
INPUT_DIGEST = "d2abe6256bda86333548416efe753db30db516aa8bcb4957f95790f426c38dfd"
OUTPUT_SCHEMA_DIGEST = "95af33f4d10e4db13400bb3e97a7db9fe5e272e46ad91fcebb448eae44a48e9c"
# Validation v2 is unchanged by Phase 6D-E4: its source files are pinned to their bytes at the D checkpoint 277ab0f.
VALIDATOR_FILES = {
    "baec_app/ai/grounding.py": "d5e7effd59fc040c9786bb39732c13a8c3a0d5e31fd5d8c630782a9696ee4114",
    "baec_app/ai/validation.py": "0d225899d08822f5529d3d75ecc00cd07ecbf066cbd989ca9cefce296d5ce8f4",
}


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


PROMPT = _normalized(SYSTEM_PROMPT_V2)
GROUNDING = PROMPT[PROMPT.index("grounding rules for all model-authored free text"):]


# --- identity ------------------------------------------------------------------------------------------


def test_prompt_v2_is_current_and_pinned_while_v1_stays_frozen_history():
    assert (PROMPT_VERSION_V1, PROMPT_VERSION) == ("baec-extraction-prompt/v1", "baec-extraction-prompt/v2")
    assert SYSTEM_PROMPT is SYSTEM_PROMPT_V2
    assert sha256_text(SYSTEM_PROMPT_V2) == PROMPT_V2_DIGEST
    assert sha256_text(SYSTEM_PROMPT_V1) == PROMPT_V1_DIGEST  # the Phase 6C and E2 identity, never relabeled


def test_prompt_v2_keeps_every_v1_rule_and_only_adds_grounding_guidance():
    """v2 is v1 with two field bullets extended and one grounding section added; no v1 sentence is removed."""
    for sentence in re.split(r"(?<=[.:])\s+", SYSTEM_PROMPT_V1):
        extended = ("Each explanation is one or two short sentences" in sentence or sentence.startswith("- uncertainties:")
                    or sentence == "short statements of what is not established.")
        if sentence and not extended:  # the two field bullets are extended, never removed
            assert sentence in SYSTEM_PROMPT_V2, sentence
    assert "Each explanation is one or two short sentences, not step-by-step reasoning" in SYSTEM_PROMPT_V2
    assert "- uncertainties: short statements of what is not established" in SYSTEM_PROMPT_V2
    assert "Preserve every threshold, number, unit, timing, and negation exactly." in SYSTEM_PROMPT_V2


def _request(system: str, prompt_version: str):
    content = canonical_input(account_id="ACC-1", interaction_id="INT-T", interaction_text=THRESHOLD_TEXT)
    return FakeProvider().prepare_request(model=MODEL, max_tokens=MAX_TOKENS, system=system, user_content=content,
                                          prompt_version=prompt_version, input_version=INPUT_VERSION,
                                          output_schema_version=OUTPUT_SCHEMA_VERSION)


def test_only_the_prompt_changes_the_request_identity():
    v1, v2 = _request(SYSTEM_PROMPT_V1, PROMPT_VERSION_V1), _request(SYSTEM_PROMPT_V2, PROMPT_VERSION)
    assert v1.digest() == REQUEST_V1_DIGEST  # the old golden is exactly reproduced by the old prompt
    assert v2.digest() == REQUEST_V2_DIGEST
    a, b = v1.to_json_object(), v2.to_json_object()
    assert set(a) == set(b) and sorted(k for k in a if a[k] != b[k]) == ["prompt_version", "system"]
    assert a["messages"] == b["messages"] and sha256_text(b["messages"][0]["content"]) == INPUT_DIGEST
    assert canonical_digest(a["output_config"]["format"]["schema"]) == OUTPUT_SCHEMA_DIGEST
    assert canonical_digest(b["output_config"]["format"]["schema"]) == OUTPUT_SCHEMA_DIGEST
    assert (a["model"], a["max_tokens"], a["request_spec_version"]) == (b["model"], b["max_tokens"], b["request_spec_version"])


def test_every_other_version_label_is_unchanged():
    assert (TASK_VERSION, INPUT_VERSION, OUTPUT_SCHEMA_VERSION, REQUEST_SPEC_VERSION, CANONICALIZATION_VERSION) == (
        "baec-extraction-task/v1", "baec-extraction-input/v1", "baec-extraction-output/v1", "baec-ai-request-spec/v1",
        "baec-canonical-json/v1")


@pytest.mark.parametrize("path", VALIDATOR_FILES)
def test_validation_v2_is_untouched(path):
    assert hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest() == VALIDATOR_FILES[path]
    assert VALIDATION_VERSION == "baec-extraction-validation/v2"
    assert len(SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[VALIDATION_VERSION]) == 38
    assert "baec-extraction-validation/v3" not in SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION


# --- what prompt v2 communicates ------------------------------------------------------------------------

CONTRACT = {
    "all four free-text surfaces": ("normalized_condition", "normalized_evaluation_link",
                                    "every criterion_hypotheses explanation", "every uncertainties entry"),
    "grounded in the interaction": ("use only information grounded in this interaction",),
    "unsupported numeric expressions": ("do not introduce any unsupported number", "number word", "percentage",
                                        "percentage-point expression", "currency amount", "unit-bearing quantity",
                                        "numeric count", "other numeric expression"),
    "unsupported meta-counts": ("do not count or enumerate criteria, conditions, reasons, excerpts, or uncertainties",
                                "unless that count is explicitly stated in the interaction"),
    "comparator dropped": ("do not introduce, drop,",),
    "comparator changed": ("weaken, strengthen, or otherwise change its comparator",),
    "units preserved": ("preserve its magnitude, numeric kind, unit, and threshold comparator",
                        "do not add or remove a unit"),
    "numeric kind preserved": ("numeric kind",),
    "no derivation": ("do not calculate, derive, convert, or round it",),
    "unresolved exact echo": ("past, beyond, within, or over", "repeat that word exactly",
                              "do not paraphrase or drop it"),
    "avoid unnecessary numeric restatement": ("prefer explanations and uncertainties that do not restate numeric or "
                                              "comparator language",
                                              "omit it rather than inventing, repairing, counting, or paraphrasing it"),
}


@pytest.mark.parametrize("requirement", CONTRACT)
def test_the_grounding_section_states_each_requirement(requirement):
    for phrase in CONTRACT[requirement]:
        assert phrase in GROUNDING, phrase


def test_explanations_and_uncertainties_get_qualitative_guidance_without_forcing_entries():
    assert "answer the criterion in plain words" in PROMPT
    assert '"the buyer describes a prospective condition."' in PROMPT
    assert "counts criteria the interaction never counts" in PROMPT
    assert '"it is not established whether the condition will occur."' in PROMPT
    assert "leave the list empty when nothing is uncertain" in PROMPT


def test_the_prompt_names_no_validator_internals():
    for internal in ("validator", "validation", "failure code", "grounding code", "_unsupported", "_changed",
                     "_unresolved", "greater_than", "at_least", "less_than", "at_most", "comparator_",
                     "number_unsupported"):
        assert internal not in PROMPT, internal
    assert not re.search(r"\b(bypass|work around|avoid (being|getting) rejected)\b", PROMPT)


def test_the_prompt_introduces_no_numbers_of_its_own_into_the_guidance():
    """The guidance itself must not seed the counts it forbids (the v1 text's 'four criteria' line is unchanged)."""
    v2_additions = _normalized(SYSTEM_PROMPT_V2.replace(SYSTEM_PROMPT_V1.split("Return only the structured result:")[0], ""))
    assert not re.search(r"\d", v2_additions)
    assert not re.search(r"\b(one|two|three|four|five)\b", GROUNDING)


# --- E2 failure classes: each is now explicitly instructed against (not a claim about the E2 outputs) -------

E2_CLASSES = {
    "uncertainty comparator change (C01, C02)": ("every uncertainties entry", "weaken, strengthen, or otherwise change its comparator",
                                                  "do not introduce, drop,"),
    "uncertainty unresolved-comparator paraphrase (C04, C09)": ("every uncertainties entry", "past, beyond, within, or over",
                                                                "do not paraphrase or drop it"),
    "explanation unsupported count or number (C05, C07, C16, C17)": ("every criterion_hypotheses explanation",
                                                                     "do not introduce any unsupported number",
                                                                     "do not count or enumerate criteria"),
    "explanation comparator change (C16)": ("every criterion_hypotheses explanation",
                                            "weaken, strengthen, or otherwise change its comparator",
                                            "do not introduce, drop,"),
    "explanation unresolved-comparator paraphrase (C09)": ("every criterion_hypotheses explanation",
                                                           "repeat that word exactly"),
    "normalization unresolved-comparator paraphrase (C04)": ("normalized_condition", "normalized_evaluation_link",
                                                             "repeat that word exactly", "do not paraphrase or drop it"),
}


@pytest.mark.parametrize("failure_class", E2_CLASSES)
def test_each_e2_failure_class_is_now_explicitly_instructed_against(failure_class):
    for phrase in E2_CLASSES[failure_class]:
        assert phrase in GROUNDING, phrase
    assert "grounding rules for all model-authored free text" not in _normalized(SYSTEM_PROMPT_V1)  # absent in v1


def test_the_service_sends_prompt_v2():
    source = (REPO_ROOT / "baec_app" / "ai" / "service.py").read_text(encoding="utf-8")
    assert "system=SYSTEM_PROMPT," in source and "SYSTEM_PROMPT_V1" not in source
