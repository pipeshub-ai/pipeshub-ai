from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: list[str] = Field(
        ...,
        description="List of recordIds of the records to fetch. Pass ALL record IDs in a single call — do NOT make multiple separate calls."
    )
    reason: str = Field(
        default="Fetching full record content for comprehensive answer",
        description="Brief explanation of why the full records are needed (e.g., 'query asks for complete details')."
    )

class FetchBlockGroupArgs(BaseModel):
    """
    Required tool args for fetching a block group.
    """
    block_group_number: str = Field(
        ...,
        description="Number of the block group to fetch."
    )
    reason: str = Field(
        default="Fetching block group for additional context",
        description="Why the block group is needed (explain the gap in the provided blocks)."
    )

async def _fetch_multiple_records_impl(
    record_ids: list[str],
    virtual_record_id_to_result: dict[str, Any]
) -> dict[str, Any]:
    """
    Fetch multiple complete records at once.
    Returns:
    {
      "ok": true,
      "records": [...],
      "not_found": [...]  # IDs that weren't found
    }
    """
    records = list(virtual_record_id_to_result.values())

    found_records = []
    not_found_ids = []

    for record_id in record_ids:
        record = next((record for record in records if record is not None and record.get("id") == record_id), None)
        if record:
            found_records.append(record)
        else:
            not_found_ids.append(record_id)

    if found_records:
        result = {
            "ok": True,
            "records": found_records,
            "record_count": len(found_records)
        }
        if not_found_ids:
            result["not_found"] = not_found_ids
        return result

    # Nothing found
    return {"ok": False, "error": f"None of the requested records were found: {', '.join(record_ids)}"}


# Option 1: Create the tool without the decorator and handle runtime kwargs manually
def create_fetch_full_record_tool(virtual_record_id_to_result: dict[str, Any]) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_ids: list[str], reason: str = "Fetching full record content for comprehensive answer") -> dict[str, Any]:
        """Fetch the complete content of one or more records when the provided blocks are insufficient to answer the query. Pass ALL record IDs in a SINGLE call using the record_ids parameter.

        Args:
            record_ids: List of recordIds to fetch (e.g., ["ba4cf9ed-d254-4989-a817-adf0475250cd"])
            reason: Brief explanation of why the full records are needed

        Returns: Complete content of the records or {"ok": false, "error": "..."}.
        """
        try:
            return await _fetch_multiple_records_impl(record_ids, virtual_record_id_to_result)
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


def create_record_for_fetch_block_group(record: dict[str, Any],block_group: dict[str, Any],blocks: list[dict[str, Any]]) -> dict[str, Any]:
    block_container = {
        "blocks": blocks,
        "block_groups": [block_group]
    }
    record["block_containers"] = block_container
    return record
