"""Project a parsed code file's blocks into ArangoDB nodes and local edges.

Writes the structural relations whose endpoints are both known while parsing a
single file -- CONTAINS, METHOD, DEFINES. Cross-file relations are carried on
each node as ``pendingEdges`` and resolved later by ``edge_builder``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.models.blocks import Block, BlockGroup, BlocksContainer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "BlockProjectionContext",
    "PARSER_SOURCE",
    "block_key_for",
    "serialize_block_for_graph",
    "write_code_file_blocks_to_graph",
]

PARSER_SOURCE = "pipeshub_parser"
_STRUCTURE_CONSTRAINT_PREFIX = "structure"

# A projected block should be well under this. Exceeding it means pendingEdges
# grew unexpectedly, which matters because these are traversed nodes and there
# is no configured ArangoDB document-size headroom in this deployment.
_NODE_SIZE_WARN_BYTES = 10240

_WRITE_CHUNK = 500
_RETRY_DELAYS = (0.1, 0.2, 0.4)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlockProjectionContext:
    org_id: str
    record_id: str
    record_group_id: str | None
    connector_id: str | None
    file_path: str | None
    language: str | None = None


def block_key_for(record_group_id: str | None, record_id: str, qualified_name: str) -> str:
    """Deterministic Arango ``_key`` for a symbol.

    Scoped by *record* rather than by file path. The qualified name is only
    unique within a file, so something must disambiguate it -- and the record
    survives a move where the path does not: the connector detects renames
    (`_reconcile_sha_moves`) and `on_records_moved` reuses the existing vertex,
    keeping the same id. A path-derived key would change on every rename, and
    since a pure rename emits no re-index event, nothing would ever repair it.

    Deterministic so re-runs produce identical keys and existing CONTAINS edges
    stay valid.
    """
    digest = hashlib.sha256(
        f"{record_group_id or ''}:{record_id}:{qualified_name}".encode("utf-8")
    )
    return digest.hexdigest()[:32]


def _now_ms() -> int:
    return int(time.time() * 1000)


def serialize_block_for_graph(
    item: Block | BlockGroup,
    ctx: BlockProjectionContext,
    *,
    block_id: str,
    is_block_group: bool,
    parent_block_id: str | None,
) -> dict[str, Any]:
    """The single place a block becomes a graph document.

    Every writer goes through here. Letting each writer stringify enums on its
    own is how field-name drift starts.
    """
    meta = item.code_metadata
    now = _now_ms()
    doc: dict[str, Any] = {
        "id": block_id,
        "orgId": ctx.org_id,
        "recordId": ctx.record_id,
        "recordGroupId": ctx.record_group_id,
        "connectorId": ctx.connector_id,
        "filePath": ctx.file_path,
        "name": item.name,
        "index": item.index,
        "isBlockGroup": is_block_group,
        "isExternal": False,
        "parentBlockId": parent_block_id,
        "contentHash": item.content_hash,
        "source": PARSER_SOURCE,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
    }
    if item.sub_type is not None:
        doc["subType"] = item.sub_type.value
    if meta is not None:
        doc["kind"] = (meta.kind or "").lower() or None
        doc["qualifiedName"] = meta.qualified_name
        doc["startLine"] = meta.start_line
        doc["endLine"] = meta.end_line
        doc["language"] = meta.language or ctx.language
        if meta.pending_edges:
            doc["pendingEdges"] = meta.pending_edges
        if meta.pending_edges_truncated:
            doc["pendingEdgesTruncated"] = True
        if meta.type_table:
            doc["typeTable"] = meta.type_table
    return doc


def _assert_projection_size(doc: dict[str, Any], log: logging.Logger) -> None:
    encoded = json.dumps(doc, default=str).encode("utf-8")
    if len(encoded) <= _NODE_SIZE_WARN_BYTES:
        return
    sizes = {
        key: len(json.dumps(value, default=str).encode("utf-8"))
        for key, value in doc.items()
    }
    largest = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:5]
    log.warning(
        "Oversized block projection: recordId=%s blockId=%s bytes=%d largest=%s",
        doc.get("recordId"), doc.get("id"), len(encoded), largest,
    )


async def _with_retry(coro_factory, *, what: str, log: logging.Logger):
    last: Exception | None = None
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 - retried then re-raised
            last = exc
            if delay is None:
                break
            log.warning("%s failed (attempt %d), retrying in %.1fs: %s", what, attempt + 1, delay, exc)
            await asyncio.sleep(delay)
    raise last  # type: ignore[misc]


def _chunks(items: list, size: int = _WRITE_CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


async def write_code_file_blocks_to_graph(
    *,
    graph_provider: IGraphDBProvider,
    context: BlockProjectionContext,
    block_containers: BlocksContainer,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """Replace this record's blocks and their structural edges.

    Returns counters: blocks_written, edges_written, blocks_removed.
    """
    log = logger or globals()["logger"]
    if not block_containers or (not block_containers.blocks and not block_containers.block_groups):
        return {"blocks_written": 0, "edges_written": 0, "blocks_removed": 0}

    docs: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    record_key = f"{CollectionNames.RECORDS.value}/{context.record_id}"

    group_ids: dict[int, str] = {}
    for group in block_containers.block_groups:
        meta = group.code_metadata
        if meta is None or not meta.qualified_name:
            continue
        gid = block_key_for(context.record_group_id, context.record_id, meta.qualified_name)
        group_ids[group.index] = gid
        doc = serialize_block_for_graph(
            group, context, block_id=gid, is_block_group=True, parent_block_id=None
        )
        _assert_projection_size(doc, log)
        docs.append(doc)
        edges.append(_structural_edge(record_key, gid, RecordRelations.CONTAINS.value, context))

    # Ids before edges: a nested block names its parent block by index, and the
    # parent is not guaranteed to have been serialized yet.
    block_ids: dict[int, str] = {}
    for block in block_containers.blocks:
        meta = block.code_metadata
        qualified_name = meta.qualified_name if meta else None
        if not qualified_name:
            # The file-summary block has no symbol of its own; give it a fixed
            # identity so it still lands and can carry the file's type table.
            qualified_name = "file_summary:1"
        block_ids[block.index] = block_key_for(
            context.record_group_id, context.record_id, qualified_name
        )

    for block in block_containers.blocks:
        meta = block.code_metadata
        bid = block_ids[block.index]
        # A definition nested in another definition hangs off that block; a member
        # of a top-level type hangs off its group. The parser sets one or the
        # other, never both.
        parent_bid = (
            block_ids.get(block.parent_block_index)
            if block.parent_block_index is not None else None
        )
        parent_gid = group_ids.get(block.parent_index) if block.parent_index is not None else None
        parent_id = parent_bid or parent_gid
        doc = serialize_block_for_graph(
            block, context, block_id=bid, is_block_group=False, parent_block_id=parent_id
        )
        _assert_projection_size(doc, log)
        docs.append(doc)

        parent_key = (
            f"{CollectionNames.BLOCKS.value}/{parent_id}" if parent_id else record_key
        )
        relation = _local_relation_for(
            meta.kind if meta else None, parent_id is not None,
            nested=parent_bid is not None,
        )
        edges.append(_structural_edge(parent_key, bid, relation, context))

    fresh_ids = {doc["id"] for doc in docs}

    existing = await _with_retry(
        lambda: graph_provider.get_nodes_by_filters(
            collection=CollectionNames.BLOCKS.value,
            filters={"recordId": context.record_id, "source": PARSER_SOURCE},
            return_fields=["_key"],
        ),
        what="load existing blocks", log=log,
    ) or []
    existing_ids = {row.get("_key") or row.get("id") for row in existing}
    stale_ids = [bid for bid in existing_ids - fresh_ids if bid]

    # Edges first, then nodes. Deleting nodes alone leaves orphaned CONTAINS
    # edges behind on every rename, which accumulate silently.
    if stale_ids:
        await _with_retry(
            lambda: _delete_edges_for_blocks(graph_provider, stale_ids),
            what="delete stale block edges", log=log,
        )
        await _with_retry(
            lambda: graph_provider.delete_nodes(stale_ids, CollectionNames.BLOCKS.value),
            what="delete stale blocks", log=log,
        )

    for chunk in _chunks(docs):
        await _with_retry(
            lambda c=chunk: graph_provider.batch_upsert_nodes(c, CollectionNames.BLOCKS.value),
            what="upsert blocks", log=log,
        )
    for chunk in _chunks(edges):
        await _with_retry(
            lambda c=chunk: graph_provider.batch_upsert_record_relations(c),
            what="upsert structural edges", log=log,
        )

    return {
        "blocks_written": len(docs),
        "edges_written": len(edges),
        "blocks_removed": len(stale_ids),
    }


def _local_relation_for(kind: str | None, has_parent: bool, *, nested: bool = False) -> str:
    """Structural relation pointing from a block's parent to the block.

    ``nested`` means the parent is a block rather than a group -- a definition
    inside another definition, which the enclosing one defines.
    """
    if not has_parent:
        return RecordRelations.CONTAINS.value
    if kind in ("method", "constructor"):
        return RecordRelations.METHOD.value
    if kind == "field" or nested:
        return RecordRelations.DEFINES.value
    return RecordRelations.CONTAINS.value


def _structural_edge(from_key: str, to_block_id: str, relation: str,
                     ctx: BlockProjectionContext) -> dict[str, Any]:
    to_key = f"{CollectionNames.BLOCKS.value}/{to_block_id}"
    return {
        "_from": from_key,
        "_to": to_key,
        "relationshipType": relation,
        # Provenance-scoped so cleanup can never cross producers.
        "constraintName": f"{_STRUCTURE_CONSTRAINT_PREFIX}:{relation.lower()}:{from_key}:{to_key}",
        "orgId": ctx.org_id,
        "recordGroupId": ctx.record_group_id,
        "source": PARSER_SOURCE,
        "createdAtTimestamp": _now_ms(),
        "updatedAtTimestamp": _now_ms(),
    }


async def _delete_edges_for_blocks(
    graph_provider: IGraphDBProvider, block_ids: list[str]
) -> None:
    for chunk in _chunks(block_ids, 5000):
        await graph_provider.delete_edges_touching_nodes(
            node_keys=chunk,
            node_collection=CollectionNames.BLOCKS.value,
            edge_collection=CollectionNames.RECORD_RELATIONS.value,
        )
