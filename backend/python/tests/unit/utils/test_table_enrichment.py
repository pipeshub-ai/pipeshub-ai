"""Regression tests for row/description alignment in table enrichment.

These patch the LLM call, not ``enrich_table_grid``, so the real header-slicing and
description-alignment logic runs — patching the latter would bypass the code under test.
"""

import logging
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
from app.modules.parsers.excel.prompt_template import (
    KeyedRowDescription,
    TableEnrichment,
)
from app.utils import table_enrichment
from app.utils.table_enrichment import _enhance_one_table
from tests.support.table_model import keyed_rows

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
    """Captures the rows actually sent to the LLM and answers one description each, under each row's key."""

    def __init__(self, truncate_to: int | None = None) -> None:
        self.sent: list[list[str]] = []
        self._truncate_to = truncate_to

    async def llm(self, _llm, messages, _model, *_args, **_kwargs):
        shown = keyed_rows(messages)
        self.sent = [list(row) for _key, row in shown]
        descriptions = [
            KeyedRowDescription(key=key, description=f"DESC<{row[0]}>") for key, row in shown
        ]
        if self._truncate_to is not None:
            descriptions = descriptions[: self._truncate_to]
        return TableEnrichment(
            summary="s",
            headers=["invented_a", "invented_b"],
            header_row_count=0,
            descriptions=descriptions,
        )


async def _enrich(group, container, harness, logger=None):
    with patch.object(
        table_enrichment,
        "invoke_with_structured_output_and_reflection",
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


@pytest.mark.asyncio
async def test_a_table_whose_model_call_failed_still_gets_a_plain_summary() -> None:
    failing = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch.object(table_enrichment, "invoke_with_structured_output_and_reflection", failing):
        result = await table_enrichment.enrich_table_grid(
            MagicMock(), [["Name", "Age"], ["a", "1"], ["b", "2"]], logger=MagicMock(), known_header_row_count=1
        )
    assert result.summary == "A table with 2 rows and columns: Name, Age."
    assert result.degraded


class TestCaptionsSurviveEnrichment:
    @pytest.mark.asyncio
    async def test_sibling_tables_keep_their_captions_in_the_summary(self) -> None:
        """Two tables with the same columns, told apart only by <caption>: the LLM
        summary of the grid replaced the caption the parser stored as the summary."""
        from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter

        table = (
            "<table><caption>{}</caption><tr><th>Plan</th><th>Fee</th></tr>"
            "<tr><td>{}</td><td>{}</td></tr></table>"
        )
        container = HtmlToBlocksConverter().convert(
            table.format("Fees in 2023", "Basic", "10")
            + table.format("Fees in 2024", "Basic", "12")
        )
        groups = [g for g in container.block_groups if g.type == GroupType.TABLE]

        for group in groups:
            await _enrich(group, container, _Harness())

        assert [g.data["table_summary"] for g in groups] == ["Fees in 2023\ns", "Fees in 2024\ns"]

    @pytest.mark.asyncio
    async def test_a_table_without_a_caption_keeps_the_llm_summary(self) -> None:
        container, group = _build(header_at=0)

        await _enrich(group, container, _Harness())

        assert group.data["table_summary"] == "s"


# --- repeated or empty column headers must not drop a cell ---------------------------------


def test_row_dict_keeps_every_cell_when_a_header_repeats() -> None:
    from app.utils.table_enrichment import _row_dict

    row = ["Total assets", "", "", "$ 372,168", "$ 377,325"]
    got = _row_dict(row, ["", "", "", "At", "At"])
    assert len(got) == 5
    assert list(got.values()) == row
    assert list(got)[3:] == ["At", "At (2)"]


def test_simple_texts_keeps_both_values_of_a_repeated_header() -> None:
    from app.utils.table_enrichment import _simple_texts

    text = _simple_texts([["Total assets", "", "", "$ 372,168", "$ 377,325"]], ["", "", "", "At", "At"])[0]
    assert "372,168" in text and "377,325" in text


def test_row_dict_unique_headers_output_unchanged() -> None:
    from app.utils.table_enrichment import _row_dict

    assert _row_dict(["a", "b", "c"], ["Name", "", "Qty"]) == {"Name": "a", "Column_2": "b", "Qty": "c"}
    # a row longer than the headers still gets Column_N keys for the extra cells
    assert _row_dict(["a", "b", "c"], ["Name"]) == {"Name": "a", "Column_2": "b", "Column_3": "c"}


def test_row_dict_generated_key_never_collides_with_a_real_header() -> None:
    from app.utils.table_enrichment import _row_dict

    got = _row_dict(["x", "y"], ["", "Column_1"])
    assert len(got) == 2 and list(got.values()) == ["x", "y"]


def test_unique_column_keys_is_idempotent() -> None:
    from app.utils.indexing_helpers import unique_column_keys

    once = unique_column_keys(["At", "At", "At (2)", ""])
    assert len(set(once)) == 4
    assert unique_column_keys(once) == once


@pytest.mark.asyncio
async def test_enrich_table_grid_stores_unique_headers() -> None:
    from app.utils.table_enrichment import enrich_table_grid

    grid = [["Item", "At", "At"], ["Total assets", "$ 372,168", "$ 377,325"]]
    result = await enrich_table_grid(None, grid, logger=logging.getLogger("t"), known_header_row_count=1)
    assert result.headers == ["Item", "At", "At (2)"]
    assert "372,168" in result.descriptions[0] and "377,325" in result.descriptions[0]


def test_a_literal_suffix_header_keeps_its_name() -> None:
    from app.utils.indexing_helpers import unique_column_keys

    keys = unique_column_keys(["X", "X", "X (2)"])
    assert keys[0] == "X" and keys[2] == "X (2)"
    assert len(set(keys)) == 3
    assert unique_column_keys(keys) == keys


def test_a_generated_name_never_takes_a_literal_header() -> None:
    from app.utils.indexing_helpers import unique_column_keys

    keys = unique_column_keys(["", "Column_1", "Column_1", "Column_1 (2)"])
    assert len(set(keys)) == 4
    assert keys[1] == "Column_1" and keys[3] == "Column_1 (2)"
    assert unique_column_keys(keys) == keys


def test_unique_column_keys_property() -> None:
    import random

    from app.utils.indexing_helpers import unique_column_keys

    rnd = random.Random(7351)
    pool = ["", "A", "A (2)", "A (3)", "Column_2", "B", " "]
    for _ in range(500):
        hs = [rnd.choice(pool) for _ in range(rnd.randint(0, 8))]
        width = len(hs) + rnd.randint(0, 3)
        keys = unique_column_keys(hs, width)
        assert len(keys) == width and len(set(keys)) == width
        # a header that is unique and non-empty keeps its own name
        for i, h in enumerate(hs):
            if h and hs.count(h) == 1:
                assert keys[i] == h
        assert unique_column_keys(keys) == keys
