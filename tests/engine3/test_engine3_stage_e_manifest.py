"""Stage E lock manifest: internally consistent, complete, and honest about its limited claim."""

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "docs" / "engine3" / "ENGINE3_STAGE_E_IMPLEMENTATION_MANIFEST.json"
STAGE_D_MANIFEST_PATH = REPO / "docs" / "engine3" / "ENGINE3_STAGE_D_IMPLEMENTATION_MANIFEST.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sha256(rel: str) -> str:
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def test_every_stage_e_file_hash_matches_the_manifest():
    for rel, expected in MANIFEST["stage_e_files"].items():
        assert _sha256(rel) == expected, rel


def test_manifest_covers_every_stage_e_file_and_not_itself():
    found = {
        p.relative_to(REPO).as_posix()
        for pattern in (
            "baec_app/engine3/proposal_contract.py",
            "baec_app/engine3/offline_evaluation.py",
            "docs/engine3/ENGINE3_STAGE_E_*.md",
            "tests/engine3/fixtures/ENGINE3_STAGE_E_*.json",
            "tests/engine3/stage_e_adapter.py",
            "tests/engine3/test_engine3_stage_e_*.py",
            "scripts/verify_engine3_stage_e.py",
        )
        for p in REPO.glob(pattern)
    }
    assert set(MANIFEST["stage_e_files"]) == found
    assert MANIFEST_PATH.relative_to(REPO).as_posix() not in MANIFEST["stage_e_files"]


def test_stage_d_base_is_bound_and_unchanged():
    assert MANIFEST["stage_d_base_commit"] == "45a72d80f643240137d2d10c8b772e52fb664c43"
    assert MANIFEST["stage_d_manifest_sha256"] == hashlib.sha256(STAGE_D_MANIFEST_PATH.read_bytes()).hexdigest()
    stage_d = json.loads(STAGE_D_MANIFEST_PATH.read_text(encoding="utf-8"))
    for group in ("locked_artifact_hashes", "production_files", "test_files"):
        for rel, expected in stage_d[group].items():
            assert _sha256(rel) == expected, rel


def test_manifest_records_the_limited_claim_without_overclaiming():
    assert MANIFEST["status"] == "LOCKED_LIMITED_CLAIM"
    assert MANIFEST["lock_date"] == "2026-10-14"
    assert MANIFEST["synthetic_data_only"] is True
    assert MANIFEST["prerecorded_proposals_only"] is True
    for flag in ("live_model_executed", "live_model_authorized", "outreach_authorized",
                 "semantic_annotation_authority_resolved"):
        assert MANIFEST[flag] is False, flag
    sem = {x["id"]: x for x in MANIFEST["limitations"]}["E3-E-SEM-001"]
    assert sem["name"] == "MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE"
    assert sem["kind"] == "DISCOVERED_ARCHITECTURAL_LIMITATION"
    assert sem["resolved"] is False
    for rel in ("docs/engine3/ENGINE3_STAGE_E_AI_PROPOSAL_CONTRACT.md",
                "docs/engine3/ENGINE3_STAGE_E_OFFLINE_EVALUATION_REPORT.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert MANIFEST["governing_claim"] in text, rel
        assert "E3-E-SEM-001" in text and "MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE" in text, rel
