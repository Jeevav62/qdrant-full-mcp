"""Database API keys the server remembers, so data tools work right after create_database_api_key.

- session: kept in this process only; gone when the MCP server stops.
- saved: also written to a key file in the user's config folder and picked up by every later session, in any MCP
  client. Opt-in per key (the agent must ask the user first). Location: %APPDATA%\\qdrant-full-mcp\\keys.json on
  Windows, ~/.config/qdrant-full-mcp/keys.json elsewhere (file mode 600). QDRANT_MCP_KEY_FILE overrides the path;
  QDRANT_MCP_KEY_FILE=off disables reading and writing it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .common import QdrantError

_session: dict[str, dict[str, Any]] = {}


def key_file() -> Optional[Path]:
    override = os.environ.get("QDRANT_MCP_KEY_FILE", "").strip()
    if override.lower() in ("off", "none", "0", "false"):
        return None
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "qdrant-full-mcp" / "keys.json"


def _load() -> dict[str, Any]:
    f = key_file()
    if f is None or not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise QdrantError(f"Key file {f} could not be read as JSON. Fix or delete it.") from None
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> Path:
    f = key_file()
    if f is None:
        raise QdrantError("Saving keys is disabled (QDRANT_MCP_KEY_FILE=off).")
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never world-readable, even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as h:
        json.dump(data, h, indent=2)
    os.replace(tmp, f)
    if os.name != "nt":
        os.chmod(f, 0o600)
    return f


def remember(cluster_id: str, secret: str, key_id: Optional[str], name: Optional[str], save: bool) -> Optional[str]:
    """Keep a key for this session; with save=True also persist it. Returns the key file path when saved."""
    entry = {"key": secret, "key_id": key_id, "name": name}
    _session[cluster_id] = entry
    if not save:
        return None
    data = _load()
    data[cluster_id] = {**entry, "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return str(_write(data))


def get(cluster_id: str) -> Optional[str]:
    if cluster_id in _session:
        return _session[cluster_id]["key"]
    entry = _load().get(cluster_id)
    return entry.get("key") if isinstance(entry, dict) else None


def forget(cluster_id: str, key_id: Optional[str] = None) -> dict[str, bool]:
    """Drop the remembered key for a cluster (only if it is key_id, when given). Does not revoke it in Qdrant."""
    def matches(entry: Any) -> bool:
        return isinstance(entry, dict) and (key_id is None or entry.get("key_id") == key_id)

    removed = {"session": False, "saved": False}
    if matches(_session.get(cluster_id)):
        del _session[cluster_id]
        removed["session"] = True
    data = _load()
    if matches(data.get(cluster_id)):
        del data[cluster_id]
        _write(data)
        removed["saved"] = True
    return removed


def summary() -> list[dict[str, Any]]:
    """What is remembered, without secrets."""
    saved = _load()
    out = []
    for cid in sorted(set(_session) | set(saved)):
        entry = _session.get(cid) or saved.get(cid) or {}
        out.append({"cluster_id": cid, "key_id": entry.get("key_id"), "name": entry.get("name"),
                    "where": "saved" if cid in saved else "session",
                    "saved_at": (saved.get(cid) or {}).get("saved_at")})
    return out
