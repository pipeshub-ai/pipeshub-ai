"""One malformed member of a result set must not cost the whole result set.

`get_flattened_results` emits a `(summary, children)` pair for table-shaped
blocks and a plain string for others. `valid_group_labels` contains block types
in both camps -- `code` is grouped by label and stringly by content -- so
unpacking on the label alone raises `ValueError` and the caller turns that into
a 500 for the entire query.

Observed in production: a single `.py` file in a 576-document corpus emitted a
4,914-character `code` block. Any query whose top-k happened to include it
returned HTTP 500 with an empty result list, while queries on the same subject
that missed it returned normally.
"""

import logging

import pytest

from app.modules.retrieval.retrieval_service import (
    expand_flattened_result,
    valid_group_labels,
)
from app.models.blocks import GroupType


@pytest.fixture
def logger():
    return logging.getLogger("test_expand_flattened_result")


class TestGroupedContent:
    """A grouped block with a real (summary, children) pair expands to children."""

    def test_table_expands_to_its_children(self, logger) -> None:
        children = [{"content": "row 1"}, {"content": "row 2"}]
        result = {
            "block_type": GroupType.TABLE.value,
            "content": ("a table summary", children),
        }

        assert expand_flattened_result(result, logger) == children

    def test_group_label_expands_to_its_children(self, logger) -> None:
        children = [{"content": "item"}]
        result = {
            "block_type": GroupType.LIST.value,
            "content": ("a list summary", children),
        }

        assert expand_flattened_result(result, logger) == children

    def test_empty_children_expands_to_nothing(self, logger) -> None:
        result = {
            "block_type": GroupType.TABLE.value,
            "content": ("summary", []),
        }

        assert expand_flattened_result(result, logger) == []


class TestNonGroupedContent:
    """Anything that is not a (summary, children) pair is a leaf, not an error."""

    def test_code_block_with_string_content_does_not_raise(self, logger) -> None:
        """The regression. `code` is in valid_group_labels; its content is a str."""
        result = {
            "block_type": GroupType.CODE.value,
            "content": "# Template 3: Meeting Summary\n\ndef generate_meeting_summary():",
        }

        assert GroupType.CODE.value in valid_group_labels
        assert expand_flattened_result(result, logger) == [result]

    def test_two_character_string_is_still_a_leaf(self, logger) -> None:
        """A 2-char string unpacks without raising, which would silently shred it."""
        result = {"block_type": GroupType.CODE.value, "content": "ab"}

        assert expand_flattened_result(result, logger) == [result]

    def test_grouped_type_with_none_content_is_a_leaf(self, logger) -> None:
        result = {"block_type": GroupType.TABLE.value, "content": None}

        assert expand_flattened_result(result, logger) == [result]

    def test_wrong_length_tuple_is_a_leaf(self, logger) -> None:
        result = {
            "block_type": GroupType.TABLE.value,
            "content": ("summary", [], "unexpected third element"),
        }

        assert expand_flattened_result(result, logger) == [result]

    def test_ordinary_text_block_is_a_leaf(self, logger) -> None:
        result = {"block_type": "text", "content": "some prose"}

        assert expand_flattened_result(result, logger) == [result]

    def test_missing_block_type_is_a_leaf(self, logger) -> None:
        result = {"content": "no block type at all"}

        assert expand_flattened_result(result, logger) == [result]


class TestResultSetIsNotLost:
    """The point of the fix: one bad member does not take the others with it."""

    def test_a_string_code_block_does_not_discard_its_neighbours(self, logger) -> None:
        good_children = [{"content": "row 1"}, {"content": "row 2"}]
        flattened = [
            {"block_type": "text", "content": "prose"},
            {"block_type": GroupType.CODE.value, "content": "x" * 5000},
            {"block_type": GroupType.TABLE.value, "content": ("s", good_children)},
        ]

        final: list = []
        for result in flattened:
            final.extend(expand_flattened_result(result, logger))

        assert len(final) == 4
        assert final[0]["content"] == "prose"
        assert final[1]["content"] == "x" * 5000
        assert final[2:] == good_children
