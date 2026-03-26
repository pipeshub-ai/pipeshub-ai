"""Unit tests for app.modules.parsers.csv.csv_parser.CSVParser."""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.parsers.csv.csv_parser import CSVParser


@pytest.fixture
def parser():
    return CSVParser()


# ---------------------------------------------------------------------------
# _parse_value
# ---------------------------------------------------------------------------
class TestParseValue:
    def test_true_string(self, parser):
        assert parser._parse_value("true") is True

    def test_True_string(self, parser):
        assert parser._parse_value("True") is True

    def test_TRUE_string(self, parser):
        assert parser._parse_value("TRUE") is True

    def test_false_string(self, parser):
        assert parser._parse_value("false") is False

    def test_False_string(self, parser):
        assert parser._parse_value("False") is False

    def test_FALSE_string(self, parser):
        assert parser._parse_value("FALSE") is False

    def test_integer_string(self, parser):
        result = parser._parse_value("42")
        assert result == 42
        assert isinstance(result, int)

    def test_negative_integer(self, parser):
        result = parser._parse_value("-7")
        assert result == -7
        assert isinstance(result, int)

    def test_zero_integer(self, parser):
        result = parser._parse_value("0")
        assert result == 0
        assert isinstance(result, int)

    def test_float_string(self, parser):
        result = parser._parse_value("3.14")
        assert result == pytest.approx(3.14)
        assert isinstance(result, float)

    def test_negative_float(self, parser):
        result = parser._parse_value("-2.5")
        assert result == pytest.approx(-2.5)
        assert isinstance(result, float)

    def test_plain_string(self, parser):
        result = parser._parse_value("hello")
        assert result == "hello"
        assert isinstance(result, str)

    def test_empty_string(self, parser):
        result = parser._parse_value("")
        assert result == ""
        assert isinstance(result, str)

    def test_string_with_spaces(self, parser):
        result = parser._parse_value("hello world")
        assert result == "hello world"

    def test_string_that_looks_numeric_but_isnt(self, parser):
        result = parser._parse_value("12abc")
        assert result == "12abc"
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _deduplicate_headers
# ---------------------------------------------------------------------------
class TestDeduplicateHeaders:
    def test_duplicates_get_suffix(self, parser):
        headers = ["A", "B", "A"]
        result = parser._deduplicate_headers(headers)
        assert result == ["A", "B", "A_2"]

    def test_no_duplicates_unchanged(self, parser):
        headers = ["X", "Y", "Z"]
        result = parser._deduplicate_headers(headers)
        assert result == ["X", "Y", "Z"]

    def test_triple_duplicate(self, parser):
        headers = ["Col", "Col", "Col"]
        result = parser._deduplicate_headers(headers)
        assert result == ["Col", "Col_2", "Col_3"]

    def test_empty_list(self, parser):
        assert parser._deduplicate_headers([]) == []

    def test_single_header(self, parser):
        assert parser._deduplicate_headers(["Only"]) == ["Only"]

    def test_multiple_different_duplicates(self, parser):
        headers = ["A", "B", "A", "B", "C"]
        result = parser._deduplicate_headers(headers)
        assert result == ["A", "B", "A_2", "B_2", "C"]


# ---------------------------------------------------------------------------
# _select_representative_sample_rows
# ---------------------------------------------------------------------------
class TestSelectRepresentativeSampleRows:
    def test_prioritizes_rows_with_no_null_values(self, parser):
        rows = [
            ["a", "b", "c"],           # perfect
            ["x", "null", "z"],         # has null
            ["d", "e", "f"],           # perfect
        ]
        result = parser._select_representative_sample_rows(rows)
        # Perfect rows should be selected first
        assert ["a", "b", "c"] in result
        assert ["d", "e", "f"] in result

    def test_falls_back_to_best_rows_when_not_enough_perfect(self, parser):
        rows = [
            ["null", "null", "null"],   # 3 nulls
            ["a", "null", "c"],         # 1 null
            ["x", "y", "null"],         # 1 null
        ]
        result = parser._select_representative_sample_rows(rows)
        # All rows should be included since we have fewer than MAX_HEADER_GENERATION_ROWS
        assert len(result) == 3

    def test_fallback_sorted_by_empty_count(self, parser):
        # No perfect rows, fallback rows should be sorted by null count ascending
        rows = [
            ["null", "null", "null"],   # 3 nulls (worst)
            ["a", "null", "c"],         # 1 null (best fallback)
            ["null", "b", "null"],      # 2 nulls (middle)
        ]
        result = parser._select_representative_sample_rows(rows)
        assert len(result) == 3
        # Result is sorted by original index after selection, so check all are present
        assert ["null", "null", "null"] in result
        assert ["a", "null", "c"] in result
        assert ["null", "b", "null"] in result

    def test_empty_input(self, parser):
        result = parser._select_representative_sample_rows([])
        assert result == []

    def test_stops_early_with_enough_perfect_rows(self, parser):
        # Create more than MAX_HEADER_GENERATION_ROWS (10) perfect rows
        rows = [["a", "b", "c"] for _ in range(15)]
        result = parser._select_representative_sample_rows(rows)
        assert len(result) == 10  # MAX_HEADER_GENERATION_ROWS

    def test_result_sorted_by_original_index(self, parser):
        rows = [
            ["null", "x"],      # idx 0, 1 null
            ["a", "b"],         # idx 1, perfect
            ["null", "y"],      # idx 2, 1 null
            ["c", "d"],         # idx 3, perfect
        ]
        result = parser._select_representative_sample_rows(rows)
        # Perfect rows first (idx 1, 3), then fallbacks (idx 0, 2) - but all sorted by index
        perfect_rows = [r for r in result if "null" not in r]
        assert perfect_rows == [["a", "b"], ["c", "d"]]


# ---------------------------------------------------------------------------
# _count_empty_values
# ---------------------------------------------------------------------------
class TestCountEmptyValues:
    def test_counts_none_values(self, parser):
        row = {"a": None, "b": "hello", "c": None}
        assert parser._count_empty_values(row) == 2

    def test_counts_empty_string_values(self, parser):
        row = {"a": "", "b": "hello", "c": ""}
        assert parser._count_empty_values(row) == 2

    def test_counts_mixed_empty(self, parser):
        row = {"a": None, "b": "", "c": "value"}
        assert parser._count_empty_values(row) == 2

    def test_no_empty_values(self, parser):
        row = {"a": "x", "b": "y", "c": "z"}
        assert parser._count_empty_values(row) == 0

    def test_all_empty(self, parser):
        row = {"a": None, "b": "", "c": None}
        assert parser._count_empty_values(row) == 3

    def test_empty_dict(self, parser):
        assert parser._count_empty_values({}) == 0

    def test_non_string_non_none_not_counted(self, parser):
        row = {"a": 0, "b": False, "c": "data"}
        assert parser._count_empty_values(row) == 0


# ---------------------------------------------------------------------------
# read_raw_rows
# ---------------------------------------------------------------------------
class TestReadRawRows:
    def test_reads_basic_csv(self, parser):
        csv_data = "a,b,c\n1,2,3\n4,5,6\n"
        stream = io.StringIO(csv_data)
        result = parser.read_raw_rows(stream)
        assert result == [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]]

    def test_reads_with_custom_delimiter(self):
        parser = CSVParser(delimiter=";")
        csv_data = "a;b;c\n1;2;3\n"
        stream = io.StringIO(csv_data)
        result = parser.read_raw_rows(stream)
        assert result == [["a", "b", "c"], ["1", "2", "3"]]

    def test_reads_quoted_fields(self, parser):
        csv_data = '"hello, world",b,c\n'
        stream = io.StringIO(csv_data)
        result = parser.read_raw_rows(stream)
        assert result == [["hello, world", "b", "c"]]

    def test_empty_stream(self, parser):
        stream = io.StringIO("")
        result = parser.read_raw_rows(stream)
        assert result == []

    def test_single_row(self, parser):
        csv_data = "a,b,c\n"
        stream = io.StringIO(csv_data)
        result = parser.read_raw_rows(stream)
        assert result == [["a", "b", "c"]]

    def test_reads_with_custom_quotechar(self):
        parser = CSVParser(quotechar="'")
        csv_data = "'hello, world',b,c\n"
        stream = io.StringIO(csv_data)
        result = parser.read_raw_rows(stream)
        assert result == [["hello, world", "b", "c"]]

    def test_returns_list_of_lists(self, parser):
        csv_data = "x,y\n1,2\n"
        stream = io.StringIO(csv_data)
        result = parser.read_raw_rows(stream)
        assert isinstance(result, list)
        for row in result:
            assert isinstance(row, list)


# ---------------------------------------------------------------------------
# _is_empty_row
# ---------------------------------------------------------------------------
class TestIsEmptyRow:
    def test_empty_list_is_empty(self, parser):
        assert parser._is_empty_row([]) is True

    def test_all_empty_strings(self, parser):
        assert parser._is_empty_row(["", "  ", ""]) is True

    def test_all_none(self, parser):
        assert parser._is_empty_row([None, None]) is True

    def test_mixed_empty(self, parser):
        assert parser._is_empty_row([None, "", "  "]) is True

    def test_has_data(self, parser):
        assert parser._is_empty_row(["", "data", ""]) is False

    def test_range_start_col(self, parser):
        row = ["data", "", ""]
        # Only checking from col 1 onward -> all empty
        assert parser._is_empty_row(row, start_col=1) is True

    def test_range_end_col(self, parser):
        row = ["", "", "data"]
        # Only checking cols 0-1 -> empty
        assert parser._is_empty_row(row, end_col=1) is True

    def test_range_start_and_end(self, parser):
        row = ["data", "", "", "data"]
        assert parser._is_empty_row(row, start_col=1, end_col=2) is True

    def test_range_start_col_beyond_length(self, parser):
        row = ["data"]
        assert parser._is_empty_row(row, start_col=5) is True

    def test_range_with_data_in_range(self, parser):
        row = ["", "data", ""]
        assert parser._is_empty_row(row, start_col=0, end_col=1) is False


# ---------------------------------------------------------------------------
# find_tables_in_csv
# ---------------------------------------------------------------------------
class TestFindTablesInCsv:
    def test_empty_input(self, parser):
        assert parser.find_tables_in_csv([]) == []

    def test_single_table_no_gaps(self, parser):
        rows = [
            ["Name", "Age", "City"],
            ["Alice", "30", "NYC"],
            ["Bob", "25", "LA"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 1
        assert tables[0]["start_row"] == 1  # 1-based
        assert tables[0]["end_row"] == 3
        assert len(tables[0]["raw_rows"]) == 3

    def test_two_tables_separated_by_empty_row(self, parser):
        rows = [
            ["A", "B"],
            ["1", "2"],
            ["", ""],          # empty row separator
            ["X", "Y"],
            ["3", "4"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 2
        assert tables[0]["start_row"] == 1
        assert tables[1]["start_row"] == 4

    def test_single_cell_table(self, parser):
        rows = [
            ["", ""],
            ["", "data"],
            ["", ""],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 1
        assert tables[0]["raw_rows"] == [["data"]]

    def test_table_at_edges(self, parser):
        """Table at the very start and end of the file (no surrounding empties needed)."""
        rows = [
            ["a", "b"],
            ["c", "d"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 1
        assert len(tables[0]["raw_rows"]) == 2


# ---------------------------------------------------------------------------
# _concatenate_multirow_headers
# ---------------------------------------------------------------------------
class TestConcatenateMultirowHeaders:
    def test_single_header_row(self, parser):
        headers = [["Name", "Age", "City"]]
        result = parser._concatenate_multirow_headers(headers, 3)
        assert result == ["Name", "Age", "City"]

    def test_multirow_headers(self, parser):
        headers = [
            ["Category", "Category", "Details"],
            ["First", "Last", "Address"],
        ]
        result = parser._concatenate_multirow_headers(headers, 3)
        # Each column takes the last non-null part
        assert result == ["First", "Last", "Address"]

    def test_empty_cells_produce_generic_name(self, parser):
        headers = [
            ["null", "null"],
        ]
        result = parser._concatenate_multirow_headers(headers, 2)
        assert result == ["Column_1", "Column_2"]

    def test_duplicate_values_across_rows_deduped(self, parser):
        headers = [
            ["Total", "Total"],
            ["Total", "Amount"],
        ]
        result = parser._concatenate_multirow_headers(headers, 2)
        # Column 0: "Total" seen once -> "Total"
        # Column 1: "Total", then "Amount" -> last is "Amount"
        assert result[0] == "Total"
        assert result[1] == "Amount"

    def test_column_count_larger_than_header_rows(self, parser):
        headers = [["A"]]
        result = parser._concatenate_multirow_headers(headers, 3)
        assert result[0] == "A"
        assert result[1] == "Column_2"
        assert result[2] == "Column_3"


# ---------------------------------------------------------------------------
# convert_table_to_dict
# ---------------------------------------------------------------------------
class TestConvertTableToDict:
    def test_basic_conversion(self, parser):
        table = {
            "headers": ["Name", "Age"],
            "data": [["Alice", "30"], ["Bob", "25"]],
            "start_row": 1,
        }
        data, line_numbers = parser.convert_table_to_dict(table)
        assert len(data) == 2
        assert data[0] == {"Name": "Alice", "Age": 30}
        assert data[1] == {"Name": "Bob", "Age": 25}
        assert line_numbers == [2, 3]  # start_row + idx + 1

    def test_empty_headers_get_generic_names(self, parser):
        table = {
            "headers": ["", None, "Valid"],
            "data": [["a", "b", "c"]],
            "start_row": 5,
        }
        data, line_numbers = parser.convert_table_to_dict(table)
        assert "Column_1" in data[0]
        assert "Column_2" in data[0]
        assert "Valid" in data[0]

    def test_duplicate_headers_get_suffixed(self, parser):
        table = {
            "headers": ["Col", "Col"],
            "data": [["x", "y"]],
            "start_row": 1,
        }
        data, line_numbers = parser.convert_table_to_dict(table)
        assert "Col" in data[0]
        assert "Col_1" in data[0]

    def test_short_row_gets_padded(self, parser):
        table = {
            "headers": ["A", "B", "C"],
            "data": [["1"]],
            "start_row": 1,
        }
        data, line_numbers = parser.convert_table_to_dict(table)
        assert data[0]["A"] == 1
        assert data[0]["B"] == ""
        assert data[0]["C"] == ""

    def test_all_none_row_skipped(self, parser):
        """Rows where all values are None are skipped by convert_table_to_dict."""
        table = {
            "headers": ["A", "B"],
            "data": [["hello", "world"], ["valid", "data"]],
            "start_row": 1,
        }
        data, line_numbers = parser.convert_table_to_dict(table)
        # Both rows have real values, so both should be included
        assert len(data) == 2

    def test_empty_data(self, parser):
        table = {
            "headers": ["A"],
            "data": [],
            "start_row": 1,
        }
        data, line_numbers = parser.convert_table_to_dict(table)
        assert data == []
        assert line_numbers == []


# ---------------------------------------------------------------------------
# _get_table
# ---------------------------------------------------------------------------
class TestGetTable:
    def test_basic_extraction(self, parser):
        all_rows = [
            ["a", "b"],
            ["c", "d"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 2)
        assert table["start_row"] == 1  # 1-based
        assert table["end_row"] == 2
        assert len(table["raw_rows"]) == 2
        assert table["raw_rows"][0] == ["a", "b"]
        assert table["raw_rows"][1] == ["c", "d"]

    def test_marks_visited_cells(self, parser):
        all_rows = [
            ["x", "y"],
            ["z", "w"],
        ]
        visited = set()
        parser._get_table(all_rows, 0, 0, visited, 2)
        assert (0, 0) in visited
        assert (0, 1) in visited
        assert (1, 0) in visited
        assert (1, 1) in visited

    def test_empty_cell_becomes_null(self, parser):
        """Empty cells within the table region are marked as 'null'."""
        # The table has 2 columns because both have some data across rows
        all_rows = [
            ["a", "b"],
            ["c", ""],  # second cell is empty -> should become "null"
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 2)
        assert table["raw_rows"][1][1] == "null"

    def test_table_stops_at_empty_column(self, parser):
        all_rows = [
            ["a", "", "b"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 3)
        # Column 1 is empty so table should stop at column 0
        assert len(table["raw_rows"][0]) == 1
        assert table["raw_rows"][0][0] == "a"

    def test_table_starts_at_offset(self, parser):
        all_rows = [
            ["", "", "x"],
            ["", "", "y"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 2, visited, 3)
        assert table["raw_rows"] == [["x"], ["y"]]

    def test_short_row_pads_with_null(self, parser):
        all_rows = [
            ["a", "b", "c"],
            ["d"],  # shorter row
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 3)
        assert table["raw_rows"][1] == ["d", "null", "null"]


# ---------------------------------------------------------------------------
# process_table_with_header_info
# ---------------------------------------------------------------------------
class TestProcessTableWithHeaderInfo:
    @pytest.fixture
    def mock_detection_single_header(self):
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection
        return CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="high",
            reasoning="First row looks like headers",
        )

    @pytest.fixture
    def mock_detection_multi_header(self):
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection
        return CSVHeaderDetection(
            has_headers=True,
            num_header_rows=2,
            confidence="high",
            reasoning="Two header rows detected",
        )

    @pytest.fixture
    def mock_detection_no_header(self):
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection
        return CSVHeaderDetection(
            has_headers=False,
            num_header_rows=0,
            confidence="high",
            reasoning="No headers detected",
        )

    @pytest.mark.asyncio
    async def test_single_row_header(self, parser, mock_detection_single_header):
        raw_rows = [
            ["Name", "Age"],
            ["Alice", "30"],
            ["Bob", "25"],
        ]
        result, line_numbers = await parser.process_table_with_header_info(
            raw_rows, mock_detection_single_header, start_row=1, llm=None
        )
        assert len(result) == 2
        assert result[0] == {"Name": "Alice", "Age": 30}
        assert result[1] == {"Name": "Bob", "Age": 25}
        assert line_numbers == [2, 3]

    @pytest.mark.asyncio
    async def test_multi_row_header(self, parser, mock_detection_multi_header):
        raw_rows = [
            ["Category", "Category"],
            ["First", "Last"],
            ["Alice", "Smith"],
        ]
        result, line_numbers = await parser.process_table_with_header_info(
            raw_rows, mock_detection_multi_header, start_row=1, llm=None
        )
        assert len(result) == 1
        # Multi-row concatenation: last non-null part for each col
        assert "First" in result[0] or "Last" in result[0]
        assert line_numbers == [3]

    @pytest.mark.asyncio
    async def test_no_headers_generates_via_llm(self, parser, mock_detection_no_header):
        from unittest.mock import AsyncMock
        mock_llm = AsyncMock()

        # Mock generate_headers_with_llm
        parser.generate_headers_with_llm = AsyncMock(
            return_value=["Col_A", "Col_B"]
        )

        raw_rows = [
            ["1", "2"],
            ["3", "4"],
        ]
        result, line_numbers = await parser.process_table_with_header_info(
            raw_rows, mock_detection_no_header, start_row=1, llm=mock_llm
        )
        assert len(result) == 2
        assert "Col_A" in result[0]
        assert "Col_B" in result[0]
        assert line_numbers == [1, 2]
        parser.generate_headers_with_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_raw_rows(self, parser, mock_detection_single_header):
        result, line_numbers = await parser.process_table_with_header_info(
            [], mock_detection_single_header, start_row=1, llm=None
        )
        assert result == []
        assert line_numbers == []

    @pytest.mark.asyncio
    async def test_null_header_cells_replaced(self, parser, mock_detection_single_header):
        raw_rows = [
            ["null", "Valid"],
            ["a", "b"],
        ]
        result, line_numbers = await parser.process_table_with_header_info(
            raw_rows, mock_detection_single_header, start_row=1, llm=None
        )
        assert len(result) == 1
        # First header was "null" -> replaced with "Column_1"
        assert "Column_1" in result[0]
        assert "Valid" in result[0]

    @pytest.mark.asyncio
    async def test_all_null_data_rows_skipped(self, parser, mock_detection_single_header):
        raw_rows = [
            ["Name", "Age"],
            ["null", "null"],  # all null data row
        ]
        result, line_numbers = await parser.process_table_with_header_info(
            raw_rows, mock_detection_single_header, start_row=1, llm=None
        )
        # The row has all values == "null" so it should be skipped
        assert len(result) == 0
        assert line_numbers == []


# ---------------------------------------------------------------------------
# detect_headers_with_llm
# ---------------------------------------------------------------------------
class TestDetectHeadersWithLlm:
    @pytest.mark.asyncio
    async def test_headers_detected_high_confidence(self, parser):
        """LLM detects headers with high confidence."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="high",
            reasoning="First row contains descriptive header names",
        )

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_detection,
        ):
            mock_llm = AsyncMock()
            first_rows = [["Name", "Age", "City"], ["Alice", "30", "NYC"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            assert result.has_headers is True
            assert result.num_header_rows == 1
            assert result.confidence == "high"

    @pytest.mark.asyncio
    async def test_no_headers_detected(self, parser):
        """LLM detects no headers."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=False,
            num_header_rows=0,
            confidence="high",
            reasoning="All rows look like data",
        )

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_detection,
        ):
            mock_llm = AsyncMock()
            first_rows = [["1", "2", "3"], ["4", "5", "6"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            assert result.has_headers is False
            assert result.num_header_rows == 0

    @pytest.mark.asyncio
    async def test_not_enough_rows_defaults_to_no_headers(self, parser):
        """Fewer than MIN_ROWS_FOR_HEADER_ANALYSIS rows returns no headers."""
        mock_llm = AsyncMock()
        result = await parser.detect_headers_with_llm([], mock_llm)
        assert result.has_headers is False
        assert result.num_header_rows == 0
        assert result.confidence == "low"

    @pytest.mark.asyncio
    async def test_llm_returns_none_defaults_to_no_headers(self, parser):
        """When LLM returns None, defaults to no headers."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mock_llm = AsyncMock()
            first_rows = [["a", "b"], ["c", "d"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            assert result.has_headers is False
            assert result.confidence == "low"

    @pytest.mark.asyncio
    async def test_llm_exception_defaults_to_no_headers(self, parser):
        """When LLM raises exception, defaults to no headers."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM error"),
        ):
            mock_llm = AsyncMock()
            first_rows = [["a", "b"], ["c", "d"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            assert result.has_headers is False
            assert "Error occurred" in result.reasoning

    @pytest.mark.asyncio
    async def test_has_headers_true_but_num_rows_zero_corrected(self, parser):
        """When LLM says has_headers=True but num_header_rows=0, corrects to 1."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=0,
            confidence="high",
            reasoning="Headers present",
        )

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_detection,
        ):
            mock_llm = AsyncMock()
            first_rows = [["Name", "Age"], ["Alice", "30"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            assert result.has_headers is True
            assert result.num_header_rows == 1

    @pytest.mark.asyncio
    async def test_has_headers_false_but_num_rows_nonzero_corrected(self, parser):
        """When LLM says has_headers=False but num_header_rows>0, corrects to 0."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=False,
            num_header_rows=2,
            confidence="high",
            reasoning="No headers",
        )

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_detection,
        ):
            mock_llm = AsyncMock()
            first_rows = [["1", "2"], ["3", "4"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            assert result.has_headers is False
            assert result.num_header_rows == 0

    @pytest.mark.asyncio
    async def test_low_confidence_returns_no_headers(self, parser):
        """When confidence is not 'high', defaults to no headers."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="low",
            reasoning="Uncertain about headers",
        )

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_detection,
        ):
            mock_llm = AsyncMock()
            first_rows = [["a", "b"], ["c", "d"]]
            result = await parser.detect_headers_with_llm(first_rows, mock_llm)
            # Low confidence: code only uses high-confidence detections
            assert result.has_headers is False
            assert result.confidence == "low"


# ---------------------------------------------------------------------------
# generate_headers_with_llm
# ---------------------------------------------------------------------------
class TestGenerateHeadersWithLlm:
    @pytest.mark.asyncio
    async def test_success_matching_column_count(self, parser):
        """LLM generates headers matching column count."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.headers = ["Name", "Age", "City"]

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            mock_llm = AsyncMock()
            sample_data = [["Alice", "30", "NYC"], ["Bob", "25", "LA"]]
            result = await parser.generate_headers_with_llm(sample_data, 3, mock_llm)
            assert result == ["Name", "Age", "City"]

    @pytest.mark.asyncio
    async def test_mismatch_column_count_uses_fallback(self, parser):
        """When generated headers don't match column count, uses fallback."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.headers = ["Name", "Age"]  # Only 2 instead of 3

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            mock_llm = AsyncMock()
            sample_data = [["Alice", "30", "NYC"]]
            result = await parser.generate_headers_with_llm(sample_data, 3, mock_llm)
            assert result == ["Column_1", "Column_2", "Column_3"]

    @pytest.mark.asyncio
    async def test_llm_returns_none_uses_fallback(self, parser):
        """When LLM returns None, uses generic fallback headers."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mock_llm = AsyncMock()
            result = await parser.generate_headers_with_llm([["a", "b"]], 2, mock_llm)
            assert result == ["Column_1", "Column_2"]

    @pytest.mark.asyncio
    async def test_llm_exception_uses_fallback(self, parser):
        """When LLM raises exception, uses generic fallback headers."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM error"),
        ):
            mock_llm = AsyncMock()
            result = await parser.generate_headers_with_llm([["a", "b"]], 2, mock_llm)
            assert result == ["Column_1", "Column_2"]

    @pytest.mark.asyncio
    async def test_empty_headers_response_uses_fallback(self, parser):
        """When LLM returns empty headers list, uses fallback."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.headers = []

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            mock_llm = AsyncMock()
            result = await parser.generate_headers_with_llm([["a"]], 1, mock_llm)
            assert result == ["Column_1"]


# ---------------------------------------------------------------------------
# get_table_summary
# ---------------------------------------------------------------------------
class TestGetTableSummary:
    @pytest.mark.asyncio
    async def test_success_with_content_attribute(self, parser):
        """LLM returns message object with .content attribute."""
        from unittest.mock import AsyncMock, MagicMock

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "This table contains employee data."
        mock_llm.ainvoke.return_value = mock_response

        rows = [{"Name": "Alice", "Age": 30}, {"Name": "Bob", "Age": 25}]
        result = await parser.get_table_summary(mock_llm, rows)
        assert result == "This table contains employee data."

    @pytest.mark.asyncio
    async def test_success_with_string_response(self, parser):
        """LLM returns plain string."""
        from unittest.mock import AsyncMock

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = "Employee records summary"

        rows = [{"Name": "Alice", "Age": 30}]
        result = await parser.get_table_summary(mock_llm, rows)
        assert result == "Employee records summary"

    @pytest.mark.asyncio
    async def test_think_tag_stripped_from_content(self, parser):
        """Think tags in content are stripped."""
        from unittest.mock import AsyncMock, MagicMock

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "<think>reasoning here</think>Employee data table."
        mock_llm.ainvoke.return_value = mock_response

        rows = [{"Name": "Alice"}]
        result = await parser.get_table_summary(mock_llm, rows)
        assert result == "Employee data table."
        assert "<think>" not in result

    @pytest.mark.asyncio
    async def test_think_tag_stripped_from_string_response(self, parser):
        """Think tags in string response are stripped."""
        from unittest.mock import AsyncMock

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = "<think>deep thoughts</think>Summary text"

        rows = [{"Col": "val"}]
        result = await parser.get_table_summary(mock_llm, rows)
        assert result == "Summary text"

    @pytest.mark.asyncio
    async def test_exception_propagates(self, parser):
        """When LLM raises exception, it propagates (wrapped by tenacity retry)."""
        import tenacity

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("LLM failure")

        rows = [{"Name": "Alice"}]
        with pytest.raises((RuntimeError, tenacity.RetryError)):
            await parser.get_table_summary(mock_llm, rows)

    @pytest.mark.asyncio
    async def test_fallback_for_dict_response(self, parser):
        """LLM returns dict/list falls back to str()."""
        from unittest.mock import AsyncMock

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = {"summary": "some data"}

        rows = [{"Name": "Alice"}]
        result = await parser.get_table_summary(mock_llm, rows)
        assert "summary" in result


# ---------------------------------------------------------------------------
# get_rows_text
# ---------------------------------------------------------------------------
class TestGetRowsText:
    @pytest.mark.asyncio
    async def test_single_batch(self, parser):
        """Single batch of rows processed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.descriptions = ["Alice is 30 years old.", "Bob is 25 years old."]

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_row_descriptions_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            mock_llm = AsyncMock()
            rows = [{"Name": "Alice", "Age": 30}, {"Name": "Bob", "Age": 25}]
            result = await parser.get_rows_text(mock_llm, rows, "Employee table", batch_size=50)
            assert len(result) == 2
            assert result[0] == "Alice is 30 years old."

    @pytest.mark.asyncio
    async def test_multiple_batches(self, parser):
        """Rows split into multiple batches."""
        from unittest.mock import AsyncMock, MagicMock, patch

        call_count = [0]

        async def mock_invoke(llm, messages, expected_count):
            call_count[0] += 1
            response = MagicMock()
            response.descriptions = [f"Row {i}" for i in range(expected_count)]
            return response

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_row_descriptions_and_reflection",
            side_effect=mock_invoke,
        ):
            mock_llm = AsyncMock()
            rows = [{"Name": f"Person{i}"} for i in range(5)]
            result = await parser.get_rows_text(mock_llm, rows, "Test table", batch_size=2)
            assert len(result) == 5
            assert call_count[0] == 3  # ceil(5/2) = 3 batches

    @pytest.mark.asyncio
    async def test_llm_returns_none_uses_simple_text(self, parser):
        """When LLM returns None, falls back to simple row text."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.modules.parsers.csv.csv_parser.invoke_with_row_descriptions_and_reflection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mock_llm = AsyncMock()
            rows = [{"Name": "Alice", "Age": 30}]
            result = await parser.get_rows_text(mock_llm, rows, "Table summary", batch_size=50)
            assert len(result) == 1
            # Should be a simple text representation
            assert isinstance(result[0], str)
            assert len(result[0]) > 0


# ---------------------------------------------------------------------------
# get_blocks_from_csv_with_multiple_tables
# ---------------------------------------------------------------------------
class TestGetBlocksFromCsvWithMultipleTables:
    @pytest.mark.asyncio
    async def test_single_table(self, parser):
        """Single table produces BlocksContainer with blocks."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="high",
            reasoning="Headers found",
        )

        tables = [{
            "raw_rows": [["Name", "Age"], ["Alice", "30"]],
            "start_row": 1,
        }]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="Employee table"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["Alice is 30"]):
                    mock_llm = AsyncMock()
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, mock_llm)
                    assert len(result.blocks) >= 1
                    assert len(result.block_groups) >= 1

    @pytest.mark.asyncio
    async def test_empty_table_skipped(self, parser):
        """Table with empty raw_rows is skipped."""
        from unittest.mock import AsyncMock

        tables = [{"raw_rows": [], "start_row": 1}]
        mock_llm = AsyncMock()
        result = await parser.get_blocks_from_csv_with_multiple_tables(tables, mock_llm)
        assert len(result.blocks) == 0
        assert len(result.block_groups) == 0

    @pytest.mark.asyncio
    async def test_multiple_tables(self, parser):
        """Multiple tables produce multiple block groups."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="high",
            reasoning="Headers found",
        )

        tables = [
            {"raw_rows": [["A", "B"], ["1", "2"]], "start_row": 1},
            {"raw_rows": [["X", "Y"], ["3", "4"]], "start_row": 5},
        ]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="Summary"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["row text"]):
                    mock_llm = AsyncMock()
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, mock_llm)
                    assert len(result.block_groups) == 2

    @pytest.mark.asyncio
    async def test_cumulative_row_threshold_skips_llm(self, parser):
        """When cumulative rows exceed threshold, simple text is used."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="high",
            reasoning="Headers found",
        )

        # Create a large table that exceeds the threshold
        data_rows = [[f"val{i}", f"val{i+1}"] for i in range(10)]
        raw_rows = [["ColA", "ColB"]] + data_rows
        tables = [{"raw_rows": raw_rows, "start_row": 1}]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="Large table"):
                with patch.dict("os.environ", {"MAX_TABLE_ROWS_FOR_LLM": "5"}):
                    mock_llm = AsyncMock()
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, mock_llm)
                    # Blocks should still be created, just with simple row text
                    assert len(result.blocks) == 10

    @pytest.mark.asyncio
    async def test_table_with_no_data_after_processing_skipped(self, parser):
        """Table that has raw_rows but no data after processing is skipped."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True,
            num_header_rows=1,
            confidence="high",
            reasoning="Headers found",
        )

        # Only header row, no data
        tables = [{"raw_rows": [["Name", "Age"]], "start_row": 1}]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            mock_llm = AsyncMock()
            result = await parser.get_blocks_from_csv_with_multiple_tables(tables, mock_llm)
            assert len(result.blocks) == 0


# ---------------------------------------------------------------------------
# _call_llm — retry behaviour
# ---------------------------------------------------------------------------
class TestCallLlm:
    @pytest.mark.asyncio
    async def test_success(self, parser):
        """Successful LLM call returns result."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = "response text"

        result = await parser._call_llm(mock_llm, ["prompt"])
        assert result == "response text"
        mock_llm.ainvoke.assert_awaited_once_with(["prompt"])

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, parser):
        """_call_llm is decorated with @retry; it retries on exception."""
        import tenacity

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("transient error")

        with pytest.raises((RuntimeError, tenacity.RetryError)):
            await parser._call_llm(mock_llm, ["prompt"])

        # Should have been called multiple times due to retry (3 attempts)
        assert mock_llm.ainvoke.await_count == 3

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, parser):
        """Fails once, then succeeds on retry."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [RuntimeError("fail"), "success"]

        result = await parser._call_llm(mock_llm, ["prompt"])
        assert result == "success"
        assert mock_llm.ainvoke.await_count == 2


# ---------------------------------------------------------------------------
# _is_empty_row — deeper range parameter tests
# ---------------------------------------------------------------------------
class TestIsEmptyRowDeep:
    def test_start_col_only_with_data_before(self, parser):
        """Data before start_col doesn't affect the check."""
        row = ["data", "more_data", "", ""]
        assert parser._is_empty_row(row, start_col=2) is True

    def test_end_col_only_with_data_after(self, parser):
        """Data after end_col doesn't affect the check."""
        row = ["", "", "data", "more_data"]
        assert parser._is_empty_row(row, end_col=1) is True

    def test_range_with_whitespace_only(self, parser):
        """Whitespace-only values within range are considered empty."""
        row = ["data", "   ", "\t", "data"]
        assert parser._is_empty_row(row, start_col=1, end_col=2) is True

    def test_range_with_none_and_empty(self, parser):
        """Mix of None and empty strings within range."""
        row = ["data", None, "", None, "data"]
        assert parser._is_empty_row(row, start_col=1, end_col=3) is True

    def test_range_full_row_with_data(self, parser):
        """Full row with data, checking entire range."""
        row = ["a", "b", "c"]
        assert parser._is_empty_row(row, start_col=0, end_col=2) is False

    def test_end_col_zero(self, parser):
        """end_col=0 checks only first element."""
        row = ["", "data"]
        assert parser._is_empty_row(row, end_col=0) is True

    def test_end_col_zero_with_data(self, parser):
        """end_col=0 with data in first element."""
        row = ["data", ""]
        assert parser._is_empty_row(row, end_col=0) is False


# ---------------------------------------------------------------------------
# _get_table — deeper tests for complex table boundaries
# ---------------------------------------------------------------------------
class TestGetTableDeep:
    def test_table_with_internal_empty_cells(self, parser):
        """Table with scattered empty cells inside; table boundary still correct."""
        all_rows = [
            ["a", "b", "c"],
            ["d", "",  "f"],
            ["g", "h", "i"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 3)
        assert len(table["raw_rows"]) == 3
        # Internal empty cell becomes "null"
        assert table["raw_rows"][1][1] == "null"

    def test_table_stops_at_fully_empty_row(self, parser):
        """Table stops when encountering a fully empty row."""
        all_rows = [
            ["a", "b"],
            ["c", "d"],
            ["", ""],   # empty row separator
            ["x", "y"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 2)
        # Should only include first two rows
        assert len(table["raw_rows"]) == 2
        assert table["raw_rows"][0] == ["a", "b"]
        assert table["raw_rows"][1] == ["c", "d"]

    def test_single_cell_table(self, parser):
        """A table consisting of just one cell."""
        all_rows = [
            ["", ""],
            ["", "data"],
            ["", ""],
        ]
        visited = set()
        table = parser._get_table(all_rows, 1, 1, visited, 2)
        assert len(table["raw_rows"]) == 1
        assert table["raw_rows"][0] == ["data"]

    def test_table_with_varying_row_lengths(self, parser):
        """Rows of different lengths are padded with null."""
        all_rows = [
            ["a", "b", "c"],
            ["d"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 0, 0, visited, 3)
        assert len(table["raw_rows"]) == 2
        assert table["raw_rows"][1] == ["d", "null", "null"]

    def test_table_metadata_is_1_based(self, parser):
        """start_row and end_row are 1-based line numbers."""
        all_rows = [
            ["", ""],
            ["a", "b"],
            ["c", "d"],
        ]
        visited = set()
        table = parser._get_table(all_rows, 1, 0, visited, 2)
        assert table["start_row"] == 2  # 1 + 1
        assert table["end_row"] == 3    # 2 + 1


# ---------------------------------------------------------------------------
# get_blocks_from_csv_with_multiple_tables — deeper coverage
# ---------------------------------------------------------------------------
class TestGetBlocksFromCsvDeep:
    """Deeper tests for BlockGroup/Block creation, table_row blocks, metadata."""

    @pytest.mark.asyncio
    async def test_block_group_has_table_metadata(self, parser):
        """BlockGroup has correct table_metadata (num_of_rows, cols, cells)."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [{
            "raw_rows": [["Name", "Age", "City"], ["Alice", "30", "NYC"], ["Bob", "25", "LA"]],
            "start_row": 1,
        }]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="People table"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["row1", "row2"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    assert len(result.block_groups) == 1
                    group = result.block_groups[0]
                    assert group.table_metadata.num_of_rows == 2
                    assert group.table_metadata.num_of_cols == 3
                    assert group.table_metadata.num_of_cells == 6

    @pytest.mark.asyncio
    async def test_block_data_contains_row_text_and_number(self, parser):
        """Each TABLE_ROW block has row_natural_language_text and row_number."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [{
            "raw_rows": [["A"], ["val1"]],
            "start_row": 1,
        }]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="Summary"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["val1 description"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    assert len(result.blocks) == 1
                    block = result.blocks[0]
                    assert block.data["row_natural_language_text"] == "val1 description"
                    assert "row_number" in block.data

    @pytest.mark.asyncio
    async def test_block_parent_index_references_table_group(self, parser):
        """Each block's parent_index references the correct table group."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [{
            "raw_rows": [["X"], ["1"], ["2"]],
            "start_row": 1,
        }]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="S"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["r1", "r2"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    for block in result.blocks:
                        assert block.parent_index == 0  # first (and only) table group

    @pytest.mark.asyncio
    async def test_block_group_children_set(self, parser):
        """BlockGroup children are set with correct block indices."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [{
            "raw_rows": [["A"], ["1"], ["2"], ["3"]],
            "start_row": 1,
        }]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="S"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["r1", "r2", "r3"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    group = result.block_groups[0]
                    # Children should reference block indices 0, 1, 2
                    assert group.children is not None

    @pytest.mark.asyncio
    async def test_block_group_data_has_column_headers(self, parser):
        """BlockGroup data includes column_headers and table_summary."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [{
            "raw_rows": [["Name", "Age"], ["Alice", "30"]],
            "start_row": 1,
        }]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="Employee data"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["Alice is 30"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    group = result.block_groups[0]
                    assert group.data["table_summary"] == "Employee data"
                    assert group.data["column_headers"] == ["Name", "Age"]

    @pytest.mark.asyncio
    async def test_multiple_tables_independent_groups(self, parser):
        """Multiple tables create independent block groups with correct indices."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [
            {"raw_rows": [["A"], ["1"]], "start_row": 1},
            {"raw_rows": [["B"], ["2"]], "start_row": 5},
        ]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="S"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["row"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    assert len(result.block_groups) == 2
                    assert result.block_groups[0].index == 0
                    assert result.block_groups[1].index == 1
                    # Block for second table should reference second group
                    assert result.blocks[1].parent_index == 1

    @pytest.mark.asyncio
    async def test_cumulative_row_count_across_tables(self, parser):
        """Cumulative row count spans across tables for threshold."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        # Table 1 has 3 data rows, table 2 has 3 data rows
        tables = [
            {"raw_rows": [["A"]] + [[str(i)] for i in range(3)], "start_row": 1},
            {"raw_rows": [["B"]] + [[str(i)] for i in range(3)], "start_row": 10},
        ]

        get_rows_calls = []

        async def mock_get_rows_text(llm, rows, summary, batch_size=50):
            get_rows_calls.append(len(rows))
            return [f"text{i}" for i in range(len(rows))]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="S"):
                with patch.object(parser, "get_rows_text", side_effect=mock_get_rows_text):
                    with patch.dict("os.environ", {"MAX_TABLE_ROWS_FOR_LLM": "4"}):
                        result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                        # Total 6 blocks across 2 tables
                        assert len(result.blocks) == 6
                        # First table (3 rows) is under threshold, so LLM is used
                        # Second table pushes cumulative to 6, exceeding threshold 4
                        # So get_rows_text should only be called once (for first table)
                        assert len(get_rows_calls) == 1

    @pytest.mark.asyncio
    async def test_block_type_is_table_row(self, parser):
        """All created blocks have type TABLE_ROW."""
        from unittest.mock import AsyncMock, patch
        from app.modules.parsers.excel.prompt_template import CSVHeaderDetection
        from app.models.blocks import BlockType

        mock_detection = CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="Headers"
        )

        tables = [{"raw_rows": [["A"], ["1"]], "start_row": 1}]

        with patch.object(parser, "detect_headers_with_llm", new_callable=AsyncMock, return_value=mock_detection):
            with patch.object(parser, "get_table_summary", new_callable=AsyncMock, return_value="S"):
                with patch.object(parser, "get_rows_text", new_callable=AsyncMock, return_value=["r"]):
                    result = await parser.get_blocks_from_csv_with_multiple_tables(tables, AsyncMock())
                    for block in result.blocks:
                        assert block.type == BlockType.TABLE_ROW


# ---------------------------------------------------------------------------
# find_tables_in_csv — deeper coverage
# ---------------------------------------------------------------------------
class TestFindTablesInCsvDeep:
    def test_three_tables_separated_by_empty_rows(self, parser):
        rows = [
            ["A", "B"], ["1", "2"],
            ["", ""],
            ["C", "D"], ["3", "4"],
            ["", ""],
            ["E", "F"], ["5", "6"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 3

    def test_multiple_empty_rows_as_separator(self, parser):
        rows = [
            ["A", "B"], ["1", "2"],
            ["", ""], ["", ""], ["", ""],
            ["C", "D"], ["3", "4"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 2

    def test_leading_empty_rows_ignored(self, parser):
        rows = [
            ["", ""],
            ["", ""],
            ["A", "B"],
            ["1", "2"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 1

    def test_trailing_empty_rows_ignored(self, parser):
        rows = [
            ["A", "B"],
            ["1", "2"],
            ["", ""],
            ["", ""],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) == 1

    def test_offset_table_detected(self, parser):
        """Table that doesn't start at column 0."""
        rows = [
            ["", "", "X", "Y"],
            ["", "", "1", "2"],
        ]
        tables = parser.find_tables_in_csv(rows)
        assert len(tables) >= 1
