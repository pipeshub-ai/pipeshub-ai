"""Placeholder parent records for ``{share}/{path}`` directory ids."""

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, Record, RecordType


class NetworkShareEntitiesProcessor(DataSourceEntitiesProcessor):
    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
        record_name: str | None = None,
        record_group_type: str | None = None,
        external_record_group_id: str | None = None,
    ) -> Record:
        parent_record = super()._create_placeholder_parent_record(
            parent_external_id,
            parent_record_type,
            record,
            record_name=record_name,
            record_group_type=record_group_type,
            external_record_group_id=external_record_group_id,
        )
        if parent_record_type == RecordType.FILE and isinstance(parent_record, FileRecord):
            path = None
            if "/" in parent_external_id:
                path = parent_external_id.split("/", 1)[1]
            parent_record.path = path
            parent_record.is_internal = True
            parent_record.hide_weburl = True
            parent_record.mime_type = MimeTypes.FOLDER.value
        return parent_record
