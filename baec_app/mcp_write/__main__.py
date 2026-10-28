"""The stdio entry point for the one-tool grant-execution server (Phase 7G).

    python -m baec_app.mcp_write --database PATH

The database path is given only by --database. Nothing is discovered, created, seeded, or migrated: the path
must be an existing database at the current schema version. The clock is the real UTC clock (SystemClock), read
by the executor once per execution. There is no other transport and no network listener.

stdout carries only MCP protocol messages; every diagnostic goes to stderr.

Shutdown mirrors the Phase 5 entry point (baec_app/mcp/__main__.py), without importing it: when the client
closes stdin the transport ends and the connection is closed; on SIGTERM or SIGINT the connection is closed on
the event-loop thread and the process exits at once. An execution is one SQLite transaction, so a stop either
lands before COMMIT (nothing written, grant unused) or after it (fully confirmed).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import anyio

from baec_app.application import DatabaseVersionError, SystemClock
from baec_app.mcp_write.composition import WriteDatabaseUnavailable, WriteRuntime, open_write_runtime

_logger = logging.getLogger("baec_app.mcp_write")


class _HelpToStderr(argparse.Action):
    """argparse prints help to stdout by default; stdout is reserved for the protocol."""

    def __init__(self, option_strings, dest, **kwargs) -> None:
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        parser.print_help(sys.stderr)
        parser.exit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m baec_app.mcp_write",
        description="Serve the one-tool BAEC grant-execution MCP server over stdio.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action=_HelpToStderr, help="show this help on stderr and exit")
    parser.add_argument("--database", required=True, metavar="PATH",
                        help="an existing synthetic BAEC database file at the current schema version")
    return parser


async def _serve(runtime: WriteRuntime) -> None:
    """Serve over stdio until the client closes stdin, or stop at once on SIGTERM or SIGINT."""
    async with anyio.create_task_group() as group:

        async def stop_on_signal() -> None:
            with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
                # stderr only, never the protocol: the stop handlers are installed from here on.
                _logger.info("stop signals armed")
                async for signum in signals:
                    runtime.close()
                    _logger.info("%s received; write connection closed", signal.Signals(signum).name)
                    logging.shutdown()
                    os._exit(128 + signum)

        group.start_soon(stop_on_signal)
        await runtime.server.run_stdio_async()
        group.cancel_scope.cancel()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        runtime = open_write_runtime(arguments.database, clock=SystemClock())
    except (WriteDatabaseUnavailable, DatabaseVersionError) as error:
        _logger.error("cannot start: %s", error)
        return 1
    status = 0
    with runtime:
        _logger.info("serving the one-tool grant-execution server over stdio")
        try:
            anyio.run(_serve, runtime)
        except Exception:
            _logger.exception("the stdio transport failed")
            status = 1
    _logger.info("stopped; write connection closed")
    return status


if __name__ == "__main__":
    sys.exit(main())
