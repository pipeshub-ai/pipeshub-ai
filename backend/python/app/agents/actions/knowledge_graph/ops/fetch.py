"""Knowledge graph fetch_record operation.

The execution body for ``knowledgegraph__fetch_record``. The tool itself is
``_FetchFullRecordTool`` in ``hooks/citations.py``, which owns registration,
the dynamic grant, and where the returned ref mapper gets stashed; this module
owns what a fetch actually does. Keeping the two apart is what lets the tool be
built fresh per call from live ``tool_state`` without duplicating the body.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import ToolOutput
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_DEFAULT_FULL_RECORD_MAX_BLOCKS = 200

FETCH_RECORD_TOOL_NAME = "knowledgegraph__fetch_record"

DEFAULT_FETCH_REASON = "Fetching full record content for comprehensive answer"


def resolve_block_cap(model_name: str, requested_max: int | None) -> int:
    """Resolve the effective block cap for a fetch.

    Deliberately not widened from the model's context window: that reports 128k
    for unknown/local models, far too optimistic for a small LLM.
    """
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
    reason: str = DEFAULT_FETCH_REASON,
    start_block: int = 0,
    max_blocks: int | None = None,
) -> tuple["ToolOutput", Any]:
    """Fetch one or more records end-to-end.

    Returns ``(output, ref_mapper)``. The mapper comes back rather than being
    stashed here, and is the SAME object passed in unless a record was actually
    rendered -- that identity is how the caller tells an update from a no-op.
    """
    from app.agent_loop_lib.tools.base import ToolOutput
    from app.agents.agent_loop.tool_adapter import _to_tool_output
    from app.utils.chat_helpers import ImageBudget, image_dict_to_part, record_to_message_content
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

    # Intra-call only. Do NOT extend this to skip records fetched by an earlier
    # call: `shape_tool_result_clearing` drops stale fetch results and tells the
    # model to re-call with the same arguments, which such a guard would refuse.
    # After resolve so a short label and its full id collapse to one entry.
    record_ids = list(dict.fromkeys(record_ids))
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
        return ToolOutput(success=False, error=str(exc)), citation_ref_mapper

    if isinstance(result, dict) and result.get("ok") and result.get("records"):
        parts: list[str] = []
        ref_mapper = citation_ref_mapper
        # Without `is_multimodal_llm` a record that IS an image (an uploaded
        # PNG whose only block is an IMAGE) renders to an empty string.
        # `collected_images` keeps the images out of the text join so they
        # can ride the multipart return below instead of being dropped.
        image_budget: ImageBudget = context.tool_state.setdefault("image_budget", ImageBudget())
        collected_images: list[dict[str, Any]] = []
        for record in result["records"]:
            content_list, ref_mapper = record_to_message_content(
                record,
                ref_mapper=ref_mapper,
                start_block=start_block,
                max_blocks=block_cap,
                is_multimodal_llm=context.is_multimodal_llm,
                collected_images=collected_images,
                image_budget=image_budget,
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
                # shorten_if_known: do not mint labels for ids the model never saw.
                not_available = [
                    record_id_shortener.shorten_if_known(rid) for rid in not_available
                ]
            ids_str = ", ".join(f"'{rid}'" for rid in not_available)
            text += f"\n\nNote: The following record(s) are not available: {ids_str}"

        for record in result["records"]:
            rid = record.get("id")
            if rid:
                context.full_records_fetched.add(rid)
                context.tool_state.setdefault("full_records_fetched", set()).add(rid)

        if collected_images and context.is_multimodal_llm:
            # Mirrors `retrieval.py`'s matching branch: multipart `data`
            # flows through `ToolOutput` -> `ToolResult.content` ->
            # `ToolMessage.content` unchanged. Only stash a fallback copy
            # when the transport needs one — models with native multipart
            # tool-result support already got the images above, and
            # `shape_retrieved_image_injection` (the sole consumer of the
            # stash) is never registered for them.
            from app.agent_loop_lib.core.messages import TextPart

            if not context.tool_state.get("supports_multipart_tool_result", True):
                context.tool_state.setdefault("pending_tool_images", []).extend(collected_images)
            image_parts = [
                part for img in collected_images
                if (part := image_dict_to_part(img)) is not None
            ]
            if image_parts:
                return ToolOutput(success=True, data=[TextPart(text=text), *image_parts]), ref_mapper

        return ToolOutput(success=True, data=text), ref_mapper

    return _to_tool_output(result), citation_ref_mapper
