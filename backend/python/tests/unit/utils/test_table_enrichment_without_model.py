"""A record with tables still indexes when no AI model is set up.

The config service below returns no AI models config, so the real ``get_llm_for_role``
raises ``LLMNotConfiguredError`` exactly as it does on an instance nobody has configured.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docling_core.types.doc.document import DoclingDocument

from app.models.blocks import (
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupType,
    TableRowMetadata,
)
from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.utils import table_enrichment
from app.utils.llm import LLMNotConfiguredError, get_llm_for_role
from app.utils.table_enrichment import enhance_tables_with_llm

_TABLE_HTML = (
    "<html><body><h1>Team</h1><table>"
    "<tr><th>Name</th><th>Role</th></tr>"
    "<tr><td>Priya</td><td>Lead</td></tr>"
    "<tr><td>Sam</td><td>Engineer</td></tr>"
    "</table></body></html>"
)


@pytest.fixture(autouse=True)
def _fresh_docling_converter():  # noqa: ANN202
    from app.modules.parsers.pdf import docling_processor

    docling_processor._get_converter.cache_clear()
    yield
    docling_processor._get_converter.cache_clear()


def _unconfigured_config_service() -> MagicMock:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={})
    return config_service


def _row_texts(container: BlocksContainer) -> list[str]:
    return [
        b.data["row_natural_language_text"]
        for b in container.blocks
        if b.type == BlockType.TABLE_ROW
        and not (b.table_row_metadata and b.table_row_metadata.is_header)
    ]


@pytest.mark.asyncio
async def test_the_config_service_stub_really_has_no_model() -> None:
    with pytest.raises(LLMNotConfiguredError):
        await get_llm_for_role(_unconfigured_config_service(), "indexing")


@pytest.mark.asyncio
async def test_block_tables_index_with_plain_text_when_no_model_is_set_up() -> None:
    container = HtmlToBlocksConverter().convert(_TABLE_HTML)
    logger = MagicMock()

    stats = await enhance_tables_with_llm(container, _unconfigured_config_service(), logger)

    (group,) = [g for g in container.block_groups if g.type == GroupType.TABLE]
    assert group.data["table_summary"] == "A table with 2 rows and columns: Name, Role."
    assert group.data["column_headers"] == ["Name", "Role"]
    assert _row_texts(container) == ["Name: Priya, Role: Lead", "Name: Sam, Role: Engineer"]
    assert stats.failed == 0
    # One line per record, not one per table or row.
    no_model_lines = [c for c in logger.info.call_args_list if "No AI model" in str(c)]
    assert len(no_model_lines) == 1


@pytest.mark.asyncio
async def test_a_header_row_names_the_columns_when_no_model_is_set_up() -> None:
    """Connector block parsers keep the header as a row and join cells with a delimiter."""
    container = HtmlToBlocksConverter().convert(_TABLE_HTML)
    (group,) = [g for g in container.block_groups if g.type == GroupType.TABLE]
    rows = [b for b in container.blocks if b.type == BlockType.TABLE_ROW]
    header = rows[0].model_copy(
        update={"data": {"cells": ["Name", "Role"]}, "table_row_metadata": TableRowMetadata(is_header=True)}
    )
    for row in rows:
        row.data["row_natural_language_text"] = "|".join(row.data["cells"])
    container.blocks.insert(rows[0].index, header)
    for i, block in enumerate(container.blocks):
        block.index = i
    group.children = BlockGroupChildren()
    for block in container.blocks:
        if block.type == BlockType.TABLE_ROW:
            group.children.add_block_index(block.index)
    group.data = {"table_summary": "", "column_headers": []}

    await enhance_tables_with_llm(container, _unconfigured_config_service(), MagicMock())

    assert group.data["column_headers"] == ["Name", "Role"]
    assert _row_texts(container) == ["Name: Priya, Role: Lead", "Name: Sam, Role: Engineer"]


@pytest.mark.asyncio
async def test_parser_row_text_is_kept_when_no_column_names_are_known() -> None:
    container = HtmlToBlocksConverter().convert(_TABLE_HTML)
    (group,) = [g for g in container.block_groups if g.type == GroupType.TABLE]
    group.data["column_headers"] = []
    group.table_metadata.column_names = []
    for block in container.blocks:
        if block.type == BlockType.TABLE_ROW:
            block.data["row_natural_language_text"] = "|".join(block.data["cells"])

    await enhance_tables_with_llm(container, _unconfigured_config_service(), MagicMock())

    assert _row_texts(container) == ["Priya|Lead", "Sam|Engineer"]
    assert group.data["table_summary"] == "A table with 2 rows and columns: unnamed columns."


@pytest.mark.asyncio
async def test_a_configured_model_that_fails_every_table_still_fails_the_record() -> None:
    container = HtmlToBlocksConverter().convert(_TABLE_HTML)
    failing = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch.object(table_enrichment, "invoke_with_count_validation_and_reflection", failing):
        with pytest.raises(RuntimeError, match="All 1 tables failed"):
            await enhance_tables_with_llm(
                container, _unconfigured_config_service(), MagicMock(), llm=MagicMock()
            )


@pytest.mark.asyncio
async def test_a_model_that_cannot_be_built_still_raises() -> None:
    container = HtmlToBlocksConverter().convert(_TABLE_HTML)
    broken = AsyncMock(side_effect=ValueError("No LLM found"))

    with patch.object(table_enrichment, "get_llm_for_role", broken):
        with pytest.raises(ValueError, match="No LLM found"):
            await enhance_tables_with_llm(container, _unconfigured_config_service(), MagicMock())


@pytest.mark.asyncio
async def test_docling_tables_index_with_plain_text_when_no_model_is_set_up() -> None:
    log = logging.getLogger("no-model-docling")
    parsed = await DoclingHtmlParser(log, None).parse(_TABLE_HTML.encode(), "t.html")
    doc = DoclingDocument.model_validate_json(parsed.raw_document)

    container = await DoclingProcessor(logger=log, config=_unconfigured_config_service()).create_blocks(doc)

    (group,) = [g for g in container.block_groups if g.type == GroupType.TABLE]
    assert group.data["table_summary"].startswith("A table with ")
    rows = _row_texts(container)
    assert any("Priya" in r and "Lead" in r for r in rows)
    assert any("Sam" in r and "Engineer" in r for r in rows)
