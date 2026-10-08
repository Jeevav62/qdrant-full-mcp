"""Qdrant data-plane tools (collections and points) over the database REST API.

Target: pass cluster_id (endpoint auto-resolved via Cloud API; key from QDRANT_API_KEY_<ID> or QDRANT_API_KEY),
or set QDRANT_URL + QDRANT_API_KEY (database API key, not the cloud management key).
Embedding is out of scope: queries and upserts take ready-made vectors.
"""
from __future__ import annotations

from typing import Any, Optional, Union
from urllib.parse import quote

from .common import QdrantError, data_call, require_writable, segment, tool

DISTANCES = ("Cosine", "Euclid", "Dot", "Manhattan")
PointId = Union[int, str]


def _col(name: str) -> str:
    return quote(segment(name, "collection"), safe="")


def _vector(v: Any, field: str = "vector") -> list[float]:
    if not isinstance(v, list) or not v or len(v) > 65536 or not all(isinstance(x, (int, float)) for x in v):
        raise QdrantError(f"{field} must be a non-empty list of numbers (max 65536).")
    return v


def _ids(ids: list[PointId]) -> list[PointId]:
    if not isinstance(ids, list) or not ids or len(ids) > 1000:
        raise QdrantError("ids must be a list of 1..1000 point ids.")
    for i in ids:
        if isinstance(i, bool) or not (isinstance(i, int) and i >= 0 or isinstance(i, str) and i):
            raise QdrantError(f"Invalid point id {i!r}: use unsigned integers or UUID strings.")
    return ids


def _limit(n: int, hi: int = 100) -> int:
    if not 1 <= n <= hi:
        raise QdrantError(f"limit must be 1..{hi}.")
    return n


def _result(r: Any) -> Any:
    return r.get("result", r) if isinstance(r, dict) else r


# ---------------------------------------------------------------- read-only


@tool
def list_collections(cluster_id: Optional[str] = None) -> Any:
    """List collection names in the Qdrant database at QDRANT_URL (data plane, not Cloud account)."""
    return _result(data_call("GET", "/collections", cluster_id=cluster_id))


@tool
def get_collection(collection: str, cluster_id: Optional[str] = None) -> Any:
    """Collection details: vector config, points count, indexing status, payload schema."""
    return _result(data_call("GET", f"/collections/{_col(collection)}", cluster_id=cluster_id))


@tool
def count_points(collection: str, filter: Optional[dict] = None, exact: bool = True, cluster_id: Optional[str] = None) -> Any:
    """Count points in a collection, optionally matching a Qdrant filter object
    (e.g. {"must":[{"key":"city","match":{"value":"London"}}]})."""
    body: dict[str, Any] = {"exact": exact}
    if filter:
        body["filter"] = filter
    return _result(data_call("POST", f"/collections/{_col(collection)}/points/count", body=body, cluster_id=cluster_id))


@tool
def get_points(collection: str, ids: list[PointId], with_payload: bool = True, with_vector: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Fetch specific points by id (max 1000)."""
    return _result(data_call("POST", f"/collections/{_col(collection)}/points", body={
        "ids": _ids(ids), "with_payload": with_payload, "with_vector": with_vector}, cluster_id=cluster_id))


@tool
def scroll_points(collection: str, filter: Optional[dict] = None, limit: int = 20, offset: Optional[PointId] = None,
                  with_payload: bool = True, with_vector: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Browse points page by page (optionally filtered). Pass next_page_offset from the result as offset."""
    body: dict[str, Any] = {"limit": _limit(limit), "with_payload": with_payload, "with_vector": with_vector}
    if filter:
        body["filter"] = filter
    if offset is not None:
        body["offset"] = offset
    return _result(data_call("POST", f"/collections/{_col(collection)}/points/scroll", body=body, cluster_id=cluster_id))


@tool
def query_points(collection: str, vector: list[float], limit: int = 10, filter: Optional[dict] = None,
                 using: Optional[str] = None, score_threshold: Optional[float] = None,
                 with_payload: bool = True, with_vector: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Nearest-neighbour search by a ready-made query vector (this server does not embed text).
    'using' selects a named vector. Optional Qdrant filter object narrows candidates."""
    body: dict[str, Any] = {"query": _vector(vector), "limit": _limit(limit),
                            "with_payload": with_payload, "with_vector": with_vector}
    if filter:
        body["filter"] = filter
    if using:
        body["using"] = segment(using, "using")
    if score_threshold is not None:
        body["score_threshold"] = score_threshold
    return _result(data_call("POST", f"/collections/{_col(collection)}/points/query", body=body, cluster_id=cluster_id))


# ------------------------------------------------------------------ writes


@tool
def create_collection(collection: str, vector_size: int, distance: str = "Cosine", confirm: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Create a collection with a single unnamed vector. distance: Cosine|Euclid|Dot|Manhattan.
    vector_size must match your embedding model. confirm=false = dry run."""
    if not 1 <= vector_size <= 65536:
        raise QdrantError("vector_size must be 1..65536.")
    if distance not in DISTANCES:
        raise QdrantError(f"distance must be one of {', '.join(DISTANCES)}.")
    body = {"vectors": {"size": vector_size, "distance": distance}}
    if not confirm:
        return {"dry_run": True, "message": "Nothing created. Call again with confirm=true.",
                "collection": collection, "would_create": body}
    require_writable()
    return _result(data_call("PUT", f"/collections/{_col(collection)}", body=body, cluster_id=cluster_id))


@tool
def delete_collection(collection: str, confirm_collection_name: str, cluster_id: Optional[str] = None) -> Any:
    """PERMANENTLY DELETE a collection and all its points. IRREVERSIBLE. Only when the user explicitly asked;
    confirm_collection_name must equal collection exactly."""
    require_writable()
    if confirm_collection_name != collection:
        raise QdrantError("Confirmation failed: confirm_collection_name must equal collection. Nothing deleted.")
    return _result(data_call("DELETE", f"/collections/{_col(collection)}", cluster_id=cluster_id))


@tool
def upsert_points(collection: str, points: list[dict], confirm: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Insert or overwrite points. Each point: {"id": int|uuid, "vector": [floats], "payload": {...}}.
    Max 256 points per call. Existing ids are overwritten. confirm=false = dry run (validates only)."""
    if not isinstance(points, list) or not 1 <= len(points) <= 256:
        raise QdrantError("points must be a list of 1..256 items.")
    clean = []
    for p in points:
        if not isinstance(p, dict):
            raise QdrantError("each point must be an object with id, vector and optional payload.")
        _ids([p.get("id")])
        item: dict[str, Any] = {"id": p["id"], "vector": _vector(p.get("vector"), "point.vector")}
        if p.get("payload") is not None:
            if not isinstance(p["payload"], dict):
                raise QdrantError("point.payload must be an object.")
            item["payload"] = p["payload"]
        clean.append(item)
    if not confirm:
        return {"dry_run": True, "message": "Validated; nothing written. Call again with confirm=true.",
                "collection": collection, "point_count": len(clean)}
    require_writable()
    return _result(data_call("PUT", f"/collections/{_col(collection)}/points", params={"wait": "true"},
                             body={"points": clean}, cluster_id=cluster_id))


@tool
def delete_points(collection: str, ids: Optional[list[PointId]] = None, filter: Optional[dict] = None,
                  confirm: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Delete points by ids OR by a Qdrant filter (exactly one). Irreversible. confirm=false = dry run that reports
    how many points would match (filter) or be targeted (ids). Call with confirm=true only after user approval."""
    if bool(ids) == bool(filter):
        raise QdrantError("Provide exactly one of ids or filter.")
    selector: dict[str, Any] = {"points": _ids(ids)} if ids else {"filter": filter}
    if not confirm:
        matched = len(ids) if ids else _result(data_call(
            "POST", f"/collections/{_col(collection)}/points/count", body={"filter": filter, "exact": True}, cluster_id=cluster_id))
        return {"dry_run": True, "message": "Nothing deleted. Get user approval, then call with confirm=true.",
                "targets": matched}
    require_writable()
    return _result(data_call("POST", f"/collections/{_col(collection)}/points/delete", params={"wait": "true"},
                             body=selector, cluster_id=cluster_id))
