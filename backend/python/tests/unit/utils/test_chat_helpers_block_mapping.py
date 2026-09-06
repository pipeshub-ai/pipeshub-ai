"""point_id -> block index must survive the sort.

`create_record_from_vector_metadata` rebuilds a record's blocks by scrolling
every collection the strategy resolves, then sorts them into document order.
The caller uses the returned mapping to pick a block back out
(`blocks[index]` in `get_flattened_results`), so the mapping has to describe
positions in the *sorted* list.

It used to record `enumerate(points)` before the sort, which is wrong twice:
the counter advanced past points carrying no payload, and it described arrival
order. Under a single collection the points usually arrive in block order and
the two happen to agree, which is why this went unnoticed — but a
per-connector-type strategy spreads one VRID across collections, and the
concatenation is then not in block order at all. The symptom is a citation
quoting the wrong block.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vector_db.models import ScrollResult, SearchResult

pytestmark = pytest.mark.asyncio


def _point(point_id: str, block_num: int, text: str) -> SearchResult:
    return SearchResult(
        id=point_id,
        score=1.0,
        payload={
            "page_content": text,
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockNum": [block_num],
                "blockType": "text",
                "orgId": "org-1",
            },
        },
    )


async def _rebuild(pages_by_collection: dict[str, list[SearchResult]]):
    """Run the real function against a fake vector DB and registry."""
    from app.utils.chat_helpers import create_record_from_vector_metadata

    vdb = MagicMock()
    vdb.filter_collection = AsyncMock(return_value=MagicMock())
    vdb.overwrite_payload = AsyncMock()

    async def _scroll(collection_name, scroll_filter, limit, offset=None):
        return ScrollResult(
            points=pages_by_collection.get(collection_name, []), next_offset=None
        )

    vdb.scroll = AsyncMock(side_effect=_scroll)

    registry = MagicMock()
    registry.resolve_for_query = AsyncMock(
        return_value=list(pages_by_collection.keys())
    )

    blob_store = MagicMock()
    blob_store.config_service = MagicMock()

    container_utils = MagicMock()
    container_utils.get_vector_db_service = AsyncMock(return_value=vdb)

    with patch(
        "app.containers.utils.utils.ContainerUtils", return_value=container_utils
    ):
        return await create_record_from_vector_metadata(
            {"recordId": "rec-1", "recordName": "doc"},
            "org-1",
            "vr-1",
            blob_store,
            collection_registry=registry,
        )


def _assert_mapping_selects_the_right_block(record, mapping, expected: dict[str, str]):
    """The invariant the caller depends on: blocks[mapping[point_id]] is the
    block that point produced."""
    blocks = record["block_containers"]["blocks"]
    for point_id, text in expected.items():
        assert point_id in mapping, f"no mapping for {point_id}"
        assert blocks[mapping[point_id]]["data"] == text, (
            f"{point_id} resolved to the wrong block"
        )


class TestMappingSurvivesTheSort:
    async def test_points_arriving_out_of_block_order_still_resolve(self):
        """Two collections, concatenated so arrival order and block order
        disagree — the shape a per-connector-type strategy produces."""
        record, mapping = await _rebuild(
            {
                "slack_records": [_point("p-late", 5, "block five")],
                "drive_records": [_point("p-early", 1, "block one")],
            }
        )

        _assert_mapping_selects_the_right_block(
            record, mapping, {"p-late": "block five", "p-early": "block one"}
        )

    async def test_a_payloadless_point_does_not_shift_the_others(self):
        """The second defect: the old counter advanced on points that produced
        no block, so every later mapping was off by one."""
        blank = SearchResult(id="p-blank", score=1.0, payload=None)
        record, mapping = await _rebuild(
            {
                "records": [
                    blank,
                    _point("p-a", 0, "block zero"),
                    _point("p-b", 1, "block one"),
                ]
            }
        )

        assert "p-blank" not in mapping
        _assert_mapping_selects_the_right_block(
            record, mapping, {"p-a": "block zero", "p-b": "block one"}
        )

    async def test_blocks_are_renumbered_contiguously(self):
        """The sort also rewrites each block's own index; the mapping must
        agree with that renumbering, not with the original blockNum."""
        record, mapping = await _rebuild(
            {
                "a_records": [_point("p-9", 9, "nine")],
                "b_records": [_point("p-3", 3, "three")],
            }
        )

        blocks = record["block_containers"]["blocks"]
        assert [b["index"] for b in blocks] == [0, 1]
        assert mapping["p-3"] == 0
        assert mapping["p-9"] == 1

    async def test_already_ordered_points_are_unaffected(self):
        """The single-collection case that always worked must keep working."""
        record, mapping = await _rebuild(
            {"records": [_point("p-0", 0, "zero"), _point("p-1", 1, "one")]}
        )

        _assert_mapping_selects_the_right_block(
            record, mapping, {"p-0": "zero", "p-1": "one"}
        )
