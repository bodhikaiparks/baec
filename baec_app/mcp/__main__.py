"""The stdio entry point for the read-only MCP Core server (Phase 5D).

    python -m baec_app.mcp --database PATH

Composition is unchanged from 5B: open_read_connection(PATH) ->
build_proposal_facade(read_connection) -> build_mcp_server(facade), all
through open_mcp_runtime, then the SDK's own stdio transport
(MCPServer.run_stdio_async). There is no other transport and no network
listener.

The database path is given only by --database. Nothing is discovered from the
current directory, nothing is created, and there is no writable fallback:
open_read_connection refuses a missing file, ":memory:", a file: URI, and a
database at another schema version.

stdout carries only MCP protocol messages. Every human diagnostic, including
help text and start-up failures, goes to stderr.

Shutdown. The read connection is opened in this thread, which then runs the
event loop, and it is closed on every exit path:
- the client closes stdin (the normal MCP stdio shutdown): the transport
  ends and the runtime context closes the connection;
- the transport fails: the error is logged and the context closes the
  connection once the transport has unwound;
- SIGTERM or SIGINT: the connection is closed on the event-loop thread and
  the process exits at once. mcp 2.2.0 reads stdin on a worker thread that
  cancellation cannot interrupt, so cancelling the transport would wait for
  the next line or EOF; exiting directly keeps a stop request bounded.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import anyio

from baec_app.application import ApplicationError, DatabaseVersionError
from baec_app.mcp.composition import McpRuntime, open_mcp_runtime

_logger = logging.getLogger("baec_app.mcp")


class _HelpToStderr(argparse.Action):
    """argparse prints help to stdout by default; stdout is reserved for the protocol."""

    def __init__(self, option_strings, dest, **kwargs) -> None:
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        parser.print_help(sys.stderr)
        parser.exit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m baec_app.mcp",
        description="Serve the read-only BAEC MCP Core over stdio.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action=_HelpToStderr, help="show this help on stderr and exit")
    parser.add_argument("--database", required=True, metavar="PATH", help="an existing synthetic BAEC database file")
    return parser


async def _serve(runtime: McpRuntime) -> None:
    """Serve over stdio until the client closes stdin, or stop at once on SIGTERM or SIGINT."""
    async with anyio.create_task_group() as group:

        async def stop_on_signal() -> None:
            with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
                # stderr only, never the protocol: the stop handlers are installed from here on.
                _logger.info("stop signals armed")
                async for signum in signals:
                    runtime.close()
                    _logger.info("%s received; read connection closed", signal.Signals(signum).name)
                    logging.shutdown()
                    os._exit(128 + signum)

        group.start_soon(stop_on_signal)
        await runtime.server.run_stdio_async()
        group.cancel_scope.cancel()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        runtime = open_mcp_runtime(arguments.database)
    except (ApplicationError, DatabaseVersionError) as error:
        _logger.error("cannot start: %s", error)
        return 1
    status = 0
    with runtime:
        _logger.info("serving the read-only MCP Core over stdio")
        try:
            anyio.run(_serve, runtime)
        except Exception:
            _logger.exception("the stdio transport failed")
            status = 1
    _logger.info("stopped; read connection closed")
    return status


if __name__ == "__main__":
    sys.exit(main())
