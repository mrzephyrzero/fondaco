# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""`fondaco` — one command, several subcommands.

    fondaco serve      run the boundary (web UI + JSON API) from the environment
    fondaco mcp        MCP server for Claude Desktop, Cursor, … (proxies a boundary)
    fondaco discover   classify a source locally and propose labels

Each subcommand lives with the code it drives; this module only dispatches,
so `fondaco discover` never imports the web app and `fondaco mcp` never
imports the boundary.
"""

from __future__ import annotations

import sys

USAGE = """usage: fondaco {serve,mcp,discover} [options]

  serve      run the boundary (web UI + JSON API) configured from the environment
  mcp        MCP server (stdio or streamable-http) proxying a running boundary
  discover   classify a source locally and propose a labels file

Run `fondaco <command> --help` for the options of each command.
"""


def _serve(argv: list[str]) -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="fondaco serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    uvicorn.run("api.main:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0 if argv else 2
    command, rest = argv[0], argv[1:]
    if command == "discover":
        from executor.discovery.cli import main as discover

        return discover(["discover", *rest])
    if command == "mcp":
        from api.mcp_server import main as mcp

        return mcp(rest)
    if command == "serve":
        return _serve(rest)
    sys.stderr.write(f"fondaco: unknown command {command!r}\n\n{USAGE}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
