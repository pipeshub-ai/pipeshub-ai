from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar, Union
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.models.blocks import (
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    SemanticMetadata,
    TableRowMetadata,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Type variable for enum classes (must be after Enum import)
EnumType = TypeVar('EnumType', bound=Enum)


class RecordGroupType(str, Enum):
    SLACK_CHANNEL = "SLACK_CHANNEL"
    CONFLUENCE_SPACES = "CONFLUENCE_SPACES"
    KB = "KB"
    NOTION_WORKSPACE = "NOTION_WORKSPACE"
    DRIVE = "DRIVE"
    PROJECT = "PROJECT"
    SHAREPOINT_SITE = "SHAREPOINT_SITE"
    SHAREPOINT_SUBSITE = "SHAREPOINT_SUBSITE"
    USER_GROUP = "USER_GROUP"
    SERVICENOWKB = "SERVICENOWKB"
    SERVICENOW_CATEGORY = "SERVICENOW_CATEGORY"
    BUCKET = "BUCKET"
    FILE_SHARE = "FILE_SHARE"
    MAILBOX = "MAILBOX"
    GROUP_MAILBOX = "GROUP_MAILBOX"
    WEB = "WEB"

class RecordType(str, Enum):
    FILE = "FILE"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    MESSAGE = "MESSAGE"
    MAIL = "MAIL"
    GROUP_MAIL = "GROUP_MAIL"
    TICKET = "TICKET"
    COMMENT = "COMMENT"
    INLINE_COMMENT = "INLINE_COMMENT"
    CONFLUENCE_PAGE = "CONFLUENCE_PAGE"
    CONFLUENCE_BLOGPOST = "CONFLUENCE_BLOGPOST"
    SHAREPOINT_PAGE = "SHAREPOINT_PAGE"
    SHAREPOINT_LIST = "SHAREPOINT_LIST"
    SHAREPOINT_LIST_ITEM = "SHAREPOINT_LIST_ITEM"
    SHAREPOINT_DOCUMENT_LIBRARY = "SHAREPOINT_DOCUMENT_LIBRARY"
    LINK = "LINK"
    PROJECT = "PROJECT"
    OTHERS = "OTHERS"


class LinkPublicStatus(str, Enum):
    """Status of link accessibility"""
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

class IndexingStatus(str, Enum):
    """Status of record indexing for search and AI features"""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"  # Record saved but not indexed (filtered out)
    PAUSED = "PAUSED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    EMPTY = "EMPTY"
    ENABLE_MULTIMODAL_MODELS = "ENABLE_MULTIMODAL_MODELS"
    QUEUED = "QUEUED"
    CONNECTOR_DISABLED = "CONNECTOR_DISABLED"


class TicketPriority(str, Enum):
    """Standard ticket priority values for all ticketing connectors"""
    LOWEST = "LOWEST"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    HIGHEST = "HIGHEST"
    CRITICAL = "CRITICAL"
    BLOCKER = "BLOCKER"
    UNKNOWN = "UNKNOWN"  # For unmapped or missing priority values


class TicketStatus(str, Enum):
    """Standard ticket status values for all ticketing connectors"""
    NEW = "NEW"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REOPENED = "REOPENED"
    PENDING = "PENDING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    UNKNOWN = "UNKNOWN"  # For unmapped or missing status values


class TicketType(str, Enum):
    """Standard ticket type values for all ticketing connectors"""
    TASK = "TASK"  # General task
    BUG = "BUG"  # Bug/defect
    STORY = "STORY"  # User story
    EPIC = "EPIC"  # Epic/large feature
    FEATURE = "FEATURE"  # Feature request
    SUBTASK = "SUBTASK"  # Subtask
    INCIDENT = "INCIDENT"  # Incident/outage
    IMPROVEMENT = "IMPROVEMENT"  # Improvement/enhancement
    QUESTION = "QUESTION"  # Question/inquiry
    DOCUMENTATION = "DOCUMENTATION"  # Documentation task
    TEST = "TEST"  # Test case
    UNKNOWN = "UNKNOWN"  # Unknown or unmapped ticket type


class TicketDeliveryStatus(str, Enum):
    """Standard ticket delivery status values for all ticketing connectors"""
    ON_TRACK = "ON_TRACK"  # On track - progressing as expected
    AT_RISK = "AT_RISK"  # At risk - some concerns but manageable
    OFF_TRACK = "OFF_TRACK"  # Off track - significant issues or delays
    HIGH_RISK = "HIGH_RISK"  # High risk - major concerns (Jira Align)
    SOME_RISK = "SOME_RISK"  # Some risk - minor concerns (Jira Align)
    UNKNOWN = "UNKNOWN"  # Unknown or unmapped delivery status


class RelatedExternalRecord(BaseModel):
    """Structured model for related external records to create LINKED_TO relations.

    This model ensures type safety and validation for related external records.
    Only external_record_id and record_type are required; relation_type defaults to LINKED_TO.
    custom_relationship_tag is optional and used to specify the relationship type from the source system
    (e.g., "is blocked by", "blocks", "clones" for Jira).
    """
    external_record_id: str = Field(description="External ID of the related record")
    record_type: RecordType = Field(description="Type of the related record")
    relation_type: RecordRelations = Field(
        default=RecordRelations.LINKED_TO,
        description="Type of relation to create (defaults to LINKED_TO)"
    )
    custom_relationship_tag: Optional[str] = Field(
        default=None,
        description="Custom relationship tag from source system (e.g., 'is blocked by', 'blocks' for Jira)"
    )


class Record(BaseModel):
    # Core record properties
    id: str = Field(description="Unique identifier for the record", default_factory=lambda: str(uuid4()))
    org_id: str = Field(description="Unique identifier for the organization", default="")
    record_name: str = Field(description="Human-readable name for the record")
    record_type: RecordType = Field(description="Type/category of the record")
    record_status: ProgressStatus = Field(default=ProgressStatus.NOT_STARTED)
    parent_record_type: Optional[RecordType] = Field(default=None, description="Type of the parent record")
    record_group_type: Optional[RecordGroupType] = Field(default=None, description="Type of the record group")
    external_record_id: str = Field(description="Unique identifier for the record in the external system")
    external_revision_id: Optional[str] = Field(default=None, description="Unique identifier for the revision of the record in the external system")
    external_record_group_id: Optional[str] = Field(default=None, description="Unique identifier for the record group in the external system")
    record_group_id: Optional[str] = Field(default=None, description="Internal identifier for the record group (UUID)")
    parent_external_record_id: Optional[str] = Field(default=None, description="Unique identifier for the parent record in the external system")
    version: int = Field(description="Version of the record")
    origin: OriginTypes = Field(description="Origin of the record")
    connector_name: Connectors = Field(description="Name of the connector used to create the record")
    connector_id: str = Field(description="Unique identifier for the connector configuration instance")
    virtual_record_id: Optional[str] = Field(description="Virtual record identifier", default=None)
    summary_document_id: Optional[str] = Field(description="Summary document identifier", default=None)
    md5_hash: Optional[str] = Field(default=None, description="MD5 hash of the record content")
    size_in_bytes: Optional[int] = Field(default=None, description="Size of the record content in bytes")
    mime_type: str = Field(default=MimeTypes.UNKNOWN.value, description="MIME type of the record")
    inherit_permissions: bool = Field(default=True, description="Inherit permissions from parent record") # Used in backend only to determine if the record should have a inherit permissions relation from its parent record
    indexing_status: str = Field(default=IndexingStatus.QUEUED.value, description="Indexing status for the record")
    extraction_status: str = Field(default=IndexingStatus.NOT_STARTED.value, description="Extraction status for the record")
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
    is_shared_with_me: Optional[bool] = False
    hide_weburl: bool = Field(default=False, description="Flag indicating if web URL should be hidden")
    is_internal: bool = Field(default=False, description="Flag indicating if record is internal")

    # Processing flags
    is_vlm_ocr_processed: Optional[bool] = Field(default=False, description="Flag indicating if VLM OCR processing has been used to process the record")

    # Content blocks
    block_containers: BlocksContainer = Field(default_factory=BlocksContainer, description="List of block containers in this record")
    semantic_metadata: Optional[SemanticMetadata] = None
    # Relationships
    parent_record_id: Optional[str] = None
    child_record_ids: Optional[List[str]] = Field(default_factory=list)
    related_record_ids: Optional[List[str]] = Field(default_factory=list)

    # Related external records (for connectors to specify relations by external IDs)
    related_external_records: Optional[List[RelatedExternalRecord]] = Field(default_factory=list, description="List of related external records to create LINKED_TO relations (not persisted)")
    # Hierarchy fields
    is_dependent_node: bool = Field(default=False, description="True for dependent records, False for root records")
    parent_node_id: Optional[str] = Field(default=None, description="Internal record ID of the parent node")
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
            "recordGroupId": self.record_group_id,
            "version": self.version,
            "origin": self.origin.value,
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
            "mimeType": self.mime_type,
            "webUrl": self.weburl,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "indexingStatus": self.indexing_status,
            "extractionStatus": self.extraction_status,
            "isDeleted": False,
            "isArchived": False,
            "deletedByUserId": None,
            "previewRenderable": self.preview_renderable,
            "isShared": self.is_shared,
            "isVLMOcrProcessed": self.is_vlm_ocr_processed,
            "md5Checksum": self.md5_hash,
            "sizeInBytes": self.size_in_bytes,
            "isDependentNode": self.is_dependent_node,
            "parentNodeId": self.parent_node_id,
            "hideWeburl": self.hide_weburl,
            "isInternal": self.is_internal,
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
            id=arango_base_record.get("id", arango_base_record.get("_key")),
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=RecordType(arango_base_record["recordType"]),
            record_group_type=arango_base_record.get("recordGroupType", None),
            external_revision_id=arango_base_record.get("externalRevisionId", None),
            external_record_id=arango_base_record["externalRecordId"],
            external_record_group_id=arango_base_record.get("externalGroupId", None),
            record_group_id=arango_base_record.get("recordGroupId", None),
            parent_external_record_id=arango_base_record.get("externalParentId", None),
            version=arango_base_record["version"],
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=connector_name,
            connector_id=arango_base_record.get("connectorId", None),
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record.get("webUrl", None),
            created_at=arango_base_record.get("createdAtTimestamp", None),
            updated_at=arango_base_record.get("updatedAtTimestamp", None),
            source_created_at=arango_base_record.get("sourceCreatedAtTimestamp", None),
            source_updated_at=arango_base_record.get("sourceLastModifiedTimestamp", None),
            virtual_record_id=arango_base_record.get("virtualRecordId", None),
            indexing_status=arango_base_record.get("indexingStatus", IndexingStatus.QUEUED.value),
            extraction_status=arango_base_record.get("extractionStatus", IndexingStatus.NOT_STARTED.value),
            preview_renderable=arango_base_record.get("previewRenderable", True),
            is_shared=arango_base_record.get("isShared", False),
            is_vlm_ocr_processed=arango_base_record.get("isVLMOcrProcessed", False),
            is_dependent_node=arango_base_record.get("isDependentNode", False),
            parent_node_id=arango_base_record.get("parentNodeId", None),
            hide_weburl=arango_base_record.get("hideWeburl", False),
            is_internal=arango_base_record.get("isInternal", False),
            md5_hash=arango_base_record.get("md5Checksum", None),
            size_in_bytes=arango_base_record.get("sizeInBytes", None),
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
            "name": self.record_name,
            "isFile": self.is_file,
            "extension": self.extension,
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
    def from_arango_record(arango_base_file_record: Dict, arango_base_record: Dict) -> "FileRecord":
        return FileRecord(
            id=arango_base_record.get("id", arango_base_record.get("_key")),
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=RecordType(arango_base_record["recordType"]),
            external_revision_id=arango_base_record.get("externalRevisionId", None),
            external_record_id=arango_base_record["externalRecordId"],
            version=arango_base_record["version"],
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=Connectors(arango_base_record["connectorName"]),
            connector_id=arango_base_record.get("connectorId"),
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record["webUrl"],
            external_record_group_id=arango_base_record.get("externalGroupId", None),
            record_group_id=arango_base_record.get("recordGroupId", None),
            parent_external_record_id=arango_base_record.get("externalParentId", None),
            created_at=arango_base_record["createdAtTimestamp"],
            updated_at=arango_base_record["updatedAtTimestamp"],
            source_created_at=arango_base_record["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record["sourceLastModifiedTimestamp"],
            is_dependent_node=arango_base_record.get("isDependentNode", False),
            parent_node_id=arango_base_record.get("parentNodeId", None),
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
            "connectorId": self.connector_id,
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
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
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
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "mimeType": self.mime_type,
            "subject": self.subject,
        }

    @staticmethod
    def from_arango_record(mail_doc: Dict, record_doc: Dict) -> "MailRecord":
        """Create MailRecord from ArangoDB documents (records + mails collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return MailRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            record_group_id=record_doc.get("recordGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            version=record_doc["version"],
            origin=OriginTypes(record_doc["origin"]),
            connector_name=connector_name,
            connector_id=record_doc.get("connectorId"),
            mime_type=record_doc.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=mail_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            subject=mail_doc.get("subject"),
            from_email=mail_doc.get("from"),
            to_emails=mail_doc.get("to", []),
            cc_emails=mail_doc.get("cc", []),
            bcc_emails=mail_doc.get("bcc", []),
            thread_id=mail_doc.get("threadId"),
            is_parent=mail_doc.get("isParent", False),
            internet_message_id=mail_doc.get("messageIdHeader"),
            conversation_index=mail_doc.get("conversationIndex"),
        )

class WebpageRecord(Record):
    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
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

    @staticmethod
    def from_arango_record(webpage_doc: Dict, record_doc: Dict) -> "WebpageRecord":
        """Create WebpageRecord from ArangoDB documents (records + webpages collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return WebpageRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            record_group_id=record_doc.get("recordGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            version=record_doc["version"],
            origin=OriginTypes(record_doc["origin"]),
            connector_name=connector_name,
            connector_id=record_doc.get("connectorId"),
            mime_type=record_doc.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
        )

class LinkRecord(Record):
    """
    Link record for URLs and attachments.

    Fields:
    - url: The link URL (required)
    - title: Link title (optional)
    - is_public: Whether the link is publicly accessible (no auth required)
    - linked_record_id: Internal record ID of a record that has the same weburl (optional)
    """
    url: str
    title: Optional[str] = None
    is_public: LinkPublicStatus = Field(description="Link public accessibility status")
    linked_record_id: Optional[str] = Field(default=None, description="Internal record ID of linked record with same weburl")

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
            "mimeType": self.mime_type,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "signedUrl": self.signed_url,
            "signedUrlRoute": self.fetch_signed_url,
            "webUrl": self.weburl,
        }

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "url": self.url,
            "title": self.title,
            "isPublic": self.is_public.value,
            "linkedRecordId": self.linked_record_id,
        }

    @staticmethod
    def from_arango_record(link_doc: Dict, record_doc: Dict) -> "LinkRecord":
        """Create LinkRecord from ArangoDB documents (records + links collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return LinkRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            version=record_doc["version"],
            origin=OriginTypes(record_doc["origin"]),
            connector_name=connector_name,
            connector_id=record_doc.get("connectorId"),
            mime_type=record_doc.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            url=link_doc["url"],
            title=link_doc.get("title"),
            is_public=LinkPublicStatus(link_doc.get("isPublic", "unknown")),
            linked_record_id=link_doc.get("linkedRecordId"),
        )

class CommentRecord(Record):
    """
    Comment record for page comments (footer and inline).

    Fields:
    - author_source_id: User accountId who created the comment
    - resolution_status: Status of the comment (e.g., "resolved", "open", None)
    - comment_selection: For inline comments, the original text selection (HTML)
    """
    author_source_id: str
    resolution_status: Optional[str] = None
    comment_selection: Optional[str] = None

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
            "mimeType": self.mime_type,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "authorSourceId": self.author_source_id,
            "resolutionStatus": self.resolution_status,
            "commentSelection": self.comment_selection,
        }

    @staticmethod
    def from_arango_record(comment_doc: Dict, record_doc: Dict) -> "CommentRecord":
        """Create CommentRecord from ArangoDB documents (records + comments collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return CommentRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            record_group_id=record_doc.get("recordGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            version=record_doc["version"],
            origin=OriginTypes(record_doc["origin"]),
            connector_name=connector_name,
            connector_id=record_doc.get("connectorId"),
            mime_type=record_doc.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            preview_renderable=record_doc.get("previewRenderable", True),
            is_dependent_node=record_doc.get("isDependentNode", False),
            parent_node_id=record_doc.get("parentNodeId", None),
            author_source_id=comment_doc.get("authorSourceId") or comment_doc.get("authorId") or "unknown",
            resolution_status=comment_doc.get("resolutionStatus"),
            comment_selection=comment_doc.get("commentSelection"),
        )

class TicketRecord(Record):
    status: Optional[Union[TicketStatus, str]] = None
    priority: Optional[Union[TicketPriority, str]] = None
    type: Optional[Union[TicketType, str]] = None
    delivery_status: Optional[Union[TicketDeliveryStatus, str]] = None
    assignee: Optional[str] = None
    reporter_email: Optional[str] = None
    assignee_email: Optional[str] = None
    reporter_name: Optional[str] = None
    creator_email: Optional[str] = None
    creator_name: Optional[str] = None

    def to_arango_record(self) -> Dict:
        def _get_value(field_value: Optional[Union[Enum, str]]) -> Optional[str]:
            """Extract string value from enum or return original string"""
            if field_value is None:
                return None
            if isinstance(field_value, Enum):
                return field_value.value
            return str(field_value)

        return {
            "_key": self.id,
            "orgId": self.org_id,
            "status": _get_value(self.status),
            "priority": _get_value(self.priority),
            "type": _get_value(self.type),
            "deliveryStatus": _get_value(self.delivery_status),
            "assignee": self.assignee,
            "reporterEmail": self.reporter_email,
            "reporterName": self.reporter_name,
            "assigneeEmail": self.assignee_email,
            "creatorEmail": self.creator_email,
            "creatorName": self.creator_name,
        }

    @staticmethod
    def _safe_enum_parse(value: Optional[str], enum_class: Type[EnumType]) -> Optional[Union[EnumType, str]]:
        """Safely parse enum value, returning original string if invalid (preserves connector-specific values)"""
        if not value:
            return None
        try:
            return enum_class(value)
        except (ValueError, KeyError):
            # If value doesn't match enum, try to find by value (case-insensitive)
            value_upper = value.upper()
            for enum_item in enum_class:
                if enum_item.value.upper() == value_upper:
                    return enum_item
            # If still no match, return original value instead of UNKNOWN to preserve connector-specific values
            return value

    @staticmethod
    def from_arango_record(ticket_doc: Dict, record_doc: Dict) -> "TicketRecord":
        """Create TicketRecord from ArangoDB documents (records + tickets collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return TicketRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            record_group_id=record_doc.get("recordGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            version=record_doc["version"],
            origin=OriginTypes(record_doc["origin"]),
            connector_name=connector_name,
            connector_id=record_doc.get("connectorId"),
            mime_type=record_doc.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            preview_renderable=record_doc.get("previewRenderable", True),
            is_dependent_node=record_doc.get("isDependentNode", False),
            parent_node_id=record_doc.get("parentNodeId", None),
            status=TicketRecord._safe_enum_parse(ticket_doc.get("status"), TicketStatus),
            priority=TicketRecord._safe_enum_parse(ticket_doc.get("priority"), TicketPriority),
            type=TicketRecord._safe_enum_parse(ticket_doc.get("type"), TicketType),
            delivery_status=TicketRecord._safe_enum_parse(ticket_doc.get("deliveryStatus"), TicketDeliveryStatus),
            assignee=ticket_doc.get("assignee"),
            reporter_email=ticket_doc.get("reporterEmail"),
            assignee_email=ticket_doc.get("assigneeEmail"),
            reporter_name=ticket_doc.get("reporterName"),
            creator_email=ticket_doc.get("creatorEmail"),
            creator_name=ticket_doc.get("creatorName"),
        )

    def to_kafka_record(self) -> Dict:

        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
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

class ProjectRecord(Record):
    """Record class for projects"""
    status: Optional[str] = None
    priority: Optional[str] = None
    lead_id: Optional[str] = None
    lead_name: Optional[str] = None
    lead_email: Optional[str] = None

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "status": self.status,
            "priority": self.priority,
            "leadId": self.lead_id,
            "leadName": self.lead_name,
            "leadEmail": self.lead_email,
        }

    @staticmethod
    def from_arango_record(project_doc: Dict, record_doc: Dict) -> "ProjectRecord":
        """Create ProjectRecord from ArangoDB documents (records + projects collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return ProjectRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            version=record_doc["version"],
            origin=OriginTypes(record_doc["origin"]),
            connector_name=connector_name,
            connector_id=record_doc.get("connectorId"),
            mime_type=record_doc.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            preview_renderable=record_doc.get("previewRenderable", True),
            is_dependent_node=record_doc.get("isDependentNode", False),
            parent_node_id=record_doc.get("parentNodeId", None),
            status=project_doc.get("status"),
            priority=project_doc.get("priority"),
            lead_id=project_doc.get("leadId"),
            lead_name=project_doc.get("leadName"),
            lead_email=project_doc.get("leadEmail"),
        )

    def to_kafka_record(self) -> Dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "connectorName": self.connector_name.value,
            "connectorId": self.connector_id,
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
            "connectorId": self.connector_id,
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
            "connectorId": self.connector_id,
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
            "connectorId": self.connector_id,
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

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
        }

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
            "connectorId": self.connector_id,
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
    connector_id: str = Field(description="Unique identifier for the connector configuration instance")
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
            "connectorId": self.connector_id,
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
            id=arango_base_record_group.get("id", arango_base_record_group.get("_key")),
            org_id=arango_base_record_group.get("orgId", ""),
            name=arango_base_record_group["groupName"],
            short_name=arango_base_record_group.get("shortName", None),
            description=arango_base_record_group.get("description", None),
            external_group_id=arango_base_record_group["externalGroupId"],
            parent_external_group_id=arango_base_record_group.get("parentExternalGroupId", None),
            connector_name=arango_base_record_group["connectorName"],
            connector_id=arango_base_record_group.get("connectorId"),
            group_type=arango_base_record_group["groupType"],
            web_url=arango_base_record_group.get("webUrl", None),
            created_at=arango_base_record_group["createdAtTimestamp"],
            updated_at=arango_base_record_group["updatedAtTimestamp"],
            source_created_at=arango_base_record_group.get("sourceCreatedAtTimestamp", None),
            source_updated_at=arango_base_record_group.get("sourceLastModifiedTimestamp", None),
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
            id=data.get("id", data.get("_key")),
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
    id: Optional[str] = None
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
        return self.id


class Person(BaseModel):
    """Lightweight entity for external email addresses (not organization members)."""
    id: str = Field(description="Unique identifier", default_factory=lambda: str(uuid4()))
    email: str = Field(description="Email address")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Creation timestamp")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Update timestamp")

    def to_arango_person(self) -> Dict[str, Any]:
        return {
            "_key": self.id,
            "email": self.email,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
        }

    @staticmethod
    def from_arango_person(data: Dict[str, Any]) -> 'Person':
        return Person(
            id=data.get("_key"),
            email=data.get("email"),
            created_at=data.get("createdAtTimestamp", get_epoch_timestamp_in_ms()),
            updated_at=data.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()),
        )


class AppUser(BaseModel):
    app_name: Connectors = Field(description="Name of the app")
    connector_id: str = Field(description="Unique identifier for the connector")
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
            id=data.get("id", data.get("_key")),
            email=data.get("email", ""),
            org_id=data.get("orgId", ""),
            user_id=data.get("userId", None),
            is_active=data.get("isActive", False),
            full_name=data.get("fullName", None),
            source_user_id=data.get("sourceUserId", ""),
            app_name=Connectors(data.get("appName", Connectors.UNKNOWN.value).replace("_", " ").upper()),
            connector_id=data.get("connectorId", ""),
        )

class AppUserGroup(BaseModel):
    id: str = Field(description="Unique identifier for the user group", default_factory=lambda: str(uuid4()))
    app_name: Connectors = Field(description="Name of the app")
    connector_id: str = Field(description="Unique identifier for the connector")
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
            "connectorId": self.connector_id,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,

        }

    @staticmethod
    def from_arango_base_user_group(arango_doc: Dict[str, Any]) -> "AppUserGroup":
        return AppUserGroup(
            id=arango_doc.get("id", arango_doc.get("_key")),
            org_id=arango_doc.get("orgId", ""),
            name=arango_doc["name"],
            source_user_group_id=arango_doc["externalGroupId"],
            app_name=Connectors(arango_doc["connectorName"]),
            connector_id=arango_doc.get("connectorId", None),
            created_at=arango_doc["createdAtTimestamp"],
            updated_at=arango_doc["updatedAtTimestamp"],
            source_created_at=arango_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_doc.get("sourceLastModifiedTimestamp"),
        )

class AppRole(BaseModel):
    id: str = Field(description="Unique identifier for the role", default_factory=lambda: str(uuid4()))
    app_name: Connectors = Field(description="Name of the app")
    connector_id: str = Field(description="Unique identifier for the connector")
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
            "connectorId": self.connector_id,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,

        }

    @staticmethod
    def from_arango_base_role(arango_doc: Dict[str, Any]) -> "AppRole":
        return AppRole(
            id=arango_doc.get("id", arango_doc.get("_key")),
            org_id=arango_doc.get("orgId", ""),
            name=arango_doc["name"],
            source_role_id=arango_doc["externalRoleId"],
            app_name=Connectors(arango_doc["connectorName"]),
            connector_id=arango_doc.get("connectorId", None),
            created_at=arango_doc["createdAtTimestamp"],
            updated_at=arango_doc["updatedAtTimestamp"],
            source_created_at=arango_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_doc.get("sourceLastModifiedTimestamp"),
        )

# Rebuild models to resolve forward references after all imports are complete
# This is necessary due to circular imports between entities.py and blocks.py
Record.model_rebuild()
FileRecord.model_rebuild()
MessageRecord.model_rebuild()
MailRecord.model_rebuild()
WebpageRecord.model_rebuild()
CommentRecord.model_rebuild()
TicketRecord.model_rebuild()
LinkRecord.model_rebuild()
ProjectRecord.model_rebuild()
SharePointListRecord.model_rebuild()
SharePointListItemRecord.model_rebuild()
SharePointDocumentLibraryRecord.model_rebuild()
SharePointPageRecord.model_rebuild()

# Rebuild blocks models that have forward references to RecordType
# ChildRecord has a forward reference to RecordType, and other models depend on it
# These models need to be rebuilt after RecordType is defined
ChildRecord.model_rebuild()
TableRowMetadata.model_rebuild()
BlockGroup.model_rebuild()
BlocksContainer.model_rebuild()
