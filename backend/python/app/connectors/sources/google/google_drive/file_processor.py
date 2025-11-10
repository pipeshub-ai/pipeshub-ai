import uuid

from app.builders.records_builder import FileRecordBuilder, RecordBuilder
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.models.records import FileRecord, Record
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


async def process_drive_file(
    metadata: dict, org_id: str
) -> tuple[FileRecord, Record, dict]:
    file_id = metadata.get("id")

    # Prepare File, Record and File Metadata
    file_record = (
        FileRecordBuilder(
            _key=str(uuid.uuid4()),
            org_id=org_id,
            name=str(metadata.get("name")),
        )
        .with_is_file(
            metadata.get("mimeType", "") != MimeTypes.GOOGLE_DRIVE_FOLDER.value
        )
        .with_extension(metadata.get("fileExtension"))
        .with_mime_type(metadata.get("mimeType"))
        .with_size(int(metadata.get("size", 0)))
        .with_url(metadata.get("webViewLink"))
        .with_path(metadata.get("path"))
        .with_etag(metadata.get("etag"))
        .with_ctag(metadata.get("ctag"))
        .with_checksums(
            quick_xor_hash=metadata.get("quickXorHash"),
            crc32_hash=metadata.get("crc32Hash"),
            md5=metadata.get("md5Checksum"),
            sha1=metadata.get("sha1Checksum"),
            sha256=metadata.get("sha256Checksum"),
        )
        .build()
    )
    # Shared files are not indexed and extracted by default as any public file opened by a user in Google Drive
    # is also shown as shared file
    is_shared = metadata.get("isSharedWithMe", False)
    status = (
        ProgressStatus.AUTO_INDEX_OFF.value
        if is_shared
        else ProgressStatus.NOT_STARTED.value
    )
    record = (
        RecordBuilder(
            _key=str(file_record.key),
            org_id=org_id,
            record_name=file_record.name,
            external_record_id=str(file_id),
            record_type=RecordTypes.FILE.value,
            origin=OriginTypes.CONNECTOR.value,
        )
        .with_external_revision_id(metadata.get("headRevisionId"))
        .with_connector(Connectors.GOOGLE_DRIVE.value)
        .with_source_timestamps(
            created=int(parse_timestamp(metadata.get("createdTime"))),
            modified=int(parse_timestamp(metadata.get("modifiedTime"))),
        )
        .with_indexing_status(status)
        .with_extraction_status(status)
        .with_web_url(metadata.get("webViewLink"))
        .with_mime_type(metadata.get("mimeType"))
        .build()
    )

    is_of_type_record = {
        "_from": f"{CollectionNames.RECORDS.value}/{record.key}",
        "_to": f"{CollectionNames.FILES.value}/{file_record.key}",
        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }

    return file_record, record, is_of_type_record
