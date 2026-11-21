"""The upgrade smoke's comparison: a drop is reported, growth is not."""

from __future__ import annotations

import pytest

from helper.upgrade_snapshot import diff

pytestmark = pytest.mark.unit


def test_a_drop_is_reported_and_growth_is_not() -> None:
    before = {"users": 3, "knowledge_bases": 2, "conversations": 0}
    assert diff(before, {"users": 4, "knowledge_bases": 2, "conversations": 0}) == []
    assert diff(before, {"users": 2, "knowledge_bases": 2, "conversations": 0}) == ["users: was 3, now 2"]


def test_a_missing_key_is_reported() -> None:
    assert diff({"users": 3}, {}) == ["users: was 3, now missing"]
