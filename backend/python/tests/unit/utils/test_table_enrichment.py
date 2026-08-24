"""Regression tests for row/description alignment in table enrichment.

These patch the LLM call, not ``enrich_table_grid``, so the real header-slicing and
description-alignment logic runs — patching the latter would bypass the code under test.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    TableMetadata,
    TableRowMetadata,
)
from app.modules.parsers.excel.prompt_template import TableEnrichment
from app.utils import indexing_helpers, table_enrichment
from app.utils.table_enrichment import _enhance_one_table

ROWS = [("SEO", "New"), ("Onboarding", "Review"), ("BackToSchool", "Disc")]


def _row(index: int, cells: list[str], *, is_header: bool) -> Block:
    return Block(
        id=f"blk-{index}",
        index=index,
        parent_index=0,
        type=BlockType.TABLE_ROW,
        format=DataFormat.JSON,
        data={"cells": cells, "row_natural_language_text": "|".join(cells)},
        table_row_metadata=TableRowMetadata(row_number=index + 1, is_header=is_header),
    )


def _build(header_at: int | None) -> tuple[BlocksContainer, BlockGroup]:
    """Table of three data rows, with an optional header row at ``header_at``."""
    cells = [list(r) for r in ROWS]
    if header_at is not None:
        cells.insert(header_at, ["Idea", "Status"])

    blocks = [
        _row(i, c, is_header=(header_at is not None and i == header_at))
        for i, c in enumerate(cells)
    ]
    children = BlockGroupChildren()
    for b in blocks:
        children.add_block_index(b.index)

    group = BlockGroup(
        id="grp-0",
        index=0,
        type=GroupType.TABLE,
        format=DataFormat.JSON,
        data={"table_markdown": "x", "column_headers": ["Idea", "Status"]},
        table_metadata=TableMetadata(
            num_of_cols=2, has_header=header_at is not None, column_names=["Idea", "Status"]
        ),
        children=children,
    )
    return BlocksContainer(blocks=blocks, block_groups=[group]), group


class _Harness:
    """Captures the rows actually sent to the LLM and answers one description each."""

    def __init__(self, truncate_to: int | None = None) -> None:
        self.sent: list[list[str]] = []
        self._truncate_to = truncate_to

    def format_spy(self, rows):
        """Stands in for format_rows_with_index; the prompt text is irrelevant here."""
        self.sent = [list(r) for r in rows]
        return str(rows)

    async def llm(self, _llm, _messages, _model, _field, count):
        descriptions = [f"DESC<{r[0]}>" for r in self.sent]
        if self._truncate_to is not None:
            return TableEnrichment(
                summary="s",
                headers=["invented_a", "invented_b"],
                header_row_count=0,
                descriptions=descriptions[: self._truncate_to],
            )
        return TableEnrichment(
            summary="s",
            headers=["invented_a", "invented_b"],
            header_row_count=0,
            descriptions=descriptions[:count],
        )


async def _enrich(group, container, harness, logger=None):
    with patch.object(indexing_helpers, "format_rows_with_index", harness.format_spy), patch.object(
        table_enrichment,
        "invoke_with_count_validation_and_reflection",
        new=AsyncMock(side_effect=harness.llm),
    ):
        await _enhance_one_table(group, container, MagicMock(), logger or MagicMock())


def _data_texts(container: BlocksContainer) -> dict[str, str]:
    return {
        b.data["cells"][0]: b.data.get("row_natural_language_text")
        for b in container.blocks
        if not (b.table_row_metadata and b.table_row_metadata.is_header)
    }


class TestRowDescriptionAlignment:
    @pytest.mark.asyncio
    async def test_header_row_does_not_shift_descriptions(self):
        """The header used to be described too, so every data row inherited the
        previous row's text and the last row's description was dropped by zip()."""
        container, group = _build(header_at=0)
        harness = _Harness()

        await _enrich(group, container, harness)

        assert [r[0] for r in harness.sent] == ["SEO", "Onboarding", "BackToSchool"]
        assert _data_texts(container) == {
            "SEO": "DESC<SEO>",
            "Onboarding": "DESC<Onboarding>",
            "BackToSchool": "DESC<BackToSchool>",
        }

    @pytest.mark.asyncio
    async def test_header_row_supplies_the_real_column_names(self):
        """Declaring the header lets the enricher read the true names off it rather
        than inventing them from the data."""
        container, group = _build(header_at=0)

        await _enrich(group, container, _Harness())

        assert group.data["column_headers"] == ["Idea", "Status"]

    @pytest.mark.asyncio
    async def test_header_keeps_its_own_text(self):
        container, group = _build(header_at=0)

        await _enrich(group, container, _Harness())

        header = container.blocks[0]
        assert header.table_row_metadata.is_header
        assert header.data["row_natural_language_text"] == "Idea|Status"

    @pytest.mark.asyncio
    async def test_table_without_header_is_unchanged(self):
        """The no-header path must behave exactly as before — this is every CSV,
        Excel, PDF and KB upload."""
        container, group = _build(header_at=None)
        harness = _Harness()

        await _enrich(group, container, harness)

        assert [r[0] for r in harness.sent] == ["SEO", "Onboarding", "BackToSchool"]
        assert _data_texts(container) == {
            "SEO": "DESC<SEO>",
            "Onboarding": "DESC<Onboarding>",
            "BackToSchool": "DESC<BackToSchool>",
        }

    @pytest.mark.asyncio
    async def test_header_in_unexpected_position_still_aligns(self):
        """A header that is not the first row falls back to sending data rows only."""
        container, group = _build(header_at=1)
        harness = _Harness()

        await _enrich(group, container, harness)

        assert _data_texts(container) == {
            "SEO": "DESC<SEO>",
            "Onboarding": "DESC<Onboarding>",
            "BackToSchool": "DESC<BackToSchool>",
        }

    @pytest.mark.asyncio
    async def test_short_llm_response_does_not_misalign(self):
        """A short list must pad with fallback text, never pair row N with N-1's."""
        container, group = _build(header_at=0)

        await _enrich(group, container, _Harness(truncate_to=1))

        texts = _data_texts(container)
        assert texts["SEO"] == "DESC<SEO>"
        assert texts["Onboarding"] != "DESC<SEO>"
        assert texts["BackToSchool"] != "DESC<Onboarding>"
