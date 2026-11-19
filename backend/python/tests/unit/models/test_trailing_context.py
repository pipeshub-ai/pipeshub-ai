"""``BlocksContainer.trailing_context``: nullable, absent reads as ``None``, and an old stored container still validates."""
from __future__ import annotations

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.utils.trailing_blocks import addressable_blocks


def _block(index: int) -> Block:
    return Block(index=index, type=BlockType.TEXT, format=DataFormat.TXT, data=f"b{index}")


def test_a_container_stored_without_the_field_validates_and_reads_none() -> None:
    old = {"blocks": [_block(0).model_dump()], "block_groups": []}
    container = BlocksContainer.model_validate(old)
    assert container.trailing_context is None


def test_the_field_round_trips_and_an_explicit_null_validates() -> None:
    container = BlocksContainer(blocks=[_block(0)], block_groups=[], trailing_context=[_block(1)])
    again = BlocksContainer.model_validate(container.model_dump())
    assert [b.data for b in again.trailing_context or []] == ["b1"]
    assert BlocksContainer.model_validate({"blocks": [], "block_groups": [], "trailing_context": None}).trailing_context is None


def test_a_local_index_names_the_blocks_then_the_trailing_context() -> None:
    containers = {"blocks": ["a", "b"], "trailing_context": ["c"]}
    assert addressable_blocks(containers) == ["a", "b", "c"]


def test_without_trailing_context_the_very_blocks_list_is_returned() -> None:
    blocks = ["a", "b"]
    assert addressable_blocks({"blocks": blocks}) is blocks
    assert addressable_blocks({"blocks": blocks, "trailing_context": None}) is blocks
    assert addressable_blocks({"blocks": blocks, "trailing_context": []}) is blocks
    assert addressable_blocks(None) == []
