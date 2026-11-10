"""The only place that decides whether a node or an edge is shown.

Sources read and walk; they never decide. Everything a response holds went
through ``trim``, and ``visible`` answers the same question for ids a client
kept from an earlier response. Both use the code tools' own entry points
(``RecordAccess`` and the light block reads), so no permission query exists
that those do not already run. A verdict lives as long as its ``RecordAccess``,
which a route builds per request.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.agents.actions.code_graph.ops import (
    CODE_RELATIONS,
    TEST_ROLE,
    RecordAccess,
    _readable_block_rows,
    get_record_roles,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from .models import Candidates, NodeId

BLOCK = "block"

# Edge types a client may be shown. Permission, group-membership and every other
# relation are absent on purpose: a new relation stays hidden until it is listed here.
ALLOWED_EDGE_TYPES = frozenset(CODE_RELATIONS)

_BATCH = 1000


def node_id(kind: str, key: str) -> NodeId:
    return f"{kind}:{key}"


def parse_id(nid: str) -> tuple[str, str] | None:
    kind, _, key = nid.partition(":")
    return (kind, key) if kind == BLOCK and key else None


class VisibilityPolicy:
    def __init__(
        self, graph_provider: IGraphDBProvider, org_id: str, access: RecordAccess, *,
        include_tests: bool = False,
        outside_limit: Callable[[set[str]], Awaitable[set[str]]] | None = None,
        groups_outside: Callable[[dict[str, str]], Awaitable[set[str]]] | None = None,
    ) -> None:
        self._graph = graph_provider
        self._org_id = org_id
        self._access = access
        self._include_tests = include_tests
        # Of the record ids given, those a connected app's knowledge limit leaves out.
        self._outside_limit = outside_limit
        # The same for record groups (id -> app id), judged by where a group sits, as the code-graph routes do.
        self._groups_outside = groups_outside

    async def trim(self, c: Candidates) -> Candidates:
        """``c`` without the nodes and edges the caller may not see.

        An edge stays only when its type is allowed, both ends are visible and
        one end is a visible anchor (a walk starts from a visible node). A node
        stays only as an anchor or an end of a kept edge.
        """
        shown = await self.visible({n.id for n in c.nodes} | set(c.anchors))
        anchors = [a for a in c.anchors if a in shown]
        start = set(anchors)
        edges = [e for e in c.edges
                 if e.type in ALLOWED_EDGE_TYPES and e.from_ in shown and e.to in shown
                 and (e.from_ in start or e.to in start)]
        keep = start | {end for e in edges for end in (e.from_, e.to)}
        return replace(c, anchors=anchors, edges=edges, nodes=[n for n in c.nodes if n.id in keep])

    async def visible(self, ids: Iterable[NodeId]) -> set[NodeId]:
        """The ids among ``ids`` the caller may see now. Unknown, malformed, hidden and missing are alike."""
        wanted = list(dict.fromkeys(ids))
        out: set[NodeId] = set()
        for i in range(0, len(wanted), _BATCH):
            out |= await self._visible_batch(wanted[i:i + _BATCH])
        return out

    async def _visible_batch(self, ids: list[NodeId]) -> set[NodeId]:
        keys = [p[1] for nid in ids if (p := parse_id(nid))]
        return {node_id(BLOCK, k) for k in await self._blocks(keys)}

    async def _blocks(self, keys: list[str]) -> set[str]:
        rows = await _readable_block_rows(
            self._graph, self._org_id, self._access, keys, extra_fields=("recordGroupId",),
        )
        if rows and self._outside_limit and self._groups_outside:
            # The repo is what a limit names; a block with none is judged by its file.
            groups = {r["recordGroupId"]: r.get("connectorId") for r in rows.values() if r.get("recordGroupId")}
            files = {r["recordId"] for r in rows.values() if not r.get("recordGroupId")}
            outside = await self._groups_outside(groups) | await self._outside_limit(files)
            rows = {k: r for k, r in rows.items() if (r.get("recordGroupId") or r["recordId"]) not in outside}
        if rows and not self._include_tests:
            roles = await get_record_roles(self._graph, self._org_id, {r["recordId"] for r in rows.values()})
            rows = {k: r for k, r in rows.items() if roles.get(r["recordId"]) != TEST_ROLE}
        return set(rows)
