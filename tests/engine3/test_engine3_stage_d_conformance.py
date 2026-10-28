"""Stage D: deterministic Engine 3 core must reproduce all 64 locked Stage B expectations."""

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from baec_app.engine3.validator import validate_case
from tests.engine3.conformance_adapter import FIXTURE_PATH, ORACLE_PATH, build_case, load_inputs, load_oracle

INPUTS = load_inputs()
ORACLE = load_oracle()
FIXTURES = {x["fixture_id"]: x["input"] for x in INPUTS["cases"]}
EXPECTED = {x["case_id"]: x for x in ORACLE["cases"]}
IDS = [x["case_id"] for x in ORACLE["cases"]]


def test_exactly_64_locked_cases_are_mapped():
    assert len(IDS) == 64
    assert len(set(IDS)) == 64
    assert set(FIXTURES) == set(IDS)
    assert ORACLE["status"] == "LOCKED"


@pytest.mark.parametrize("case_id", IDS)
def test_locked_case_conformance(case_id):
    result = validate_case(build_case(FIXTURES[case_id]))
    expected = EXPECTED[case_id]
    assert result.allowed is expected["expected_pass"]
    assert result.primary_reason_code == expected["stage_c_primary_reason_code"]


def test_conformance_primary_reason_distribution_is_stable():
    derived = Counter()
    for case_id in IDS:
        result = validate_case(build_case(FIXTURES[case_id]))
        derived[result.primary_reason_code or "PASS"] += 1
    expected = Counter((EXPECTED[c]["stage_c_primary_reason_code"] or "PASS") for c in IDS)
    assert derived == expected
    assert sum(derived.values()) == 64


def test_stage_d_fixture_has_no_expected_output_fields():
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "expected_verdict" not in text
    assert "expected_pass" not in text
    assert "primary_reason_code" not in text


def test_fixture_is_bound_to_locked_stage_c_design():
    repo_root = Path(__file__).resolve().parents[2]
    design = repo_root / "docs" / "engine3" / "ENGINE3_STAGE_C_DOMAIN_VALIDATOR_DESIGN.md"
    assert hashlib.sha256(design.read_bytes()).hexdigest() == INPUTS["stage_c_locked_sha256"]
