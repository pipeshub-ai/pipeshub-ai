import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Union

from langchain.chat_models.base import BaseChatModel
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
    row_text_prompt,
    table_summary_prompt,
)


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
        self.row_text_prompt = row_text_prompt
        self.delimiter = delimiter
        self.quotechar = quotechar
        self.encoding = encoding
        self.table_summary_prompt = table_summary_prompt


        # Configure retry parameters
        self.max_retries = 3
        self.min_wait = 1  # seconds
        self.max_wait = 10  # seconds

    def read_file(
        self, file_path: str | Path, encoding: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Read a CSV file and return its contents as a list of dictionaries.

        Args:
            file_path: Path to the CSV file
            encoding: Optional encoding to use for this specific read (overrides default)

        Returns:
            List of dictionaries where keys are column headers and values are row values

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

    def read_stream(self, file_stream: TextIO) -> List[Dict[str, Any]]:
        """
        Read a CSV from a file stream and return its contents as a list of dictionaries.

        Args:
            file_stream: An opened file stream containing CSV data

        Returns:
            List of dictionaries where keys are column headers and values are row values
        """
        reader = csv.DictReader(
            file_stream, delimiter=self.delimiter, quotechar=self.quotechar
        )

        # Convert all rows to dictionaries and store them
        data = []
        for row in reader:
            # Clean up the row data
            cleaned_row = {
                key: self._parse_value(value)
                for key, value in row.items()
                if key is not None  # Skip None keys that might appear in malformed CSVs
            }
            data.append(cleaned_row)

        if not data:
            raise ValueError("CSV file is empty or has no valid rows")

        return data

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
        self, llm, rows: List[Dict[str, Any]], batch_size: int = 10
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
                sheet_summary=" ",
                table_summary=" ",
                rows_data=json.dumps(rows_data, indent=2),
            )

            response = await self._call_llm(llm, messages)
            if '</think>' in response.content:
                response.content = response.content.split('</think>')[-1]
            # Try to extract JSON array from response
            try:
                processed_texts.extend(json.loads(response.content))
            except json.JSONDecodeError:
                # If that fails, try to find and parse a JSON array in the response
                content = response.content
                start = content.find("[")
                end = content.rfind("]")
                if start != -1 and end != -1:
                    try:
                        processed_texts.extend(json.loads(content[start : end + 1]))
                    except json.JSONDecodeError:
                        # If still can't parse, add response as single-item array
                        processed_texts.append(content)
                else:
                    # If no array found, add response as single-item array
                    processed_texts.append(content)

        return processed_texts
    #  recordName, recordId, version, source, orgId, csv_binary, virtual_record_id
    async def get_blocks_from_csv_result(self, csv_result: List[Dict[str, Any]], recordId: str, orgId: str, recordName: str, version: str, origin: str, llm: BaseChatModel) -> BlocksContainer:

        blocks = []
        children = []

        # Determine optimal batch size based on file size
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
                task = self.get_rows_text(llm, batch)
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
                # row_entry = {"number": idx, "content": row, "type": "row"}
                blocks.append(
                    Block(
                        index=idx,
                        type=BlockType.TABLE_ROW,
                        format=DataFormat.JSON,
                        data={
                            "row_natural_language_text": row_text,
                            "row_number": idx+1,
                            "row":json.dumps(row)
                        },
                        parent_index=0,
                    )
                    )
                children.append(BlockContainerIndex(block_index=idx))

        csv_markdown = self.to_markdown(csv_result)
        table_summary = await self.get_table_summary(llm, csv_result)
        blockGroup = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            format=DataFormat.JSON,
            table_metadata=TableMetadata(
                num_of_rows=len(csv_result),
                num_of_cols=len(csv_result[0]),
            ),
            data={
                "table_summary": table_summary,
                "column_headers": list(csv_result[0].keys()),
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
        read_data = parser.read_file(test_file)
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
