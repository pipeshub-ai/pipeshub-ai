from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.modules.transformers.blob_storage import BlobStorage
from app.utils.logger import create_logger

# Create a logger for this module
logger = create_logger("fetch_full_record")


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


async def _try_blobstore_fetch(blob_store: BlobStorage, org_id: str, record_id: str) -> Optional[Dict[str, Any]]:
    """
    Try common BlobStorage paths. We don't know the exact method names in your code,
    so attempt a few sensible options and return the first successful payload.
    """
    fetch_start_time = time.time()
    try:
        rec = await blob_store.get_record_from_storage(org_id=org_id, virtual_record_id=record_id)
        if rec:
            fetch_duration_ms = (time.time() - fetch_start_time) * 1000
            logger.info("⏱️ Tool fetch completed in %.0fms for virtual_record_id: %s", fetch_duration_ms, record_id)
            return rec
    except Exception:
        pass

async def _fetch_full_record_using_vrid(vrid: str, blob_store: BlobStorage,org_id: str) -> Dict[str, Any]:
    """
    Fetch complete record using virtual record id.
    """
    record = await _try_blobstore_fetch(blob_store, org_id, vrid)
    if record:
        return {"ok": True, "record": record}
    else:
        return {"ok": False, "error": f"Record with vrid '{vrid}' not found in blob store."}

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
    fetch_start_time = time.time()
    records = list(virtual_record_id_to_result.values())

    found_records = []
    not_found_ids = []

    for record_id in record_ids:
        record = next((record for record in records if record is not None and record.get("id") == record_id), None)
        if record:
            found_records.append(record)
        else:
            not_found_ids.append(record_id)

    fetch_duration_ms = (time.time() - fetch_start_time) * 1000
    
    if found_records:
        avg_duration_ms = fetch_duration_ms / len(found_records) if len(found_records) > 0 else 0
        logger.info("⏱️ Tool batch fetch completed: %d records in %.0fms (avg: %.0fms/record)", len(found_records), fetch_duration_ms, avg_duration_ms)
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
def create_fetch_full_record_tool(virtual_record_id_to_result: Dict[str, Any]) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_ids: List[str], reason: str = "Fetching full record content for comprehensive answer") -> Dict[str, Any]:
        """
        Retrieve the complete content of multiple records (all blocks/groups) for better answering.
        Pass all record IDs at once instead of making multiple separate calls.

        Args:
            record_ids: List of virtual record IDs to fetch (e.g., ["80b50ab4-b775-46bf-b061-f0241c0dfa19"])
            reason: Clear explanation of why the full records are needed

        Returns:
        {"ok": true, "records": [...], "record_count": N, "not_found": [...]}
        or {"ok": false, "error": "..."}.
        """
        try:
            result = await _fetch_multiple_records_impl(record_ids, virtual_record_id_to_result)
            return result
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


def create_record_for_fetch_block_group(record: Dict[str, Any],block_group: Dict[str, Any],blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    block_container = {
        "blocks": blocks,
        "block_groups": [block_group]
    }
    record["block_containers"] = block_container
    return record
