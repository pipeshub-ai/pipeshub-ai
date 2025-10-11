from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Point(BaseModel):
    x: float
    y: float

class CommentFormat(str, Enum):
    TXT = "txt"
    BIN = "bin"
    MARKDOWN = "markdown"
    HTML = "html"

class BlockType(str, Enum):
    TEXT = "text"
    PARAGRAPH = "paragraph"
    TEXTSECTION = "textsection"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
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

class DataFormat(str, Enum):
    TXT = "txt"
    BIN = "bin"
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    XML = "xml"
    CSV = "csv"
    YAML = "yaml"
    BASE64 = "base64"
    UTF8 = "utf8"

class BlockComment(BaseModel):
    text: str
    format: DataFormat
    thread_id: Optional[str] = None
    attachment_record_ids: Optional[List[str]] = None

class CitationMetadata(BaseModel):
    """Citation-specific metadata for referencing source locations"""
    # All File formatsspecific
    section_title: Optional[str] = None

    # PDF specific
    page_number: Optional[int] = None
    has_more_pages: Optional[bool] = None
    more_page_numbers: Optional[List[int]] = None
    bounding_boxes: Optional[List[Point]] = None
    more_page_bounding_boxes: Optional[List[List[Point]]] = None

    # PDF/Word/Text specific
    line_number: Optional[int] = None
    paragraph_number: Optional[int] = None

    # Excel specific
    sheet_number: Optional[int] = None
    sheet_name: Optional[str] = None
    cell_reference: Optional[str] = None  # e.g., "A1", "B5"

    # Excel/CSV specific
    row_number: Optional[int] = None
    column_number: Optional[int] = None

    # Slide specific
    slide_number: Optional[int] = None

    # Video/Audio specific
    start_timestamp: Optional[str] = None  # For video/audio content
    end_timestamp: Optional[str] = None  # For video/audio content
    duration_ms: Optional[int] = None  # For video/audio

    @field_validator('bounding_boxes')
    @classmethod
    def validate_bounding_boxes(cls, v: List[Point]) -> List[Point]:
        """Validate that the bounding boxes contain exactly 4 points"""
        COORDINATE_COUNT = 4
        if len(v) != COORDINATE_COUNT:
            raise ValueError(f'bounding_boxes must contain exactly {COORDINATE_COUNT} points')
        return v

class TableCellMetadata(BaseModel):
    """Metadata specific to table cell blocks"""
    row_number: Optional[int] = None
    column_number: Optional[int] = None
    row_span: Optional[int] = None
    column_span: Optional[int] = None
    column_header: Optional[bool] = None
    row_header: Optional[bool] = None

class TableRowMetadata(BaseModel):
    """Metadata specific to table row blocks"""
    row_number: Optional[int] = None
    row_span: Optional[int] = None
    is_header: bool = False

class TableMetadata(BaseModel):
    """Metadata specific to table blocks"""
    num_of_rows: Optional[int] = None
    num_of_cols: Optional[int] = None
    has_header: bool = False
    column_names: Optional[List[str]] = None
    captions: Optional[List[str]] = Field(default_factory=list)
    footnotes: Optional[List[str]] = Field(default_factory=list)

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

class ImageMetadata(BaseModel):
    """Metadata specific to image blocks"""
    image_type: Optional[Literal["image", "drawing"]] = None
    image_format: Optional[str] = None
    image_size: Optional[Dict[str, int]] = None
    image_resolution: Optional[Dict[str, int]] = None
    image_dpi: Optional[int] = None
    captions: Optional[List[str]] = Field(default_factory=list)
    footnotes: Optional[List[str]] = Field(default_factory=list)
    annotations: Optional[List[str]] = Field(default_factory=list)

class Confidence(str, Enum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class GroupType(str, Enum):
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    MEDIA = "media"
    SHEET = "sheet"
    FORM_AREA = "form_area"
    INLINE = "inline"
    KEY_VALUE_AREA = "key_value_area"
    ORDERED_LIST = "ordered_list"

class SemanticMetadata(BaseModel):
    entities: Optional[List[Dict[str, Any]]] = None
    section_numbers: Optional[List[str]] = None
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None
    departments: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    record_id: Optional[str] = None
    categories: Optional[List[str]] = Field(default_factory=list)
    sub_category_level_1: Optional[str] = None
    sub_category_level_2: Optional[str] = None
    sub_category_level_3: Optional[str] = None
    confidence: Optional[Confidence] = None

class Block(BaseModel):
    # Core block properties
    id: str = Field(default_factory=lambda: str(uuid4()))
    index: int = None
    parent_index: Optional[int] = Field(default=None, description="Index of the parent block group")
    type: BlockType
    name: Optional[str] = None
    format: DataFormat = None
    comments: List[BlockComment] = Field(default_factory=list)
    source_creation_date: Optional[datetime] = None
    source_update_date: Optional[datetime] = None
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    # Content and links
    data: Optional[Any] = None
    links: Optional[List[str]] = None
    weburl: Optional[HttpUrl] = None
    public_data_link: Optional[HttpUrl] = None
    public_data_link_expiration_epoch_time_in_ms: Optional[int] = None
    citation_metadata: Optional[CitationMetadata] = None
    list_metadata: Optional[ListMetadata] = None
    table_row_metadata: Optional[TableRowMetadata] = None
    table_cell_metadata: Optional[TableCellMetadata] = None
    code_metadata: Optional[CodeMetadata] = None
    media_metadata: Optional[MediaMetadata] = None
    file_metadata: Optional[FileMetadata] = None
    link_metadata: Optional[LinkMetadata] = None
    image_metadata: Optional[ImageMetadata] = None
    semantic_metadata: Optional[SemanticMetadata] = None

class Blocks(BaseModel):
    blocks: List[Block] = Field(default_factory=list)

class BlockContainerIndex(BaseModel):
    block_index: Optional[int] = None
    block_group_index: Optional[int] = None

class BlockGroup(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    index: int = None
    name: Optional[str] = Field(description="Name of the block group",default=None)
    type: GroupType = Field(description="Type of the block group")
    parent_index: Optional[int] = Field(description="Index of the parent block group",default=None)
    description: Optional[str] = Field(description="Description of the block group",default=None)
    source_group_id: Optional[str] = Field(description="Source group identifier",default=None)
    citation_metadata: Optional[CitationMetadata] = None
    list_metadata: Optional[ListMetadata] = None
    table_metadata: Optional[TableMetadata] = None
    table_row_metadata: Optional[TableRowMetadata] = None
    table_cell_metadata: Optional[TableCellMetadata] = None
    code_metadata: Optional[CodeMetadata] = None
    media_metadata: Optional[MediaMetadata] = None
    file_metadata: Optional[FileMetadata] = None
    link_metadata: Optional[LinkMetadata] = None
    semantic_metadata: Optional[SemanticMetadata] = None
    children: Optional[List[BlockContainerIndex]] = None
    data: Optional[Any] = None
    format: Optional[DataFormat] = None

class BlockGroups(BaseModel):
    block_groups: List[BlockGroup] = Field(default_factory=list)

class BlocksContainer(BaseModel):
    block_groups: List[BlockGroup] = Field(default_factory=list)
    blocks: List[Block] = Field(default_factory=list)

