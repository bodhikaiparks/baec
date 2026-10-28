"""Stage C conformance: the domain core independently reproduces all 64 locked Stage B cases.

The adapter derives each result from corpus inputs only. The oracle fields are
read here, after classification, for comparison.
"""

import hashlib
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from baec_app.engine2.domain import CorrespondenceDimension, CorrespondenceOutcome, DimensionFinding, SufficiencyFinding
from baec_app.engine2.errors import ReviewInconsistentError
from tests.engine2.corpus_adapter import CORPUS_JSON, CORPUS_MD, REPO, STAGE_A, findings_from, load_corpus, run_case

CORPUS = load_corpus()
CASES = CORPUS["cases"]
IDS = [c["case_id"] for c in CASES]
LOCKED_SHA256 = {
    CORPUS_MD: "177848a45ccfb7ad0716c01f7f350bdb813c90e6b0265eb79544b0007a93d801",
    CORPUS_JSON: "fb96e3d17ba82b3b8877d1c1f7499919c549af05eed8902a754ad31c76ff7bf3",
}
PRODUCTION = REPO / "baec_app" / "engine2"


def test_locked_stage_b_artifacts_are_unchanged():
    for path, digest in LOCKED_SHA256.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path.name
    assert CORPUS["status"] == "Stage B LOCKED"


def test_all_64_locked_cases_are_present_in_order():
    assert IDS == [f"HBR-CORR-{i:03d}" for i in range(1, 65)]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_case_conforms_to_locked_oracle(case):
    run = run_case(CORPUS, case)
    expected = case["expected_correspondence_outcome"]
    admitted = {o["packet_id"] for o in case["raw_observations_expected"]}

    if expected is None:
        assert run.outcome is None and run.review is None
        disposition = case["expected_processing_disposition"]
        if disposition.startswith("Monitoring Plan not activated"):
            assert run.activation_error is not None and run.ledger is None and not run.observations
        else:
            assert disposition == "reject before Observation creation"
            assert run.activation_error is None and not run.observations
            assert {r.item_id for r in run.rejected} == {p["packet_id"] for p in case["source_packets"]}
        return

    # Source authorization: exactly the corpus's admitted packets became Observations.
    assert run.activation_error is None
    assert {o.exact_evidence_content for o in run.observations} == {
        p["content"] for p in case["source_packets"] if p["packet_id"] in admitted}
    assert {r.item_id for r in run.rejected} == {n["packet_id"] for n in case["packets_not_admitted"]}

    # Observations are raw and preserved; supersession is relational, never an edit.
    by_id = {o.observation_id: o for o in run.observations}
    for entry in case["raw_observations_expected"]:
        obs = by_id[entry["observation_id"]]
        assert obs.exact_evidence_content == entry["exact_evidence_content"]
        assert obs.content_sha256 == entry["integrity"]["content_sha256"]
        assert obs.evidence_event_id == entry["evidence_event_id"]
        assert (obs.observation_id in run.ledger.usable_observation_ids) is entry["usable_for_current_findings"]

    # Derived Measurements recompute exactly and lose support when an input is superseded.
    for dm in case["derived_measurements_expected"]:
        m = next(x for x in run.measurements if x.measurement_id == dm["measurement_id"])
        assert m.output_value == Decimal(dm["output"]["value"]) and m.output_unit == dm["output"]["unit"]
        assert m.transformation.value == dm["transformation_id"] and m.transformation_version == dm["transformation_version"]
        assert m.formula == dm["formula"] and dm["rounding_rule"].startswith(m.rounding_rule)
        assert m.source_observation_ids == set(dm["source_observation_ids"])
        assert (m.measurement_id in run.ledger.usable_measurement_ids) is dm["supports_findings"]

    review = run.review
    assert review.baec_revalidation_required is case["expected_baec_revalidation_required"]
    assert review.sufficiency is SufficiencyFinding(case["expected_human_sufficiency_finding"])
    assert run.outcome is CorrespondenceOutcome(expected)
    if run.outcome is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE:
        assert review.sufficiency is SufficiencyFinding.YES and not review.baec_revalidation_required
        assert all(f is DimensionFinding.SUPPORTED for f in review.finding_map.values())

    # Refused review entries in the corpus are refused by the core.
    for refused in case["refused_review_entries"]:
        bad = dict(run.review_kwargs, findings=findings_from(case, {refused["dimension"]: DimensionFinding(refused["attempted_finding"])}))
        with pytest.raises(ReviewInconsistentError):
            type(review)(**bad)


# --- Stage B corpus-only conventions (stricter than the production domain rules) ---

CLASSIFIED = [c for c in CASES if c["expected_correspondence_outcome"] is not None]


@pytest.mark.parametrize("case", CLASSIFIED, ids=[c["case_id"] for c in CLASSIFIED])
def test_stage_b_fixture_conventions(case):
    """Locked Stage B conventions. These are fixture rules, deliberately not production invariants."""
    findings = {d: f["finding"] for d, f in case["expected_dimension_findings"].items()}
    checks = case["cross_cutting_checks"]
    unresolved = [d for d, f in findings.items() if f == "UNRESOLVED"]
    # Missing evidence lists exactly the UNRESOLVED dimensions (production only requires the reverse direction).
    assert checks["Missing evidence"]["affected_dimensions"] == unresolved
    # Harbor numeric structure: a decided threshold needs comparator, magnitude, and unit SUPPORTED.
    if findings["Threshold match"] in ("SUPPORTED", "CONTRADICTED"):
        assert all(findings[d] == "SUPPORTED" for d in ("Comparator match", "Numeric magnitude", "Unit match"))
    # Corpus conflicts are either unresolved or resolved by a Monitoring Plan rule.
    for d in checks["Contradictory evidence"]["affected_dimensions"]:
        evidence = case["expected_dimension_findings"][d]["evidence"]
        assert findings[d] == "UNRESOLVED" or any(e.startswith("PLAN-RULE-") for e in evidence)
    # Every observation hash validates under the approved exact UTF-8 rule.
    for o in case["raw_observations_expected"]:
        assert o["integrity"]["content_sha256"] == hashlib.sha256(o["exact_evidence_content"].encode("utf-8")).hexdigest()


def test_every_stage_b_content_hash_matches_the_utf8_rule():
    entries = [o for c in CASES for o in c["raw_observations_expected"]]
    assert entries and all(
        o["integrity"]["content_sha256"] == hashlib.sha256(o["exact_evidence_content"].encode("utf-8")).hexdigest()
        for o in entries)


def test_adapter_review_time_is_fixed_scaffolding_after_every_observation():
    from tests.engine2.corpus_adapter import REVIEWED_AT
    for case in CLASSIFIED:
        run = run_case(CORPUS, case)
        assert run.review.reviewed_at == REVIEWED_AT >= max(o.observed_at for o in run.observations)


def test_case_057_is_blocked_by_a_missing_engine1_reading_not_another_status():
    from tests.engine2.corpus_adapter import currentness_for
    case = next(c for c in CASES if c["case_id"] == "HBR-CORR-057")
    assert currentness_for(CORPUS, case) is None
    run = run_case(CORPUS, case)
    assert "missing" in str(run.activation_error) and run.ledger is None and run.outcome is None
    others = [currentness_for(CORPUS, c) for c in CASES if c["case_id"] != "HBR-CORR-057"]
    assert all(r is not None and r.staleness_status.value == "CURRENT" for r in others)


def test_case_037_old_but_authoritative_evidence_supports_timing_contradiction():
    case = next(c for c in CASES if c["case_id"] == "HBR-CORR-037")
    run = run_case(CORPUS, case)
    assert run.ledger.usable_observation_ids == {"OBS-037-01"}  # old, not superseded: still usable
    timing = run.review.finding_map
    assert timing[CorrespondenceDimension.TIMING_MATCH] is DimensionFinding.CONTRADICTED
    assert run.outcome is CorrespondenceOutcome.NO_CORRESPONDENCE


def test_outcome_distribution_matches_the_locked_corpus():
    derived = Counter()
    for case in CASES:
        outcome = run_case(CORPUS, case).outcome
        derived[outcome.value if outcome else None] += 1
    assert derived == Counter(c["expected_correspondence_outcome"] for c in CASES)
    assert derived == {"NO_CORRESPONDENCE": 20, "POSSIBLE_CORRESPONDENCE": 32, "HUMAN_VERIFIED_CORRESPONDENCE": 9, None: 3}


def test_duplicate_copies_resolve_to_one_evidence_event():
    case = next(c for c in CASES if c["case_id"] == "HBR-CORR-042")
    run = run_case(CORPUS, case)
    assert len(run.observations) == 4
    assert run.ledger.evidence_events([o.observation_id for o in run.observations]) == {"EVT-042-01"}


def test_stage_a_contract_is_the_dimension_authority():
    text = STAGE_A.read_text(encoding="utf-8")
    table = text.split("### 8.2")[1].split("### 8.3")[0]
    rows = re.findall(r"^\| ([A-Z][a-z][^|]+?) \| (?:Is|Does|What)", table, re.M)
    assert rows[:11] == CORPUS["vocabulary"]["required_dimensions"]
    assert rows[11:] == CORPUS["vocabulary"]["cross_cutting_checks"]


# --- static audit of production code --------------------------------------------

def _production_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(PRODUCTION.glob("*.py")))


@pytest.mark.parametrize(
    "pattern",
    [r"HBR-CORR", r"expected_", r"case_id", r"\beval\(", r"\bexec\(", r"\bfloat\(", r"import (requests|urllib|socket|http)",
     r"anthropic", r"\bAccountState\b", r"score", r"probab", r"confidence", r"permission_to_contact",
     r"VERIFIED_CORRESPONDENCE(?<!HUMAN_VERIFIED_CORRESPONDENCE)", r"LIKELY_CORRESPONDENCE", r"\bdatetime\.now\b", r"\btime\.time\b"],
)
def test_production_engine2_code_has_no_forbidden_construct(pattern):
    assert not re.search(pattern, _production_source(), re.I), pattern


def test_production_engine2_imports_only_engine1_vocabularies():
    imports = set(re.findall(r"^from (baec_app\.[\w.]+) import", _production_source(), re.M))
    assert {i for i in imports if not i.startswith("baec_app.engine2")} == {"baec_app.domain.enums"}


def test_engine1_modules_do_not_import_engine2():
    for path in (REPO / "baec_app").rglob("*.py"):
        if PRODUCTION not in path.parents:
            assert "engine2" not in path.read_text(encoding="utf-8"), path
