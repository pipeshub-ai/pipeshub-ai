from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator

if TYPE_CHECKING:
    pass

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
    IMAGE = "image"
    TABLE_ROW = "table_row"

    # Do not use these types as currently not supported
    PARAGRAPH = "paragraph"
    TEXTSECTION = "textsection"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    FILE = "file"
    VIDEO = "video"
    AUDIO = "audio"
    LINK = "link"
    CODE = "code"
    BULLET_LIST = "bullet_list"
    NUMBERED_LIST = "numbered_list"
    HEADING = "heading"
    QUOTE = "quote"
    DIVIDER = "divider"

class BlockSubType(str, Enum):
    CHILD_RECORD = "child_record"
    COMMENT = "comment"
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    QUOTE = "quote"
    LIST_ITEM = "list_item"
    CODE = "code"
    EQUATION = "equation"
    DIVIDER = "divider"
    LINK = "link"
    COMMIT = "commit"

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
    PATCH = "patch"
    DIFF = "diff"
    CODE = "code"

class CommentAttachment(BaseModel):
    """Attachment model for comments"""
    name: str = Field(description="Name of the attachment")
    id: str = Field(description="ID of the attachment")

class BlockComment(BaseModel):
    text: str
    format: DataFormat
    author_id: Optional[str] = Field(default=None, description="ID of the user who created the comment")
    author_name: Optional[str] = Field(default=None, description="Name of the user who created the comment")
    thread_id: Optional[str] = None
    resolution_status: Optional[str] = Field(default=None, description="Status of the comment (e.g., 'resolved', 'open')")
    weburl: Optional[HttpUrl] = Field(default=None, description="Web URL for the comment (e.g., direct link to comment in the source system)")
    created_at: Optional[datetime] = Field(default=None, description="Timestamp when the comment was created")
    updated_at: Optional[datetime] = Field(default=None, description="Timestamp when the comment was updated")
    attachments: Optional[List[CommentAttachment]] = Field(default=None, description="List of attachments associated with the comment")
    quoted_text: Optional[str] = Field(default=None, description="Quoted text for inline comments")

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

class ChildType(str, Enum):
    """Type of child reference"""
    RECORD = "record"
    USER = "user"

class ChildRecord(BaseModel):
    """Metadata for child references (records, users, or other types)"""
    child_type: ChildType = Field(description="Type of child: 'record', 'user', etc.")
    child_id: str = Field(description="ID of the child (ArangoDB record ID, user ID, etc.)")
    child_name: Optional[str] = Field(default=None, description="Name/title of the child")

class TableRowMetadata(BaseModel):
    """Metadata specific to table row blocks"""
    row_number: Optional[int] = None
    row_span: Optional[int] = None
    is_header: bool = False
    children_records: Optional[List[ChildRecord]] = None

class TableMetadata(BaseModel):
    """Metadata specific to table blocks"""
    num_of_rows: Optional[int] = None
    num_of_cols: Optional[int] = None
    num_of_cells: Optional[int] = None
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
    TEXT_SECTION = "text_section"
    LIST = "list"
    TABLE = "table"
    COMMITS = "commits"
    PATCH = "patch"
    SHEET = "sheet"
    FORM_AREA = "form_area"
    INLINE = "inline"
    KEY_VALUE_AREA = "key_value_area"
    ORDERED_LIST = "ordered_list"
    COLUMN = "column"
    COLUMN_LIST = "column_list"

    # Do not use these types as currently not supported
    CODE = "code"
    MEDIA = "media"
    FULL_CODE_PATCH = "full_code_patch"

class GroupSubType(str, Enum):
    MILESTONE = "milestone" # Milestone block group
    UPDATE = "update" # Update block group
    CHILD_RECORD = "child_record" # Child record reference block group
    CONTENT = "content" # Content block group
    RECORD = "record" # Record block group
    COMMENT_THREAD = "comment_thread" # Comment thread block group (used for comments in a thread)
    COMMENT = "comment" # Comment block group
    PROJECT_CONTENT = "project_content"
    ISSUE_CONTENT = "issue_content"
    TOGGLE = "toggle"
    CALLOUT = "callout"
    QUOTE = "quote"
    SYNCED_BLOCK = "synced_block"
    NESTED_BLOCK = "nested_block"  # Generic wrapper for blocks with children
    PR_FILE_CHANGE = "pr_file_change"

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

    def to_llm_context(self) -> List[str]:
        lines = []
        if self.summary:
            lines.append(f"Summary         : {self.summary}")
        if self.topics:
            lines.append(f"Topics          : {self.topics}")
        if self.categories:
            lines.append(f"Category        : {self.categories[0]}")
        if self.sub_category_level_1:
            lines.append(f"Sub-categories  :\n  - Level 1: {self.sub_category_level_1}")
        if self.sub_category_level_2:
            lines.append(f"  - Level 2: {self.sub_category_level_2}")
        if self.sub_category_level_3:
            lines.append(f"  - Level 3: {self.sub_category_level_3}")

        return lines

class Block(BaseModel):
    # Core block properties
    id: str = Field(default_factory=lambda: str(uuid4()))
    index: int = None
    parent_index: Optional[int] = Field(default=None, description="Index of the parent block group")
    type: BlockType
    sub_type: Optional[BlockSubType] = None
    name: Optional[str] = None
    format: DataFormat = None
    comments: List[List[BlockComment]] = Field(default_factory=list, description="2D list of comments grouped by thread_id, with each thread's comments in API order")
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
    children_records: Optional[List[ChildRecord]] = Field(default=None, description="List of child records associated with this block")

class Blocks(BaseModel):
    blocks: List[Block] = Field(default_factory=list)

class BlockContainerIndex(BaseModel):
    """Legacy model for backward compatibility - use BlockGroupChildren instead"""
    block_index: Optional[int] = None
    block_group_index: Optional[int] = None

class IndexRange(BaseModel):
    """Represents a range of indices (inclusive)"""
    start: int = Field(description="Starting index (inclusive)")
    end: int = Field(description="Ending index (inclusive)")

class BlockGroupChildren(BaseModel):
    """Container for child block and block group references using ranges"""
    block_ranges: List[IndexRange] = Field(default_factory=list, description="Ranges of block indices")
    block_group_ranges: List[IndexRange] = Field(default_factory=list, description="Ranges of block group indices")

    def add_block_index(self, index: int) -> None:
        """Add a block index, merging into existing ranges if contiguous"""
        if not self.block_ranges:
            self.block_ranges.append(IndexRange(start=index, end=index))
            return

        # Try to merge with existing ranges
        for range_obj in self.block_ranges:
            if index == range_obj.end + 1:
                range_obj.end = index
                return
            elif index == range_obj.start - 1:
                range_obj.start = index
                return
            elif range_obj.start <= index <= range_obj.end:
                # Already in range
                return

        # Add new range and sort
        self.block_ranges.append(IndexRange(start=index, end=index))
        self.block_ranges.sort(key=lambda r: r.start)

    def add_block_group_index(self, index: int) -> None:
        """Add a block group index, merging into existing ranges if contiguous"""
        if not self.block_group_ranges:
            self.block_group_ranges.append(IndexRange(start=index, end=index))
            return

        # Try to merge with existing ranges
        for range_obj in self.block_group_ranges:
            if index == range_obj.end + 1:
                range_obj.end = index
                return
            elif index == range_obj.start - 1:
                range_obj.start = index
                return
            elif range_obj.start <= index <= range_obj.end:
                # Already in range
                return

        # Add new range and sort
        self.block_group_ranges.append(IndexRange(start=index, end=index))
        self.block_group_ranges.sort(key=lambda r: r.start)

    @staticmethod
    def from_indices(block_indices: Optional[List[int]] = None,
                     block_group_indices: Optional[List[int]] = None) -> 'BlockGroupChildren':
        """Create BlockGroupChildren from lists of indices by grouping into ranges"""
        def indices_to_ranges(indices: List[int]) -> List[IndexRange]:
            if not indices:
                return []

            # Sort and remove duplicates
            sorted_indices = sorted(set(indices))
            ranges = []
            start = sorted_indices[0]
            end = sorted_indices[0]

            for i in range(1, len(sorted_indices)):
                if sorted_indices[i] == end + 1:
                    # Contiguous, extend current range
                    end = sorted_indices[i]
                else:
                    # Gap found, save current range and start new one
                    ranges.append(IndexRange(start=start, end=end))
                    start = sorted_indices[i]
                    end = sorted_indices[i]

            # Add the last range
            ranges.append(IndexRange(start=start, end=end))
            return ranges

        block_ranges = indices_to_ranges(block_indices or [])
        block_group_ranges = indices_to_ranges(block_group_indices or [])

        return BlockGroupChildren(
            block_ranges=block_ranges,
            block_group_ranges=block_group_ranges
        )

class BlockGroup(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    index: int = None
    name: Optional[str] = Field(description="Name of the block group",default=None)
    type: GroupType = Field(description="Type of the block group")
    sub_type: Optional[GroupSubType] = Field(default=None, description="Subtype of the block group (e.g., milestone, update, content)")
    parent_index: Optional[int] = Field(description="Index of the parent block group",default=None)
    description: Optional[str] = Field(description="Description of the block group",default=None)
    source_group_id: Optional[str] = Field(description="Source group identifier",default=None)
    requires_processing : bool = Field(default=False, description="Indicates if further processing is needed for this block group")
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
    children_records: Optional[List[ChildRecord]] = Field(default=None, description="List of child records associated with this block group")
    children: Optional[BlockGroupChildren] = None
    data: Optional[Any] = None

    @field_validator('children', mode='before')
    @classmethod
    def convert_children_format(cls, v:Optional[BlockGroupChildren|dict|list]) -> Optional[BlockGroupChildren|dict|list]:
        """Convert old List[BlockContainerIndex] format to new BlockGroupChildren format"""
        if v is None:
            return None

        # If it's already a BlockGroupChildren instance or dict with the new format, return as-is
        if isinstance(v, BlockGroupChildren):
            return v
        if isinstance(v, dict) and ('block_ranges' in v or 'block_group_ranges' in v):
            return v

        # If it's a list, it's the old format (List[BlockContainerIndex])
        if isinstance(v, list):
            block_indices = []
            block_group_indices = []

            for item in v:
                if isinstance(item, dict):
                    if item.get('block_index') is not None:
                        block_indices.append(item['block_index'])
                    if item.get('block_group_index') is not None:
                        block_group_indices.append(item['block_group_index'])
                elif hasattr(item, 'block_index') or hasattr(item, 'block_group_index'):
                    if hasattr(item, 'block_index') and item.block_index is not None:
                        block_indices.append(item.block_index)
                    if hasattr(item, 'block_group_index') and item.block_group_index is not None:
                        block_group_indices.append(item.block_group_index)

            return BlockGroupChildren.from_indices(block_indices, block_group_indices)

        return v
    format: Optional[DataFormat] = None
    weburl: Optional[HttpUrl] = Field(default=None, description="Web URL for the original source context (e.g., Linear project page). This will be used as primary webUrl in citations for all generated blocks")
    comments: List[List[BlockComment]] = Field(default_factory=list, description="2D list of comments grouped by thread_id, with each thread's comments sorted by created_at")
    source_modified_date: Optional[datetime] = None

class BlockGroups(BaseModel):
    block_groups: List[BlockGroup] = Field(default_factory=list)

class BlocksContainer(BaseModel):
    block_groups: List[BlockGroup] = Field(default_factory=list)
    blocks: List[Block] = Field(default_factory=list)
