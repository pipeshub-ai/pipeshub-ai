"""Reading a delimited file into rows and finding the tables in them.

Kept free of the CSV parser's LLM imports on purpose: a parse worker process
that only needs these functions should not load a language-model stack to run
them.
"""
from __future__ import annotations

import csv
import io
from typing import Any, TextIO

from app.modules.parsers.text_decoding import decode_text

__all__ = [
    "DelimitedReadError",
    "find_tables",
    "get_table",
    "read_csv_tables",
    "read_raw_rows",
]


class DelimitedReadError(ValueError):
    """The bytes could not be read as rows (bad quoting, an oversized field)."""


def read_raw_rows(file_stream: TextIO, delimiter: str = ",", quotechar: str = '"') -> list[list[str]]:
    """Read a delimited file as a raw list of rows, each a list of strings."""
    return list(csv.reader(file_stream, delimiter=delimiter, quotechar=quotechar))


def get_table(
    all_rows: list[list[Any]],
    start_row: int,
    start_col: int,
    visited_cells: set,
    max_cols: int
) -> dict[str, Any]:
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
        Dictionary with raw_rows (all rows without header assumptions) and metadata:
        - raw_rows: List of all rows in the table
        - start_row: Starting line number (1-based)
        - end_row: Ending line number (1-based)
        - column_count: Number of columns in the table
    """

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
                if value.strip():
                    has_data = True
                    max_col = col
                    break
        if not has_data:
            break

    # Find the last row of the table by scanning down
    max_row = start_row
    for row in range(start_row+1, max_row_in_file + 1):
        has_data = False
        # Check if this row has any data in the columns we've determined
        for col in range(start_col, max_col + 1):
            if row < len(all_rows) and col < len(all_rows[row]):
                value = all_rows[row][col]
                if value.strip():
                    has_data = True
                    max_row = row
                    break
        if not has_data and row != start_row+1:
            break

    # Now extract the rectangular table region
    # Process ALL rows uniformly without assuming first row is headers
    raw_rows = []


    # Extract ALL rows uniformly (including start_row)
    for row_idx in range(start_row, max_row + 1):
        if row_idx < len(all_rows):
            row = all_rows[row_idx]
            row_data = []
            for col in range(start_col, max_col + 1):
                if col < len(row):
                    value = row[col].strip()

                    if value:
                        row_data.append(value)
                        visited_cells.add((row_idx, col))
                    else:
                        row_data.append("null")
                else:
                    row_data.append("null")
            raw_rows.append(row_data)

    return {
        "raw_rows": raw_rows,  # All rows without header assumptions
        "start_row": start_row + 1,  # Convert to 1-based line numbers
        "end_row": max_row + 1,
    }


def find_tables(all_rows: list[list[Any]]) -> list[dict[str, Any]]:
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
        - raw_rows: List of all rows (without header assumptions)
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
                if value.strip():
                    # Found a potential table start - expand to find bounds
                    table = get_table(all_rows, row_idx, col_idx, visited_cells, max_cols)
                    tables.append(table)




    return tables


def read_csv_tables(
    content: bytes | str, delimiter: str = ",", quotechar: str = '"'
) -> list[dict[str, Any]] | None:
    """Read rows and find the tables in them, as one function a parse worker can be sent.

    ``None`` when the file has no rows.
    """
    try:
        all_rows = read_raw_rows(io.StringIO(decode_text(content)), delimiter, quotechar)
    except Exception as e:
        raise DelimitedReadError(str(e)) from None
    if not all_rows:
        return None
    return find_tables(all_rows)
