"""Entry point: `qdrant-full-mcp` (stdio transport)."""
from __future__ import annotations

import os
import sys

from .common import mcp


def load_modules() -> list[str]:
    """QDRANT_MCP_MODULES=cloud,data (default both). Use to expose only what you need."""
    wanted = [m.strip() for m in os.environ.get("QDRANT_MCP_MODULES", "cloud,data").split(",") if m.strip()]
    for m in wanted:
        if m == "cloud":
            from . import cloud  # noqa: F401  (registers tools on import)
        elif m == "data":
            from . import data  # noqa: F401
        else:
            print(f"[qdrant-full-mcp] unknown module {m!r} (valid: cloud, data)", file=sys.stderr)
    return wanted


def main() -> None:
    load_modules()
    mcp.run()


if __name__ == "__main__":
    main()
