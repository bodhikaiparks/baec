"""The Stage H live Engine 2 experiment. Opt-in only; it never runs in the default test suite.

Deselected by default (pytest.ini adds -m "not live_claude"), and even when selected it skips unless the
Stage H gate in tests/live/engine2_stage_h.py is open. Stage H-B runs only after separate approval:

    BAEC_LIVE_E2_STAGE_H=1 BAEC_LIVE_E2_RESEARCH_DIR=/absolute/empty/dir/outside/the/repo \\
        python -m pytest tests/live_engine2/test_live_engine2_stage_h.py -m live_claude -s -p no:cacheprovider
"""

import pytest

from tests.live.engine2_stage_h import LiveGateClosed, run_live

pytestmark = pytest.mark.live_claude


def test_stage_h_single_live_attempt():
    try:
        run = run_live()  # every gate first; then exactly one provider invocation
    except LiveGateClosed as closed:
        pytest.skip(str(closed))
    # Model behavior is recorded, not asserted. Only the harness invariants are checked here.
    print(f"run record sha256 {run.record_sha256}; outcome {run.record['outcome']}")
    assert run.record["ai_database_verification"] == "VERIFIED"
