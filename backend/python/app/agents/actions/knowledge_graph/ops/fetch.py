"""Knowledge graph fetch_record operation.

Extracted from hooks/citations.py ``_FetchFullRecordTool`` so the execution
logic lives in one place and both the new ``knowledgegraph__fetch_record``
tool and the legacy dynamic tool name can call it.

The dynamic-grant gating mechanism in ``citations.py`` is preserved
unchanged; only the *name* of the registered tool changes.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_DEFAULT_FULL_RECORD_MAX_BLOCKS = 200

FETCH_RECORD_TOOL_NAME = "knowledgegraph__fetch_record"


def resolve_block_cap(model_name: str, requested_max: int | None) -> int:
    """Resolve the effective block cap for a fetch."""
    env_raw = os.getenv("PIPESHUB_FULL_RECORD_MAX_BLOCKS", "")
    try:
        env_cap = int(env_raw) if env_raw.strip() else _DEFAULT_FULL_RECORD_MAX_BLOCKS
        if env_cap <= 0:
            env_cap = _DEFAULT_FULL_RECORD_MAX_BLOCKS
    except ValueError:
        logger.warning(
            "Invalid PIPESHUB_FULL_RECORD_MAX_BLOCKS=%r, using %d",
            env_raw, _DEFAULT_FULL_RECORD_MAX_BLOCKS,
        )
        env_cap = _DEFAULT_FULL_RECORD_MAX_BLOCKS
    if requested_max is not None and requested_max > 0:
        return min(env_cap, requested_max)
    return env_cap


async def execute_fetch_record(
    *,
    context: "AgentContext",
    virtual_records: dict[str, Any],
    citation_ref_mapper: Any,
    record_ids: list[str] | str,
    reason: str = "Fetching full record content for comprehensive answer",
    start_block: int = 0,
    max_blocks: int | None = None,
) -> dict[str, Any]:
    """Fetch one or more records end-to-end.

    Returns a dict with keys:
    - ``success``: bool
    - ``text``: formatted text for the LLM (when success=True)
    - ``error``: error string (when success=False)
    - ``updated_ref_mapper``: updated CitationRefMapper (caller must stash)
    """
    from app.utils.chat_helpers import record_to_message_content
    from app.utils.fetch_full_record import create_fetch_full_record_tool

    if isinstance(record_ids, str):
        record_ids = [record_ids]

    # TEMPORARY token-savings experiment (opt-in, disabled by default — see
    # `ChatQuery.enableRecordIdShortening`): resolve any short "R<n>" labels
    # (assigned by whichever knowledge tool ran first — see
    # `RecordIdShortener` in `utils/chat_helpers.py`) back to full Record
    # IDs before matching against `virtual_records`. IDs the model got
    # elsewhere are never shortened and pass through unchanged. Created
    # here (not just read) so a fetch that happens to be the first
    # knowledge call this request still shortens the ids it prints below.
    # `None` when the flag is off — record_ids pass through untouched.
    from app.utils.chat_helpers import get_record_id_shortener_if_enabled
    record_id_shortener = get_record_id_shortener_if_enabled(context.tool_state)
    if record_id_shortener is not None:
        record_ids = [record_id_shortener.resolve(rid) for rid in record_ids]

    block_cap = resolve_block_cap(context.model_name, max_blocks)

    structured_tool = create_fetch_full_record_tool(
        virtual_records,
        org_id=context.org_id,
        graph_provider=context.graph_provider,
        user_id=context.user_id,
    )
    try:
        result = await structured_tool.coroutine(record_ids=record_ids, reason=reason)
    except Exception as exc:
        return {"success": False, "error": str(exc), "updated_ref_mapper": citation_ref_mapper}

    if isinstance(result, dict) and result.get("ok") and result.get("records"):
        parts: list[str] = []
        ref_mapper = citation_ref_mapper
        for record in result["records"]:
            content_list, ref_mapper = record_to_message_content(
                record,
                ref_mapper=ref_mapper,
                start_block=start_block,
                max_blocks=block_cap,
            )
            parts.append("".join(
                item["text"] for item in content_list if item.get("type") == "text"
            ))

        text = "\n".join(parts)
        # TEMPORARY token-savings experiment: shorten every "Record ID:"
        # this fetch prints back down to the same "R<n>" label the model
        # already saw — see `RecordIdShortener`. No-op (full ids as-is)
        # when the flag is off.
        if record_id_shortener is not None:
            text = record_id_shortener.shorten_record_ids_in_text(text)
        text += (
            "\n\nCite facts from the above using each block's `[refN]` id "
            "as a markdown link, e.g. [source](ref2). Do NOT use external URLs as citations."
        )
        not_available = result.get("not_available_ids", [])
        if not_available:
            if record_id_shortener is not None:
                not_available = [
                    record_id_shortener.get_or_create_short_id(rid) for rid in not_available
                ]
            ids_str = ", ".join(f"'{rid}'" for rid in not_available)
            text += f"\n\nNote: The following record(s) are not available: {ids_str}"

        for record in result["records"]:
            rid = record.get("id")
            if rid:
                context.full_records_fetched.add(rid)
                context.tool_state.setdefault("full_records_fetched", set()).add(rid)

        return {"success": True, "text": text, "updated_ref_mapper": ref_mapper}

    from app.agents.agent_loop.tool_adapter import _to_tool_output
    out = _to_tool_output(result)
    return {
        "success": out.success,
        "text": out.data if out.success else None,
        "error": out.error if not out.success else None,
        "updated_ref_mapper": citation_ref_mapper,
    }
