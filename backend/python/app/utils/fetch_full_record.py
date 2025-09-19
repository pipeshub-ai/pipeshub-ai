from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.transformers.blob_storage import BlobStorage


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching a full record.
    """
    record_id: str = Field(
        ...,
        description="ID (or virtualRecordId) of the record to fetch. Prefer the ID found in chunk metadata."
    )
    reason: str = Field(
        ...,
        description="Why the full record is needed (explain the gap in the provided blocks)."
    )


async def _try_blobstore_fetch(blob_store: BlobStorage, org_id: str, record_id: str) -> Optional[Dict[str, Any]]:
    """
    Try common BlobStorage paths. We don't know the exact method names in your code,
    so attempt a few sensible options and return the first successful payload.
    """
    try:
        rec = await blob_store.get_record_from_storage(org_id=org_id, virtual_record_id=record_id)
        if rec:
            return rec
    except Exception:
        pass


async def _fetch_full_record_impl(
    record_id: str,
    blob_store: BlobStorage,
    arango_service: ArangoService,
    org_id: str,
) -> Dict[str, Any]:
    """
    Fetch complete record in the structure your prompt expects:
    {
      "record_id": ...,
      "record_name": ...,
      "semantic_metadata": {...},
      "block_containers": {"blocks": [...], "block_groups": [...]},
      ...
    }
    """
    print(f"record_id: {record_id}")
    print(f"arango_service: {arango_service}")
    record = await arango_service.get_record_by_id(record_id)
    print(f"record: {record}")
    # 1) Try blob store (fast path in your pipeline)
    record = await _try_blobstore_fetch(blob_store, org_id, record.virtual_record_id)
    if record:
        return {"ok": True, "record": record}

    # Nothing found
    return {"ok": False, "error": f"Record '{record_id}' not found via blob store or arango."}


# Option 1: Create the tool without the decorator and handle runtime kwargs manually
def create_fetch_full_record_tool(blob_store: BlobStorage, arango_service: ArangoService, org_id: str) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_id: str, reason: str) -> str:
        """
        Retrieve the complete content of a record (all blocks/groups) for better answering.
        Returns a JSON string: {"ok": true, "record": {...}} or {"ok": false, "error": "..."}.
        """

        result = await _fetch_full_record_impl(record_id, blob_store, arango_service, org_id)
        return json.dumps(result)

    return fetch_full_record_tool
