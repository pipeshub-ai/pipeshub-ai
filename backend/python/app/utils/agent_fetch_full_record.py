"""
Agent-specific fetch-full-record tool.

This is a dedicated tool for the QnA agent that mirrors exactly how the chatbot
handles full-record retrieval.  The chatbot flow is:

  1. LLM calls fetch_full_record with record IDs (from Block Web URLs) or UUIDs.
  2. stream_llm_response_with_tools → execute_tool_calls runs the tool.
  3. The tool returns {"ok": True, "records": [raw_record_dict, ...]}.
  4. execute_tool_calls calls record_to_message_content(record, final_results)
     on each raw record — producing formatted block text with web URLs.
  5. Formatted text is placed into a ToolMessage for the LLM.

This agent tool follows the same contract so execute_tool_calls formats results
identically to the chatbot.

Key differences from the raw fetch_full_record tool (fetch_full_record.py):
  - This file is agent-only — it never touches chatbot code.
  - It accepts an explicit final_results list for consistent record numbering.
  - Supports record_id extraction from Block Web URLs, R-label fallback,
    and direct virtual_record_id (UUID) lookups.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

# ── Shared regex for R-label detection (legacy fallback) ───────────────────
_R_LABEL_RE = re.compile(r"^R(\d+)(?:-\d+)?$", re.IGNORECASE)
# Extract record ID from a Block Web URL like /record/{record_id}/preview#blockIndex=N
_RECORD_ID_FROM_URL_RE = re.compile(r"/record/([^/]+)/preview")


class AgentFetchFullRecordArgs(BaseModel):
    """Arguments for the agent fetch_full_record tool."""

    record_ids: list[str] = Field(
        ...,
        description=(
            "List of record references to fetch. Accepted formats:\n"
            "  - Record IDs extracted from Block Web URLs (the part between "
            "/record/ and /preview in the URL)\n"
            "  - Full Block Web URLs (the record ID will be extracted automatically)\n"
            "  - Actual virtualRecordIds (UUIDs) if you have them\n"
            "Pass ALL record IDs you need in a SINGLE call."
        ),
    )
    reason: str = Field(
        default="Fetching full record content for a comprehensive answer",
        description="Explain WHY the full records are needed (what gap exists in "
        "the provided blocks).",
    )


def _resolve_record_ids(
    record_ids: list[str],
    virtual_record_id_to_result: dict[str, Any],
    label_to_virtual_record_id: Optional[dict[str, str]],
) -> tuple[list, list]:
    """
    Resolve a list of record references to raw record dicts.

    Accepts record IDs from Block Web URLs, full web URLs, virtual_record_ids
    (UUIDs), record["id"] (ArangoDB _key), and legacy R-labels as fallback.

    IMPORTANT: Each returned record dict has ``virtual_record_id`` injected so
    that ``record_to_message_content()`` in streaming.py can assign the
    correct block web URLs for the record.

    Returns:
        (found_records, not_found_ids)
    """
    found_records: list = []
    not_found_ids: list = []
    seen_vids: set = set()

    for record_id in record_ids:
        resolved_vid: Optional[str] = None
        effective_id = record_id.strip()

        # Strategy 1 — Extract record ID from a Block Web URL
        url_match = _RECORD_ID_FROM_URL_RE.search(effective_id)
        if url_match:
            extracted_id = url_match.group(1)
            for vid, rec in virtual_record_id_to_result.items():
                if rec is not None and rec.get("id") == extracted_id:
                    resolved_vid = vid
                    break

        # Strategy 2 — Direct virtual_record_id (UUID) lookup
        if not resolved_vid and effective_id in virtual_record_id_to_result:
            resolved_vid = effective_id

        # Strategy 3 — Match by record["id"] (ArangoDB _key)
        if not resolved_vid:
            for vid, rec in virtual_record_id_to_result.items():
                if rec is not None and rec.get("id") == effective_id:
                    resolved_vid = vid
                    break

        # Strategy 4 — Legacy R-label fallback ("R1", "R2", "R1-4" → base "R1")
        if not resolved_vid:
            r_match = _R_LABEL_RE.match(effective_id)
            if r_match:
                base_label = f"R{r_match.group(1)}"
                if label_to_virtual_record_id and base_label in label_to_virtual_record_id:
                    resolved_vid = label_to_virtual_record_id[base_label]

        if resolved_vid:
            if resolved_vid in seen_vids:
                continue
            record = virtual_record_id_to_result.get(resolved_vid)
            if record is not None:
                record_with_vid = dict(record)
                record_with_vid["virtual_record_id"] = resolved_vid
                found_records.append(record_with_vid)
                seen_vids.add(resolved_vid)
            else:
                not_found_ids.append(record_id)
        else:
            not_found_ids.append(record_id)

    return found_records, not_found_ids


def create_agent_fetch_full_record_tool(
    virtual_record_id_to_result: dict[str, Any],
    label_to_virtual_record_id: Optional[dict[str, str]] = None,
) -> Callable:
    """
    Factory that creates the agent-specific fetch_full_record tool.

    The returned tool is named "fetch_full_record" (same name as the chatbot
    tool) so the LLM's existing prompting works unchanged.

    Args:
        virtual_record_id_to_result:
            Mapping  virtual_record_id → full record dict.  Populated by
            get_flattened_results() in the retrieval action — records MUST
            have been fetched with virtual_to_record_map + graph_provider so
            that context_metadata is present (same requirement as chatbot).
        label_to_virtual_record_id:
            Optional mapping {"R1": "<uuid>", …} for legacy R-label fallback.
    """

    @tool("fetch_full_record", args_schema=AgentFetchFullRecordArgs)
    async def agent_fetch_full_record(
        record_ids: list[str],
        reason: str = "Fetching full record content for a comprehensive answer",
    ) -> dict[str, Any]:
        """
        Retrieve the complete content of one or more records (all blocks/groups).

        Use this when the blocks shown in context are incomplete or you need
        more detail to answer accurately.  Pass ALL record IDs in ONE call.

        Args:
            record_ids: Record IDs from Block Web URLs, full Block Web URLs,
                        or actual virtualRecordIds (UUIDs).
            reason:     Why the full records are needed.

        Returns:
            {"ok": true, "records": [...], "record_count": N}
            or {"ok": false, "error": "..."}
        """
        try:
            found_records, not_found_ids = _resolve_record_ids(
                record_ids,
                virtual_record_id_to_result,
                label_to_virtual_record_id,
            )

            if not found_records:
                return {
                    "ok": False,
                    "error": (
                        f"None of the requested records were found: "
                        f"{', '.join(record_ids)}"
                    ),
                }

            result: dict[str, Any] = {
                "ok": True,
                # stream_llm_response_with_tools → execute_tool_calls reads the
                # "records" key and passes each dict through
                # record_to_message_content(record, final_results) to produce
                # block text with web URLs — identical to the chatbot pipeline.
                "records": found_records,
                "record_count": len(found_records),
            }
            if not_found_ids:
                result["not_found"] = not_found_ids

            return result

        except Exception as exc:
            return {"ok": False, "error": f"Failed to fetch records: {exc}"}

    return agent_fetch_full_record
