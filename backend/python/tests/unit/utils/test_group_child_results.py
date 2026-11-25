"""Tests for app.utils.chat_helpers.group_child_results.

A code block and a code group both carry the label "code", so the function
decides by shape: only a group's content is a ``(summary, children)`` tuple.
None means "treat this as a leaf"; [] means "a group that contributed
nothing" and must stay distinct from None.
"""

from typing import Any

import pytest

from app.utils.chat_helpers import group_child_results


class TestNotAGroup:
    """Anything that is not a two-item tuple is a leaf, so the result is None."""

    @pytest.mark.parametrize(
        "doc",
        [
            pytest.param({}, id="no-content"),
            pytest.param({"content": "code source"}, id="string-is-not-a-group"),
            pytest.param({"content": ("summary",)}, id="one-item-tuple"),
            pytest.param(
                {"content": ("summary", "children", "extra")}, id="three-item-tuple"
            ),
            pytest.param({"content": ["summary", ["a"]]}, id="list-is-not-a-tuple"),
        ],
    )
    def test_returns_none(self, doc: dict[str, Any]) -> None:
        assert group_child_results(doc) is None


class TestGroup:
    """A two-item tuple is a group; its children come back as a list."""

    def test_returns_the_same_children_list(self) -> None:
        children = ["a", "b"]
        result = group_child_results({"content": ("summary", children)})
        assert result == ["a", "b"]
        assert result is children

    def test_empty_children_stay_an_empty_group_not_a_leaf(self) -> None:
        result = group_child_results({"content": ("summary", [])})
        assert result is not None
        assert result == []

    @pytest.mark.parametrize(
        "children",
        [
            pytest.param(None, id="none-children"),
            pytest.param("child", id="non-list-children"),
        ],
    )
    def test_non_list_children_are_an_empty_group(self, children: object) -> None:
        result = group_child_results({"content": ("summary", children)})
        assert result is not None
        assert result == []
