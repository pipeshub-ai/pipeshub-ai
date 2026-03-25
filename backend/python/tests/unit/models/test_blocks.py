"""Tests for blocks module: enums, Block, BlockGroup, BlockGroupChildren, SemanticMetadata, CitationMetadata."""

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlockSubType,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
    IndexRange,
    Point,
    SemanticMetadata,
)


# ============================================================================
# Enum value tests
# ============================================================================


class TestBlockType:
    def test_text(self):
        assert BlockType.TEXT.value == "text"

    def test_image(self):
        assert BlockType.IMAGE.value == "image"

    def test_table_row(self):
        assert BlockType.TABLE_ROW.value == "table_row"

    def test_paragraph(self):
        assert BlockType.PARAGRAPH.value == "paragraph"

    def test_textsection(self):
        assert BlockType.TEXTSECTION.value == "textsection"

    def test_table(self):
        assert BlockType.TABLE.value == "table"

    def test_table_cell(self):
        assert BlockType.TABLE_CELL.value == "table_cell"

    def test_file(self):
        assert BlockType.FILE.value == "file"

    def test_video(self):
        assert BlockType.VIDEO.value == "video"

    def test_audio(self):
        assert BlockType.AUDIO.value == "audio"

    def test_link(self):
        assert BlockType.LINK.value == "link"

    def test_code(self):
        assert BlockType.CODE.value == "code"

    def test_bullet_list(self):
        assert BlockType.BULLET_LIST.value == "bullet_list"

    def test_numbered_list(self):
        assert BlockType.NUMBERED_LIST.value == "numbered_list"

    def test_heading(self):
        assert BlockType.HEADING.value == "heading"

    def test_quote(self):
        assert BlockType.QUOTE.value == "quote"

    def test_divider(self):
        assert BlockType.DIVIDER.value == "divider"

    def test_is_str_enum(self):
        assert isinstance(BlockType.TEXT, str)


class TestGroupType:
    def test_text_section(self):
        assert GroupType.TEXT_SECTION.value == "text_section"

    def test_list(self):
        assert GroupType.LIST.value == "list"

    def test_table(self):
        assert GroupType.TABLE.value == "table"

    def test_commits(self):
        assert GroupType.COMMITS.value == "commits"

    def test_patch(self):
        assert GroupType.PATCH.value == "patch"

    def test_sheet(self):
        assert GroupType.SHEET.value == "sheet"

    def test_form_area(self):
        assert GroupType.FORM_AREA.value == "form_area"

    def test_inline(self):
        assert GroupType.INLINE.value == "inline"

    def test_key_value_area(self):
        assert GroupType.KEY_VALUE_AREA.value == "key_value_area"

    def test_ordered_list(self):
        assert GroupType.ORDERED_LIST.value == "ordered_list"

    def test_column(self):
        assert GroupType.COLUMN.value == "column"

    def test_column_list(self):
        assert GroupType.COLUMN_LIST.value == "column_list"

    def test_code(self):
        assert GroupType.CODE.value == "code"

    def test_media(self):
        assert GroupType.MEDIA.value == "media"

    def test_full_code_patch(self):
        assert GroupType.FULL_CODE_PATCH.value == "full_code_patch"


class TestBlockSubType:
    def test_child_record(self):
        assert BlockSubType.CHILD_RECORD.value == "child_record"

    def test_comment(self):
        assert BlockSubType.COMMENT.value == "comment"

    def test_paragraph(self):
        assert BlockSubType.PARAGRAPH.value == "paragraph"

    def test_heading(self):
        assert BlockSubType.HEADING.value == "heading"

    def test_quote(self):
        assert BlockSubType.QUOTE.value == "quote"

    def test_list_item(self):
        assert BlockSubType.LIST_ITEM.value == "list_item"

    def test_code(self):
        assert BlockSubType.CODE.value == "code"

    def test_equation(self):
        assert BlockSubType.EQUATION.value == "equation"

    def test_divider(self):
        assert BlockSubType.DIVIDER.value == "divider"

    def test_link(self):
        assert BlockSubType.LINK.value == "link"

    def test_commit(self):
        assert BlockSubType.COMMIT.value == "commit"


class TestDataFormat:
    def test_txt(self):
        assert DataFormat.TXT.value == "txt"

    def test_bin(self):
        assert DataFormat.BIN.value == "bin"

    def test_markdown(self):
        assert DataFormat.MARKDOWN.value == "markdown"

    def test_html(self):
        assert DataFormat.HTML.value == "html"

    def test_json(self):
        assert DataFormat.JSON.value == "json"

    def test_xml(self):
        assert DataFormat.XML.value == "xml"

    def test_csv(self):
        assert DataFormat.CSV.value == "csv"

    def test_yaml(self):
        assert DataFormat.YAML.value == "yaml"

    def test_base64(self):
        assert DataFormat.BASE64.value == "base64"

    def test_utf8(self):
        assert DataFormat.UTF8.value == "utf8"

    def test_patch(self):
        assert DataFormat.PATCH.value == "patch"

    def test_diff(self):
        assert DataFormat.DIFF.value == "diff"

    def test_code(self):
        assert DataFormat.CODE.value == "code"


class TestGroupSubType:
    def test_milestone(self):
        assert GroupSubType.MILESTONE.value == "milestone"

    def test_update(self):
        assert GroupSubType.UPDATE.value == "update"

    def test_child_record(self):
        assert GroupSubType.CHILD_RECORD.value == "child_record"

    def test_content(self):
        assert GroupSubType.CONTENT.value == "content"

    def test_record(self):
        assert GroupSubType.RECORD.value == "record"

    def test_comment_thread(self):
        assert GroupSubType.COMMENT_THREAD.value == "comment_thread"

    def test_comment(self):
        assert GroupSubType.COMMENT.value == "comment"

    def test_project_content(self):
        assert GroupSubType.PROJECT_CONTENT.value == "project_content"

    def test_issue_content(self):
        assert GroupSubType.ISSUE_CONTENT.value == "issue_content"

    def test_toggle(self):
        assert GroupSubType.TOGGLE.value == "toggle"

    def test_callout(self):
        assert GroupSubType.CALLOUT.value == "callout"

    def test_quote(self):
        assert GroupSubType.QUOTE.value == "quote"

    def test_synced_block(self):
        assert GroupSubType.SYNCED_BLOCK.value == "synced_block"

    def test_nested_block(self):
        assert GroupSubType.NESTED_BLOCK.value == "nested_block"

    def test_pr_file_change(self):
        assert GroupSubType.PR_FILE_CHANGE.value == "pr_file_change"


# ============================================================================
# Block model tests
# ============================================================================


class TestBlock:
    def test_minimal_creation(self):
        block = Block(type=BlockType.TEXT)
        assert block.type == BlockType.TEXT
        assert block.id is not None
        assert block.index is None
        assert block.sub_type is None
        assert block.name is None
        assert block.format is None
        assert block.comments == []
        assert block.data is None

    def test_with_all_core_fields(self):
        block = Block(
            index=0,
            parent_index=1,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            name="Test Block",
            format=DataFormat.MARKDOWN,
            data="Hello world",
        )
        assert block.index == 0
        assert block.parent_index == 1
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.name == "Test Block"
        assert block.format == DataFormat.MARKDOWN
        assert block.data == "Hello world"

    def test_unique_id_generation(self):
        block1 = Block(type=BlockType.TEXT)
        block2 = Block(type=BlockType.TEXT)
        assert block1.id != block2.id

    def test_optional_metadata_fields_default_none(self):
        block = Block(type=BlockType.TEXT)
        assert block.citation_metadata is None
        assert block.list_metadata is None
        assert block.table_row_metadata is None
        assert block.table_cell_metadata is None
        assert block.code_metadata is None
        assert block.media_metadata is None
        assert block.file_metadata is None
        assert block.link_metadata is None
        assert block.image_metadata is None
        assert block.semantic_metadata is None
        assert block.children_records is None

    def test_links_field(self):
        block = Block(type=BlockType.TEXT, links=["https://example.com"])
        assert block.links == ["https://example.com"]


# ============================================================================
# BlockGroup tests
# ============================================================================


class TestBlockGroup:
    def test_minimal_creation(self):
        bg = BlockGroup(type=GroupType.TEXT_SECTION)
        assert bg.type == GroupType.TEXT_SECTION
        assert bg.id is not None
        assert bg.index is None
        assert bg.name is None
        assert bg.parent_index is None
        assert bg.children is None

    def test_with_sub_type(self):
        bg = BlockGroup(type=GroupType.TEXT_SECTION, sub_type=GroupSubType.CONTENT)
        assert bg.sub_type == GroupSubType.CONTENT

    def test_with_children_new_format(self):
        children = BlockGroupChildren(
            block_ranges=[IndexRange(start=0, end=2)],
            block_group_ranges=[IndexRange(start=0, end=0)],
        )
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=children)
        assert bg.children is not None
        assert len(bg.children.block_ranges) == 1
        assert bg.children.block_ranges[0].start == 0
        assert bg.children.block_ranges[0].end == 2

    def test_convert_children_old_list_format_to_new(self):
        """Test that old List[BlockContainerIndex] format is auto-converted."""
        old_format = [
            {"block_index": 0},
            {"block_index": 1},
            {"block_index": 2},
            {"block_group_index": 5},
        ]
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=old_format)
        assert isinstance(bg.children, BlockGroupChildren)
        # Blocks 0, 1, 2 should be in a single range
        assert len(bg.children.block_ranges) == 1
        assert bg.children.block_ranges[0].start == 0
        assert bg.children.block_ranges[0].end == 2
        # Block group index 5
        assert len(bg.children.block_group_ranges) == 1
        assert bg.children.block_group_ranges[0].start == 5
        assert bg.children.block_group_ranges[0].end == 5

    def test_convert_children_old_format_with_gaps(self):
        old_format = [
            {"block_index": 0},
            {"block_index": 5},
            {"block_index": 6},
        ]
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=old_format)
        assert len(bg.children.block_ranges) == 2
        assert bg.children.block_ranges[0].start == 0
        assert bg.children.block_ranges[0].end == 0
        assert bg.children.block_ranges[1].start == 5
        assert bg.children.block_ranges[1].end == 6

    def test_convert_children_none_passthrough(self):
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=None)
        assert bg.children is None

    def test_convert_children_new_format_dict_passthrough(self):
        """Test that new format dict with block_ranges key passes through."""
        new_format = {
            "block_ranges": [{"start": 0, "end": 2}],
            "block_group_ranges": [],
        }
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=new_format)
        assert isinstance(bg.children, BlockGroupChildren)
        assert bg.children.block_ranges[0].start == 0

    def test_convert_children_already_block_group_children(self):
        children = BlockGroupChildren(
            block_ranges=[IndexRange(start=0, end=5)],
        )
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=children)
        assert bg.children is children

    def test_convert_children_old_format_with_hasattr_objects(self):
        """Test conversion with objects that have block_index/block_group_index attributes."""
        class FakeIndex:
            def __init__(self, block_index=None, block_group_index=None):
                self.block_index = block_index
                self.block_group_index = block_group_index

        old_format = [
            FakeIndex(block_index=0),
            FakeIndex(block_index=1),
            FakeIndex(block_group_index=3),
        ]
        bg = BlockGroup(type=GroupType.TEXT_SECTION, children=old_format)
        assert isinstance(bg.children, BlockGroupChildren)
        assert len(bg.children.block_ranges) == 1
        assert bg.children.block_ranges[0].start == 0
        assert bg.children.block_ranges[0].end == 1
        assert len(bg.children.block_group_ranges) == 1

    def test_convert_children_unknown_type_raises_validation_error(self):
        """Test that non-list, non-dict, non-BlockGroupChildren raises ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BlockGroup(type=GroupType.TEXT_SECTION, children="unexpected")


# ============================================================================
# BlockGroupChildren tests
# ============================================================================


class TestBlockGroupChildren:
    def test_empty_creation(self):
        children = BlockGroupChildren()
        assert children.block_ranges == []
        assert children.block_group_ranges == []

    # --- add_block_index tests ---

    def test_add_block_index_first(self):
        children = BlockGroupChildren()
        children.add_block_index(5)
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 5
        assert children.block_ranges[0].end == 5

    def test_add_block_index_contiguous_extend_end(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=2)])
        children.add_block_index(3)
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].end == 3

    def test_add_block_index_contiguous_extend_start(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=5, end=7)])
        children.add_block_index(4)
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 4

    def test_add_block_index_already_in_range(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=5)])
        children.add_block_index(3)
        # Should not duplicate; range remains same
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 0
        assert children.block_ranges[0].end == 5

    def test_add_block_index_new_separate_range(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=2)])
        children.add_block_index(10)
        assert len(children.block_ranges) == 2
        assert children.block_ranges[0].start == 0
        assert children.block_ranges[1].start == 10

    def test_add_block_index_sorted_after_insert(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=10, end=12)])
        children.add_block_index(5)
        assert len(children.block_ranges) == 2
        assert children.block_ranges[0].start == 5
        assert children.block_ranges[1].start == 10

    # --- add_block_group_index tests ---

    def test_add_block_group_index_first(self):
        children = BlockGroupChildren()
        children.add_block_group_index(3)
        assert len(children.block_group_ranges) == 1
        assert children.block_group_ranges[0].start == 3
        assert children.block_group_ranges[0].end == 3

    def test_add_block_group_index_contiguous_extend_end(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=0, end=2)])
        children.add_block_group_index(3)
        assert len(children.block_group_ranges) == 1
        assert children.block_group_ranges[0].end == 3

    def test_add_block_group_index_contiguous_extend_start(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=5, end=7)])
        children.add_block_group_index(4)
        assert len(children.block_group_ranges) == 1
        assert children.block_group_ranges[0].start == 4

    def test_add_block_group_index_already_in_range(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=0, end=5)])
        children.add_block_group_index(3)
        assert len(children.block_group_ranges) == 1
        assert children.block_group_ranges[0].end == 5

    def test_add_block_group_index_new_separate_range(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=0, end=2)])
        children.add_block_group_index(10)
        assert len(children.block_group_ranges) == 2

    def test_add_block_group_index_sorted_after_insert(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=10, end=12)])
        children.add_block_group_index(3)
        assert children.block_group_ranges[0].start == 3
        assert children.block_group_ranges[1].start == 10

    # --- from_indices tests ---

    def test_from_indices_empty(self):
        children = BlockGroupChildren.from_indices()
        assert children.block_ranges == []
        assert children.block_group_ranges == []

    def test_from_indices_single_block(self):
        children = BlockGroupChildren.from_indices(block_indices=[5])
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 5
        assert children.block_ranges[0].end == 5

    def test_from_indices_contiguous_blocks(self):
        children = BlockGroupChildren.from_indices(block_indices=[0, 1, 2, 3])
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 0
        assert children.block_ranges[0].end == 3

    def test_from_indices_non_contiguous_blocks(self):
        children = BlockGroupChildren.from_indices(block_indices=[0, 1, 5, 6, 10])
        assert len(children.block_ranges) == 3
        assert children.block_ranges[0] == IndexRange(start=0, end=1)
        assert children.block_ranges[1] == IndexRange(start=5, end=6)
        assert children.block_ranges[2] == IndexRange(start=10, end=10)

    def test_from_indices_deduplication(self):
        children = BlockGroupChildren.from_indices(block_indices=[1, 1, 2, 2, 3])
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 1
        assert children.block_ranges[0].end == 3

    def test_from_indices_unsorted_input(self):
        children = BlockGroupChildren.from_indices(block_indices=[5, 1, 3, 2, 4])
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 1
        assert children.block_ranges[0].end == 5

    def test_from_indices_block_group_indices(self):
        children = BlockGroupChildren.from_indices(block_group_indices=[0, 1, 5, 6])
        assert len(children.block_group_ranges) == 2
        assert children.block_group_ranges[0] == IndexRange(start=0, end=1)
        assert children.block_group_ranges[1] == IndexRange(start=5, end=6)

    def test_from_indices_both_types(self):
        children = BlockGroupChildren.from_indices(
            block_indices=[0, 1, 2],
            block_group_indices=[10, 11],
        )
        assert len(children.block_ranges) == 1
        assert len(children.block_group_ranges) == 1

    def test_from_indices_empty_lists(self):
        children = BlockGroupChildren.from_indices(block_indices=[], block_group_indices=[])
        assert children.block_ranges == []
        assert children.block_group_ranges == []


# ============================================================================
# SemanticMetadata tests
# ============================================================================


class TestSemanticMetadata:
    def test_default_creation(self):
        meta = SemanticMetadata()
        assert meta.summary is None
        assert meta.topics is None
        assert meta.categories == []

    def test_to_llm_context_empty(self):
        meta = SemanticMetadata()
        result = meta.to_llm_context()
        assert result == []

    def test_to_llm_context_summary_only(self):
        meta = SemanticMetadata(summary="Test summary")
        result = meta.to_llm_context()
        assert len(result) == 1
        assert "Summary" in result[0]
        assert "Test summary" in result[0]

    def test_to_llm_context_topics(self):
        meta = SemanticMetadata(topics=["AI", "ML"])
        result = meta.to_llm_context()
        assert len(result) == 1
        assert "Topics" in result[0]

    def test_to_llm_context_categories(self):
        meta = SemanticMetadata(categories=["Engineering"])
        result = meta.to_llm_context()
        assert len(result) == 1
        assert "Category" in result[0]
        assert "Engineering" in result[0]

    def test_to_llm_context_empty_categories_not_shown(self):
        meta = SemanticMetadata(categories=[])
        result = meta.to_llm_context()
        assert len(result) == 0

    def test_to_llm_context_sub_category_level_1(self):
        meta = SemanticMetadata(sub_category_level_1="Sub1")
        result = meta.to_llm_context()
        assert len(result) == 1
        assert "Sub-categories" in result[0]
        assert "Level 1" in result[0]
        assert "Sub1" in result[0]

    def test_to_llm_context_sub_category_level_2_without_level_1(self):
        """Level 2 appears even without level 1 (it's a separate if)."""
        meta = SemanticMetadata(sub_category_level_2="Sub2")
        result = meta.to_llm_context()
        assert len(result) == 1
        assert "Level 2" in result[0]

    def test_to_llm_context_sub_category_level_3(self):
        meta = SemanticMetadata(sub_category_level_3="Sub3")
        result = meta.to_llm_context()
        assert len(result) == 1
        assert "Level 3" in result[0]

    def test_to_llm_context_all_fields(self):
        meta = SemanticMetadata(
            summary="Test summary",
            topics=["AI"],
            categories=["Engineering"],
            sub_category_level_1="Backend",
            sub_category_level_2="API",
            sub_category_level_3="REST",
        )
        result = meta.to_llm_context()
        # summary(1) + topics(1) + categories(1) + sub1(1) + sub2(1) + sub3(1) = 6
        assert len(result) == 6


# ============================================================================
# CitationMetadata tests
# ============================================================================


class TestCitationMetadata:
    def test_default_creation(self):
        meta = CitationMetadata()
        assert meta.page_number is None
        assert meta.bounding_boxes is None

    def test_validate_bounding_boxes_exactly_4_points(self):
        points = [Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1), Point(x=0, y=1)]
        meta = CitationMetadata(bounding_boxes=points)
        assert len(meta.bounding_boxes) == 4

    def test_validate_bounding_boxes_less_than_4_raises(self):
        points = [Point(x=0, y=0), Point(x=1, y=0)]
        with pytest.raises(ValueError, match="exactly 4 points"):
            CitationMetadata(bounding_boxes=points)

    def test_validate_bounding_boxes_more_than_4_raises(self):
        points = [Point(x=0, y=0), Point(x=1, y=0), Point(x=1, y=1), Point(x=0, y=1), Point(x=0.5, y=0.5)]
        with pytest.raises(ValueError, match="exactly 4 points"):
            CitationMetadata(bounding_boxes=points)

    def test_validate_bounding_boxes_empty_list_raises(self):
        with pytest.raises(ValueError, match="exactly 4 points"):
            CitationMetadata(bounding_boxes=[])

    def test_none_bounding_boxes_default(self):
        """bounding_boxes defaults to None when not provided."""
        meta = CitationMetadata()
        assert meta.bounding_boxes is None

    def test_all_optional_fields(self):
        meta = CitationMetadata(
            section_title="Introduction",
            page_number=5,
            has_more_pages=True,
            more_page_numbers=[6, 7],
            line_number=10,
            paragraph_number=3,
            sheet_number=1,
            sheet_name="Sheet1",
            cell_reference="A1",
            row_number=2,
            column_number=3,
            slide_number=4,
            start_timestamp="00:01:00",
            end_timestamp="00:02:00",
            duration_ms=60000,
        )
        assert meta.section_title == "Introduction"
        assert meta.page_number == 5
        assert meta.has_more_pages is True
        assert meta.more_page_numbers == [6, 7]
        assert meta.line_number == 10
        assert meta.sheet_name == "Sheet1"
        assert meta.slide_number == 4
        assert meta.duration_ms == 60000
