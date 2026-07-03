"""Unit tests for gitlab common URL utilities.

Covers parse_item_id_from_url:
- Classic issues URL          …/-/issues/<id>
- work_items URL (new GitLab) …/-/work_items/<id>
- Merge-requests URL          …/-/merge_requests/<id>
- Trailing slash tolerance
- Non-numeric ID raises ValueError
- Unrecognised URL shape raises ValueError
"""
from __future__ import annotations

import pytest

from app.connectors.sources.gitlab.common.utils import parse_item_id_from_url


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
