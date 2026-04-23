import builtins
import os
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional,Dict, List, Literal, TypeVar
from uuid import uuid4
from jinja2 import Template
from app.modules.qna.prompt_templates import (
    agent_block_group_prompt,
)
from pydantic import BaseModel, Field
from app.models.blocks import BlockType, GroupType
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.models._model_rebuild import rebuild_all_models
from app.models.blocks import (
    BlocksContainer,
    SemanticMetadata,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Type variable for enum classes (must be after Enum import)
EnumType = TypeVar('EnumType', bound=Enum)


class LlmTextContent(BaseModel):
    """A single LLM message-content item produced by ``to_llm_full_context``."""

    type: Literal["text"]
    text: str


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
    REPOSITORY = "REPOSITORY"
    MAILBOX = "MAILBOX"
    GROUP_MAILBOX = "GROUP_MAILBOX"
    WEB = "WEB"
    SHELF = "SHELF"
    BOOK = "BOOK"
    CHAPTER = "CHAPTER"
    RSS_FEED = "RSS_FEED"
    SALESFORCE_FILE = "SALESFORCE_FILE"
    PRODUCT = "PRODUCT"
    DEAL = "DEAL"
    CASE = "CASE"
    TASK = "TASK"
    SALESFORCE_ORG = "SALESFORCE_ORG"
    SQL_DATABASE = "SQL_DATABASE"
    SQL_NAMESPACE = "SQL_NAMESPACE"
    STAGE = "STAGE"

class RecordType(str, Enum):
    FILE = "FILE"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    DATABASE = "DATABASE"
    DATASOURCE = "DATASOURCE"
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
    PULL_REQUEST = "PULL_REQUEST"
    MEETING = "MEETING"
    PRODUCT = "PRODUCT"
    DEAL = "DEAL"
    CASE = "CASE"
    TASK = "TASK"
    ARTIFACT = "ARTIFACT"
    CODE_FILE = "CODE_FILE"
    SQL_TABLE = "SQL_TABLE"
    SQL_VIEW = "SQL_VIEW"
    OTHERS = "OTHERS"


class LinkPublicStatus(str, Enum):
    """Status of link accessibility"""
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class Priority(str, Enum):
    """Standard priority values for all connectors"""
    LOWEST = "LOWEST"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    HIGHEST = "HIGHEST"
    CRITICAL = "CRITICAL"
    BLOCKER = "BLOCKER"
    UNKNOWN = "UNKNOWN"  # For unmapped or missing priority values


class Status(str, Enum):
    """Standard status values for all connectors"""
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
    QA = "QA"
    UNKNOWN = "UNKNOWN"  # For unmapped or missing status values


class ItemType(str, Enum):
    """Standard item type values for all connectors"""
    TASK = "TASK"
    BUG = "BUG"
    STORY = "STORY"
    EPIC = "EPIC"
    FEATURE = "FEATURE"
    SUBTASK = "SUBTASK"
    INCIDENT = "INCIDENT"
    IMPROVEMENT = "IMPROVEMENT"
    QUESTION = "QUESTION"
    DOCUMENTATION = "DOCUMENTATION"
    TEST = "TEST"
    ISSUE = "ISSUE"
    SUB_ISSUE = "SUB_ISSUE"
    UNKNOWN = "UNKNOWN"


class DeliveryStatus(str, Enum):
    """Standard delivery status values for all connectors"""
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    OFF_TRACK = "OFF_TRACK"
    HIGH_RISK = "HIGH_RISK"
    SOME_RISK = "SOME_RISK"
    UNKNOWN = "UNKNOWN"


class RelatedExternalRecord(BaseModel):
    """Structured model for related external records to create record relations.

    This model ensures type safety and validation for related external records.
    Only external_record_id and record_type are required; relation_type defaults to LINKED_TO.
    Optional source_column, target_column, constraint_name are used for SQL FK edges.
    """
    external_record_id: str = Field(description="External ID of the related record")
    record_type: RecordType = Field(description="Type of the related record")
    record_name: Optional[str] = Field(default=None, description="Human-readable name for the related record placeholder (e.g., table name for SQL FK targets). Defaults to external_record_id if not provided.")
    relation_type: RecordRelations = Field(
        default=RecordRelations.LINKED_TO,
        description="Type of relation to create (e.g., BLOCKS, CLONES, etc.)"
    )
    source_column: Optional[str] = Field(default=None, description="Source column name (e.g. for SQL foreign keys)")
    target_column: Optional[str] = Field(default=None, description="Target column name (e.g. for SQL foreign keys)")
    child_table_name: Optional[str] = Field(default=None, description="Child table name (e.g. for SQL foreign keys)")
    parent_table_name: Optional[str] = Field(default=None, description="Parent table name (e.g. for SQL foreign keys)")
    constraint_name: Optional[str] = Field(default=None, description="Constraint name (e.g. FK constraint name)")


class Record(BaseModel):
    # Core record properties
    id: str = Field(description="Unique identifier for the record", default_factory=lambda: str(uuid4()))
    org_id: str = Field(description="Unique identifier for the organization", default="")
    record_name: str = Field(description="Human-readable name for the record")
    record_type: RecordType = Field(description="Type/category of the record")
    record_status: ProgressStatus = Field(default=ProgressStatus.NOT_STARTED)
    parent_record_type: RecordType | None = Field(default=None, description="Type of the parent record")
    record_group_type: RecordGroupType | None = Field(default=None, description="Type of the record group")
    external_record_id: str = Field(description="Unique identifier for the record in the external system")
    external_revision_id: str | None = Field(default=None, description="Unique identifier for the revision of the record in the external system")
    external_record_group_id: str | None = Field(default=None, description="Unique identifier for the record group in the external system")
    record_group_id: str | None = Field(default=None, description="Internal identifier for the record group (UUID)")
    parent_external_record_id: str | None = Field(default=None, description="Unique identifier for the parent record in the external system")
    version: int = Field(description="Version of the record")
    origin: OriginTypes = Field(description="Origin of the record")
    connector_name: Connectors = Field(description="Name of the connector used to create the record")
    connector_id: str = Field(description="Unique identifier for the connector configuration instance")
    virtual_record_id: str | None = Field(description="Virtual record identifier", default=None)
    summary_document_id: str | None = Field(description="Summary document identifier", default=None)
    md5_hash: str | None = Field(default=None, description="MD5 hash of the record content")
    size_in_bytes: int | None = Field(default=None, description="Size of the record content in bytes")
    mime_type: str = Field(default=MimeTypes.UNKNOWN.value, description="MIME type of the record")
    inherit_permissions: bool = Field(default=True, description="Inherit permissions from parent record") # Used in backend only to determine if the record should have a inherit permissions relation from its parent record
    indexing_status: str = Field(default=ProgressStatus.QUEUED.value, description="Indexing status for the record")
    extraction_status: str = Field(default=ProgressStatus.NOT_STARTED.value, description="Extraction status for the record")
    reason: str | None = Field(default=None, description="Reason for the record status")
    # Epoch Timestamps
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the record creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the record update in the source system")

    # Source information
    weburl: str | None = None
    signed_url: str | None = None
    preview_renderable: bool | None = True
    is_shared: bool | None = False
    is_shared_with_me: bool | None = False
    shared_with_me_record_group_id: str | None = None
    hide_weburl: bool = Field(default=False, description="Flag indicating if web URL should be hidden")
    is_internal: bool = Field(default=False, description="Flag indicating if record is internal")

    # Processing flags
    is_vlm_ocr_processed: bool | None = Field(default=False, description="Flag indicating if VLM OCR processing has been used to process the record")

    # Content blocks
    block_containers: BlocksContainer = Field(default_factory=BlocksContainer, description="List of block containers in this record")
    semantic_metadata: SemanticMetadata | None = None
    # Relationships
    parent_record_id: str | None = None
    child_record_ids: list[str] | None = Field(default_factory=list)
    related_record_ids: list[str] | None = Field(default_factory=list)

    # Related external records (for connectors to specify relations by external IDs)
    related_external_records: list[RelatedExternalRecord] | None = Field(default_factory=list, description="List of related external records to create LINKED_TO relations (not persisted)")
    # Hierarchy fields
    is_dependent_node: bool = Field(default=False, description="True for dependent records, False for root records")
    parent_node_id: str | None = Field(default=None, description="Internal record ID of the parent node")

    def _format_timestamp(self, epoch_ms: int | None) -> str:
        if epoch_ms is None:
            return "N/A"
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _format_person(self, name: str | None, email: str | None) -> str:
        """Helper to format a person with name and/or email"""
        if name and email:
            return f"{name} ({email})"
        return name or email or "N/A"

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        lines = [
            f"Record ID       : {self.id}",
            f"Name            : {self.record_name}",
            f"Connector       : {self.connector_name.value}",
            f"Type            : {self.record_type.value}",
            f"External ID     : {self.external_record_id}",
            f"Created At      : {self._format_timestamp(self.source_created_at)}",
            f"Last Updated At : {self._format_timestamp(self.source_updated_at)}",
            f"Connector ID    : {self.connector_id if self.connector_id else 'N/A'}",
            f"connector Name  : {self.connector_name.value if self.connector_name else 'N/A'}",
        ]
        if self.mime_type:
            lines.append(f"MIME Type       : {self.mime_type}")

        if self.weburl:
            if not self.weburl.startswith("http"):
                base_url = frontend_url or "http://localhost:3000"
                weburl = f"{base_url.rstrip('/')}{self.weburl}"
            else:
                weburl = self.weburl

            lines.append(f"Web URL         : {weburl}")

        if self.semantic_metadata:
            lines.extend(self.semantic_metadata.to_llm_context())

        return "\n".join(lines)

    def to_arango_base_record(self) -> dict:
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
            "reason": self.reason,
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
    def from_arango_base_record(arango_base_record: dict) -> "Record":
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
            record_group_type=arango_base_record.get("recordGroupType"),
            external_revision_id=arango_base_record.get("externalRevisionId"),
            external_record_id=arango_base_record["externalRecordId"],
            external_record_group_id=arango_base_record.get("externalGroupId"),
            record_group_id=arango_base_record.get("recordGroupId"),
            parent_external_record_id=arango_base_record.get("externalParentId"),
            version=arango_base_record["version"],
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=connector_name,
            connector_id=arango_base_record.get("connectorId"),
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record.get("webUrl"),
            created_at=arango_base_record.get("createdAtTimestamp"),
            updated_at=arango_base_record.get("updatedAtTimestamp"),
            source_created_at=arango_base_record.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_base_record.get("sourceLastModifiedTimestamp"),
            virtual_record_id=arango_base_record.get("virtualRecordId"),
            indexing_status=arango_base_record.get("indexingStatus", ProgressStatus.QUEUED.value),
            extraction_status=arango_base_record.get("extractionStatus", ProgressStatus.NOT_STARTED.value),
            preview_renderable=arango_base_record.get("previewRenderable", True),
            is_shared=arango_base_record.get("isShared", False),
            is_vlm_ocr_processed=arango_base_record.get("isVLMOcrProcessed", False),
            is_dependent_node=arango_base_record.get("isDependentNode", False),
            parent_node_id=arango_base_record.get("parentNodeId"),
            hide_weburl=arango_base_record.get("hideWeburl", False),
            is_internal=arango_base_record.get("isInternal", False),
            md5_hash=arango_base_record.get("md5Checksum"),
            size_in_bytes=arango_base_record.get("sizeInBytes"),
            reason=arango_base_record.get("reason"),
        )

    def to_kafka_record(self) -> dict:
        raise NotImplementedError("Implement this method in the subclass")

class FileRecord(Record):
    is_file: bool
    extension: str | None = None
    path: str | None = None
    etag: str | None = None
    ctag: str | None = None
    quick_xor_hash: str | None = None
    crc32_hash: str | None = None
    sha1_hash: str | None = None
    sha256_hash: str | None = None

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        """Returns formatted file-specific metadata for LLM context"""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.extension:
            specific_lines.append(f"* Extension: {self.extension}")

        if specific_lines:
            lines.append("File Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    def to_llm_full_context(self) -> list[LlmTextContent]:
        """
        Convert a record JSON object to message content format matching get_message_content.

        Args:
            record: The record JSON object containing block_containers and other metadata
            ref_mapper: Optional shared CitationRefMapper for tiny-ref generation

        Returns:
            Tuple of (content list, ref_mapper)
        """

        try:
            from app.utils.chat_helpers import valid_group_labels, build_group_blocks

            content: list[LlmTextContent] = []
            context_metadata = f"record/{self.id}"
            content.append(LlmTextContent(
                type="text",
                text=f"""<record>\n{context_metadata}
    Record blocks (sorted):\n\n""",
            ))
            # Process blocks
            block_containers = self.block_containers
            blocks = block_containers.blocks
            block_groups = block_containers.block_groups

            # build_group_blocks (in chat_helpers) expects plain dicts, not Pydantic models
            blocks_as_dicts = [b.model_dump() for b in blocks]
            block_groups_as_dicts = [bg.model_dump() for bg in block_groups]

            seen_block_groups = set()
            for block in blocks:
                block_type = block.type.value if block.type else None

                data = block.data or ""

                if block_type == BlockType.IMAGE.value:
                    continue
                elif block_type == BlockType.TEXT.value and block.parent_index is None:
                    content.append(LlmTextContent(
                        type="text",
                        text=f"* Block Type: {block_type}\n* Block Content: {data}\n\n",
                    ))
                elif block_type == BlockType.TABLE_ROW.value:
                    block_group_index = block.parent_index
                    block_group_id = f"{self.virtual_record_id or ''}-{block_group_index}"
                    if block_group_id in seen_block_groups:
                        continue
                    seen_block_groups.add(block_group_id)
                    if block_group_index is not None:
                        corresponding_block_group = block_groups[block_group_index]

                        block_type = corresponding_block_group.type.value if corresponding_block_group.type else None
                        data = corresponding_block_group.data or {}

                        if block_type == GroupType.TABLE.value:
                            children = corresponding_block_group.children
                            rows_to_be_included_list = []
                            if children:
                                for range_obj in children.block_ranges:
                                    rows_to_be_included_list.extend(range(range_obj.start, range_obj.end + 1))

                            child_results = []
                            for row_index in rows_to_be_included_list:
                                if row_index < len(blocks):
                                    row_block = blocks[row_index]
                                    block_data = row_block.data
                                    if isinstance(block_data, dict):
                                        row_text = block_data.get("row_natural_language_text", "")
                                    else:
                                        row_text = str(block_data or "")

                                    child_results.append({
                                        "content": row_text
                                    })

                            if child_results:
                                template = Template(agent_block_group_prompt)
                                rendered_form = template.render(
                                    block_group_index=block_group_index,
                                    label=GroupType.TABLE.value,
                                    blocks=child_results,
                                )
                                content.append(LlmTextContent(
                                    type="text",
                                    text=f"{rendered_form}\n\n",
                                ))
                elif block.parent_index is not None:
                    parent_index = block.parent_index
                    block_group_id = f"{self.virtual_record_id or ''}-{parent_index}"
                    if block_group_id in seen_block_groups:
                        continue
                    template = Template(agent_block_group_prompt)
                    if parent_index >= len(block_groups):
                        continue
                    block_group = block_groups[parent_index]
                    block_group_type = block_group.type.value if block_group.type else None
                    if block_group_type not in valid_group_labels:
                        continue

                    virtual_record_id = self.virtual_record_id or ""
                    group_blocks = build_group_blocks(block_groups_as_dicts, blocks_as_dicts, parent_index, virtual_record_id, self.to_kafka_record(), {})

                    if not group_blocks:
                        continue
                    seen_block_groups.add(block_group_id)
                    rendered_form = template.render(
                        block_group_index=parent_index,
                        label=block_group_type,
                        blocks=group_blocks,
                    )
                    content.append(LlmTextContent(
                        type="text",
                        text=f"{rendered_form}\n\n",
                    ))
                else:
                    continue

            return content
        except Exception as e:
            raise RuntimeError(f"Error in record_to_message_content: {e}") from e

    def to_arango_record(self) -> dict:
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
    def from_arango_record(arango_base_file_record: dict, arango_base_record: dict) -> "FileRecord":
        # Handle connectorName which might be missing for KB uploaded files
        conn_name_value = arango_base_record.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return FileRecord(
            id=arango_base_record.get("id", arango_base_record.get("_key")),
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=RecordType(arango_base_record["recordType"]),
            external_revision_id=arango_base_record.get("externalRevisionId"),
            external_record_id=arango_base_record["externalRecordId"],
            version=arango_base_record["version"],
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=connector_name,
            connector_id=arango_base_record.get("connectorId"),
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record["webUrl"],
            external_record_group_id=arango_base_record.get("externalGroupId"),
            record_group_id=arango_base_record.get("recordGroupId"),
            parent_external_record_id=arango_base_record.get("externalParentId"),
            created_at=arango_base_record["createdAtTimestamp"],
            updated_at=arango_base_record["updatedAtTimestamp"],
            source_created_at=arango_base_record["sourceCreatedAtTimestamp"],
            source_updated_at=arango_base_record["sourceLastModifiedTimestamp"],
            is_dependent_node=arango_base_record.get("isDependentNode", False),
            parent_node_id=arango_base_record.get("parentNodeId"),
            is_internal=arango_base_record.get("isInternal", False),
            is_file=arango_base_file_record.get("isFile", True),
            size_in_bytes=size if (size := arango_base_record.get("sizeInBytes")) is not None else arango_base_file_record.get("sizeInBytes"),
            extension=arango_base_file_record.get("extension"),
            path=arango_base_file_record.get("path"),
            etag=arango_base_file_record.get("etag"),
            ctag=arango_base_file_record.get("ctag"),
            quick_xor_hash=arango_base_file_record.get("quickXorHash"),
            crc32_hash=arango_base_file_record.get("crc32Hash"),
            sha1_hash=arango_base_file_record.get("sha1Hash"),
            sha256_hash=arango_base_file_record.get("sha256Hash"),
        )

    def to_kafka_record(self) -> dict:
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
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
            "isFile": self.is_file,
        }

class MessageRecord(Record):
    content: str | None = None

    def to_kafka_record(self) -> dict:
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
    subject: str | None = None
    from_email: str | None = None
    to_emails: list[str] | None = None
    cc_emails: list[str] | None = None
    bcc_emails: list[str] | None = None
    thread_id: str | None = None
    is_parent: bool = False
    internet_message_id: str | None = None
    conversation_index: str | None = None
    label_ids: list[str] | None = None

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        """Returns formatted email-specific metadata for LLM context"""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.subject:
            specific_lines.append(f"* Subject: {self.subject}")

        if self.from_email:
            specific_lines.append(f"* From: {self.from_email}")

        if self.to_emails:
            specific_lines.append(f"* To: {', '.join(self.to_emails)}")

        if self.cc_emails:
            specific_lines.append(f"* CC: {', '.join(self.cc_emails)}")

        if self.bcc_emails:
            specific_lines.append(f"* BCC: {', '.join(self.bcc_emails)}")

        if specific_lines:
            lines.append("Email Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    def to_arango_record(self) -> dict:
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
            "labelIds": self.label_ids or [],
        }


    def to_kafka_record(self) -> dict:
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
    def from_arango_record(mail_doc: dict, record_doc: dict) -> "MailRecord":
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
            label_ids=mail_doc.get("labelIds", []),
        )

class WebpageRecord(Record):
    def to_kafka_record(self) -> dict:
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
        }

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
        }

    @staticmethod
    def from_arango_record(webpage_doc: dict, record_doc: dict) -> "WebpageRecord":
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
    title: str | None = None
    is_public: LinkPublicStatus = Field(description="Link public accessibility status")
    linked_record_id: str | None = Field(default=None, description="Internal record ID of linked record with same weburl")

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        """Returns formatted link-specific metadata for LLM context"""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.url:
            specific_lines.append(f"* URL: {self.url}")

        if self.title:
            specific_lines.append(f"* Title: {self.title}")

        if self.is_public:
            public_status = self.is_public.value if isinstance(self.is_public, Enum) else self.is_public
            specific_lines.append(f"* Public Access: {public_status}")

        if self.linked_record_id:
            specific_lines.append(f"* Linked Record ID: {self.linked_record_id}")

        if specific_lines:
            lines.append("Link Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    def to_kafka_record(self) -> dict:
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
            "webUrl": self.weburl,
        }

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "url": self.url,
            "title": self.title,
            "isPublic": self.is_public.value,
            "linkedRecordId": self.linked_record_id,
        }

    @staticmethod
    def from_arango_record(link_doc: dict, record_doc: dict) -> "LinkRecord":
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
    resolution_status: str | None = None
    comment_selection: str | None = None

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        """Returns formatted comment-specific metadata for LLM context"""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.resolution_status:
            specific_lines.append(f"* Resolution Status: {self.resolution_status}")

        if specific_lines:
            lines.append("Comment Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    def to_kafka_record(self) -> dict:
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

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "authorSourceId": self.author_source_id,
            "resolutionStatus": self.resolution_status,
            "commentSelection": self.comment_selection,
        }

    @staticmethod
    def from_arango_record(comment_doc: dict, record_doc: dict) -> "CommentRecord":
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
            parent_node_id=record_doc.get("parentNodeId"),
            author_source_id=comment_doc.get("authorSourceId") or comment_doc.get("authorId") or "unknown",
            resolution_status=comment_doc.get("resolutionStatus"),
            comment_selection=comment_doc.get("commentSelection"),
        )

class TicketRecord(Record):
    status: Status | str | None = None
    priority: Priority | str | None = None
    type: ItemType | str | None = None
    delivery_status: DeliveryStatus | str | None = None
    assignee: str | None = None
    reporter_email: str | None = None
    assignee_email: str | None = None
    reporter_name: str | None = None
    creator_email: str | None = None
    creator_name: str | None = None
    # Connector-provided timestamps for when relationships were established
    assignee_source_timestamp: int | None = None
    creator_source_timestamp: int | None = None
    reporter_source_timestamp: int | None = None
    labels: list[str] | None = Field(default_factory=list)
    is_email_hidden: bool = False # this means reporters, assignees... emails are hidden and represents connector's native id
    assignee_source_id: list[str] | None = Field(default_factory=list) # this means reporters  source ids in the connector system
    reporter_source_id:str | None=None

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        """Returns formatted ticket-specific metadata for LLM context"""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.status:
            status_val = self.status.value if isinstance(self.status, Enum) else self.status
            specific_lines.append(f"* Status: {status_val}")

        if self.priority:
            priority_val = self.priority.value if isinstance(self.priority, Enum) else self.priority
            specific_lines.append(f"* Priority: {priority_val}")

        if self.type:
            type_val = self.type.value if isinstance(self.type, Enum) else self.type
            specific_lines.append(f"* Type: {type_val}")

        if self.assignee or self.assignee_email:
            specific_lines.append(f"* Assignee: {self._format_person(self.assignee, self.assignee_email)}")

        if self.delivery_status:
            delivery_val = self.delivery_status.value if isinstance(self.delivery_status, Enum) else self.delivery_status
            specific_lines.append(f"* Delivery Status: {delivery_val}")

        if self.reporter_name or self.reporter_email:
            specific_lines.append(f"* Reporter: {self._format_person(self.reporter_name, self.reporter_email)}")

        if self.creator_name or self.creator_email:
            specific_lines.append(f"* Creator: {self._format_person(self.creator_name, self.creator_email)}")

        if specific_lines:
            lines.append("Ticket Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    def to_arango_record(self) -> dict:
        def _get_value(field_value: Enum | str | None) -> str | None:
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
            "assigneeSourceTimestamp": self.assignee_source_timestamp,
            "creatorSourceTimestamp": self.creator_source_timestamp,
            "reporterSourceTimestamp": self.reporter_source_timestamp,
            "labels":self.labels ,
            "assignee_source_id": self.assignee_source_id ,
            "reporter_source_id": self.reporter_source_id,
            "is_email_hidden": self.is_email_hidden,
        }

    @staticmethod
    def _safe_enum_parse(value: str | None, enum_class: builtins.type[EnumType]) -> EnumType | str | None:
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
    def from_arango_record(ticket_doc: dict, record_doc: dict) -> "TicketRecord":
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
            parent_node_id=record_doc.get("parentNodeId"),
            status=TicketRecord._safe_enum_parse(ticket_doc.get("status"), Status),
            priority=TicketRecord._safe_enum_parse(ticket_doc.get("priority"), Priority),
            type=TicketRecord._safe_enum_parse(ticket_doc.get("type"), ItemType),
            delivery_status=TicketRecord._safe_enum_parse(ticket_doc.get("deliveryStatus"), DeliveryStatus),
            assignee=ticket_doc.get("assignee"),
            reporter_email=ticket_doc.get("reporterEmail"),
            assignee_email=ticket_doc.get("assigneeEmail"),
            reporter_name=ticket_doc.get("reporterName"),
            creator_email=ticket_doc.get("creatorEmail"),
            creator_name=ticket_doc.get("creatorName"),
            assignee_source_timestamp=ticket_doc.get("assigneeSourceTimestamp"),
            creator_source_timestamp=ticket_doc.get("creatorSourceTimestamp"),
            reporter_source_timestamp=ticket_doc.get("reporterSourceTimestamp"),
            labels=ticket_doc.get("labels"),
        )

    def to_kafka_record(self) -> dict:

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
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

class ProjectRecord(Record):
    """Record class for projects"""
    status: str | None = None
    priority: str | None = None
    lead_id: str | None = None
    lead_name: str | None = None
    lead_email: str | None = None

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        """Returns formatted project-specific metadata for LLM context"""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.status:
            specific_lines.append(f"* Status: {self.status}")

        if self.priority:
            specific_lines.append(f"* Priority: {self.priority}")

        if self.lead_name or self.lead_email:
            specific_lines.append(f"* Lead: {self._format_person(self.lead_name, self.lead_email)}")

        if specific_lines:
            lines.append("Project Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    def to_arango_record(self) -> dict:
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
    def from_arango_record(project_doc: dict, record_doc: dict) -> "ProjectRecord":
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
            parent_node_id=record_doc.get("parentNodeId"),
            status=project_doc.get("status"),
            priority=project_doc.get("priority"),
            lead_id=project_doc.get("leadId"),
            lead_name=project_doc.get("leadName"),
            lead_email=project_doc.get("leadEmail"),
        )

    def to_kafka_record(self) -> dict:
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
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }


class ProductRecord(Record):
    """Record class for products with Name, ProductCode, ProductFamily."""

    product_code: str | None = Field(default=None, description="Product code")
    product_family: str | None = Field(default=None, description="Product family")
    is_active: bool | None = Field(default=None, description="Whether the product is active")
    sku: str | None = Field(default=None, description="Stock keeping unit")
    list_price: float | None = Field(default=None, description="Standard list price from pricebook")

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "productCode": self.product_code,
            "productFamily": self.product_family,
            "isActive": self.is_active,
            "sku": self.sku,
            "listPrice": self.list_price,
        }

    @staticmethod
    def from_arango_record(product_doc: dict, record_doc: dict) -> "ProductRecord":
        """Create ProductRecord from ArangoDB documents (records + products collections)."""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return ProductRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"], #required
            record_name=record_doc["recordName"], #required
            record_type=RecordType(record_doc["recordType"]), #required
            external_record_id=record_doc["externalRecordId"], #required
            external_revision_id=record_doc.get("externalRevisionId"),#optional
            external_record_group_id=record_doc.get("externalGroupId"),#optional
            record_group_id=record_doc.get("recordGroupId"),#optional
            parent_external_record_id=record_doc.get("externalParentId"), #optional
            version=record_doc["version"], #required
            origin=OriginTypes(record_doc["origin"]), #required
            connector_name=connector_name, #required
            connector_id=record_doc.get("connectorId"), #required
            mime_type=record_doc.get("mimeType", MimeTypes.MARKDOWN.value), #required use MimeTypes.MARKDOWN for markdown files
            weburl=record_doc.get("webUrl"), #optional
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"), #optional, default is None
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"), #optional, default is None
            virtual_record_id=record_doc.get("virtualRecordId"), #optional, default is None
            preview_renderable=record_doc.get("previewRenderable", False), #optional, default is False
            is_dependent_node=record_doc.get("isDependentNode", False), #optional, default is False
            parent_node_id=record_doc.get("parentNodeId", None), #optional, default is None
            product_code=product_doc.get("productCode"),
            product_family=product_doc.get("productFamily"),
            is_active=product_doc.get("isActive"),
            sku=product_doc.get("sku"),
            list_price=product_doc.get("listPrice"),
        )

    def to_kafka_record(self) -> dict:
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
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

    def to_llm_context(
        self,
        frontend_url: str | None = None,
    ) -> str:
        """Returns formatted product-specific metadata for LLM context."""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.product_code:
            specific_lines.append(f"* Product Code: {self.product_code}")
        if self.product_family:
            specific_lines.append(f"* Product Family: {self.product_family}")
        if self.is_active is not None:
            specific_lines.append(f"* Active: {self.is_active}")
        if self.sku:
            specific_lines.append(f"* SKU: {self.sku}")
        if self.list_price is not None:
            specific_lines.append(f"* List Price: {self.list_price}")

        if specific_lines:
            lines.append("Product Information:")
            lines.extend(specific_lines)
        return "\n".join(lines)


class DealRecord(Record):
    """Record class for deals/opportunities with sales pipeline information."""

    name: str | None = Field(default=None, description="Deal name")
    amount: float | None = Field(default=None, description="Deal amount")
    expected_revenue: float | None = Field(default=None, description="Expected revenue")
    expected_close_date: str | None = Field(default=None, description="Expected close date")
    conversion_probability: float | None = Field(default=None, description="Conversion probability")
    type: str | None = Field(default=None, description="Deal type")
    owner_id: str | None = Field(default=None, description="Owner ID")
    is_won: bool | None = Field(default=None, description="Is deal won")
    is_closed: bool | None = Field(default=None, description="Is deal closed")
    created_date: str | None = Field(default=None, description="Created date")
    close_date: str | None = Field(default=None, description="Close date")

    @staticmethod
    async def fetch_deal_info_edges_to_deal(graph_provider: Any, record_id: str) -> list[dict[str, Any]]:
        """Load dealInfo edges whose graph target (_to) is this deal record (Org → Deal)."""
        node_id = f"{CollectionNames.RECORDS.value}/{record_id}"
        return await graph_provider.get_edges_to_node(
            node_id=node_id,
            edge_collection=CollectionNames.DEAL_INFO.value,
        )

    @staticmethod
    async def fetch_sold_in_edges_with_products_to_deal(graph_provider: Any, record_id: str) -> list[dict[str, Any]]:
        """Load soldIn edges to this deal and resolve each edge's _from product document."""
        node_id = f"{CollectionNames.RECORDS.value}/{record_id}"
        sold_in_edges = await graph_provider.get_edges_to_node(
            node_id=node_id,
            edge_collection=CollectionNames.SOLD_IN.value,
        )

        relations: list[dict[str, Any]] = []
        for edge in sold_in_edges:
            from_ref = edge.get("_from")
            if not from_ref:
                from_collection = edge.get("from_collection")
                from_id = edge.get("from_id")
                if from_collection and from_id:
                    from_ref = f"{from_collection}/{from_id}"

            product_doc = None
            if from_ref and "/" in from_ref:
                from_collection, from_key = from_ref.split("/", 1)
                if from_collection == CollectionNames.RECORDS.value and from_key:
                    try:
                        product_doc = await graph_provider.get_document(
                            document_key=from_key,
                            collection=CollectionNames.RECORDS.value,
                        )
                    except (ValueError, TypeError, AttributeError):
                        product_doc = None
            relations.append({"edge": edge, "product": product_doc})

        return relations

    def _deal_info_edges_to_llm_lines(self, edges: list[dict[str, Any]]) -> list[str]:
        """Format incoming dealInfo edge documents for LLM context (Arango _from/_to or Neo4j generic shape)."""
        lines: list[str] = ["DealInfo relations (incoming to this deal, Org → Deal):"]
        if not edges:
            lines.append("* No incoming dealInfo edges.")
            return lines
        for i, edge in enumerate(edges, start=1):
            from_ref = edge.get("_from")
            if not from_ref:
                fc, fid = edge.get("from_collection"), edge.get("from_id")
                if fc and fid:
                    from_ref = f"{fc}/{fid}"
                elif fid:
                    from_ref = str(fid)
            parts: list[str] = []
            if from_ref:
                parts.append(f"from {from_ref}")
            if edge.get("stage") is not None:
                parts.append(f"stage: {edge['stage']}")
            cat, uat = edge.get("createdAtTimestamp"), edge.get("updatedAtTimestamp")
            if cat is not None:
                parts.append(f"edge created: {self._format_timestamp(cat)}")
            if uat is not None:
                parts.append(f"edge updated: {self._format_timestamp(uat)}")
            lines.append(f"* [{i}] " + ("; ".join(parts) if parts else "(no edge attributes)"))
        return lines

    def _sold_in_edges_products_to_llm_lines(self, relations: list[dict[str, Any]]) -> list[str]:
        """Format soldIn edge + product data for LLM context.
        Each individual line item is emitted as its own numbered instance."""
        lines: list[str] = ["Products in this deal:"]
        if not relations:
            lines.append("* No products in this deal.")
            return lines

        counter = 1
        for relation in relations:
            edge = relation.get("edge", {}) or {}
            product = relation.get("product", {}) or {}

            from_ref = edge.get("_from")
            if not from_ref:
                fc, fid = edge.get("from_collection"), edge.get("from_id")
                if fc and fid:
                    from_ref = f"{fc}/{fid}"
                elif fid:
                    from_ref = str(fid)

            product_name = product.get("recordName")
            quantities = edge.get("quantities") or []
            unit_prices = edge.get("unitPrices") or []
            total_prices = edge.get("totalPrices") or []
            is_deleted_flags = edge.get("isDeletedFlags") or []
            cat, uat = edge.get("createdAtTimestamp"), edge.get("updatedAtTimestamp")

            count = max(len(quantities), len(unit_prices), len(total_prices), len(is_deleted_flags))
            for i in range(count):
                parts = []
                if from_ref:
                    parts.append(f"from {from_ref}")
                if product_name:
                    parts.append(f"product: {product_name}")
                q = quantities[i] if i < len(quantities) else None
                up = unit_prices[i] if i < len(unit_prices) else None
                tp = total_prices[i] if i < len(total_prices) else None
                is_deleted = is_deleted_flags[i] if i < len(is_deleted_flags) else None
                if q is not None:
                    parts.append(f"qty: {q}")
                if up is not None:
                    parts.append(f"unitPrice: {up}")
                if tp is not None:
                    parts.append(f"totalPrice: {tp}")
                if is_deleted is not None:
                    parts.append(f"isDeleted: {is_deleted}")
                if cat is not None:
                    parts.append(f"edge created: {self._format_timestamp(cat)}")
                if uat is not None:
                    parts.append(f"edge updated: {self._format_timestamp(uat)}")
                lines.append(f"* [{counter}] " + ("; ".join(parts) if parts else "(no attributes)"))
                counter += 1

        return lines

    def to_llm_context(
        self,
        frontend_url: str | None = None,
    ) -> str:
        """Returns formatted deal/opportunity-specific metadata for LLM context."""
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]

        specific_lines = []
        if self.name:
            specific_lines.append(f"* Deal Name: {self.name}")
        if self.amount is not None:
            specific_lines.append(f"* Amount: {self.amount}")
        if self.expected_revenue is not None:
            specific_lines.append(f"* Expected Revenue: {self.expected_revenue}")
        if self.expected_close_date:
            specific_lines.append(f"* Expected Close Date: {self.expected_close_date}")
        if self.conversion_probability is not None:
            specific_lines.append(f"* Conversion Probability: {self.conversion_probability}")
        if self.type:
            specific_lines.append(f"* Deal Type: {self.type}")
        if self.owner_id:
            specific_lines.append(f"* Owner ID: {self.owner_id}")
        if self.is_won is not None:
            specific_lines.append(f"* Won: {self.is_won}")
        if self.is_closed is not None:
            specific_lines.append(f"* Closed: {self.is_closed}")
        if self.created_date:
            specific_lines.append(f"* Created Date: {self.created_date}")
        if self.close_date:
            specific_lines.append(f"* Close Date: {self.close_date}")

        if specific_lines:
            lines.append("Deal Information:")
            lines.extend(specific_lines)

        return "\n".join(lines)

    async def to_llm_context_with_graph(
        self,
        frontend_url: str | None = None,
        graph_provider: Any = None,
    ) -> str:
        """
        Returns full deal LLM context including graph-edge data (sales deal
        relationships and sold-in product links).  Callers that have a
        graph_provider should use this method; callers without one can fall
        back to the synchronous to_llm_context().
        """
        base_context = self.to_llm_context(frontend_url=frontend_url)
        if not graph_provider:
            return base_context

        lines = [base_context]

        try:
            deal_info_edges = await self.fetch_deal_info_edges_to_deal(graph_provider, self.id)
            if deal_info_edges is not None:
                lines.extend(self._deal_info_edges_to_llm_lines(deal_info_edges))
        except (ValueError, TypeError, AttributeError):
            pass

        try:
            sold_in_relations = await self.fetch_sold_in_edges_with_products_to_deal(graph_provider, self.id)
            if sold_in_relations is not None:
                lines.extend(self._sold_in_edges_products_to_llm_lines(sold_in_relations))
        except (ValueError, TypeError, AttributeError):
            pass

        return "\n".join(lines)

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.name,
            "amount": self.amount,
            "expectedRevenue": self.expected_revenue,
            "expectedCloseDate": self.expected_close_date,
            "conversionProbability": self.conversion_probability,
            "type": self.type,
            "ownerId": self.owner_id,
            "isWon": self.is_won,
            "isClosed": self.is_closed,
            "createdDate": self.created_date,
            "closeDate": self.close_date,
        }

    @staticmethod
    def from_arango_record(deal_doc: dict, record_doc: dict) -> "DealRecord":
        """Create DealRecord from ArangoDB documents (records + deals collections)."""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return DealRecord(
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
            mime_type=record_doc.get("mimeType", MimeTypes.MARKDOWN.value),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            preview_renderable=record_doc.get("previewRenderable", False),
            is_dependent_node=record_doc.get("isDependentNode", False),
            parent_node_id=record_doc.get("parentNodeId", None),
            name=deal_doc.get("name"),
            amount=deal_doc.get("amount"),
            expected_revenue=deal_doc.get("expectedRevenue"),
            expected_close_date=deal_doc.get("expectedCloseDate"),
            conversion_probability=deal_doc.get("conversionProbability"),
            type=deal_doc.get("type"),
            owner_id=deal_doc.get("ownerId"),
            is_won=deal_doc.get("isWon"),
            is_closed=deal_doc.get("isClosed"),
            created_date=deal_doc.get("createdDate"),
            close_date=deal_doc.get("closeDate"),
        )

    def to_kafka_record(self) -> dict:
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
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }


class SharePointListRecord(Record):
    """Record class for SharePoint lists"""

    def to_kafka_record(self) -> dict:
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

    def to_kafka_record(self) -> dict:
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

    def to_kafka_record(self) -> dict:
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

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
        }

    def to_kafka_record(self) -> dict:
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


class SQLViewRecord(Record):
    """Record class for SQL views (Snowflake, etc.)"""
    database_name: Optional[str] = Field(default=None, description="Database containing the view")
    schema_name: Optional[str] = Field(default=None, description="Schema containing the view")
    fqn: Optional[str] = Field(default=None, description="Fully qualified name: database.schema.view")
    definition: Optional[str] = Field(default=None, description="SQL definition of the view")
    source_tables: Optional[List[str]] = Field(default_factory=list, description="Tables referenced by this view")
    is_secure: bool = Field(default=False, description="Whether this is a secure view")
    comment: Optional[str] = Field(default=None, description="View description/comment")

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "databaseName": self.database_name,
            "schemaName": self.schema_name,
            "fqn": self.fqn,
            "definition": self.definition,
            "sourceTables": self.source_tables,
            "isSecure": self.is_secure,
            "comment": self.comment,
        }

    @staticmethod
    def from_arango_record(view_doc: dict, record_doc: dict) -> "SQLViewRecord":
        """Create SQLViewRecord from ArangoDB documents (records + sqlViews collections)."""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return SQLViewRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            record_group_id=record_doc.get("recordGroupId"),
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
            parent_node_id=record_doc.get("parentNodeId"),
            database_name=view_doc.get("databaseName"),
            schema_name=view_doc.get("schemaName"),
            fqn=view_doc.get("fqn"),
            definition=view_doc.get("definition"),
            source_tables=view_doc.get("sourceTables") or [],
            is_secure=view_doc.get("isSecure", False),
            comment=view_doc.get("comment"),
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
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
        }


class SQLTableRecord(Record):
    """Record class for SQL tables (Snowflake, etc.)"""
    database_name: Optional[str] = Field(default=None, description="Database containing the table")
    schema_name: Optional[str] = Field(default=None, description="Schema containing the table")
    fqn: Optional[str] = Field(default=None, description="Fully qualified name: database.schema.table")
    row_count: Optional[int] = Field(default=None, description="Number of rows in the table")
    size_bytes: Optional[int] = Field(default=None, description="Size of the table in bytes")
    column_count: Optional[int] = Field(default=None, description="Number of columns in the table")
    ddl: Optional[str] = Field(default=None, description="CREATE TABLE DDL statement")
    primary_keys: Optional[List[str]] = Field(default_factory=list, description="Primary key column names")
    foreign_keys: Optional[List[Dict]] = Field(default_factory=list, description="Foreign key definitions")
    comment: Optional[str] = Field(default=None, description="Table description/comment")

    def to_arango_record(self) -> Dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "databaseName": self.database_name,
            "schemaName": self.schema_name,
            "fqn": self.fqn,
            "rowCount": self.row_count,
            "sizeInBytes": self.size_bytes,
            "columnCount": self.column_count,
            "ddl": self.ddl,
            "primaryKeys": self.primary_keys,
            "foreignKeys": self.foreign_keys,
            "comment": self.comment,
        }

    @staticmethod
    def from_arango_record(table_doc: dict, record_doc: dict) -> "SQLTableRecord":
        """Create SQLTableRecord from ArangoDB documents (records + sqlTables collections)."""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return SQLTableRecord(
            id=record_doc.get("id", record_doc.get("_key")),
            org_id=record_doc["orgId"],
            record_name=record_doc["recordName"],
            record_type=RecordType(record_doc["recordType"]),
            external_record_id=record_doc["externalRecordId"],
            external_revision_id=record_doc.get("externalRevisionId"),
            external_record_group_id=record_doc.get("externalGroupId"),
            parent_external_record_id=record_doc.get("externalParentId"),
            record_group_id=record_doc.get("recordGroupId"),
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
            parent_node_id=record_doc.get("parentNodeId"),
            database_name=table_doc.get("databaseName"),
            schema_name=table_doc.get("schemaName"),
            fqn=table_doc.get("fqn"),
            row_count=table_doc.get("rowCount"),
            size_bytes=table_doc.get("sizeInBytes"),
            column_count=table_doc.get("columnCount"),
            ddl=table_doc.get("ddl"),
            primary_keys=table_doc.get("primaryKeys") or [],
            foreign_keys=table_doc.get("foreignKeys") or [],
            comment=table_doc.get("comment"),
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
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "parentExternalRecordId": self.parent_external_record_id,
        }

class PullRequestRecord(Record):
    """Record class for Github Pull Request"""
    status: str | None = None
    assignee: list[str] | None = Field(default_factory=list)
    assignee_email: list[str] | None = Field(default_factory=list)
    creator_email: str | None = None
    creator_name: str | None = None
    review_email: list[str] | None = Field(default_factory=list)
    review_name: list[str] | None = Field(default_factory=list)
    mergeable: str | None = None
    merged_by: str | None = None
    labels: list[str] | None = Field(default_factory=list)
    last_commit_sha: str | None = Field(default=None)

    def to_kafka_record(self) -> dict:
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
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "lastCommitSha": self.last_commit_sha,
        }
    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "status": self.status,
            "assignee": self.assignee,
            "assigneeEmail": self.assignee_email ,
            "creatorEmail": self.creator_email,
            "creatorName": self.creator_name,
            "reviewEmail": self.review_email ,
            "reviewName": self.review_name ,
            "mergeable": self.mergeable,
            "mergedBy": self.merged_by,
            "labels":self.labels ,
            "lastCommitSha": self.last_commit_sha,
        }

class LifecycleStatus(str, Enum):
    """Lifecycle status of the artifact"""
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class ArtifactType(str, Enum):
    """Type of artifact produced by sandbox code execution."""
    CODE_OUTPUT = "CODE_OUTPUT"
    CHART = "CHART"
    DOCUMENT = "DOCUMENT"
    IMAGE = "IMAGE"
    SPREADSHEET = "SPREADSHEET"
    PRESENTATION = "PRESENTATION"
    DATA_FILE = "DATA_FILE"
    OTHER = "OTHER"


class ArtifactRecord(Record):
    """Record class for Artifacts"""
    description: str = Field(description="Description of the artifact", default="")
    lifecycle_status: LifecycleStatus = Field(description="Lifecycle status of the artifact", default=LifecycleStatus.PUBLISHED)
    artifact_type: ArtifactType = Field(default=ArtifactType.OTHER, description="Type of artifact")
    source_tool: str | None = Field(default=None, description="Tool that generated this artifact (e.g. coding_sandbox.execute_python)")
    conversation_id: str | None = Field(default=None, description="Conversation that produced this artifact")
    is_temporary: bool = Field(default=False, description="Whether this artifact is eligible for automatic cleanup")
    expires_at: int | None = Field(default=None, description="Epoch ms timestamp for auto-cleanup of temporary artifacts")

    def to_arango_artifact_record(self) -> dict:
        """Return artifact sub-record for the ``artifacts`` collection."""
        _, ext = os.path.splitext(self.record_name) if self.record_name else ("", "")
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "extension": ext.lstrip(".") if ext else None,
            "mimeType": self.mime_type,
            "sizeInBytes": self.size_in_bytes,
            "description": self.description,
            "lifecycleStatus": self.lifecycle_status.value,
            "artifactType": self.artifact_type.value,
            "sourceTool": self.source_tool,
            "conversationId": self.conversation_id,
            "isTemporary": self.is_temporary,
            "expiresAt": self.expires_at,
        }

    @staticmethod
    def from_arango_record(artifact_doc: dict, record_doc: dict) -> "ArtifactRecord":
        """Create ArtifactRecord from ArangoDB documents (records + artifacts collections)."""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.CODING_SANDBOX
        except ValueError:
            connector_name = Connectors.CODING_SANDBOX

        lifecycle_raw = artifact_doc.get("lifecycleStatus")
        try:
            lifecycle = LifecycleStatus(lifecycle_raw) if lifecycle_raw else LifecycleStatus.PUBLISHED
        except ValueError:
            lifecycle = LifecycleStatus.PUBLISHED

        artifact_type_raw = artifact_doc.get("artifactType")
        try:
            artifact_type = ArtifactType(artifact_type_raw) if artifact_type_raw else ArtifactType.OTHER
        except ValueError:
            artifact_type = ArtifactType.OTHER

        return ArtifactRecord(
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
            mime_type=record_doc.get("mimeType"),
            weburl=record_doc.get("webUrl"),
            created_at=record_doc.get("createdAtTimestamp"),
            updated_at=record_doc.get("updatedAtTimestamp"),
            source_created_at=record_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=record_doc.get("sourceLastModifiedTimestamp"),
            virtual_record_id=record_doc.get("virtualRecordId"),
            size_in_bytes=record_doc.get("sizeInBytes"),
            preview_renderable=record_doc.get("previewRenderable", True),
            hide_weburl=record_doc.get("hideWeburl", False),
            description=artifact_doc.get("description", ""),
            lifecycle_status=lifecycle,
            artifact_type=artifact_type,
            source_tool=artifact_doc.get("sourceTool"),
            conversation_id=artifact_doc.get("conversationId"),
            is_temporary=artifact_doc.get("isTemporary", False),
            expires_at=artifact_doc.get("expiresAt"),
        )

        
class RecordGroup(BaseModel):
    id: str = Field(description="Unique identifier for the record group", default_factory=lambda: str(uuid4()))
    org_id: str = Field(description="Unique identifier for the organization", default="")
    name: str = Field(description="Name of the record group")
    short_name: str | None = Field(default=None, description="Short name of the record group")
    description: str | None = Field(default=None, description="Description of the record group")
    external_group_id: str | None = Field(description="External identifier for the record group")
    parent_external_group_id: str | None = Field(default=None, description="External identifier for the parent record group")
    parent_record_group_id: str | None = Field(default=None, description="Internal identifier for the parent record group")
    connector_name: Connectors = Field(description="Name of the connector used to create the record group")
    connector_id: str = Field(description="Unique identifier for the connector configuration instance")
    web_url: str | None = Field(default=None, description="Web URL of the record group")
    group_type: RecordGroupType | None = Field(description="Type of the record group")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record group creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the record group update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the record group creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the record group update in the source system")
    inherit_permissions: bool | None = Field(default=False, description="Permissions for the record group")
    is_internal: bool | None = Field(default=False, description="Flag indicating if the record group is for internal use")

    def to_arango_base_record_group(self) -> dict:
        return {
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
            "isInternal": self.is_internal,
            "webUrl": self.web_url,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

    @staticmethod
    def from_arango_base_record_group(arango_base_record_group: dict) -> "RecordGroup":
        return RecordGroup(
            id=arango_base_record_group.get("id", arango_base_record_group.get("_key")),
            org_id=arango_base_record_group.get("orgId", ""),
            name=arango_base_record_group.get("groupName"),
            short_name=arango_base_record_group.get("shortName"),
            description=arango_base_record_group.get("description"),
            external_group_id=arango_base_record_group.get("externalGroupId"),
            parent_external_group_id=arango_base_record_group.get("parentExternalGroupId"),
            connector_name=arango_base_record_group.get("connectorName", Connectors.KNOWLEDGE_BASE),
            connector_id=arango_base_record_group.get("connectorId"),
            is_internal=arango_base_record_group.get("isInternal", False),
            group_type=arango_base_record_group.get("groupType", RecordGroupType.KB),
            web_url=arango_base_record_group.get("webUrl"),
            created_at=arango_base_record_group.get("createdAtTimestamp", get_epoch_timestamp_in_ms()),
            updated_at=arango_base_record_group.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()),
            source_created_at=arango_base_record_group.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_base_record_group.get("sourceLastModifiedTimestamp"),
        )

class ArtifactsRecordGroup(RecordGroup):
    """Record group class for Artifacts"""
    description: str = Field(description="Description of the artifact", default="")

class CodeFileRecord(Record):
    """Record class for Code Files"""

    file_path: str | None = None
    file_hash: str | None = None

    def to_kafka_record(self) -> dict:
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
            "origin": self.origin.value,
            "webUrl": self.weburl,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
            "filePath": self.file_path,
            "fileHash": self.file_hash,
        }

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "orgId": self.org_id,
            "name": self.record_name,
            "filePath": self.file_path,
            "fileHash": self.file_hash,
        }

    @staticmethod
    def from_arango_record(
        arango_base_code_file_record: dict, arango_base_record: dict
    ) -> "CodeFileRecord":
        """Create CodeFileRecord from ArangoDB documents (records + codeFiles collections)"""
        conn_name_value = arango_base_record.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE
        return CodeFileRecord(
            id=arango_base_record.get("id", arango_base_record.get("_key")),
            org_id=arango_base_record["orgId"],
            record_name=arango_base_record["recordName"],
            record_type=RecordType(arango_base_record["recordType"]),
            external_record_id=arango_base_record["externalRecordId"],
            file_path=arango_base_code_file_record.get("filePath"),
            file_hash=arango_base_code_file_record.get("fileHash"),
            origin=OriginTypes(arango_base_record["origin"]),
            connector_name=connector_name,
            connector_id=arango_base_record.get("connectorId"),
            mime_type=arango_base_record.get("mimeType", MimeTypes.UNKNOWN.value),
            weburl=arango_base_record.get("webUrl"),
        )


class Anyone(BaseModel):
    id: str = Field(description="Unique identifier for the anyone", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class AnyoneWithLink(BaseModel):
    id: str = Field(description="Unique identifier for the anyone with link", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone with link")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class AnyoneSameOrg(BaseModel):
    id: str = Field(description="Unique identifier for the anyone same org", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone same org")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone same org creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone same org update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone same org creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone same org update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class Org(BaseModel):
    id: str = Field(description="Unique identifier for the organization", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the organization")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the organization creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the organization update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the organization creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the organization update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")
    account_type: str | None = Field(default=None, description="Account type - individual or enterprise")
    is_external: bool | None = Field(default=None, description="Whether the org is an external account (e.g. Salesforce account)")
    is_active: bool | None = Field(default=None, description="Whether the organization is active")
    website: str | None = Field(default=None, description="Organization website URL")
    industry: str | None = Field(default=None, description="Industry sector")
    ownership_type: str | None = Field(default=None, description="Ownership type: public, private, subsidiary, government, other")
    phone: str | None = Field(default=None, description="Organization phone number")
    duns_id: str | None = Field(default=None, description="DUNS number (Data Universal Numbering System)")

    def to_arango_org(self) -> dict[str, Any]:
        """Convert Org model to ArangoDB document format. Output matches orgs_schema (no extra fields)."""
        return {
            "_key": self.id,
            "name": self.name,
            "accountType": self.account_type if self.account_type is not None else "enterprise",
            "isActive": self.is_active if self.is_active is not None else True,
            "isExternal": self.is_external if self.is_external is not None else False,
            "website": self.website,
            "industry": self.industry,
            "ownershipType": self.ownership_type,
            "phone": self.phone,
            "dunsId": self.duns_id,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "sourceCreatedAtTimestamp": self.source_created_at,
            "sourceLastModifiedTimestamp": self.source_updated_at,
        }

    @staticmethod
    def from_arango_org(data: dict[str, Any]) -> 'Org':
        """Create Org model from ArangoDB document."""
        return Org(
            id=data.get("id", data.get("_key")),
            name=data.get("name", ""),
            created_at=data.get("createdAtTimestamp", get_epoch_timestamp_in_ms()),
            updated_at=data.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()),
            source_created_at=data.get("sourceCreatedAtTimestamp"),
            source_updated_at=data.get("sourceLastModifiedTimestamp"),
            org_id=data.get("orgId", ""),
            account_type=data.get("accountType"),
            is_external=data.get("isExternal", False),
            is_active=data.get("isActive"),
            website=data.get("website"),
            industry=data.get("industry"),
            ownership_type=data.get("ownershipType"),
            phone=data.get("phone"),
            duns_id=data.get("dunsId"),
        )

class Domain(BaseModel):
    id: str = Field(description="Unique identifier for the domain", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the domain")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the domain creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the domain update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the domain creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the domain update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")

class AnyOneWithLink(BaseModel):
    id: str = Field(description="Unique identifier for the anyone with link", default_factory=lambda: str(uuid4()))
    name: str = Field(description="Name of the anyone with link")
    created_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link creation")
    updated_at: int = Field(default=get_epoch_timestamp_in_ms(), description="Epoch timestamp in milliseconds of the anyone with link update")
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the anyone with link update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")


class User(BaseModel):
    id: str = Field(description="Unique identifier for the user", default_factory=lambda: str(uuid4()))
    email: str
    source_user_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    is_active: bool | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    title: str | None = None


    def to_arango_base_record(self) -> dict[str, Any]:
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
    def from_arango_user(data: dict[str, Any]) -> 'User':
        return User(
            id=data.get("id", data.get("_key")),
            email=data.get("email", ""),
            org_id=data.get("orgId", ""),
            user_id=data.get("userId"),
            is_active=data.get("isActive", False),
            first_name=data.get("firstName"),
            middle_name=data.get("middleName"),
            last_name=data.get("lastName"),
            full_name=data.get("fullName"),
            title=data.get("title"),
        )


class UserGroup(BaseModel):
    source_user_group_id: str
    name: str
    mail: str | None = None
    id: str | None = None
    description: str | None = None
    created_at_timestamp: float | None = None
    updated_at_timestamp: float | None = None
    last_sync_timestamp: float | None = None
    source_created_at_timestamp: float | None = None
    source_last_modified_timestamp: float | None = None

    def to_dict(self) -> dict[str, Any]:
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
    # Salesforce contact fields
    first_name: str | None = Field(default=None, description="First name")
    last_name: str | None = Field(default=None, description="Last name")
    phone: str | None = Field(default=None, description="Phone number")

    def to_arango_person(self) -> dict[str, Any]:
        return {
            "_key": self.id,
            "email": self.email,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
            "firstName": self.first_name,
            "lastName": self.last_name,
            "phone": self.phone,
        }

    @staticmethod
    def from_arango_person(data: dict[str, Any]) -> 'Person':
        return Person(
            id=data.get("_key"),
            email=data.get("email"),
            created_at=data.get("createdAtTimestamp", get_epoch_timestamp_in_ms()),
            updated_at=data.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()),
            first_name=data.get("firstName"),
            last_name=data.get("lastName"),
            phone=data.get("phone"),
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
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the user creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the user update in the source system")
    is_active: bool = Field(default=False, description="Whether the user is active")
    title: str | None = Field(default=None, description="Title of the user")

    def to_arango_base_user(self) -> dict:
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
    def from_arango_user(data: dict[str, Any]) -> 'AppUser':
        return AppUser(
            id=data.get("id", data.get("_key")),
            email=data.get("email", ""),
            org_id=data.get("orgId", ""),
            user_id=data.get("userId"),
            is_active=data.get("isActive", False),
            full_name=data.get("fullName"),
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
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the user group creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the user group update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")
    description: str | None = Field(default=None, description="Description of the user group")

    def to_arango_base_user_group(self) -> dict[str, Any]:
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
    def from_arango_base_user_group(arango_doc: dict[str, Any]) -> "AppUserGroup":
        return AppUserGroup(
            id=arango_doc.get("id", arango_doc.get("_key")),
            org_id=arango_doc.get("orgId", ""),
            name=arango_doc["name"],
            source_user_group_id=arango_doc["externalGroupId"],
            app_name=Connectors(arango_doc["connectorName"]),
            connector_id=arango_doc.get("connectorId"),
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
    source_created_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the role creation in the source system")
    source_updated_at: int | None = Field(default=None, description="Epoch timestamp in milliseconds of the role update in the source system")
    org_id: str = Field(default="", description="Unique identifier for the organization")
    parent_role_id: str | None = Field(default=None, description="ArangoDB ID of the parent role for role hierarchy")

    def to_arango_base_role(self) -> dict[str, Any]:
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
            "parentRoleId": self.parent_role_id if self.parent_role_id else None,
        }

    @staticmethod
    def from_arango_base_role(arango_doc: dict[str, Any]) -> "AppRole":
        return AppRole(
            id=arango_doc.get("id", arango_doc.get("_key")),
            org_id=arango_doc.get("orgId", ""),
            name=arango_doc["name"],
            source_role_id=arango_doc["externalRoleId"],
            app_name=Connectors(arango_doc["connectorName"]),
            connector_id=arango_doc.get("connectorId"),
            created_at=arango_doc["createdAtTimestamp"],
            updated_at=arango_doc["updatedAtTimestamp"],
            source_created_at=arango_doc.get("sourceCreatedAtTimestamp"),
            source_updated_at=arango_doc.get("sourceLastModifiedTimestamp"),
            parent_role_id=arango_doc.get("parentRoleId"),
        )


class AppMetadata(BaseModel):
    """Represents an App/Connector document from the database."""
    connector_id: str = Field(description="Unique identifier for the connector (_key)")
    name: str = Field(description="Name of the app")
    type: str = Field(description="Type of the app")
    app_group: str = Field(description="App group")
    auth_type: str | None = Field(default=None, description="Authentication type")
    scope: str = Field(description="Connector scope (personal/team)")
    is_active: bool = Field(default=True, description="Whether the app is active")
    is_agent_active: bool = Field(default=False, description="Whether the agent is active")
    is_configured: bool = Field(default=False, description="Whether the app is configured")
    is_authenticated: bool = Field(default=False, description="Whether the app is authenticated")
    created_by: str | None = Field(default=None, description="User ID who created the app")
    updated_by: str | None = Field(default=None, description="User ID who last updated the app")
    created_at_timestamp: int = Field(description="Epoch timestamp in milliseconds of app creation")
    updated_at_timestamp: int = Field(description="Epoch timestamp in milliseconds of app update")
    status: str | None = Field(default=None, description="Current sync status")
    is_locked: bool | None = Field(default=None, description="Whether the app is locked")

    @staticmethod
    def from_db_document(doc: dict[str, Any]) -> "AppMetadata":
        """Convert database document to AppMetadata model."""
        return AppMetadata(
            connector_id=doc.get("_key", ""),
            name=doc.get("name", ""),
            type=doc.get("type", ""),
            app_group=doc.get("appGroup", ""),
            auth_type=doc.get("authType"),
            scope=doc.get("scope", "personal"),
            is_active=doc.get("isActive", True),
            is_agent_active=doc.get("isAgentActive", False),
            is_configured=doc.get("isConfigured", False),
            is_authenticated=doc.get("isAuthenticated", False),
            created_by=doc.get("createdBy"),
            updated_by=doc.get("updatedBy"),
            created_at_timestamp=doc.get("createdAtTimestamp", 0),
            updated_at_timestamp=doc.get("updatedAtTimestamp", 0),
            status=doc.get("status"),
            is_locked=doc.get("isLocked"),
        )

class MeetingRecord(Record):
    """Record model for a Zoom meeting synced via user past-meetings report APIs.

    Fields that are already covered by the base Record are intentionally omitted:
    - record_name  → meeting topic
    - external_record_id → meeting UUID (used for live transcript fetch in stream_record)
    - weburl → transcript listing page with #:~:text= fragment for the meeting topic
    - source_created_at / source_updated_at → derived from meeting start_time

    Transcript text is NOT stored; it is fetched live via stream_record().
    """

    host_email: str | None = Field(default=None, description="Email of the meeting host")
    host_id: str | None = Field(default=None, description="Zoom user ID of the host")
    meeting_type: int | None = Field(
        default=None,
        description="Zoom meeting type code (e.g. 1=instant, 2=scheduled, 3/8=recurring)",
    )
    duration_minutes: int | None = Field(default=None, description="Meeting duration in minutes")
    start_time: str | None = Field(default=None, description="Meeting start time ISO-8601 UTC")
    end_time: str | None = Field(default=None, description="Meeting end time ISO-8601 UTC")
    timezone: str | None = Field(default=None, description="Timezone reported by Zoom")
    recording_url: str | None = Field(
        default=None,
        description="Cloud recording share URL (https://zoom.us/rec/share/...). "
                    "Available only when cloud recording exists for the meeting.",
    )

    def to_llm_context(self, frontend_url: str | None = None) -> str:
        base = super().to_llm_context(frontend_url=frontend_url)
        lines = [base]
        specific_lines = []
        if self.host_email:
            specific_lines.append(f"* Host: {self.host_email}")
        if self.start_time:
            specific_lines.append(f"* Start Time: {self.start_time}")
        if self.end_time:
            specific_lines.append(f"* End Time: {self.end_time}")
        if self.duration_minutes is not None:
            specific_lines.append(f"* Duration: {self.duration_minutes} minutes")
        if self.recording_url:
            specific_lines.append(f"* Recording: {self.recording_url}")
        if specific_lines:
            lines.append("Meeting Information:")
            lines.extend(specific_lines)
        return "\n".join(lines)

    def to_arango_record(self) -> dict:
        return {
            "_key": self.id,
            "hostEmail": self.host_email,
            "hostId": self.host_id,
            "meetingType": self.meeting_type,
            "durationMinutes": self.duration_minutes,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "timezone": self.timezone,
            "recordingUrl": self.recording_url,
        }

    @staticmethod
    def from_arango_record(meeting_doc: dict, record_doc: dict) -> "MeetingRecord":
        """Create MeetingRecord from ArangoDB documents (records + meetings collections)"""
        conn_name_value = record_doc.get("connectorName")
        try:
            connector_name = Connectors(conn_name_value) if conn_name_value else Connectors.KNOWLEDGE_BASE
        except ValueError:
            connector_name = Connectors.KNOWLEDGE_BASE

        return MeetingRecord(
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
            host_email=meeting_doc.get("hostEmail"),
            host_id=meeting_doc.get("hostId"),
            meeting_type=meeting_doc.get("meetingType"),
            duration_minutes=meeting_doc.get("durationMinutes"),
            start_time=meeting_doc.get("startTime"),
            end_time=meeting_doc.get("endTime"),
            timezone=meeting_doc.get("timezone"),
            recording_url=meeting_doc.get("recordingUrl"),
        )

    def to_kafka_record(self) -> dict:
        return {
            "recordId": self.id,
            "orgId": self.org_id,
            "recordName": self.record_name,
            "recordType": self.record_type.value,
            "externalRecordId": self.external_record_id,
            "externalRevisionId": self.external_revision_id,
            "externalGroupId": self.external_record_group_id,
            "recordGroupId": self.record_group_id,
            "virtualRecordId": self.virtual_record_id,
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
            "hostEmail": self.host_email,
            "hostId": self.host_id,
            "meetingType": self.meeting_type,
            "durationMinutes": self.duration_minutes,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "timezone": self.timezone,
            "recordingUrl": self.recording_url,
        }


# Rebuild models to resolve forward references after all imports are complete
# Call rebuild function after all models are defined to avoid circular import issues
rebuild_all_models()
