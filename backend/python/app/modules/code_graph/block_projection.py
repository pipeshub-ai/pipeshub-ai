"""Project a parsed code file's blocks into ArangoDB nodes and local edges.

Writes the structural relations whose endpoints are both known while parsing a
single file -- CONTAINS, METHOD, DEFINES. Cross-file relations are carried on
each node as ``pendingEdges`` and resolved later by ``edge_builder``.

A block deliberately does not carry its file path. The path belongs to the
record (``codeFiles.filePath``) and changes without the blocks changing -- a
directory rename moves every file and touches no symbol -- so readers join on
``recordId`` rather than storing ~30 copies per file that then go stale.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.code_graph.module_refs import module_ref
from app.modules.code_graph.version import PARSER_VERSION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from app.models.blocks import Block, BlockGroup, BlocksContainer
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "BlockProjectionContext",
    "FILE_SUMMARY_QUALIFIED_NAME",
    "RESIDENT_RELATIONS",
    "block_key_for",
    "drop_code_file_blocks",
    "serialize_block_for_graph",
    "write_code_file_blocks_to_graph",
]

_STRUCTURE_CONSTRAINT_PREFIX = "structure"
FILE_SUMMARY_QUALIFIED_NAME = "file_summary:1"

# The only facts kept on the node: what the edge builder reads for *every* file
# on *every* build. Heritage, to walk the base classes of files it is not
# re-resolving; re-exports, to follow barrel chains through them. Calls and
# imports -- most of the bytes -- are read only for the files being re-resolved
# and come from the record's blob, which holds this same parse.
RESIDENT_RELATIONS = frozenset({
    RecordRelations.INHERITS.value,
    RecordRelations.IMPLEMENTS.value,
    RecordRelations.RE_EXPORTS.value,
})
# A module specifier (`.base`, `shopkit.pricing`) never matches a defined symbol
# name, so the per-file name list keeps only its last segment: the module's own
# name, which is what a build compares when that module is added or changed.
_MODULE_SPECIFIER_RELATIONS = frozenset({
    RecordRelations.IMPORTS_FROM.value,
    RecordRelations.RE_EXPORTS.value,
})

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
    language: str | None = None
    # Repo-relative path; a Python package's `__init__.py` keeps its imports on
    # the node (see RESIDENT_RELATIONS).
    file_path: str | None = None


def block_key_for(record_id: str, qualified_name: str) -> str:
    """Deterministic Arango ``_key`` for a symbol.

    Scoped by *record* rather than by file path. The qualified name is only
    unique within a file, so something must disambiguate it -- and the record
    survives a move where the path does not: the connector detects renames
    (`_reconcile_sha_moves`) and `on_records_moved` reuses the existing vertex,
    keeping the same id. A path-derived key would change on every rename, and
    since a pure rename emits no re-index event, nothing would ever repair it.

    The record id is a uuid4, so nothing further is needed to make this unique.
    `recordGroupId` in particular must stay out: a cross-group move keeps the
    record and swaps the group, which would re-key every block of the file and
    strand its edges -- the same failure a path-derived key has.

    Deterministic so re-runs produce identical keys and existing CONTAINS edges
    stay valid.
    """
    digest = hashlib.sha256(f"{record_id}:{qualified_name}".encode("utf-8"))
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
    # The Arango `blocks` schema rejects these server-side; Neo4j has no
    # equivalent (existence constraints are Enterprise-only and its app-side
    # validation skips `required`), so the invariant is held here for both. A
    # block without them is unscoped: every org-filtered lookup misses it.
    if not (isinstance(ctx.org_id, str) and ctx.org_id):
        raise ValueError(f"block {block_id!r} has no orgId")
    if not (isinstance(ctx.record_id, str) and ctx.record_id):
        raise ValueError(f"block {block_id!r} has no recordId")
    meta = item.code_metadata
    now = _now_ms()
    doc: dict[str, Any] = {
        "id": block_id,
        "orgId": ctx.org_id,
        "recordId": ctx.record_id,
        "recordGroupId": ctx.record_group_id,
        "connectorId": ctx.connector_id,
        "name": item.name,
        # What a bare-name lookup matches on, exactly and through an index (SC-6).
        # lower(), not casefold(): it must equal the databases' LOWER/toLower,
        # which the backfill uses.
        "nameFolded": item.name.lower() if item.name else None,
        # The parser and projection this block was made by (O-10).
        "parserVersion": PARSER_VERSION,
        "index": item.index,
        "isBlockGroup": is_block_group,
        "isExternal": False,
        "parentBlockId": parent_block_id,
        "contentHash": item.content_hash,
        "createdAtTimestamp": now,
        "updatedAtTimestamp": now,
        # Every optional field is written even when empty. Neo4j upserts with
        # `SET n += props`, which cannot drop a key that is simply absent,
        # while Arango replaces the whole document -- so omitting one here
        # means the two backends disagree the moment a symbol loses its last
        # cross-file reference, and the stale `pendingEdges` resolves the
        # removed edge back into existence. `None` is what clears a Neo4j
        # property, and the schema admits it for all of these.
        "subType": item.sub_type.value if item.sub_type is not None else None,
        "kind": ((meta.kind or "").lower() or None) if meta else None,
        "qualifiedName": meta.qualified_name if meta else None,
        "startLine": meta.start_line if meta else None,
        "endLine": meta.end_line if meta else None,
        "language": (meta.language or ctx.language) if meta else None,
        "pendingEdges": (_resident_facts(meta.pending_edges, package_init=_is_package_init(ctx.file_path)) or None)
        if meta else None,
        # Both moved to the blob. Still written, as None, so a node projected
        # before that change is cleared instead of keeping a stale copy.
        "typeTable": None,
        # Moved to codeFiles node. Still written as None so nodes projected
        # before that change are cleared.
        "referencedNames": None,
    }
    return doc


def _is_package_init(file_path: str | None) -> bool:
    return bool(file_path) and (file_path == "__init__.py" or file_path.endswith("/__init__.py"))


def _resident_facts(facts: list[dict] | None, *, package_init: bool = False) -> list[dict]:
    """A package `__init__.py`'s imports are its re-exports (`from .base import
    Model` makes `models.Model`), which every build follows, so they stay too."""
    keep = RESIDENT_RELATIONS | ({RecordRelations.IMPORTS.value} if package_init else frozenset())
    return [fact for fact in facts or () if fact.get("relation") in keep]


def _referenced_names(container: BlocksContainer, file_path: str | None = None) -> list[str] | None:
    """Every symbol name the file references, deduplicated, plus the modules it imports.

    The edge builder's only question about a file it is not re-resolving is
    "does it mention a name the changed files define?" -- this answers it
    without the facts themselves. Modules go in as ``module:<path>``
    (``module_refs``), resolved against *file_path* where relative.
    """
    names: set[str] = set()
    for item in (*container.block_groups, *container.blocks):
        meta = item.code_metadata
        for fact in (meta.pending_edges if meta else None) or ():
            name = fact.get("toName")
            if not isinstance(name, str) or not name:
                continue
            if fact.get("relation") in _MODULE_SPECIFIER_RELATIONS:
                ref = module_ref(name, file_path)
                if ref:
                    names.add(ref)
                continue
            names.add(name)
            # `from pkg import sub` may import a module, not a name in pkg.
            prefix = fact.get("qualifiedPrefix")
            if isinstance(prefix, str) and prefix and "/" not in prefix:
                ref = module_ref(prefix + ("" if prefix.endswith(".") else ".") + name, file_path)
                if ref:
                    names.add(ref)
    return sorted(names) or None


# How long a file keeps reporting names whose definitions it changed. Builds
# normally run minutes after a projection; this only has to outlast a build
# that keeps failing.
# shortcut: names are not cleared once a build has used them, so a file edited
# again within the TTL re-selects their referencers; clear them conditionally
# (on changedNamesAt) after a successful build if that shows up in build sizes.
_CHANGED_NAMES_TTL_MS = 7 * 24 * 3600 * 1000


def _merge_changed_names(code_file: dict[str, Any], changed: set[str]) -> dict[str, Any]:
    """``changedNames`` for the codeFiles node: these names plus recent earlier ones.

    The next incremental build re-resolves every file that references one of
    them. Without it, a file that dropped a definition left its callers bound
    to nothing, or never bound them to another definition of the same name,
    until a full rebuild (R-3); and selecting by every name the file defines
    re-resolved most of the repo on any edit (R-7). Earlier names are kept,
    not replaced: a second projection can land before the build that was meant
    to see the first.
    """
    now = _now_ms()
    earlier = code_file.get("changedNames") or []
    if now - (code_file.get("changedNamesAt") or 0) > _CHANGED_NAMES_TTL_MS:
        earlier = []
    return {"changedNames": sorted(changed | set(earlier)), "changedNamesAt": now}


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


async def _with_retry[T](coro_factory: Callable[[], Awaitable[T]], *, what: str, log: logging.Logger) -> T:
    last: Exception | None = None
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            return await coro_factory()
        except Exception as exc:  # retried then re-raised
            last = exc
            if delay is None:
                break
            log.warning("%s failed (attempt %d), retrying in %.1fs: %s", what, attempt + 1, delay, exc)
            await asyncio.sleep(delay)
    raise last  # type: ignore[misc]


async def _both[A, B](first: Awaitable[A], second: Awaitable[B]) -> tuple[A, B]:
    """Await two independent calls together. Both run to the end, then the first
    failure is raised as it is, as two sequential awaits would raise it."""
    results = await asyncio.gather(first, second, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results[0], results[1]  # type: ignore[return-value]


def _chunks(items: list, size: int = _WRITE_CHUNK) -> Iterator[list]:
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
        gid = block_key_for(context.record_id, meta.qualified_name)
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
            # identity so it still lands and can carry the file's name list.
            qualified_name = FILE_SUMMARY_QUALIFIED_NAME
        block_ids[block.index] = block_key_for(context.record_id, qualified_name)

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
            meta.kind if meta else None, has_parent=parent_id is not None,
            nested=parent_bid is not None,
        )
        edges.append(_structural_edge(parent_key, bid, relation, context))

    fresh_ids = {doc["id"] for doc in docs}

    # The two reads are independent: one round trip of latency, not two.
    existing, code_file = await _both(
        _with_retry(
            lambda: graph_provider.get_nodes_by_filters(
                collection=CollectionNames.BLOCKS.value,
                filters={"recordId": context.record_id},
                return_fields=["_key", "name", "kind"],
            ),
            what="load existing blocks", log=log,
        ),
        _with_retry(
            lambda: graph_provider.get_document(
                context.record_id, CollectionNames.CODE_FILES.value, raise_on_error=True
            ),
            what="load codeFiles node", log=log,
        ),
    )
    existing = existing or []
    existing_ids = {row.get("_key") or row.get("id") for row in existing}
    stale_ids = [bid for bid in existing_ids - fresh_ids if bid]
    # By identity (qualified name, and kind), not bare name: a definition that
    # moved between classes, or became a class, can rebind callers elsewhere.
    before = {(row.get("_key") or row.get("id"), row.get("kind")): row.get("name") for row in existing}
    after = {(doc["id"], doc.get("kind")): doc.get("name") for doc in docs}
    changed = {
        name for key, name in (*before.items(), *after.items())
        if name and key[1] != "file_summary" and (key not in before or key not in after)
    }

    # referencedNames is written to the codeFiles node, not a block. The edge
    # builder queries it alongside filePath, avoiding a block-scan join.
    referenced = _referenced_names(block_containers, (code_file or {}).get("filePath"))
    code_file_update: Callable[[], Awaitable[Any]] | None = None
    if referenced is not None or changed:
        if code_file:
            updates: dict[str, Any] = {}
            if referenced is not None:
                updates["referencedNames"] = referenced
            if changed:
                updates.update(_merge_changed_names(code_file, changed))
            # Written alongside the first block chunk below: neither reads the other.
            def code_file_update() -> Awaitable[Any]:
                return _with_retry(
                    lambda: graph_provider.update_node(
                        context.record_id, CollectionNames.CODE_FILES.value, updates,
                    ),
                    what="update codeFiles names", log=log,
                )
        elif docs and referenced is not None:
            # No codeFiles node (an uploaded .py): updating one would 404 and
            # fail the record. The symbol index falls back to the names on the
            # file's blocks, so they go on the first one.
            docs[0]["referencedNames"] = referenced

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
        upsert = _with_retry(
            lambda c=chunk: graph_provider.batch_upsert_nodes(c, CollectionNames.BLOCKS.value),
            what="upsert blocks", log=log,
        )
        if code_file_update is not None:
            await _both(upsert, code_file_update())
            code_file_update = None
        else:
            await upsert
    if code_file_update is not None:
        await code_file_update()
    for chunk in _chunks(edges):
        await _with_retry(
            lambda c=chunk: graph_provider.batch_upsert_node_relations(c),
            what="upsert structural edges", log=log,
        )

    return {
        "blocks_written": len(docs),
        "edges_written": len(edges),
        "blocks_removed": len(stale_ids),
    }


async def drop_code_file_blocks(
    graph_provider: IGraphDBProvider, record_id: str, log: logging.Logger | None = None,
) -> None:
    """Delete a code file's blocks, remembering the names they defined.

    For a file emptied, made unparseable or skipped: it still exists, so it is
    in the next build's touched set, and its lost names send that build to the
    files that called into it.
    """
    log = log or logger
    existing = await _with_retry(
        lambda: graph_provider.get_nodes_by_filters(
            collection=CollectionNames.BLOCKS.value,
            filters={"recordId": record_id},
            return_fields=["name", "kind"],
        ),
        what="load existing blocks", log=log,
    ) or []
    removed = {
        row.get("name") for row in existing
        if row.get("name") and row.get("kind") != "file_summary"
    }
    if removed:
        code_file = await _with_retry(
            lambda: graph_provider.get_document(
                record_id, CollectionNames.CODE_FILES.value, raise_on_error=True
            ),
            what="load codeFiles node", log=log,
        )
        if code_file:
            await _with_retry(
                lambda: graph_provider.update_node(
                    record_id, CollectionNames.CODE_FILES.value,
                    _merge_changed_names(code_file, removed),
                ),
                what="update codeFiles changedNames", log=log,
            )
    await graph_provider.delete_blocks_for_records([record_id])


def _local_relation_for(kind: str | None, *, has_parent: bool, nested: bool = False) -> str:
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
            edge_collection=CollectionNames.RECORD_LINKS.value,
        )

