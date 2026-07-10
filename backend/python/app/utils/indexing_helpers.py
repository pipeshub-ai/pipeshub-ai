import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Template
from pydantic import BaseModel, Field

from app.modules.parsers.excel.prompt_template import RowDescriptions, row_text_prompt
from app.utils.llm import get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection

# Maximum number of table rows to include in summary prompts
MAX_TABLE_ROWS_FOR_SUMMARY = 20


class TableSummary(BaseModel):
    summary: str = Field(description="Summary of the table")
    headers: list[str] = Field(description="Column headers of the table")


table_summary_prompt_template = """
# Task:
Provide a clear summary of this table's purpose and content. Also provide the column headers of the table.

# Table Data:
{{table_data}}

# Output Format:
You must return a single valid JSON object with the following structure:
{
    "summary": "Summary of the table",
    "headers": ["Column headers of the table. If no headers are found, return an empty array."]
}

Return the JSON object only, no additional text or explanation.
"""


async def get_table_summary_n_headers(config, table_data) -> Optional[TableSummary]:
    """
    Use LLM to generate a concise summary from table data (grid or structured data).

    Args:
        config: Configuration service
        table_data: Can be:
            - list[list[str]]: Grid representation of table (list of rows, each row is list of cell values)
            - list[dict]: List of dictionaries with column headers as keys (CSV/Excel format)

    Returns:
        TableSummary Pydantic model with summary and headers, or None if parsing fails.
    """
    try:
        # Get LLM
        llm, _ = await get_llm_for_role(config, "indexing")

        # Convert table data to text representation
        if isinstance(table_data, list):
            if len(table_data) > 0:
                if isinstance(table_data[0], list):
                    # Grid format: list of lists
                    # Format as simple text table
                    table_text_lines = []
                    for row in table_data[:MAX_TABLE_ROWS_FOR_SUMMARY]:
                        row_text = " | ".join(str(cell) if cell else "" for cell in row)
                        table_text_lines.append(row_text)
                    table_data_str = "\n".join(table_text_lines)
                    if len(table_data) > MAX_TABLE_ROWS_FOR_SUMMARY:
                        table_data_str += f"\n... ({len(table_data) - MAX_TABLE_ROWS_FOR_SUMMARY} more rows)"
                elif isinstance(table_data[0], dict):
                    # Structured format: list of dicts
                    # Format as column: value pairs
                    headers = list(table_data[0].keys())
                    table_data_str = f"Headers: {', '.join(headers)}\n\n"
                    for i, row in enumerate(table_data[:MAX_TABLE_ROWS_FOR_SUMMARY]):
                        row_str = ", ".join(f"{k}: {v}" for k, v in row.items())
                        table_data_str += f"Row {i+1}: {row_str}\n"
                    if len(table_data) > MAX_TABLE_ROWS_FOR_SUMMARY:
                        table_data_str += f"\n... ({len(table_data) - MAX_TABLE_ROWS_FOR_SUMMARY} more rows)"
                else:
                    table_data_str = str(table_data)
            else:
                table_data_str = "Empty table"
        else:
            table_data_str = str(table_data)

        # Prepare prompt
        template = Template(table_summary_prompt_template)
        rendered_form = template.render(table_data=table_data_str)
        messages = [
            {
                "role": "system",
                "content": "You are a data analysis expert.",
            },
            {"role": "user", "content": rendered_form},
        ]

        # Use centralized utility with reflection
        parsed_response = await invoke_with_structured_output_and_reflection(
            llm, messages, TableSummary
        )

        if parsed_response is not None:
            return parsed_response
        else:
            # Return a default TableSummary if parsing fails
            return TableSummary(summary="", headers=[])
    except Exception as e:
        raise e


async def get_rows_text(
    config, table_data: dict, table_summary: str, column_headers: list[str]
) -> Tuple[List[str], List[List[dict]]]:
    """Convert multiple rows into natural language text using context from summaries in a single prompt"""
    table = table_data.get("grid")
    if table:
        try:
            # Skip first row if column_headers provided (first row is header)
            if column_headers:
                table_rows = table[1:]
            else:
                table_rows = table

            rows_data = [
                {
                    column_headers[i] if column_headers and i<len(column_headers) else f"Column_{i+1}": (
                        cell.get("text", "") if isinstance(cell, dict) else cell
                    )
                    for i, cell in enumerate(row)
                }
                for row in table_rows
            ]

            # Get natural language text from LLM with retry
            messages = row_text_prompt.format_messages(
                table_summary=table_summary, rows_data=json.dumps(rows_data, indent=2, ensure_ascii=False)
            )
            llm, _ = await get_llm_for_role(config, "indexing")

            # Default to string representations of rows
            descriptions = [str(row) for row in rows_data]

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, messages, RowDescriptions
            )

            if parsed_response is not None and parsed_response.descriptions:
                descriptions = parsed_response.descriptions

            return descriptions, table_rows
        except Exception:
            raise
    else:
        return [], []


def generate_simple_row_text(row_data: Dict[str, Any]) -> str:
    """
    Generate simple row text in "column: value" format without using LLM.
    Args:
        row_data: Dictionary with column names as keys and cell values as values
    Returns:
        String in format "Column1: value1, Column2: value2, ..."
    """
    parts = []
    for key, value in row_data.items():
        # Convert value to string, handle None values
        value_str = str(value) if value is not None else "null"
        parts.append(f"{key}: {value_str}")
    return ", ".join(parts)


def image_bytes_to_base64(image_bytes, extention) -> str:
    mime_type = f"image/{extention}"
    base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
    return f"data:{mime_type};base64,{base64_encoded}"


def format_rows_with_index(rows: list[dict]) -> str:
    """Format rows with explicit numbering for clarity."""
    numbered_rows = []
    for i, row in enumerate(rows, 1):
        numbered_rows.append(f"Row {i}: {json.dumps(row, indent=2, ensure_ascii=False)}")
    return "\n".join(numbered_rows)




