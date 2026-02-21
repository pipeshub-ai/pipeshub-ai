"""
Agent-specific fetch-full-record tool.

This is a dedicated tool for the QnA agent that mirrors exactly how the chatbot
handles full-record retrieval.  The chatbot flow is:

  1. LLM calls fetch_full_record with R-labels ("R1", "R2") or UUIDs.
  2. stream_llm_response_with_tools → execute_tool_calls runs the tool.
  3. The tool returns {"ok": True, "records": [raw_record_dict, ...]}.
  4. execute_tool_calls calls record_to_message_content(record, final_results)
     on each raw record — producing formatted R-block text.
  5. Formatted text is placed into a ToolMessage for the LLM.

This agent tool follows the same contract so execute_tool_calls formats results
identically to the chatbot, giving the LLM properly R-labelled block content.

Key differences from the raw fetch_full_record tool (fetch_full_record.py):
  - This file is agent-only — it never touches chatbot code.
  - It accepts an explicit final_results list for consistent record numbering.
  - R-label resolution uses the same logic as the chatbot mapping.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ── Shared regex for R-label detection ─────────────────────────────────────
_R_LABEL_RE = re.compile(r"^R(\d+)(?:-\d+)?$", re.IGNORECASE)


class AgentFetchFullRecordArgs(BaseModel):
    """Arguments for the agent fetch_full_record tool."""

    record_ids: List[str] = Field(
        ...,
        description=(
            "List of record references to fetch. Accepted formats:\n"
            "  - R-labels like [\"R1\", \"R2\"] (preferred — use the record label "
            "shown in the context)\n"
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
    record_ids: List[str],
    virtual_record_id_to_result: Dict[str, Any],
    label_to_virtual_record_id: Optional[Dict[str, str]],
) -> tuple[list, list]:
    """
    Resolve a list of record references (R-labels or UUIDs) to raw record dicts.

    IMPORTANT: Each returned record dict has ``virtual_record_id`` injected so
    that ``record_to_message_content()`` in streaming.py can determine the
    correct R-number for the record (it looks up
    ``record.get("virtual_record_id")`` against the ordered final_results list).
    Without this field the function always falls back to record_number=1,
    causing every fetched record to be labelled R1 regardless of which
    document was actually fetched.

    Returns:
        (found_records, not_found_ids)
    """
    found_records: list = []
    not_found_ids: list = []
    seen_vids: set = set()  # guard against duplicate records

    for record_id in record_ids:
        resolved_vid: Optional[str] = None

        # Strategy 1 — R-label resolution ("R1", "R2", "R1-4" → base "R1")
        match = _R_LABEL_RE.match(record_id.strip())
        if match:
            base_label = f"R{match.group(1)}"
            if label_to_virtual_record_id and base_label in label_to_virtual_record_id:
                resolved_vid = label_to_virtual_record_id[base_label]

        # Strategy 2 — Direct virtual_record_id (UUID) lookup
        if not resolved_vid and record_id in virtual_record_id_to_result:
            resolved_vid = record_id

        # Strategy 3 — Legacy: match by record["id"] (ArangoDB _key)
        if not resolved_vid:
            for vid, rec in virtual_record_id_to_result.items():
                if rec is not None and rec.get("id") == record_id:
                    resolved_vid = vid
                    break

        if resolved_vid:
            if resolved_vid in seen_vids:
                # Already included this record — skip duplicates
                continue
            record = virtual_record_id_to_result.get(resolved_vid)
            if record is not None:
                # Inject virtual_record_id so record_to_message_content() can
                # look up the correct record number from final_results.
                # We make a shallow copy to avoid mutating the shared dict.
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
    virtual_record_id_to_result: Dict[str, Any],
    label_to_virtual_record_id: Optional[Dict[str, str]] = None,
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
            Optional mapping {"R1": "<uuid>", "R2": "<uuid>", …} built by
            build_record_label_mapping() in response_prompt.py.  When supplied
            the LLM can pass R-labels and they are resolved to UUIDs.
    """

    @tool("fetch_full_record", args_schema=AgentFetchFullRecordArgs)
    async def agent_fetch_full_record(
        record_ids: List[str],
        reason: str = "Fetching full record content for a comprehensive answer",
    ) -> Dict[str, Any]:
        """
        Retrieve the complete content of one or more records (all blocks/groups).

        Use this when the blocks shown in context are incomplete or you need
        more detail to answer accurately.  Pass ALL record IDs in ONE call.

        Args:
            record_ids: R-labels shown in context (e.g. ["R1", "R2"]) or
                        actual virtualRecordIds (UUIDs).
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

            result: Dict[str, Any] = {
                "ok": True,
                # stream_llm_response_with_tools → execute_tool_calls reads the
                # "records" key and passes each dict through
                # record_to_message_content(record, final_results) to produce
                # R-labelled block text — identical to the chatbot pipeline.
                "records": found_records,
                "record_count": len(found_records),
            }
            if not_found_ids:
                result["not_found"] = not_found_ids

            return result

        except Exception as exc:
            return {"ok": False, "error": f"Failed to fetch records: {exc}"}

    return agent_fetch_full_record
