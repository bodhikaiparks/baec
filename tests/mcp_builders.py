"""Shared setup for the Phase 5 MCP tests. Setup only; expectations live in the tests."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from baec_app.data.database import open_database
from baec_app.data.repository import Repository
from baec_app.data.seed import build_seed_database
from baec_app.mcp.composition import open_mcp_runtime
from tests.persistence_builders import dump

FIXED_RESOURCES = {"baec://accounts", "baec://baecs"}
APPROVED_TOOLS = (
    "preview_baec_classification",
    "preview_move_to_conditionally_dormant",
    "preview_move_to_active_opportunity",
    "preview_move_to_no_plausible_path",
)
RESOURCE_TEMPLATES = {
    "baec://accounts/{account_id}",
    "baec://accounts/{account_id}/interactions",
    "baec://accounts/{account_id}/transition-history",
    "baec://interactions/{interaction_id}",
    "baec://baecs/{baec_id}",
    "baec://baecs/{baec_id}/dormancy-judgments",
}


def seeded_database(directory: Path) -> str:
    """Build the synthetic seed database as a file and return its path."""
    path = str(directory / "mcp-seed.sqlite3")
    build_seed_database(path).close()
    return path


class Writer:
    """A writable connection to the same file, used only by tests to inspect or tamper with storage."""

    def __init__(self, path: str) -> None:
        self.connection = open_database(path)
        self.repository = Repository(self.connection)

    def dump(self):
        return dump(self.connection)

    def close(self) -> None:
        self.connection.close()


def run(async_function):
    """Run one async test body on a fresh event loop in the current thread."""
    return anyio.run(async_function)


@contextlib.asynccontextmanager
async def connected(path: str, **client_options):
    """Open the MCP runtime in the event-loop thread and connect an in-process client."""
    with open_mcp_runtime(path) as runtime:
        async with Client(runtime.server, **client_options) as client:
            yield runtime, client


REPO_ROOT = Path(__file__).resolve().parents[1]


def server_command(path: str, module: str = "baec_app.mcp") -> list[str]:
    """The documented invocation: python -m baec_app.mcp --database PATH."""
    return [sys.executable, "-m", module, "--database", str(path)]


@contextlib.asynccontextmanager
async def stdio_connected(path: str, errlog, module: str = "baec_app.mcp", **client_options):
    """Launch the server as a child process and connect through the SDK's real stdio client transport."""
    command = server_command(path, module)
    parameters = StdioServerParameters(command=command[0], args=command[1:], cwd=str(REPO_ROOT))
    async with Client(stdio_client(parameters, errlog=errlog), **client_options) as client:
        yield client
