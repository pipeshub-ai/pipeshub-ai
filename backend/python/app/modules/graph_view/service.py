"""Expand and revalidate: validate, ask the policy, read through the source, shape.

Hidden and missing look the same everywhere in here, and nothing a hidden node
holds changes a response. The routes are thin; this is the only place the two
operations are defined.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import replace
from typing import TYPE_CHECKING

from .models import (
    MAX_READ_ROWS,
    Anchor,
    Candidates,
    ExpandQuery,
    ExpandRequest,
    ExpandResponse,
    GraphNode,
    Page,
    ResolveRequest,
    RevalidateRequest,
)
from .policy import ALLOWED_EDGE_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agents.actions.code_graph.ops import RecordAccess

    from .policy import VisibilityPolicy
    from .ports import GraphSource

# A cursor re-walks from the top, so the offset it names is bounded by the most rows a read takes.
MAX_OFFSET = MAX_READ_ROWS
_CURSOR = re.compile(r"[A-Za-z0-9_-]{1,32}")

NOT_FOUND = "No readable node matches the request."
BAD_CURSOR = "Invalid cursor."


class GraphViewError(Exception):
    """A refusal the route answers as ``status`` with ``detail``; the words never depend on hidden data."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"v1:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        if not _CURSOR.fullmatch(cursor):
            raise ValueError
        version, _, offset = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode().partition(":")
        if version != "v1" or not offset.isascii() or not offset.isdigit() or int(offset) > MAX_OFFSET:
            raise ValueError
        return int(offset)
    except (ValueError, binascii.Error):
        raise GraphViewError(400, BAD_CURSOR) from None


class GraphViewService:
    def __init__(self, sources: Mapping[str, GraphSource]) -> None:
        self._sources = sources

    def scope_for(self, source: str) -> str:
        return self._sources[source].scope

    async def expand(self, req: ExpandRequest, *, access: RecordAccess, policy: VisibilityPolicy) -> dict:
        if set(req.edgeTypes or ()) - ALLOWED_EDGE_TYPES:
            raise GraphViewError(400, f"Unknown edgeTypes. Valid: {', '.join(sorted(ALLOWED_EDGE_TYPES))}")
        offset = decode_cursor(req.cursor)
        ids = list(dict.fromkeys(req.nodeIds))
        shown = await policy.visible(ids)
        anchors = [i for i in ids if i in shown]
        if not anchors:
            # Every anchor hidden, missing or malformed: one answer.
            raise GraphViewError(404, NOT_FOUND)
        q = ExpandQuery(anchors, req.direction, req.edgeTypes, req.includeTests, req.limit, offset)
        return _shape(await self._read(self._sources[req.source], q, access, policy), q).model_dump(
            by_alias=True, exclude_none=True)

    async def _read(
        self, source: GraphSource, q: ExpandQuery, access: RecordAccess, policy: VisibilityPolicy,
    ) -> Candidates:
        """Enough trimmed rows to show the page and to know whether another shown edge follows it.

        A read stops at ``rows`` readable rows, so a page costs what it shows, not the hub's degree. Fewer
        edges can come out of those rows: parallel edges and an edge between two anchors merge, and the policy
        drops what a connected app's limit leaves out. So a read that stopped at its limit without one edge
        past the page is read again with a larger one (the rows read first are the same ones), up to the most a
        read takes. The page is then judged on shown edges alone, whatever lay outside the limit.
        """
        rows = q.offset + q.limit + 1
        while True:
            found = await policy.trim(await source.expand(replace(q, rows=rows), access))
            if not found.truncated or len(found.edges) > q.offset + q.limit or rows >= MAX_READ_ROWS:
                return found
            rows = min(2 * rows, MAX_READ_ROWS)

    async def resolve(
        self, req: ResolveRequest, connector_id: str, group_id: str, *, access: RecordAccess, policy: VisibilityPolicy,
    ) -> dict:
        nid = await self._sources[req.source].resolve(req, connector_id, group_id, access)
        if not nid or nid not in await policy.visible([nid]):
            raise GraphViewError(404, NOT_FOUND)
        return {"nodeId": nid}

    async def revalidate(self, req: RevalidateRequest, *, policy: VisibilityPolicy) -> dict:
        ids = list(dict.fromkeys(req.nodeIds))
        shown = await policy.visible(ids)
        return {"visible": [i for i in ids if i in shown]}


def _shape(c: Candidates, q: ExpandQuery) -> ExpandResponse:
    """The page ``q`` asks for.

    There is more when an edge follows the page. A read that stopped at the most rows a read takes can also
    say so, but never on an empty page. ``total`` counts the anchor's edges in the read, at most up to the
    first edge after the page, so it is exact only when there is no more and is otherwise a lower bound that
    does not depend on how many rows the read took to get there.
    """
    page = c.edges[q.offset:q.offset + q.limit]
    more = len(c.edges) > q.offset + q.limit or (c.truncated and bool(page))
    counted = c.edges[:q.offset + q.limit + 1] if more else c.edges
    totals = {a: sum(1 for e in counted if a in (e.from_, e.to)) for a in c.anchors}
    by_id: dict[str, GraphNode] = {n.id: n for n in c.nodes}
    next_offset = q.offset + len(page)
    ends = dict.fromkeys([*c.anchors, *(end for e in page for end in (e.from_, e.to))])
    return ExpandResponse(
        nodes=[by_id[i] for i in ends if i in by_id],
        edges=page,
        anchors=[Anchor(id=a, total=t) for a, t in totals.items()],
        page=Page(truncated=more, incomplete=c.incomplete,
                  nextCursor=encode_cursor(next_offset) if more and page and next_offset <= MAX_OFFSET else None),
        graphStatus=c.graph_status,
    )
