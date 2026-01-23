import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple, Union

from langchain_core.language_models.chat_models import BaseChatModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    TableMetadata,
)
from app.modules.parsers.excel.prompt_template import (
    HeaderDetection,
    TableHeaders,
    csv_header_detection_prompt,
    csv_header_generation_prompt,
    row_text_prompt_for_csv,
    table_summary_prompt,
)
from app.utils.indexing_helpers import format_rows_with_index, generate_simple_row_text
from app.utils.logger import create_logger
from app.utils.streaming import (
    invoke_with_row_descriptions_and_reflection,
    invoke_with_structured_output_and_reflection,
)

logger = create_logger("csv_parser")

class CSVParser:
    def __init__(
        self, delimiter: str = ",", quotechar: str = '"', encoding: str = "utf-8"
    ) -> None:
        """
        Initialize the CSV parser with configurable parameters.

        Args:
            delimiter: Character used to separate fields (default: comma)
            quotechar: Character used for quoting fields (default: double quote)
            encoding: File encoding (default: utf-8)
        """
        self.row_text_prompt = row_text_prompt_for_csv
        self.delimiter = delimiter
        self.quotechar = quotechar
        self.encoding = encoding
        self.table_summary_prompt = table_summary_prompt
        self.csv_header_detection_prompt = csv_header_detection_prompt
        self.csv_header_generation_prompt = csv_header_generation_prompt

        # Configure retry parameters
        self.max_retries = 3
        self.min_wait = 1  # seconds
        self.max_wait = 10  # seconds

    def read_file(
        self, file_path: str | Path, encoding: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[int]]: # line_numbers: List of line numbers from the CSV file
        """
        Read a CSV file and return its contents as a list of dictionaries.

        Args:
            file_path: Path to the CSV file
            encoding: Optional encoding to use for this specific read (overrides default)

        Returns:
            Tuple of (data, line_numbers) where:
            - data: List of dictionaries where keys are column headers and values are row values
            - line_numbers: List of actual line numbers from the CSV file, where line_numbers[i] corresponds to data[i]

        Raises:
            FileNotFoundError: If the specified file doesn't exist
            ValueError: If the CSV file is empty or malformed
            UnicodeDecodeError: If the file cannot be decoded with the specified encoding
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        # Use provided encoding or fall back to default
        file_encoding = encoding or self.encoding

        with open(file_path, "r", encoding=file_encoding) as file:
            return self.read_stream(file)

    def read_stream(self, file_stream: TextIO) -> Tuple[List[Dict[str, Any]], List[int]]:  # line_numbers: List of line numbers from the CSV file
        """
        Read a CSV from a file stream and return its contents as a list of dictionaries.

        Args:
            file_stream: An opened file stream containing CSV data

        Returns:
            Tuple of (data, line_numbers) where:
            - data: List of dictionaries where keys are column headers and values are row values
            - line_numbers: List of actual line numbers from the CSV file, where line_numbers[i] corresponds to data[i]
        """
        reader = csv.reader(
            file_stream, delimiter=self.delimiter, quotechar=self.quotechar
        )

        # Read all rows as lists first
        all_rows = list(reader)

        if not all_rows:
            raise ValueError("CSV file is empty or has no valid rows")

        # Use first row as headers, generating placeholder names for empty values
        first_row = all_rows[0]
        headers = [
            stripped if col and (stripped := str(col).strip()) else f"Column_{i}"
            for i, col in enumerate(first_row, start=1)
        ]

        # Convert remaining rows to dictionaries
        data = []
        line_numbers = []
        row_number = 2  # First data row starts at line 2 (line 1 is headers)

        for row in all_rows[1:]:
            # Pad row if it's shorter than headers, or truncate if longer
            padded_row = row + [""] * (len(headers) - len(row)) if len(row) < len(headers) else row[:len(headers)]

            # Create dictionary with parsed values
            cleaned_row = {
                headers[i]: self._parse_value(padded_row[i])
                for i in range(len(headers))
            }

            # Skip rows where all values are None
            if not all(value is None for value in cleaned_row.values()):
                line_numbers.append(row_number)
                data.append(cleaned_row)

            row_number += 1

        if not data:
            raise ValueError("CSV file is empty or has no valid rows")
        return (data, line_numbers)

    def to_markdown(self, data: List[Dict[str, Any]]) -> str:
        """
        Convert CSV data to markdown table format.
        Args:
            data: List of dictionaries from read_stream() method
        Returns:
            String containing markdown formatted table
        """
        if not data:
            return ""

        # Get headers from the first row
        headers = list(data[0].keys())

        # Start building the markdown table
        markdown_lines = []

        # Add header row
        header_row = "| " + " | ".join(str(header) for header in headers) + " |"
        markdown_lines.append(header_row)

        # Add separator row
        separator_row = "|" + "|".join(" --- " for _ in headers) + "|"
        markdown_lines.append(separator_row)

        # Add data rows
        for row in data:
            # Handle None values and convert to string, escape pipe characters
            formatted_values = []
            for header in headers:
                value = row.get(header, "")
                if value is None:
                    value = ""
                # Escape pipe characters and convert to string
                value_str = str(value).replace("|", "\\|")
                formatted_values.append(value_str)

            data_row = "| " + " | ".join(formatted_values) + " |"
            markdown_lines.append(data_row)

        return "\n".join(markdown_lines)

    def write_file(self, file_path: str | Path, data: List[Dict[str, Any]]) -> None:
        """
        Write data to a CSV file.

        Args:
            file_path: Path where the CSV file should be written
            data: List of dictionaries to write to the CSV

        Raises:
            ValueError: If the data is empty or malformed
        """
        if not data:
            raise ValueError("No data provided to write to CSV")

        file_path = Path(file_path)
        fieldnames = data[0].keys()

        with open(file_path, "w", encoding=self.encoding, newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
                delimiter=self.delimiter,
                quotechar=self.quotechar,
                quoting=csv.QUOTE_MINIMAL,
            )

            writer.writeheader()
            writer.writerows(data)

    def _parse_value(self, value: str) -> int | float | bool | str | None:
        """
        Parse a string value into its appropriate Python type.

        Args:
            value: String value to parse

        Returns:
            Parsed value as the appropriate type (int, float, bool, or string)
        """
        if value is None or value.strip() == "":
            return None

        # Remove leading/trailing whitespace
        value = value.strip()

        # Try to convert to boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"

        # Try to convert to integer
        try:
            return int(value)
        except ValueError:
            pass

        # Try to convert to float
        try:
            return float(value)
        except ValueError:
            pass

        # Return as string if no other type matches
        return value

    def _count_empty_values(self, row: Dict[str, Any]) -> int:
        """Count the number of empty/None values in a row"""
        return sum(1 for value in row.values() if value is None or value == "")

    def _select_representative_sample_rows(
        self, csv_result: List[Dict[str, Any]], num_sample_rows: int = 5
    ) -> List[Tuple[int, Dict[str, Any], int]]:
        """
        Select representative sample rows from CSV data by prioritizing rows with fewer empty values.
        
        This method selects up to num_sample_rows rows, prioritizing:
        1. Perfect rows with no empty values (stops early if enough are found)
        2. Rows with the fewest empty values as fallback
        
        Args:
            csv_result: List of dictionaries representing CSV rows
            num_sample_rows: Number of sample rows to select (default: 5)
            
        Returns:
            List of tuples (row_index, row_dict, empty_count) sorted by original index
        """
        selected_rows = []
        fallback_rows = []
        
        for idx, row in enumerate(csv_result):
            empty_count = self._count_empty_values(row)
            
            if empty_count == 0:
                # Perfect row with no empty values
                selected_rows.append((idx, row, empty_count))
                if len(selected_rows) >= num_sample_rows:
                    break  # Early stop - found enough perfect rows
            else:
                # Keep track of best non-perfect rows as fallback
                fallback_rows.append((idx, row, empty_count))
        
        # If we didn't find enough perfect rows, supplement with the best fallback rows
        if len(selected_rows) < num_sample_rows:
            # Sort fallback rows by empty count (ascending), then by index
            fallback_rows.sort(key=lambda x: (x[2], x[0]))
            # Add the best fallback rows to reach the target count
            needed = num_sample_rows - len(selected_rows)
            selected_rows.extend(fallback_rows[:needed])
        
        # Sort by original index to maintain logical order
        selected_rows.sort(key=lambda x: x[0])
        
        return selected_rows

    def _reconstruct_csv_with_new_headers(
        self,
        new_headers: List[str],
        current_headers: List[str],
        all_data_rows: List[List[Any]],
        first_row_line_number: int,
        existing_line_numbers: List[int]
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        Reconstruct csv_result with newly generated headers.
        
        This helper creates a new csv_result where:
        - The first row contains the old headers as data (with "null" for auto-generated columns)
        - Subsequent rows contain the actual data
        - All rows use the new headers as keys
        
        Args:
            new_headers: List of newly generated header names
            current_headers: List of current/old header names
            all_data_rows: All data rows as lists of values
            first_row_line_number: Line number for the first row (old headers)
            existing_line_numbers: Line numbers for the data rows
            
        Returns:
            Tuple of (reconstructed_csv_result, reconstructed_line_numbers)
        """
        # Create first row with old headers as data
        csv_result = [{
            new_headers[i]: "null" if current_headers[i].startswith("Column_") else current_headers[i]
            for i in range(len(new_headers))
        }]
        
        # Reconstruct line_numbers: first row gets specified line number, then existing
        line_numbers = [first_row_line_number] + existing_line_numbers
        
        # Add remaining data rows with new headers
        csv_result.extend([
            {new_headers[i]: row[i] for i in range(len(new_headers))}
            for row in all_data_rows
        ])
        
        return (csv_result, line_numbers)

    def read_raw_rows(self, file_stream: TextIO) -> List[List[str]]:
        """
        Read CSV as raw list of lists without any processing.
        
        Args:
            file_stream: An opened file stream containing CSV data
            
        Returns:
            List of rows, where each row is a list of string values
        """
        reader = csv.reader(
            file_stream, delimiter=self.delimiter, quotechar=self.quotechar
        )
        return list(reader)

    def _is_empty_row(self, row: List[Any], start_col: Optional[int] = None, end_col: Optional[int] = None) -> bool:
        """
        Check if all values in a row (or a range within the row) are None/empty.
        
        Args:
            row: List of values (can be strings or None)
            start_col: Optional starting column index (inclusive, 0-based)
            end_col: Optional ending column index (inclusive, 0-based)
            
        Returns:
            True if all values in the row (or specified range) are empty/None, False otherwise
        """
        if not row:
            return True
        
        # If range is specified, check only that range
        if start_col is not None and end_col is not None:
            values_to_check = row[start_col:end_col + 1] if start_col < len(row) else []
        elif start_col is not None:
            values_to_check = row[start_col:] if start_col < len(row) else []
        elif end_col is not None:
            values_to_check = row[:end_col + 1]
        else:
            values_to_check = row
        
        return all(
            value is None or (isinstance(value, str) and value.strip() == "")
            for value in values_to_check
        )

    def _is_empty_column(self, all_rows: List[List[Any]], col_idx: int, start_row: Optional[int] = None, end_row: Optional[int] = None) -> bool:
        """
        Check if a column is empty within a specific row range.
        
        Args:
            all_rows: List of all rows
            col_idx: Column index to check
            start_row: Optional starting row index (inclusive, 0-based)
            end_row: Optional ending row index (inclusive, 0-based)
            
        Returns:
            True if all values in the column (within specified range) are empty/None, False otherwise
        """
        if not all_rows:
            return True
        
        # Determine row range to check
        if start_row is not None and end_row is not None:
            rows_to_check = all_rows[start_row:end_row + 1]
        elif start_row is not None:
            rows_to_check = all_rows[start_row:]
        elif end_row is not None:
            rows_to_check = all_rows[:end_row + 1]
        else:
            rows_to_check = all_rows
        
        # Check all rows in the specified range
        for row in rows_to_check:
            if col_idx < len(row):
                value = row[col_idx]
                if value is not None and isinstance(value, str) and value.strip():
                    return False
        
        return True

    def _extract_rectangular_table(
        self, 
        all_rows: List[List[Any]], 
        start_row: int, 
        start_col: int, 
        end_row: int, 
        end_col: int
    ) -> Dict[str, Any]:
        """
        Extract a table from a bounded rectangular region.
        
        Args:
            all_rows: All rows from the CSV
            start_row: Starting row index (0-based)
            start_col: Starting column index (0-based)
            end_row: Ending row index (inclusive, 0-based)
            end_col: Ending column index (inclusive, 0-based)
            
        Returns:
            Dictionary with headers, data, and metadata
        """
        if start_row > end_row or start_col > end_col:
            return {
                "headers": [],
                "data": [],
                "start_row": start_row + 1,  # Convert to 1-based line numbers
                "end_row": end_row + 1,
                "column_count": 0,
            }
        
        # Extract header row
        header_row = all_rows[start_row][start_col:end_col + 1] if start_row < len(all_rows) else []
        
        # Extract data rows
        data_rows = []
        for row_idx in range(start_row + 1, min(end_row + 1, len(all_rows))):
            row = all_rows[row_idx]
            if row_idx < len(all_rows):
                # Extract columns for this row, pad if necessary
                row_data = row[start_col:end_col + 1] if start_col < len(row) else []
                # Pad to match column count
                while len(row_data) < len(header_row):
                    row_data.append("")
                data_rows.append(row_data[:len(header_row)])
        
        return {
            "headers": header_row,
            "data": data_rows,
            "start_row": start_row + 1,  # Convert to 1-based line numbers
            "end_row": end_row + 1,
            "column_count": len(header_row),
        }

    def _get_table(
        self,
        all_rows: List[List[Any]],
        start_row: int,
        start_col: int,
        visited_cells: set,
        max_cols: int
    ) -> Dict[str, Any]:
        """
        Extract a table starting from (start_row, start_col) by expanding to find rectangular bounds.
        
        This method finds the maximum column and row extent of the table by scanning:
        - Right until finding a column that's empty within the current region
        - Down until finding a row that's empty within the current region
        
        Args:
            all_rows: All rows from the CSV
            start_row: Starting row index (0-based)
            start_col: Starting column index (0-based)
            visited_cells: Set of (row, col) tuples to track processed cells
            max_cols: Maximum number of columns across all rows
            
        Returns:
            Dictionary with headers, data, and metadata
        """
        if start_row >= len(all_rows) or start_col < 0:
            return {
                "headers": [],
                "data": [],
                "start_row": start_row + 1,
                "end_row": start_row + 1,
                "column_count": 0,
            }
        
        # Find the last column of the table by scanning right
        max_col = start_col
        max_row_in_file = len(all_rows) - 1
        
        for col in range(start_col, max_cols):
            has_data = False
            # Check if this column has any data in the rows we've seen so far
            # We need to check from start_row downward to find where the table ends
            for r in range(start_row, max_row_in_file + 1):
                if r < len(all_rows) and col < len(all_rows[r]):
                    value = all_rows[r][col]
                    if value is not None and isinstance(value, str) and value.strip():
                        has_data = True
                        max_col = col
                        break
            if not has_data:
                break
        
        # Find the last row of the table by scanning down
        max_row = start_row
        for row in range(start_row, max_row_in_file + 1):
            has_data = False
            # Check if this row has any data in the columns we've determined
            for col in range(start_col, max_col + 1):
                if row < len(all_rows) and col < len(all_rows[row]):
                    value = all_rows[row][col]
                    if value is not None and isinstance(value, str) and value.strip():
                        has_data = True
                        max_row = row
                        break
            if not has_data:
                break
        
        # Now extract the rectangular table region
        table_data = []
        headers = []
        
        # Process header row
        if start_row < len(all_rows):
            header_row = all_rows[start_row]
            header_cells = []
            for col in range(start_col, max_col + 1):
                if col < len(header_row):
                    value = header_row[col]
                    header_cells.append(value)
                    visited_cells.add((start_row, col))
                else:
                    header_cells.append("")
            
            # Only consider it a header row if at least one cell has data
            if any(
                val is not None and (not isinstance(val, str) or val.strip())
                for val in header_cells
            ):
                headers = header_cells
                table_data.append(header_cells)
            else:
                return {
                    "headers": [],
                    "data": [],
                    "start_row": start_row + 1,
                    "end_row": start_row + 1,
                    "column_count": 0,
                }
        
        # Process data rows within the determined boundaries
        for row_idx in range(start_row + 1, max_row + 1):
            if row_idx < len(all_rows):
                row = all_rows[row_idx]
                row_data = []
                for col in range(start_col, max_col + 1):
                    if col < len(row):
                        value = row[col]
                        row_data.append(value)
                        if value is not None and isinstance(value, str) and value.strip():
                            visited_cells.add((row_idx, col))
                    else:
                        row_data.append("")
                table_data.append(row_data)
        
        return {
            "headers": headers,
            "data": table_data[1:] if table_data else [],  # Skip header row from data
            "start_row": start_row + 1,  # Convert to 1-based line numbers
            "end_row": max_row + 1,
            "column_count": len(headers),
        }

    def find_tables_in_csv(self, all_rows: List[List[Any]]) -> List[Dict[str, Any]]:
        """
        Find and extract all tables from CSV rows using region-growing approach.
        
        Detection criteria:
        A table is a rectangular region surrounded by empty rows & empty columns.
        Boundaries only need to be empty within the context of that region, not globally.
        File edges count as boundaries (no empty rows/columns needed at edges).
        
        Args:
            all_rows: List of all rows from CSV (each row is a list of values)
            
        Returns:
            List of table dictionaries, each containing:
            - headers: List of header values
            - data: List of data rows (as lists)
            - start_row: Starting line number (1-based)
            - end_row: Ending line number (1-based)
            - column_count: Number of columns
        """
        if not all_rows:
            return []
        
        tables = []
        visited_cells: set = set()  # Track already processed cells as (row, col) tuples
        
        # Find maximum column count across all rows
        max_cols = max(len(row) for row in all_rows) if all_rows else 0
        
        # Scan for tables: iterate through all rows and columns
        for row_idx in range(len(all_rows)):
            for col_idx in range(max_cols):
                # Check if this cell has data and hasn't been visited
                if (row_idx, col_idx) in visited_cells:
                    continue
                
                # Check if cell has non-empty data
                if row_idx < len(all_rows) and col_idx < len(all_rows[row_idx]):
                    value = all_rows[row_idx][col_idx]
                    if value is not None and isinstance(value, str) and value.strip():
                        # Found a potential table start - expand to find bounds
                        table = self._get_table(all_rows, row_idx, col_idx, visited_cells, max_cols)
                        
                        # Check if table has meaningful data
                        has_data = any(
                            val is not None and (not isinstance(val, str) or val.strip())
                            for val in table["headers"]
                        ) or any(
                            any(val is not None and (not isinstance(val, str) or val.strip()) for val in row)
                            for row in table["data"]
                        )
                        
                        if has_data:
                            tables.append(table)
        
        # If no tables detected, treat entire file as single table
        if not tables:
            max_col = max((len(row) - 1 for row in all_rows), default=0)
            table = self._extract_rectangular_table(
                all_rows,
                0,
                0,
                len(all_rows) - 1,
                max_col
            )
            if table["data"] or any(
                val is not None and (not isinstance(val, str) or val.strip())
                for val in table["headers"]
            ):
                tables.append(table)
        
        return tables

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _call_llm(self, llm, messages) -> Union[str, dict, list]:
        """Wrapper for LLM calls with retry logic"""
        return await llm.ainvoke(messages)

    def _convert_rows_to_strings(self, rows: List[List[Any]], num_rows: int = 6) -> List[List[str]]:
        """
        Convert multiple rows to lists of strings.

        Args:
            rows: List of rows where each row is a list of values
            num_rows: Number of rows to convert (default: 6)

        Returns:
            List of converted rows, where each row is a list of strings.
            Returns empty list for rows that don't exist.
        """
        return [
            [str(v) if v is not None else "" for v in rows[i]] if i < len(rows) else []
            for i in range(num_rows)
        ]

    async def detect_headers_with_llm(
        self, first_rows: List[List[Any]], llm: BaseChatModel
    ) -> bool:
        """
        Use LLM to detect if the first row contains valid headers.

        Args:
            first_rows: List of first 6 rows as lists of values
            llm: Language model instance

        Returns:
            True if valid headers detected, False otherwise
        """
        # Constants for header detection
        MIN_ROWS_FOR_ANALYSIS = 2

        try:
            if len(first_rows) < MIN_ROWS_FOR_ANALYSIS:
                # Not enough rows to analyze, assume headers exist
                return True

            # Prepare rows for prompt (convert to strings for display)
            row1, row2, row3, row4, row5, row6 = self._convert_rows_to_strings(first_rows, 6)

            messages = self.csv_header_detection_prompt.format_messages(
                row1=row1,
                row2=row2,
                row3=row3,
                row4=row4,
                row5=row5,
                row6=row6,
            )

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, messages, HeaderDetection
            )

            print(f"parsed_responseeeeee: {parsed_response}")

            if parsed_response is not None:
                # Only trust high-confidence detections, or if low confidence but says has_headers
                if parsed_response.confidence == "high":
                    return parsed_response.has_headers
                elif parsed_response.confidence == "low" and parsed_response.has_headers:
                    # Low confidence but says headers exist - be conservative and keep them
                    return True
                else:
                    # Low confidence and says no headers - generate new ones
                    return False

            # Fallback: assume headers exist if LLM fails
            logger.warning("Header detection LLM call failed, defaulting to assuming headers exist")
            return True

        except Exception as e:
            logger.warning(f"Error in header detection: {e}, defaulting to assuming headers exist")
            return True

    async def generate_headers_with_llm(
        self, sample_data: List[List[Any]], column_count: int, llm: BaseChatModel
    ) -> List[str]:
        """
        Generate descriptive headers from data samples using LLM.

        Args:
            sample_data: List of data rows (each row is a list of values)
            column_count: Number of columns expected
            llm: Language model instance

        Returns:
            List of generated header names
        """
        try:
            # Format sample data for display
            formatted_samples = []
            for row in sample_data[:10]:  # Use first 10 rows max
                formatted_row = [str(v) if v is not None else "" for v in row]
                formatted_samples.append(formatted_row)

            # Format as JSON string for prompt
            sample_data_str = json.dumps(formatted_samples, indent=2)

            messages = self.csv_header_generation_prompt.format_messages(
                sample_data=sample_data_str,
                column_count=column_count,
                sample_count=len(formatted_samples),
            )

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, messages, TableHeaders
            )

            if parsed_response is not None and parsed_response.headers:
                generated_headers = parsed_response.headers

                # Validate header count matches column count
                if len(generated_headers) == column_count:
                    return generated_headers
                else:
                    logger.warning(
                        f"Generated header count ({len(generated_headers)}) doesn't match "
                        f"column count ({column_count}), using fallback headers"
                    )

            # Fallback: generate generic headers
            logger.warning("Header generation LLM call failed, using generic headers")
            return [f"Column_{i}" for i in range(column_count)]

        except Exception as e:
            logger.warning(f"Error in header generation: {e}, using generic headers")
            return [f"Column_{i}" for i in range(column_count)]

    async def get_table_summary(self, llm, rows: List[Dict[str, Any]]) -> str:
        """Get table summary from LLM"""
        try:
            headers = list(rows[0].keys())
            sample_data = [
                {
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in row.items()
                }
                for row in rows[:3]
            ]
            messages = self.table_summary_prompt.format_messages(
                sample_data=json.dumps(sample_data, indent=2),headers=headers
            )
            response = await self._call_llm(llm, messages)
            # Handle response - it could be a message object with .content or a string
            # Use getattr to safely access .content attribute
            content = getattr(response, 'content', None)  # type: ignore
            if content is not None:
                if '</think>' in content:
                    content = content.split('</think>')[-1]
                return content
            elif isinstance(response, str):
                if '</think>' in response:
                    return response.split('</think>')[-1]
                return response
            else:
                # Fallback for dict/list responses
                return str(response)
        except Exception:
            raise

    async def get_rows_text(
        self, llm, rows: List[Dict[str, Any]], table_summary: str, batch_size: int = 50
    ) -> List[str]:
        """Convert multiple rows into natural language text in batches."""
        processed_texts = []

        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            # Prepare rows data
            rows_data = [
                {
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in row.items()
                }
                for row in batch
            ]

            # Get natural language text from LLM with retry
            messages = self.row_text_prompt.format_messages(
                table_summary=table_summary,
                numbered_rows_data=format_rows_with_index(rows_data),
                row_count=len(rows_data)
            )

            # Default to string representations of rows
            descriptions = [generate_simple_row_text(row) for row in batch]

            # Use centralized utility with reflection and count validation
            parsed_response = await invoke_with_row_descriptions_and_reflection(
                llm, messages, expected_count=len(rows_data)
            )

            if parsed_response is not None and parsed_response.descriptions:
                descriptions = parsed_response.descriptions

            processed_texts.extend(descriptions)

        return processed_texts
    def convert_table_to_dict(self, table: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        Convert a table (with headers and data rows as lists) to dictionary format.
        
        Args:
            table: Table dictionary with headers and data
            
        Returns:
            Tuple of (data, line_numbers) where:
            - data: List of dictionaries where keys are column headers
            - line_numbers: List of line numbers (1-based)
        """
        headers = table["headers"]
        data_rows = table["data"]
        start_row = table["start_row"]
        
        # Generate placeholder names for empty headers
        processed_headers = [
            stripped if col and (stripped := str(col).strip()) else f"Column_{i}"
            for i, col in enumerate(headers, start=1)
        ]
        
        # Convert rows to dictionaries
        data = []
        line_numbers = []
        
        for idx, row in enumerate(data_rows):
            # Pad row if shorter than headers, or truncate if longer
            padded_row = row + [""] * (len(processed_headers) - len(row)) if len(row) < len(processed_headers) else row[:len(processed_headers)]
            
            # Create dictionary with parsed values
            cleaned_row = {
                processed_headers[i]: self._parse_value(padded_row[i])
                for i in range(len(processed_headers))
            }
            
            # Skip rows where all values are None
            if not all(value is None for value in cleaned_row.values()):
                line_numbers.append(start_row + idx + 1)  # +1 because data starts after header
                data.append(cleaned_row)
        
        return (data, line_numbers)

    async def get_blocks_from_csv_with_multiple_tables(
        self, 
        tables: List[Dict[str, Any]], 
        llm: BaseChatModel
    ) -> BlocksContainer:
        """
        Process multiple tables from CSV and create BlocksContainer.
        
        Args:
            tables: List of table dictionaries from find_tables_in_csv()
            llm: Language model instance
            
        Returns:
            BlocksContainer with multiple TABLE BlockGroups
        """
        blocks: List[Block] = []
        block_groups: List[BlockGroup] = []
        
        # Get threshold from environment variable (default: 1000)
        threshold = int(os.getenv("MAX_TABLE_ROWS_FOR_LLM", "1000"))
        
        # Track cumulative row count at record level
        cumulative_row_count = [0]
        
        # Process each table independently
        for table_idx, table in enumerate(tables):
            # Convert table to dictionary format
            csv_result, line_numbers = self.convert_table_to_dict(table)
            
            if not csv_result:
                continue
            
            # Phase 1: Detect if headers are valid for this table
            current_headers = list(csv_result[0].keys())
            first_rows = [current_headers]
            
            # Select representative sample rows using helper method
            NUM_SAMPLE_ROWS = 5
            selected_rows = self._select_representative_sample_rows(csv_result, NUM_SAMPLE_ROWS)
            first_rows.extend([list(row[1].values()) for row in selected_rows])
            
            has_valid_headers = await self.detect_headers_with_llm(first_rows, llm)
            
            # Phase 2: Generate headers if needed
            if not has_valid_headers:
                logger.info(f"No valid headers detected for table {table_idx + 1}, generating headers with LLM")
                all_data_rows = [list(row.values()) for row in csv_result]
                
                new_headers = await self.generate_headers_with_llm(
                    all_data_rows[:10],
                    len(current_headers),
                    llm
                )
                
                # Reconstruct csv_result with new headers using helper
                csv_result, line_numbers = self._reconstruct_csv_with_new_headers(
                    new_headers,
                    current_headers,
                    all_data_rows,
                    table["start_row"],
                    line_numbers
                )
            else:
                logger.info(f"Valid headers detected for table {table_idx + 1}, using existing headers")
            
            # Add current table rows to cumulative count
            table_row_count = len(csv_result)
            cumulative_row_count[0] += table_row_count
            
            # Check if cumulative count exceeds threshold
            use_llm_for_rows = cumulative_row_count[0] <= threshold
            
            # Get table summary (always use LLM)
            table_summary = await self.get_table_summary(llm, csv_result)
            
            # Create table BlockGroup
            table_group_index = len(block_groups)
            table_group_children: List[BlockContainerIndex] = []
            
            column_headers = list(csv_result[0].keys())
            
            if use_llm_for_rows:
                # Use LLM for row descriptions
                batch_size = 50
                batches = []
                for i in range(0, len(csv_result), batch_size):
                    batch = csv_result[i : i + batch_size]
                    batches.append((i, batch))
                
                max_concurrent_batches = min(10, len(batches))
                batch_results = []
                
                for i in range(0, len(batches), max_concurrent_batches):
                    current_batches = batches[i:i + max_concurrent_batches]
                    
                    batch_tasks = []
                    for start_idx, batch in current_batches:
                        task = self.get_rows_text(llm, batch, table_summary)
                        batch_tasks.append((start_idx, batch, task))
                    
                    task_results = await asyncio.gather(*[task for _, _, task in batch_tasks])
                    
                    for j, (start_idx, batch, _) in enumerate(batch_tasks):
                        row_texts = task_results[j]
                        batch_results.append((start_idx, batch, row_texts))
                
                # Create blocks for this table
                for start_idx, batch, row_texts in batch_results:
                    for idx, (row, row_text) in enumerate(zip(batch, row_texts), start=start_idx):
                        block_index = len(blocks)
                        actual_row_number = line_numbers[idx] if idx < len(line_numbers) else idx + 1
                        
                        blocks.append(
                            Block(
                                index=block_index,
                                type=BlockType.TABLE_ROW,
                                format=DataFormat.JSON,
                                data={
                                    "row_natural_language_text": row_text,
                                    "row_number": actual_row_number,
                                    "row": json.dumps(row)
                                },
                                parent_index=table_group_index,
                            )
                        )
                        table_group_children.append(BlockContainerIndex(block_index=block_index))
            else:
                # Use simple format for rows (skip LLM)
                for idx, row in enumerate(csv_result):
                    block_index = len(blocks)
                    actual_row_number = line_numbers[idx] if idx < len(line_numbers) else idx + 1
                    row_text = generate_simple_row_text(row)
                    
                    blocks.append(
                        Block(
                            index=block_index,
                            type=BlockType.TABLE_ROW,
                            format=DataFormat.JSON,
                            data={
                                "row_natural_language_text": row_text,
                                "row_number": actual_row_number,
                                "row": json.dumps(row)
                            },
                            parent_index=table_group_index,
                        )
                    )
                    table_group_children.append(BlockContainerIndex(block_index=block_index))
            
            # Create markdown for this table
            csv_markdown = self.to_markdown(csv_result)
            
            table_group = BlockGroup(
                index=table_group_index,
                type=GroupType.TABLE,
                format=DataFormat.JSON,
                table_metadata=TableMetadata(
                    num_of_rows=len(csv_result),
                    num_of_cols=len(column_headers),
                ),
                data={
                    "table_summary": table_summary,
                    "column_headers": column_headers,
                    "table_markdown": csv_markdown,
                },
                children=table_group_children,
            )
            block_groups.append(table_group)
        
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    #  recordName, recordId, version, source, orgId, csv_binary, virtual_record_id
    async def get_blocks_from_csv_result(self, csv_result: List[Dict[str, Any]], line_numbers: List[int], llm: BaseChatModel) -> BlocksContainer:

        blocks = []
        children = []

        # Phase 1: Detect if headers are valid
        current_headers = list(csv_result[0].keys())
        first_rows = [current_headers]

        # Select representative sample rows using helper method
        NUM_SAMPLE_ROWS = 5
        selected_rows = self._select_representative_sample_rows(csv_result, NUM_SAMPLE_ROWS)
        first_rows.extend([list(row[1].values()) for row in selected_rows])

        has_valid_headers = await self.detect_headers_with_llm(first_rows, llm)

        # Phase 2: Generate headers if needed
        if not has_valid_headers:
            logger.info("No valid headers detected, generating headers with LLM")
            # Treat all rows as data (including first row which was treated as header)
            # Convert all rows to list format, preserving line numbers
            all_data_rows = [list(row.values()) for row in csv_result]

            new_headers = await self.generate_headers_with_llm(
                all_data_rows[:10],  # Use first 10 rows as sample
                len(current_headers),
                llm
            )
            
            # Reconstruct csv_result with new headers using helper
            csv_result, line_numbers = self._reconstruct_csv_with_new_headers(
                new_headers,
                current_headers,
                all_data_rows,
                1,  # First row gets line number 1 (original header line)
                line_numbers
            )
        else:
            logger.info("Valid headers detected, using existing headers")

        # Get threshold from environment variable (default: 1000)
        threshold = int(os.getenv("MAX_TABLE_ROWS_FOR_LLM", "1000"))

        # Check if table exceeds threshold
        use_llm_for_rows = len(csv_result) <= threshold
        table_summary = await self.get_table_summary(llm, csv_result)

        if use_llm_for_rows:
            # Use LLM for row descriptions
            batch_size = 50

            # Create batches
            batches = []
            for i in range(0, len(csv_result), batch_size):
                batch = csv_result[i : i + batch_size]
                batches.append((i, batch))  # Store start index and batch data

            # Process batches with controlled concurrency to avoid overwhelming the system
            max_concurrent_batches = min(10, len(batches))  # Limit concurrent batches
            batch_results = []

            for i in range(0, len(batches), max_concurrent_batches):
                current_batches = batches[i:i + max_concurrent_batches]

                # Process current batch group
                batch_tasks = []
                for start_idx, batch in current_batches:
                    task = self.get_rows_text(llm, batch, table_summary)
                    batch_tasks.append((start_idx, batch, task))

                # Wait for current batch group to complete
                task_results = await asyncio.gather(*[task for _, _, task in batch_tasks])

                # Combine results with their metadata
                for j, (start_idx, batch, _) in enumerate(batch_tasks):
                    row_texts = task_results[j]
                    batch_results.append((start_idx, batch, row_texts))

            # Process results and create blocks
            for start_idx, batch, row_texts in batch_results:

                for idx, (row, row_text) in enumerate(
                        zip(batch, row_texts), start=start_idx
                    ):
                    # Use actual line number from separate list
                    actual_row_number = line_numbers[idx] if idx < len(line_numbers) else idx + 1
                    blocks.append(
                        Block(
                            index=idx,
                            type=BlockType.TABLE_ROW,
                            format=DataFormat.JSON,
                            data={
                                "row_natural_language_text": row_text,
                                "row_number": actual_row_number,
                                "row": json.dumps(row)
                            },
                            parent_index=0,
                        )
                        )
                    children.append(BlockContainerIndex(block_index=idx))
        else:
            # Use simple format for rows (skip LLM)
            for idx, row in enumerate(csv_result):
                # Use actual line number from separate list
                actual_row_number = line_numbers[idx] if idx < len(line_numbers) else idx+1
                row_text = generate_simple_row_text(row)
                blocks.append(
                    Block(
                        index=idx,
                        type=BlockType.TABLE_ROW,
                        format=DataFormat.JSON,
                        data={
                            "row_natural_language_text": row_text,
                            "row_number": actual_row_number,
                            "row": json.dumps(row)
                        },
                        parent_index=0,
                    )
                )
                children.append(BlockContainerIndex(block_index=idx))

        csv_markdown = self.to_markdown(csv_result)
        column_headers = list(csv_result[0].keys())
        blockGroup = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            format=DataFormat.JSON,
            table_metadata=TableMetadata(
                num_of_rows=len(csv_result),
                num_of_cols=len(column_headers),
            ),
            data={
                "table_summary": table_summary,
                "column_headers": column_headers,
                "table_markdown": csv_markdown,
            },
            children=children,
        )
        blocks_container = BlocksContainer(blocks=blocks, block_groups=[blockGroup])
        return blocks_container

def main() -> None:
    """Test the CSV parser functionality"""
    # Create sample data
    test_data = [
        {
            "name": "John Doe",
            "age": "30",
            "active": "true",
            "salary": "50000.50",
            "notes": "Senior Developer",
        },
        {
            "name": "Jane Smith",
            "age": "25",
            "active": "false",
            "salary": "45000.75",
            "notes": "Junior Developer",
        },
    ]

    parser = CSVParser()
    test_file = "test_output.csv"

    try:
        # Test writing
        print("Writing test data to CSV...")
        parser.write_file(test_file, test_data)
        print(f"✅ Successfully wrote data to {test_file}")

        # Test reading
        print("\nReading test data from CSV...")
        read_data, line_numbers = parser.read_file(test_file)
        print("✅ Successfully read data from CSV")
        print("\nParsed data:")
        for row in read_data:
            print(row)

        # Verify data types
        print("\nVerifying data types:")
        first_row = read_data[0]
        print(f"name (str): {first_row['name']} ({type(first_row['name'])})")
        print(f"age (int): {first_row['age']} ({type(first_row['age'])})")
        print(
            f"""active (bool): {first_row['active']} ({
              type(first_row['active'])})"""
        )
        print(
            f"""salary (float): {
              first_row['salary']} ({type(first_row['salary'])})"""
        )

    finally:
        # Clean up test file
        if os.path.exists(test_file):
            os.remove(test_file)
            print(f"\nℹ️ Cleaned up test file: {test_file}")


if __name__ == "__main__":
    main()
