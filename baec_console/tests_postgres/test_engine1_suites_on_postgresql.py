"""V0.4 Phase 3C: the existing Engine 1 repository test suites, unchanged, pass on PostgreSQL.

The suites below run in a subprocess with the plugin engine1_on_postgres.py, which gives every database they open a
PostgreSQL Engine1Connection (a fresh synthetic organization per database, as the application role). They are the
suites that exercise Engine 1 through the repository and the application layer: storage round trips, fail-closed
rehydration of corrupted rows, every transition rule, the command facade, classification, and dormancy. The run must
have no failure and no error; the only tests not run are the ones the plugin lists in NOT_APPLICABLE, each with the
reason and the test that covers its invariant on both backends.

Not included (whole files), because they exercise SQLite mechanisms rather than repository behavior:
    tests/test_rc33_schema_guards.py, tests/test_evidence_fidelity.py   raw-SQL schema guards: every one of their
        invariants runs on both backends in test_engine1_parity.py
    tests/test_application_errors.py, tests/test_application_proposals.py   open SQLite files by path and the
        read-only proposal facade (open_read_connection, an exact sqlite3.Connection); ConsoleService does not use it
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from .engine1_on_postgres import NOT_APPLICABLE

ROOT = Path(__file__).resolve().parents[2]
SUITES = ("tests/test_repository_integrity.py", "tests/test_repository_roundtrip.py",
          "tests/test_transition_persistence.py", "tests/test_application_account_state.py",
          "tests/test_application_classification.py", "tests/test_application_dormancy.py")


def test_the_existing_engine1_repository_suites_pass_on_postgresql(engine1_roles, tmp_path):
    report = tmp_path / "suites.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "baec_console.tests_postgres.engine1_on_postgres", "-p",
         "no:cacheprovider", "-q", f"--junitxml={report}", *SUITES],
        cwd=ROOT, capture_output=True, text=True, timeout=900, env={**os.environ, "PYTHONPATH": str(ROOT)})
    tree = ElementTree.parse(report).getroot()
    suite = tree if tree.tag == "testsuite" else tree.find("testsuite")
    counts = {k: int(suite.get(k)) for k in ("tests", "failures", "errors", "skipped")}
    skipped = {f"{c.get('classname').replace('.', '/')}.py::{c.get('name')}" for c in suite.iter("testcase")
               if c.find("skipped") is not None}
    assert result.returncode == 0, result.stdout[-4000:]
    assert counts["failures"] == counts["errors"] == 0
    assert all(name in NOT_APPLICABLE or name.split("[")[0] in NOT_APPLICABLE for name in skipped), skipped
    assert counts["tests"] - counts["skipped"] >= 300, counts
    print(f"existing Engine 1 suites on PostgreSQL: {counts}")
