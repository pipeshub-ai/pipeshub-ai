"""Unit tests for CSVParser.parse_to_blocks_lightweight."""

from unittest.mock import MagicMock

import pytest

from app.models.blocks import BlockType, GroupType
from app.modules.parsers.csv.csv_parser import CSVParser


@pytest.fixture
def parser():
    return CSVParser(config_service=MagicMock())


@pytest.mark.asyncio
async def test_parse_to_blocks_lightweight_happy_path(parser):
    content = b"name,amount\nAlice,10\nBob,20\n"
    container = await parser.parse_to_blocks_lightweight(content, max_rows=500)

    assert len(container.block_groups) == 1
    assert container.block_groups[0].type == GroupType.TABLE
    assert len(container.blocks) == 2
    assert all(b.type == BlockType.TABLE_ROW for b in container.blocks)
    headers = container.block_groups[0].data["column_headers"]
    assert headers == ["name", "amount"]


@pytest.mark.asyncio
async def test_parse_to_blocks_lightweight_respects_max_rows(parser):
    rows = ["col1,col2"] + [f"v{i},w{i}" for i in range(100)]
    content = "\n".join(rows).encode("utf-8")
    container = await parser.parse_to_blocks_lightweight(content, max_rows=10)

    assert len(container.blocks) == 10


@pytest.mark.asyncio
async def test_parse_to_blocks_lightweight_empty_file(parser):
    container = await parser.parse_to_blocks_lightweight(b"", max_rows=500)
    assert container.blocks == []
    assert container.block_groups == []


@pytest.mark.asyncio
async def test_parse_to_blocks_lightweight_latin1_encoding(parser):
    # Non-UTF8 content with a latin-1 character
    content = "name,note\nAlice,café\n".encode("latin-1")
    container = await parser.parse_to_blocks_lightweight(content, max_rows=500)
    assert len(container.blocks) == 1
    text = container.blocks[0].data["row_natural_language_text"]
    assert "Alice" in text


@pytest.mark.asyncio
async def test_parse_to_blocks_lightweight_tsv_delimiter():
    tsv_parser = CSVParser(config_service=MagicMock(), delimiter="\t")
    content = b"a\tb\n1\t2\n3\t4\n"
    container = await tsv_parser.parse_to_blocks_lightweight(content, max_rows=500)
    assert len(container.blocks) == 2
    assert container.block_groups[0].data["column_headers"] == ["a", "b"]
