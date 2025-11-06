from dataclasses import dataclass, field
from typing import Any

from app.models.graph import Node


@dataclass
class RecordGroup(Node):
    _key: str
    org_id: str = ""
    name: str = ""
    description: str | None = None
    created_at_timestamp: float | None = None
    updated_at_timestamp: float | None = None
    last_sync_timestamp: float | None = None
    source_created_at_timestamp: float | None = None
    source_last_modified_timestamp: float | None = None

@dataclass
class Record(Node):
    """Base record class for all types of records"""

    _key: str = ""
    org_id: str = ""
    record_name: str = ""
    external_record_id: str = ""
    external_revision_id: str | None = None
    record_type: str = ""  # FILE, DRIVE, WEBPAGE, MESSAGE, MAIL, OTHERS
    version: int = 0
    origin: str = ""  # UPLOAD or CONNECTOR
    connector_name: str | None = None
    created_at_timestamp: float | None = None
    updated_at_timestamp: float | None = None
    last_sync_timestamp: float | None = None
    source_created_at_timestamp: float | None = None
    source_last_modified_timestamp: float | None = None
    indexing_status: str | None = None
    extraction_status: str | None = None
    is_latest_version: bool = True
    is_deleted: bool = False
    is_archived: bool = False
    is_dirty: bool = False
    reason: str | None = None
    last_index_timestamp: float | None = None
    last_extraction_timestamp: float | None = None
    summary_document_id: str | None = None
    virtual_record_id: str | None = None
    deleted_by_user_id: str | None = None
    web_url: str | None = None,
    mime_type: str | None = None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Record":
        record = Record()

        record._key = data.get("_key", "")
        record.org_id = data.get("orgId", "")
        record.record_name = data.get("recordName", "")
        record.external_record_id = data.get("externalRecordId", "")
        record.external_revision_id = data.get("externalRevisionId")
        record.record_type = data.get("recordType", "")
        record.version = data.get("version", 0)
        record.origin = data.get("origin", "UPLOAD")
        record.connector_name = data.get("connectorName")
        record.created_at_timestamp = data.get("createdAtTimestamp")
        record.updated_at_timestamp = data.get("updatedAtTimestamp")
        record.last_sync_timestamp = data.get("lastSyncTimestamp")
        record.source_created_at_timestamp = data.get("sourceCreatedAtTimestamp")
        record.source_last_modified_timestamp = data.get("sourceLastModifiedTimestamp")
        record.indexing_status = data.get("indexingStatus")
        record.extraction_status = data.get("extractionStatus")
        record.is_latest_version = data.get("isLatestVersion", True)
        record.is_deleted = data.get("isDeleted", False)
        record.is_archived = data.get("isArchived", False)
        record.is_dirty = data.get("isDirty", False)
        record.reason = data.get("reason")
        record.last_index_timestamp = data.get("lastIndexTimestamp")
        record.last_extraction_timestamp = data.get("lastExtractionTimestamp")
        record.summary_document_id = data.get("summaryDocumentId")
        record.virtual_record_id = data.get("virtualRecordId")
        record.deleted_by_user_id = data.get("deletedByUserId")
        record.web_url = data.get("webUrl")
        record.mime_type = data.get("mimeType")
        return record

    def __post_init__(self) -> None:
        # if not self._key:
        #     raise ValueError("_key must be set")
        # if not self.org_id:
        #     raise ValueError("org_id must be set")
        if not self.record_name:
            raise ValueError("record_name must be set")
        if not self.external_record_id:
            raise ValueError("external_record_id must be set")
        if not self.record_type:
            raise ValueError("record_type must be set")
        if not self.origin:
            raise ValueError("origin must be set")
        if not self.connector_name:
            raise ValueError("connector_name must be set")
        if not self.created_at_timestamp:
            raise ValueError("created_at_timestamp must be set")
        if not self.updated_at_timestamp:
            raise ValueError("updated_at_timestamp must be set")

    # def __setattr__(self, name, value) -> None:
    #     if name == '_key' and hasattr(self, '_key'):
    #         raise AttributeError("Cannot modify _key after initialization")
    #     super().__setattr__(name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "_key": self._key,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "externalRecordId": self.external_record_id,
            "externalRevisionId": self.external_revision_id,
            "recordType": self.record_type,
            "version": self.version,
            "origin": self.origin,
            "connectorName": self.connector_name,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "indexingStatus": self.indexing_status,
            "extractionStatus": self.extraction_status,
            "isLatestVersion": self.is_latest_version,
            "isDeleted": self.is_deleted,
            "isArchived": self.is_archived,
            "sourceCreatedAtTimestamp": self.source_created_at_timestamp,
            "sourceLastModifiedTimestamp": self.source_last_modified_timestamp,
            "lastIndexTimestamp": self.last_index_timestamp,
            "lastExtractionTimestamp": self.last_extraction_timestamp,
            "summaryDocumentId": self.summary_document_id,
            "virtualRecordId": self.virtual_record_id,
            "isDirty": self.is_dirty,
            "reason": self.reason,
            "lastSyncTimestamp": self.last_sync_timestamp,
            "deletedByUserId": self.deleted_by_user_id,
            "webUrl": self.web_url,
            "mimeType": self.mime_type,
        }

    def validate(self) -> bool:
        valid_record_types = ["FILE", "DRIVE", "WEBPAGE", "MESSAGE", "MAIL", "OTHERS"]
        valid_origins = ["UPLOAD", "CONNECTOR"]
        return (
            self.record_type in valid_record_types and
            self.origin in valid_origins
        )

    @property
    def key(self) -> str:
        return self._key

@dataclass
class FileRecord(Node):
    """Specific record type for files"""

    _key: str = ""
    org_id: str = ""
    name: str = ""
    is_file: bool = True
    extension: str | None = None
    mime_type: str | None = None
    size_in_bytes: int | None = None
    web_url: str | None = None
    path: str | None = None

    etag: str | None = None
    ctag: str | None = None
    md5_checksum: str | None = None
    quick_xor_hash: str | None = None
    crc32_hash: str | None = None
    sha1_hash: str | None = None
    sha256_hash: str | None = None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "FileRecord":
        file_record = FileRecord()
        file_record._key = data.get("_key", "")
        file_record.org_id = data.get("orgId", "")
        file_record.name = data.get("name", "")

        file_record.is_file = data.get("isFile", True)
        file_record.extension = data.get("extension")
        file_record.mime_type = data.get("mimeType")
        file_record.size_in_bytes = data.get("sizeInBytes")
        file_record.web_url = data.get("webUrl")
        file_record.path = data.get("path")
        file_record.etag = data.get("etag")
        file_record.ctag = data.get("ctag")
        file_record.md5_checksum = data.get("md5Checksum")
        file_record.quick_xor_hash = data.get("quickXorHash")
        file_record.crc32_hash = data.get("crc32Hash")
        file_record.sha1_hash = data.get("sha1Hash")
        file_record.sha256_hash = data.get("sha256Hash")

        return file_record

    def __post_init__(self) -> None:
        # if not self._key:
        #     raise ValueError("_key must be set")
        # if not self.org_id:
        #     raise ValueError("org_id must be set")
        if not self.name:
            raise ValueError("name must be set")

    def to_dict(self) -> dict[str, Any]:
        return {
            "_key": self._key,
            "orgId": self.org_id,
            "name": self.name,
            "isFile": self.is_file,
            "extension": self.extension,
            "mimeType": self.mime_type,
            "sizeInBytes": self.size_in_bytes,
            "webUrl": self.web_url,
            "path": self.path,
            "etag": self.etag,
            "ctag": self.ctag,
            "md5Checksum": self.md5_checksum,
            "quickXorHash": self.quick_xor_hash,
            "crc32Hash": self.crc32_hash,
            "sha1Hash": self.sha1_hash,
            "sha256Hash": self.sha256_hash,
        }

    def validate(self) -> bool:
        return len(self.name) > 0

    @property
    def key(self) -> str:
        return self._key

@dataclass
class MailRecord(Node):
    """Specific record type for emails"""

    _key: str
    thread_id: str = ""
    is_parent: bool = False
    subject: str | None = None
    from_address: str | None = None
    to_addresses: list[str] = field(default_factory=list)
    cc_addresses: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)
    web_url: str | None = None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "MailRecord":
        mail_record = MailRecord()
        mail_record._key = data.get("_key", "")
        mail_record.thread_id = data.get("threadId", "")
        mail_record.is_parent = data.get("isParent", False)
        mail_record.subject = data.get("subject")
        mail_record.from_address = data.get("from")
        mail_record.to_addresses = data.get("to", [])
        mail_record.cc_addresses = data.get("cc", [])
        mail_record.bcc_addresses = data.get("bcc", [])
        mail_record.web_url = data.get("webUrl")

        return mail_record

    def __post_init__(self) -> None:
        if not self._key:
            raise ValueError("_key must be set")
        if not self.thread_id:
            raise ValueError("thread_id must be set")
        if not self.is_parent:
            raise ValueError("is_parent must be set")
        if not self.subject:
            raise ValueError("subject must be set")
        if not self.from_address:
            raise ValueError("from_address must be set")


    def to_dict(self) -> dict[str, Any]:
        return {
            "_key": self._key,
            "threadId": self.thread_id,
            "isParent": self.is_parent,
            "subject": self.subject,
            "from": self.from_address,
            "to": self.to_addresses,
            "cc": self.cc_addresses,
            "bcc": self.bcc_addresses,
            "webUrl": self.web_url,
        }

    def validate(self) -> bool:
        return len(self.thread_id) > 0

    @property
    def key(self) -> str:
        return self._key
