"""Locating a vector hit's block from the point's own metadata, old and new points."""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.utils.test_chat_helpers_citations import (
    InMemoryBlobStore,
    blob,
    flatten,
    graph_record,
    hit,
    text,
)

pytestmark = pytest.mark.asyncio

RECON_DOWN = RuntimeError("reconciliation metadata unavailable")


def _table_record(*, extra_groups: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = [
        {"id": f"row-{i}", "index": i, "type": "table_row", "parent_index": 0,
         "data": {"row_natural_language_text": f"row {i}"}}
        for i in range(2)
    ]
    table = {"id": "table-0", "index": 0, "type": "table", "data": {"table_summary": "Offices"},
             "table_metadata": {"num_of_cells": 4},
             "children": {"block_ranges": [{"start": 0, "end": 1}]}}
    return blob("v1", rows, [table, *(extra_groups or [])])


class _CountingStore(InMemoryBlobStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.recon_reads = 0

    async def get_reconciliation_metadata(self, vrid: str, org_id: str) -> dict | None:
        self.recon_reads += 1
        return await super().get_reconciliation_metadata(vrid, org_id)


async def test_row_point_with_its_index_needs_no_reconciliation_read() -> None:
    store = _CountingStore({"v1": _table_record()}, recon={"v1": RECON_DOWN})
    (table,), _ = await flatten(store, [hit("v1", 1, blockId="row-1")], {"v1": graph_record("r1")})
    assert [row["block_index"] for row in table["content"][1]] == [1]
    assert store.recon_reads == 0


async def test_row_point_indexed_before_the_index_was_written_still_needs_the_read() -> None:
    store = InMemoryBlobStore(
        {"v1": _table_record()},
        recon={"v1": {"block_id_to_index": {"row-1": 1}, "hash_to_block_ids": {}}},
    )
    (table,), _ = await flatten(store, [hit("v1", blockId="row-1")], {"v1": graph_record("r1")})
    assert [row["block_index"] for row in table["content"][1]] == [1]

    down = InMemoryBlobStore({"v1": _table_record()}, recon={"v1": RECON_DOWN})
    results, _ = await flatten(down, [hit("v1", blockId="row-1")], {"v1": graph_record("r1")})
    assert results == []


async def test_group_point_is_located_by_its_group_index() -> None:
    store = _CountingStore({"v1": _table_record()}, recon={"v1": RECON_DOWN})
    (table,), _ = await flatten(
        store, [hit("v1", group=True, blockId="table-0", blockGroupIndex=0)], {"v1": graph_record("r1")},
    )
    assert table["block_group_index"] == 0
    assert [row["content"] for row in table["content"][1]] == ["row 0", "row 1"]
    assert store.recon_reads == 0


async def test_old_table_block_point_flagged_as_a_group_does_not_read_another_table() -> None:
    other = {"id": "table-1", "index": 1, "type": "table", "data": {"table_summary": "Unrelated"},
             "table_metadata": {"num_of_cells": 2}, "children": {"block_ranges": [{"start": 0, "end": 0}]}}
    record = _table_record(extra_groups=[other])
    record["block_containers"]["blocks"][1] = {
        "id": "table-block", "index": 1, "type": "table", "data": {"table_summary": "Stored as a block"},
    }
    store = InMemoryBlobStore(
        {"v1": record},
        recon={"v1": {"block_id_to_index": {"table-block": 1}, "hash_to_block_ids": {}}},
    )
    results, _ = await flatten(store, [hit("v1", group=True, blockId="table-block")], {"v1": graph_record("r1")})
    assert all(r.get("content", ("",))[0] != "Unrelated" for r in results)


async def test_record_summary_hit_does_not_read_reconciliation_metadata() -> None:
    store = _CountingStore({"v1": blob("v1", [text(0, "a")])}, recon={"v1": RECON_DOWN})
    summary = {
        "metadata": {"virtualRecordId": "v1", "blockId": "v1_summary", "isRecordSummary": True,
                     "isBlockGroup": False},
        "content": "What the record is about",
        "score": 0.8,
    }
    (result,), _ = await flatten(store, [summary], {"v1": graph_record("r1")})
    assert result["content"] == "What the record is about"
    assert store.recon_reads == 0
