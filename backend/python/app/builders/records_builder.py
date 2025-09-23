from datetime import datetime
from typing import List

from app.models.records import FileRecord, MailRecord, Record
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RecordBuilder:
    """Builder for creating Record instances with fluent interface"""

    def __init__(self, _key: str, org_id: str, record_name: str, external_record_id: str, record_type: str, origin: str) -> None:
        self._key = _key
        self._org_id = org_id
        self._record_name = record_name
        self._external_record_id = external_record_id
        self._external_revision_id = None

        self._record_type = record_type
        self._version = 0
        self._origin = origin
        self._connector_name = None
        self._created_at_timestamp = get_epoch_timestamp_in_ms()
        self._updated_at_timestamp = self._created_at_timestamp
        self._indexing_status = None
        self._extraction_status = None
        self._is_latest_version = True
        self._is_deleted = False
        self._is_archived = False
        self._summary_document_id = None
        self._virtual_record_id = None
        self._last_sync_timestamp = None
        self._source_created_at_timestamp = None
        self._source_last_modified_timestamp = None
        self._deleted_by_user_id = None
        self._last_index_timestamp = None
        self._last_extraction_timestamp = None
        self._is_dirty = False
        self._reason = None
        self._web_url = None
        self._mime_type = None

    def with_key(self, key: str) -> 'RecordBuilder':
        self._key = key
        return self

    def with_org_id(self, org_id: str) -> 'RecordBuilder':
        self._org_id = org_id
        return self

    def with_name(self, name: str) -> 'RecordBuilder':
        self._record_name = name
        return self

    def with_external_id(self, external_id: str) -> 'RecordBuilder':
        self._external_record_id = external_id
        if not self._key:
            self._key = external_id
        return self

    def with_type(self, record_type: str) -> 'RecordBuilder':
        if record_type not in ["FILE", "DRIVE", "WEBPAGE", "MESSAGE", "MAIL", "OTHERS"]:
            raise ValueError(f"Invalid record type: {record_type}")
        self._record_type = record_type
        return self

    def with_version(self, version: int) -> 'RecordBuilder':
        self._version = version
        return self

    def with_origin(self, origin: str) -> 'RecordBuilder':
        if origin not in ["UPLOAD", "CONNECTOR"]:
            raise ValueError(f"Invalid origin: {origin}")
        self._origin = origin
        return self

    def with_connector(self, connector_name: str) -> 'RecordBuilder':
        valid_connectors = ["ONEDRIVE", "DRIVE", "CONFLUENCE", "GMAIL", "SLACK", "NOTION", "LINEAR", "SHAREPOINT ONLINE"]
        if connector_name and connector_name not in valid_connectors:
            raise ValueError(f"Invalid connector: {connector_name}")
        self._connector_name = connector_name
        return self

    def with_indexing_status(self, status: str) -> 'RecordBuilder':
        valid_statuses = ["NOT_STARTED", "IN_PROGRESS", "PAUSED", "FAILED",
                         "COMPLETED", "FILE_TYPE_NOT_SUPPORTED", "AUTO_INDEX_OFF"]
        if status not in valid_statuses:
            raise ValueError(f"Invalid indexing status: {status}")
        self._indexing_status = status
        return self

    def with_extraction_status(self, status: str) -> 'RecordBuilder':
        valid_statuses = ["NOT_STARTED", "IN_PROGRESS", "PAUSED", "FAILED",
                         "COMPLETED", "FILE_TYPE_NOT_SUPPORTED", "AUTO_INDEX_OFF"]
        if status not in valid_statuses:
            raise ValueError(f"Invalid extraction status: {status}")
        self._extraction_status = status
        return self

    def as_deleted(self, deleted_by: str = None) -> 'RecordBuilder':
        self._is_deleted = True
        self._deleted_by_user_id = deleted_by
        return self

    def as_archived(self) -> 'RecordBuilder':
        self._is_archived = True
        return self

    def as_older_version(self) -> 'RecordBuilder':
        self._is_latest_version = False
        return self

    def with_timestamps(self, created: float = None, updated: float = None) -> 'RecordBuilder':
        self._created_at_timestamp = created or datetime.now().timestamp()
        self._updated_at_timestamp = updated or self._created_at_timestamp
        return self

    def with_source_timestamps(self, created: float = None, modified: float = None) -> 'RecordBuilder':
        self._source_created_at_timestamp = created
        self._source_last_modified_timestamp = modified
        return self

    def with_external_revision_id(self, external_revision_id: str) -> 'RecordBuilder':
        self._external_revision_id = external_revision_id
        return self

    def with_summary_document_id(self, summary_document_id: str) -> 'RecordBuilder':
        self._summary_document_id = summary_document_id
        return self

    def with_virtual_record_id(self, virtual_record_id: str) -> 'RecordBuilder':
        self._virtual_record_id = virtual_record_id
        return self

    def with_last_sync_timestamp(self, last_sync_timestamp: float) -> 'RecordBuilder':
        self._last_sync_timestamp = last_sync_timestamp
        return self

    def with_last_index_timestamp(self, last_index_timestamp: float) -> 'RecordBuilder':
        self._last_index_timestamp = last_index_timestamp
        return self

    def with_last_extraction_timestamp(self, last_extraction_timestamp: float) -> 'RecordBuilder':
        self._last_extraction_timestamp = last_extraction_timestamp
        return self

    def with_deleted_by_user_id(self, deleted_by_user_id: str) -> 'RecordBuilder':
        self._deleted_by_user_id = deleted_by_user_id
        return self

    def with_reason(self, reason: str) -> 'RecordBuilder':
        self._reason = reason
        return self

    def with_is_dirty(self, is_dirty: bool) -> 'RecordBuilder':
        self._is_dirty = is_dirty
        return self

    def with_web_url(self, web_url: str) -> 'RecordBuilder':
        self._web_url = web_url
        return self

    def with_mime_type(self, mime_type: str) -> 'RecordBuilder':
        self._mime_type = mime_type
        return self


    def build(self) -> 'Record':
        """Build and validate the Record instance"""
        if not self._org_id:
            raise ValueError("org_id is required")
        if not self._record_name:
            raise ValueError("record_name is required")
        if not self._external_record_id:
            raise ValueError("external_record_id is required")
        if not self._record_type:
            raise ValueError("record_type is required")

        return Record(
            _key=self._key,
            org_id=self._org_id,
            record_name=self._record_name,
            external_record_id=self._external_record_id,
            external_revision_id=self._external_revision_id,
            record_type=self._record_type,
            version=self._version,
            origin=self._origin,
            connector_name=self._connector_name,
            created_at_timestamp=self._created_at_timestamp,
            updated_at_timestamp=self._updated_at_timestamp,
            indexing_status=self._indexing_status,
            extraction_status=self._extraction_status,
            is_latest_version=self._is_latest_version,
            is_deleted=self._is_deleted,
            is_archived=self._is_archived,
            summary_document_id=self._summary_document_id,
            virtual_record_id=self._virtual_record_id,
            last_sync_timestamp=self._last_sync_timestamp,
            source_created_at_timestamp=self._source_created_at_timestamp,
            source_last_modified_timestamp=self._source_last_modified_timestamp,
            deleted_by_user_id=self._deleted_by_user_id,
            last_index_timestamp=self._last_index_timestamp,
            last_extraction_timestamp=self._last_extraction_timestamp,
            is_dirty=self._is_dirty,
            reason=self._reason,
            web_url=self._web_url,
            mime_type=self._mime_type
        )

class FileRecordBuilder:
    """Builder for creating FileRecord instances"""

    def __init__(self, _key: str, org_id: str, name: str) -> None:
        self._key = _key
        self._org_id = org_id
        self._name = name
        self._is_file = True
        if '.' in name:
            self._extension = name.split('.')[-1]
        self._mime_type = None
        self._size_in_bytes = None
        self._web_url = None
        self._path = None
        self._md5_checksum = None
        self._etag = None
        self._ctag = None
        self._quick_xor_hash = None
        self._crc32_hash = None
        self._sha1_hash = None
        self._sha256_hash = None

    def with_key(self, key: str) -> 'FileRecordBuilder':
        self._key = key
        return self

    def with_org_id(self, org_id: str) -> 'FileRecordBuilder':
        self._org_id = org_id
        return self

    def with_name(self, name: str) -> 'FileRecordBuilder':
        self._name = name
        # Auto-extract extension if not set
        if not self._extension and '.' in name:
            self._extension = name.split('.')[-1]
        return self

    def as_folder(self) -> 'FileRecordBuilder':
        self._is_file = False
        self._extension = None
        return self

    def with_extension(self, extension: str)  -> 'FileRecordBuilder':
        self._extension = extension
        return self

    def with_is_file(self, is_file: bool) -> 'FileRecordBuilder':
        self._is_file = is_file
        return self

    def with_mime_type(self, mime_type: str) -> 'FileRecordBuilder':
        self._mime_type = mime_type
        return self

    def with_size(self, size_in_bytes: int) -> 'FileRecordBuilder':
        self._size_in_bytes = size_in_bytes
        return self

    def with_url(self, web_url: str) -> 'FileRecordBuilder':
        self._web_url = web_url
        return self

    def with_path(self, path: str) -> 'FileRecordBuilder':
        self._path = path
        return self

    def with_etag(self, etag: str) -> 'FileRecordBuilder':
        self._etag = etag
        return self

    def with_ctag(self, ctag: str) -> 'FileRecordBuilder':
        self._ctag = ctag
        return self

    def with_checksums(self, quick_xor_hash: str = None, crc32_hash: str = None, md5: str = None, sha1: str = None, sha256: str = None) -> 'FileRecordBuilder':
        if quick_xor_hash:
            self._quick_xor_hash = quick_xor_hash
        if crc32_hash:
            self._crc32_hash = crc32_hash
        if md5:
            self._md5_checksum = md5
        if sha1:
            self._sha1_hash = sha1
        if sha256:
            self._sha256_hash = sha256
        return self

    def build(self) -> FileRecord:
        """Build and validate the FileRecord instance"""
        if not self._org_id:
            raise ValueError("org_id is required")
        if not self._name:
            raise ValueError("name is required")

        return FileRecord(
            _key=self._key,
            org_id=self._org_id,
            name=self._name,
            is_file=self._is_file,
            extension=self._extension,
            mime_type=self._mime_type,
            size_in_bytes=self._size_in_bytes,
            web_url=self._web_url,
            path=self._path,
            md5_checksum=self._md5_checksum
        )

class MailRecordBuilder:
    """Builder for creating MailRecord instances"""

    def __init__(self) -> None:
        self._key = None
        self._thread_id = None
        self._is_parent = False
        self._subject = None
        self._from_address = None
        self._to_addresses = []
        self._cc_addresses = []
        self._bcc_addresses = []
        self._web_url = None
        self._internal_date = None
        self._date = None
        self._message_id_header = None
        self._history_id = None
        self._label_ids = []

    def with_key(self, key: str) -> 'MailRecordBuilder':
        self._key = key
        return self

    def with_thread_id(self, thread_id: str) -> 'MailRecordBuilder':
        self._thread_id = thread_id
        return self

    def as_parent(self) -> 'MailRecordBuilder':
        self._is_parent = True
        return self

    def with_subject(self, subject: str) -> 'MailRecordBuilder':
        self._subject = subject
        return self

    def from_sender(self, from_address: str) -> 'MailRecordBuilder':
        self._from_address = from_address
        return self

    def to_recipients(self, *recipients: str) -> 'MailRecordBuilder':
        self._to_addresses.extend(recipients)
        return self

    def cc_recipients(self, *recipients: str) -> 'MailRecordBuilder':
        self._cc_addresses.extend(recipients)
        return self

    def bcc_recipients(self, *recipients: str) -> 'MailRecordBuilder':
        self._bcc_addresses.extend(recipients)
        return self

    def with_url(self, web_url: str) -> 'MailRecordBuilder':
        self._web_url = web_url
        return self

    def with_dates(self, internal_date: str = None, date: str = None) -> 'MailRecordBuilder':
        if internal_date:
            self._internal_date = internal_date
        if date:
            self._date = date
        return self

    def with_gmail_metadata(self, message_id: str = None,
                           history_id: str = None,
                           label_ids: List[str] = None) -> 'MailRecordBuilder':
        """Convenience method for Gmail messages"""
        if message_id:
            self._message_id_header = message_id
        if history_id:
            self._history_id = history_id
        if label_ids:
            self._label_ids = label_ids
        return self

    def build(self) -> 'MailRecord':
        """Build and validate the MailRecord instance"""
        if not self._thread_id:
            raise ValueError("thread_id is required")

        return MailRecord(
            _key=self._key,
            thread_id=self._thread_id,
            is_parent=self._is_parent,
            subject=self._subject,
            from_address=self._from_address,
            to_addresses=self._to_addresses,
            cc_addresses=self._cc_addresses,
            bcc_addresses=self._bcc_addresses,
            web_url=self._web_url
        )
