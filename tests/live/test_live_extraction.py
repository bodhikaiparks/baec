"""The Phase 6C live comparison run. Opt-in only; it never runs in the default test suite.

It is deselected by default (pytest.ini adds -m "not live_claude"), and even when
selected it skips unless the live gate is open (see tests/live/harness.py and
docs/PHASE6C_LIVE_EVALUATION.md). Run one model per invocation, for example:

    BAEC_LIVE_CLAUDE=1 BAEC_LIVE_MODEL=claude-sonnet-5-5 BAEC_LIVE_REPORT_DIR=/absolute/ignored/or/external/dir \\
        BAEC_LIVE_CORPUS=<an exact corpus version, chosen explicitly for the run> \\
        python -m pytest tests/live/test_live_extraction.py -m live_claude -s -p no:cacheprovider

Since Phase 6D-C1 the gate also requires BAEC_LIVE_REPORT_DIR (tests/live/report.py), checked before any
provider attempt, and the run writes one sanitized baec-live-evaluation-report/v2 file there. Since Phase 6D-D
the corpus is selected explicitly by BAEC_LIVE_CORPUS; there is no default and no "latest".
"""

import os

import pytest

from tests.live.harness import LiveGateClosed
from tests.live.report import run_live

pytestmark = pytest.mark.live_claude


def test_live_extraction_comparison_run():
    try:
        run = run_live(os.environ)  # every gate first; then the production runtime and the real provider
    except LiveGateClosed as closed:
        pytest.skip(str(closed))
    report = run.evaluation
    # Infrastructure invariants. Model behavior is reported, not asserted: it informs the comparison.
    assert report.authority_unchanged
    assert report.calls_attempted == report.audit.cases_expected
    assert "provenance_failure" not in report.critical_failures
    assert run.report_file is not None and "report_failure" not in report.operational_failures
