"""Every point says where its block is, so a hit is located without a blob read."""

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from tests.unit.modules.transformers.test_vectorstore_embedding_text import (
    _capturing_vectorstore,
    _index,
)


def _metadata_by_block_id(capture) -> dict:
    return {p.payload["metadata"]["blockId"]: p.payload["metadata"] for p in capture.points}


@pytest.mark.asyncio
async def test_table_rows_and_their_table_carry_their_positions():
    vs, capture = _capturing_vectorstore()
    lead = Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="Intro")
    row = Block(index=1, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=1,
                data={"row_natural_language_text": "City: Lyon", "row_number": 1})
    groups = [
        BlockGroup(index=0, type=GroupType.LIST),
        BlockGroup(index=1, type=GroupType.TABLE, data={"table_summary": "Cities"},
                   children=BlockGroupChildren.from_indices(block_indices=[1])),
    ]

    await _index(vs, BlocksContainer(blocks=[lead, row], block_groups=groups))

    meta = _metadata_by_block_id(capture)
    assert meta[row.id]["blockIndex"] == 1
    assert meta[row.id]["isBlockGroup"] is False
    assert meta[groups[1].id]["blockGroupIndex"] == 1
    assert meta[groups[1].id]["isBlockGroup"] is True
    assert "blockIndex" not in meta[groups[1].id]


@pytest.mark.asyncio
async def test_table_stored_as_a_block_is_written_as_a_block():
    vs, capture = _capturing_vectorstore()
    lead = Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="Intro")
    table = Block(index=1, type=BlockType.TABLE, format=DataFormat.JSON,
                  data={"table_summary": "Quarterly totals"})

    await _index(vs, BlocksContainer(blocks=[lead, table]))

    meta = _metadata_by_block_id(capture)[table.id]
    assert meta["isBlockGroup"] is False
    assert meta["blockIndex"] == 1


@pytest.mark.asyncio
async def test_sql_rows_and_tables_carry_their_positions():
    vs, capture = _capturing_vectorstore()
    row = Block(index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=0,
                data={"row_natural_language_text": "id: 7, name: Lyon"})
    # The vector store routes rows by this sub-type string; no enum member has it.
    row.sub_type = "sql_table"
    group = BlockGroup(index=0, type=GroupType.TABLE, sub_type=GroupSubType.SQL_TABLE,
                       data={"fqn": "db.cities", "ddl": "CREATE TABLE cities (id int)"})

    await _index(vs, BlocksContainer(blocks=[row], block_groups=[group]))

    meta = _metadata_by_block_id(capture)
    assert meta[row.id]["blockIndex"] == 0
    assert meta[group.id]["blockGroupIndex"] == 0
