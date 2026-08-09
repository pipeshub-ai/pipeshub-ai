"""Unit tests for gitlab common utilities.

Covers parse_item_id_from_url and wire_block_group_parent_children.
"""
from __future__ import annotations

import pytest

from app.connectors.sources.gitlab.common.utils import (
    parse_item_id_from_url,
    wire_block_group_parent_children,
)
from app.models.blocks import (
    BlockGroup,
    BlockGroupChildren,
    GroupType,
    IndexRange,
)


class TestParseItemIdFromUrl:
    def test_classic_issues_url(self) -> None:
        assert parse_item_id_from_url("https://gitlab.com/ns/proj/-/issues/42") == 42

    def test_work_items_url(self) -> None:
        assert parse_item_id_from_url("https://gitlab.com/ns/proj/-/work_items/99") == 99

    def test_merge_requests_url(self) -> None:
        assert parse_item_id_from_url("https://gitlab.com/ns/proj/-/merge_requests/7") == 7

    def test_trailing_slash_is_tolerated(self) -> None:
        assert parse_item_id_from_url("https://gitlab.com/ns/proj/-/issues/5/") == 5

    def test_subgroup_issues_url(self) -> None:
        url = "https://gitlab.example.com/group/subgroup/proj/-/issues/100"
        assert parse_item_id_from_url(url) == 100

    def test_non_numeric_id_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse item ID"):
            parse_item_id_from_url("https://gitlab.com/ns/proj/-/issues/abc")

    def test_unrecognised_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse item ID"):
            parse_item_id_from_url("https://gitlab.com/ns/proj/-/commits/deadbeef")

    def test_empty_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse item ID"):
            parse_item_id_from_url("")


class TestWireBlockGroupParentChildren:
    def test_wires_comment_groups_onto_parent(self) -> None:
        parent = BlockGroup(index=0, name="issue", type=GroupType.TEXT_SECTION.value)
        c1 = BlockGroup(
            index=1, parent_index=0, name="c1", type=GroupType.TEXT_SECTION.value
        )
        c2 = BlockGroup(
            index=2, parent_index=0, name="c2", type=GroupType.TEXT_SECTION.value
        )
        wire_block_group_parent_children([parent, c1, c2])
        assert parent.children is not None
        assert parent.children.block_group_ranges == [IndexRange(start=1, end=2)]

    def test_preserves_existing_block_ranges(self) -> None:
        parent = BlockGroup(
            index=0,
            name="commits",
            type=GroupType.COMMITS.value,
            children=BlockGroupChildren(block_ranges=[IndexRange(start=0, end=2)]),
        )
        child = BlockGroup(
            index=1, parent_index=0, name="c1", type=GroupType.TEXT_SECTION.value
        )
        wire_block_group_parent_children([parent, child])
        assert parent.children is not None
        assert parent.children.block_ranges == [IndexRange(start=0, end=2)]
        assert parent.children.block_group_ranges == [IndexRange(start=1, end=1)]

    def test_noop_when_no_parent_links(self) -> None:
        parent = BlockGroup(index=0, name="issue", type=GroupType.TEXT_SECTION.value)
        orphan = BlockGroup(index=1, name="file", type=GroupType.FULL_CODE_PATCH.value)
        wire_block_group_parent_children([parent, orphan])
        assert parent.children is None
