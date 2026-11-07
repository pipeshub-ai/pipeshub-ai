"""A hit on a chunk point brings the whole chunk it was embedded from."""

from __future__ import annotations

import pytest

from app.models.blocks import BlockType
from app.modules.retrieval.context.neighbours import expand_neighbours
from app.modules.retrieval.context.units import NEIGHBOUR_OF_KEY
from app.utils.chat_helpers import SPAN_BLOCK_INDICES_KEY
from tests.unit.utils.test_chat_helpers_citations import (
    InMemoryBlobStore,
    blob,
    flatten,
    graph_record,
    hit,
    text,
)

_TEXT = BlockType.TEXT.value


def _record(vrid: str, types: list[str]) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [{"type": t, "data": f"{vrid} block {i}"} for i, t in enumerate(types)],
            "block_groups": [],
        },
    }


def _text(vrid: str, index: int) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "block_type": _TEXT,
            "content": f"{vrid} block {index}", "metadata": {}}


def test_the_rest_of_the_chunk_joins_the_hit_then_one_block_either_side() -> None:
    records = {"r": _record("r", [_TEXT] * 8)}
    unit = {**_text("r", 2), SPAN_BLOCK_INDICES_KEY: [2, 3, 4]}

    units = expand_neighbours([unit], records)

    added = [(u["block_index"], u[NEIGHBOUR_OF_KEY]) for u in units if u.get(NEIGHBOUR_OF_KEY)]
    assert added == [(3, [2]), (4, [2]), (1, [2]), (5, [2])]


def test_the_neighbour_limit_does_not_cut_the_chunk() -> None:
    records = {"r": _record("r", [_TEXT] * 8)}
    unit = {**_text("r", 2), SPAN_BLOCK_INDICES_KEY: [2, 3, 4]}

    units = expand_neighbours([unit], records, limit=0)

    assert sorted(u["block_index"] for u in units) == [2, 3, 4]


def test_a_unit_without_a_span_keeps_plain_neighbours() -> None:
    records = {"r": _record("r", [_TEXT] * 4)}
    units = expand_neighbours([_text("r", 1)], records)
    assert sorted(u["block_index"] for u in units) == [0, 1, 2]


@pytest.mark.asyncio
async def test_a_chunk_points_blocks_reach_the_unit() -> None:
    store = InMemoryBlobStore({"v1": blob("v1", [text(i, f"Para {i}", id=f"b{i}") for i in range(3)])})
    point = hit("v1", 0, blockId="b0", content="Para 0\nPara 1\nPara 2",
                spanBlockIds=["b0", "b1", "b2"], spanBlockIndices=[0, 1, 2])

    (unit,), _ = await flatten(store, [point], {"v1": graph_record("r1")})

    assert (unit["block_index"], unit["content"], unit[SPAN_BLOCK_INDICES_KEY]) == (0, "Para 0", [0, 1, 2])


@pytest.mark.asyncio
async def test_a_kept_chunk_point_follows_its_blocks_after_an_insert_above() -> None:
    """A reconciled update keeps an unchanged chunk's point, written when its
    blocks sat one place higher; the blocks are found by id."""
    blocks = [text(0, "New opening", id="n0"), *(text(i + 1, f"Para {i}", id=f"b{i}") for i in range(3))]
    store = InMemoryBlobStore(
        {"v1": blob("v1", blocks)},
        recon={"v1": {"block_id_to_index": {"b0": 1}, "hash_to_block_ids": {}}},
    )
    point = hit("v1", 0, blockId="b0", spanBlockIds=["b0", "b1", "b2"], spanBlockIndices=[0, 1, 2])

    (unit,), _ = await flatten(store, [point], {"v1": graph_record("r1")})

    assert (unit["block_index"], unit[SPAN_BLOCK_INDICES_KEY]) == (1, [1, 2, 3])


@pytest.mark.asyncio
async def test_a_plain_point_carries_no_span() -> None:
    store = InMemoryBlobStore({"v1": blob("v1", [text(0, "Para 0", id="b0")])})
    (unit,), _ = await flatten(store, [hit("v1", 0, blockId="b0")], {"v1": graph_record("r1")})
    assert SPAN_BLOCK_INDICES_KEY not in unit
