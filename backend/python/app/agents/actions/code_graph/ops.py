"""Pure implementations behind the CodeGraph toolset.

Kept out of the decorated class so they can be tested directly, without the
``@tool`` wrapper in the way.

A symbol is addressed by ``(file_path, qualified_name)``. Both halves are load
bearing: ``qualified_name`` (``method:BaseClient.send``) is unique only within a
file, so the path is what disambiguates it -- two indexed files can each hold
``function:main``.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations

logger = logging.getLogger(__name__)

__all__ = [
    "CALL_RELATIONS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_NEIGHBOR_LIMIT",
    "SymbolRef",
    "find_call_neighbors_impl",
    "find_symbol_path_impl",
    "get_symbol_code_impl",
    "resolve_symbol",
]

# "Who calls this" is about calls, not about every way two symbols relate.
CALL_RELATIONS = [
    RecordRelations.CALLS.value,
    RecordRelations.INDIRECT_CALL.value,
]

DEFAULT_NEIGHBOR_LIMIT = 25
DEFAULT_MAX_DEPTH = 6
# Ceiling on nodes expanded per side, so one hub symbol cannot walk the repo.
_MAX_VISITED = 4000
_FRONTIER_LIMIT = 2000

_BLOCKS = CollectionNames.BLOCKS.value
_RECORDS = CollectionNames.RECORDS.value


class SymbolRef(dict):
    """A symbol as the model addresses it, plus what it needs to read one."""

    @staticmethod
    def from_block(block: dict[str, Any]) -> "SymbolRef":
        ref = SymbolRef(
            file_path=block.get("filePath"),
            qualified_name=block.get("qualifiedName"),
            kind=block.get("kind"),
        )
        if block.get("startLine") is not None:
            ref["start_line"] = block.get("startLine")
        if block.get("endLine") is not None:
            ref["end_line"] = block.get("endLine")
        return ref


async def resolve_symbol(
    graph_provider: Any,
    org_id: str,
    file_path: str,
    qualified_name: str,
    connector_id: str,
) -> dict[str, Any] | None:
    """Find the block node for ``(file_path, qualified_name)`` in one repo connector.

    Paths are repo-relative, so `(file_path, qualified_name)` is only unique within a
    connector -- two indexed repos can each hold `src/main.py#function:main`.

    Identity is case-sensitive because `Foo` and `foo` are different symbols in
    every language this indexes, but the lookup is not: a model retyping an
    address rather than copying it should not silently get "no such symbol".
    The exact match wins whenever there is one, so a file holding both spellings
    still resolves each correctly.
    """
    try:
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS,
            filters={
                "orgId": org_id,
                "filePath": file_path,
                "qualifiedName": qualified_name,
                "connectorId": connector_id,
            },
        )
        if rows:
            return rows[0]
        return await _resolve_case_insensitive(
            graph_provider, org_id, file_path, qualified_name, connector_id
        )
    except Exception as exc:
        logger.warning("Symbol lookup failed for %s#%s: %s", file_path, qualified_name, exc)
        return None


async def _resolve_case_insensitive(
    graph_provider: Any,
    org_id: str,
    file_path: str,
    qualified_name: str,
    connector_id: str,
) -> dict[str, Any] | None:
    """Fall back to a casefolded match among that file's blocks.

    Scoped to the one file, so this reads a handful of rows rather than scanning.
    Ambiguous when the file holds several spellings that differ only by case --
    there is no right answer then, so it resolves nothing rather than guess.
    """
    wanted = qualified_name.casefold()
    try:
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS,
            filters={"orgId": org_id, "filePath": file_path, "connectorId": connector_id},
        )
    except Exception as exc:
        logger.warning("Case-insensitive lookup failed for %s: %s", file_path, exc)
        return None
    matches = [r for r in (rows or []) if (r.get("qualifiedName") or "").casefold() == wanted]
    if len(matches) == 1:
        return matches[0]
    if matches:
        logger.debug("Ambiguous case-insensitive match for %s#%s", file_path, qualified_name)
    return None


async def _user_can_read(
    graph_provider: Any, user_id: str, org_id: str, record_id: str | None
) -> bool:
    """Gate every result on the caller's access to the owning record.

    A denial is reported by the caller as an empty result rather than an error:
    telling an agent that a record it cannot read exists is itself a leak.
    """
    if not record_id:
        return False
    if not user_id:
        return False
    try:
        access = await graph_provider.check_record_access_with_details(
            user_id=user_id, org_id=org_id, record_id=record_id
        )
    except Exception as exc:
        logger.warning("Access check failed for record %s: %s", record_id, exc)
        return False
    return bool(access)


async def _readable_blocks(
    graph_provider: Any,
    org_id: str,
    user_id: str,
    keys: list[str],
    allowed: dict[str, bool] | None = None,
    connector_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load block nodes by key, dropping any whose record the user cannot read.

    ``allowed`` memoises the per-record verdict; pass one across several calls in
    the same request to check each record once.

    ``connector_id`` re-applies the repo scope on nodes reached by traversal.
    Edges cannot span connectors today -- the edge builder indexes one
    ``recordGroupId`` at a time -- but that is an invariant of another module,
    and a scope boundary should not depend on one.
    """
    if not keys:
        return {}
    try:
        rows = await graph_provider.get_nodes_by_field_in(
            collection=_BLOCKS, field_name="_key", field_values=keys
        )
    except Exception as exc:
        logger.warning("Block batch load failed: %s", exc)
        return {}

    out: dict[str, dict[str, Any]] = {}
    allowed = {} if allowed is None else allowed
    for row in rows or []:
        if row.get("orgId") != org_id:
            continue
        if connector_id is not None and row.get("connectorId") != connector_id:
            continue
        record_id = row.get("recordId")
        if record_id not in allowed:
            allowed[record_id] = await _user_can_read(
                graph_provider, user_id, org_id, record_id
            )
        if allowed[record_id]:
            out[row.get("_key") or row.get("id")] = row
    return out


# ---------------------------------------------------------------------------
# Tool 1 — callers / callees
# ---------------------------------------------------------------------------

async def find_call_neighbors_impl(
    *,
    graph_provider: Any,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str,
    direction: str,
    limit: int = DEFAULT_NEIGHBOR_LIMIT,
) -> dict[str, Any]:
    if direction not in ("caller", "callee"):
        return {"error": "direction must be 'caller' or 'callee'"}

    anchor = await resolve_symbol(graph_provider, org_id, file_path, qualified_name, connector_id)
    if anchor is None:
        return {"error": f"No symbol {qualified_name!r} in {file_path!r}"}
    if not await _user_can_read(graph_provider, user_id, org_id, anchor.get("recordId")):
        return {"symbol": None, "direction": direction, "neighbors": []}

    # A caller points *at* this symbol, so the edge is inbound.
    edge_direction = "inbound" if direction == "caller" else "outbound"
    try:
        rows = await graph_provider.get_neighbors_by_relationship_types(
            node_key=anchor.get("_key") or anchor.get("id"),
            node_collection=_BLOCKS,
            relationship_types=CALL_RELATIONS,
            direction=edge_direction,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Neighbour walk failed")
        return {"error": f"Failed to walk the call graph: {exc}"}

    block_keys = [r["key"] for r in rows or [] if r.get("collection") == _BLOCKS and r.get("key")]
    blocks = await _readable_blocks(graph_provider, org_id, user_id, block_keys,
                                     connector_id=connector_id)

    neighbors: list[dict[str, Any]] = []
    for row in rows or []:
        block = blocks.get(row.get("key"))
        if block is None:
            continue
        ref = SymbolRef.from_block(block)
        ref["relation"] = row.get("relationshipType")
        neighbors.append(ref)

    return {
        "symbol": SymbolRef.from_block(anchor),
        "direction": direction,
        "neighbors": neighbors,
    }


# ---------------------------------------------------------------------------
# Tool 2 — the symbol's source
# ---------------------------------------------------------------------------

async def get_symbol_code_impl(
    *,
    graph_provider: Any,
    connector_id: str,
    blob_store: Any,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str,
) -> dict[str, Any]:
    block = await resolve_symbol(graph_provider, org_id, file_path, qualified_name, connector_id)
    if block is None:
        return {"error": f"No symbol {qualified_name!r} in {file_path!r}"}

    record_id = block.get("recordId")
    if not await _user_can_read(graph_provider, user_id, org_id, record_id):
        return {"error": f"No symbol {qualified_name!r} in {file_path!r}"}
    if blob_store is None:
        return {"error": "Blob storage is not available."}

    # The graph node deliberately carries no source: `data` is excluded from the
    # projection to keep traversed nodes small. The text lives in the blob.
    record_doc = await graph_provider.get_document(record_id, _RECORDS)
    virtual_record_id = (record_doc or {}).get("virtualRecordId")
    if not virtual_record_id:
        return {"error": f"No stored content for {file_path!r}"}

    try:
        record = await blob_store.get_record_from_storage(
            virtual_record_id=virtual_record_id, org_id=org_id
        )
    except Exception as exc:
        logger.warning("Blob fetch failed for %s: %s", virtual_record_id, exc)
        return {"error": f"Failed to read stored content for {file_path!r}"}
    if not record:
        return {"error": f"No stored content for {file_path!r}"}

    found = _find_blob_block(record, qualified_name)
    if found is None:
        return {"error": f"No stored content for symbol {qualified_name!r}"}

    ref = SymbolRef.from_block(block)
    ref["code"] = found
    return ref


def _find_blob_block(record: dict[str, Any], qualified_name: str) -> str | None:
    """Match a graph block to its blob twin on ``code_metadata.qualified_name``.

    The graph `_key` is a hash and never appears in the blob, and the blob's own
    block ids are uuid4 -- the qualified name is the only shared identity. The blob is
    snake_case (no alias generators on the block models).
    """
    containers = record.get("block_containers") or {}
    for bucket in ("blocks", "block_groups"):
        for item in containers.get(bucket) or []:
            meta = item.get("code_metadata") or {}
            if meta.get("qualified_name") != qualified_name:
                continue
            data = item.get("data")
            if isinstance(data, dict):
                return data.get("text")
            if isinstance(data, str):
                return data
    return None


# ---------------------------------------------------------------------------
# Tool 3 — how two symbols are connected
# ---------------------------------------------------------------------------

async def find_symbol_path_impl(
    *,
    graph_provider: Any,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path_a: str,
    qualified_name_a: str,
    file_path_b: str,
    qualified_name_b: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, Any]:
    start = await resolve_symbol(graph_provider, org_id, file_path_a, qualified_name_a, connector_id)
    if start is None:
        return {"error": f"No symbol {qualified_name_a!r} in {file_path_a!r}"}
    end = await resolve_symbol(graph_provider, org_id, file_path_b, qualified_name_b, connector_id)
    if end is None:
        return {"error": f"No symbol {qualified_name_b!r} in {file_path_b!r}"}

    for block in (start, end):
        if not await _user_can_read(graph_provider, user_id, org_id, block.get("recordId")):
            return {"found": False, "hops": []}

    start_key = start.get("_key") or start.get("id")
    end_key = end.get("_key") or end.get("id")
    if start_key == end_key:
        return {"found": True, "hops": []}

    edges = await _bfs_edges(
        graph_provider, start_key, end_key, max_depth=max_depth
    )
    if edges is None:
        return {"found": False, "hops": []}

    keys = {start_key, end_key}
    for edge in edges:
        keys.add(edge["from"])
        keys.add(edge["to"])
    blocks = await _readable_blocks(graph_provider, org_id, user_id, sorted(keys),
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
        })

    return {"found": True, "hops": hops}


async def _bfs_edges(
    graph_provider: Any,
    start_key: str,
    end_key: str,
    *,
    max_depth: int,
) -> list[dict[str, str]] | None:
    """Breadth-first search over every relation type, ignoring edge direction.

    Undirected on purpose: CONTAINS runs parent->child while CALLS runs
    caller->callee, so a strictly directed search finds nothing between two
    methods of the same class. Direction is still reported per hop.

    Done in Python rather than with a native shortest-path call so it behaves
    identically on Arango and Neo4j, and so each hop keeps the relation type and
    direction a native call would not return.
    """
    all_relations = [r.value for r in RecordRelations]
    parents: dict[str, tuple[str, str, str]] = {}
    visited = {start_key}
    frontier = [start_key]

    for _ in range(max_depth):
        if not frontier:
            break
        try:
            rows = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
                node_keys=frontier[:_FRONTIER_LIMIT],
                node_collection=_BLOCKS,
                relationship_types=all_relations,
                direction="any",
                limit=_FRONTIER_LIMIT,
            )
        except Exception as exc:
            logger.warning("Path BFS frontier failed: %s", exc)
            return None

        next_frontier: list[str] = []
        for row in rows or []:
            if row.get("collection") != _BLOCKS:
                continue
            key = row.get("key")
            anchor = row.get("anchorKey")
            if not key or not anchor or key in visited:
                continue
            visited.add(key)
            parents[key] = (anchor, row.get("relationshipType"), row.get("direction"))
            if key == end_key:
                return _unwind(parents, start_key, end_key)
            next_frontier.append(key)
            if len(visited) > _MAX_VISITED:
                logger.info("Path search hit the visited-node ceiling")
                return None
        frontier = next_frontier

    return None


def _unwind(
    parents: dict[str, tuple[str, str, str]], start_key: str, end_key: str
) -> list[dict[str, str]]:
    chain: deque[dict[str, str]] = deque()
    cursor = end_key
    while cursor != start_key:
        anchor, relation, direction = parents[cursor]
        chain.appendleft({
            "from": anchor,
            "to": cursor,
            "relation": relation,
            "direction": direction,
        })
        cursor = anchor
    return list(chain)
