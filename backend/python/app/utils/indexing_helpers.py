import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Template
from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    Point,
    TableMetadata,
)
from app.modules.parsers.excel.prompt_template import RowDescriptions, row_text_prompt
from app.utils.llm import get_llm
from app.utils.streaming import invoke_with_structured_output_and_reflection


class TableSummary(BaseModel):
    summary: str = Field(description="Summary of the table")
    headers: list[str] = Field(description="Column headers of the table")


table_summary_prompt_template = """
# Task:
Provide a clear summary of this table's purpose and content. Also provide the column headers of the table.

# Table Markdown:
{{table_markdown}}

# Output Format:
You must return a single valid JSON object with the following structure:
{
    "summary": "Summary of the table",
    "headers": ["Column headers of the table. If no headers are found, return an empty array."]
}

Return the JSON object only, no additional text or explanation.
"""


async def get_table_summary_n_headers(config, table_markdown: str) -> Optional[TableSummary]:
    """
    Use LLM to generate a concise summary, mirroring the approach in Excel's get_table_summary.

    Returns:
        TableSummary Pydantic model with summary and headers, or None if parsing fails.
    """
    try:
        # Get LLM
        llm, _ = await get_llm(config)

        # Prepare prompt
        template = Template(table_summary_prompt_template)
        rendered_form = template.render(table_markdown=table_markdown)
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
            # Prepare rows data
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
                table_summary=table_summary, rows_data=json.dumps(rows_data, indent=2)
            )
            llm, _ = await get_llm(config)

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
        value_str = str(value) if value is not None else "None"
        parts.append(f"{key}: {value_str}")
    return ", ".join(parts)


def _normalize_bbox(
    bbox: Tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> List[Dict[str, float]]:
    """Normalize bounding box coordinates to 0-1 range"""
    x0, y0, x1, y1 = bbox
    return [
        {"x": x0 / page_width, "y": y0 / page_height},
        {"x": x1 / page_width, "y": y0 / page_height},
        {"x": x1 / page_width, "y": y1 / page_height},
        {"x": x0 / page_width, "y": y1 / page_height},
    ]


def image_bytes_to_base64(image_bytes, extention) -> str:
    mime_type = f"image/{extention}"
    base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
    return f"data:{mime_type};base64,{base64_encoded}"

async def process_table_pymupdf(
    page,
    result: dict,
    config: ConfigurationService,
    page_number: int,
) -> Tuple[List[str], List[List[dict]]]:
    """Process table data with normalized coordinates"""
    page_width = page.rect.width
    page_height = page.rect.height
    table_finder = page.find_tables()
    tables = table_finder.tables
    for table in tables:
        table_markdown = table.to_markdown()
        response = await get_table_summary_n_headers(config, table_markdown)
        table_summary = response.summary if response else ""
        column_headers = response.headers if response else []
        table_data = table.extract()
        table_rows_text,table_rows = await get_rows_text(config, {"grid": table_data}, table_summary, column_headers)
        bbox = _normalize_bbox(table.bbox, page_width, page_height)
        bbox = [Point(x=p["x"], y=p["y"]) for p in bbox]
        block_group = BlockGroup(
            index=len(result["tables"]),
            type=GroupType.TABLE,
            description=None,
            table_metadata=TableMetadata(
                num_of_rows=len(table_data),
                num_of_cols=table.col_count if table.col_count else None,
            ),
            data={
                "table_summary": table_summary,
                "column_headers": column_headers,
                "table_markdown": table_markdown,
            },
            format=DataFormat.JSON,
            citation_metadata=CitationMetadata(
                page_number=page_number,
                bounding_boxes=bbox,
            ),
        )
        for i,row in enumerate(table_rows):
            block = Block(
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                comments=[],
                parent_index=block_group.index,
                data={
                    "row_natural_language_text": table_rows_text[i] if i<len(table_rows_text) else "",
                    "row_number": i+1,
                    "row":json.dumps(row)
                },
                citation_metadata=block_group.citation_metadata
            )
            # _enrich_metadata(block, row, doc_dict)
            result["blocks"].append(block)


        result["tables"].append(block_group)


def format_rows_with_index(rows: list[dict]) -> str:
        """Format rows with explicit numbering for clarity."""
        numbered_rows = []
        for i, row in enumerate(rows, 1):
            numbered_rows.append(f"Row {i}: {json.dumps(row, indent=2)}")
        return "\n".join(numbered_rows)





