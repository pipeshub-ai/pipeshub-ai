"""The code graph as a graph-view source: blocks (symbols) joined by CALLS, IMPORTS, INHERITS and the like."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from app.agents.actions.code_graph.neighbours import (
    Walk,
    _resolve_anchors,
    walk_neighbours,
)
from app.agents.actions.code_graph.ops import (
    CODE_RELATIONS,
    _readable_block_rows,
    _readable_blocks,
    _unwrap,
)
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes
from app.modules.code_graph.build_state import BuildStateStore, graph_status
from app.modules.parsers.code_parser.models import FILLER_KINDS

from ..models import Candidates, GraphEdge, GraphNode
from ..policy import BLOCK, node_id, parse_id

if TYPE_CHECKING:
    from app.agents.actions.code_graph.ops import RecordAccess
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from ..models import ExpandQuery, ResolveRequest

# Ranked low to high: a response says the least ready state among its repos.
_STATUS_ORDER = ("ready", "stale", "waiting_on_indexing", "building", "retrying",
                 "never_built", "too_large", "failed", "unknown")


def edge_id(src: str, dst: str, kind: str, line: int | None) -> str:
    return "e:" + hashlib.sha1(f"{src}|{dst}|{kind}|{line}".encode()).hexdigest()[:12]


def _node(block: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id(BLOCK, block.get("_key") or block.get("id")), kind=block.get("kind"),
        label=block.get("name") or block.get("qualifiedName"), recordId=block.get("recordId"),
        file=block.get("filePath"), line=block.get("startLine"),
    )


def _edges(walk: Walk) -> dict[str, tuple[GraphEdge, str, dict[str, Any]]]:
    """Edge id -> the edge, its far node's id and block, in walk order."""
    found: dict[str, tuple[GraphEdge, str, dict[str, Any]]] = {}
    for row in walk.rows:
        far, near = node_id(BLOCK, row["key"]), node_id(BLOCK, row["anchorKey"])
        src, dst = (near, far) if row.get("direction") == "outbound" else (far, near)
        edge = GraphEdge(
            id=edge_id(src, dst, row.get("relationshipType"), row.get("line")), from_=src, to=dst,
            type=row.get("relationshipType"), confidence=row.get("confidence"), line=row.get("line"),
        )
        found.setdefault(edge.id, (edge, far, walk.blocks[row["key"]]))
    return found


def _file_order(block: dict[str, Any]) -> tuple[bool, int, str]:
    """A file's own definitions first, in file order; the key breaks a tie, so which block a bare file resolves to
    never depends on the order a store returned the rows in."""
    return ((block.get("kind") or "").lower() in FILLER_KINDS, block.get("startLine") or 0,
            str(block.get("_key") or block.get("id") or ""))


class CodeSource:
    scope = OAuthScopes.CODE_READ.value

    def __init__(self, graph_provider: IGraphDBProvider, org_id: str, user_id: str) -> None:
        self._graph = graph_provider
        self._org_id = org_id
        self._user_id = user_id

    async def expand(self, q: ExpandQuery, access: RecordAccess) -> Candidates:
        parsed = [p for nid in q.anchors if (p := parse_id(nid))]
        keys = [key for kind, key in parsed if kind == BLOCK]
        loaded = await _readable_blocks(self._graph, self._org_id, access, keys)
        # In the order asked: a store returns a batch in no order we may depend on.
        anchors = {k: loaded[k] for k in dict.fromkeys(keys) if k in loaded}
        by_connector: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in anchors.values():
            by_connector[block.get("connectorId")].append(block)

        out = Candidates(anchors=[node_id(BLOCK, k) for k in anchors])
        nodes = {n.id: n for n in map(_node, anchors.values())}
        edges: dict[str, GraphEdge] = {}
        for connector_id, group in sorted(by_connector.items(), key=lambda kv: str(kv[0])):
            walk = await walk_neighbours(
                self._graph, self._org_id, self._user_id, group,
                relations=list(q.edge_types or CODE_RELATIONS), direction=q.direction,
                connector_id=connector_id, include_tests=q.include_tests,
                row_limit=q.rows, access=access,
            )
            out.truncated = out.truncated or walk.fanout_capped
            out.incomplete = out.incomplete or walk.incomplete
            for edge, far, block in _edges(walk).values():
                edges.setdefault(edge.id, edge)
                nodes.setdefault(far, _node(block))
            # A later connector's rows wait until this one is read out, or they would shift as a larger read fills it in.
            if walk.fanout_capped:
                break
        out.nodes, out.edges = list(nodes.values()), list(edges.values())
        out.graph_status = await self._status({b.get("recordGroupId") for b in anchors.values()})
        return out

    async def resolve(
        self, req: ResolveRequest, connector_id: str, group_id: str, access: RecordAccess,
    ) -> str | None:
        if req.recordId:
            rows = await self._graph.get_nodes_by_field_in(
                collection=CollectionNames.BLOCKS.value, field_name="recordId", field_values=[req.recordId],
                return_fields=["_key"], raise_on_error=True,
            ) or []
            keys = [k for row in rows if (k := (_unwrap(row).get("_key") or _unwrap(row).get("id")))]
            # The org, connector and readable checks are the shared helper's; a store failure raises out of it.
            found = await _readable_block_rows(
                self._graph, self._org_id, access, keys, connector_id,
                extra_fields=("kind", "startLine", "qualifiedName"),
            )
            blocks = [{**row, "_key": key} for key, row in found.items()
                      if not req.qualifiedName or row.get("qualifiedName") == req.qualifiedName]
            blocks.sort(key=_file_order)
        else:
            blocks, _ = await _resolve_anchors(
                self._graph, self._org_id, req.filePath, req.qualifiedName, connector_id,
                repo=group_id, access=access,
            )
            if not req.qualifiedName:  # a named symbol keeps the order of the match (exact before looser)
                blocks.sort(key=_file_order)
        return node_id(BLOCK, blocks[0].get("_key") or blocks[0].get("id")) if blocks else None

    async def _status(self, group_ids: set[str | None]) -> str:
        store = BuildStateStore(self._graph)
        states = []
        for group_id in sorted(g for g in group_ids if g):
            try:
                states.append(graph_status(await store.read(self._org_id, group_id)).value)
            except Exception:  # a build state we cannot read must not fail a read of the graph
                states.append("unknown")
        return max(states, key=_STATUS_ORDER.index, default="ready")
