"""Local mode: QDRANT_LOCAL_PATH runs Qdrant inside this process (qdrant-client local mode), no server needed.

The data and text tools speak the Qdrant REST API through common.data_call. In local mode those same REST calls are
translated here into qdrant-client calls, and results are returned in the REST response shape, so the tools do not
need to know which backend they are talking to. QDRANT_LOCAL_PATH=":memory:" keeps everything in memory.

Local mode suits trying things out and small datasets. It is single-process (the folder is locked while the server
runs) and has no payload indexes (they are accepted and ignored).
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional
from urllib.parse import unquote

from .common import QdrantError

_client: Any = None
_client_path: Optional[str] = None


def local_path() -> Optional[str]:
    return os.environ.get("QDRANT_LOCAL_PATH", "").strip() or None


def preload() -> None:
    """Import qdrant-client (grpc, numpy) before the stdio transport starts; see text.preload for why."""
    import qdrant_client  # noqa: F401


def client() -> Any:
    global _client, _client_path
    path = local_path()
    if _client is None or path != _client_path:
        from qdrant_client import QdrantClient
        try:
            _client = QdrantClient(location=":memory:") if path == ":memory:" else QdrantClient(path=path)
        except Exception as e:  # folder locked by another process, permissions
            raise QdrantError(f"Could not open local Qdrant at {path!r}: {type(e).__name__}: {str(e)[:200]}") from None
        _client_path = path
    return _client


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    return obj


# ------------------------------------------------------------------ routes
# Each handler gets (client, models, collection, body, extra path groups) and returns the REST "result" value.


def _create_collection(c, m, col, body, *_):
    req = m.CreateCollection(**(body or {}))
    return c.create_collection(col, vectors_config=req.vectors or {}, sparse_vectors_config=req.sparse_vectors)


def _query(c, m, col, body, *_):
    q = m.QueryRequest(**body)
    return c.query_points(col, query=q.query, using=q.using, prefetch=q.prefetch, query_filter=q.filter,
                          limit=q.limit or 10, offset=q.offset, with_payload=q.with_payload if q.with_payload is not None
                          else True, with_vectors=q.with_vector or False, score_threshold=q.score_threshold)


def _scroll(c, m, col, body, *_):
    s = m.ScrollRequest(**(body or {}))
    points, nxt = c.scroll(col, scroll_filter=s.filter, limit=s.limit or 10, offset=s.offset,
                           with_payload=s.with_payload if s.with_payload is not None else True,
                           with_vectors=s.with_vector or False)
    return {"points": _dump(points), "next_page_offset": nxt}


def _count(c, m, col, body, *_):
    req = m.CountRequest(**(body or {}))
    return c.count(col, count_filter=req.filter, exact=True if req.exact is None else req.exact)


def _retrieve(c, m, col, body, *_):
    req = m.PointRequest(**body)
    return c.retrieve(col, ids=req.ids, with_payload=req.with_payload if req.with_payload is not None else True,
                      with_vectors=req.with_vector or False)


def _delete_points(c, m, col, body, *_):
    if body.get("points") is not None:
        selector = m.PointIdsList(points=body["points"])
    else:
        selector = m.FilterSelector(filter=m.Filter(**body["filter"]))
    return c.delete(col, points_selector=selector)


def _create_index(c, m, col, body, *_):
    return c.create_payload_index(col, field_name=body["field_name"], field_schema=body["field_schema"])


ROUTES: list[tuple[str, re.Pattern, Callable]] = [
    ("GET", re.compile(r"^/collections$"),
     lambda c, m, *_: {"collections": [{"name": x.name} for x in c.get_collections().collections]}),
    ("GET", re.compile(r"^/collections/([^/]+)/exists$"), lambda c, m, col, *_: {"exists": c.collection_exists(col)}),
    ("GET", re.compile(r"^/collections/([^/]+)$"), lambda c, m, col, *_: c.get_collection(col)),
    ("PUT", re.compile(r"^/collections/([^/]+)$"), _create_collection),
    ("DELETE", re.compile(r"^/collections/([^/]+)$"), lambda c, m, col, *_: c.delete_collection(col)),
    ("PUT", re.compile(r"^/collections/([^/]+)/points$"),
     lambda c, m, col, body, *_: c.upsert(col, points=m.PointsList(**body).points)),
    ("POST", re.compile(r"^/collections/([^/]+)/points$"), _retrieve),
    ("POST", re.compile(r"^/collections/([^/]+)/points/query$"), _query),
    ("POST", re.compile(r"^/collections/([^/]+)/points/scroll$"), _scroll),
    ("POST", re.compile(r"^/collections/([^/]+)/points/count$"), _count),
    ("POST", re.compile(r"^/collections/([^/]+)/points/delete$"), _delete_points),
    ("PUT", re.compile(r"^/collections/([^/]+)/index$"), _create_index),
    ("DELETE", re.compile(r"^/collections/([^/]+)/index/([^/]+)$"),
     lambda c, m, col, body, field: c.delete_payload_index(col, field_name=field)),
]


def request(method: str, path: str, body: Any = None) -> Any:
    """Serve one REST-style call from the local database; returns {"result": ...} like the REST API."""
    from qdrant_client import models
    for verb, pattern, handler in ROUTES:
        match = pattern.match(path)
        if verb == method and match:
            groups = [unquote(g) for g in match.groups()]
            col, extra = (groups[0], groups[1:]) if groups else (None, [])
            if col is not None and method != "PUT" and path != f"/collections/{match.group(1)}/exists":
                if not client().collection_exists(col):
                    raise QdrantError(f"Qdrant database 404 (not found): collection {col!r} does not exist.")
            try:
                return {"result": _dump(handler(client(), models, col, body, *extra))}
            except QdrantError:
                raise
            except Exception as e:  # validation errors, wrong vector size, unsupported options
                raise QdrantError(f"Local Qdrant error: {type(e).__name__}: {str(e)[:400]}") from None
    raise QdrantError(f"Not available in local mode (QDRANT_LOCAL_PATH): {method} {path}. "
                      "Use a Qdrant server (QDRANT_URL) for this operation.")
