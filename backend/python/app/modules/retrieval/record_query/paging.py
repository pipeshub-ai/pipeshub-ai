"""Paging a record query's matched rows with a keyset cursor.

A cursor names the last row of the previous page by its place in the sort
order (``"<sortTs>:<id>"`` for newest first), so a record added, changed or
removed between calls neither repeats a row nor skips one, and nothing is
held between calls. It is opaque to the model: base64 of the key, tied to the
sort it was made for. A cursor is only a place in the order, never a record
the caller may see: a forged one starts the page somewhere else among rows
the access check already admitted.
"""

from __future__ import annotations

import base64
import binascii
import bisect
import json
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.retrieval.record_query.evaluate import Row

NEWEST = "newest"
_MAX_CURSOR_CHARS = 512
INVALID_CURSOR = "cursor is not one this tool printed for this sort; copy it exactly or omit it"

SortKey = tuple[Any, ...]


def sort_key(row: Row, sort: str = NEWEST) -> SortKey:
    """The row's place in ``sort``: newest first, ties broken by id."""
    return -int(row.get("sortTs") or 0), str(row.get("_key") or "")


def _key_types(sort: str) -> tuple[type, ...]:
    return int, str


def encode_cursor(sort: str, key: SortKey) -> str:
    raw = json.dumps([sort, *key], separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(sort: str, cursor: str) -> SortKey:
    """The key ``cursor`` names; ValueError when it is not one made for ``sort``."""
    try:
        if len(cursor) > _MAX_CURSOR_CHARS:
            raise ValueError
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(INVALID_CURSOR) from exc
    types = _key_types(sort)
    if not isinstance(decoded, list) or len(decoded) != len(types) + 1 or decoded[0] != sort:
        raise ValueError(INVALID_CURSOR)
    key = tuple(decoded[1:])
    for value, kind in zip(key, types, strict=True):
        ok = isinstance(value, str) if kind is str else (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
        if not ok:
            raise ValueError(INVALID_CURSOR)
    return key


def page(
    rows: list[Row], *, sort: str, cursor: str | None, limit: int,
) -> tuple[list[Row], int, str | None]:
    """The page of ``rows`` after ``cursor``, where it starts among them, and
    the cursor to continue with (None at the end)."""
    ordered = sorted(rows, key=lambda r: sort_key(r, sort))
    start = 0
    if cursor:
        keys = [sort_key(r, sort) for r in ordered]
        start = bisect.bisect_right(keys, decode_cursor(sort, cursor))
    shown = ordered[start:start + limit]
    more = start + len(shown) < len(ordered)
    return shown, start, encode_cursor(sort, sort_key(shown[-1], sort)) if more and shown else None


__all__ = ["INVALID_CURSOR", "NEWEST", "decode_cursor", "encode_cursor", "page", "sort_key"]
