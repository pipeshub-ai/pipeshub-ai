"""Unit tests for app.modules.parsers.blocks.blocks_parser.BlocksParser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    IndexRange,
    TableMetadata,
    TableRowMetadata,
)
from app.modules.parsers.blocks.blocks_parser import BlocksParser
from app.services.parsing.interface import ParseError, ParseErrorCode


def _make_parser() -> BlocksParser:
    return BlocksParser(logger=MagicMock(), config_service=MagicMock())


def _processed_container(
    blocks: list | None = None,
    block_groups: list | None = None,
) -> MagicMock:
    container = MagicMock()
    container.blocks = blocks if blocks is not None else [MagicMock()]
    container.block_groups = block_groups if block_groups is not None else [MagicMock()]
    return container


def _text_block(index: int = 0, data: str = "text", parent_index: int | None = 0) -> Block:
    return Block(
        index=index,
        type=BlockType.TEXT,
        format=DataFormat.TXT,
        data=data,
        parent_index=parent_index,
    )


# ---------------------------------------------------------------------------
# _separate_block_groups_by_index
# ---------------------------------------------------------------------------


class TestSeparateBlockGroupsByIndex:
    def test_splits_indexed_and_unindexed(self):
        parser = _make_parser()
        with_idx = BlockGroup(index=0, type=GroupType.TEXT_SECTION)
        without_idx = BlockGroup.model_construct(
            type=GroupType.TEXT_SECTION, index=None
        )

        indexed, unindexed = parser._separate_block_groups_by_index(
            [with_idx, without_idx]
        )

        assert indexed == [with_idx]
        assert unindexed == [without_idx]

    def test_all_indexed(self):
        parser = _make_parser()
        groups = [
            BlockGroup(index=0, type=GroupType.TEXT_SECTION),
            BlockGroup(index=1, type=GroupType.TEXT_SECTION),
        ]
        indexed, unindexed = parser._separate_block_groups_by_index(groups)
        assert len(indexed) == 2
        assert unindexed == []


# ---------------------------------------------------------------------------
# _calculate_index_shift_map
# ---------------------------------------------------------------------------


class TestCalculateIndexShiftMap:
    def test_no_processing_results(self):
        parser = _make_parser()
        bg0 = BlockGroup(index=0, type=GroupType.TEXT_SECTION)
        bg1 = BlockGroup(index=1, type=GroupType.TEXT_SECTION)
        assert parser._calculate_index_shift_map([bg0, bg1], {}) == {0: 0, 1: 0}

    def test_cumulative_shift_from_new_block_groups(self):
        parser = _make_parser()
        bg0 = BlockGroup(index=0, type=GroupType.TEXT_SECTION)
        bg1 = BlockGroup(index=1, type=GroupType.TEXT_SECTION)
        bg2 = BlockGroup(index=2, type=GroupType.TEXT_SECTION)
        processing_results = {
            0: ([MagicMock(), MagicMock()], []),
            1: ([MagicMock()], []),
        }
        result = parser._calculate_index_shift_map(
            [bg0, bg1, bg2], processing_results
        )
        assert result == {0: 0, 1: 2, 2: 3}


# ---------------------------------------------------------------------------
# _build_updated_blocks_container
# ---------------------------------------------------------------------------


class TestBuildUpdatedBlocksContainer:
    def test_empty_inputs(self):
        parser = _make_parser()
        container = BlocksContainer(blocks=[], block_groups=[])
        result = parser._build_updated_blocks_container(container, [], [], {}, {})
        assert result.blocks == []
        assert result.block_groups == []

    def test_unprocessed_reassigns_existing_blocks(self):
        parser = _make_parser()
        # Two blocks same parent; one with None index sorts last before reassignment
        block_a = _text_block(index=5, parent_index=0, data="a")
        block_b = Block.model_construct(
            index=None,
            type=BlockType.TEXT,
            format=DataFormat.TXT,
            data="b",
            parent_index=0,
        )
        # Block with no parent_index is ignored for reassignment
        orphan = _text_block(index=9, parent_index=None, data="orphan")
        bg = BlockGroup(index=0, type=GroupType.TEXT_SECTION, requires_processing=False)
        bg.children = BlockGroupChildren()  # already initialized
        container = BlocksContainer(
            blocks=[block_b, block_a, orphan], block_groups=[bg]
        )

        result = parser._build_updated_blocks_container(
            container, [bg], [], {}, {0: 0}
        )

        assert len(result.blocks) == 2
        assert result.blocks[0].data == "a"
        assert result.blocks[0].index == 0
        assert result.blocks[1].data == "b"
        assert result.blocks[1].index == 1
        assert result.blocks[0].parent_index == 0

    def test_unprocessed_initializes_null_children(self):
        parser = _make_parser()
        block = _text_block(index=0, parent_index=0)
        bg = BlockGroup(index=0, type=GroupType.TEXT_SECTION, requires_processing=False)
        bg.children = None
        container = BlocksContainer(blocks=[block], block_groups=[bg])

        result = parser._build_updated_blocks_container(
            container, [bg], [], {}, {0: 0}
        )

        assert bg.children is not None
        assert len(result.blocks) == 1

    def test_processed_merges_new_blocks_and_nested_groups(self):
        parser = _make_parser()
        parent = BlockGroup(
            index=0, type=GroupType.TEXT_SECTION, requires_processing=True
        )
        parent.children = None

        nested = BlockGroup(index=0, type=GroupType.LIST)
        nested.parent_index = None
        nested.children = BlockGroupChildren(
            block_ranges=[IndexRange(start=0, end=0)],
            block_group_ranges=[IndexRange(start=0, end=0)],
        )
        new_block = _text_block(index=0, parent_index=None)

        container = BlocksContainer(blocks=[], block_groups=[parent])
        result = parser._build_updated_blocks_container(
            container,
            [parent],
            [],
            {0: ([nested], [new_block])},
            {0: 0},
        )

        assert parent.requires_processing is False
        assert len(result.blocks) == 1
        assert result.blocks[0].parent_index == 0
        assert len(result.block_groups) == 2
        assert nested.index == 1
        assert nested.parent_index == 0
        assert nested.children.block_ranges[0].start == 0
        assert nested.children.block_group_ranges[0].start == 1

    def test_processed_relative_parent_indices(self):
        parser = _make_parser()
        parent = BlockGroup(
            index=0, type=GroupType.TEXT_SECTION, requires_processing=True
        )
        parent.children = BlockGroupChildren()

        nested = BlockGroup(index=0, type=GroupType.LIST, parent_index=0)
        nested.children = None
        child_block = _text_block(index=0, parent_index=0)

        container = BlocksContainer(blocks=[], block_groups=[parent])
        result = parser._build_updated_blocks_container(
            container,
            [parent],
            [],
            {0: ([nested], [child_block])},
            {0: 0},
        )

        # relative parent_index 0 + insertion_index 1
        assert result.blocks[0].parent_index == 1
        assert nested.parent_index == 1

    def test_shifts_parent_and_child_group_ranges(self):
        parser = _make_parser()
        child = BlockGroup(index=1, type=GroupType.LIST, parent_index=0)
        child.children = None
        parent = BlockGroup(index=0, type=GroupType.TEXT_SECTION)
        parent.children = BlockGroupChildren.from_indices(block_group_indices=[1])

        # Also include an index not in the shift map inside ranges
        parent.children.block_group_ranges = [
            IndexRange(start=1, end=2),
        ]

        container = BlocksContainer(blocks=[], block_groups=[parent, child])
        # shift of 1 applied to index 0's descendants conceptually; map both
        index_shift_map = {0: 0, 1: 2}
        result = parser._build_updated_blocks_container(
            container, [parent, child], [], {}, index_shift_map
        )

        assert parent.index == 0
        assert child.index == 3  # 1 + 2
        assert child.parent_index == 0  # 0 + shift[0]
        assert result.block_groups[0].children.block_group_ranges

    def test_appends_block_groups_without_index(self):
        parser = _make_parser()
        with_idx = BlockGroup(index=0, type=GroupType.TEXT_SECTION)
        without_idx = BlockGroup.model_construct(
            type=GroupType.TEXT_SECTION, index=None, children=None
        )
        container = BlocksContainer(blocks=[], block_groups=[with_idx])

        result = parser._build_updated_blocks_container(
            container, [with_idx], [without_idx], {}, {0: 0}
        )

        assert len(result.block_groups) == 2
        assert result.block_groups[-1].index is None

    def test_sorts_blocks_with_none_index_last(self):
        parser = _make_parser()
        bg = BlockGroup(index=0, type=GroupType.TEXT_SECTION, requires_processing=True)
        bg.children = None
        # Create blocks that will get indices assigned; empty processing still works
        container = BlocksContainer(blocks=[], block_groups=[bg])
        b0 = _text_block(index=0, parent_index=None)
        result = parser._build_updated_blocks_container(
            container, [bg], [], {0: ([], [b0])}, {0: 0}
        )
        assert result.blocks[0].index == 0


# ---------------------------------------------------------------------------
# _process_blockgroup_images
# ---------------------------------------------------------------------------


class TestProcessBlockgroupImages:
    @pytest.mark.asyncio
    async def test_no_images_returns_original(self):
        parser = _make_parser()
        with patch(
            "app.modules.parsers.blocks.blocks_parser.ImageParser"
        ) as mock_image_cls, patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ) as mock_md_cls:
            mock_md = MagicMock()
            mock_md.extract_and_replace_images.return_value = ("# hello", [])
            mock_md_cls.return_value = mock_md
            mock_image_cls.return_value = MagicMock()

            md, caption_map = await parser._process_blockgroup_images("# hello")

        assert md == "# hello"
        assert caption_map == {}

    @pytest.mark.asyncio
    async def test_converts_image_urls_to_base64(self):
        parser = _make_parser()
        with patch(
            "app.modules.parsers.blocks.blocks_parser.ImageParser"
        ) as mock_image_cls, patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ) as mock_md_cls:
            mock_md = MagicMock()
            mock_md.extract_and_replace_images.return_value = (
                "![Image_1](http://example.com/a.png)",
                [{"url": "http://example.com/a.png", "new_alt_text": "Image_1"}],
            )
            mock_md_cls.return_value = mock_md

            mock_image = MagicMock()
            mock_image.urls_to_base64 = AsyncMock(
                return_value=["data:image/png;base64,abc"]
            )
            mock_image_cls.return_value = mock_image

            md, caption_map = await parser._process_blockgroup_images(
                "![x](http://example.com/a.png)"
            )

        assert "Image_1" in md
        assert caption_map == {"Image_1": "data:image/png;base64,abc"}

    @pytest.mark.asyncio
    async def test_skips_failed_base64_conversions(self):
        parser = _make_parser()
        with patch(
            "app.modules.parsers.blocks.blocks_parser.ImageParser"
        ) as mock_image_cls, patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ) as mock_md_cls:
            mock_md = MagicMock()
            mock_md.extract_and_replace_images.return_value = (
                "![Image_1](http://example.com/a.png)",
                [{"url": "http://example.com/a.png", "new_alt_text": "Image_1"}],
            )
            mock_md_cls.return_value = mock_md

            mock_image = MagicMock()
            mock_image.urls_to_base64 = AsyncMock(return_value=[None])
            mock_image_cls.return_value = mock_image

            _, caption_map = await parser._process_blockgroup_images("![x](u)")

        assert caption_map == {}


# ---------------------------------------------------------------------------
# _process_single_blockgroup (markdown)
# ---------------------------------------------------------------------------


class TestProcessSingleBlockgroup:
    @pytest.mark.asyncio
    async def test_no_markdown_data_raises(self):
        parser = _make_parser()
        bg = MagicMock(data=None, index=0)
        with pytest.raises(ValueError, match="no valid markdown data"):
            await parser._process_single_blockgroup(bg, "record", MagicMock())

    @pytest.mark.asyncio
    async def test_non_string_data_raises(self):
        parser = _make_parser()
        bg = MagicMock(data={"x": 1}, index=0)
        with pytest.raises(ValueError, match="no valid markdown data"):
            await parser._process_single_blockgroup(bg, "record", MagicMock())

    @pytest.mark.asyncio
    async def test_success_delegates_to_md_parser(self):
        parser = _make_parser()
        parser._process_blockgroup_images = AsyncMock(
            return_value=("# Hello", {"Image_1": "data:image/png;base64,abc"})
        )
        bg = MagicMock(data="# Hello World", index=0)
        bg.configure_mock(name="doc.md")

        result = _processed_container(blocks=[MagicMock()], block_groups=[MagicMock()])
        md_parser = MagicMock()
        md_parser.parse_to_blocks = AsyncMock(return_value=result)

        new_bgs, new_blocks = await parser._process_single_blockgroup(
            bg, "record", md_parser
        )

        assert len(new_blocks) == 1
        assert len(new_bgs) == 1
        md_parser.parse_to_blocks.assert_awaited_once_with(
            "# Hello",
            caption_map={"Image_1": "data:image/png;base64,abc"},
            name="doc.md",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_record_name(self):
        parser = _make_parser()
        parser._process_blockgroup_images = AsyncMock(return_value=("# Hello", {}))
        bg = MagicMock(data="# Hello", index=0)
        bg.configure_mock(name=None)

        result = _processed_container(blocks=[MagicMock()], block_groups=[])
        md_parser = MagicMock()
        md_parser.parse_to_blocks = AsyncMock(return_value=result)

        await parser._process_single_blockgroup(bg, "fallback", md_parser)

        md_parser.parse_to_blocks.assert_awaited_once_with(
            "# Hello", caption_map=None, name="fallback"
        )


# ---------------------------------------------------------------------------
# _process_single_blockgroup_html
# ---------------------------------------------------------------------------


class TestProcessSingleBlockgroupHtml:
    @pytest.mark.asyncio
    async def test_no_html_data_raises(self):
        parser = _make_parser()
        bg = MagicMock(data=None, index=0)
        with pytest.raises(ValueError, match="no valid HTML data"):
            await parser._process_single_blockgroup_html(bg, "record", MagicMock())

    @pytest.mark.asyncio
    async def test_non_string_data_raises(self):
        parser = _make_parser()
        bg = MagicMock(data={"not": "html"}, index=0)
        with pytest.raises(ValueError, match="no valid HTML data"):
            await parser._process_single_blockgroup_html(bg, "record", MagicMock())

    @pytest.mark.asyncio
    async def test_success_without_images(self):
        parser = _make_parser()
        bg = MagicMock(data="<p>Hello</p>", index=0)
        bg.configure_mock(name="page.html")

        html_parser = MagicMock()
        html_parser.clean_html.return_value = "<p>Hello</p>"
        html_parser.extract_and_replace_images.return_value = ("<p>Hello</p>", [])
        html_parser.parse_to_blocks = AsyncMock(
            return_value=_processed_container(blocks=[MagicMock()], block_groups=[])
        )

        new_bgs, new_blocks = await parser._process_single_blockgroup_html(
            bg, "record", html_parser
        )

        assert len(new_blocks) == 1
        assert new_bgs == []
        html_parser.parse_to_blocks.assert_awaited_once_with(
            "<p>Hello</p>", caption_map=None, name="page.html"
        )

    @pytest.mark.asyncio
    async def test_success_converts_images_to_base64(self):
        parser = _make_parser()
        bg = MagicMock(data='<img src="http://example.com/a.png">', index=1)
        bg.configure_mock(name=None)

        html_parser = MagicMock()
        html_parser.clean_html.return_value = '<img src="http://example.com/a.png">'
        html_parser.extract_and_replace_images.return_value = (
            '<img alt="Image_1" src="http://example.com/a.png">',
            [{"url": "http://example.com/a.png", "new_alt_text": "Image_1"}],
        )
        html_parser.parse_to_blocks = AsyncMock(return_value=_processed_container())

        with patch(
            "app.modules.parsers.blocks.blocks_parser.ImageParser"
        ) as mock_image_cls:
            image_parser = MagicMock()
            image_parser.urls_to_base64 = AsyncMock(
                return_value=["data:image/png;base64,abc"]
            )
            mock_image_cls.return_value = image_parser

            await parser._process_single_blockgroup_html(
                bg, "fallback_name", html_parser
            )

        html_parser.parse_to_blocks.assert_awaited_once_with(
            '<img alt="Image_1" src="http://example.com/a.png">',
            caption_map={"Image_1": "data:image/png;base64,abc"},
            name="fallback_name",
        )

    @pytest.mark.asyncio
    async def test_skips_failed_image_base64(self):
        parser = _make_parser()
        bg = MagicMock(data="<img src='u'>", index=0)
        bg.configure_mock(name="x")

        html_parser = MagicMock()
        html_parser.clean_html.return_value = "<img src='u'>"
        html_parser.extract_and_replace_images.return_value = (
            "<img alt='Image_1' src='u'>",
            [{"url": "u", "new_alt_text": "Image_1"}],
        )
        html_parser.parse_to_blocks = AsyncMock(
            return_value=_processed_container(blocks=[], block_groups=[])
        )

        with patch(
            "app.modules.parsers.blocks.blocks_parser.ImageParser"
        ) as mock_image_cls:
            image_parser = MagicMock()
            image_parser.urls_to_base64 = AsyncMock(return_value=[None])
            mock_image_cls.return_value = image_parser
            await parser._process_single_blockgroup_html(bg, "r", html_parser)

        html_parser.parse_to_blocks.assert_awaited_once_with(
            "<img alt='Image_1' src='u'>", caption_map=None, name="x"
        )


# ---------------------------------------------------------------------------
# _process_blockgroups
# ---------------------------------------------------------------------------


class TestProcessBlockgroups:
    @pytest.mark.asyncio
    async def test_empty_block_groups_returns_original(self):
        parser = _make_parser()
        container = BlocksContainer(blocks=[], block_groups=[])
        assert await parser._process_blockgroups(container, "r") is container

    @pytest.mark.asyncio
    async def test_no_processing_needed_returns_original(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0, type=GroupType.TEXT_SECTION, requires_processing=False
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])
        assert await parser._process_blockgroups(container, "r") is container

    @pytest.mark.asyncio
    async def test_requires_processing_without_data_skipped(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0, type=GroupType.TEXT_SECTION, requires_processing=True, data=None
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])
        assert await parser._process_blockgroups(container, "r") is container

    @pytest.mark.asyncio
    async def test_routes_html_format(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="<p>hi</p>",
            format=DataFormat.HTML,
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])
        new_block = _text_block(data="hi", parent_index=None)
        parser._process_single_blockgroup_html = AsyncMock(
            return_value=([], [new_block])
        )
        parser._process_single_blockgroup = AsyncMock()

        with patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ), patch("app.modules.parsers.blocks.blocks_parser.HTMLParser"):
            result = await parser._process_blockgroups(container, "record")

        parser._process_single_blockgroup_html.assert_awaited_once()
        parser._process_single_blockgroup.assert_not_awaited()
        assert len(result.blocks) == 1
        assert result.block_groups[0].requires_processing is False

    @pytest.mark.asyncio
    async def test_routes_markdown_format(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="# hello",
            format=DataFormat.MARKDOWN,
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])
        new_block = _text_block(data="hello", parent_index=None)
        parser._process_single_blockgroup = AsyncMock(
            return_value=([], [new_block])
        )
        parser._process_single_blockgroup_html = AsyncMock()

        with patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ), patch("app.modules.parsers.blocks.blocks_parser.HTMLParser"):
            result = await parser._process_blockgroups(container, "record")

        parser._process_single_blockgroup.assert_awaited_once()
        parser._process_single_blockgroup_html.assert_not_awaited()
        assert len(result.blocks) == 1

    @pytest.mark.asyncio
    async def test_processing_error_propagates(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="# x",
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])
        parser._process_single_blockgroup = AsyncMock(
            side_effect=RuntimeError("parser failed")
        )

        with patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ), patch("app.modules.parsers.blocks.blocks_parser.HTMLParser"):
            with pytest.raises(RuntimeError, match="parser failed"):
                await parser._process_blockgroups(container, "record")

# ---------------------------------------------------------------------------
# _enhance_tables_with_llm
# ---------------------------------------------------------------------------


class TestEnhanceTablesWithLlm:
    @pytest.mark.asyncio
    async def test_no_table_groups_returns(self):
        parser = _make_parser()
        bg = BlockGroup(index=0, type=GroupType.TEXT_SECTION)
        container = BlocksContainer(blocks=[], block_groups=[bg])
        await parser._enhance_tables_with_llm(container)

    @pytest.mark.asyncio
    async def test_table_without_markdown_skipped(self):
        parser = _make_parser()
        bg = BlockGroup(index=0, type=GroupType.TABLE, data={"other": True})
        container = BlocksContainer(blocks=[], block_groups=[bg])
        await parser._enhance_tables_with_llm(container)

    @pytest.mark.asyncio
    async def test_table_with_null_data_skipped(self):
        parser = _make_parser()
        bg = BlockGroup(index=0, type=GroupType.TABLE, data=None)
        container = BlocksContainer(blocks=[], block_groups=[bg])
        await parser._enhance_tables_with_llm(container)

    @pytest.mark.asyncio
    async def test_llm_returns_none(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0, type=GroupType.TABLE, data={"table_markdown": "| A |"}
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await parser._enhance_tables_with_llm(container)

        assert "table_summary" not in bg.data

    @pytest.mark.asyncio
    async def test_recreates_data_dict_when_cleared_during_llm_call(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0, type=GroupType.TABLE, data={"table_markdown": "| A |"}
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        async def _summary(*_a, **_k):
            bg.data = None
            return MagicMock(summary="s", headers=["A"])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            side_effect=_summary,
        ):
            await parser._enhance_tables_with_llm(container)

        assert bg.data == {"table_summary": "s", "column_headers": ["A"]}

    @pytest.mark.asyncio
    async def test_empty_headers_still_builds_grid_for_rows(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["v1", "extra"]},
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])
        container = BlocksContainer(blocks=[row], block_groups=[bg])

        # headers empty list -> cols = [], grid has no header row
        mock_response = MagicMock(summary="s", headers=[])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["desc"], []),
        ) as mock_rows:
            await parser._enhance_tables_with_llm(container)

        mock_rows.assert_awaited_once()
        assert row.data["row_natural_language_text"] == "desc"

    @pytest.mark.asyncio
    async def test_enhancement_with_block_group_children(self):
        parser = _make_parser()
        header = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["A", "B"]},
            table_row_metadata=TableRowMetadata(is_header=True),
        )
        row = Block(
            index=1,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["v1", "v2"]},
            table_row_metadata=TableRowMetadata(is_header=False),
        )
        # Non-row block in range is ignored
        text = _text_block(index=2, parent_index=0)

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A | B |\n| v1 | v2 |"},
            table_metadata=TableMetadata(column_names=["old"]),
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0, 1, 2])
        container = BlocksContainer(blocks=[header, row, text], block_groups=[bg])

        mock_response = MagicMock(summary="summary", headers=["Col_A", "Col_B"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["row desc"], []),
        ):
            await parser._enhance_tables_with_llm(container)

        assert bg.description == "summary"
        assert bg.data["table_summary"] == "summary"
        assert bg.data["column_headers"] == ["Col_A", "Col_B"]
        assert bg.table_metadata.column_names == ["Col_A", "Col_B"]
        assert row.data["row_natural_language_text"] == "row desc"
        assert "row_natural_language_text" not in header.data

    @pytest.mark.asyncio
    async def test_enhancement_legacy_children_list(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["only"]},
        )
        # Non-table-row + invalid index entries exercise continue paths
        text = _text_block(index=1, parent_index=0)
        bg = BlockGroup.model_construct(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |\n| only |"},
            table_metadata=None,
            children=[
                BlockContainerIndex(block_index=0),
                BlockContainerIndex(block_index=1),
                BlockContainerIndex(block_index=None),
                BlockContainerIndex(block_index=99),
                "not-an-index",
            ],
        )
        container = BlocksContainer(blocks=[row, text], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["desc"], []),
        ):
            await parser._enhance_tables_with_llm(container)

        assert row.data["row_natural_language_text"] == "desc"

    @pytest.mark.asyncio
    async def test_legacy_children_cells_not_list(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": "bad"},
        )
        bg = BlockGroup.model_construct(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
            table_metadata=None,
            children=[BlockContainerIndex(block_index=0)],
        )
        container = BlocksContainer(blocks=[row], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=([""], []),
        ):
            await parser._enhance_tables_with_llm(container)

    @pytest.mark.asyncio
    async def test_row_data_without_cells_key(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"no_cells": True},
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])
        container = BlocksContainer(blocks=[row], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
        ) as mock_rows:
            await parser._enhance_tables_with_llm(container)

        # No row_dicts collected => get_rows_text not called
        mock_rows.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_headers_and_falsy_row_data(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["x"]},
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
            children=None,
        )
        # children None skips row collection; still updates summary
        container = BlocksContainer(blocks=[row], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=None)
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
        ) as mock_rows:
            await parser._enhance_tables_with_llm(container)

        assert bg.data["table_summary"] == "s"
        assert bg.data["column_headers"] == []
        mock_rows.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_row_description_skipped_when_row_data_none(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["v"]},
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])
        container = BlocksContainer(blocks=[row], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])

        async def _rows(*_a, **_k):
            # Clear data before descriptions are applied
            row.data = None
            return (["desc"], [])

        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            side_effect=_rows,
        ):
            await parser._enhance_tables_with_llm(container)

        assert row.data is None

    @pytest.mark.asyncio
    async def test_fewer_descriptions_than_rows(self):
        parser = _make_parser()
        rows = [
            Block(
                index=i,
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                data={"cells": [f"v{i}"]},
            )
            for i in range(2)
        ]
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0, 1])
        container = BlocksContainer(blocks=rows, block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=(["only one"], []),
        ):
            await parser._enhance_tables_with_llm(container)

        assert rows[0].data["row_natural_language_text"] == "only one"
        assert "row_natural_language_text" not in rows[1].data

    @pytest.mark.asyncio
    async def test_row_without_cells_list_appends_empty_dict(self):
        parser = _make_parser()
        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": "not-a-list"},
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])
        container = BlocksContainer(blocks=[row], block_groups=[bg])

        mock_response = MagicMock(summary="", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
            return_value=([""], []),
        ) as mock_rows:
            await parser._enhance_tables_with_llm(container)

        # grid built from empty row dict
        assert mock_rows.await_count == 1

    @pytest.mark.asyncio
    async def test_no_non_header_rows_skips_get_rows_text(self):
        parser = _make_parser()
        header = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={"cells": ["A"]},
            table_row_metadata=TableRowMetadata(is_header=True),
        )
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren.from_indices(block_indices=[0])
        container = BlocksContainer(blocks=[header], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
        ) as mock_rows:
            await parser._enhance_tables_with_llm(container)

        mock_rows.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_out_of_range_block_index_ignored(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            data={"table_markdown": "| A |"},
        )
        bg.children = BlockGroupChildren(
            block_ranges=[IndexRange(start=5, end=5)]
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        mock_response = MagicMock(summary="s", headers=["A"])
        with patch(
            "app.utils.indexing_helpers.get_table_summary_n_headers",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), patch(
            "app.utils.indexing_helpers.get_rows_text",
            new_callable=AsyncMock,
        ) as mock_rows:
            await parser._enhance_tables_with_llm(container)

        mock_rows.assert_not_awaited()
        assert bg.data["table_summary"] == "s"


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


class TestParse:
    @pytest.mark.asyncio
    async def test_bytes_payload(self):
        parser = _make_parser()
        container = BlocksContainer(blocks=[], block_groups=[])
        parser._process_blockgroups = AsyncMock(return_value=container)
        parser._enhance_tables_with_llm = AsyncMock()

        result = await parser.parse(
            container.model_dump_json().encode("utf-8"), "rec"
        )

        assert result.block_container is container
        assert result.metadata == {"record_name": "rec"}
        parser._process_blockgroups.assert_awaited_once()
        parser._enhance_tables_with_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_str_payload(self):
        parser = _make_parser()
        container = BlocksContainer(blocks=[], block_groups=[])
        parser._process_blockgroups = AsyncMock(return_value=container)
        parser._enhance_tables_with_llm = AsyncMock()

        # After bytes decode path; pass str by skipping bytes branch via already-decoded
        # parse type-hints bytes but accepts str after the first isinstance check fails
        # when we pass str directly.
        result = await parser.parse(container.model_dump_json(), "rec")  # type: ignore[arg-type]
        assert result.block_container is container

    @pytest.mark.asyncio
    async def test_dict_payload(self):
        parser = _make_parser()
        payload = {"blocks": [], "block_groups": []}
        out = BlocksContainer(**payload)
        parser._process_blockgroups = AsyncMock(return_value=out)
        parser._enhance_tables_with_llm = AsyncMock()

        result = await parser.parse(payload, "rec")  # type: ignore[arg-type]
        assert result.block_container is out

    @pytest.mark.asyncio
    async def test_invalid_content_type_raises(self):
        parser = _make_parser()
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(12345, "rec")  # type: ignore[arg-type]
        assert exc_info.value.code == ParseErrorCode.INVALID_INPUT

    @pytest.mark.asyncio
    async def test_processes_html_blockgroups_end_to_end(self):
        parser = _make_parser()
        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            requires_processing=True,
            data="<p>body</p>",
            format=DataFormat.HTML,
        )
        payload = BlocksContainer(blocks=[], block_groups=[bg]).model_dump_json()
        new_block = _text_block(data="body", parent_index=None)
        parser._process_single_blockgroup_html = AsyncMock(
            return_value=([], [new_block])
        )
        parser._enhance_tables_with_llm = AsyncMock()

        with patch(
            "app.modules.parsers.blocks.blocks_parser.MarkdownParser"
        ), patch("app.modules.parsers.blocks.blocks_parser.HTMLParser"):
            result = await parser.parse(payload.encode("utf-8"), "record")

        parser._process_single_blockgroup_html.assert_awaited_once()
        assert len(result.block_container.blocks) == 1
