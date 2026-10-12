"""Shared MCP app, config, validation and HTTP helpers."""
from __future__ import annotations

import functools
import os
import re
from typing import Any, Callable, Optional

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("qdrant")

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")


class QdrantError(Exception):
    """Expected, user-presentable failure (bad input, API error, missing config)."""


# ------------------------------------------------------------------ config


def cloud_api_url() -> str:
    return os.environ.get("QDRANT_CLOUD_API_URL", "https://api.cloud.qdrant.io").rstrip("/")


def cloud_key() -> str:
    key = os.environ.get("QDRANT_CLOUD_API_KEY", "").strip()
    if not key:
        raise QdrantError("QDRANT_CLOUD_API_KEY is not set.")
    return key


_account_cache: dict[str, str] = {}


def acct() -> str:
    """Account UUID: QDRANT_CLOUD_ACCOUNT_ID if set, else auto-discovered from the management key."""
    env = os.environ.get("QDRANT_CLOUD_ACCOUNT_ID", "").strip()
    if env:
        if not UUID_RE.match(env):
            raise QdrantError("QDRANT_CLOUD_ACCOUNT_ID is not a valid UUID.")
        return env
    key = cloud_key()
    if key not in _account_cache:
        items = cloud_call("GET", "/api/account/v1/accounts").get("items", [])
        if len(items) != 1:
            names = ", ".join(f"{i.get('name')} ({i.get('id')})" for i in items) or "none"
            raise QdrantError(
                "Could not pick an account automatically: this key can see "
                f"{len(items)} accounts [{names}]. Set QDRANT_CLOUD_ACCOUNT_ID to the one you want.")
        _account_cache[key] = items[0]["id"]
    return _account_cache[key]


READ_ONLY_VARS = ("QDRANT_MCP_READ_ONLY", "QDRANT_READ_ONLY", "QDRANT_CLOUD_READ_ONLY")  # QDRANT_READ_ONLY: official name


def read_only() -> bool:
    return any(os.environ.get(v, "").strip().lower() in ("1", "true", "yes") for v in READ_ONLY_VARS)


def require_writable() -> None:
    if read_only():
        raise QdrantError("Server is in read-only mode (QDRANT_MCP_READ_ONLY or QDRANT_READ_ONLY is set).")


# -------------------------------------------------------------- validation


def uuid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not UUID_RE.match(value):
        raise QdrantError(f"{field} must be a UUID, got {value!r}.")
    return value


def segment(value: Any, field: str) -> str:
    """Validate a value interpolated into a URL path (blocks traversal / injection)."""
    if not isinstance(value, str) or not SEGMENT_RE.match(value) or value in (".", ".."):
        raise QdrantError(f"{field} has invalid characters: {value!r}.")
    return value


def cidrs(values: list[str]) -> list[str]:
    if len(values) > 40 or any(not CIDR_RE.match(c) for c in values):
        raise QdrantError("allowed_ip_source_ranges must be <=40 IPv4 CIDRs like '1.2.3.4/32'.")
    return values


def page(page_size: int) -> int:
    if not 1 <= page_size <= 250:
        raise QdrantError("page_size must be 1..250.")
    return page_size


# --------------------------------------------------------------------- http


def _raise_for(r: httpx.Response, what: str) -> None:
    if r.status_code < 400:
        return
    try:
        body = r.json()
        detail = body.get("message") or body.get("status", {}).get("error") or r.text
    except Exception:
        detail = r.text
    hint = {
        401: " (invalid or missing key)",
        403: " (forbidden: the key lacks permission, or the feature is not available on this plan)",
        404: " (not found)",
    }.get(r.status_code, "")
    message = f"{what} {r.status_code}{hint}: {str(detail)[:500]}"
    # Qdrant Cloud runs in strict mode: filtering on a field needs a payload index first.
    missing = re.search(r'Index required but not found for "([^"]+)" of one of the following types: \[(\w+)', str(detail))
    if missing:
        message += (f" Fix: call create_payload_index with field_name='{missing.group(1)}', "
                    f"field_schema='{missing.group(2)}' (once per collection), then retry.")
    raise QdrantError(message)


def _send(method: str, url: str, headers: dict, params: Optional[dict], body: Any, what: str) -> Any:
    try:
        r = httpx.request(
            method, url,
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=body, headers={**headers, "Accept": "application/json"}, timeout=30,
        )
    except httpx.HTTPError as e:
        raise QdrantError(f"Network error calling {what}: {type(e).__name__}") from None
    _raise_for(r, what)
    return r.json() if r.content else {}


def cloud_call(method: str, path: str, *, params: Optional[dict] = None, body: Any = None) -> Any:
    """Call the Qdrant Cloud management API. Query params must be snake_case (gateway requirement)."""
    key = cloud_key()
    return _send(method, f"{cloud_api_url()}{path}", {"Authorization": f"apikey {key}"}, params, body,
                 "Qdrant Cloud API")


_endpoint_cache: dict[str, str] = {}


def cluster_endpoint(cluster_id: str) -> str:
    """Resolve a Cloud cluster's REST endpoint (https://host:port) via the management API."""
    uuid(cluster_id, "cluster_id")
    if cluster_id not in _endpoint_cache:
        r = cloud_call("GET", f"/api/cluster/v1/accounts/{acct()}/clusters/{cluster_id}")
        ep = ((r.get("cluster", r).get("state") or {}).get("endpoint") or {})
        url = (ep.get("url") or "").rstrip("/")
        if not url:
            raise QdrantError("Cluster has no endpoint yet (still creating, suspended or failed). "
                              "Check get_cluster state.phase.")
        _endpoint_cache[cluster_id] = f"{url}:{ep.get('restPort') or 6333}"
    return _endpoint_cache[cluster_id]


def cluster_key(cluster_id: str) -> tuple[str, str]:
    """Database key for a Cloud cluster and where it came from: QDRANT_API_KEY_<ID_WITH_UNDERSCORES>, then a key
    remembered by create_database_api_key (keystore), then QDRANT_API_KEY."""
    from . import keystore
    per = "QDRANT_API_KEY_" + cluster_id.replace("-", "_").upper()
    for source, key in (("env", os.environ.get(per, "")), ("remembered", keystore.get(cluster_id)),
                        ("env", os.environ.get("QDRANT_API_KEY", ""))):
        if key and key.strip():
            return key.strip(), source
    raise QdrantError(
        f"No database API key for cluster {cluster_id}. Ask to create one with create_database_api_key (it is "
        f"remembered automatically, no restart needed), or set {per} / QDRANT_API_KEY. The management key cannot "
        "access data.")


def data_call(method: str, path: str, *, params: Optional[dict] = None, body: Any = None,
              cluster_id: Optional[str] = None) -> Any:
    """Call a Qdrant database (REST).

    With cluster_id: endpoint is looked up from the Cloud API and the key comes from QDRANT_API_KEY_<ID>/QDRANT_API_KEY.
    Without: QDRANT_LOCAL_PATH (in-process database, see local.py), or QDRANT_URL + QDRANT_API_KEY (key optional).
    """
    if cluster_id:
        url, (key, source) = cluster_endpoint(cluster_id), cluster_key(cluster_id)
        try:
            return _send(method, f"{url}{path}", {"api-key": key}, params, body, "Qdrant database")
        except QdrantError as e:
            if source == "remembered" and " 401 " in f" {e} ":
                raise QdrantError(f"{e} The remembered key for this cluster was rejected (expired or revoked). "
                                  "Create a new one with create_database_api_key, or remove it with "
                                  "forget_database_api_key.") from None
            raise
    elif os.environ.get("QDRANT_LOCAL_PATH", "").strip():
        if os.environ.get("QDRANT_URL", "").strip():
            raise QdrantError("Set either QDRANT_URL or QDRANT_LOCAL_PATH, not both.")
        from . import local
        return local.request(method, path, body)
    else:
        url = os.environ.get("QDRANT_URL", "").strip().rstrip("/")
        if not url:
            raise QdrantError("QDRANT_URL is not set (e.g. https://<cluster>.cloud.qdrant.io:6333 or "
                              "http://localhost:6333). Alternatively pass cluster_id to use a Cloud cluster, or set "
                              "QDRANT_LOCAL_PATH to a folder to run without a server.")
        if not re.match(r"^https?://", url):
            raise QdrantError("QDRANT_URL must start with http:// or https://")
        key = os.environ.get("QDRANT_API_KEY", "").strip()
    headers = {"api-key": key} if key else {}
    return _send(method, f"{url}{path}", headers, params, body, "Qdrant database")


# ------------------------------------------------------------------- tools


def tool(fn: Callable) -> Callable:
    """Register as an MCP tool; surface QdrantError as a clean message (never a stack trace)."""

    @functools.wraps(fn)
    def inner(*a, **kw):
        try:
            return fn(*a, **kw)
        except QdrantError as e:
            raise ValueError(str(e)) from None

    return mcp.tool()(inner)
