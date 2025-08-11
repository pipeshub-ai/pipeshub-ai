from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.blocks import BlocksContainer, SemanticMetadata
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RecordType(str, Enum):
    FILE = "FILE"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    MESSAGE = "MESSAGE"
    MAIL = "MAIL"
    OTHERS = "OTHERS"

class RecordStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    MANUAL_SYNC = "MANUAL_SYNC"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"

class Record(BaseModel):
    # Core record properties
    id: str = Field(description="Unique identifier for the record", default_factory=lambda: str(uuid4()))
    org_id: str = Field(description="Unique identifier for the organization", default="")
    record_name: str = Field(description="Human-readable name for the record")
    record_type: RecordType = Field(description="Type/category of the record")
    record_status: RecordStatus = Field(default=RecordStatus.NOT_STARTED)
    parent_record_type: Optional[str] = Field(default=None, description="Type of the parent record")
    record_group_type: Optional[str] = Field(description="Type of the record group")
    external_record_id: str = Field(description="Unique identifier for the record in the external system")
    external_revision_id: Optional[str] = Field(default=None, description="Unique identifier for the revision of the record in the external system")
    external_record_group_id: Optional[str] = Field(default=None, description="Unique identifier for the record group in the external system")
    parent_external_record_id: Optional[str] = Field(default=None, description="Unique identifier for the parent record in the external system")
    version: int = Field(description="Version of the record")
    origin: str = Field(description="Origin of the record")
    connector_name: Optional[str] = Field(description="Name of the connector used to create the record")
    virtual_record_id: Optional[str] = Field(description="Virtual record identifier", default=None)
    summary_document_id: Optional[str] = Field(description="Summary document identifier", default=None)
    md5_hash: Optional[str] = Field(default=None, description="MD5 hash of the record")
    mime_type: Optional[str] = Field(default=None, description="MIME type of the record")
    # Epoch Timestamps
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record update in the source system")

    # Source information
    weburl: Optional[str] = None
    signed_url: Optional[str] = None
    fetch_signed_url: Optional[str] = None
    mime_type: Optional[str] = None
    # Content blocks
    block_containers: BlocksContainer = Field(default_factory=BlocksContainer, description="List of block containers in this record")

    semantic_metadata: Optional[SemanticMetadata] = None

    # Relationships
    parent_record_id: Optional[str] = None
    child_record_ids: Optional[List[str]] = Field(default_factory=list)
    related_record_ids: Optional[List[str]] = Field(default_factory=list)

    def to_arango_base_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "externalRevisionId": self.external_revision_id,
            "version": self.version,
            "origin": self.origin,
            "connectorName": self.connector_name,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "indexingStatus": "NOT_STARTED",
            "extractionStatus": "NOT_STARTED",
            "isDeleted": False,
            "isArchived": False,
            "deletedByUserId": None,
        }

    @staticmethod
    def from_arango_base_record(arango_base_record: Dict) -> "Record":
        return Record(
            id=arango_base_record["_key"],
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=arango_base_record["recordType"],
            record_group_type=None,
            external_record_id=arango_base_record["externalRecordId"],
            version=arango_base_record["version"],
            origin=arango_base_record["origin"],
            connector_name=arango_base_record["connectorName"],
            mime_type=arango_base_record["mimeType"],
            weburl=arango_base_record["webUrl"],
            created_at=arango_base_record["createdAtTimestamp"],
            updated_at=arango_base_record["updatedAtTimestamp"],
            source_created_at=arango_base_record["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record["sourceLastModifiedTimestamp"],
        )

    def to_kafka_record(self) -> Dict:
        pass

class FileRecord(Record):
    is_file: bool
    size_in_bytes: int = None
    extension: Optional[str] = None
    path: Optional[str] = None
    etag: Optional[str] = None
    ctag: Optional[str] = None
    quick_xor_hash: Optional[str] = None
    crc32_hash: Optional[str] = None
    sha1_hash: Optional[str] = None
    sha256_hash: Optional[str] = None

    def to_arango_file_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "isFile": self.is_file,
            "extension": self.extension,
            "mimeType": self.mime_type,
            "sizeInBytes": self.size_in_bytes,
            "webUrl": self.weburl,
            "etag": self.etag,
            "ctag": self.ctag,
            "md5Checksum": self.md5_hash,
            "quickXorHash": self.quick_xor_hash,
            "crc32Hash": self.crc32_hash,
            "sha1Hash": self.sha1_hash,
            "sha256Hash": self.sha256_hash,
            "path": self.path,
        }

    @staticmethod
    def from_arango_base_file_record(arango_base_file_record: Dict, arango_base_record: Dict) -> "FileRecord":
        return FileRecord(
            id=arango_base_record["_key"],
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=arango_base_record["recordType"],
            external_record_id=arango_base_record["externalRecordId"],
            version=arango_base_record["version"],
            origin=arango_base_record["origin"],
            connector_name=arango_base_record["connectorName"],
            mime_type=arango_base_record["mimeType"],
            weburl=arango_base_record["webUrl"],
            created_at=arango_base_record["createdAtTimestamp"],
            updated_at=arango_base_record["updatedAtTimestamp"],
            source_created_at=arango_base_record["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record["sourceLastModifiedTimestamp"],
            size_in_bytes=arango_base_file_record["sizeInBytes"],
            extension=arango_base_file_record["extension"],
            path=arango_base_file_record["path"],
            etag=arango_base_file_record["etag"],
            ctag=arango_base_file_record["ctag"],
            quick_xor_hash=arango_base_file_record["quickXorHash"],
            crc32_hash=arango_base_file_record["crc32Hash"],
            sha1_hash=arango_base_file_record["sha1Hash"],
            sha256_hash=arango_base_file_record["sha256Hash"],
        )

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "version": self.version,
            "origin": self.origin,
            "connectorName": self.connector_name,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "extension": self.extension,
            "sizeInBytes": self.size_in_bytes,
            "signedUrl": self.signed_url,
            "signedUrlRoute": self.fetch_signed_url,
            "externalRevisionId": self.external_revision_id,
            "externalRecordGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
            "isFile": self.is_file,
        }

class MessageRecord(Record):
    content: Optional[str] = None


    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

class MailRecord(Record):
    subject: Optional[str] = None
    from_email: Optional[str] = None
    to_emails: Optional[List[str]] = None
    cc_emails: Optional[List[str]] = None
    bcc_emails: Optional[List[str]] = None


    def to_arango_mail_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "subject": self.subject,
            "from": self.from_email,
            "to": self.to_emails,
            "cc": self.cc_emails,
            "bcc": self.bcc_emails,
        }


    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
        }

class WebpageRecord(Record):
    webpage_url: Optional[str] = None
    webpage_title: Optional[str] = None
    webpage_description: Optional[str] = None


    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

class RecordGroup(BaseModel):
    id: str = Field(description="Unique identifier for the record group", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the record group")
    external_group_id: Optional[str] = Field(description="External identifier for the record group")
    connector_name: Optional[str] = Field(description="Name of the connector used to create the record group")
    group_type: Optional[str] = Field(description="Type of the record group")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record group creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record group update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record group creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record group update in the source system")

    def to_arango_base_record_group(self) -> Dict:
        return {
            "_key": self.id,
            "groupName": self.name,
            "externalGroupId": self.external_group_id,
            "connectorName": self.connector_name,
            "groupType": self.group_type,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

    @staticmethod
    def from_arango_base_record_group(arango_base_record_group: Dict) -> "RecordGroup":
        return RecordGroup(
            id=arango_base_record_group["_key"],
            name=arango_base_record_group["groupName"],
            external_group_id=arango_base_record_group["externalGroupId"],
            connector_name=arango_base_record_group["connectorName"],
            group_type=arango_base_record_group["groupType"],
            created_at=arango_base_record_group["createdAtTimestamp"],
            updated_at=arango_base_record_group["updatedAtTimestamp"],
            source_created_at=arango_base_record_group["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record_group["sourceLastModifiedTimestamp"],
        )

class User(BaseModel):
    id: str = Field(description="Unique identifier for the user", default_factory=lambda: str(uuid4()))
    email: str = Field(description="Email of the user")
    name: str = Field(description="Name of the user")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the user creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the user update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the user creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the user update in the source system")

    @staticmethod
    def from_arango_base_user(arango_base_user: Dict) -> "User":
        return User(
            id=arango_base_user["_key"],
            email=arango_base_user["email"],
            name=arango_base_user["fullName"],
            created_at=arango_base_user["createdAtTimestamp"],
            updated_at=arango_base_user["updatedAtTimestamp"],
        )
