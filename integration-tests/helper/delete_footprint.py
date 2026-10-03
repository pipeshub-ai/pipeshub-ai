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
* blob storage and MongoDB: the processed-record envelope, filed under the
  record's place in its collection or connector
  (``{orgId}/PipesHub/records/{connectorId}/<folders>/<name>``, or the flat
  ``records/{virtualRecordId}`` when indexing cannot work that out) and found the
  way ``MongoStoreProbe.envelope_path`` finds it; and for a knowledge-base upload
  the original file, a storage document whose id is the record's
  ``externalRecordId`` and whose bytes sit under ``{documentPath}/{id}``.

Records with identical content share one virtual record id (MD5 dedup is
org-wide), so the vector, blob and envelope data of a shared id belong to every
record that carries it, even though the envelope is filed under the name of the
copy indexed first. What a survivor check expects of that envelope depends on
the delete:

* a record or folder delete leaves storage alone, so ``assert_unchanged``
  counts the envelope where it was before the delete: "unchanged" means those
  bytes are still there.
* a connector or collection delete removes its whole ``records/{id}`` tree,
  shared envelope included, then re-indexes one surviving holder
  (``repair_shared_records``), which files a new envelope under the holder's
  own place. ``assert_rebuilt`` checks the content is whole again there.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from helper.cleanup_errors import StoreNotEmptied
from helper.mongo_store import is_within, records_folder

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


async def envelope_location(
    mongo: "MongoStoreProbe", org_id: str, virtual_record_id: str, *, within: str
) -> tuple[str, str]:
    """The folder a record's envelope was filed in, and which backend holds it."""
    path = await mongo.envelope_path(org_id, virtual_record_id, within=within)
    return path, await mongo.storage_vendor_under_path(path) or "local"


async def storage_vendor(
    mongo: "MongoStoreProbe", org_id: str, virtual_record_id: str, *, within: str
) -> str:
    """Which backend holds a record's envelope, read from where it was filed."""
    return (await envelope_location(mongo, org_id, virtual_record_id, within=within))[1]


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
    graph: "GraphProviderProtocol", connector_id: str, *, excluding_records: Iterable[str] = ()
) -> GraphFootprint:
    """*excluding_records* leaves out records checked on their own, with their type nodes."""
    handles = await graph.connector_node_handles(connector_id)
    assert handles, f"Nothing in the graph belongs to {connector_id}; a delete test must start from something."
    excluded = sorted(set(excluding_records))
    skip = set(await graph.record_node_handles(excluded)) if excluded else set()
    kept = tuple(h for h in handles if h not in skip)
    assert kept, f"Excluding {excluded} leaves nothing of {connector_id} to check."
    return GraphFootprint(kept, await graph.count_edges_touching(kept))


async def graph_footprint_of_records(
    graph: "GraphProviderProtocol", record_ids: Iterable[str]
) -> GraphFootprint:
    ids = sorted(set(record_ids))
    handles = tuple(await graph.record_node_handles(ids))
    # By id, not by count: a found record's type node would make up for a missing one.
    found = {h.split("/", 1)[1] for h in handles if h.split("/", 1)[0] in ("records", "Record")}
    missing = [i for i in ids if i not in found]
    assert not missing, (
        f"Records {missing} are not in the graph; every record should be there before it is deleted."
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
    # Virtual record id -> folder its envelope was filed in, read before the delete.
    envelope_paths: dict[str, str] = field(default_factory=dict)


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
    within: str | None = None,
    connector_id: str | None = None,
    vendor: str = "local",
    upload_paths: dict[str, str] | None = None,
    envelope_paths: dict[str, str] | None = None,
) -> StoresFootprint:
    """Count what each store holds for *records*.

    *within* is the collection or connector folder the records' envelopes are
    filed in; each one's exact folder is read from MongoDB. *envelope_paths*
    supplies folders already known, as a re-count after a delete must look where
    the content was before it.
    """
    records = list(records)
    vrids = sorted({r.virtual_record_id for r in records if r.virtual_record_id})
    paths = dict(upload_paths or {})
    for record in records:
        if record.upload_document_id and record.upload_document_id not in paths:
            paths[record.upload_document_id] = await _upload_path(mongo, record.upload_document_id)
    envelopes = dict(envelope_paths or {})
    for vrid in vrids:
        if vrid not in envelopes:
            assert within, f"No folder to look for {vrid}'s envelope in; pass the collection or connector folder."
            envelopes[vrid] = await mongo.envelope_path(org_id, vrid, within=within)

    points = {v: await vector.count_for_virtual_record(v) for v in vrids}
    blob_keys = [envelopes[v] for v in vrids] + sorted(paths.values())
    blobs = {p: await blob.count_under(p, vendor) for p in blob_keys}
    doc_keys = [f"prefix:{envelopes[v]}" for v in vrids] + [f"id:{d}" for d in sorted(paths)]
    documents = {k: await _document_count(mongo, k) for k in doc_keys}
    connector_points = await vector.count_for_connector(connector_id) if connector_id else None
    footprint = StoresFootprint(graph_fp, connector_points, points, blobs, documents, paths, envelopes)
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


def assert_shared_envelope_counted(fp: StoresFootprint, virtual_record_id: str) -> None:
    """Precondition: shared content counted as zero before the delete would pass "unchanged" with nothing there."""
    path = fp.envelope_paths.get(virtual_record_id)
    counts = {
        "embeddings": fp.points.get(virtual_record_id, 0),
        "blob files": fp.blobs.get(path, 0) if path else 0,
        "storage documents": fp.documents.get(f"prefix:{path}", 0) if path else 0,
    }
    empty = [store for store, count in counts.items() if not count]
    assert path and not empty, (
        f"The shared content {virtual_record_id} has nothing in {empty or 'any store'} "
        f"under {path!r} before the delete, so checking it is unchanged would prove nothing."
    )


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


def blob_keys_for(fp: StoresFootprint, records: Iterable[Tracked]) -> list[str]:
    records = list(records)
    return [fp.envelope_paths[r.virtual_record_id] for r in records if r.virtual_record_id] + [
        fp.upload_paths[r.upload_document_id] for r in records if r.upload_document_id
    ]


def document_keys_for(fp: StoresFootprint, records: Iterable[Tracked]) -> list[str]:
    records = list(records)
    return [f"prefix:{fp.envelope_paths[r.virtual_record_id]}" for r in records if r.virtual_record_id] + [
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
        upload_paths=before.upload_paths, envelope_paths=before.envelope_paths,
    )
    changes = [] if nodes_now == len(before.graph.handles) else [
        f"graph nodes {len(before.graph.handles)} -> {nodes_now}"
    ]
    if after.graph.edges != before.graph.edges:
        changes.append(f"graph edges {before.graph.edges} -> {after.graph.edges}")
    if after.connector_points != before.connector_points:
        changes.append(f"connector points {before.connector_points} -> {after.connector_points}")
    changes += store_changes(before, after)
    assert not changes, f"The delete changed {what}: " + "; ".join(changes)


def store_changes(
    before: StoresFootprint, after: StoresFootprint, moved: dict[str, str] | None = None
) -> list[str]:
    """Each embedding, blob and document count that differs, reading a *moved* folder's count at its new place."""
    moved = moved or {}
    renamed = {**moved, **{f"prefix:{old}": f"prefix:{new}" for old, new in moved.items()}}
    changes: list[str] = []
    for store, was, now in (
        ("embeddings", before.points, after.points),
        ("blob files", before.blobs, after.blobs),
        ("storage documents", before.documents, after.documents),
    ):
        for key in was:
            new_key = renamed.get(key, key)
            if was[key] != now.get(new_key):
                where = key if new_key == key else f"{key} (now {new_key})"
                changes.append(f"{store} {where}: {was[key]} -> {now.get(new_key)}")
    return changes


async def wait_for_rebuild(
    graph: "GraphProviderProtocol",
    mongo: "MongoStoreProbe",
    *,
    org_id: str,
    connector_id: str,
    holder: Tracked,
    timeout: int = 480,
) -> str:
    """The folder the survivor's re-index filed the shared content in, once that re-index has settled."""
    folder = records_folder(org_id, connector_id)
    path = await mongo.envelope_path(org_id, str(holder.virtual_record_id), within=folder, timeout=timeout)
    assert is_within(path, folder), (
        f"The shared content {holder.virtual_record_id} was rebuilt at {path!r}, not under "
        f"{folder!r} where its surviving holder {holder.name} lives."
    )
    await wait_for_connector_records(graph, connector_id, [holder.name], timeout=timeout)
    return path


async def assert_rebuilt(
    before: StoresFootprint,
    graph: "GraphProviderProtocol",
    vector: "VectorStoreProbe",
    blob: "BlobStoreProbe",
    mongo: "MongoStoreProbe",
    *,
    org_id: str,
    connector_id: str,
    holder: Tracked,
    vendor: str = "local",
    what: str,
) -> None:
    """Shared content whose envelope went with a connector or collection is whole again under *holder*.

    *before* counted it where it was filed before the delete. Edges are not
    compared: the re-index re-runs extraction, which reconciles the record's
    category and topic edges afresh.
    """
    vrid = str(holder.virtual_record_id)
    old = before.envelope_paths.get(vrid)
    assert old, f"{what}: no envelope of {vrid} was counted before the delete, so there is nothing to compare."
    new = await wait_for_rebuild(graph, mongo, org_id=org_id, connector_id=connector_id, holder=holder)
    nodes_now = await graph.count_existing_nodes(before.graph.handles)
    after = await capture_when_stable(
        before.graph, vector, blob, mongo,
        org_id=org_id, records=[holder], vendor=vendor,
        upload_paths=before.upload_paths, envelope_paths={vrid: new},
    )
    changes = [] if nodes_now == len(before.graph.handles) else [
        f"graph nodes {len(before.graph.handles)} -> {nodes_now}"
    ]
    changes += store_changes(before, after, moved={old: new})
    assert not changes, f"{what} was not rebuilt whole under {new!r}: " + "; ".join(changes)


async def settle(check, what: str) -> None:
    """Give a delete time to finish before survivors are compared; the tests assert the outcome."""
    try:
        await check
    except AssertionError as exc:
        logger.warning("Delete of %s not finished; the tests will report it: %s", what, exc)
