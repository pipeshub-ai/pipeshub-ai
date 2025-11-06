from datetime import datetime
from enum import Enum
from typing import Any, Literal
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
    thread_id: str | None = None
    attachment_record_ids: list[str] | None = None

class CitationMetadata(BaseModel):
    """Citation-specific metadata for referencing source locations"""

    # All File formatsspecific
    section_title: str | None = None

    # PDF specific
    page_number: int | None = None
    has_more_pages: bool | None = None
    more_page_numbers: list[int] | None = None
    bounding_boxes: list[Point] | None = None
    more_page_bounding_boxes: list[list[Point]] | None = None

    # PDF/Word/Text specific
    line_number: int | None = None
    paragraph_number: int | None = None

    # Excel specific
    sheet_number: int | None = None
    sheet_name: str | None = None
    cell_reference: str | None = None  # e.g., "A1", "B5"

    # Excel/CSV specific
    row_number: int | None = None
    column_number: int | None = None

    # Slide specific
    slide_number: int | None = None

    # Video/Audio specific
    start_timestamp: str | None = None  # For video/audio content
    end_timestamp: str | None = None  # For video/audio content
    duration_ms: int | None = None  # For video/audio

    @field_validator("bounding_boxes")
    @classmethod
    def validate_bounding_boxes(cls, v: list[Point]) -> list[Point]:
        """Validate that the bounding boxes contain exactly 4 points"""
        COORDINATE_COUNT = 4
        if len(v) != COORDINATE_COUNT:
            raise ValueError(f"bounding_boxes must contain exactly {COORDINATE_COUNT} points")
        return v

class TableCellMetadata(BaseModel):
    """Metadata specific to table cell blocks"""

    row_number: int | None = None
    column_number: int | None = None
    row_span: int | None = None
    column_span: int | None = None
    column_header: bool | None = None
    row_header: bool | None = None

class TableRowMetadata(BaseModel):
    """Metadata specific to table row blocks"""

    row_number: int | None = None
    row_span: int | None = None
    is_header: bool = False

class TableMetadata(BaseModel):
    """Metadata specific to table blocks"""

    num_of_rows: int | None = None
    num_of_cols: int | None = None
    has_header: bool = False
    column_names: list[str] | None = None
    captions: list[str] | None = Field(default_factory=list)
    footnotes: list[str] | None = Field(default_factory=list)

class CodeMetadata(BaseModel):
    """Metadata specific to code blocks"""

    language: str | None = None
    execution_context: str | None = None
    is_executable: bool = False
    dependencies: list[str] | None = None

class MediaMetadata(BaseModel):
    """Metadata for media blocks (image, video, audio)"""

    duration_ms: int | None = None  # For video/audio
    dimensions: dict[str, int] | None = None  # {"width": 1920, "height": 1080}
    file_size_bytes: int | None = None
    mime_type: str | None = None
    alt_text: str | None = None
    transcription: str | None = None  # For audio/video

class ListMetadata(BaseModel):
    """Metadata specific to list blocks"""

    list_style: Literal["bullet", "numbered", "checkbox", "dash"] | None = None
    indent_level: int = 0
    parent_list_id: str | None = None
    item_count: int | None = None

class FileMetadata(BaseModel):
    """Metadata specific to file blocks"""

    file_name: str | None = None
    file_size_bytes: int | None = None
    mime_type: str | None = None
    file_extension: str | None = None
    file_path: str | None = None

class LinkMetadata(BaseModel):
    """Metadata specific to link blocks"""

    link_text: str | None = None
    link_url: HttpUrl | None = None
    link_type: Literal["internal", "external"] | None = None
    link_target: str | None = None

class ImageMetadata(BaseModel):
    """Metadata specific to image blocks"""

    image_type: Literal["image", "drawing"] | None = None
    image_format: str | None = None
    image_size: dict[str, int] | None = None
    image_resolution: dict[str, int] | None = None
    image_dpi: int | None = None
    captions: list[str] | None = Field(default_factory=list)
    footnotes: list[str] | None = Field(default_factory=list)
    annotations: list[str] | None = Field(default_factory=list)

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
    entities: list[dict[str, Any]] | None = None
    section_numbers: list[str] | None = None
    summary: str | None = None
    keywords: list[str] | None = None
    departments: list[str] | None = None
    languages: list[str] | None = None
    topics: list[str] | None = None
    record_id: str | None = None
    categories: list[str] | None = Field(default_factory=list)
    sub_category_level_1: str | None = None
    sub_category_level_2: str | None = None
    sub_category_level_3: str | None = None
    confidence: Confidence | None = None

class Block(BaseModel):
    # Core block properties
    id: str = Field(default_factory=lambda: str(uuid4()))
    index: int = None
    parent_index: int | None = Field(default=None, description="Index of the parent block group")
    type: BlockType
    name: str | None = None
    format: DataFormat = None
    comments: list[BlockComment] = Field(default_factory=list)
    source_creation_date: datetime | None = None
    source_update_date: datetime | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    # Content and links
    data: Any | None = None
    links: list[str] | None = None
    weburl: HttpUrl | None = None
    public_data_link: HttpUrl | None = None
    public_data_link_expiration_epoch_time_in_ms: int | None = None
    citation_metadata: CitationMetadata | None = None
    list_metadata: ListMetadata | None = None
    table_row_metadata: TableRowMetadata | None = None
    table_cell_metadata: TableCellMetadata | None = None
    code_metadata: CodeMetadata | None = None
    media_metadata: MediaMetadata | None = None
    file_metadata: FileMetadata | None = None
    link_metadata: LinkMetadata | None = None
    image_metadata: ImageMetadata | None = None
    semantic_metadata: SemanticMetadata | None = None

class Blocks(BaseModel):
    blocks: list[Block] = Field(default_factory=list)

class BlockContainerIndex(BaseModel):
    block_index: int | None = None
    block_group_index: int | None = None

class BlockGroup(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    index: int = None
    name: str | None = Field(description="Name of the block group",default=None)
    type: GroupType = Field(description="Type of the block group")
    parent_index: int | None = Field(description="Index of the parent block group",default=None)
    description: str | None = Field(description="Description of the block group",default=None)
    source_group_id: str | None = Field(description="Source group identifier",default=None)
    citation_metadata: CitationMetadata | None = None
    list_metadata: ListMetadata | None = None
    table_metadata: TableMetadata | None = None
    table_row_metadata: TableRowMetadata | None = None
    table_cell_metadata: TableCellMetadata | None = None
    code_metadata: CodeMetadata | None = None
    media_metadata: MediaMetadata | None = None
    file_metadata: FileMetadata | None = None
    link_metadata: LinkMetadata | None = None
    semantic_metadata: SemanticMetadata | None = None
    children: list[BlockContainerIndex] | None = None
    data: Any | None = None
    format: DataFormat | None = None

class BlockGroups(BaseModel):
    block_groups: list[BlockGroup] = Field(default_factory=list)

class BlocksContainer(BaseModel):
    block_groups: list[BlockGroup] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)

