"""Dropping the previous vectors of a record that an update left empty."""

from typing import Any

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.reconciliation.service import ReconciliationMetadata
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.vector_db.strategy import RecordContext


async def drop_previous_vectors_of_emptied_record(
    *,
    graph_provider: Any,  # noqa: ANN401
    pipeline: Any,  # noqa: ANN401
    orchestrator: Any,  # noqa: ANN401
    doc: dict[str, Any],
    virtual_record_id: str | None,
    event_type: str | None,
) -> None:
    """An update that leaves a record empty must not leave its old content searchable.

    Every type still holds its vectors when this runs: nothing is taken out
    ahead of the work. A VRID another record also holds is that record's
    content, so it is left alone.
    """
    if not virtual_record_id or event_type not in (
        EventTypes.UPDATE_RECORD.value,
        EventTypes.REINDEX_RECORD.value,
    ):
        return
    record_id = doc.get("_key") or doc.get("id")
    details = {"record_id": record_id, "virtual_record_id": virtual_record_id}
    # An unreadable graph also answers []; raising lets the event retry rather
    # than reading the failure as "nobody else holds this content". A trashed
    # holder counts: a restore reads this content.
    try:
        holders = await graph_provider.get_records_by_virtual_record_id(
            virtual_record_id, raise_on_error=True, visibility=RecordVisibility.ALL
        )
    except Exception as e:
        raise IndexingError("Could not tell whether another record shares this content", details=details) from e
    if set(holders or []) != {record_id}:
        return
    if pipeline is None:
        # Completing anyway would leave the old content in the vector store for good.
        raise IndexingError("No indexing pipeline to delete the previous vectors of an emptied record", details=details)
    # An IndexingError, so a processor's catch-all does not turn a vector-store outage into a final failure.
    try:
        await pipeline.delete_points_for_virtual_record(virtual_record_id, RecordContext.from_graph_document(doc))
    except IndexingError:
        raise
    except Exception as e:
        raise IndexingError("Could not delete the previous vectors of an emptied record", details=details) from e
    # The VRID is kept, so without this the next diff would pair unchanged
    # blocks with the points just deleted and embed nothing.
    try:
        if orchestrator is None:
            raise RuntimeError("no sink orchestrator to reset the reconciliation metadata")
        org_id = doc.get("orgId")
        await orchestrator.blob_storage.save_reconciliation_metadata(
            org_id, record_id, virtual_record_id, ReconciliationMetadata().to_dict(),
            document_path=await orchestrator.blob_storage.get_actual_content_path(org_id, virtual_record_id),
            connector_id=doc.get("connectorId"),
            record_group_id=doc.get("recordGroupId"),
        )
    except Exception as e:
        raise IndexingError("Could not reset the stored block list of an emptied record", details=details) from e
