"""A shared ExcelParser must not let one document's workbook leak into another.

The parsing service and the legacy processor hold one ExcelParser for all
records, and block creation awaits LLM calls between loading the workbook and
reading it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.parsers.excel.excel_parser import ExcelParser


def test_new_document_parser_has_no_workbook_of_its_own() -> None:
    shared = ExcelParser(MagicMock(), MagicMock())
    shared.workbook = object()
    document = shared.new_document_parser()
    assert document is not shared
    assert document.workbook is None
    assert document.config_service is shared.config_service


@pytest.mark.asyncio
async def test_concurrent_parses_each_see_their_own_workbook() -> None:
    def load(self: ExcelParser, content: bytes) -> None:
        self.workbook = content.decode()

    async def create_blocks(self: ExcelParser, llm: object) -> BlocksContainer:
        seen_before = self.workbook
        await asyncio.sleep(0.01)  # the LLM round trip another parse can interleave with
        return BlocksContainer(
            blocks=[
                Block(
                    index=0,
                    type=BlockType.TEXT,
                    format=DataFormat.TXT,
                    data=f"{seen_before}->{self.workbook}",
                )
            ]
        )

    shared = ExcelParser(MagicMock(), MagicMock())
    with patch.object(ExcelParser, "load_workbook_from_binary", load), patch.object(
        ExcelParser, "create_blocks", create_blocks
    ), patch(
        "app.modules.parsers.excel.excel_parser.get_llm_for_role",
        AsyncMock(return_value=(MagicMock(), {})),
    ):
        first, second = await asyncio.gather(
            shared.parse(b"sheet-A", "a.xlsx"), shared.parse(b"sheet-B", "b.xlsx")
        )

    assert first.block_container.blocks[0].data == "sheet-A->sheet-A"
    assert second.block_container.blocks[0].data == "sheet-B->sheet-B"
