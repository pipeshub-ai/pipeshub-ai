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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.retrieval.record_query.evaluate import Row

NEWEST = "newest"
OLDEST = "oldest"
_MAX_CURSOR_CHARS = 512
INVALID_CURSOR = "cursor is not one this tool printed for this sort; copy it exactly or omit it"

SortKey = tuple[Any, ...]
ValueOf = Callable[[Any, str], Any]


def sort_key(row: Row, sort: str, value_of: ValueOf) -> SortKey:
    """The row's place in ``sort``: ``newest``, ``oldest``, or ``"<field> asc|desc"``.
    Ties go newest first, then by id; a row without the field comes after
    every row with it, either way, since unknown is neither high nor low."""
    newest = -int(row.get("sortTs") or 0)
    record_id = str(row.get("_key") or "")
    if sort == NEWEST:
        return newest, record_id
    if sort == OLDEST:
        return -newest, record_id
    name, direction = sort.split(" ")
    value = value_of(row, name)
    if value is None:
        return 1, 0, newest, record_id
    return 0, float(value) if direction == "asc" else -float(value), newest, record_id


def _key_types(sort: str) -> tuple[type, ...]:
    return (int, str) if sort in (NEWEST, OLDEST) else (int, float, int, str)


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
            and (kind is float or float(value).is_integer())
        )
        if not ok:
            raise ValueError(INVALID_CURSOR)
    return key


def page(
    rows: list[Row], *, sort: str, cursor: str | None, limit: int, value_of: ValueOf,
) -> tuple[list[Row], int, str | None]:
    """The page of ``rows`` after ``cursor``, where it starts among them, and
    the cursor to continue with (None at the end)."""
    keyed = sorted(((sort_key(r, sort, value_of), r) for r in rows), key=lambda kr: kr[0])
    start = bisect.bisect_right([k for k, _ in keyed], decode_cursor(sort, cursor)) if cursor else 0
    shown = keyed[start:start + limit]
    more = start + len(shown) < len(keyed)
    return [r for _, r in shown], start, encode_cursor(sort, shown[-1][0]) if more and shown else None


__all__ = ["INVALID_CURSOR", "NEWEST", "OLDEST", "decode_cursor", "encode_cursor", "page", "sort_key"]
