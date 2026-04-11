from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolArg, tool
from pydantic import BaseModel, Field

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.service import config_node_constants
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_record
from app.utils.logger import create_logger

logger = create_logger(__name__)


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: list[str] = Field(
        ...,
        description=(
            "List of Record IDs to fetch. Each Record ID is shown in the 'Record ID :' line "
            "of the record's context metadata in the conversation. "
            "Use ONLY the exact Record IDs from the context — do NOT invent, guess, or reuse example IDs. "
            "Pass ALL record IDs in a single call."
        )
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


def _extract_text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = ""
        for item in content:
            if isinstance(item, str):
                text += item
            elif isinstance(item, dict) and item.get("type") == "text":
                text += item.get("text", "")
        return text
    return ""


def _extract_text_from_messages(messages: list) -> str:
    """Return concatenated text from HumanMessage, ToolMessage, and user-role dict messages."""
    all_text = ""
    for msg in messages:
        if isinstance(msg, (HumanMessage, ToolMessage)):
            content = msg.content
        elif isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
        else:
            continue
        text = _extract_text_from_message_content(content)
        all_text += f" {text}"
    return all_text

async def _fetch_multiple_records_impl(
    record_ids: list[str],
    virtual_record_id_to_result: dict[str, Any],
    org_id: str | None = None,
    graph_provider=None,
    messages: list | None = None,
) -> dict[str, Any]:
    """
    Fetch multiple complete records at once.

    Validates record_ids against the conversation messages (HumanMessage +
    ToolMessage) before fetching. Invalid IDs (not present in context) return
    a reflection message so the caller can ask the LLM to self-correct.

    For each valid record_id:
    1. Search existing map values by graphDb record ID
    2. Check if it's a virtual_record_id (map key)
    3. If not in map, try to fetch from blob_store via graph DB lookup

    Returns:
    {
      "ok": true,
      "records": [...],
      "not_available_ids": [...],
    }
    """
    if messages:
        all_text = _extract_text_from_messages(messages)
        invalid_ids = [rid for rid in record_ids if rid not in all_text]

        if invalid_ids:
            ids_str = ", ".join(f"'{rid}'" for rid in invalid_ids)
            return {
                "ok": False,
                "message": (
                    f"You called fetch_full_records with some invalid record ID(s): {ids_str}. "
                    "These IDs are not present in the context. "
                    "Please identify the correct Record IDs from "
                    "the context and call fetch_full_records again."
                ),
            }

    found_records = []
    not_available_ids = []

    # Get frontend_url from the first non-None record already in the map
    frontend_url = next(
        (r["frontend_url"] for r in virtual_record_id_to_result.values()
         if r is not None and r.get("frontend_url")),
        None,
    )

    for record_id in record_ids:
        virtual_record_id = None
        found_record = None

        for vrid, record in virtual_record_id_to_result.items():
            if record is not None and record.get("id") == record_id:
                virtual_record_id = vrid
                found_record = record
                break

        if found_record:
            found_record["virtual_record_id"] = virtual_record_id
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
                        if not vrid:
                            not_available_ids.append(record_id)
                            continue
                        blob_store = BlobStorage(
                            logger=logger,
                            config_service=graph_provider.config_service,
                            graph_provider=graph_provider,
                        )
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
                        virtual_to_record_map = {vrid: graphDb_record}
                        await get_record(
                            vrid,
                            virtual_record_id_to_result,
                            blob_store,
                            org_id,
                            virtual_to_record_map,
                            graph_provider,
                            frontend_url,
                        )
                        blob_record = virtual_record_id_to_result.get(vrid)
                        if blob_record:
                            blob_record["virtual_record_id"] = vrid
                            found_records.append(blob_record)
                            continue
            except Exception:
                pass

        not_available_ids.append(record_id)

    if not found_records:
        return {"ok": False, "error": "None of the requested records were found."}

    result: dict[str, Any] = {
        "ok": True,
        "records": found_records,
        "not_available_ids": not_available_ids,
    }

    return result

def create_fetch_full_record_tool() -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    """
    @tool("fetch_full_records")
    async def fetch_full_record_tool(
        record_ids: list[str],
        reason: str = "Fetching full record content for comprehensive answer",
        virtual_record_id_to_result: Annotated[dict[str, Any] | None, InjectedToolArg] = None,
        org_id: Annotated[str | None, InjectedToolArg] = None,
        graph_provider: Annotated[Any, InjectedToolArg] = None,
        messages: Annotated[list | None, InjectedToolArg] = None,
    ) -> dict[str, Any]:
        """Fetch the complete content of one or more records when the provided blocks are insufficient to answer the query. Pass ALL record IDs in a SINGLE call using the record_ids parameter.

        IMPORTANT: record_ids must be taken directly from the 'Record ID :' field shown in the context metadata for each record. Do NOT use invented IDs, example IDs that are not present in the current context.
        Args:
            record_ids: List of Record IDs to fetch — use the exact 'Record ID :' values from the context
            reason: Brief explanation of why the full records are needed

        Returns: Complete content of the records or {"ok": false, "message/error": "..."}.
        """
        try:
            result = await _fetch_multiple_records_impl(
                record_ids=record_ids,
                virtual_record_id_to_result=virtual_record_id_to_result or {},
                org_id=org_id,
                graph_provider=graph_provider,
                messages=messages,
            )
            return result
        except Exception as e:
            logger.exception("fetch_full_records failed")
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


