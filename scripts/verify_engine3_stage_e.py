"""Verify BAEC Engine 3 Stage E (locked with a limited claim).

Offline only: no network call and no API key. Checks Stage E file hashes, confirms the
Stage D locked artifacts are unchanged, runs the Stage E tests, and prints the
unresolved semantic-annotation limitation.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs" / "engine3" / "ENGINE3_STAGE_E_IMPLEMENTATION_MANIFEST.json"
STAGE_D_MANIFEST = REPO / "docs" / "engine3" / "ENGINE3_STAGE_D_IMPLEMENTATION_MANIFEST.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(expected: dict[str, str]) -> list[str]:
    failures = []
    for rel, digest in expected.items():
        path = REPO / rel
        if not path.exists():
            failures.append(f"missing: {rel}")
        elif _sha256(path) != digest:
            failures.append(f"hash mismatch: {rel}")
    return failures


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(f"Stage E status: {manifest['status']} (manifest sha256 {_sha256(MANIFEST)})")

    failures = _check(manifest["stage_e_files"])
    if failures:
        for failure in failures:
            print("FAIL ", failure)
        return 1
    print(f"PASS  Stage E file hashes: {len(manifest['stage_e_files'])}")

    if _sha256(STAGE_D_MANIFEST) != manifest["stage_d_manifest_sha256"]:
        print("FAIL  Stage D manifest changed")
        return 1
    stage_d = json.loads(STAGE_D_MANIFEST.read_text(encoding="utf-8"))
    locked = {}
    for group in ("locked_artifact_hashes", "production_files", "test_files"):
        locked.update(stage_d[group])
    failures = _check(locked)
    if failures:
        for failure in failures:
            print("FAIL ", failure)
        return 1
    print(f"PASS  Stage D locked artifacts, production, and tests unchanged: {len(locked)}")

    tests = sorted(rel for rel in manifest["stage_e_files"] if Path(rel).name.startswith("test_"))
    sys.stdout.flush()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=REPO)
    if result.returncode:
        print("FAIL  Stage E tests")
        return result.returncode
    print("PASS  Stage E tests")

    for limitation in manifest["limitations"]:
        state = "RESOLVED" if limitation["resolved"] else "NOT RESOLVED"
        print(f"LIMIT {limitation['id']} {limitation['name']} [{state}]: {limitation['meaning']}")
    print("NOTE  Stage E is a limited structural/offline claim. It does not establish deterministic semantic")
    print("      validation of free-form model text, live-model readiness, or outreach authorization.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
