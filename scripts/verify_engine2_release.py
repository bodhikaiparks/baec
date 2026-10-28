"""Verify the BAEC Engine 2 release candidate offline.

Usage, from the repository root:

    python3 scripts/verify_engine2_release.py            # identity, integrity, schemas, demo, focused suites
    python3 scripts/verify_engine2_release.py --full     # also the full repository suite

Expected values come from docs/engine2/ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json. Every check is local: git is
read, never fetched; no credential is read; no network or model is used; nothing is written except pytest's own
temporary files and its git-ignored cache. The exit status is 0 only if every check passes. Passing is software
verification on synthetic data, not evidence for the BAEC theory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

RECORD = REPOSITORY_ROOT / "docs" / "engine2" / "ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json"
LIVE_GATES = ("BAEC_LIVE_E2_STAGE_H", "BAEC_LIVE_E2_RESEARCH_DIR", "BAEC_LIVE_CLAUDE")
SUITES = {
    "Stage K handoff": ["tests/engine2/test_engine2_engine1_handoff.py"],
    "Stage I application flow": ["tests/engine2/test_engine2_review_application.py"],
    "Stage J operator workflow and boundaries": ["tests/engine2/test_engine2_operator_ui.py"],
    "Stage L release demonstration": ["tests/engine2/test_engine2_release.py"],
    "64-case distributions": ["tests/engine2", "-k", "distribution"],
    "Engine 2 static audits": ["tests/engine2/test_engine2_corpus_conformance.py",
                               "tests/engine2/test_engine2_plan_version_identity.py",
                               "tests/engine2/test_engine2_ai_boundary.py", "tests/engine2/test_engine2_ai_persistence.py"],
    "all Engine 2 tests": ["tests/engine2"],
    "Engine 1 boundary audits": ["tests/test_application_boundary.py", "tests/test_phase7c_boundaries.py",
                                 "tests/test_phase7e_boundaries.py", "tests/test_ai_boundary.py",
                                 "tests/test_mcp_write_boundary.py"],
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=REPOSITORY_ROOT, capture_output=True, text=True,
                          check=True).stdout.removesuffix("\n")


def identity_checks(expected: dict) -> list[tuple[str, bool, str]]:
    """Checks that need no test run. Each result is (name, passed, detail)."""
    from baec_app.data.database import SCHEMA_VERSION as ENGINE1_SCHEMA_VERSION
    from baec_app.engine2 import ai_persistence, persistence

    results = []
    head, status = _git("rev-parse", "HEAD"), _git("status", "--porcelain")
    results.append(("working tree clean", status == "", f"HEAD {head}"))
    gates = [name for name in LIVE_GATES if name in os.environ]
    results.append(("no live gate variable set", not gates, ", ".join(gates) or "none set"))
    for name, relative in expected["integrity_files"].items():
        actual = _sha256_file(REPOSITORY_ROOT / relative["path"])
        results.append((f"{name} hash", actual == relative["sha256"], actual))
    results.append(("Engine 1 schema version", ENGINE1_SCHEMA_VERSION == expected["schemas"]["engine1_schema_version"],
                    str(ENGINE1_SCHEMA_VERSION)))
    authoritative = persistence.open_engine2_database(":memory:")
    ai = ai_persistence.open_ai_audit_database(":memory:")
    try:
        for label, module, connection, verify in (("BAEC_ENGINE_2", persistence, authoritative, persistence.verify_database),
                                                  ("BAEC_ENGINE_2_AI", ai_persistence, ai, ai_persistence.verify_ai_database)):
            want = expected["schemas"][label]
            tables = len(verify(connection))
            found = {"engine": module.ENGINE, "schema_version": module.SCHEMA_VERSION, "tables": tables}
            results.append((f"{label} schema", found == want, json.dumps(found, sort_keys=True)))
    finally:
        authoritative.close()
        ai.close()
    dev = REPOSITORY_ROOT / expected["engine1_dev_database"]["path"]
    if dev.exists():
        actual = _sha256_file(dev)
        results.append(("Engine 1 dev database hash", actual == expected["engine1_dev_database"]["sha256"], actual))
    else:
        results.append(("Engine 1 dev database hash", True, "not present in this checkout (git-ignored, generated)"))
    return results


def demo_checks(expected: dict) -> list[tuple[str, bool, str]]:
    from scripts.engine2_release_demo import run_release_demo

    first, second = run_release_demo(), run_release_demo()
    digest = first["release_demo_digest"]
    return [("release demo repeats identically", first == second, digest),
            ("release demo digest matches the record", digest == expected["release_demo"]["release_demo_digest"], digest),
            ("release demo outcome", first["engine2"]["outcome"] == expected["release_demo"]["outcome"],
             first["engine2"]["outcome"]),
            ("release demo leaves Engine 1 account state untouched",
             first["engine1"]["account_state_transitions"] == 0 and all(s is None for _, s in first["engine1"]["account_states"]),
             "0 transitions")]


def suite_check(name: str, arguments: list[str]) -> tuple[str, bool, str]:
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *arguments],
                               cwd=REPOSITORY_ROOT, capture_output=True, text=True)
    tail = (completed.stdout.strip().splitlines() or [""])[-1]
    tail = re.sub(r" in [0-9.]+s.*$", "", tail)
    return (name, completed.returncode == 0, tail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", action="store_true", help="also run the full repository suite")
    arguments = parser.parse_args(argv)
    expected = json.loads(RECORD.read_text(encoding="utf-8"))
    results = identity_checks(expected) + demo_checks(expected)
    for name, suite in SUITES.items():
        results.append(suite_check(name, suite))
    if arguments.full:
        results.append(suite_check("full repository suite", []))
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")
    failed = [name for name, passed, _ in results if not passed]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
