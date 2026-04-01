from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.service import config_node_constants
from app.modules.transformers.blob_storage import BlobStorage
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.utils.logger import create_logger

logger = create_logger(__name__)

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
    virtual_record_id_to_result: dict[str, Any],
    org_id: str | None = None,
    graph_provider=None,
) -> dict[str, Any]:
    """
    Fetch multiple complete records at once.

    For each record_id:
    1. Search existing map values by graphDb record ID
    2. Check if it's a virtual_record_id (map key)
    3. If not in map and valid UUID, try to fetch from blob_store as virtual_record_id

    Returns:
    {
      "ok": true,
      "records": [...],
      "record_count": N,
      "not_available": {"id": "This record is not available"},   # fetched or map-keyed but missing
      "invalid_record_ids": {"id": "Invalid record ID"}           # malformed / non-UUID IDs
    }
    """
    found_records = []
    not_available_ids = []

    # Get frontend_url from the first non-None record already in the map
    frontend_url = next(
        (r["frontend_url"] for r in virtual_record_id_to_result.values()
         if r is not None and r.get("frontend_url")),
        None,
    )

    for record_id in record_ids:
        found_record = next(
            (r for r in virtual_record_id_to_result.values()
             if r is not None and r.get("id") == record_id),
            None,
        )
        if found_record:
            found_records.append(found_record)
            continue

        if org_id and graph_provider:
            try:
                graphDb_record = await graph_provider.get_document(
                                document_key=record_id,
                                collection=CollectionNames.RECORDS.value
                            )

                if graphDb_record:
                    indexing_status = graphDb_record.get("indexingStatus")
                    if indexing_status == ProgressStatus.COMPLETED.value:
                        vrid = graphDb_record.get("virtualRecordId")
                        blob_store = BlobStorage(logger=logger, config_service=graph_provider.config_service, graph_provider=graph_provider)
                        blob_record = await blob_store.get_record_from_storage(virtual_record_id=vrid, org_id=org_id)
                        if blob_record:
                            frontend_url = None
                            try:
                                endpoints_config = await blob_store.config_service.get_config(
                                    config_node_constants.ENDPOINTS.value,
                                    default={}
                                )
                                if isinstance(endpoints_config, dict):
                                    frontend_url = endpoints_config.get("frontend", {}).get("publicEndpoint")
                            except Exception:
                                pass
                            blob_record["frontend_url"] = frontend_url or ""
                            found_records.append(blob_record)
                            continue
            except Exception:
                pass

        not_available_ids.append(record_id)

    result: dict[str, Any] = {}
    result["ok"] = False

    if found_records:
        result["ok"] = True
        result["records"] = found_records
    else:
        return {"ok": False, "error": f"None of the requested records were found."}


    result["not_available_ids"] = not_available_ids

    return result


def create_fetch_full_record_tool(
    virtual_record_id_to_result: dict[str, Any],
    org_id: str | None = None,
    graph_provider=None,
) -> Callable:
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
            return await _fetch_multiple_records_impl(
                record_ids,
                virtual_record_id_to_result,
                org_id=org_id,
                graph_provider=graph_provider,
            )
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
