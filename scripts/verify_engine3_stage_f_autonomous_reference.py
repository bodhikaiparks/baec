"""Verify the BAEC Engine 3 Stage F-AR autonomous-reference track (DESIGNED_OFFLINE_NOT_EXECUTED).

Offline only: no model, no network call, no key. Confirms the locked Stage F material the track reads is
unchanged, checks the Stage F-AR manifest hashes and coverage, the no-execution record, the governing claim
and reference labels, that the human track is unchanged (O-3 PRE_RUN_REQUIRED, human pilot readiness false), the
reserved-term, exposure, and qualification-governance statements, that autonomous readiness is false with exactly
the AR-PRE blockers and never O-3, then runs the Stage F-AR tests.
Separate from, and does not replace, the Stage F design, harness, O-5, operational-guide, and calibration
verifiers.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "engine3"
MANIFEST = DOCS / "ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_MANIFEST.json"
PROTOCOL = DOCS / "ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_PROTOCOL.md"
CLAIM_BOUNDARY = DOCS / "ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_CLAIM_BOUNDARY.md"
HARNESS_MANIFEST = DOCS / "ENGINE3_STAGE_F_HARNESS_MANIFEST.json"
PACKAGE = REPO / "experiments" / "engine3_stage_f_ar"

STATUS = "DESIGNED_OFFLINE_NOT_EXECUTED"
GOVERNING_CLAIM = (
    "Stage F autonomous-reference execution measures the generating model's semantic annotations and Stage D "
    "outcomes relative to a blinded, independently generated autonomous reviewer consensus. Autonomous consensus "
    "annotations are not human reference labels, human ground truth, or evidence of human semantic reliability."
)
HUMAN_PILOT_STATEMENT = (
    "Stage F-AR does not execute, constitute, substitute for, or satisfy the Stage F human-reference pilot. The term "
    "pilot remains reserved for the locked human-reference track. O-3 remains PRE_RUN_REQUIRED and human pilot_ready "
    "remains false."
)
EXPOSURE_STATEMENT = (
    "All Stage F-AR annotations, disagreements, adjudications and comparison results are labelled evaluation-derived "
    "material for O-3 exposure purposes."
)
REFERENCE_LABELS = ["AUTONOMOUS_CONSENSUS_REFERENCE", "AUTONOMOUS_ADJUDICATED_REFERENCE"]
NO_EXECUTION = {
    "live_model_executed": False,
    "live_model_authorized": False,
    "api_key_used": False,
    "network_calls": 0,
    "ar_44_initial_autonomous_execution_batch_executed": False,
    "ar_full_run_executed": False,
    "qualification_executed": False,
    "autonomous_results_exist": False,
    "outreach_authorized": False,
}
HUMAN_TRACK = {"O-3": "PRE_RUN_REQUIRED", "pilot_ready": False}
AR_BLOCKERS = {f"AR-PRE-{n}": "PRE_RUN_REQUIRED" for n in range(1, 7)}
AR_GLOBS = (
    "experiments/engine3_stage_f_ar/*.py",
    "tests/engine3/stage_f_ar_adapter.py",
    "tests/engine3/test_engine3_autonomous_reference_*.py",
    "scripts/verify_engine3_stage_f_autonomous_reference.py",
    "docs/engine3/ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_*.md",
)
_NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:anthropic|openai|mcp|requests|httpx|aiohttp|urllib|http\.client|socket|smtplib)\b",
    re.MULTILINE,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_hashes(files: dict, label: str, failures: list[str]) -> None:
    for rel, expected in files.items():
        path = REPO / rel
        if not path.exists():
            failures.append(f"{label}: missing {rel}")
        elif _sha256(path) != expected:
            failures.append(f"{label}: hash mismatch {rel}")


def _check_manifest(manifest: dict, failures: list[str]) -> None:
    if manifest.get("status") != STATUS or manifest.get("autonomous_reference_ready") is not False:
        failures.append(f"manifest: status must be {STATUS} with autonomous_reference_ready false")
    for key, value in NO_EXECUTION.items():
        if manifest.get("execution_record", {}).get(key) != value:
            failures.append(f"manifest: execution_record.{key} must be {value!r}")
    if manifest.get("governing_claim") != GOVERNING_CLAIM:
        failures.append("manifest: governing claim differs from the locked text")
    if manifest.get("reference_labels") != REFERENCE_LABELS:
        failures.append(f"manifest: reference labels must be exactly {REFERENCE_LABELS}")
    if manifest.get("human_track") != {**HUMAN_TRACK, "unchanged": True}:
        failures.append("manifest: human track must record O-3 PRE_RUN_REQUIRED and pilot_ready false, unchanged")
    if manifest.get("autonomous_blockers") != AR_BLOCKERS:
        failures.append(f"manifest: autonomous blockers must be exactly {AR_BLOCKERS}")
    if manifest.get("human_pilot_statement") != HUMAN_PILOT_STATEMENT:
        failures.append("manifest: the human pilot statement is missing or altered")
    if manifest.get("o3_exposure", {}).get("statement") != EXPOSURE_STATEMENT:
        failures.append("manifest: the O-3 exposure statement is missing or altered")
    governance = manifest.get("qualification_governance", {})
    if governance.get("status") != "NOT_FROZEN" or governance.get("thresholds") is not None:
        failures.append("manifest: qualification governance must be NOT_FROZEN with no thresholds (AR-PRE-4 open)")
    if manifest.get("reviewer_model_ids_selected") is not False:
        failures.append("manifest: reviewer model ids must not be selected")
    _check_hashes(manifest.get("locked_inputs_sha256", {}), "locked input", failures)
    files = manifest.get("ar_files_sha256", {})
    _check_hashes(files, "Stage F-AR file", failures)
    found = {p.relative_to(REPO).as_posix() for pattern in AR_GLOBS for p in REPO.glob(pattern)}
    if set(files) != found:
        failures.append(f"manifest coverage: unlisted={sorted(found - set(files))} missing={sorted(set(files) - found)}")


def _check_documents(failures: list[str]) -> None:
    for path in (PROTOCOL, CLAIM_BOUNDARY):
        text = path.read_text(encoding="utf-8")
        flat = " ".join(line.removeprefix("> ") for line in text.splitlines())
        for name, statement in (("governing claim", GOVERNING_CLAIM), ("human pilot statement", HUMAN_PILOT_STATEMENT),
                                ("O-3 exposure statement", EXPOSURE_STATEMENT)):
            if statement not in flat:
                failures.append(f"{path.name}: {name} missing or altered")
        if STATUS not in text:
            failures.append(f"{path.name}: status {STATUS} missing")


def _check_human_track(failures: list[str]) -> None:
    human = json.loads(HARNESS_MANIFEST.read_text(encoding="utf-8")).get("pre_run_state_current", {})
    if human.get("status", {}).get("O-3") != "PRE_RUN_REQUIRED" or human.get("pilot_readiness") is not False:
        failures.append("human track: O-3 must be PRE_RUN_REQUIRED and pilot readiness false")


def _check_offline(failures: list[str]) -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        if _NETWORK_IMPORT.search(path.read_text(encoding="utf-8")):
            failures.append(f"{path.name}: model SDK or network import")


def main() -> int:
    failures: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    _check_manifest(manifest, failures)
    _check_documents(failures)
    _check_human_track(failures)
    _check_offline(failures)
    tests = sorted(p.relative_to(REPO).as_posix() for p in REPO.glob("tests/engine3/test_engine3_autonomous_reference_*.py"))
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=REPO, capture_output=True, text=True)
    summary = (completed.stdout.strip().splitlines() or [""])[-1]
    if completed.returncode != 0:
        failures.append(f"Stage F-AR tests failed: {summary}")
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"Stage F-AR tests: {summary}")
    print("PASS" if not failures else f"{len(failures)} failure(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
