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
    RowDescriptions,
    TableHeaders,
    csv_header_detection_prompt,
    csv_header_generation_prompt,
    row_text_prompt_for_csv,
    table_summary_prompt,
)
from app.utils.indexing_helpers import generate_simple_row_text, format_rows_with_index
from app.utils.streaming import (
    invoke_with_row_descriptions_and_reflection,
    invoke_with_structured_output_and_reflection,
)
from app.utils.logger import create_logger

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
        reader = csv.DictReader(
            file_stream, delimiter=self.delimiter, quotechar=self.quotechar
        )

        # Convert all rows to dictionaries and store them
        data = []
        line_numbers = []
        row_number = 2
        for row in reader:
            # Clean up the row data
            cleaned_row = {
                key: self._parse_value(value)
                for key, value in row.items()
                if key is not None  # Skip None keys that might appear in malformed CSVs
            }
            # Skip rows where all values are None
            if not all(value is None for value in cleaned_row.values()):
                # Store the actual line number from the CSV file
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _call_llm(self, llm, messages) -> Union[str, dict, list]:
        """Wrapper for LLM calls with retry logic"""
        return await llm.ainvoke(messages)

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
        try:
            if len(first_rows) < 2:
                # Not enough rows to analyze, assume headers exist
                return True

            # Prepare rows for prompt (convert to strings for display)
            row1 = [str(v) if v is not None else "" for v in first_rows[0]]
            row2 = [str(v) if v is not None else "" for v in first_rows[1]]
            row3 = [str(v) if v is not None else "" for v in first_rows[2]] if len(first_rows) > 2 else []
            row4 = [str(v) if v is not None else "" for v in first_rows[3]] if len(first_rows) > 3 else []
            row5 = [str(v) if v is not None else "" for v in first_rows[4]] if len(first_rows) > 4 else []
            row6 = [str(v) if v is not None else "" for v in first_rows[5]] if len(first_rows) > 5 else []
            
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
            if '</think>' in response.content:
                response.content = response.content.split('</think>')[-1]
            return response.content
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
    #  recordName, recordId, version, source, orgId, csv_binary, virtual_record_id
    async def get_blocks_from_csv_result(self, csv_result: List[Dict[str, Any]], line_numbers: List[int], llm: BaseChatModel) -> BlocksContainer:

        blocks = []
        children = []

        # Phase 1: Detect if headers are valid
        current_headers = list(csv_result[0].keys())
        first_rows = [current_headers]
        first_rows.extend([list(row.values()) for row in csv_result[:5]])
        
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
            # Reconstruct csv_result with new headers and all rows as data
            # First row gets line number 1 (original header line)
            csv_result = [{new_headers[i]: current_headers[i] for i in range(len(new_headers))}]
            # Reconstruct line_numbers: first row is line 1, then use existing line_numbers
            line_numbers = [1] + line_numbers
            
            # Reconstruct remaining rows with new headers
            csv_result.extend([
                {new_headers[i]: row[i] for i in range(len(new_headers))}
                for row in all_data_rows
            ])
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
