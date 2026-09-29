"""What a delete must remove, and what it must leave alone, across all four stores.

A delete test needs two snapshots taken *before* the delete: the footprint of
what is being deleted (so each piece can be checked gone) and the footprint of
everything that must survive (so nothing about it can change unnoticed). Both
are taken up front because the delete destroys the graph data that says which
vectors, files and documents belonged to what.

Where a record's data lives:

* graph: its node, its type node (File, ...) and every edge touching them. Edges
  are counted by their ends, so an edge left pointing at a deleted node is seen;
  walking out from nodes that still exist would miss it.
* vector database: points whose ``metadata.virtualRecordId`` is the record's.
* blob storage and MongoDB: the processed-record envelope under
  ``{orgId}/PipesHub/records/{virtualRecordId}``, and for a knowledge-base upload
  the original file, a storage document whose id is the record's
  ``externalRecordId`` and whose bytes sit under ``{documentPath}/{id}``.

Records with identical content share one virtual record id (MD5 dedup is
org-wide), so the vector, blob and envelope data of a shared id belong to every
record that carries it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from helper.cleanup_errors import StoreNotEmptied

if TYPE_CHECKING:
    from helper.blob_store import BlobStoreProbe
    from helper.graph_provider import GraphProviderProtocol
    from helper.mongo_store import MongoStoreProbe
    from helper.vector_store import VectorStoreProbe

logger = logging.getLogger("delete-footprint")

POLL = 5

# Statuses after which indexing will not touch a record again on its own.
SETTLED_STATUSES = frozenset({
    "COMPLETED", "FAILED", "FILE_TYPE_NOT_SUPPORTED", "EMPTY",
    "AUTO_INDEX_OFF", "ENABLE_MULTIMODAL_MODELS", "CONNECTOR_DISABLED",
})


def envelope_prefix(org_id: str, virtual_record_id: str) -> str:
    return f"{org_id}/PipesHub/records/{virtual_record_id}"


@dataclass(frozen=True)
class Tracked:
    """One record and the keys the other three stores file its data under."""

    name: str
    record_id: str
    virtual_record_id: str | None
    upload_document_id: str | None = None
    status: str | None = None


def tracked_from_graph(record: dict[str, Any]) -> Tracked:
    return Tracked(
        name=str(record.get("recordName") or record.get("name")),
        record_id=str(record.get("id") or record.get("_key")),
        virtual_record_id=record.get("virtualRecordId"),
        status=record.get("indexingStatus"),
    )


async def wait_for_connector_records(
    graph: "GraphProviderProtocol",
    connector_id: str,
    names: Iterable[str],
    *,
    timeout: int = 480,
) -> dict[str, Tracked]:
    """Block until each named record is in the graph and done indexing."""
    wanted = set(names)
    deadline = asyncio.get_event_loop().time() + timeout
    found: dict[str, dict[str, Any]] = {}
    while True:
        for name in wanted:
            record = await graph.get_record_by_name(connector_id, name)
            if record is not None:
                found[name] = record
        unsettled = {
            name: (found.get(name) or {}).get("indexingStatus")
            for name in wanted
            if (found.get(name) or {}).get("indexingStatus") not in SETTLED_STATUSES
        }
        if not unsettled:
            return {name: tracked_from_graph(found[name]) for name in wanted}
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"Records of {connector_id} not settled after {timeout}s "
                f"(name -> indexing status): {unsettled}"
            )
        await asyncio.sleep(POLL)


@dataclass(frozen=True)
class GraphFootprint:
    handles: tuple[str, ...]
    edges: int

    def __str__(self) -> str:
        return f"{len(self.handles)} node(s), {self.edges} edge(s)"


async def graph_footprint_of_connector(
    graph: "GraphProviderProtocol", connector_id: str
) -> GraphFootprint:
    handles = tuple(await graph.connector_node_handles(connector_id))
    assert handles, f"Nothing in the graph belongs to {connector_id}; a delete test must start from something."
    return GraphFootprint(handles, await graph.count_edges_touching(handles))


async def graph_footprint_of_records(
    graph: "GraphProviderProtocol", record_ids: Iterable[str]
) -> GraphFootprint:
    ids = sorted(set(record_ids))
    handles = tuple(await graph.record_node_handles(ids))
    assert len(handles) >= len(ids), (
        f"Only {len(handles)} graph node(s) found for {len(ids)} record(s); every "
        "record should be in the graph before it is deleted."
    )
    return GraphFootprint(handles, await graph.count_edges_touching(handles))


@dataclass(frozen=True)
class StoresFootprint:
    """Counts per store, keyed so the same keys can be asked again after the delete."""

    graph: GraphFootprint
    connector_points: int | None
    points: dict[str, int] = field(default_factory=dict)
    blobs: dict[str, int] = field(default_factory=dict)
    documents: dict[str, int] = field(default_factory=dict)
    # Storage document id -> path of its bytes, read while the document exists.
    upload_paths: dict[str, str] = field(default_factory=dict)


async def _upload_path(mongo: "MongoStoreProbe", document_id: str) -> str:
    doc = await mongo.assert_document_present(document_id)
    return f"{doc['documentPath']}/{document_id}"


async def _document_count(mongo: "MongoStoreProbe", key: str) -> int:
    kind, _, value = key.partition(":")
    if kind == "prefix":
        return await mongo.count_documents_under_path(value)
    return 0 if await mongo.find_document(value) is None else 1


async def capture(
    graph_fp: GraphFootprint,
    vector: "VectorStoreProbe",
    blob: "BlobStoreProbe",
    mongo: "MongoStoreProbe",
    *,
    org_id: str,
    records: Iterable[Tracked],
    connector_id: str | None = None,
    vendor: str = "local",
    upload_paths: dict[str, str] | None = None,
) -> StoresFootprint:
    records = list(records)
    vrids = sorted({r.virtual_record_id for r in records if r.virtual_record_id})
    paths = dict(upload_paths or {})
    for record in records:
        if record.upload_document_id and record.upload_document_id not in paths:
            paths[record.upload_document_id] = await _upload_path(mongo, record.upload_document_id)

    points = {v: await vector.count_for_virtual_record(v) for v in vrids}
    blob_keys = [envelope_prefix(org_id, v) for v in vrids] + sorted(paths.values())
    blobs = {p: await blob.count_under(p, vendor) for p in blob_keys}
    doc_keys = [f"prefix:{envelope_prefix(org_id, v)}" for v in vrids] + [f"id:{d}" for d in sorted(paths)]
    documents = {k: await _document_count(mongo, k) for k in doc_keys}
    connector_points = await vector.count_for_connector(connector_id) if connector_id else None
    footprint = StoresFootprint(graph_fp, connector_points, points, blobs, documents, paths)
    logger.info("Captured footprint: %s", footprint)
    return footprint


async def capture_when_stable(*args: Any, attempts: int = 12, **kwargs: Any) -> StoresFootprint:
    """Capture until two reads a poll apart agree: blob and envelope writes trail the embeddings."""
    previous = await capture(*args, **kwargs)
    for _ in range(attempts):
        await asyncio.sleep(POLL)
        current = await capture(*args, **kwargs)
        if current == previous:
            return current
        previous = current
    raise AssertionError(f"Store counts kept changing; last read: {previous}")


def assert_every_store_holds_it(fp: StoresFootprint) -> None:
    """Precondition: a store that was already empty would make "gone" prove nothing."""
    empty = [
        f"{store} {key}"
        for store, counts in (("embeddings", fp.points), ("blob files", fp.blobs), ("storage documents", fp.documents))
        for key, count in counts.items()
        if count == 0
    ]
    assert fp.points and not empty, (
        f"Nothing to delete in: {empty or 'every store (no indexed records)'}. "
        "Indexing may not have finished."
    )
    assert fp.graph.edges > 0, "The graph footprint has no edges; the fixture built nothing."


async def _wait_until_empty(read, timeout: int) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    left = await read()
    while left and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(POLL)
        left = await read()
    return left


async def assert_graph_gone(
    graph: "GraphProviderProtocol", fp: GraphFootprint, *, timeout: int = 180
) -> None:
    async def read() -> dict:
        nodes = await graph.count_existing_nodes(fp.handles)
        edges = await graph.count_edges_touching(fp.handles)
        return {"nodes": nodes, "edges": edges} if nodes or edges else {}

    left = await _wait_until_empty(read, timeout)
    assert not left, (
        f"{left['nodes']} of {len(fp.handles)} node(s) and {left['edges']} edge(s) are "
        f"still in the graph {timeout}s after the delete (before: {fp})."
    )


async def assert_embeddings_gone(
    vector: "VectorStoreProbe", fp: StoresFootprint, vrids: Iterable[str], *, timeout: int = 180
) -> None:
    wanted = sorted(set(vrids))
    assert wanted and all(fp.points.get(v) for v in wanted), (
        f"No embeddings recorded before the delete for {wanted}; nothing to check."
    )

    async def read() -> dict:
        counts = {v: await vector.count_for_virtual_record(v) for v in wanted}
        return {v: c for v, c in counts.items() if c}

    left = await _wait_until_empty(read, timeout)
    assert not left, f"Embeddings still present {timeout}s after the delete: {left}"


async def assert_blobs_gone(
    blob: "BlobStoreProbe", fp: StoresFootprint, paths: Iterable[str], *, vendor: str = "local", timeout: int = 60
) -> None:
    wanted = sorted(set(paths))
    assert wanted and all(fp.blobs.get(p) for p in wanted), (
        f"No blob files recorded before the delete under {wanted}; nothing to check."
    )

    async def read() -> dict:
        counts = {p: await blob.count_under(p, vendor) for p in wanted}
        return {p: c for p, c in counts.items() if c}

    left = await _wait_until_empty(read, timeout)
    if left:
        raise StoreNotEmptied(f"Blob files still present {timeout}s after the delete: {left}")


async def assert_documents_gone(
    mongo: "MongoStoreProbe", fp: StoresFootprint, keys: Iterable[str], *, timeout: int = 60
) -> None:
    wanted = sorted(set(keys))
    assert wanted and all(fp.documents.get(k) for k in wanted), (
        f"No storage documents recorded before the delete for {wanted}; nothing to check."
    )

    async def read() -> dict:
        counts = {k: await _document_count(mongo, k) for k in wanted}
        return {k: c for k, c in counts.items() if c}

    left = await _wait_until_empty(read, timeout)
    if left:
        raise StoreNotEmptied(f"Storage documents still present {timeout}s after the delete: {left}")


def blob_keys_for(fp: StoresFootprint, org_id: str, records: Iterable[Tracked]) -> list[str]:
    records = list(records)
    return [envelope_prefix(org_id, r.virtual_record_id) for r in records if r.virtual_record_id] + [
        fp.upload_paths[r.upload_document_id] for r in records if r.upload_document_id
    ]


def document_keys_for(org_id: str, records: Iterable[Tracked]) -> list[str]:
    records = list(records)
    return [f"prefix:{envelope_prefix(org_id, r.virtual_record_id)}" for r in records if r.virtual_record_id] + [
        f"id:{r.upload_document_id}" for r in records if r.upload_document_id
    ]


async def assert_unchanged(
    before: StoresFootprint,
    graph: "GraphProviderProtocol",
    vector: "VectorStoreProbe",
    blob: "BlobStoreProbe",
    mongo: "MongoStoreProbe",
    *,
    org_id: str,
    records: Iterable[Tracked],
    connector_id: str | None = None,
    vendor: str = "local",
    what: str,
) -> None:
    """Every count taken before the delete is the same after it, store by store."""
    nodes_now = await graph.count_existing_nodes(before.graph.handles)
    after_graph = GraphFootprint(before.graph.handles, await graph.count_edges_touching(before.graph.handles))
    after = await capture(
        after_graph, vector, blob, mongo,
        org_id=org_id, records=records, connector_id=connector_id, vendor=vendor,
        upload_paths=before.upload_paths,
    )
    changes: list[str] = []
    if nodes_now != len(before.graph.handles):
        changes.append(f"graph nodes {len(before.graph.handles)} -> {nodes_now}")
    if after.graph.edges != before.graph.edges:
        changes.append(f"graph edges {before.graph.edges} -> {after.graph.edges}")
    if after.connector_points != before.connector_points:
        changes.append(f"connector points {before.connector_points} -> {after.connector_points}")
    for store, was, now in (
        ("embeddings", before.points, after.points),
        ("blob files", before.blobs, after.blobs),
        ("storage documents", before.documents, after.documents),
    ):
        for key in was:
            if was[key] != now.get(key):
                changes.append(f"{store} {key}: {was[key]} -> {now.get(key)}")
    assert not changes, f"The delete changed {what}: " + "; ".join(changes)


async def settle(check, what: str) -> None:
    """Give a delete time to finish before survivors are compared; the tests assert the outcome."""
    try:
        await check
    except AssertionError as exc:
        logger.warning("Delete of %s not finished; the tests will report it: %s", what, exc)
