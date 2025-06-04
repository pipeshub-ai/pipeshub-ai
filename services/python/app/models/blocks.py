from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class CommentFormat(str, Enum):
    TXT = "txt"
    BIN = "bin"
    MARKDOWN = "markdown"

class BlockType(str, Enum):
    PARAGRAPH = "paragraph"
    TEXTSECTION = "textsection"
    TABLE = "table"
    FILE = "file"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    LINK = "link"
    CODE = "code"
    BULLET_LIST = "bullet_list"
    NUMBERED_LIST = "numbered_list"
    HEADING = "heading"
    QUOTE = "quote"
    DIVIDER = "divider"


class TextFormat(str, Enum):
    TXT = "txt"
    BIN = "bin"
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    XML = "xml"
    CSV = "csv"
    YAML = "yaml"
    BASE64 = "base64"


class BlockComment(BaseModel):
    text: str
    format: TextFormat
    threadId: Optional[str] = None
    attachementRecordIds: Optional[List[str]] = None

class CitationMetadata(BaseModel):
    """Citation-specific metadata for referencing source locations"""
    page_number: Optional[int] = None
    sheet_number: Optional[int] = None
    sheet_name: Optional[str] = None
    row_number: Optional[int] = None
    column_number: Optional[int] = None
    cell_reference: Optional[str] = None  # e.g., "A1", "B5"
    line_number: Optional[int] = None
    paragraph_number: Optional[int] = None
    section_title: Optional[str] = None
    timestamp: Optional[str] = None  # For video/audio content
    slide_number: Optional[int] = None
    bounding_boxes: Optional[List[int]] = None

class TableMetadata(BaseModel):
    """Metadata specific to table blocks"""
    rows: Optional[int] = None
    columns: Optional[int] = None
    has_header: bool = False
    column_names: Optional[List[str]] = None
    table_caption: Optional[str] = None

class CodeMetadata(BaseModel):
    """Metadata specific to code blocks"""
    language: Optional[str] = None
    execution_context: Optional[str] = None
    is_executable: bool = False
    dependencies: Optional[List[str]] = None

class MediaMetadata(BaseModel):
    """Metadata for media blocks (image, video, audio)"""
    duration_ms: Optional[int] = None  # For video/audio
    dimensions: Optional[Dict[str, int]] = None  # {"width": 1920, "height": 1080}
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    alt_text: Optional[str] = None
    transcription: Optional[str] = None  # For audio/video

class ListMetadata(BaseModel):
    """Metadata specific to list blocks"""
    list_style: Optional[Literal["bullet", "numbered", "checkbox", "dash"]] = None
    indent_level: int = 0
    parent_list_id: Optional[str] = None
    item_count: Optional[int] = None

class FileMetadata(BaseModel):
    """Metadata specific to file blocks"""
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    file_extension: Optional[str] = None
    file_path: Optional[str] = None

class LinkMetadata(BaseModel):
    """Metadata specific to link blocks"""
    link_text: Optional[str] = None
    link_url: Optional[HttpUrl] = None
    link_type: Optional[Literal["internal", "external"]] = None
    link_target: Optional[str] = None

class Confidence(str, Enum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Block(BaseModel):
    # Core block properties
    block_id: str
    block_type: BlockType
    block_name: Optional[str] = None
    block_format: TextFormat
    block_comments: List[BlockComment] = Field(default_factory=list)
    block_source_creation_date: Optional[datetime] = None
    block_source_update_date: Optional[datetime] = None
    block_source_id: Optional[str] = None
    block_source_name: Optional[str] = None
    block_source_type: Optional[str] = None


    # Content and links
    data: Optional[Any] = None
    links: Optional[List[str]] = None
    weburl: Optional[HttpUrl] = None
    public_data_link: Optional[HttpUrl] = None
    public_data_link_expiration_epoch_time_in_ms: Optional[int] = None

    # Block-type specific metadata
    list_metadata: Optional[ListMetadata] = None
    table_metadata: Optional[TableMetadata] = None
    code_metadata: Optional[CodeMetadata] = None
    media_metadata: Optional[MediaMetadata] = None
    file_metadata: Optional[FileMetadata] = None
    link_metadata: Optional[LinkMetadata] = None
    citation_metadata: Optional[CitationMetadata] = None

    # Semantic metadata
    entities: Optional[List[Dict[str, Any]]] = None
    sectionNumbers: Optional[List[str]] = None
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None
    departments: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    recordId: Optional[str] = None
    page_number: Optional[int] = None
    sheet_number: Optional[int] = None
    category: Optional[str] = None
    sub_category_level_1: Optional[str] = None
    sub_category_level_2: Optional[str] = None
    sub_category_level_3: Optional[str] = None

    confidence: Confidence = Confidence.VERY_HIGH

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
    MANUAL_SYNC = "AUTO_INDEX_OFF"

class Record(BaseModel):
    # Core record properties
    record_id: str = Field(description="Unique identifier for the record")
    org_id: str = Field(description="Unique identifier for the organization")
    record_name: str = Field(description="Human-readable name for the record")
    record_type: RecordType = Field(description="Type/category of the record")
    record_status: RecordStatus = Field(default=RecordStatus.PROCESSING)
    external_record_id: str = Field(description="Unique identifier for the record in the external system")
    external_revision_id: Optional[str] = Field(description="Unique identifier for the revision of the record in the external system")
    version: int = Field(description="Version of the record")
    origin: str = Field(description="Origin of the record")
    connector_name: Optional[str] = Field(description="Name of the connector used to create the record")
    virtualRecordId: Optional[str] = Field(description="Virtual record identifier")
    summaryDocumentId: Optional[str] = Field(description="Summary document identifier")
    md5_hash: Optional[str] = Field(description="MD5 hash of the record")
    # Timestamps
    created_at: int = Field()
    updated_at: int = Field()
    source_created_at: Optional[int] = None
    source_updated_at: Optional[int] = None

    # Source information
    weburl: Optional[HttpUrl] = None


    # Content blocks
    blocks: List[Block] = Field(default_factory=list, description="List of content blocks in this record")

    # Semantic information at record level
    title: Optional[str] = None
    description: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    categories: Optional[List[str]] = Field(default_factory=list)
    keywords: Optional[List[str]] = Field(default_factory=list)
    entities: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    topics: Optional[List[str]] = Field(default_factory=list)
    departments: Optional[List[str]] = Field(default_factory=list)
    languages: Optional[List[str]] = Field(default_factory=list)
    category: Optional[str] = None
    sub_category_level_1: Optional[str] = None
    sub_category_level_2: Optional[str] = None
    sub_category_level_3: Optional[str] = None


    # Relationships
    parent_record_id: Optional[str] = None
    child_record_ids: Optional[List[str]] = Field(default_factory=list)
    related_record_ids: Optional[List[str]] = Field(default_factory=list)

class FileRecord(Record):
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    file_extension: Optional[str] = None
    file_path: Optional[str] = None

class MessageRecord(Record):
    message_id: Optional[str] = None
    message_subject: Optional[str] = None

class MailRecord(Record):
    mail_id: Optional[str] = None
    mail_subject: Optional[str] = None
    mail_from: Optional[str] = None
    mail_to: Optional[str] = None
    mail_cc: Optional[str] = None
    mail_bcc: Optional[str] = None


class WebpageRecord(Record):
    webpage_url: Optional[HttpUrl] = None
    webpage_title: Optional[str] = None
    webpage_description: Optional[str] = None

