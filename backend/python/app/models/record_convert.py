"""Build a ``Record`` from a stored record document.

Kept out of ``app.events.processor`` so callers that only need this conversion (the
chat route, the Kafka record handler) do not import the parser and event-processing
stack with it. ``app.events.processor`` re-exports the name.
"""
from typing import Any, cast

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.sources.localKB.push.contract import PUSH_ORIGIN_FIELD
from app.models.entities import Record, RecordType
from app.models.source_dates import SOURCE_TIMESTAMP_ORIGIN_FIELD
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def convert_record_dict_to_record(record_dict: dict[str, Any]) -> Record:
    conn_name_value = record_dict.get("connectorName")
    try:
        connector_name = (
            Connectors(conn_name_value)
            if conn_name_value is not None
            else Connectors.KNOWLEDGE_BASE
        )
    except ValueError:
        connector_name = Connectors.KNOWLEDGE_BASE
    origin_value = record_dict.get("origin", OriginTypes.UPLOAD.value)
    try:
        origin = OriginTypes(origin_value)
    except ValueError:
        origin = OriginTypes.UPLOAD

    mime_type = cast(str, record_dict.get("mimeType"))

    # Record validates the required string fields; a document missing one fails there.
    return Record(
        id=cast(str, record_dict.get("_key") or record_dict.get("id")),
        org_id=cast(str, record_dict.get("orgId")),
        record_name=cast(str, record_dict.get("recordName")),
        record_type=RecordType(record_dict.get("recordType", "FILE")),
        record_status=ProgressStatus(record_dict.get("indexingStatus", "NOT_STARTED")),
        indexing_status=record_dict.get("indexingStatus", ProgressStatus.QUEUED.value),
        extraction_status=record_dict.get("extractionStatus", ProgressStatus.NOT_STARTED.value),
        external_record_id=cast(str, record_dict.get("externalRecordId")),
        version=record_dict.get("version", 1),
        origin=origin,
        summary_document_id=record_dict.get("summaryDocumentId"),
        created_at=(
            record_dict["createdAtTimestamp"]
            if record_dict.get("createdAtTimestamp") is not None
            else get_epoch_timestamp_in_ms()
        ),
        updated_at=(
            record_dict["updatedAtTimestamp"]
            if record_dict.get("updatedAtTimestamp") is not None
            else get_epoch_timestamp_in_ms()
        ),
        source_created_at=record_dict.get("sourceCreatedAtTimestamp"),
        source_updated_at=record_dict.get("sourceLastModifiedTimestamp"),
        source_timestamp_origin=record_dict.get(SOURCE_TIMESTAMP_ORIGIN_FIELD),
        weburl=record_dict.get("webUrl"),
        mime_type=mime_type,
        external_revision_id=record_dict.get("externalRevisionId"),
        connector_name=connector_name,
        is_vlm_ocr_processed=record_dict.get("isVLMOcrProcessed", False),
        connector_id=cast(str, record_dict.get("connectorId")),
        md5_hash=record_dict.get("md5Checksum"),
        record_group_id=record_dict.get("recordGroupId"),
        external_record_group_id=record_dict.get("externalGroupId"),
        # A named-entity persist retry rebuilds the record from its document and finds the
        # stored extraction by this.
        virtual_record_id=record_dict.get("virtualRecordId"),
        push_origin=record_dict.get(PUSH_ORIGIN_FIELD) is True,
    )
