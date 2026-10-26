"""A Knowledge Hub listing read inside a bound the listing query cannot apply.

A bound below app level (a connected app limited to some folders or files, a
saved agent's or a project's sources) names nodes the listing queries have no
filter for. Its rows are read from the listing's own pages and the rows outside
the bound dropped, so a page of the source is not a page of the answer: the
pages are refilled from the source until they are full, and nothing counted
over the source is reported as a count of the answer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

    from app.connectors.sources.localKB.api.knowledge_hub_models import (
        KnowledgeHubNodesResponse,
        NodeItem,
    )

#: Rows read from the source per call, and calls per page asked for.
BOUNDED_BATCH = 50
BOUNDED_MAX_BATCHES = 6


@dataclass(frozen=True)
class Position:
    """Where a read of the source resumes: the batch that holds the next row
    (by the source's own cursor, or by page number when it hands out none) and
    how many of that batch's rows were already read."""

    cursor: str | None = None
    page: int = 1
    skip: int = 0


@dataclass
class BoundedRead:
    """The admitted rows one read found, in order.

    ``after[i]`` is where the rows after ``kept[i]`` start, None when the
    source holds nothing after it. ``stop`` is where the read stopped short of
    its goal, None when it reached its goal or the source ran out.
    """

    kept: list[NodeItem] = field(default_factory=list)
    after: list[Position | None] = field(default_factory=list)
    stop: Position | None = None
    #: The source ran out: no admitted row follows the last one kept.
    exhausted: bool = False
    #: The first batch, for what a page carries besides its rows (filters,
    #: permissions); the failure itself when a batch failed.
    first: KnowledgeHubNodesResponse | None = None
    failed: bool = False


async def read_inside(
    fetch: Callable[..., Awaitable[KnowledgeHubNodesResponse]],
    inside: Callable[[list[str]], Awaitable[set[str]]],
    *,
    start: Position,
    want: int,
) -> BoundedRead:
    """Up to ``want`` rows ``inside`` admits, read from ``start`` a whole batch
    at a time and for at most ``BOUNDED_MAX_BATCHES`` batches.

    ``fetch(position, first=...)`` reads ``BOUNDED_BATCH`` rows at ``position``;
    ``first`` is True for the first batch only, so what is read once per page
    (filter options, permissions) is not read again per batch.
    """
    read = BoundedRead()
    position = start
    for batch in range(BOUNDED_MAX_BATCHES):
        response = await fetch(position, first=batch == 0)
        if read.first is None:
            read.first = response
        if not response.success:
            return BoundedRead(first=response, failed=True)
        rows = (response.items or [])[position.skip:]
        has_next = bool(response.pagination and response.pagination.hasNext)
        following = Position(
            cursor=response.pagination.nextCursor if response.pagination else None,
            page=position.page + 1,
        ) if has_next else None
        admitted = await inside([item.id for item in rows]) if rows else set()
        for offset, item in enumerate(rows, start=1):
            if item.id in admitted:
                read.kept.append(item)
                last = offset == len(rows)
                read.after.append(
                    following if last else Position(position.cursor, position.page, position.skip + offset),
                )
        if following is None:
            read.exhausted = True
            return read
        if len(read.kept) >= want:
            return read
        position = following
    read.stop = position
    return read


_CURSOR_PREFIX = "lim1."

#: The request parameters a cursor carries as the request sent them, so the
#: cursor alone resumes the same listing, as the service's own cursors do.
#: They are parsed and validated again on every request that brings them back.
CARRIED_TEXT = (
    "q", "sort_by", "sort_order", "node_types", "record_types", "origins", "connector_ids",
    "indexing_status", "created_at", "updated_at", "size",
)
CARRIED_FLAGS = ("only_containers", "flattened")


@dataclass(frozen=True)
class BoundedCursor:
    mode: str
    #: Rows before the page.
    seen: int
    #: Where the page starts.
    position: Position
    #: The listing's parameters (``CARRIED_TEXT`` and ``CARRIED_FLAGS``);
    #: one left out was not sent.
    params: dict[str, str | bool] = field(default_factory=dict)
    #: ``bound_key`` of the limit the listing was read under.
    bound: str | None = None


def bound_key(sources: Mapping[str, Iterable[str]] | None) -> str:
    """A short, stable name for a connection's limit. A cursor carries it, so a
    page read under one limit is not resumed under another: its offset and
    position count rows of the first."""
    canonical = {key: sorted(set(values or ())) for key, values in sorted((sources or {}).items())}
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()[:16]


def encode_bounded_cursor(
    mode: str, seen: int, position: Position | None = None, params: Mapping[str, object] | None = None,
    *, bound: str | None = None,
) -> str:
    """A next-page cursor for a listing read inside a bound. Not signed, and
    need not be: every row it leads to is checked against the bound again, so
    an edited one can only skip rows inside it or change what is asked for
    inside it. The source cursor it carries is signed by the source."""
    payload: dict[str, object] = {"m": mode, "n": seen}
    if position is not None:
        payload.update(c=position.cursor, p=position.page, s=position.skip)
    carried = {k: v for k, v in (params or {}).items() if k in (*CARRIED_TEXT, *CARRIED_FLAGS) and v is not None}
    if carried:
        payload["f"] = carried
    if bound is not None:
        payload["b"] = bound
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return _CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def is_bounded_cursor(token: str | None) -> bool:
    return bool(token) and token.startswith(_CURSOR_PREFIX)


def _valid_params(params: object) -> bool:
    if not isinstance(params, dict):
        return False
    return all(
        (key in CARRIED_TEXT and isinstance(value, str)) or (key in CARRIED_FLAGS and isinstance(value, bool))
        for key, value in params.items()
    )


def decode_bounded_cursor(token: str) -> BoundedCursor:
    """Raises:
        ValueError: the token is not one ``encode_bounded_cursor`` made.
    """
    if not is_bounded_cursor(token):
        raise ValueError("not a cursor of this listing")
    body = token[len(_CURSOR_PREFIX):]
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("unreadable cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("unreadable cursor")
    mode, seen = payload.get("m"), payload.get("n")
    cursor, page, skip = payload.get("c"), payload.get("p", 1), payload.get("s", 0)
    params = payload.get("f", {})
    bound = payload.get("b")
    valid = (
        (bound is None or isinstance(bound, str)) and
        mode in ("browse", "search")
        and all(isinstance(v, int) and not isinstance(v, bool) for v in (seen, page, skip))
        and seen >= 0 and page >= 1 and skip >= 0
        and (cursor is None or isinstance(cursor, str))
        and _valid_params(params)
    )
    if not valid:
        raise ValueError("unreadable cursor")
    return BoundedCursor(mode, seen, Position(cursor=cursor, page=page, skip=skip), params, bound)
