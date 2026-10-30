"""find_symbol_path: how one symbol reaches another, hop by hop."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import RecordRelations

from .ops import (
    _BLOCKS,
    CODE_RELATIONS,
    STRUCTURAL_RELATIONS,
    TEST_ROLE,
    RecordAccess,
    SymbolRef,
    _readable_blocks,
    _readable_keys,
    coverage_for,
    get_record_roles,
    resolve_symbol,
)
from .results import touched

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_DEPTH", "MAX_PATH_DEPTH", "find_symbol_path_impl"]

# One graph query per hop, so the model cannot buy an unbounded scan by
# asking for a deep path.
MAX_PATH_DEPTH = 8
# The search stops at the first path, so only a missing one pays for every hop; a
# shallower default made a 7-hop call chain read as "no path".
DEFAULT_MAX_DEPTH = MAX_PATH_DEPTH
# Ceiling on nodes expanded per side, so one hub symbol cannot walk the repo.
_MAX_VISITED = 4000
_FRONTIER_LIMIT = 2000
# What `Foo()` runs: a Python, then a JS/TS constructor.
_CONSTRUCTORS = frozenset({"__init__", "constructor"})

# ---------------------------------------------------------------------------
# Tool 3 — how two symbols are connected
# ---------------------------------------------------------------------------

async def find_symbol_path_impl(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path_a: str,
    qualified_name_a: str,
    file_path_b: str,
    qualified_name_b: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
    edge_types: list[str] | None = None,
    include_tests: bool = False,
    undirected: bool = False,
    repo: str | None = None,
) -> dict[str, Any]:
    """How a reaches b: calls, imports and heritage followed the way they point.

    Containment is walked both ways, so two methods of one class still connect
    through it. When a does not reach b, b reaching a is tried and reported as
    such. ``undirected`` ignores edge direction entirely; it answers "are these
    related at all", and a shared caller is enough to connect two symbols.
    """
    unknown = [e for e in (edge_types or []) if e not in CODE_RELATIONS]
    if unknown:
        return {"error": (
            f"unknown edge_types {unknown}. Valid: {', '.join(CODE_RELATIONS)}"
        )}

    max_depth = max(1, min(int(max_depth or DEFAULT_MAX_DEPTH), MAX_PATH_DEPTH))

    access = RecordAccess(graph_provider, org_id, user_id)
    start, end = await asyncio.gather(
        resolve_symbol(graph_provider, org_id, file_path_a, qualified_name_a, connector_id,
                       repo=repo, access=access),
        resolve_symbol(graph_provider, org_id, file_path_b, qualified_name_b, connector_id,
                       repo=repo, access=access),
    )
    # A missing endpoint and a denied one share the denial's payload.
    if start is None or end is None:
        return {"found": False, "hops": []}

    readable = await access.readable([start.get("recordId"), end.get("recordId")])
    if start.get("recordId") not in readable or end.get("recordId") not in readable:
        return {"found": False, "hops": []}

    if not include_tests:
        check_ids = {r for r in (start.get("recordId"), end.get("recordId")) if r}
        roles = await get_record_roles(graph_provider, org_id, check_ids)
        for sym in (start, end):
            if roles.get(sym.get("recordId")) == TEST_ROLE:
                return {
                    "found": False, "hops": [],
                    "note": "Endpoint is in a test file; set include_tests=true to include test files",
                }

    start_key = start.get("_key") or start.get("id")
    end_key = end.get("_key") or end.get("id")
    if start_key == end_key:
        return {"found": True, "hops": []}

    def readable(keys: list[str]) -> Awaitable[set[str]]:
        return _readable_keys(graph_provider, org_id, access, keys)

    # `Foo()` is an edge to the class, and a path does not step from a class it
    # reached into its members: a path to a constructor is a path to its class,
    # then the one step down into it.
    target_key, constructor_hop = end_key, None
    if not undirected and end.get("name") in _CONSTRUCTORS and end.get("parentBlockId"):
        rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
            node_keys=[end["parentBlockId"]], node_collection=_BLOCKS,
            relationship_types=list(STRUCTURAL_RELATIONS), direction="outbound",
            limit=_FRONTIER_LIMIT, raise_on_error=True,
        ) or []
        relation = next((r.get("relationshipType") for r in rows if r.get("key") == end_key), None)
        if relation:
            target_key = end["parentBlockId"]
            constructor_hop = {"from": target_key, "to": end_key, "relation": relation,
                               "direction": "outbound"}

    if start_key == target_key:
        edges, capped = [], False
    else:
        edges, capped = await _bfs_edges(
            graph_provider, start_key, target_key, max_depth=max_depth, relations=edge_types,
            undirected=undirected, readable=readable,
        )
    if edges is not None and constructor_hop:
        edges = [*edges, constructor_hop]
    reversed_path = False
    if edges is None and not undirected:
        edges, back_capped = await _bfs_edges(
            graph_provider, end_key, start_key, max_depth=max_depth, relations=edge_types,
            readable=readable,
        )
        capped = capped or back_capped
        reversed_path = edges is not None
    if edges is None:
        missed: dict[str, Any] = touched({"found": False, "hops": [], "searched_hops": max_depth},
                                         [start, end])
        if gap := coverage_for([start, end]):
            missed["coverage"] = gap
        if capped:
            missed["capped"] = True
            missed["note"] = (
                "The search hit its traversal cap before exhausting the graph, so "
                "no path here does not prove there is none. Narrow with "
                "`edge_types`, or start from symbols closer together."
            )
        return missed

    keys = {start_key, end_key}
    for edge in edges:
        keys.add(edge["from"])
        keys.add(edge["to"])
    blocks = await _readable_blocks(graph_provider, org_id, access, sorted(keys),
                                     connector_id=connector_id)

    hops: list[dict[str, Any]] = []
    for edge in edges:
        src = blocks.get(edge["from"])
        dst = blocks.get(edge["to"])
        if src is None or dst is None:
            # A hop through something the caller cannot read is not a path they
            # are allowed to see.
            return {"found": False, "hops": []}
        hops.append({
            "from": SymbolRef.from_block(src),
            "to": SymbolRef.from_block(dst),
            "relation": edge["relation"],
            "direction": edge["direction"],
            "explain": _explain_hop(src, dst, edge),
        })

    found: dict[str, Any] = touched(
        {"found": True, "connector_id": connector_id, "hops": hops}, [start, end],
    )
    if reversed_path:
        found["reversed"] = True
        found["note"] = (
            f"{qualified_name_a} does not reach {qualified_name_b}; the path runs "
            "the other way, from the second symbol to the first."
        )
    if capped:
        found["capped"] = True
    return found


async def _bfs_edges(
    graph_provider: IGraphDBProvider,
    start_key: str,
    end_key: str,
    *,
    max_depth: int,
    relations: list[str] | None = None,
    undirected: bool = False,
    readable: Callable[[list[str]], Awaitable[set[str]]] | None = None,
) -> tuple[list[dict[str, str]] | None, bool]:
    """Breadth-first search from ``start_key`` towards ``end_key``.

    Defaults to all of `CODE_RELATIONS`, structural edges included. Two methods
    of the same class are connected only through their container, so
    narrowing this to the cross-file edges finds no path between them.

    A directed path has one shape: out to the symbol's containers (and down into
    one of their members), then along cross-file edges the way they point.
    Walking containment freely connected a method to whatever its sibling
    methods call; stepping down after a call made a call into a class (its
    constructor) a call of every method it has; and walking calls both ways
    connected two helpers through any caller they share. ``undirected`` drops
    the shape and the direction entirely.

    Done in Python rather than with a native shortest-path call so it behaves
    identically on Arango and Neo4j, and so each hop keeps the relation type and
    direction a native call would not return.

    ``readable`` keeps the search to blocks the caller may read, so a path
    through a hidden file neither shows nor hides a readable one (S-7).

    Returns ``(edges, capped)``. ``capped`` is set whenever a frontier or row
    cap bit, so the caller can say that an absent path is unproven.
    """
    all_relations = list(relations) if relations else list(CODE_RELATIONS)
    start = (start_key, _CLIMBING)
    parents: dict[tuple[str, int], tuple[tuple[str, int], str, str]] = {}
    visited = {start}
    frontier = [start]
    capped = False

    for _ in range(max_depth):
        if not frontier:
            break
        if len(frontier) > _FRONTIER_LIMIT:
            capped = True
        frontier = frontier[:_FRONTIER_LIMIT]
        phases_by_key: dict[str, list[int]] = {}
        for key, phase in frontier:
            phases_by_key.setdefault(key, []).append(phase)
        rows, hit_cap = await _neighbour_rows(graph_provider, phases_by_key, all_relations,
                                              undirected=undirected)
        capped = capped or hit_cap
        # The shape rules first, then the access check on what survives: checking
        # every neighbour of a hub (thousands of callers a directed walk drops anyway)
        # made each hop one large permission query (B2: up to 43 s on a 20k-file repo).
        candidates = [
            row for row in rows
            if row.get("collection") == _BLOCKS and row.get("key") and row.get("anchorKey")
            and any(
                (step := _next_phase(phase, row, undirected=undirected)) is not None
                and (row["key"], step) not in visited
                for phase in phases_by_key.get(row["anchorKey"], ())
            )
        ]
        allowed = None
        if readable is not None:
            allowed = await readable([row["key"] for row in candidates])
        rows = candidates

        next_frontier: list[tuple[str, int]] = []
        for row in rows:
            if row.get("collection") != _BLOCKS:
                continue
            key = row.get("key")
            if allowed is not None and key not in allowed:
                continue
            anchor = row.get("anchorKey")
            if not key or not anchor:
                continue
            for phase in phases_by_key.get(anchor, ()):
                step = _next_phase(phase, row, undirected=undirected)
                if step is None:
                    continue
                state = (key, step)
                if state in visited:
                    continue
                visited.add(state)
                parents[state] = ((anchor, phase), row.get("relationshipType"),
                                  row.get("direction"))
                if key == end_key:
                    return _unwind(parents, start, state), capped
                next_frontier.append(state)
                if len(visited) > _MAX_VISITED:
                    logger.info("Path search hit the visited-node ceiling")
                    return None, True
        frontier = next_frontier

    return None, capped


async def _neighbour_rows(
    graph_provider: IGraphDBProvider,
    phases_by_key: dict[str, list[int]],
    relations: list[str],
    *,
    undirected: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """The frontier's neighbours a walk in this shape can step to, and whether a cap bit.

    A directed walk only ever takes outbound edges, plus inbound structural ones
    while climbing out of the start symbol's containers (`_next_phase`), so it
    asks for just those instead of every edge both ways.
    """
    # A provider failure propagates: `_run` turns it into an error, whereas
    # `found: False` here would be read as "no path exists".
    async def fetch(keys: list[str], rels: list[str], direction: str) -> list[dict[str, Any]]:
        return list(await graph_provider.get_neighbors_for_nodes_by_relationship_types(
            node_keys=keys, node_collection=_BLOCKS, relationship_types=rels,
            direction=direction, limit=_FRONTIER_LIMIT, raise_on_error=True,
        ) or [])

    if undirected:
        batches = [await fetch(list(phases_by_key), relations, "any")]
    else:
        batches = [await fetch(list(phases_by_key), relations, "outbound")]
        climbing = [key for key, phases in phases_by_key.items() if _CLIMBING in phases]
        structural = [rel for rel in relations if rel in STRUCTURAL_RELATIONS]
        if climbing and structural:
            batches.append(await fetch(climbing, structural, "inbound"))
    return [row for batch in batches for row in batch], any(len(b) >= _FRONTIER_LIMIT for b in batches)


# Where a directed path is in its shape: climbing out of the start symbol's
# containers, crossing along outbound edges, or descending into a container.
_CLIMBING, _CROSSING, _DESCENDING = 0, 1, 2


def _next_phase(phase: int, row: dict[str, Any], *, undirected: bool) -> int | None:
    if undirected:
        return _CLIMBING
    structural = row.get("relationshipType") in STRUCTURAL_RELATIONS
    outbound = row.get("direction") == "outbound"
    if structural and not outbound:
        return _CLIMBING if phase == _CLIMBING else None
    if structural:
        # A call into a class is its constructor, not a call of each of its methods,
        # so only a path still climbing out of the start may step down; once down,
        # it may go on through nested definitions.
        if phase == _DESCENDING:
            return _DESCENDING if row.get("relationshipType") == RecordRelations.DEFINES.value else None
        return _DESCENDING if phase == _CLIMBING else None
    if outbound and phase != _DESCENDING:
        return _CROSSING
    return None


_HOP_VERBS = {
    RecordRelations.CALLS.value: "calls",
    RecordRelations.IMPORTS.value: "imports",
    RecordRelations.IMPORTS_FROM.value: "imports from",
    RecordRelations.RE_EXPORTS.value: "re-exports",
    RecordRelations.EXPORTS.value: "exports",
    RecordRelations.INHERITS.value: "inherits from",
    RecordRelations.IMPLEMENTS.value: "implements",
    RecordRelations.OVERRIDES.value: "overrides",
    RecordRelations.CONTAINS.value: "contains",
    RecordRelations.METHOD.value: "has method",
    RecordRelations.DEFINES.value: "defines",
}


def _explain_hop(src: dict[str, Any], dst: dict[str, Any], edge: dict[str, str]) -> str:
    """One sentence per hop, in the direction the edge points."""
    head, tail = (src, dst) if edge.get("direction") != "inbound" else (dst, src)
    verb = _HOP_VERBS.get(edge.get("relation") or "", (edge.get("relation") or "").lower())
    return f"{head.get('qualifiedName')} {verb} {tail.get('qualifiedName')}"


def _unwind(
    parents: dict[tuple[str, int], tuple[tuple[str, int], str, str]],
    start: tuple[str, int],
    end: tuple[str, int],
) -> list[dict[str, str]]:
    chain: deque[dict[str, str]] = deque()
    cursor = end
    while cursor != start:
        previous, relation, direction = parents[cursor]
        chain.appendleft({
            "from": previous[0],
            "to": cursor[0],
            "relation": relation,
            "direction": direction,
        })
        cursor = previous
    return list(chain)
