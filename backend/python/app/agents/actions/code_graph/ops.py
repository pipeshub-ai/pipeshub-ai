"""Pure implementations behind the CodeGraph toolset.

Kept out of the decorated class so they can be tested directly, without the
``@tool`` wrapper in the way.

A symbol is addressed by ``(file_path, qualified_name)``. Both halves are load
bearing: ``qualified_name`` (``method:BaseClient.send``) is unique only within a
file, so the path is what disambiguates it -- two indexed files can each hold
``function:main``.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, RecordRelations

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

__all__ = [
    "CODE_RELATIONS",
    "CROSS_FILE_RELATIONS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_LINES",
    "DEFAULT_NEIGHBOR_LIMIT",
    "MAX_NEIGHBOUR_DEPTH",
    "STRUCTURAL_RELATIONS",
    "TEST_ROLE",
    "SymbolRef",
    "attach_file_paths",
    "find_symbol_path_impl",
    "get_accessible_record_ids",
    "get_neighbour_impl",
    "get_record_roles",
    "read_code_impl",
    "resolve_symbol",
]

TEST_ROLE = "test"

# Written at parse time, both endpoints inside one file. These describe how a
# file is put together, which is `list_code`'s question, not a dependency.
STRUCTURAL_RELATIONS = [
    RecordRelations.CONTAINS.value,
    RecordRelations.DEFINES.value,
    RecordRelations.METHOD.value,
]

# Written by the edge-resolution pass once a repo is indexed: one symbol
# reaching another. `get_neighbour` defaults to these -- including METHOD would
# make "what calls this class" answer with the class's own methods.
CROSS_FILE_RELATIONS = [
    RecordRelations.CALLS.value,
    RecordRelations.IMPORTS.value,
    RecordRelations.IMPORTS_FROM.value,
    RecordRelations.RE_EXPORTS.value,
    RecordRelations.EXPORTS.value,
    RecordRelations.INHERITS.value,
    RecordRelations.EXTENDS.value,
]

# Everything the code parser emits. `find_symbol_path` walks all of it: two
# methods of the same class are connected only through their container, so
# dropping the structural edges there breaks the search (see `_bfs_edges`).
CODE_RELATIONS = [*STRUCTURAL_RELATIONS, *CROSS_FILE_RELATIONS]

DEFAULT_NEIGHBOR_LIMIT = 25
DEFAULT_MAX_DEPTH = 6
# A neighbour walk is for tracing a specific chain, not surveying the repo:
# fan-out compounds per hop, so a deep walk returns more than it explains.
MAX_NEIGHBOUR_DEPTH = 3
# Frontier width per hop while walking past the first. The caller's `limit`
# bounds what comes back; this bounds what is traversed to find it.
_NEIGHBOUR_FANOUT = 200
# Default ceiling on a whole-file read. Enough for ~95% of files outright, and
# it is the hub files an architecture question lands on that overrun it -- which
# is exactly where an uncapped read costs thousands of tokens of context.
DEFAULT_MAX_LINES = 600
# Ceiling on nodes expanded per side, so one hub symbol cannot walk the repo.
_MAX_VISITED = 4000
_FRONTIER_LIMIT = 2000

_BLOCKS = CollectionNames.BLOCKS.value
_RECORDS = CollectionNames.RECORDS.value
_CODE_FILES = CollectionNames.CODE_FILES.value


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
        record_ids = await _records_for_path(graph_provider, org_id, file_path)
        if not record_ids:
            return None
        for record_id in record_ids:
            rows = await graph_provider.get_nodes_by_filters(
                collection=_BLOCKS,
                filters={
                    "orgId": org_id,
                    "recordId": record_id,
                    "qualifiedName": qualified_name,
                    "connectorId": connector_id,
                },
            )
            if rows:
                return _with_file_path(rows[0], file_path)
        return await _resolve_case_insensitive(
            graph_provider, org_id, record_ids, file_path, qualified_name, connector_id
        )
    except Exception as exc:
        logger.warning("Symbol lookup failed for %s#%s: %s", file_path, qualified_name, exc)
        return None


async def _records_for_path(
    graph_provider: Any, org_id: str, file_path: str
) -> list[str]:
    """Record ids for a repo-relative path.

    The path is a property of the file, not of its symbols, so it is stored once
    on ``codeFiles`` (keyed by record id) rather than on every block. Normally one
    id; more than one means two connectors indexed the same path, and the caller
    narrows by connector.
    """
    rows = await graph_provider.get_nodes_by_filters(
        collection=_CODE_FILES,
        filters={"orgId": org_id, "filePath": file_path},
        return_fields=["_key"],
    )
    return [key for row in rows or [] if (key := row.get("_key") or row.get("id"))]


def _with_file_path(block: dict[str, Any], file_path: str | None) -> dict[str, Any]:
    """Attach the owning record's path so callers can address the block."""
    block["filePath"] = file_path
    return block


async def _resolve_case_insensitive(
    graph_provider: Any,
    org_id: str,
    record_ids: list[str],
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
    rows: list[dict[str, Any]] = []
    try:
        for record_id in record_ids:
            rows.extend(await graph_provider.get_nodes_by_filters(
                collection=_BLOCKS,
                filters={"orgId": org_id, "recordId": record_id, "connectorId": connector_id},
            ) or [])
    except Exception as exc:
        logger.warning("Case-insensitive lookup failed for %s: %s", file_path, exc)
        return None
    matches = [r for r in rows if (r.get("qualifiedName") or "").casefold() == wanted]
    if len(matches) == 1:
        return _with_file_path(matches[0], file_path)
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
    accessible: set[str] | None = None,
    connector_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load block nodes by key, dropping any whose record the user cannot read.

    ``accessible`` is the pre-computed set of record IDs the user may read
    (from ``get_accessible_record_ids``).  When provided the access check is an
    O(1) set lookup per block instead of a graph traversal per record.  When
    ``None`` the function falls back to parallel per-record checks.

    ``connector_id`` re-applies the repo scope on nodes reached by traversal.
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

    candidates: list[dict[str, Any]] = []
    for row in rows or []:
        if row.get("orgId") != org_id:
            continue
        if connector_id is not None and row.get("connectorId") != connector_id:
            continue
        candidates.append(row)

    if accessible is not None:
        out = {
            (row.get("_key") or row.get("id")): row
            for row in candidates
            if row.get("recordId") in accessible
        }
    else:
        unique_rids = list({r.get("recordId") for r in candidates if r.get("recordId")})
        verdicts = await asyncio.gather(*(
            _user_can_read(graph_provider, user_id, org_id, rid)
            for rid in unique_rids
        ))
        allowed = dict(zip(unique_rids, verdicts))
        out = {
            (row.get("_key") or row.get("id")): row
            for row in candidates
            if allowed.get(row.get("recordId"), False)
        }

    await attach_file_paths(graph_provider, org_id, out.values())
    return out


async def attach_file_paths(
    graph_provider: Any, org_id: str, blocks: "Iterable[dict[str, Any]]"
) -> None:
    """Fill in each block's ``filePath`` from its owning record.

    The path is stored once per file on ``codeFiles``, so it is joined here
    rather than copied onto every block. Every tool reaches its blocks through
    this or ``resolve_symbol``, which is what lets the rest of the code-graph
    code keep reading ``block["filePath"]`` unchanged.
    """
    blocks = list(blocks)
    record_ids = sorted({b.get("recordId") for b in blocks if b.get("recordId")})
    if not record_ids:
        return
    try:
        paths = await graph_provider.get_file_paths_for_records(
            org_id=org_id, record_ids=record_ids
        )
    except Exception as exc:
        logger.warning("File path lookup failed: %s", exc)
        return
    for block in blocks:
        block["filePath"] = paths.get(block.get("recordId"))


async def get_record_roles(
    graph_provider: Any, org_id: str, record_ids: set[str] | list[str]
) -> dict[str, str]:
    """``fileRole`` per record, read from ``codeFiles``."""
    if not record_ids:
        return {}
    ids = sorted(record_ids)
    try:
        rows = await graph_provider.get_nodes_by_field_in(
            collection=_CODE_FILES,
            field_name="_key",
            field_values=ids,
            return_fields=["_key", "fileRole"],
        )
    except Exception as exc:
        logger.warning("File role lookup failed: %s", exc)
        return {}
    out: dict[str, str] = {}
    for raw in rows or []:
        row = raw
        if isinstance(raw, dict) and len(raw) <= 2 and ("b" in raw or "node" in raw):
            row = raw.get("b") or raw.get("node") or raw
        key = row.get("_key") or row.get("id")
        role = row.get("fileRole")
        if key and role:
            out[key] = role
    return out


async def get_accessible_record_ids(
    graph_provider: Any, org_id: str, user_id: str
) -> set[str] | None:
    """Every record the caller may read, in one bulk query.

    Backed by ``get_accessible_virtual_record_ids`` (which itself sits behind a
    Redis cache with a 300 s TTL), so repeated calls in the same agent turn are
    near-free.  Returns ``None`` when the bulk lookup is unavailable; callers
    must fall back to per-record ``_user_can_read`` in that case.
    """
    try:
        mapping = await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id
        )
    except Exception as exc:
        logger.warning("Bulk permission lookup failed: %s", exc)
        return None
    return {r for r in (mapping or {}).values() if r}


# ---------------------------------------------------------------------------
# Tool 1 — callers / callees
# ---------------------------------------------------------------------------

async def get_neighbour_impl(
    *,
    graph_provider: Any,
    connector_id: str,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str,
    direction: str = "any",
    edge_types: list[str] | None = None,
    depth: int = 1,
    limit: int = DEFAULT_NEIGHBOR_LIMIT,
    include_tests: bool = False,
) -> dict[str, Any]:
    if direction not in ("inbound", "outbound", "any"):
        return {"error": "direction must be 'inbound', 'outbound' or 'any'"}

    # Rejected, not filtered. Dropping an unrecognized type and carrying on
    # would answer a typo (`['CALL']`) with every relation instead of none --
    # a narrower request silently becoming the broadest possible one.
    unknown = [e for e in (edge_types or []) if e not in CODE_RELATIONS]
    if unknown:
        return {"error": (
            f"unknown edge_types {unknown}. Valid: {', '.join(CODE_RELATIONS)}"
        )}
    relations = list(edge_types) if edge_types else list(CROSS_FILE_RELATIONS)
    depth = max(1, min(int(depth or 1), MAX_NEIGHBOUR_DEPTH))

    anchor, accessible = await asyncio.gather(
        resolve_symbol(graph_provider, org_id, file_path, qualified_name, connector_id),
        get_accessible_record_ids(graph_provider, org_id, user_id),
    )
    if anchor is None:
        return {"error": f"No symbol {qualified_name!r} in {file_path!r}"}

    anchor_rid = anchor.get("recordId")
    empty = {"symbol": None, "direction": direction, "neighbors": []}
    if accessible is not None:
        if anchor_rid not in accessible:
            return empty
    elif not await _user_can_read(graph_provider, user_id, org_id, anchor_rid):
        return empty

    anchor_key = anchor.get("_key") or anchor.get("id")
    visited = {anchor_key}
    frontier = [anchor_key]
    rows: list[dict[str, Any]] = []
    for hop in range(1, depth + 1):
        if not frontier:
            break
        try:
            batch = await graph_provider.get_neighbors_for_nodes_by_relationship_types(
                node_keys=frontier[:_NEIGHBOUR_FANOUT],
                node_collection=_BLOCKS,
                relationship_types=relations,
                direction=direction,
                limit=_NEIGHBOUR_FANOUT,
            )
        except Exception as exc:
            logger.exception("Neighbour walk failed")
            return {"error": f"Failed to walk the code graph: {exc}"}

        next_frontier: list[str] = []
        for row in batch or []:
            key = row.get("key")
            if not key:
                continue
            row["hop"] = hop
            rows.append(row)
            if row.get("collection") == _BLOCKS and key not in visited:
                visited.add(key)
                next_frontier.append(key)
        frontier = next_frontier

    block_keys = [r["key"] for r in rows if r.get("collection") == _BLOCKS and r.get("key")]
    blocks = await _readable_blocks(graph_provider, org_id, user_id, block_keys,
                                     accessible=accessible, connector_id=connector_id)

    if not include_tests:
        rid_set = {b.get("recordId") for b in blocks.values() if b.get("recordId")}
        roles = await get_record_roles(graph_provider, org_id, rid_set)
        blocks = {k: b for k, b in blocks.items()
                  if roles.get(b.get("recordId")) != TEST_ROLE}

    neighbors: list[dict[str, Any]] = []
    for row in rows:
        # `limit` bounds what is returned, and rows arrive breadth-first, so a
        # truncated walk keeps the nearest neighbours rather than an arbitrary
        # slice of the far ones.
        if len(neighbors) >= limit:
            break
        block = blocks.get(row.get("key"))
        if block is None:
            continue
        ref = SymbolRef.from_block(block)
        ref["relation"] = row.get("relationshipType")
        if depth > 1:
            ref["hop"] = row.get("hop")
        if row.get("line") is not None:
            ref["line"] = row.get("line")
        if row.get("confidence"):
            ref["confidence"] = row.get("confidence")
        neighbors.append(ref)

    return {
        "symbol": SymbolRef.from_block(anchor),
        "connector_id": connector_id,
        "direction": direction,
        "edge_types": relations,
        "depth": depth,
        "neighbors": neighbors,
        # Silence reads as absence: a model that thinks it saw every neighbour
        # will state a wrong conclusion confidently.
        "truncated": len(neighbors) >= limit,
    }


# ---------------------------------------------------------------------------
# Tool 2 — the symbol's source
# ---------------------------------------------------------------------------

async def read_code_impl(
    *,
    graph_provider: Any,
    connector_id: str,
    blob_store: Any,
    org_id: str,
    user_id: str,
    file_path: str,
    qualified_name: str | None = None,
    lines: str | None = None,
    max_lines: int | None = None,
    include_tests: bool = False,
) -> dict[str, Any]:
    """Source for one symbol, one line range, or a whole file.

    All three modes cost the same single blob fetch -- ``get_record_from_storage``
    returns every block of the file regardless -- so reading five symbols from
    one file should be one call, not five downloads of the same blob.

    ``max_lines`` bounds a whole-file read and applies by default, so asking for
    a file is safe without knowing its size. It is a budget, not a window: the
    caller says how much it can afford, and the file says where that lands. A
    line range is for when the caller already knows *which* part it wants.
    """
    if qualified_name:
        block, accessible = await asyncio.gather(
            resolve_symbol(graph_provider, org_id, file_path, qualified_name, connector_id),
            get_accessible_record_ids(graph_provider, org_id, user_id),
        )
        if block is None:
            return {"error": f"No symbol {qualified_name!r} in {file_path!r}"}
        record_id = block.get("recordId")
    else:
        block = None
        record_ids, accessible = await asyncio.gather(
            _records_for_path(graph_provider, org_id, file_path),
            get_accessible_record_ids(graph_provider, org_id, user_id),
        )
        record_id = await _record_in_connector(
            graph_provider, org_id, record_ids, connector_id
        )
        if record_id is None:
            return {"error": f"No indexed file at {file_path!r}"}

    if accessible is not None:
        if record_id not in accessible:
            return {"error": f"No indexed file at {file_path!r}"}
    elif not await _user_can_read(graph_provider, user_id, org_id, record_id):
        return {"error": f"No indexed file at {file_path!r}"}

    if not include_tests:
        roles = await get_record_roles(graph_provider, org_id, {record_id})
        if roles.get(record_id) == TEST_ROLE:
            return {"error": f"{file_path!r} is a test file; set include_tests=true to read it"}

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

    # `_record_id` is an internal handle the tool wrapper strips before the
    # model sees it: record-escalation keys on record ids, but every code tool
    # addresses files by path, and carrying both identities to the model invites
    # passing the wrong one.
    if qualified_name:
        found = _find_blob_block(record, qualified_name)
        if found is None:
            return {"error": f"No stored content for symbol {qualified_name!r}"}
        ref = SymbolRef.from_block(block)
        ref["connector_id"] = connector_id
        ref["code"] = found
        ref["_record_id"] = record_id
        return ref

    span = _parse_line_range(lines)
    if lines and span is None:
        return {"error": f"lines must look like '40-80', got {lines!r}"}
    budget = DEFAULT_MAX_LINES if max_lines is None else max(1, int(max_lines))
    out = _file_code(record, file_path, connector_id, span, budget)
    out["_record_id"] = record_id
    return out


async def _record_in_connector(
    graph_provider: Any, org_id: str, record_ids: list[str], connector_id: str
) -> str | None:
    """Pick the record from *record_ids* that belongs to this connector."""
    if not record_ids:
        return None
    for record_id in record_ids:
        rows = await graph_provider.get_nodes_by_filters(
            collection=_BLOCKS,
            filters={"orgId": org_id, "recordId": record_id, "connectorId": connector_id},
        )
        if rows:
            return record_id
    return None


def _parse_line_range(lines: str | None) -> tuple[int, int] | None:
    """``"40-80"`` -> ``(40, 80)``; ``None`` for absent, invalid stays ``None``."""
    if not lines:
        return None
    head, sep, tail = lines.partition("-")
    if not sep:
        head = tail = lines
    try:
        start, end = int(head.strip()), int(tail.strip())
    except ValueError:
        return None
    if start < 1 or end < start:
        return None
    return start, end


def _file_code(
    record: dict[str, Any],
    file_path: str,
    connector_id: str,
    span: tuple[int, int] | None,
    budget: int,
) -> dict[str, Any]:
    """Every block of a file, in source order, clipped to a range and a budget.

    Blocks tile the file exactly, so concatenating them reconstructs it; both
    the range and the budget keep whole blocks rather than slicing text, so
    every returned fragment is still a complete symbol.
    """
    containers = record.get("block_containers") or {}
    items: list[tuple[int, dict[str, Any]]] = []
    for bucket in ("blocks", "block_groups"):
        for item in containers.get(bucket) or []:
            meta = item.get("code_metadata") or {}
            start = meta.get("start_line") or 0
            end = meta.get("end_line") or start
            if span and (end < span[0] or start > span[1]):
                continue
            data = item.get("data")
            text = data.get("text") if isinstance(data, dict) else None
            if not text:
                continue
            items.append((start, {
                "qualified_name": meta.get("qualified_name"),
                "kind": meta.get("kind"),
                "start_line": start,
                "end_line": end,
                "code": text,
            }))
    items.sort(key=lambda pair: pair[0])
    kept: list[dict[str, Any]] = []
    spent = 0
    for _, entry in items:
        length = max(1, (entry["end_line"] or 0) - (entry["start_line"] or 0) + 1)
        # Always keep the first block: a budget smaller than the opening symbol
        # should still return that symbol rather than nothing.
        if kept and spent + length > budget:
            break
        kept.append(entry)
        spent += length
    truncated = len(kept) < len(items)
    out = {
        "file_path": file_path,
        "connector_id": connector_id,
        "lines": f"{span[0]}-{span[1]}" if span else None,
        "truncated": truncated,
        "blocks": kept,
    }
    if truncated:
        resume = kept[-1]["end_line"] if kept else 0
        out["next"] = (
            f"Stopped at line {resume} of {items[-1][1]['end_line']}. Continue "
            f"with lines='{(resume or 0) + 1}-{items[-1][1]['end_line']}', or "
            "raise max_lines."
        )
    return out


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
    edge_types: list[str] | None = None,
    include_tests: bool = False,
) -> dict[str, Any]:
    unknown = [e for e in (edge_types or []) if e not in CODE_RELATIONS]
    if unknown:
        return {"error": (
            f"unknown edge_types {unknown}. Valid: {', '.join(CODE_RELATIONS)}"
        )}

    start, end, accessible = await asyncio.gather(
        resolve_symbol(graph_provider, org_id, file_path_a, qualified_name_a, connector_id),
        resolve_symbol(graph_provider, org_id, file_path_b, qualified_name_b, connector_id),
        get_accessible_record_ids(graph_provider, org_id, user_id),
    )
    if start is None:
        return {"error": f"No symbol {qualified_name_a!r} in {file_path_a!r}"}
    if end is None:
        return {"error": f"No symbol {qualified_name_b!r} in {file_path_b!r}"}

    for block in (start, end):
        rid = block.get("recordId")
        if accessible is not None:
            if rid not in accessible:
                return {"found": False, "hops": []}
        elif not await _user_can_read(graph_provider, user_id, org_id, rid):
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

    edges = await _bfs_edges(
        graph_provider, start_key, end_key, max_depth=max_depth, relations=edge_types
    )
    if edges is None:
        return {"found": False, "hops": []}

    keys = {start_key, end_key}
    for edge in edges:
        keys.add(edge["from"])
        keys.add(edge["to"])
    blocks = await _readable_blocks(graph_provider, org_id, user_id, sorted(keys),
                                     accessible=accessible, connector_id=connector_id)

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

    return {"found": True, "connector_id": connector_id, "hops": hops}


async def _bfs_edges(
    graph_provider: Any,
    start_key: str,
    end_key: str,
    *,
    max_depth: int,
    relations: list[str] | None = None,
) -> list[dict[str, str]] | None:
    """Breadth-first search over every code relation, ignoring edge direction.

    Defaults to all of `CODE_RELATIONS`, structural edges included -- unlike
    `get_neighbour`, which excludes them. Two methods of the same class are
    connected only through their container, so narrowing this to the cross-file
    edges finds no path between them.

    Undirected on purpose: CONTAINS runs parent->child while CALLS runs
    caller->callee, so a strictly directed search finds nothing between two
    methods of the same class. Direction is still reported per hop.

    Done in Python rather than with a native shortest-path call so it behaves
    identically on Arango and Neo4j, and so each hop keeps the relation type and
    direction a native call would not return.
    """
    all_relations = list(relations) if relations else list(CODE_RELATIONS)
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
