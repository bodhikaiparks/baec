"""Test-only launcher: the production stdio entry point with one preview replaced by an unexpected failure.

    python -m tests.mcp_stdio_fault_server --database PATH

Used by tests/test_mcp_stdio.py to show that an unexpected handler failure
reaches the client as a generic error, keeps its details on stderr, and never
corrupts the stdout protocol stream. Production code never imports this.
"""

import sys

from baec_app.application import ProposalFacade
from baec_app.mcp.__main__ import main

SECRET = "SECRET-FAILURE-DETAIL /private/var/baec.sqlite3 SELECT * FROM accounts"


def _fail(self, *args, **kwargs):
    raise RuntimeError(SECRET)


if __name__ == "__main__":
    ProposalFacade.preview_move_to_active_opportunity = _fail
    sys.exit(main())
