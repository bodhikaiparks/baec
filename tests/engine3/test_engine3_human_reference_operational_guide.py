"""E3F-ISSUE-HR-001: the decontaminated operational guide and its offline audit."""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "verify_engine3_stage_f_operational_guide", REPO / "scripts" / "verify_engine3_stage_f_operational_guide.py")
guide_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guide_audit)
o5 = guide_audit.o5


@pytest.fixture(scope="module")
def corpus():
    c = o5.evaluation_corpus()
    return c, guide_audit.corpus_ngram_set(c)


def test_verifier_passes():
    assert guide_audit.main() == 0


@pytest.mark.parametrize("text", [
    "The July renewal increase came in at 9.00%.",
    "Has a review of the contract actually started, or is it still only a possibility?",
    "Now that you're moving away from Cobalt, which providers are you considering?",
])
def test_audit_detects_the_contaminated_protocol_examples(corpus, text):
    result = guide_audit.audit_text(text, *corpus)
    assert result["exact"]


def test_every_worked_example_in_the_guide_is_clean(corpus):
    for example in guide_audit.worked_examples():
        assert not any(guide_audit.audit_text(example, *corpus).values()), example


def test_guide_contains_no_banned_evaluation_term():
    assert o5.banned_terms(guide_audit.GUIDE.read_text(encoding="utf-8")) == []


def test_packet_guide_is_clean_after_harness_amendment_002(corpus):
    assert guide_audit.packet_guide_findings(guide_audit.ANNOTATION_GUIDE, *corpus) == {}


def test_packet_guide_detector_still_catches_the_pre_amendment_text(corpus):
    pre = {"specificity_convention": {"90": "exact or near-exact factual detail exposed, including formatting-only "
                                            "variants such as 9.00% -> 9%"},
           "reference_use_convention": {"semantically_equivalent_rewording": "coded PARAPHRASE (example more than 8% -> above 8%)"}}
    assert set(guide_audit.packet_guide_findings(pre, *corpus)) == {
        ("specificity_convention", "90"), ("reference_use_convention", "semantically_equivalent_rewording")}


def test_operational_guide_and_packet_instructions_use_the_same_example_values():
    guide = guide_audit.GUIDE.read_text(encoding="utf-8")
    convention = guide_audit.ANNOTATION_GUIDE["reference_use_convention"]
    # Formatting-only variant: DIRECT, flagged, specificity 90, in both.
    assert "$46.00 -> $46" in convention["formatting_only_numeric_variant"]
    assert "$46.00 -> $46" in guide_audit.ANNOTATION_GUIDE["specificity_convention"]["90"]
    assert 'Example: "$46" for `$46.00` changes only numeric formatting, not wording.' in guide
    assert "Formatting-only variants count here, including $46.00 → $46." in guide
    # Semantically equivalent rewording: PARAPHRASE, in both.
    assert "over $40 -> higher than $40" in convention["semantically_equivalent_rewording"]
    assert '"higher than $40" for `over $40` is **paraphrase**;' in guide
    # The packet guide's rule text is the guide's rule text (sections 7.4 and 7.5).
    assert "the integers are not a score of any kind" in guide
    assert convention["PARAPHRASE"] == "when any externally used cited content is semantically reworded"
    assert "When **any** externally used cited content is semantically reworded" in guide


def test_resolution_status_parser_reads_only_table_rows():
    text = "| `E3F-ISSUE-HR-001` | `RESOLVED_DECONTAMINATED_OPERATIONAL_PATH` |\nE3F-ISSUE-HR-001 OPEN"
    assert guide_audit._status(text, "E3F-ISSUE-HR-001") == "RESOLVED_DECONTAMINATED_OPERATIONAL_PATH"
    assert guide_audit._status("no table", "E3F-ISSUE-HR-001") is None


def test_substitution_must_match_exactly_once():
    assert guide_audit.apply_substitutions("rule text example", (("example", "fictional"),)) == "rule text fictional"
    assert guide_audit.apply_substitutions("rule text", (("example", "fictional"),)) is None
    assert guide_audit.apply_substitutions("example and example", (("example", "fictional"),)) is None


def test_every_substitution_leaves_the_rule_text_outside_the_example_unchanged():
    for line, pairs in guide_audit.SUBSTITUTIONS.items():
        result = guide_audit.apply_substitutions(line, pairs)
        assert result is not None, line
        rest_before, rest_after = line, result
        for old, new in pairs:
            rest_before = rest_before.replace(old, "\x00")
            rest_after = rest_after.replace(new, "\x00")
        assert rest_before == rest_after, line
