from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RecordGroupType(str, Enum):
    SLACK_CHANNEL = "SLACK_CHANNEL"
    CONFLUENCE_SPACES = "CONFLUENCE_SPACES"
    KB = "KB"
    NOTION_WORKSPACE = "NOTION_WORKSPACE"
    DRIVE = "DRIVE"
    JIRA_PROJECT = "JIRA_PROJECT"
    SHAREPOINT_SITE = "SHAREPOINT_SITE"
    SHAREPOINT_SUBSITE = "SHAREPOINT_SUBSITE"
    USER_GROUP = "USER_GROUP"
    SERVICENOWKB = "SERVICENOWKB"
    SERVICENOW_CATEGORY = "SERVICENOW_CATEGORY"

    MAILBOX = "MAILBOX"
    WEB = "WEB"

class RecordType(str, Enum):
    FILE = "FILE"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    MESSAGE = "MESSAGE"
    MAIL = "MAIL"
    TICKET = "TICKET"
    SHAREPOINT_LIST = "SHAREPOINT_LIST"
    SHAREPOINT_LIST_ITEM = "SHAREPOINT_LIST_ITEM"
    SHAREPOINT_DOCUMENT_LIBRARY = "SHAREPOINT_DOCUMENT_LIBRARY"
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
    parent_record_type: Optional[RecordType] = Field(default=None, description="Type of the parent record")
    record_group_type: Optional[RecordGroupType] = Field(default=None, description="Type of the record group")
    external_record_id: str = Field(description="Unique identifier for the record in the external system")
    external_revision_id: Optional[str] = Field(default=None, description="Unique identifier for the revision of the record in the external system")
    external_record_group_id: Optional[str] = Field(default=None, description="Unique identifier for the record group in the external system")
    parent_external_record_id: Optional[str] = Field(default=None, description="Unique identifier for the parent record in the external system")
    version: int = Field(description="Version of the record")
    origin: OriginTypes = Field(description="Origin of the record")
    connector_name: Connectors = Field(description="Name of the connector used to create the record")
    virtual_record_id: Optional[str] = Field(description="Virtual record identifier", default=None)
    summary_document_id: Optional[str] = Field(description="Summary document identifier", default=None)
    md5_hash: Optional[str] = Field(default=None, description="MD5 hash of the record")
    mime_type: str = Field(default=MimeTypes.UNKNOWN.value, description="MIME type of the record")
    inherit_permissions: bool = Field(default=True, description="Inherit permissions from parent record") # Used in backend only to determine if the record should have a inherit permissions relation from its parent record
    # Epoch Timestamps
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record update in the source system")

    # Source information
    weburl: Optional[str] = None
    signed_url: Optional[str] = None
    fetch_signed_url: Optional[str] = None
    preview_renderable: Optional[bool] = True
    is_shared: Optional[bool] = False
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
            "externalGroupId": self.external_record_group_id,
            "externalParentId": self.parent_external_record_id,
            "version": self.version,
            "origin": self.origin.value,
            "connectorName": self.connector_name.value,
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
            "previewRenderable": self.preview_renderable,
            "isShared": self.is_shared,
        }

    @staticmethod
    def from_arango_base_record(arango_base_record: Dict) -> "Record":
        # Handle connectorName which might be missing for uploaded files
        conn_name_value = arango_base_record.get("connectorName")
        try:
            connector_name = (
                Connectors(conn_name_value)
                if conn_name_value is not None
                else Connectors.KNOWLEDGE_BASE
            )
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return Record(
            id=arango_base_record["_key"],
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=RecordType(arango_base_record["recordType"]),
            record_group_type=arango_base_record.get("recordGroupType", None),
            external_revision_id=arango_base_record.get("externalRevisionId", None),
            external_record_id=arango_base_record["externalRecordId"],
            external_record_group_id=arango_base_record.get("externalGroupId", None),
            parent_external_record_id=arango_base_record.get("externalParentId", None),
            version=arango_base_record["version"],
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=connector_name,
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record.get("webUrl", None),
            created_at=arango_base_record.get("createdAtTimestamp", None),
            updated_at=arango_base_record.get("updatedAtTimestamp", None),
            source_created_at=arango_base_record.get("sourceCreatedAtTimestamp", None),
            source_updated_at=arango_base_record.get("sourceLastModifiedTimestamp", None),
            virtual_record_id=arango_base_record.get("virtualRecordId", None),
            preview_renderable=arango_base_record.get("previewRenderable", True),
            is_shared=arango_base_record.get("isShared", False),
        )

    def to_kafka_record(self) -> Dict:
        raise NotImplementedError("Implement this method in the subclass")

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

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "recordGroupId": self.external_record_group_id,
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
            record_type=RecordType(arango_base_record["recordType"]),
            external_record_id=arango_base_record["externalRecordId"],
            version=arango_base_record["version"],
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=Connectors(arango_base_record["connectorName"]),
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record["webUrl"],
            external_record_group_id=arango_base_record.get("externalGroupId", None),
            parent_external_record_id=arango_base_record.get("externalParentId", None),
            created_at=arango_base_record["createdAtTimestamp"],
            updated_at=arango_base_record["updatedAtTimestamp"],
            source_created_at=arango_base_record["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record["sourceLastModifiedTimestamp"],
            is_file=arango_base_file_record["isFile"],
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
            "origin": self.origin.value,
            "connectorName": self.connector_name.value,
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
            "externalGroupId": self.external_record_group_id,
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
    thread_id: Optional[str] = None
    is_parent: bool = False
    internet_message_id: Optional[str] = None
    conversation_index: Optional[str] = None


    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "threadId": self.thread_id or "",
            "isParent": self.is_parent,
            "subject": self.subject or "",
            "from": self.from_email or "",
            "to": self.to_emails or [],
            "cc": self.cc_emails or [],
            "bcc": self.bcc_emails or [],
            "messageIdHeader": self.internet_message_id,
            "webUrl": self.weburl or "",
            "conversationIndex": self.conversation_index,
        }


    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "mimeType": self.mime_type,
            "subject": self.subject,
        }

class WebpageRecord(Record):
    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "mimeType": self.mime_type,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "signedUrl": self.signed_url,
            "signedUrlRoute": self.fetch_signed_url,
        }

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
        }

class TicketRecord(Record):
    summary: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    reporter_email: Optional[str] = None
    assignee_email: Optional[str] = None
    reporter_name: Optional[str] = None
    creator_email: Optional[str] = None
    creator_name: Optional[str] = None

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "summary": self.summary,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "assignee": self.assignee,
            "reporterEmail": self.reporter_email,
            "assigneeEmail": self.assignee_email,
            "creatorEmail": self.creator_email,
            "creatorName": self.creator_name,
        }

    def to_kafka_record(self) -> Dict:

        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "connectorName": self.connector_name.value,
            "mimeType": self.mime_type,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "signedUrl": self.signed_url,
            "signedUrlRoute": self.fetch_signed_url,
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

class SharePointListRecord(Record):
    """Record class for SharePoint lists"""

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "version": self.version,
            "origin": self.origin.value,
            "connectorName": self.connector_name,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
        }

class SharePointListItemRecord(Record):
    """Record class for SharePoint list items"""

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "version": self.version,
            "origin": self.origin.value,
            "connectorName": self.connector_name,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
        }

class SharePointDocumentLibraryRecord(Record):
    """Record class for SharePoint document libraries"""

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "version": self.version,
            "origin": self.origin.value,
            "connectorName": self.connector_name.value,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
        }

class SharePointPageRecord(Record):
    """Record class for SharePoint pages"""

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "version": self.version,
            "origin": self.origin.value,
            "connectorName": self.connector_name.value,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
        }

class RecordGroup(BaseModel):
    id: str = Field(description="Unique identifier for the record group", default_factory=lambda: str(uuid4()))
    org_id: str = Field(description="Unique identifier for the organization", default="")
    name: str = Field(description="Name of the record group")
    short_name: Optional[str] = Field(default=None, description="Short name of the record group")
    description: Optional[str] = Field(default=None, description="Description of the record group")
    external_group_id: Optional[str] = Field(description="External identifier for the record group")
    parent_external_group_id: Optional[str] = Field(default=None, description="External identifier for the parent record group")
    parent_record_group_id: Optional[str] = Field(default=None, description="Internal identifier for the parent record group")
    connector_name: Connectors = Field(description="Name of the connector used to create the record group")
    web_url: Optional[str] = Field(default=None, description="Web URL of the record group")
    group_type: Optional[RecordGroupType] = Field(description="Type of the record group")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record group creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record group update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record group creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the record group update in the source system")
    inherit_permissions: Optional[bool] = Field(default=False, description="Permissions for the record group")

    def to_arango_base_record_group(self) -> Dict:
        doc = {
            "_key": self.id,
            "orgId": self.org_id,
            "groupName": self.name,
            "shortName": self.short_name,
            "description": self.description,
            "externalGroupId": self.external_group_id,
            "parentExternalGroupId": self.parent_external_group_id,
            "connectorName": self.connector_name.value,
            "groupType": self.group_type.value,
            "webUrl": self.web_url,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }
        return doc

    @staticmethod
    def from_arango_base_record_group(arango_base_record_group: Dict) -> "RecordGroup":
        return RecordGroup(
            id=arango_base_record_group["_key"],
            org_id=arango_base_record_group.get("orgId", ""),
            name=arango_base_record_group["groupName"],
            short_name=arango_base_record_group.get("shortName", None),
            description=arango_base_record_group.get("description", None),
            external_group_id=arango_base_record_group["externalGroupId"],
            parent_external_group_id=arango_base_record_group.get("parentExternalGroupId", None),
            connector_name=arango_base_record_group["connectorName"],
            group_type=arango_base_record_group["groupType"],
            web_url=arango_base_record_group.get("webUrl", None),
            created_at=arango_base_record_group["createdAtTimestamp"],
            updated_at=arango_base_record_group["updatedAtTimestamp"],
            source_created_at=arango_base_record_group["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record_group["sourceLastModifiedTimestamp"],
        )

class Anyone(BaseModel):
    id: str = Field(description="Unique identifier for the anyone", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class AnyoneWithLink(BaseModel):
    id: str = Field(description="Unique identifier for the anyone with link", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone with link")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class AnyoneSameOrg(BaseModel):
    id: str = Field(description="Unique identifier for the anyone same org", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone same org")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone same org creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone same org update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone same org creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone same org update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class Org(BaseModel):
    id: str = Field(description="Unique identifier for the organization", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the organization")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the organization creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the organization update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the organization creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the organization update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class Domain(BaseModel):
    id: str = Field(description="Unique identifier for the domain", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the domain")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the domain creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the domain update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the domain creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the domain update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class AnyOneWithLink(BaseModel):
    id: str = Field(description="Unique identifier for the anyone with link", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone with link")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")


class User(BaseModel):
    id: str = Field(description="Unique identifier for the user", default_factory=lambda: str(uuid4()))
    email: str
    source_user_id: Optional[str] = None
    org_id: Optional[str] = None
    user_id: Optional[str] = None
    is_active: Optional[bool] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    title: Optional[str] = None


    def to_arango_base_record(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "fullName": self.full_name,
            "isActive": self.is_active,
        }

    def validate(self) -> bool:
        return self.email is not None and self.email != ""

    def key(self) -> str:
        return self.email

    @staticmethod
    def from_arango_user(data: Dict[str, Any]) -> 'User':
        return User(
            id=data.get("_key", None),
            email=data.get("email", ""),
            org_id=data.get("orgId", ""),
            user_id=data.get("userId", None),
            is_active=data.get("isActive", False),
            first_name=data.get("firstName", None),
            middle_name=data.get("middleName", None),
            last_name=data.get("lastName", None),
            full_name=data.get("fullName", None),
            title=data.get("title", None),
        )


class UserGroup(BaseModel):
    source_user_group_id: str
    name: str
    mail: Optional[str] = None
    _key: Optional[str] = None
    description: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    last_sync_timestamp: Optional[float] = None
    source_created_at_timestamp: Optional[float] = None
    source_last_modified_timestamp: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at_timestamp": self.created_at_timestamp,
            "updated_at_timestamp": self.updated_at_timestamp,
            "last_sync_timestamp": self.last_sync_timestamp,
            "source_created_at_timestamp": self.source_created_at_timestamp,
            "source_last_modified_timestamp": self.source_last_modified_timestamp
        }

    def validate(self) -> bool:
        return True

    def key(self) -> str:
        return self._key


class AppUser(BaseModel):
    app_name: Connectors = Field(description="Name of the app")
    id: str = Field(description="Unique identifier for the user", default_factory=lambda: str(uuid4()))
    source_user_id: str = Field(description="Unique identifier for the user in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")
    email: str = Field(description="Email of the user")
    full_name: str = Field(description="Name of the user")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the user creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the user update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the user creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the user update in the source system")
    is_active: bool = Field(default=False, description="Whether the user is active")
    title: Optional[str] = Field(default=None, description="Title of the user")

    def to_arango_base_user(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "email": self.email,
            "fullName": self.full_name,
            "userId": self.source_user_id,
            "isActive": self.is_active,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
        }

    @staticmethod
    def from_arango_user(data: Dict[str, Any]) -> 'AppUser':
        return AppUser(
            id=data.get("_key", None),
            email=data.get("email", ""),
            org_id=data.get("orgId", ""),
            user_id=data.get("userId", None),
            is_active=data.get("isActive", False),
            full_name=data.get("fullName", None),
            source_user_id=data.get("sourceUserId", ""),
            app_name=Connectors(data.get("appName", Connectors.UNKNOWN.value)),
        )

class AppUserGroup(BaseModel):
    id: str = Field(description="Unique identifier for the user group", default_factory=lambda: str(uuid4()))
    app_name: Connectors = Field(description="Name of the app")
    source_user_group_id: str = Field(description="Unique identifier for the user group in the source system")
    name: str = Field(description="Name of the user group")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the user group creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the user group update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the user group creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the user group update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")
    description: Optional[str] = Field(default=None, description="Description of the user group")

    def to_arango_base_user_group(self) -> Dict[str, Any]:
        """
        Converts the AppUserGroup model to a dictionary that matches the ArangoDB schema.
        """
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.name,
            "appName": self.app_name.value,
            "externalGroupId": self.source_user_group_id,
            "connectorName": self.app_name.value,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,

        }

    @staticmethod
    def from_arango_base_user_group(arango_doc: Dict[str, Any]) -> "AppUserGroup":
        return AppUserGroup(
            id=arango_doc["_key"],
            org_id=arango_doc.get("orgId", ""),
            name=arango_doc["name"],
            source_user_group_id=arango_doc["externalGroupId"],
            app_name=Connectors(arango_doc["connectorName"]),
            created_at=arango_doc["createdAtTimestamp"],
            updated_at=arango_doc["updatedAtTimestamp"],
            source_created_at=arango_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_doc.get("sourceLastModifiedTimestamp"),
        )

class AppRole(BaseModel):
    id: str = Field(description="Unique identifier for the role", default_factory=lambda: str(uuid4()))
    app_name: Connectors = Field(description="Name of the app")
    source_role_id: str = Field(description="Unique identifier for the role in the source system")
    name: str = Field(description="Name of the role")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the role creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the role update")
    source_created_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the role creation in the source system")
    source_updated_at: Optional[int] = Field(default=None, description="Epoch timestamp in milliseconds of the role update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

    def to_arango_base_role(self) -> Dict[str, Any]:
        """
        Converts the AppRole model to a dictionary that matches the ArangoDB schema.
        """
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.name,
            "externalRoleId": self.source_role_id,
            "connectorName": self.app_name.value,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,

        }

    @staticmethod
    def from_arango_base_role(arango_doc: Dict[str, Any]) -> "AppRole":
        return AppRole(
            id=arango_doc["_key"],
            org_id=arango_doc.get("orgId", ""),
            name=arango_doc["name"],
            source_role_id=arango_doc["externalRoleId"],
            app_name=Connectors(arango_doc["connectorName"]),
            created_at=arango_doc["createdAtTimestamp"],
            updated_at=arango_doc["updatedAtTimestamp"],
            source_created_at=arango_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_doc.get("sourceLastModifiedTimestamp"),
        )
