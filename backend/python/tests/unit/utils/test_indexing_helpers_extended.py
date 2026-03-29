"""
Extended tests for app/utils/indexing_helpers.py targeting uncovered lines:
126-166 (get_rows_text) and 212-264 (process_table_pymupdf).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===================================================================
# get_rows_text
# ===================================================================


class TestGetRowsText:
    """Cover get_rows_text including column header branching and LLM parsing."""

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_with_column_headers_and_parsed_response(
        self, mock_get_llm, mock_invoke
    ):
        """Lines 130-131, 159-160: column headers present, LLM returns descriptions."""
        from app.utils.indexing_helpers import get_rows_text

        mock_get_llm.return_value = (MagicMock(), None)

        parsed = MagicMock()
        parsed.descriptions = ["Row 1 is about X", "Row 2 is about Y"]
        mock_invoke.return_value = parsed

        table_data = {
            "grid": [
                ["Name", "Age"],
                ["Alice", "30"],
                ["Bob", "25"],
            ]
        }

        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "People table", ["Name", "Age"]
        )
        assert len(descriptions) == 2
        assert descriptions[0] == "Row 1 is about X"
        assert len(rows) == 2

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_without_column_headers(self, mock_get_llm, mock_invoke):
        """Lines 132-133: no column headers, use all rows."""
        from app.utils.indexing_helpers import get_rows_text

        mock_get_llm.return_value = (MagicMock(), None)
        mock_invoke.return_value = None  # LLM returns None

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
        # Default descriptions are string representations
        assert "Alice" in descriptions[0]

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_with_dict_cells(self, mock_get_llm, mock_invoke):
        """Lines 137-139: cells are dicts with 'text' key."""
        from app.utils.indexing_helpers import get_rows_text

        mock_get_llm.return_value = (MagicMock(), None)
        mock_invoke.return_value = None

        table_data = {
            "grid": [
                ["Name", "Age"],
                [{"text": "Alice"}, {"text": "30"}],
            ]
        }

        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "People table", ["Name", "Age"]
        )
        assert len(rows) == 1
        assert "Alice" in descriptions[0]

    @pytest.mark.asyncio
    async def test_empty_grid(self):
        """Line 166: no 'grid' key returns empty."""
        from app.utils.indexing_helpers import get_rows_text

        descriptions, rows = await get_rows_text(
            MagicMock(), {}, "summary", []
        )
        assert descriptions == []
        assert rows == []

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_parsed_response_empty_descriptions(self, mock_get_llm, mock_invoke):
        """Lines 159: parsed_response is not None but descriptions is empty list."""
        from app.utils.indexing_helpers import get_rows_text

        mock_get_llm.return_value = (MagicMock(), None)
        parsed = MagicMock()
        parsed.descriptions = []  # Empty
        mock_invoke.return_value = parsed

        table_data = {"grid": [["A"], ["B"]]}

        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "summary", []
        )
        # Should keep default string descriptions
        assert len(descriptions) == 2

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_exception_propagates(self, mock_get_llm):
        """Lines 163-164: exception is re-raised."""
        from app.utils.indexing_helpers import get_rows_text

        mock_get_llm.side_effect = Exception("LLM error")

        table_data = {"grid": [["A"], ["B"]]}

        with pytest.raises(Exception, match="LLM error"):
            await get_rows_text(MagicMock(), table_data, "summary", ["Col"])

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.invoke_with_structured_output_and_reflection")
    @patch("app.utils.indexing_helpers.get_llm")
    async def test_more_columns_than_headers(self, mock_get_llm, mock_invoke):
        """Lines 137: more columns than headers => uses Column_N fallback."""
        from app.utils.indexing_helpers import get_rows_text

        mock_get_llm.return_value = (MagicMock(), None)
        mock_invoke.return_value = None

        table_data = {
            "grid": [
                ["Name", "Age", "Extra"],
                ["Alice", "30", "abc"],
            ]
        }

        descriptions, rows = await get_rows_text(
            MagicMock(), table_data, "summary", ["Name"]  # Only 1 header for 3 cols
        )
        assert len(rows) == 1
        # The third column should use "Column_3" fallback
        assert "Column_2" in descriptions[0] or "Column_3" in descriptions[0]


# ===================================================================
# process_table_pymupdf
# ===================================================================


class TestProcessTablePymupdf:
    """Cover process_table_pymupdf (lines 212-264)."""

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_rows_text")
    @patch("app.utils.indexing_helpers.get_table_summary_n_headers")
    async def test_process_single_table(self, mock_summary, mock_rows_text):
        from app.utils.indexing_helpers import process_table_pymupdf

        # Mock table summary
        summary_response = MagicMock()
        summary_response.summary = "Sales data table"
        summary_response.headers = ["Product", "Revenue"]
        mock_summary.return_value = summary_response

        # Mock rows text
        mock_rows_text.return_value = (
            ["Product A had $100 revenue", "Product B had $200 revenue"],
            [["Product A", "100"], ["Product B", "200"]],
        )

        # Mock page
        mock_rect = MagicMock()
        mock_rect.width = 612.0
        mock_rect.height = 792.0

        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["Product", "Revenue"],
            ["Product A", "100"],
            ["Product B", "200"],
        ]
        mock_table.bbox = (50.0, 100.0, 500.0, 300.0)
        mock_table.col_count = 2

        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]

        mock_page = MagicMock()
        mock_page.rect = mock_rect
        mock_page.find_tables.return_value = mock_table_finder

        result = {"tables": [], "blocks": []}

        await process_table_pymupdf(mock_page, result, MagicMock(), page_number=1)

        assert len(result["tables"]) == 1
        assert len(result["blocks"]) == 2
        assert result["tables"][0].data["table_summary"] == "Sales data table"
        assert result["blocks"][0].data["row_number"] == 1
        assert result["blocks"][1].data["row_number"] == 2

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_rows_text")
    @patch("app.utils.indexing_helpers.get_table_summary_n_headers")
    async def test_process_table_no_col_count(self, mock_summary, mock_rows_text):
        """Line 226: table.col_count is None => falls back to len(first row)."""
        from app.utils.indexing_helpers import process_table_pymupdf

        summary_response = MagicMock()
        summary_response.summary = "Data table"
        summary_response.headers = ["A", "B"]
        mock_summary.return_value = summary_response

        mock_rows_text.return_value = (
            ["Row 1 text"],
            [["val1", "val2"]],
        )

        mock_rect = MagicMock()
        mock_rect.width = 612.0
        mock_rect.height = 792.0

        mock_table = MagicMock()
        mock_table.extract.return_value = [
            ["A", "B"],
            ["val1", "val2"],
        ]
        mock_table.bbox = (0, 0, 612, 792)
        mock_table.col_count = 0  # Falsy => fallback

        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]

        mock_page = MagicMock()
        mock_page.rect = mock_rect
        mock_page.find_tables.return_value = mock_table_finder

        result = {"tables": [], "blocks": []}

        await process_table_pymupdf(mock_page, result, MagicMock(), page_number=1)

        assert len(result["tables"]) == 1
        table_group = result["tables"][0]
        assert table_group.table_metadata.num_of_cols == 2

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_rows_text")
    @patch("app.utils.indexing_helpers.get_table_summary_n_headers")
    async def test_process_table_no_summary_response(self, mock_summary, mock_rows_text):
        """Lines 219-220: summary response is None."""
        from app.utils.indexing_helpers import process_table_pymupdf

        mock_summary.return_value = None

        mock_rows_text.return_value = (
            ["Row text"],
            [["val1"]],
        )

        mock_rect = MagicMock()
        mock_rect.width = 612.0
        mock_rect.height = 792.0

        mock_table = MagicMock()
        mock_table.extract.return_value = [["val1"]]
        mock_table.bbox = (0, 0, 100, 100)
        mock_table.col_count = 1

        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]

        mock_page = MagicMock()
        mock_page.rect = mock_rect
        mock_page.find_tables.return_value = mock_table_finder

        result = {"tables": [], "blocks": []}

        await process_table_pymupdf(mock_page, result, MagicMock(), page_number=1)

        assert len(result["tables"]) == 1
        assert result["tables"][0].data["table_summary"] == ""

    @pytest.mark.asyncio
    @patch("app.utils.indexing_helpers.get_rows_text")
    @patch("app.utils.indexing_helpers.get_table_summary_n_headers")
    async def test_more_rows_than_descriptions(self, mock_summary, mock_rows_text):
        """Line 255: row index exceeds descriptions length => empty string."""
        from app.utils.indexing_helpers import process_table_pymupdf

        summary_response = MagicMock()
        summary_response.summary = "Table"
        summary_response.headers = ["A"]
        mock_summary.return_value = summary_response

        # Fewer descriptions than rows
        mock_rows_text.return_value = (
            ["Row 1 text"],  # Only 1 description
            [["val1"], ["val2"]],  # But 2 rows
        )

        mock_rect = MagicMock()
        mock_rect.width = 612.0
        mock_rect.height = 792.0

        mock_table = MagicMock()
        mock_table.extract.return_value = [["A"], ["val1"], ["val2"]]
        mock_table.bbox = (0, 0, 100, 100)
        mock_table.col_count = 1

        mock_table_finder = MagicMock()
        mock_table_finder.tables = [mock_table]

        mock_page = MagicMock()
        mock_page.rect = mock_rect
        mock_page.find_tables.return_value = mock_table_finder

        result = {"tables": [], "blocks": []}

        await process_table_pymupdf(mock_page, result, MagicMock(), page_number=1)

        assert len(result["blocks"]) == 2
        # Second block should have empty text since descriptions only has 1 entry
        assert result["blocks"][1].data["row_natural_language_text"] == ""
