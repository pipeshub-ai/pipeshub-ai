from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    GroupType,
)
from app.modules.parsers.table_titles import table_titles
from app.modules.transformers.transformer import ReconciliationContext
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore


def _text(index: int, text: str, page: int = 5) -> Block:
    return Block(index=index, type=BlockType.TEXT, format="txt", data=text,
                 citation_metadata=CitationMetadata(page_number=page))


def _row(index: int, group: int = 0, page: int = 5) -> Block:
    return Block(index=index, type=BlockType.TABLE_ROW, format="json", parent_index=group,
                 data={"row_natural_language_text": "Total current assets is 1,200"},
                 citation_metadata=CitationMetadata(page_number=page))


def _table(index: int = 0, page: int = 5) -> BlockGroup:
    return BlockGroup(index=index, type=GroupType.TABLE, citation_metadata=CitationMetadata(page_number=page))


def test_the_short_lines_above_a_table_are_its_title() -> None:
    blocks = [_text(0, "x" * 400), _text(1, "Consolidated Balance Sheets"), _text(2, "(in millions)"), _row(3), _row(4)]
    assert table_titles(blocks, [_table()]) == {0: "Consolidated Balance Sheets (in millions)"}


def test_at_most_two_lines_are_taken() -> None:
    blocks = [_text(0, "Item 8"), _text(1, "Consolidated Balance Sheets"), _text(2, "(in millions)"), _row(3)]
    assert table_titles(blocks, [_table()]) == {0: "Consolidated Balance Sheets (in millions)"}


def test_a_page_number_is_skipped() -> None:
    blocks = [_text(0, "Consolidated Statements of Income"), _text(1, "52"), _row(2)]
    assert table_titles(blocks, [_table()]) == {0: "Consolidated Statements of Income"}


def test_a_long_paragraph_or_another_page_ends_the_title() -> None:
    paragraph = [_text(0, "Revenue rose because " * 20), _row(1)]
    other_page = [_text(0, "Consolidated Balance Sheets", page=4), _row(1)]
    assert table_titles(paragraph, [_table()]) == {}
    assert table_titles(other_page, [_table()]) == {}


def test_a_table_without_rows_or_page_has_no_title() -> None:
    blocks = [_text(0, "Consolidated Balance Sheets")]
    no_page = BlockGroup(index=0, type=GroupType.TABLE)
    assert table_titles(blocks, [_table()]) == {}
    assert table_titles([*blocks, _row(1)], [no_page]) == {}


@pytest.mark.asyncio
async def test_rows_and_summary_are_embedded_under_their_title() -> None:
    """An 8-K's vote table: the question "were there nominees with far more votes
    against?" matches the heading, which no row says."""
    vs = _make_vectorstore()
    vs.get_embedding_model_instance = AsyncMock(return_value=False)
    vs._create_embeddings = AsyncMock()
    row = Block(index=2, type=BlockType.TABLE_ROW, format="json", parent_index=0,
                data={"row_natural_language_text": "Richard A. Johnson received 16,105,005 votes against."},
                citation_metadata=CitationMetadata(page_number=2))
    table = BlockGroup(index=0, type=GroupType.TABLE, citation_metadata=CitationMetadata(page_number=2),
                       data={"table_summary": "Voting results for ten individuals."})
    blocks = [_text(0, "Submission of Matters to a Vote of Security Holders.", page=2),
              _text(1, "Item 5.07.", page=2), row]

    await vs.index_documents(BlocksContainer(blocks=blocks, block_groups=[table]), "org-1", "rec-1", "vr-1")

    embedded = {d.metadata["blockId"]: d.page_content for d in vs._create_embeddings.call_args[0][0]}
    title = "Submission of Matters to a Vote of Security Holders. Item 5.07."
    assert embedded[row.id] == f"{title}: Richard A. Johnson received 16,105,005 votes against."
    assert embedded[table.id] == f"{title}: Voting results for ten individuals."
    assert row.data["row_natural_language_text"].startswith("Richard")


def test_a_filtered_list_is_read_by_position_not_by_block_index() -> None:
    blocks = [_text(10, "Consolidated Balance Sheets"), _row(11, group=3)]
    assert table_titles(blocks, [_table(index=3)]) == {3: "Consolidated Balance Sheets"}


@pytest.mark.asyncio
async def test_a_reindexed_row_keeps_the_title_of_its_unchanged_heading() -> None:
    """Reconciliation hands index_documents only the changed blocks; the heading
    above a changed row is usually not one of them."""
    vs = _make_vectorstore()
    vs.get_embedding_model_instance = AsyncMock(return_value=False)
    vs._process_document_chunks = AsyncMock()
    vs.delete_blocks_by_ids = AsyncMock()
    row = _row(1)
    record = MagicMock(id="rec-1", virtual_record_id="vr-1", org_id="org-1", semantic_metadata=None,
                       block_containers=BlocksContainer(blocks=[_text(0, "Consolidated Balance Sheets"), row],
                                                        block_groups=[_table()]))
    ctx = MagicMock(record=record, reconciliation_context=ReconciliationContext(
        blocks_to_index_ids={row.id}, block_ids_to_delete=set()))

    await vs.apply(ctx)

    embedded = {d.metadata["blockId"]: d.page_content for d in vs._process_document_chunks.call_args[0][0]}
    assert embedded == {row.id: "Consolidated Balance Sheets: Total current assets is 1,200"}
