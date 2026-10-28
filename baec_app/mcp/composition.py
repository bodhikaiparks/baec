"""Builds the MCP runtime from a database path through the public application API.

open_read_connection(path) -> build_proposal_facade(read_connection) -> build_mcp_server(facade).

The runtime owns the query-only read connection and closes it deterministically.
The connection is opened in the calling thread, which must be the thread that
runs the server's event loop: the connection is thread-affine and its thread
check stays enabled. No writable connection is ever opened.
"""

from __future__ import annotations

import os

from mcp.server import MCPServer

from baec_app.application import build_proposal_facade, open_read_connection
from baec_app.mcp.server import build_mcp_server


class McpRuntime:
    """The server plus ownership of its read connection. Use as a context manager or call close()."""

    def __init__(self, server: MCPServer, read_connection) -> None:
        self._server = server
        self._read_connection = read_connection
        self._closed = False

    @property
    def server(self) -> MCPServer:
        if self._closed:
            raise RuntimeError("the MCP runtime is closed")
        return self._server

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._read_connection.close()

    def __enter__(self) -> McpRuntime:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def open_mcp_runtime(database_path: str | os.PathLike[str]) -> McpRuntime:
    """Open the read path and build the server; on any failure the read connection is closed."""
    read_connection = open_read_connection(database_path)
    try:
        server = build_mcp_server(build_proposal_facade(read_connection))
    except BaseException:
        read_connection.close()
        raise
    return McpRuntime(server, read_connection)
