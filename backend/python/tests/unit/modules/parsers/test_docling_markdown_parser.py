"""Full coverage tests for docling_markdown_parser."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import Block, BlockType, BlocksContainer, ImageMetadata
from app.services.parsing.interface import ParseResult

_DOCLING_MOCKS = {
    "docling": MagicMock(),
    "docling.datamodel": MagicMock(),
    "docling.datamodel.document": MagicMock(),
    "docling.document_converter": MagicMock(),
}

with patch.dict("sys.modules", _DOCLING_MOCKS):
    from app.modules.parsers.image_parser.image_parser import ImageParser
    from app.modules.parsers.markdown.docling_markdown_parser import (
        DoclingMarkdownParser,
        _apply_caption_map,
        _extract_and_replace_images,
    )

_MD_MODULE = DoclingMarkdownParser.parse_string.__globals__["markdown_lib"]


@pytest.fixture
def parser():
    with patch.dict("sys.modules", _DOCLING_MOCKS):
        with patch(
            "app.modules.parsers.markdown.docling_markdown_parser.DocumentConverter"
        ):
            return DoclingMarkdownParser()


@pytest.fixture
def parser_with_logger():
    logger = logging.getLogger("test.docling_markdown_parser")
    with patch.dict("sys.modules", _DOCLING_MOCKS):
        with patch(
            "app.modules.parsers.markdown.docling_markdown_parser.DocumentConverter"
        ):
            return DoclingMarkdownParser(logger=logger, config_service={"key": "value"})


class TestDoclingMarkdownParserInit:
    def test_uses_provided_logger_and_config(self, parser_with_logger):
        assert parser_with_logger._logger.name == "test.docling_markdown_parser"
        assert parser_with_logger._config_service == {"key": "value"}

    def test_defaults_logger_when_omitted(self, parser):
        assert parser._logger.name == (
            "app.modules.parsers.markdown.docling_markdown_parser"
        )
        assert parser._config_service is None


class TestDoclingMarkdownParserParseString:
    def test_converts_markdown_to_html_bytes(self, parser):
        with patch.object(_MD_MODULE, "markdown", return_value="<h1>Hello</h1>") as mock_fn:
            result = parser.parse_string("# Hello")
            assert result == b"<h1>Hello</h1>"
            mock_fn.assert_called_once_with("# Hello", extensions=["md_in_html"])

    def test_extract_and_replace_images_delegates(self, parser):
        modified, images = parser.extract_and_replace_images("![alt](https://x.com/a.png)")
        assert images[0]["url"] == "https://x.com/a.png"
        assert "![Image_1]" in modified

    def test_extract_and_replace_images_handles_html_img_tags(self, parser):
        modified, images = parser.extract_and_replace_images(
            '<img src="https://x.com/a.png" alt="diagram">'
        )
        assert images[0]["image_type"] == "html"
        assert images[0]["url"] == "https://x.com/a.png"
        assert 'alt="Image_1"' in modified


class TestDoclingMarkdownParserParse:
    @pytest.mark.asyncio
    async def test_parse_bytes_strips_and_delegates(self, parser):
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch.object(
            parser, "extract_and_replace_images", return_value=("# Title", [])
        ), patch.object(
            parser,
            "parse_to_blocks",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_parse:
            result = await parser.parse(b"  # Title  ", "notes.md")

        assert isinstance(result, ParseResult)
        assert result.block_container is expected
        assert result.metadata == {"record_name": "notes.md"}
        mock_parse.assert_awaited_once_with(
            "# Title",
            caption_map=None,
            name="notes.md",
        )

    @pytest.mark.asyncio
    async def test_parse_accepts_str_content(self, parser):
        expected = BlocksContainer(blocks=[], block_groups=[])
        with patch.object(
            parser, "extract_and_replace_images", return_value=("text", [])
        ), patch.object(
            parser,
            "parse_to_blocks",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await parser.parse("text", "plain.md")

        assert result.metadata == {"record_name": "plain.md"}

    @pytest.mark.asyncio
    async def test_parse_builds_caption_map_from_base64_urls(self, parser):
        expected = BlocksContainer(blocks=[], block_groups=[])
        images = [
            {
                "url": "https://example.com/a.png",
                "alt_text": "a",
                "new_alt_text": "Image_1",
            }
        ]
        with patch.object(
            parser, "extract_and_replace_images", return_value=("# Hi", images)
        ), patch.object(
            ImageParser,
            "urls_to_base64",
            new_callable=AsyncMock,
            return_value=["data:image/png;base64,ENC"],
        ) as mock_b64, patch.object(
            parser,
            "parse_to_blocks",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_parse:
            await parser.parse(b"# Hi", "readme.md")

        mock_b64.assert_awaited_once_with(["https://example.com/a.png"])
        mock_parse.assert_awaited_once_with(
            "# Hi",
            caption_map={"Image_1": "data:image/png;base64,ENC"},
            name="readme.md",
        )

    @pytest.mark.asyncio
    async def test_parse_skips_none_base64_urls(self, parser):
        expected = BlocksContainer(blocks=[], block_groups=[])
        images = [
            {"url": "https://a.com/1.png", "alt_text": "", "new_alt_text": "Image_1"},
            {"url": "https://a.com/2.png", "alt_text": "", "new_alt_text": "Image_2"},
        ]
        with patch.object(
            parser, "extract_and_replace_images", return_value=("# Hi", images)
        ), patch.object(
            ImageParser,
            "urls_to_base64",
            new_callable=AsyncMock,
            return_value=[None, "data:image/png;base64,OK"],
        ), patch.object(
            parser,
            "parse_to_blocks",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_parse:
            await parser.parse(b"# Hi", "readme.md")

        mock_parse.assert_awaited_once_with(
            "# Hi",
            caption_map={"Image_2": "data:image/png;base64,OK"},
            name="readme.md",
        )


class TestDoclingMarkdownParserParseToBlocks:
    @pytest.mark.asyncio
    async def test_parse_to_blocks_without_caption_map(self, parser):
        expected = BlocksContainer(blocks=[], block_groups=[])
        html_bytes = b"<p>Hello</p>"
        with patch.object(parser, "parse_string", return_value=html_bytes), patch(
            "app.modules.parsers.pdf.docling_processor.DoclingProcessor"
        ) as mock_processor_cls:
            instance = mock_processor_cls.return_value
            instance.parse_document = AsyncMock(return_value=MagicMock())
            instance.create_blocks = AsyncMock(return_value=expected)

            container = await parser.parse_to_blocks("# Hello", name="report.md")

        assert container is expected
        mock_processor_cls.assert_called_once_with(
            logger=parser._logger,
            config=parser._config_service,
        )
        instance.parse_document.assert_awaited_once_with("report.md", html_bytes)
        instance.create_blocks.assert_awaited_once_with(
            instance.parse_document.return_value,
            page_number=None,
        )

    @pytest.mark.asyncio
    async def test_parse_to_blocks_default_filename_and_page_number(
        self, parser_with_logger
    ):
        expected = BlocksContainer(blocks=[], block_groups=[])
        html_bytes = b"<p>Hello</p>"
        with patch.object(parser_with_logger, "parse_string", return_value=html_bytes), patch(
            "app.modules.parsers.pdf.docling_processor.DoclingProcessor"
        ) as mock_processor_cls:
            instance = mock_processor_cls.return_value
            instance.parse_document = AsyncMock(return_value=MagicMock())
            instance.create_blocks = AsyncMock(return_value=expected)

            container = await parser_with_logger.parse_to_blocks(
                "# Hello",
                page_number=3,
            )

        assert container is expected
        instance.parse_document.assert_awaited_once_with("document.md", html_bytes)
        instance.create_blocks.assert_awaited_once_with(
            instance.parse_document.return_value,
            page_number=3,
        )

    @pytest.mark.asyncio
    async def test_parse_to_blocks_applies_caption_map(self, parser):
        image_block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            image_metadata=ImageMetadata(captions=["Image_1"]),
            data=None,
        )
        expected = BlocksContainer(blocks=[image_block], block_groups=[])
        with patch.object(parser, "parse_string", return_value=b"<h1>Title</h1>"), patch(
            "app.modules.parsers.pdf.docling_processor.DoclingProcessor"
        ) as mock_processor_cls:
            instance = mock_processor_cls.return_value
            instance.parse_document = AsyncMock(return_value=MagicMock())
            instance.create_blocks = AsyncMock(return_value=expected)

            container = await parser.parse_to_blocks(
                "# Title",
                caption_map={"Image_1": "data:image/png;base64,URI"},
                name="report.md",
            )

        assert container.blocks[0].data == {"uri": "data:image/png;base64,URI"}


class TestApplyCaptionMap:
    def test_sets_uri_on_matching_image_block(self):
        logger = MagicMock()
        block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            image_metadata=ImageMetadata(captions=["Image_1"]),
            data=None,
        )
        container = BlocksContainer(blocks=[block], block_groups=[])
        _apply_caption_map(container, {"Image_1": "data:image/png;base64,xyz"}, logger)
        assert block.data == {"uri": "data:image/png;base64,xyz"}

    def test_replaces_non_dict_data_with_uri_dict(self):
        logger = MagicMock()
        block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            image_metadata=ImageMetadata(captions=["Image_1"]),
            data="legacy",
        )
        container = BlocksContainer(blocks=[block], block_groups=[])
        _apply_caption_map(container, {"Image_1": "data:image/png;base64,xyz"}, logger)
        assert block.data == {"uri": "data:image/png;base64,xyz"}

    def test_logs_when_caption_not_in_map(self):
        logger = MagicMock()
        block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            image_metadata=ImageMetadata(captions=["Image_99"]),
            data={},
        )
        container = BlocksContainer(blocks=[block], block_groups=[])
        _apply_caption_map(container, {"Image_1": "data:image/png;base64,xyz"}, logger)
        logger.warning.assert_called_once()

    def test_skips_non_image_blocks(self):
        logger = MagicMock()
        block = Block(index=0, type=BlockType.TEXT.value, data="text")
        container = BlocksContainer(blocks=[block], block_groups=[])
        _apply_caption_map(container, {"Image_1": "data:image/png;base64,xyz"}, logger)
        assert block.data == "text"

    def test_skips_image_block_with_empty_captions(self):
        logger = MagicMock()
        block = Block(
            index=0,
            type=BlockType.IMAGE.value,
            image_metadata=ImageMetadata(captions=[]),
            data=None,
        )
        container = BlocksContainer(blocks=[block], block_groups=[])
        _apply_caption_map(container, {"Image_1": "data:image/png;base64,xyz"}, logger)
        assert block.data is None

    def test_skips_image_block_without_metadata(self):
        logger = MagicMock()
        block = Block(index=0, type=BlockType.IMAGE.value, image_metadata=None, data=None)
        container = BlocksContainer(blocks=[block], block_groups=[])
        _apply_caption_map(container, {"Image_1": "data:image/png;base64,xyz"}, logger)
        assert block.data is None


class TestReferencePositionGuard:
    def test_inline_replacer_skips_reference_positions(self):
        import app.modules.parsers.markdown.docling_markdown_parser as mod

        captured: dict[str, object] = {}
        real_sub = mod.re.sub

        def intercept_sub(pattern, repl, string, count=0, flags=0):
            if r"\(" in pattern:
                captured["inline_repl"] = repl
            return real_sub(pattern, repl, string, count=count, flags=flags)

        md = "![ref][id]\n\n[id]: https://example.com/img.png"
        with patch.object(mod.re, "sub", side_effect=intercept_sub):
            _extract_and_replace_images(md)

        inline_repl = captured["inline_repl"]
        mock_match = MagicMock()
        mock_match.start.return_value = 0
        mock_match.group.return_value = "![unchanged](url.png)"

        assert inline_repl(mock_match) == "![unchanged](url.png)"


class TestDoclingMarkdownParserParseFile:
    def test_parse_file_success_returns_document(self, parser):
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        parser.converter.convert = MagicMock(return_value=mock_result)

        assert parser.parse_file("/some/file.md") is mock_result.document

    def test_parse_file_failure_includes_status_details(self, parser):
        mock_result = MagicMock()
        mock_result.status.value = "failure"
        mock_result.status.__str__ = MagicMock(return_value="failure")
        parser.converter.convert = MagicMock(return_value=mock_result)

        with pytest.raises(DocumentProcessingError, match="Failed to parse Markdown"):
            parser.parse_file("/bad.md")
