"""
Tests for indexing helper pure functions:
  - format_rows_with_index(rows) - Format rows with explicit numbering
  - generate_simple_row_text(row_data) - Generate "column: value" text
  - get_table_summary_n_headers(config, table_data) - LLM-based table summary
  - _normalize_bbox - Normalize bounding box coordinates
  - image_bytes_to_base64 - Convert image bytes to data URI
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.indexing_helpers import (
    _normalize_bbox,
    format_rows_with_index,
    generate_simple_row_text,
    get_table_summary_n_headers,
    image_bytes_to_base64,
    TableSummary,
)


# ===========================================================================
# format_rows_with_index
# ===========================================================================


class TestFormatRowsWithIndex:
    """Test format_rows_with_index function."""

    def test_empty_list(self):
        result = format_rows_with_index([])
        assert result == ""

    def test_single_row(self):
        rows = [{"name": "Alice", "age": 30}]
        result = format_rows_with_index(rows)
        assert "Row 1:" in result
        parsed = json.loads(result.split("Row 1: ")[1])
        assert parsed == {"name": "Alice", "age": 30}

    def test_multiple_rows(self):
        rows = [
            {"a": 1},
            {"b": 2},
            {"c": 3},
        ]
        result = format_rows_with_index(rows)
        lines = result.strip().split("\n")
        # Each row produces multiple lines due to indent=2
        assert "Row 1:" in result
        assert "Row 2:" in result
        assert "Row 3:" in result

    def test_numbering_starts_at_one(self):
        rows = [{"x": 1}]
        result = format_rows_with_index(rows)
        assert result.startswith("Row 1:")

    def test_json_formatting(self):
        rows = [{"key": "value"}]
        result = format_rows_with_index(rows)
        # Should contain indented JSON
        assert "  " in result  # indent=2 produces spaces

    def test_special_characters_in_values(self):
        rows = [{"msg": 'hello "world"', "path": "a\\b"}]
        result = format_rows_with_index(rows)
        assert "Row 1:" in result
        # JSON should properly escape the quotes
        json_part = result.split("Row 1: ")[1]
        parsed = json.loads(json_part)
        assert parsed["msg"] == 'hello "world"'

    def test_nested_dict_values(self):
        rows = [{"nested": {"inner": "val"}}]
        result = format_rows_with_index(rows)
        json_part = result.split("Row 1: ")[1]
        parsed = json.loads(json_part)
        assert parsed["nested"]["inner"] == "val"


# ===========================================================================
# generate_simple_row_text
# ===========================================================================


class TestGenerateSimpleRowText:
    """Test generate_simple_row_text function."""

    def test_single_column(self):
        result = generate_simple_row_text({"name": "Alice"})
        assert result == "name: Alice"

    def test_multiple_columns(self):
        result = generate_simple_row_text({"name": "Alice", "age": 30})
        assert result == "name: Alice, age: 30"

    def test_none_value(self):
        result = generate_simple_row_text({"name": None})
        assert result == "name: null"

    def test_empty_dict(self):
        result = generate_simple_row_text({})
        assert result == ""

    def test_numeric_values(self):
        result = generate_simple_row_text({"count": 42, "price": 9.99})
        assert result == "count: 42, price: 9.99"

    def test_boolean_values(self):
        result = generate_simple_row_text({"active": True, "deleted": False})
        assert result == "active: True, deleted: False"

    def test_empty_string_value(self):
        result = generate_simple_row_text({"name": ""})
        assert result == "name: "

    def test_complex_value(self):
        result = generate_simple_row_text({"data": [1, 2, 3]})
        assert result == "data: [1, 2, 3]"


# ===========================================================================
# get_table_summary_n_headers
# ===========================================================================


class TestGetTableSummaryNHeaders:
    """Test get_table_summary_n_headers with mocked LLM."""

    @pytest.mark.asyncio
    async def test_grid_format_small_table(self):
        """Grid format (list of lists) with a small table."""
        table_data = [
            ["Name", "Age", "City"],
            ["Alice", "30", "NYC"],
            ["Bob", "25", "LA"],
        ]

        mock_summary = TableSummary(
            summary="A table of people with names, ages, and cities.",
            headers=["Name", "Age", "City"],
        )

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), table_data)

        assert result is not None
        assert result.summary == "A table of people with names, ages, and cities."
        assert result.headers == ["Name", "Age", "City"]

    @pytest.mark.asyncio
    async def test_dict_format_table(self):
        """Dict format (list of dicts) with column headers as keys."""
        table_data = [
            {"Name": "Alice", "Age": 30},
            {"Name": "Bob", "Age": 25},
        ]

        mock_summary = TableSummary(summary="People table", headers=["Name", "Age"])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), table_data)

        assert result is not None
        assert result.summary == "People table"
        assert result.headers == ["Name", "Age"]

    @pytest.mark.asyncio
    async def test_empty_table(self):
        """Empty list should result in 'Empty table' text."""
        mock_summary = TableSummary(summary="Empty", headers=[])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), [])

        assert result is not None

    @pytest.mark.asyncio
    async def test_non_list_table_data(self):
        """Non-list data should be stringified."""
        mock_summary = TableSummary(summary="String table", headers=[])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), "raw string data")

        assert result is not None

    @pytest.mark.asyncio
    async def test_large_grid_truncated(self):
        """Tables with more than MAX_TABLE_ROWS_FOR_SUMMARY rows should be truncated."""
        # Create a table with 25 rows (> MAX_TABLE_ROWS_FOR_SUMMARY = 20)
        table_data = [["col1", "col2"]] + [[f"val{i}", f"val{i+1}"] for i in range(24)]

        mock_summary = TableSummary(summary="Large table", headers=["col1", "col2"])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary

                result = await get_table_summary_n_headers(MagicMock(), table_data)

        assert result is not None
        # Verify truncation message was included in the rendered prompt
        call_args = mock_invoke.call_args
        messages = call_args[0][1]
        user_content = messages[1]["content"]
        assert "more rows" in user_content

    @pytest.mark.asyncio
    async def test_large_dict_format_truncated(self):
        """Dict-format tables exceeding row limit should show truncation message."""
        table_data = [{"col": f"val{i}"} for i in range(25)]

        mock_summary = TableSummary(summary="Large dict table", headers=["col"])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), table_data)

        assert result is not None
        call_args = mock_invoke.call_args
        messages = call_args[0][1]
        user_content = messages[1]["content"]
        assert "more rows" in user_content

    @pytest.mark.asyncio
    async def test_llm_returns_none_gives_default(self):
        """If LLM parsing returns None, should return default empty TableSummary."""
        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = None
                result = await get_table_summary_n_headers(
                    MagicMock(), [["a", "b"]]
                )

        assert result is not None
        assert result.summary == ""
        assert result.headers == []

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        """Exceptions from LLM should propagate."""
        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_get_llm.side_effect = RuntimeError("LLM unavailable")

            with pytest.raises(RuntimeError, match="LLM unavailable"):
                await get_table_summary_n_headers(MagicMock(), [["a"]])

    @pytest.mark.asyncio
    async def test_grid_with_none_cells(self):
        """Grid cells that are None should be rendered as empty strings."""
        table_data = [
            ["Name", None],
            [None, "value"],
        ]

        mock_summary = TableSummary(summary="Table with nulls", headers=[])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), table_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_list_of_non_list_non_dict(self):
        """List containing non-list, non-dict items should be stringified."""
        table_data = [1, 2, 3]

        mock_summary = TableSummary(summary="Numeric list", headers=[])

        with patch("app.utils.indexing_helpers.get_llm", new_callable=AsyncMock) as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = (mock_llm, None)

            with patch(
                "app.utils.indexing_helpers.invoke_with_structured_output_and_reflection",
                new_callable=AsyncMock,
            ) as mock_invoke:
                mock_invoke.return_value = mock_summary
                result = await get_table_summary_n_headers(MagicMock(), table_data)

        assert result is not None


# ===========================================================================
# _normalize_bbox
# ===========================================================================


class TestNormalizeBbox:
    """Test bounding box normalisation."""

    def test_basic_normalization(self):
        result = _normalize_bbox((100, 200, 300, 400), 600, 800)
        assert len(result) == 4
        # Top-left
        assert result[0] == {"x": 100 / 600, "y": 200 / 800}
        # Top-right
        assert result[1] == {"x": 300 / 600, "y": 200 / 800}
        # Bottom-right
        assert result[2] == {"x": 300 / 600, "y": 400 / 800}
        # Bottom-left
        assert result[3] == {"x": 100 / 600, "y": 400 / 800}

    def test_full_page_bbox(self):
        result = _normalize_bbox((0, 0, 100, 100), 100, 100)
        assert result[0] == {"x": 0.0, "y": 0.0}
        assert result[2] == {"x": 1.0, "y": 1.0}

    def test_zero_origin(self):
        result = _normalize_bbox((0, 0, 50, 50), 100, 200)
        assert result[0] == {"x": 0.0, "y": 0.0}
        assert result[2] == {"x": 0.5, "y": 0.25}


# ===========================================================================
# image_bytes_to_base64
# ===========================================================================


class TestImageBytesToBase64:
    """Test image_bytes_to_base64 function."""

    def test_png_image(self):
        img_bytes = b"\x89PNG\r\n\x1a\n"
        result = image_bytes_to_base64(img_bytes, "png")
        assert result.startswith("data:image/png;base64,")
        # Verify the base64 portion decodes correctly
        b64_part = result.split(",")[1]
        assert base64.b64decode(b64_part) == img_bytes

    def test_jpeg_image(self):
        img_bytes = b"\xff\xd8\xff\xe0"
        result = image_bytes_to_base64(img_bytes, "jpeg")
        assert result.startswith("data:image/jpeg;base64,")

    def test_empty_bytes(self):
        result = image_bytes_to_base64(b"", "png")
        assert result.startswith("data:image/png;base64,")
        b64_part = result.split(",")[1]
        assert base64.b64decode(b64_part) == b""
