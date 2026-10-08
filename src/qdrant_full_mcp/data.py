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


def _sparse(v: Any, field: str = "sparse_vector") -> dict:
    """Sparse vector: {"indices": [uint...], "values": [float...]} with equal lengths."""
    if not isinstance(v, dict) or set(v) != {"indices", "values"}:
        raise QdrantError(f'{field} must be {{"indices": [...], "values": [...]}}.')
    idx, val = v["indices"], v["values"]
    if (not isinstance(idx, list) or not isinstance(val, list) or not idx or len(idx) != len(val)
            or len(idx) > 100000
            or not all(isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in idx)
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val)):
        raise QdrantError(f"{field}: indices (unsigned ints) and values (numbers) must be equal-length, non-empty.")
    return v


def _point_vector(v: Any) -> Any:
    """A point's vector: a dense list, or a dict of named dense lists / sparse objects."""
    if isinstance(v, list):
        return _vector(v, "point.vector")
    if isinstance(v, dict) and v and not set(v) == {"indices", "values"}:
        out = {}
        for name, item in v.items():
            segment(name, "vector name")
            out[name] = _sparse(item, f"point.vector[{name}]") if isinstance(item, dict) else _vector(
                item, f"point.vector[{name}]")
        return out
    raise QdrantError("point.vector must be a list of numbers, or an object mapping vector names to "
                      'dense lists or sparse {"indices","values"} objects.')


def _field(name: str) -> str:
    """Payload field path, e.g. 'city' or 'meta.tags[].name'."""
    import re
    if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9_.\[\]-]{1,255}$", name):
        raise QdrantError(f"Invalid payload field name: {name!r}.")
    return name


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
def query_points(collection: str, vector: Optional[list[float]] = None, sparse_vector: Optional[dict] = None,
                 limit: int = 10, filter: Optional[dict] = None,
                 using: Optional[str] = None, score_threshold: Optional[float] = None,
                 with_payload: bool = True, with_vector: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Nearest-neighbour search by a ready-made query vector (this server does not embed text).
    Pass exactly one of: vector (dense list) or sparse_vector ({"indices":[...],"values":[...]}).
    'using' selects a named vector (required for sparse and for named-vector collections).
    Optional Qdrant filter object narrows candidates. For dense+sparse together use hybrid_query_points."""
    if (vector is None) == (sparse_vector is None):
        raise QdrantError("Provide exactly one of vector or sparse_vector.")
    query = _vector(vector) if vector is not None else _sparse(sparse_vector)
    if sparse_vector is not None and not using:
        raise QdrantError("'using' (the sparse vector name) is required with sparse_vector.")
    body: dict[str, Any] = {"query": query, "limit": _limit(limit),
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
def hybrid_query_points(collection: str, dense_vector: list[float], sparse_vector: dict,
                        dense_using: str = "dense", sparse_using: str = "sparse", fusion: str = "rrf",
                        limit: int = 10, prefetch_limit: int = 20, filter: Optional[dict] = None,
                        with_payload: bool = True, cluster_id: Optional[str] = None) -> Any:
    """Hybrid search: runs a dense and a sparse query, then fuses the rankings.
    The collection must have a named dense vector (dense_using) and a named sparse vector (sparse_using).
    sparse_vector: {"indices":[...], "values":[...]}. fusion: 'rrf' (reciprocal rank fusion, default) or 'dbsf'.
    Needs ready-made vectors; this server does not embed text."""
    if fusion not in ("rrf", "dbsf"):
        raise QdrantError("fusion must be 'rrf' or 'dbsf'.")
    pf = {"limit": _limit(prefetch_limit, 1000)}
    if filter:
        pf["filter"] = filter
    body = {
        "prefetch": [
            {**pf, "query": _sparse(sparse_vector), "using": segment(sparse_using, "sparse_using")},
            {**pf, "query": _vector(dense_vector, "dense_vector"), "using": segment(dense_using, "dense_using")},
        ],
        "query": {"rrf": {}} if fusion == "rrf" else {"fusion": "dbsf"},
        "limit": _limit(limit), "with_payload": with_payload,
    }
    return _result(data_call("POST", f"/collections/{_col(collection)}/points/query", body=body,
                             cluster_id=cluster_id))


@tool
def list_payload_indexes(collection: str, cluster_id: Optional[str] = None) -> Any:
    """Show the payload indexes (field -> type) of a collection, read from its payload_schema."""
    info = _result(data_call("GET", f"/collections/{_col(collection)}", cluster_id=cluster_id))
    return (info or {}).get("payload_schema", {})


@tool
def create_payload_index(collection: str, field_name: str, field_schema: str, is_tenant: bool = False,
                         confirm: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Create a payload index so filters on field_name are fast. field_schema: keyword|integer|float|bool|geo|
    datetime|text|uuid. is_tenant=true (keyword/uuid only) optimizes multi-tenant collections. Uses extra
    memory/CPU while indexing. confirm=false = dry run."""
    if field_schema not in ("keyword", "integer", "float", "bool", "geo", "datetime", "text", "uuid"):
        raise QdrantError("field_schema must be keyword, integer, float, bool, geo, datetime, text or uuid.")
    if is_tenant and field_schema not in ("keyword", "uuid"):
        raise QdrantError("is_tenant is only valid for keyword and uuid indexes.")
    schema: Any = {"type": field_schema, "is_tenant": True} if is_tenant else field_schema
    body = {"field_name": _field(field_name), "field_schema": schema}
    if not confirm:
        return {"dry_run": True, "message": "Nothing created. Call again with confirm=true.",
                "collection": collection, "would_create": body}
    require_writable()
    return _result(data_call("PUT", f"/collections/{_col(collection)}/index", params={"wait": "true"}, body=body,
                             cluster_id=cluster_id))


@tool
def delete_payload_index(collection: str, field_name: str, confirm: bool = False,
                         cluster_id: Optional[str] = None) -> Any:
    """Delete a payload index (data is kept; filters on that field become slower). confirm=false = dry run."""
    field = _field(field_name)
    if not confirm:
        return {"dry_run": True, "message": "Nothing deleted. Call again with confirm=true.",
                "collection": collection, "field_name": field}
    require_writable()
    return _result(data_call("DELETE", f"/collections/{_col(collection)}/index/{quote(field, safe='')}",
                             params={"wait": "true"}, cluster_id=cluster_id))


@tool
def create_collection(collection: str, vector_size: Optional[int] = None, distance: str = "Cosine",
                      named_vectors: Optional[dict] = None, sparse_vectors: Optional[list[str]] = None,
                      confirm: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Create a collection. distance: Cosine|Euclid|Dot|Manhattan. Choose a layout:
    (a) one unnamed dense vector: vector_size (must match your embedding model) + distance;
    (b) named dense vectors: named_vectors={"dense": {"size": 768, "distance": "Cosine"}, ...};
    optionally add sparse_vectors=["sparse"] (names of sparse vectors, e.g. for hybrid search).
    (a) cannot be combined with (b). confirm=false = dry run."""
    if vector_size is not None and named_vectors:
        raise QdrantError("Use either vector_size or named_vectors, not both.")
    body: dict[str, Any] = {}
    if named_vectors:
        vecs = {}
        for name, cfg in named_vectors.items():
            segment(name, "vector name")
            size, dist = (cfg or {}).get("size"), (cfg or {}).get("distance", "Cosine")
            if not isinstance(size, int) or not 1 <= size <= 65536 or dist not in DISTANCES:
                raise QdrantError(f"named_vectors[{name}] needs size 1..65536 and distance in {DISTANCES}.")
            vecs[name] = {"size": size, "distance": dist}
        body["vectors"] = vecs
    elif vector_size is not None:
        if not 1 <= vector_size <= 65536:
            raise QdrantError("vector_size must be 1..65536.")
        if distance not in DISTANCES:
            raise QdrantError(f"distance must be one of {', '.join(DISTANCES)}.")
        body["vectors"] = {"size": vector_size, "distance": distance}
    elif not sparse_vectors:
        raise QdrantError("Provide vector_size, named_vectors, or sparse_vectors.")
    if sparse_vectors:
        body["sparse_vectors"] = {segment(n, "sparse vector name"): {} for n in sparse_vectors}
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
    """Insert or overwrite points. Each point: {"id": int|uuid, "vector": ..., "payload": {...}} where vector is a
    list of floats, or for named/sparse vectors an object like
    {"dense": [floats], "sparse": {"indices": [...], "values": [...]}}.
    Max 256 points per call. Existing ids are overwritten. confirm=false = dry run (validates only)."""
    if not isinstance(points, list) or not 1 <= len(points) <= 256:
        raise QdrantError("points must be a list of 1..256 items.")
    clean = []
    for p in points:
        if not isinstance(p, dict):
            raise QdrantError("each point must be an object with id, vector and optional payload.")
        _ids([p.get("id")])
        item: dict[str, Any] = {"id": p["id"], "vector": _point_vector(p.get("vector"))}
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
