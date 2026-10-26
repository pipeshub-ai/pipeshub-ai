"""Check a deletion cleared every store, not just the graph.

Deleting a record has to clear four places: the graph, the vector database,
blob storage and MongoDB. Each is checked by its own probe; this module ties
them together and handles the one thing that is easy to get wrong when writing
such a test.

**Capture the footprint before deleting.** The identifiers the other three
stores are keyed on — chiefly the virtual record id — are only obtainable from
the graph, and the delete removes them. A test that tries to look them up
afterwards finds nothing and then "passes", having checked that nothing is
nothing. ``capture_footprint`` exists to make the ordering explicit, and the
assertions take a footprint rather than a record name so that a test cannot be
written the wrong way round by accident.

Blob storage and MongoDB both file a record's processed content in one
folder, under the record's place in its connector
(``{orgId}/PipesHub/records/{connectorId}/<folders>/<name>``, or the flat
``records/{virtualRecordId}`` when indexing cannot work that out). The footprint
reads that folder from MongoDB before the delete, so one footprint answers for
both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from helper.mongo_store import records_folder

if TYPE_CHECKING:
    from helper.blob_store import BlobStoreProbe
    from helper.graph_provider import GraphProviderProtocol
    from helper.mongo_store import MongoStoreProbe
    from helper.vector_store import VectorStoreProbe

logger = logging.getLogger("cross-store")


@dataclass(frozen=True)
class RecordFootprint:
    """Everything the four stores need to be asked about one record.

    Frozen because it is a snapshot of a moment that cannot be recovered: once
    the record is deleted, none of this is derivable again.
    """

    record_name: str
    virtual_record_id: str
    org_id: str
    # Where both blob storage and MongoDB filed this record's content.
    storage_prefix: str
    connector_id: str | None = None
    embedding_count: int = 0
    blob_file_count: int = 0
    mongo_document_count: int = 0

    def __str__(self) -> str:
        return (
            f"{self.record_name} (virtual {self.virtual_record_id}): "
            f"{self.embedding_count} embedding(s), "
            f"{self.blob_file_count} file(s), "
            f"{self.mongo_document_count} document(s)"
        )


async def capture_footprint(
    graph: "GraphProviderProtocol",
    vector: "VectorStoreProbe",
    blob: "BlobStoreProbe",
    mongo: "MongoStoreProbe",
    *,
    connector_id: str,
    record_name: str,
    org_id: str,
    storage_vendor: str = "local",
) -> RecordFootprint:
    """Record what all four stores hold for one record, before it is deleted.

    Requires the graph record, its virtual id, and embeddings: without those a
    footprint is meaningless and every later "it is gone" assertion would pass
    against nothing, the failure mode this module exists to avoid. The folder
    the content was filed in is read from the record's ``record_<vrid>`` storage
    document, waiting for indexing to write it; the blob and Mongo counts in it
    are then captured as they stand and may be zero, so a caller that needs them
    present before a delete must assert that itself.
    """
    record = await graph.get_record_by_name(connector_id, record_name)
    assert record is not None, (
        f"Record {record_name!r} is not in the graph for connector "
        f"{connector_id}. The deletion test has nothing to delete."
    )

    virtual_record_id = _virtual_id_of(record)
    assert virtual_record_id, (
        f"Record {record_name!r} has no virtualRecordId. Blob storage and "
        "MongoDB both file content under that id, so without it this test "
        "could only ever check the graph — which is the gap it exists to close."
    )

    embeddings = await vector.count_for_virtual_record(virtual_record_id)
    assert embeddings > 0, (
        f"{record_name!r} has no embeddings before the delete. Either indexing "
        "has not finished or the record was never indexed; either way, "
        "asserting they are gone afterwards would prove nothing."
    )
    prefix = await mongo.envelope_path(
        org_id, virtual_record_id, within=records_folder(org_id, connector_id)
    )
    files = await blob.count_under(prefix, storage_vendor)
    documents = await mongo.count_documents_under_path(prefix)

    footprint = RecordFootprint(
        record_name=record_name,
        virtual_record_id=virtual_record_id,
        org_id=org_id,
        storage_prefix=prefix,
        connector_id=connector_id,
        embedding_count=embeddings,
        blob_file_count=files,
        mongo_document_count=documents,
    )
    logger.info("Captured footprint before delete: %s", footprint)
    return footprint


async def assert_fully_deleted(
    footprint: RecordFootprint,
    graph: "GraphProviderProtocol",
    vector: "VectorStoreProbe",
    blob: "BlobStoreProbe",
    mongo: "MongoStoreProbe",
    *,
    storage_vendor: str = "local",
    timeout: int = 180,
) -> None:
    """All four stores have let go of this record.

    Every store is checked even after one fails, so a failure reports the whole
    picture rather than the first thing that went wrong. Knowing that the graph
    is clean but three stores are not is a different bug from one store lagging.
    """
    failures: list[str] = []

    try:
        await graph.assert_record_not_exists(
            footprint.connector_id or "", footprint.record_name
        )
    except AssertionError as exc:
        failures.append(f"graph: {exc}")

    try:
        await vector.assert_embeddings_gone(
            footprint.virtual_record_id, timeout=timeout
        )
    except AssertionError as exc:
        failures.append(f"vector database: {exc}")

    try:
        await blob.assert_blobs_gone(
            footprint.storage_prefix, storage_vendor, timeout=timeout
        )
    except AssertionError as exc:
        failures.append(f"blob storage: {exc}")

    try:
        await mongo.assert_documents_under_path_gone(
            footprint.storage_prefix, timeout=timeout
        )
    except AssertionError as exc:
        failures.append(f"MongoDB: {exc}")

    if failures:
        joined = "\n\n".join(f"  - {f}" for f in failures)
        raise AssertionError(
            f"Deleting {footprint.record_name!r} left data behind in "
            f"{len(failures)} of 4 stores.\n\n{joined}\n\n"
            f"Footprint before the delete: {footprint}"
        )


def _virtual_id_of(record: Any) -> str | None:
    """Read the virtual record id off whatever shape the provider returned."""
    if isinstance(record, dict):
        return record.get("virtualRecordId") or record.get("virtual_record_id")
    return getattr(record, "virtual_record_id", None) or getattr(
        record, "virtualRecordId", None
    )
