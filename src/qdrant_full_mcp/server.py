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
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _env(*names: str, default: str) -> str:
    """First non-empty variable. FASTMCP_SERVER_* are the official mcp-server-qdrant names, FASTMCP_* the SDK's."""
    return next((os.environ[n].strip() for n in names if os.environ.get(n, "").strip()), default)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="qdrant-full-mcp", description="MCP server for Qdrant Cloud and Qdrant data.")
    p.add_argument("--transport", choices=TRANSPORTS, default=os.environ.get("QDRANT_MCP_TRANSPORT", "stdio"),
                   help="stdio (default), sse or streamable-http")
    p.add_argument("--host", default=_env("FASTMCP_SERVER_HOST", "FASTMCP_HOST", default="127.0.0.1"),
                   help="bind address for sse/streamable-http (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=int(_env("FASTMCP_SERVER_PORT", "FASTMCP_PORT", default="8000")),
                   help="port for sse/streamable-http (default 8000)")
    args = p.parse_args(argv)
    if args.transport not in TRANSPORTS:
        p.error(f"QDRANT_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_modules()
    if os.environ.get("QDRANT_LOCAL_PATH", "").strip():
        from . import local
        local.preload()
    if args.transport != "stdio":
        configure_http(args.host, args.port)
    mcp.run(transport=args.transport)


def configure_http(host: str, port: int) -> None:
    mcp.settings.host, mcp.settings.port = host, port
    if host not in LOOPBACK:
        # FastMCP pins allowed Host headers to localhost when created; drop that pin like it does itself for
        # non-loopback hosts, or every request through a container or LAN address is rejected.
        mcp.settings.transport_security = None
        print(f"[qdrant-full-mcp] WARNING: listening on {host} without authentication. Anyone who can reach "
              "this port can use your Qdrant keys. Put it behind an authenticating proxy.", file=sys.stderr)


if __name__ == "__main__":
    main()
