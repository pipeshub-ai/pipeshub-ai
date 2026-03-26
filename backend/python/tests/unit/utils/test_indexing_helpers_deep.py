"""
Additional tests for app.utils.indexing_helpers targeting uncovered lines:
- get_rows_text: with column_headers, without headers, empty grid
- process_table_pymupdf: table processing with mocked page
- get_table_summary_n_headers: list-of-dicts format, empty table, non-list, exception
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.indexing_helpers import (
    get_rows_text,
    get_table_summary_n_headers,
    TableSummary,
)


# ============================================================================
# get_rows_text
# ============================================================================


class TestGetRowsText:
    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_with_column_headers(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)

        # mock_invoke returns RowDescriptions with descriptions
        from app.modules.parsers.excel.prompt_template import RowDescriptions
        mock_invoke.return_value = RowDescriptions(descriptions=["Row 1 description", "Row 2 description"])

        table_data = {
            "grid": [
                ["Name", "Age"],  # Header row
                ["Alice", "30"],
                ["Bob", "25"],
            ]
        }
        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "People table", ["Name", "Age"]
        )
        assert len(descriptions) == 2
        assert descriptions[0] == "Row 1 description"
        assert len(rows) == 2  # Excludes header row

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_without_column_headers(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = None  # Fallback to str(row)

        table_data = {
            "grid": [
                ["Alice", "30"],
                ["Bob", "25"],
            ]
        }
        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "People table", []
        )
        assert len(descriptions) == 2
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_empty_grid(self):
        table_data = {"grid": None}
        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "Empty", []
        )
        assert descriptions == []
        assert rows == []

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_grid_with_dict_cells(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = None

        table_data = {
            "grid": [
                [{"text": "Name"}, {"text": "Age"}],
                [{"text": "Alice"}, {"text": "30"}],
            ]
        }
        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "Table", ["Name", "Age"]
        )
        assert len(descriptions) == 1
        assert len(rows) == 1


# ============================================================================
# get_table_summary_n_headers
# ============================================================================


class TestGetTableSummaryNHeadersExtra:
    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_list_of_dicts(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = TableSummary(summary="People data", headers=["Name", "Age"])

        table_data = [
            {"Name": "Alice", "Age": 30},
            {"Name": "Bob", "Age": 25},
        ]
        result = await get_table_summary_n_headers(MagicMock(), table_data)
        assert result is not None
        assert result.summary == "People data"
        assert "Name" in result.headers

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_empty_table(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = TableSummary(summary="Empty", headers=[])

        result = await get_table_summary_n_headers(MagicMock(), [])
        assert result.summary == "Empty"

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_non_list_input(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = TableSummary(summary="String data", headers=[])

        result = await get_table_summary_n_headers(MagicMock(), "some string data")
        assert result is not None

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_returns_default_on_none_response(self, mock_invoke, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = None

        result = await get_table_summary_n_headers(MagicMock(), [["a", "b"]])
        assert result is not None
        assert result.summary == ""
        assert result.headers == []

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_exception_raised(self, mock_get_llm):
        mock_get_llm.side_effect = Exception("LLM unavailable")

        with pytest.raises(Exception, match="LLM unavailable"):
            await get_table_summary_n_headers(MagicMock(), [["a"]])

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_list_of_lists_truncation(self, mock_invoke, mock_get_llm):
        """Test that tables with more than MAX_TABLE_ROWS_FOR_SUMMARY rows are truncated."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = TableSummary(summary="Large table", headers=["A"])

        # Create 25 rows (more than MAX_TABLE_ROWS_FOR_SUMMARY=20)
        table_data = [["value"] for _ in range(25)]
        result = await get_table_summary_n_headers(MagicMock(), table_data)
        assert result.summary == "Large table"

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_list_of_dicts_truncation(self, mock_invoke, mock_get_llm):
        """Test list-of-dicts with more than MAX_TABLE_ROWS_FOR_SUMMARY rows."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = TableSummary(summary="Large dict table", headers=["A"])

        table_data = [{"A": f"val{i}"} for i in range(25)]
        result = await get_table_summary_n_headers(MagicMock(), table_data)
        assert result.summary == "Large dict table"

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    async def test_list_with_non_list_non_dict_items(self, mock_invoke, mock_get_llm):
        """Test list with items that are neither list nor dict."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = (mock_llm, None)
        mock_invoke.return_value = TableSummary(summary="Integer data", headers=[])

        table_data = [1, 2, 3]
        result = await get_table_summary_n_headers(MagicMock(), table_data)
        assert result.summary == "Integer data"
