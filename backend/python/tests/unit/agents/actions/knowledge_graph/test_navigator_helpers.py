"""Tests for helper functions in ``app.agents.actions.knowledge_graph.navigator``."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.actions.knowledge_graph.navigator import (
    _condensed_summary,
    _node_item_to_row,
)


# ---------------------------------------------------------------------------
# _condensed_summary
# ---------------------------------------------------------------------------

class TestCondensedSummary:
    def test_star_lines_joined(self) -> None:
        block = "Name: Ticket\n* Status: Open\n* Priority: High"
        assert _condensed_summary(block) == "Status: Open, Priority: High"

    def test_single_star_line(self) -> None:
        block = "Name: Issue\n* Assignee: Alice"
        assert _condensed_summary(block) == "Assignee: Alice"

    def test_fallback_to_summary_line(self) -> None:
        block = "Name: Record\nSummary: A brief description of this item"
        assert _condensed_summary(block) == "A brief description of this item"

    def test_summary_truncated_at_150(self) -> None:
        long_summary = "x" * 200
        block = f"Name: Record\nSummary: {long_summary}"
        result = _condensed_summary(block)
        assert result is not None
        assert len(result) == 150
        assert result.endswith("...")

    def test_summary_at_150_not_truncated(self) -> None:
        exact = "x" * 150
        block = f"Name: Record\nSummary: {exact}"
        result = _condensed_summary(block)
        assert result == exact

    def test_no_star_no_summary_returns_none(self) -> None:
        block = "Name: Plain Record\nType: FILE"
        assert _condensed_summary(block) is None

    def test_empty_string(self) -> None:
        assert _condensed_summary("") is None

    def test_star_lines_take_precedence_over_summary(self) -> None:
        block = "Name: Ticket\n* Status: Open\nSummary: Should be ignored"
        assert _condensed_summary(block) == "Status: Open"


# ---------------------------------------------------------------------------
# _node_item_to_row
# ---------------------------------------------------------------------------

def _item(**kwargs: Any) -> SimpleNamespace:
    defaults = {
        "id": "n-1",
        "name": "Node",
        "nodeType": "record",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestNodeItemToRow:
    def test_basic_record(self) -> None:
        row = _node_item_to_row(_item())
        assert row.id == "n-1"
        assert row.name == "Node"
        assert row.node_type == "record"
        assert row.is_record is True

    def test_enum_node_type(self) -> None:
        enum_like = SimpleNamespace(value="folder")
        row = _node_item_to_row(_item(nodeType=enum_like))
        assert row.node_type == "folder"

    def test_size_kilobytes(self) -> None:
        row = _node_item_to_row(_item(sizeInBytes=2048))
        assert row.detail is not None
        assert "2 KB" in row.detail

    def test_size_bytes(self) -> None:
        row = _node_item_to_row(_item(sizeInBytes=512))
        assert row.detail is not None
        assert "512 B" in row.detail

    def test_indexing_status_not_completed(self) -> None:
        row = _node_item_to_row(_item(indexingStatus="PROCESSING"))
        assert row.detail is not None
        assert "PROCESSING" in row.detail
        assert row.indexing_status == "PROCESSING"

    def test_indexing_status_completed_hidden(self) -> None:
        row = _node_item_to_row(_item(indexingStatus="COMPLETED"))
        assert row.detail is None or "COMPLETED" not in (row.detail or "")

    def test_timestamps_zero_treated_as_none(self) -> None:
        row = _node_item_to_row(_item(createdAt=0, updatedAt=0))
        assert row.source_created_at is None
        assert row.source_modified_at is None

    def test_timestamps_nonzero_preserved(self) -> None:
        row = _node_item_to_row(_item(createdAt=1700000000, updatedAt=1700001000))
        assert row.source_created_at == 1700000000
        assert row.source_modified_at == 1700001000

    def test_record_type_from_recordType(self) -> None:
        row = _node_item_to_row(_item(recordType="TICKET"))
        assert row.sub_type == "TICKET"

    def test_record_type_fallback_recordGroupType(self) -> None:
        row = _node_item_to_row(_item(nodeType="recordGroup", recordGroupType="KB"))
        assert row.sub_type == "KB"

    def test_record_type_fallback_connector(self) -> None:
        row = _node_item_to_row(_item(nodeType="app", connector="JIRA"))
        assert row.sub_type == "JIRA"

    def test_web_url(self) -> None:
        row = _node_item_to_row(_item(webUrl="https://example.com"))
        assert row.web_url == "https://example.com"

    def test_no_web_url(self) -> None:
        row = _node_item_to_row(_item())
        assert row.web_url is None

    def test_size_and_status_combined(self) -> None:
        row = _node_item_to_row(_item(sizeInBytes=4096, indexingStatus="FAILED"))
        assert row.detail is not None
        assert "4 KB" in row.detail
        assert "FAILED" in row.detail
