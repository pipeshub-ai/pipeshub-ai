"""Unit tests for CSVParser.parse_to_blocks_lightweight."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlocksContainer, BlockType, GroupType
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


# Excel on Windows saves "CSV" as Windows-1252: 0x80 is the euro sign and
# 0x93/0x94 are curly quotes, which Latin-1 would read as invisible control characters.
DECODING_CASES = [
    pytest.param(
        b"item,note\nWidget,\x80 5\nQuote,\x93best\x94 \x96 top\n",
        [["item", "note"], ["Widget", "€ 5"], ["Quote", "“best” – top"]],
        id="windows-1252",
    ),
    # 0x81 is undefined in Windows-1252; the file must still be read, and its
    # other characters must keep their Windows-1252 meaning.
    pytest.param(
        b"name,note\ncaf\xe9,\x81 \x80\n",
        [["name", "note"], ["café", "� €"]],
        id="undefined-windows-1252-byte",
    ),
    pytest.param(
        "name,city\nZoë,Zürich €\n".encode(),
        [["name", "city"], ["Zoë", "Zürich €"]],
        id="utf-8",
    ),
    pytest.param(
        b"\xef\xbb\xbf" + "name,city\nZoë,Zürich €\n".encode(),
        [["name", "city"], ["Zoë", "Zürich €"]],
        id="utf-8-with-bom",
    ),
]


def _capture_rows(parser: CSVParser) -> list:
    captured: list = []
    real_read = parser.read_raw_rows

    def _read(stream: io.StringIO) -> list:
        rows = real_read(stream)
        captured.append(rows)
        return rows

    parser.read_raw_rows = _read
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "expected_rows"), DECODING_CASES)
async def test_parse_to_blocks_lightweight_decodes_text(
    parser: CSVParser, content: bytes, expected_rows: list
) -> None:
    captured = _capture_rows(parser)
    await parser.parse_to_blocks_lightweight(content, max_rows=500)
    assert captured == [expected_rows]


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "expected_rows"), DECODING_CASES)
async def test_parse_decodes_text(parser: CSVParser, content: bytes, expected_rows: list) -> None:
    captured = _capture_rows(parser)
    parser.get_blocks_from_csv_with_multiple_tables = AsyncMock(
        return_value=BlocksContainer(blocks=[], block_groups=[])
    )
    with patch(
        "app.modules.parsers.csv.csv_parser.get_llm_for_role",
        new_callable=AsyncMock,
        return_value=(MagicMock(), {}),
    ):
        await parser.parse(content, "sheet.csv")
    assert captured == [expected_rows]
