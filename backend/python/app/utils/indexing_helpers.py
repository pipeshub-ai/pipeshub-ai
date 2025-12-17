import base64
import json
from typing import Dict, List, Tuple, Union

from jinja2 import Template
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import TypeAdapter
from typing_extensions import TypedDict

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
from app.utils.streaming import _apply_structured_output, cleanup_content


class TableSummary(TypedDict):
    summary: str
    headers: list[str]

row_adapter = TypeAdapter(RowDescriptions)



table_summary_adapter = TypeAdapter(TableSummary)


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


async def _call_llm(llm, messages) -> Union[str, dict, list]:
    return await llm.ainvoke(messages)



async def get_table_summary_n_headers(config, table_markdown: str) -> TableSummary:
    """
    Use LLM to generate a concise summary, mirroring the approach in Excel's get_table_summary.
    """
    try:
        # Get LLM with structured output
        llm, _ = await get_llm(config)
        llm_with_structured_output = _apply_structured_output(llm, schema=TableSummary)

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

        # Call LLM with structured output
        response = await _call_llm(llm_with_structured_output, messages)
        parsed_response = None
        try:
            # Handle both structured and non-structured responses
            if isinstance(response, dict):
                # Structured output (dict)
                parsed_response = table_summary_adapter.validate_python(response)
            else:
                # Non-structured output (AIMessage)
                response = response.content
                response_text = cleanup_content(response)
                parsed_response = table_summary_adapter.validate_json(response_text)

        except Exception as parse_error:
            # Reflection: attempt to fix the validation issue by providing feedback to the LLM
            try:
                reflection_prompt = f"""
                The previous response failed validation with the following error:
                {str(parse_error)}

                Please correct your response to match the expected schema.
                Ensure all fields are properly formatted and all required fields are present.
                Respond only with valid JSON.
                """

                reflection_messages = [
                    HumanMessage(content=rendered_form),
                    AIMessage(content=json.dumps(response)),
                    HumanMessage(content=reflection_prompt),
                ]

                # Use structured output for reflection attempt
                reflection_response = await _call_llm(llm_with_structured_output, reflection_messages)

                if isinstance(reflection_response, dict):
                    parsed_response = table_summary_adapter.validate_python(reflection_response)
                else:
                    reflection_text = cleanup_content(reflection_response.content)
                    parsed_response = table_summary_adapter.validate_json(reflection_text)

            except Exception:
                raise ValueError(
                    f"Failed to parse LLM response and reflection attempt failed: {str(parse_error)}"
                )
        
        if parsed_response is not None:
            return parsed_response
        else:
            return {"summary": "", "headers": []}
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
            llm,_ = await get_llm(config)

            llm_with_structured_output = _apply_structured_output(llm, schema=RowDescriptions)
            response = await _call_llm(llm_with_structured_output, messages)
            parsed_response = None
            descriptions = [str(row) for row in rows_data]
            try:
                if isinstance(response, dict):
                    parsed_response = row_adapter.validate_python(response)
                else:
                    response = cleanup_content(response.content)
                    parsed_response = row_adapter.validate_json(response)

            except Exception as parse_error:
                # Reflection: attempt to fix the validation issue by providing feedback to the LLM
                try:
                    reflection_prompt = f"""
                    The previous response failed validation with the following error:
                    {str(parse_error)}

                    Please correct your response to match the expected schema.
                    Ensure all fields are properly formatted and all required fields are present.
                    Respond only with valid JSON."""

                    messages.append(AIMessage(content=json.dumps(response)))
                    messages.append(HumanMessage(content=reflection_prompt))

                    reflection_response = await _call_llm(llm_with_structured_output, messages)

                    if isinstance(reflection_response, dict):
                        parsed_response = row_adapter.validate_python(reflection_response)
                    else:
                        reflection_text = cleanup_content(reflection_response.content)
                        parsed_response = row_adapter.validate_json(reflection_text)

                except Exception:
                    pass

            if parsed_response is not None and parsed_response.get("descriptions"):
                descriptions = parsed_response.get("descriptions")

            return descriptions, table_rows
        except Exception:
            raise
    else:
        return [], []


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
        table_summary = response.get("summary", "")
        column_headers = response.get("headers", [])
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





