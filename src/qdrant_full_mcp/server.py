"""Entry point: `qdrant-full-mcp` (stdio transport)."""
from __future__ import annotations

import argparse
import os
import sys

from .common import mcp


def load_modules() -> list[str]:
    """QDRANT_MCP_MODULES=cloud,data,text (default all). Use to expose only what you need."""
    wanted = [m.strip() for m in os.environ.get("QDRANT_MCP_MODULES", "cloud,data,text").split(",") if m.strip()]
    for m in wanted:
        if m == "cloud":
            from . import cloud  # noqa: F401  (registers tools on import)
        elif m == "data":
            from . import data  # noqa: F401
        elif m == "text":
            from . import text
            text.preload()
        else:
            print(f"[qdrant-full-mcp] unknown module {m!r} (valid: cloud, data, text)", file=sys.stderr)
    return wanted


TRANSPORTS = ("stdio", "sse", "streamable-http")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="qdrant-full-mcp", description="MCP server for Qdrant Cloud and Qdrant data.")
    p.add_argument("--transport", choices=TRANSPORTS, default=os.environ.get("QDRANT_MCP_TRANSPORT", "stdio"),
                   help="stdio (default), sse or streamable-http")
    p.add_argument("--host", default=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
                   help="bind address for sse/streamable-http (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=int(os.environ.get("FASTMCP_PORT", "8000")),
                   help="port for sse/streamable-http (default 8000)")
    args = p.parse_args(argv)
    if args.transport not in TRANSPORTS:
        p.error(f"QDRANT_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_modules()
    if args.transport != "stdio":
        mcp.settings.host, mcp.settings.port = args.host, args.port
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print(f"[qdrant-full-mcp] WARNING: listening on {args.host} without authentication. Anyone who can reach "
                  "this port can use your Qdrant keys. Put it behind an authenticating proxy.", file=sys.stderr)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
