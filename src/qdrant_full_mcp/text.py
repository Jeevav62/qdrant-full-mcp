"""Text-in, text-out tools: store_text and find_text.

Embeds with FastEmbed locally (the same approach as the official mcp-server-qdrant), so you can store and search
plain text without making vectors yourself. Needs the optional extra: pip install "qdrant-full-mcp[embed]".

Layout matches the official server so collections are interchangeable: one named dense vector called
"fast-<model>" (default fast-all-minilm-l6-v2), payload {"document": text, "metadata": {...}}.
"""
from __future__ import annotations

import os
import uuid as _uuid
from typing import Any, Optional

from .common import QdrantError, data_call, require_writable, segment, tool
from .datautil import PointId, _col, _ids, _limit, _result

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MAX_TEXTS = 256
MAX_TEXT_CHARS = 100_000

_models: dict[str, Any] = {}


def model_name() -> str:
    return os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def vector_name(model: Optional[str] = None) -> str:
    """Same naming as the official server: fast-<last path part of the model, lowercased>."""
    return "fast-" + (model or model_name()).split("/")[-1].lower()


def _load(model: str) -> Any:
    if model not in _models:
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise QdrantError(
                "Text embedding needs the optional 'embed' extra. Install it with: "
                'uvx --from "qdrant-full-mcp[embed]" qdrant-full-mcp  (or pip install "qdrant-full-mcp[embed]").'
            ) from None
        try:
            _models[model] = TextEmbedding(model_name=model)
        except Exception as e:  # unknown model name, download failure
            raise QdrantError(f"Could not load embedding model {model!r}: {type(e).__name__}: {str(e)[:200]}") from None
    return _models[model]


def embed(texts: list[str], model: Optional[str] = None) -> list[list[float]]:
    """Embed texts; returns one list of floats per text. (Replaced in tests.)"""
    m = _load(model or model_name())
    return [[float(x) for x in v] for v in m.embed(texts)]


def _texts(texts: Any) -> list[str]:
    if (not isinstance(texts, list) or not 1 <= len(texts) <= MAX_TEXTS
            or not all(isinstance(t, str) and t.strip() and len(t) <= MAX_TEXT_CHARS for t in texts)):
        raise QdrantError(f"texts must be a list of 1..{MAX_TEXTS} non-empty strings (each <= {MAX_TEXT_CHARS} chars).")
    return texts


def _vectors_config(collection: str, cluster_id: Optional[str]) -> Optional[Any]:
    """Return the collection's vectors config, or None if the collection does not exist."""
    exists = _result(data_call("GET", f"/collections/{_col(collection)}/exists", cluster_id=cluster_id))
    if not (isinstance(exists, dict) and exists.get("exists")):
        return None
    info = _result(data_call("GET", f"/collections/{_col(collection)}", cluster_id=cluster_id)) or {}
    return ((info.get("config") or {}).get("params") or {}).get("vectors") or {}


def _target_vector(vectors: Any, name: str, collection: str) -> Optional[str]:
    """Which vector to write/search. None = the collection's single unnamed vector."""
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return None  # unnamed
    if isinstance(vectors, dict) and name in vectors:
        return name
    if isinstance(vectors, dict) and len(vectors) == 1:
        return next(iter(vectors))
    have = sorted(vectors) if isinstance(vectors, dict) else []
    raise QdrantError(f"Collection {collection!r} has vectors {have}; none is {name!r}. "
                      "Use store/find only on collections created by these tools, or use upsert_points/query_points.")


def _check_size(vectors: Any, target: Optional[str], dim: int, collection: str) -> None:
    cfg = vectors if target is None else (vectors.get(target) or {})
    size = cfg.get("size") if isinstance(cfg, dict) else None
    if isinstance(size, int) and size != dim:
        raise QdrantError(f"Collection {collection!r} expects {size}-dimension vectors but model {model_name()!r} "
                          f"produces {dim}. Use the model the collection was created with (EMBEDDING_MODEL).")


@tool
def store_text(collection: str, texts: list[str], metadata: Optional[list[dict]] = None,
               ids: Optional[list[PointId]] = None, confirm: bool = False, cluster_id: Optional[str] = None) -> Any:
    """Store plain text in Qdrant. The text is embedded locally (FastEmbed, default all-MiniLM-L6-v2, change with
    EMBEDDING_MODEL), so no vectors are needed. Creates the collection if it does not exist. Each text becomes a point
    with payload {"document": text, "metadata": {...}}; ids are generated unless given.
    metadata: optional list, one object per text. confirm=false = dry run (nothing embedded or written); when the user
    clearly asked to store something, call with confirm=true directly. Max 256 texts per call."""
    items = _texts(texts)
    if metadata is not None and (not isinstance(metadata, list) or len(metadata) != len(items)
                                 or not all(isinstance(m, dict) for m in metadata)):
        raise QdrantError("metadata must be a list of objects, one per text.")
    if ids is not None and len(_ids(ids)) != len(items):
        raise QdrantError("ids must have one id per text.")
    name = vector_name()
    if not confirm:
        return {"dry_run": True, "message": "Validated; nothing embedded or written. Call again with confirm=true.",
                "collection": collection, "text_count": len(items), "model": model_name(), "vector_name": name}
    require_writable()
    vectors = _vectors_config(collection, cluster_id)
    target = name if vectors is None else _target_vector(vectors, name, collection)  # fail before embedding
    vecs = embed(items)
    created = False
    if vectors is None:
        _result(data_call("PUT", f"/collections/{_col(collection)}", cluster_id=cluster_id, body={
            "vectors": {name: {"size": len(vecs[0]), "distance": "Cosine"}}}))
        created = True
    else:
        _check_size(vectors, target, len(vecs[0]), collection)
    points = []
    for i, (text, vec) in enumerate(zip(items, vecs)):
        points.append({
            "id": ids[i] if ids else str(_uuid.uuid4()),
            "vector": vec if target is None else {target: vec},
            "payload": {"document": text, "metadata": metadata[i] if metadata else None},
        })
    _result(data_call("PUT", f"/collections/{_col(collection)}/points", params={"wait": "true"},
                      body={"points": points}, cluster_id=cluster_id))
    return {"stored": len(points), "collection": collection, "collection_created": created,
            "ids": [p["id"] for p in points]}


@tool
def find_text(collection: str, query: str, limit: int = 5, filter: Optional[dict] = None,
              score_threshold: Optional[float] = None, cluster_id: Optional[str] = None) -> Any:
    """Semantic search by plain text: embeds the query locally and returns the closest stored texts with score and
    metadata. Works on collections made by store_text (or by the official mcp-server-qdrant with the same model).
    Optional Qdrant filter narrows candidates, e.g. {"must":[{"key":"metadata.topic","match":{"value":"billing"}}]}."""
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_TEXT_CHARS:
        raise QdrantError("query must be a non-empty string.")
    _limit(limit)
    name = vector_name()
    vectors = _vectors_config(collection, cluster_id)
    if vectors is None:
        raise QdrantError(f"Collection {collection!r} does not exist. Use store_text first or list_collections.")
    target = _target_vector(vectors, name, collection)
    qvec = embed([query])[0]
    _check_size(vectors, target, len(qvec), collection)
    body: dict[str, Any] = {"query": qvec, "limit": limit, "with_payload": True}
    if target is not None:
        body["using"] = segment(target, "vector name")
    if filter:
        body["filter"] = filter
    if score_threshold is not None:
        body["score_threshold"] = score_threshold
    res = _result(data_call("POST", f"/collections/{_col(collection)}/points/query", body=body, cluster_id=cluster_id))
    points = res.get("points", []) if isinstance(res, dict) else res
    return [{"id": p.get("id"), "score": p.get("score"),
             "document": (p.get("payload") or {}).get("document"),
             "metadata": (p.get("payload") or {}).get("metadata")} for p in points]
