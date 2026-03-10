from __future__ import annotations

from typing import Any, Callable, Dict, List

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: List[str] = Field(
        ...,
        description="List of IDs (or virtualRecordIds) of the records to fetch. Pass all record IDs that need to be fetched in a single call. Prefer the IDs found in chunk metadata."
    )
    reason: str = Field(
        default="Fetching full record content for comprehensive answer",
        description="Why the full records are needed (explain the gap in the provided blocks)."
    )

async def _fetch_multiple_records_impl(
    record_ids: List[str],
    virtual_record_id_to_result: Dict[str, Any]
) -> Dict[str, Any]:
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
            "result_type": "records",
            "records": found_records,
            "record_count": len(found_records)
        }
        if not_found_ids:
            result["not_found"] = not_found_ids
        return result

    # Nothing found
    return {"ok": False, "error": f"None of the requested records were found: {', '.join(record_ids)}"}


# Option 1: Create the tool without the decorator and handle runtime kwargs manually
def create_fetch_full_record_tool(virtual_record_id_to_result: Dict[str, Any]) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_ids: List[str], reason: str = "Fetching full record content for comprehensive answer") -> Dict[str, Any]:
        """You have access to this tool called "fetch_full_record" that retrieves the complete content of multiple records.

        **When to use:**
        - Provided blocks contain partial information with gaps in understanding
        - Need more context from specific records for a complete answer
        - Blocks suggest important information exists but isn't fully captured
        - References to content that should be in records but isn't in provided blocks
        - Query asks for comprehensive details ("full details", "complete overview", "all information")
        - CRITICAL: If blocks seem incomplete or you're uncertain, USE THE TOOL rather than providing a partial answer

        **How to use:**
        - Call this tool with a LIST of RecordIds: ["80b50ab4-b775-46bf-b061-f0241c0dfa19", "90c60bc5-c886-57cg-c172-g1352d1egb2a"]
        - Provide a clear reason explaining why you need the full records
        - The tool returns the complete content of all requested records including all blocks
        - **CRITICAL: Pass ALL record IDs in a SINGLE call. Do NOT make multiple separate calls.**

        NOTE:
        - **Balanced Tool Usage**: The provided blocks are optimized semantic search results. Use them when adequate, but don't hesitate to fetch full records when they would materially improve answer quality.

        Tool Usage Strategy:
        - **Tool call format:** When using the tool, explain your reasoning clearly in the "reason" parameter
        - **Integration:** After receiving tool results, seamlessly integrate the information with existing blocks

        For any query that cannot be answered with current blocks, attempt to use this tool

        Args:
            record_ids: List of recordIds (e.g., ["b541abcc-0bc9-42aa-8fc7-22ecfa12ef11"])
            reason: Clear explanation of information gaps requiring full records

        Returns:
            List of blocks for the records or {"ok": false, "error": "..."}

        Example:
            fetch_full_record(
                record_ids=["80b50ab4-b775-46bf-b061-f0241c0dfa19","3c5357e0-1838-4b16-bbf4-9fb1f606bf4a"],
                reason="Blocks only show summary; need full implementation details to answer how the feature works"
            )"""
        try:
            result = await _fetch_multiple_records_impl(record_ids, virtual_record_id_to_result)
            return result
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool



