"""Take a record's index back: its vectors, stored content, stage states, classification and named entities.

One routine for every caller that must leave a record not searchable while the record itself stays. Every step is
idempotent and raises on failure, so the caller's retry repeats the whole routine.

The record's status and revision tombstone are written by the caller **before** this runs, so a stage job still queued
for the old content fails its revision check and writes nothing. The routine takes no lease of its own: the consumer's
record lease for the event already excludes stage writers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.indexing.taxonomy_cleanup import delete_unreached_taxonomy_points
from app.modules.named_entities.graph_writer import NamedEntityGraphWriter
from app.modules.transformers.graphdb import GraphDBTransformer
from app.services.graph_db.common.utils import taxonomy_refs_from_links

if TYPE_CHECKING:
    from collections.abc import Mapping
    from logging import Logger


async def unindex_record(
    record_doc: Mapping[str, Any],
    org_id: str,
    *,
    processor: Any,  # noqa: ANN401 - the indexing ``Processor``; typing it imports the events package
    logger: Logger,
) -> None:
    """Remove everything indexing produced for ``record_doc``. ``processor`` is the ``Processor`` (graph provider,
    indexing pipeline, sink orchestrator). The record's title point stays: the record still exists and is findable by name.
    """
    graph = processor.graph_provider
    record_key = record_doc.get("_key") or record_doc.get("id")
    if not record_key:
        raise DocumentProcessingError("A record to un-index has no key")
    record_key = str(record_key)
    virtual_record_id = record_doc.get("virtualRecordId")
    sink = getattr(processor, "sink_orchestrator", None)

    # Read before anything is cleared, and allowed to raise: the refs cannot be found again once the edges are gone.
    links = await graph.get_record_taxonomy_links([record_key], include_departments=True)
    refs = taxonomy_refs_from_links(links).get(record_key, [])

    await GraphDBTransformer(graph, logger).clear_classification(record_key)

    writer = NamedEntityGraphWriter(graph, GraphDataStore(logger, graph), logger)
    await writer.clear_for_record(record_key)

    if not await graph.update_node(
        record_key, CollectionNames.RECORDS.value, {"summaryDocumentId": None, "isVLMOcrProcessed": False}
    ):
        raise DocumentProcessingError("Failed to clear the classification fields of a record", doc_id=record_key)

    if virtual_record_id:
        others = [
            key
            for key in await graph.get_records_by_virtual_record_id(virtual_record_id, raise_on_error=True)
            if key != record_key
        ]
        if not others:
            await graph.stage_states_delete_for_virtual_records([virtual_record_id])
        await processor._release_skipped_content(org_id, record_key, virtual_record_id)

    store = getattr(sink, "entity_vector_store", None)
    if store is not None and refs:
        await delete_unreached_taxonomy_points(store, graph, org_id, record_key, refs)
