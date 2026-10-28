"""Verify BAEC Engine 3 Stage D deterministic core and locked artifact bindings."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCKED = {'docs/engine3/ENGINE3_REENGAGEMENT_PREPARATION_DOMAIN_CONTRACT.md': '890870e0af12bbbc35d26ef825544f12f8589198fddf6507d680036d21212b3a', 'docs/engine3/corpus/ENGINE3_STAGE_B_SYNTHETIC_CORPUS.md': '6f64b037b25165cc2512d9d2342954f8ba316fc2176f594c8a8542dfabf574d2', 'docs/engine3/corpus/ENGINE3_STAGE_B_SYNTHETIC_CORPUS.json': '362dc5df456acd03246cdf4a44b1b16306d9901ad82774e33f52e2cb22ef087a', 'docs/engine3/ENGINE3_STAGE_C_DOMAIN_VALIDATOR_DESIGN.md': '322d8dc7f8b1c86013631a994fb05adf232bda33817c4c51148d14930f0cdbdc', 'docs/engine3/ENGINE3_STAGE_C_VALIDATOR_SPEC.json': 'f6023ed8592a7ccedd4a29e10c613b10111302145b1d0e56eb4db4baf5654aa0', 'docs/engine3/ENGINE3_STAGE_C_CORPUS_VALIDATION_ORACLE.json': '4ec28457f27a11e60c699a6dd467425451339ee2bd6a2ea6f2f9e1a2004dd800', 'tests/engine3/fixtures/ENGINE3_STAGE_D_CONFORMANCE_INPUTS.json': '6bb38796b59ee510968646eaeb81d498155617bcb5c7cc2882a1d3c35ffa8cf8', 'tests/engine3/fixtures/ENGINE3_STAGE_C_CORPUS_VALIDATION_ORACLE_LOCKED.json': '4ec28457f27a11e60c699a6dd467425451339ee2bd6a2ea6f2f9e1a2004dd800'}

def main() -> int:
    failures = []
    for rel, expected in LOCKED.items():
        path = REPO / rel
        if not path.exists():
            failures.append(f"missing: {rel}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(f"hash mismatch: {rel}: {actual}")
    if failures:
        for failure in failures:
            print("FAIL ", failure)
        return 1
    print(f"PASS  locked artifact hashes: {len(LOCKED)}")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/engine3"], cwd=REPO)
    if result.returncode:
        return result.returncode
    print("PASS  Engine 3 Stage D deterministic suite")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
