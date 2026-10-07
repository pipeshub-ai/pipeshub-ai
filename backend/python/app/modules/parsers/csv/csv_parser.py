import asyncio
import io
import json
import os
from datetime import datetime
from collections.abc import Callable
from typing import Any, Dict, List, Optional, TextIO, Tuple, TypeVar, Union

from app.config.configuration_service import ConfigurationService
from app.services.parsing.interface import ParseResult
from app.utils.llm import get_llm_for_role
from langchain_core.language_models.chat_models import BaseChatModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    TableMetadata,
)
from app.modules.parsers import parse_pool
from app.modules.parsers.csv import table_detection
from app.modules.parsers.csv.table_detection import DelimitedReadError, read_csv_tables
from app.modules.parsers.excel.prompt_template import (
    CSVHeaderDetection,
    TableHeaders,
    csv_header_generation_prompt,
    excel_header_detection_prompt,
    row_text_prompt_for_csv,
    table_summary_prompt,
)
from app.modules.parsers.text_decoding import decode_text
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.indexing_helpers import format_rows_with_index, generate_simple_row_text
from app.utils.logger import create_logger
from app.utils.streaming import (
    invoke_with_row_descriptions_and_reflection,
    invoke_with_structured_output_and_reflection,
)

logger = create_logger("csv_parser")

T = TypeVar("T")

# Module-level constants for CSV processing
NUM_SAMPLE_ROWS = 5  # Number of representative sample rows to select for header detection
DEFAULT_BATCH_SIZE = 50  # Default batch size for processing rows
MAX_CONCURRENT_BATCHES = 10  # Maximum number of concurrent batches for LLM calls
MAX_HEADER_GENERATION_ROWS = 10  # Maximum number of rows to use for header generation
MAX_SUMMARY_SAMPLE_ROWS = 10  # Maximum number of sample rows for table summary
MIN_ROWS_FOR_HEADER_ANALYSIS = 1  # Minimum number of rows required for header analysis
MAX_HEADER_DETECTION_ROWS = 10  # CSV uses 6 rows (vs Excel's 4)


# Below this a table's rows are converted inline: the loop is shorter than the
# thread hop that would avoid it.
ROWS_WORTH_A_THREAD = 1000


async def _off_loop_if_many_rows(row_count: int, fn: Callable[..., T], *args: object) -> T:
    if row_count >= ROWS_WORTH_A_THREAD:
        return await asyncio.to_thread(fn, *args)
    return fn(*args)


def _simple_row_blocks(
    rows: list[dict[str, Any]],
    line_numbers: list[int],
    first_index: int,
    table_group_index: int,
) -> list[Block]:
    """Row blocks in "column: value" form, for tables too large to describe with the LLM."""
    return [
        Block(
            index=first_index + idx,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data={
                "row_natural_language_text": generate_simple_row_text(row),
                "row_number": line_numbers[idx] if idx < len(line_numbers) else idx + 1,
            },
            parent_index=table_group_index,
        )
        for idx, row in enumerate(rows)
    ]


class CSVParser:
    def __init__(
        self, config_service: ConfigurationService, delimiter: str = ",", quotechar: str = '"', encoding: str = "utf-8"
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
        self.excel_header_detection_prompt = excel_header_detection_prompt
        self.csv_header_generation_prompt = csv_header_generation_prompt
        self.config_service = config_service

        # Configure retry parameters
        self.max_retries = 3
        self.min_wait = 1  # seconds
        self.max_wait = 10  # seconds

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
            llm, _ = await get_llm_for_role(self.config_service, "indexing", reasoning_effort="low")

            if parse_pool.should_offload(len(content)):
                tables = await self.read_tables_in_worker(content, record_name)
            else:
                try:
                    # Sync CSV scan; keep large files off the event loop.
                    all_rows = await asyncio.to_thread(self.read_raw_rows, io.StringIO(decode_text(content)))
                except Exception as e:
                    logger.warning("Could not read rows from CSV %s: %s", record_name, e)
                    all_rows = None
                # Detect multiple tables
                tables = await asyncio.to_thread(self.find_tables_in_csv, all_rows) if all_rows else None

            if tables is None:
                return ParseResult(
                    block_container=BlocksContainer(blocks=[], block_groups=[]),
                    metadata={
                        "record_name": record_name,
                    },
                )

            block_containers = await self.get_blocks_from_csv_with_multiple_tables(tables, llm)

            return ParseResult(
                block_container=block_containers,
                metadata={
                    "record_name": record_name,
                },
            )

    async def parse_to_blocks_lightweight(
        self,
        content: bytes,
        max_rows: int = 500,
    ) -> BlocksContainer:
        """Parse CSV/TSV bytes into blocks without LLM enrichment.

        Assumes the first non-empty row of each detected table is a header row.
        Caps data rows at ``max_rows`` across all tables to keep chat context bounded.
        """
        all_rows = await asyncio.to_thread(self.read_raw_rows, io.StringIO(decode_text(content)))

        if not all_rows:
            return BlocksContainer(blocks=[], block_groups=[])

        tables = await asyncio.to_thread(self.find_tables_in_csv, all_rows)
        return self._build_basic_block_container_from_tables(tables, max_rows=max_rows)

    async def read_tables_in_worker(
        self, content: bytes | str, record_name: str
    ) -> list[dict[str, Any]] | None:
        """Rows and table boundaries, read in a parse worker process.

        ``csv.reader`` holds the GIL for the whole file and table detection is
        a Python loop over every cell, so neither leaves the event loop free
        when run in a thread. ``None`` when the file is empty or unreadable.
        """
        try:
            return await parse_pool.submit(
                read_csv_tables, content, self.delimiter, self.quotechar,
                label=record_name, size=len(content),
            )
        except DelimitedReadError as e:
            logger.warning("Could not read rows from CSV %s: %s", record_name, e)
            return None

    def _build_basic_block_container_from_tables(
        self,
        tables: List[Dict[str, Any]],
        max_rows: int = 500,
    ) -> BlocksContainer:
        """Build TABLE → TABLE_ROW blocks from detected CSV tables without LLM calls."""
        blocks: List[Block] = []
        block_groups: List[BlockGroup] = []
        rows_emitted = 0

        for table_idx, table in enumerate(tables):
            if rows_emitted >= max_rows:
                break

            raw_rows = table.get("raw_rows") or []
            if not raw_rows:
                continue

            column_count = len(raw_rows[0])
            if column_count <= 0:
                continue

            # Lightweight path: treat the first row as headers.
            headers = [
                (v if v and v != "null" else f"Column_{i + 1}")
                for i, v in enumerate(raw_rows[0])
            ]
            # Pad/truncate to column_count.
            if len(headers) < column_count:
                headers.extend(f"Column_{i + 1}" for i in range(len(headers), column_count))
            headers = self._deduplicate_headers(headers[:column_count])

            data_rows = raw_rows[1:]
            remaining = max_rows - rows_emitted
            data_rows = data_rows[:remaining]
            start_row = int(table.get("start_row") or 1)

            tg_idx = len(block_groups)
            block_groups.append(
                BlockGroup(
                    index=tg_idx,
                    name=None,
                    type=GroupType.TABLE,
                    parent_index=None,
                    table_metadata=TableMetadata(
                        num_of_rows=len(data_rows),
                        num_of_cols=column_count,
                        num_of_cells=len(data_rows) * column_count,
                    ),
                    data={
                        "table_summary": "",
                        "column_headers": headers,
                        "table_number": table_idx + 1,
                    },
                    format=DataFormat.JSON,
                )
            )

            row_indices: List[int] = []
            for idx, row in enumerate(data_rows):
                row_dict = {
                    headers[i]: self._parse_value(row[i]) if i < len(row) else None
                    for i in range(column_count)
                }
                # Skip entirely empty rows.
                if all(value is None or value == "" or value == "null" for value in row_dict.values()):
                    continue

                bi = len(blocks)
                line_number = start_row + 1 + idx  # +1 skips the header row
                blocks.append(
                    Block(
                        index=bi,
                        type=BlockType.TABLE_ROW,
                        format=DataFormat.JSON,
                        data={
                            "row_natural_language_text": generate_simple_row_text(row_dict),
                            "row_number": line_number,
                            "row_end_number": line_number,
                            "row_count": 1,
                        },
                        parent_index=tg_idx,
                    )
                )
                row_indices.append(bi)

            block_groups[tg_idx].children = BlockGroupChildren.from_indices(
                block_indices=row_indices
            )
            # Update metadata with actual emitted row count (after empty-row skips).
            if block_groups[tg_idx].table_metadata is not None:
                block_groups[tg_idx].table_metadata.num_of_rows = len(row_indices)
                block_groups[tg_idx].table_metadata.num_of_cells = (
                    len(row_indices) * column_count
                )
            rows_emitted += len(row_indices)

        logger.info(
            "Lightweight CSV parsing complete: %d blocks, %d block groups (max_rows=%d)",
            len(blocks),
            len(block_groups),
            max_rows,
        )
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    def _parse_value(self, value: str) -> int | float | bool | str | None:
        """
        Parse a string value into its appropriate Python type.

        Args:
            value: String value to parse

        Returns:
            Parsed value as the appropriate type (int, float, bool, or string)
        """

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
        self, raw_rows: List[List[Any]]
    ) -> List[List[Any]]:
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

        for idx, row in enumerate(raw_rows):
            empty_count = sum(1 for value in row if value == "null")

            if empty_count == 0:
                # Perfect row with no empty values
                selected_rows.append((idx, row, empty_count))
                if len(selected_rows) >= MAX_HEADER_GENERATION_ROWS:
                    break  # Early stop - found enough perfect rows
            else:
                # Keep track of best non-perfect rows as fallback
                fallback_rows.append((idx, row, empty_count))

        # If we didn't find enough perfect rows, supplement with the best fallback rows
        if len(selected_rows) < MAX_HEADER_GENERATION_ROWS:
            # Sort fallback rows by empty count (ascending), then by index
            fallback_rows.sort(key=lambda x: (x[2], x[0]))
            # Add the best fallback rows to reach the target count
            needed = MAX_HEADER_GENERATION_ROWS - len(selected_rows)
            selected_rows.extend(fallback_rows[:needed])

        # Sort by original index to maintain logical order
        selected_rows.sort(key=lambda x: x[0])

        return [row for _, row, _ in selected_rows]

    def _deduplicate_headers(self, headers: List[str]) -> List[str]:
        """
        Add suffixes to duplicate headers: Header, Header_2, Header_3, etc.

        Args:
            headers: List of header strings (may contain duplicates)

        Returns:
            List of deduplicated headers with numeric suffixes
        """
        seen = {}
        deduplicated = []
        for header in headers:
            # Get base name (use Column_N for empty headers)
            base_name = header

            # Handle duplicates by adding numeric suffix
            if base_name in seen:
                seen[base_name] += 1
                unique_name = f"{base_name}_{seen[base_name]}"
            else:
                seen[base_name] = 1
                unique_name = base_name

            deduplicated.append(unique_name)

        return deduplicated


    def read_raw_rows(self, file_stream: TextIO) -> List[List[str]]:
        """
        Read CSV as raw list of lists without any processing.

        Args:
            file_stream: An opened file stream containing CSV data

        Returns:
            List of rows, where each row is a list of string values
        """
        return table_detection.read_raw_rows(file_stream, self.delimiter, self.quotechar)

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


    def _get_table(
        self,
        all_rows: list[list[Any]],
        start_row: int,
        start_col: int,
        visited_cells: set,
        max_cols: int
    ) -> dict[str, Any]:
        """Extract the table starting at (start_row, start_col). See ``table_detection.get_table``."""
        return table_detection.get_table(all_rows, start_row, start_col, visited_cells, max_cols)

    def find_tables_in_csv(self, all_rows: list[list[Any]]) -> list[dict[str, Any]]:
        """Find every table in the rows. See ``table_detection.find_tables``."""
        return table_detection.find_tables(all_rows)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _call_llm(self, llm, messages) -> Union[str, dict, list]:
        """Wrapper for LLM calls with retry logic"""
        return await llm.ainvoke(messages)


    def _concatenate_multirow_headers(
        self, multirow_headers: List[List[Any]], column_count: int
    ) -> List[str]:
        """
        Concatenate multi-row headers with underscores (same logic as Excel parser).

        Args:
            multirow_headers: List of header rows
            column_count: Expected number of columns

        Returns:
            List of concatenated header strings
        """
        logger.info(f"Concatenating {len(multirow_headers)} header rows")
        consolidated = []

        for col_idx in range(column_count):
            # Collect non-empty values from all header rows for this column
            parts = []
            seen = set()  # Track seen values to avoid duplicates

            for header_row in multirow_headers:
                if col_idx < len(header_row):
                    value = header_row[col_idx]
                    if value not in seen and value != "null":
                        parts.append(value)
                        seen.add(value)

            # Join with underscores or use generic name if no parts
            if parts:
                header = parts[-1]
            else:
                header = f"Column_{col_idx + 1}"

            consolidated.append(header)

        return consolidated

    async def process_table_with_header_info(
        self,
        raw_rows: List[List[Any]],
        detection: CSVHeaderDetection,
        start_row: int,
        llm: BaseChatModel
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """
        Unified processing path for all header scenarios.
        Handles single-row, multi-row, and no-header cases in one method.

        Args:
            raw_rows: All table rows without header assumptions
            detection: Header detection result from LLM
            start_row: Starting line number (1-based)
            llm: Language model for header generation if needed

        Returns:
            Tuple of (csv_result, line_numbers)
        """
        if not raw_rows:
            return ([], [])

        column_count = len(raw_rows[0]) if raw_rows else 0

        if detection.has_headers and detection.num_header_rows == 1:
            # Scenario 1: Single-row headers
            headers = [v if v != "null" else f"Column_{i+1}" for i, v in enumerate(raw_rows[0])]

            data_rows = raw_rows[1:]
            data_start_line = start_row + 1

        elif detection.has_headers and detection.num_header_rows > 1:
            # Scenario 2: Multi-row headers - concatenate them
            header_rows = raw_rows[:detection.num_header_rows]
            headers = self._concatenate_multirow_headers(header_rows, column_count)
            data_rows = raw_rows[detection.num_header_rows:]
            data_start_line = start_row + detection.num_header_rows

        else:
            # Scenario 3: No headers - generate them using smart sampling
            logger.info("No headers detected, generating with smart sampling")

            if len(raw_rows) > MAX_HEADER_GENERATION_ROWS:
                sample_rows = self._select_representative_sample_rows(raw_rows)
            else:
                sample_rows = raw_rows

            headers = await self.generate_headers_with_llm(sample_rows, column_count, llm)
            data_rows = raw_rows
            data_start_line = start_row


        headers = self._deduplicate_headers(headers)

        # A Python loop over every cell: seconds for a large table. In a thread
        # the interpreter hands the GIL back every few milliseconds, where
        # inline it held the event loop until the last row.
        return await _off_loop_if_many_rows(
            len(data_rows), self._rows_to_dicts, data_rows, headers, column_count, data_start_line
        )

    def _rows_to_dicts(
        self,
        data_rows: list[list[Any]],
        headers: list[str],
        column_count: int,
        data_start_line: int,
    ) -> tuple[list[dict[str, Any]], list[int]]:
        csv_result = []
        line_numbers = []

        for idx, row in enumerate(data_rows):

            # Create dictionary with parsed values
            row_dict = {
                headers[i]: self._parse_value(row[i])
                for i in range(column_count)
            }

            # Skip entirely empty rows
            if not all(value == "null" for value in row_dict.values()):
                csv_result.append(row_dict)
                line_numbers.append(data_start_line + idx)

        return (csv_result, line_numbers)

    async def detect_headers_with_llm(
        self, first_rows: List[List[Any]], llm: BaseChatModel
    ) -> CSVHeaderDetection:
        """
        Use LLM to detect if the first row contains valid headers and how many header rows exist.

        Args:
            first_rows: List of first 6 rows as lists of values
            llm: Language model instance

        Returns:
            CSVHeaderDetection object with has_headers, num_header_rows, confidence, and reasoning
        """
        try:
            if len(first_rows) < MIN_ROWS_FOR_HEADER_ANALYSIS:
                return CSVHeaderDetection(
                    has_headers=False,
                    num_header_rows=0,
                    confidence="low",
                    reasoning="Not enough rows to analyze, defaulting to no headers"
                )

            # Format rows for prompt (same format as Excel parser)
            rows_text_lines = []
            for i, row in enumerate(first_rows, start=1):
                rows_text_lines.append(f"Row {i}: {row}")

            rows_text = "\n".join(rows_text_lines)

            logger.info("Calling LLM for CSV header detection using Excel prompt")
            messages = self.excel_header_detection_prompt.format_messages(
                rows_text=rows_text
            )

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, messages, CSVHeaderDetection
            )

            if parsed_response is not None:
                # Validate and normalize the response
                if parsed_response.confidence == "high":
                # If has_headers=True but num_header_rows=0, correct to num_header_rows=1
                    if parsed_response.has_headers and parsed_response.num_header_rows == 0:
                        logger.warning("LLM returned has_headers=True but num_header_rows=0, correcting to 1")
                        parsed_response.num_header_rows = 1

                    # If has_headers=False, ensure num_header_rows=0
                    if not parsed_response.has_headers and parsed_response.num_header_rows > 0:
                        logger.warning(f"LLM returned has_headers=False but num_header_rows={parsed_response.num_header_rows}, correcting to 0")
                        parsed_response.num_header_rows = 0

                    return parsed_response

            # Fallback: assume single-row headers exist if LLM fails
            logger.warning("Header detection LLM call failed, defaulting to no headers")
            return CSVHeaderDetection(
                has_headers=False,
                num_header_rows=0,
                confidence="low",
                reasoning="LLM call failed, defaulting to no headers"
            )

        except Exception as e:
            logger.warning(f"Error in header detection: {e}, defaulting to no headers")
            return CSVHeaderDetection(
                has_headers=False,
                num_header_rows=0,
                confidence="low",
                reasoning=f"Error occurred: {str(e)}, defaulting to no headers"
            )

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

            # Format as JSON string for prompt
            sample_data_str = json.dumps(sample_data, indent=2)
            sample_count = len(sample_data)
            messages = self.csv_header_generation_prompt.format_messages(
                sample_data=sample_data_str,
                column_count=column_count,
                sample_count=sample_count,
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
            return [f"Column_{i+1}" for i in range(column_count)]

        except Exception as e:
            logger.warning(f"Error in header generation: {e}, using generic headers")
            return [f"Column_{i+1}" for i in range(column_count)]

    async def get_table_summary(self, llm, rows: List[Dict[str, Any]]) -> str:
        """Get table summary from LLM"""
        try:
            headers = list(rows[0].keys())
            sample_data = [
                {
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in row.items()
                }
                for row in rows[:MAX_SUMMARY_SAMPLE_ROWS]
            ]
            messages = self.table_summary_prompt.format_messages(
                sample_data=json.dumps(sample_data, indent=2),headers=headers
            )
            response = await self._call_llm(llm, messages)
            # Handle response - it could be a message object with .content or a string
            # Use getattr to safely access .content attribute
            content = getattr(response, 'content', None)  # type: ignore
            if content is not None:
                # Gemini and similar return content as a list of blocks, not a string.
                summary = coerce_message_content_to_text(content)
                if '</think>' in summary:
                    summary = summary.split('</think>')[-1]
                return summary
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
        self, llm, rows: List[Dict[str, Any]], table_summary: str, batch_size: int = DEFAULT_BATCH_SIZE
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

        # Generate placeholder names for empty headers and handle duplicates
        seen = {}
        processed_headers = []
        for i, col in enumerate(headers, start=1):
            # Get base name
            base_name = str(col).strip() if col and str(col).strip() else f"Column_{i}"

            # Handle duplicates by adding numeric suffix
            if base_name in seen:
                seen[base_name] += 1
                unique_name = f"{base_name}_{seen[base_name]}"
            else:
                seen[base_name] = 0
                unique_name = base_name

            processed_headers.append(unique_name)

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
        cumulative_row_count = 0

        # Process each table independently
        for table_idx, table in enumerate(tables):
            raw_rows = table["raw_rows"]

            if not raw_rows:
                continue

            # Prepare rows for header detection (first N rows)
            detection_rows = raw_rows[:MAX_HEADER_DETECTION_ROWS]

            # Detect headers using LLM
            detection = await self.detect_headers_with_llm(detection_rows, llm)
            logger.info(f"Table {table_idx + 1}: has_headers={detection.has_headers}, num_header_rows={detection.num_header_rows}")

            # Unified processing path - handles all three scenarios
            csv_result, line_numbers = await self.process_table_with_header_info(
                raw_rows,
                detection,
                table["start_row"],
                llm
            )

            if not csv_result:
                continue

            # Add current table rows to cumulative count
            table_row_count = len(csv_result)
            cumulative_row_count += table_row_count

            # Check if cumulative count exceeds threshold
            use_llm_for_rows = cumulative_row_count <= threshold

            # Get table summary (always use LLM)
            table_summary = await self.get_table_summary(llm, csv_result)

            # Create table BlockGroup
            table_group_index = len(block_groups)
            table_row_block_indices = []

            column_headers = list(csv_result[0].keys())

            if use_llm_for_rows:
                # Use LLM for row descriptions
                batches = []
                for i in range(0, len(csv_result), DEFAULT_BATCH_SIZE):
                    batch = csv_result[i : i + DEFAULT_BATCH_SIZE]
                    batches.append((i, batch))

                max_concurrent_batches = min(MAX_CONCURRENT_BATCHES, len(batches))
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
                                },
                                parent_index=table_group_index,
                            )
                        )
                        table_row_block_indices.append(block_index)
            else:
                # Use simple format for rows (skip LLM)
                row_blocks = await _off_loop_if_many_rows(
                    len(csv_result), _simple_row_blocks,
                    csv_result, line_numbers, len(blocks), table_group_index,
                )
                blocks.extend(row_blocks)
                table_row_block_indices.extend(block.index for block in row_blocks)

            num_of_rows = table_row_count
            num_of_cols = len(column_headers)
            num_of_cells = num_of_rows * num_of_cols

            table_group = BlockGroup(
                index=table_group_index,
                type=GroupType.TABLE,
                format=DataFormat.JSON,

                table_metadata=TableMetadata(
                    num_of_rows=num_of_rows,
                    num_of_cols=num_of_cols,
                    num_of_cells=num_of_cells,
                ),
                data={
                    "table_summary": table_summary,
                    "column_headers": column_headers,
                },
                children=BlockGroupChildren.from_indices(block_indices=table_row_block_indices),
            )
            block_groups.append(table_group)

        return BlocksContainer(blocks=blocks, block_groups=block_groups)


