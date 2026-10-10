"""Data-plane helpers shared by data.py and text.py. Importing this registers no tools."""
from __future__ import annotations

from typing import Any, Union
from urllib.parse import quote

from .common import QdrantError, segment

PointId = Union[int, str]


def _col(name: str) -> str:
    return quote(segment(name, "collection"), safe="")


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
