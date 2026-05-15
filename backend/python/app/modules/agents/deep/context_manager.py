"""
Context Manager - Conversation Compaction and Sub-Agent Context Building

Prevents context bloating by:
1. Summarizing old conversation history
2. Truncating large tool results
3. Building focused context for each sub-agent
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.modules.agents.deep.state import SubAgentTask

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RESULT_CHARS = 3000  # Max chars per tool result in compacted form
MAX_SUMMARY_WORDS = 200
MAX_RECENT_PAIRS = 5  # Keep last N conversation pairs verbatim
_USER_MSG_TRUNCATE = 200
_BOT_MSG_TRUNCATE = 300
_STR_VALUE_MAX_LEN = 200
_MIN_BUDGET_FOR_NESTED = 100
_LIST_PREVIEW_ITEMS = 3
_MAX_SUMMARIES_TEXT_LEN = 50000
TRUNCATION_MARKER = "\n... [truncated for brevity]"
MAX_CONVERSATION_PAIRS = 20  # Match react agent's MAX_CONVERSATION_HISTORY


def _image_attachment_count(conv: Dict[str, Any]) -> int:
    """Count image mime attachments on a conversation item (user_query)."""
    attachments = conv.get("attachments") or []
    return sum(
        1
        for att in attachments
        if isinstance(att, dict) and (att.get("mimeType") or "").lower().startswith("image/")
    )


def _pdf_attachment_count(conv: Dict[str, Any]) -> int:
    """Count PDF attachments on a conversation item (user_query)."""
    attachments = conv.get("attachments") or []
    return sum(
        1
        for att in attachments
        if isinstance(att, dict) and (att.get("mimeType") or "").lower() == "application/pdf"
    )


def _user_plain_summary_line(conv: Dict[str, Any], text_max: int) -> Optional[str]:
    """
    One text line digest for a user_query turn (for prompts that cannot carry images/PDFs).

    Returns None if the turn should be omitted (no text and no attachments).
    """
    content_raw = conv.get("content", "") or ""
    content = content_raw.strip()
    n_img = _image_attachment_count(conv)
    n_pdf = _pdf_attachment_count(conv)
    if not content and not n_img and not n_pdf:
        return None
    if not content_raw and (n_img or n_pdf):
        parts = []
        if n_img:
            parts.append(f"{n_img} image(s) attached")
        if n_pdf:
            parts.append(f"{n_pdf} PDF(s) attached")
        return f"User: [{', '.join(parts)}]"
    short = (
        content_raw[:text_max] + "..."
        if len(content_raw) > text_max
        else content_raw
    )
    attachment_parts = []
    if n_img:
        attachment_parts.append(f"{n_img} image(s)")
    if n_pdf:
        attachment_parts.append(f"{n_pdf} PDF(s)")
    suffix = f" [{', '.join(attachment_parts)} attached]" if attachment_parts else ""
    return f"User: {short}{suffix}"


def ensure_blob_store(state: Dict[str, Any], log: logging.Logger) -> Any:
    """Ensure ``state["blob_store"]`` is initialised, creating one if needed."""
    blob_store = state.get("blob_store")
    if blob_store is not None:
        return blob_store
    try:
        from app.modules.transformers.blob_storage import BlobStorage
        blob_store = BlobStorage(
            logger=log,
            config_service=state.get("config_service"),
            graph_provider=state.get("graph_provider"),
        )
        state["blob_store"] = blob_store
    except Exception:
        log.debug("Could not initialise BlobStorage for conversation history", exc_info=True)
    return blob_store


# ---------------------------------------------------------------------------
# Shared: Conversation History → LangChain Messages
# ---------------------------------------------------------------------------

async def build_conversation_messages(
    conversations: List[Dict[str, Any]],
    log: logging.Logger,
    max_pairs: int = MAX_CONVERSATION_PAIRS,
    include_reference_data: bool = False,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
    ref_mapper: Any = None,
    out_records: dict[str, dict] | None = None,
) -> list:
    """Convert flat conversation history to LangChain messages with sliding window.

    This is the single source of truth for converting ChatState's
    ``previous_conversations`` (flat format with ``role``/``content`` keys)
    into alternating HumanMessage/AIMessage suitable for LLM prompts.

    Mirrors the react agent's ``_build_conversation_messages`` in nodes.py:
    groups items into user+bot pairs, applies a sliding window, and
    optionally extracts reference data from the full history.

    When *is_multimodal_llm* is True, image attachments on previous user_query
    messages are fetched from blob storage and included as ``image_url`` content
    blocks alongside the text.

    PDF attachments on previous user_query messages are resolved from blob
    storage via ``record_to_message_content`` and appended to the same user
    message under an "Attached PDF documents:" label.

    Args:
        conversations: Flat list — each item has ``role`` ("user_query" or
            "bot_response") and ``content``.
        log: Logger instance.
        max_pairs: Maximum number of user+bot pairs to keep (default 20).
        include_reference_data: If True, append formatted reference data
            (IDs, keys, URLs) from the full history to the last AI message
            so the LLM can reuse them without re-fetching.
        is_multimodal_llm: Whether the LLM supports multimodal content.
        blob_store: BlobStorage instance for fetching image and PDF attachments.
        org_id: Organisation ID for blob storage lookups.
        ref_mapper: Shared CitationRefMapper so historical PDF citation IDs
            are consistent with those used for retrieval results.  Pass
            ``state["citation_ref_mapper"]``; a fresh one is created if not
            provided.
        out_records: If provided, historical PDF records fetched from blob
            storage are stored here keyed by virtualRecordId so callers can
            populate ``virtual_record_id_to_result`` for citation resolution.

    Returns:
        List of HumanMessage / AIMessage.
    """
    if not conversations:
        return []

    all_reference_data: List[Dict[str, Any]] = []

    if include_reference_data:
        for conv in conversations:
            if conv.get("role") == "bot_response":
                ref_data = conv.get("referenceData", [])
                if ref_data:
                    all_reference_data.extend(ref_data)

    # Group into user+bot pairs
    pairs: List[List[Dict[str, Any]]] = []
    current_pair: List[Dict[str, Any]] = []

    for conv in conversations:
        role = conv.get("role", "")
        if role == "user_query":
            if current_pair:
                pairs.append(current_pair)
            current_pair = [conv]
        elif role == "bot_response":
            if current_pair:
                current_pair.append(conv)
                pairs.append(current_pair)
                current_pair = []
            else:
                pairs.append([conv])

    if current_pair:
        pairs.append(current_pair)

    # Sliding window: keep last N pairs
    if len(pairs) > max_pairs:
        pairs = pairs[-max_pairs:]
        log.debug("Conversation sliding window: kept last %d pairs", max_pairs)

    # Convert to LangChain messages
    messages: list = []
    for pair in pairs:
        for conv in pair:
            role = conv.get("role", "")
            content = conv.get("content", "")
            if not content:
                continue
            if role == "user_query":
                attachments = conv.get("attachments") or []
                if is_multimodal_llm and attachments and blob_store and org_id:
                    from app.utils.chat_helpers import build_multimodal_user_content
                    content = await build_multimodal_user_content(
                        content, attachments, blob_store, org_id,
                    )
                # Add PDF attachments from conversation history
                pdf_attachments = [
                    att for att in attachments
                    if isinstance(att, dict)
                    and (att.get("mimeType") or "").lower() == "application/pdf"
                ]
                if pdf_attachments and blob_store and org_id:
                    from app.utils.chat_helpers import record_to_message_content, CitationRefMapper
                    if ref_mapper is None:
                        ref_mapper = CitationRefMapper()
                    pdf_blocks: List[Dict[str, Any]] = []
                    for att in pdf_attachments:
                        vrid = att.get("virtualRecordId") or ""
                        if not vrid:
                            continue
                        try:
                            record = await blob_store.get_record_from_storage(vrid, org_id)
                            if not record:
                                continue
                            if out_records is not None and vrid not in out_records:
                                out_records[vrid] = record
                            blocks, ref_mapper = record_to_message_content(record, ref_mapper=ref_mapper, is_multimodal_llm=is_multimodal_llm)
                            pdf_blocks.extend(blocks)
                        except Exception as exc:
                            log.warning("Failed to resolve historical PDF attachment vrid=%s: %s", vrid, exc)
                    if pdf_blocks:
                        parts: list = list(content) if isinstance(content, list) else (
                            [{"type": "text", "text": content}] if content else []
                        )
                        parts.append({"type": "text", "text": "Attached PDF documents:"})
                        parts.extend(pdf_blocks)
                        content = parts
                messages.append(HumanMessage(content=content))
            elif role == "bot_response":
                messages.append(AIMessage(content=content))

    # Append reference data so the LLM can reuse IDs/keys
    if all_reference_data:
        ref_text = _format_reference_data(all_reference_data)
        if ref_text and messages and isinstance(messages[-1], AIMessage):
            messages[-1].content = messages[-1].content + "\n\n" + ref_text
        elif ref_text:
            messages.append(AIMessage(content=ref_text))

    if messages:
        log.info(
            "Built %d conversation messages (%d pairs) for LLM context",
            len(messages), len(pairs),
        )

    return messages


def _format_reference_data(reference_data: List[Dict[str, Any]]) -> str:
    """Format reference data so the LLM can reuse IDs and keys."""
    if not reference_data:
        return ""
    lines = ["[Reference data from previous responses — use these IDs/keys directly]"]
    for item in reference_data[:50]:  # Cap to avoid bloat
        item_type = item.get("type", "unknown")
        parts = [f"type={item_type}"]
        for key in ("id", "key", "name", "title", "number", "owner", "repo", "url"):
            val = item.get(key)
            if val:
                parts.append(f"{key}={val}")
        lines.append("  " + ", ".join(parts))
    return "\n".join(lines)


async def build_respond_conversation_context(
    previous_conversations: List[Dict[str, Any]],
    conversation_summary: Optional[str],
    log: logging.Logger,
    max_recent_pairs: int = 5,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
    ref_mapper: Any = None,
    out_records: dict[str, dict] | None = None,
) -> list:
    """Build compact conversation context for the respond node.

    Unlike ``build_conversation_messages`` (which sends raw full-length
    messages), this function keeps the LLM focused on the current task by:

    1. Using the pre-computed ``conversation_summary`` for older turns
       (compact, no multi-thousand-char bot responses).
    2. Including only the last ``max_recent_pairs`` turns as actual
       messages, with bot responses truncated to 500 chars.
    3. Keeping the total history footprint small so the current task's
       data (retrieval blocks, tool results, analyses) gets the most
       attention.

    When *is_multimodal_llm* is True, image attachments on recent user_query
    messages are fetched from blob storage and included as ``image_url``
    content blocks alongside the text.

    PDF attachments on recent user_query messages are resolved from blob
    storage via ``record_to_message_content`` and appended to the same user
    message under an "Attached PDF documents:" label.

    Args:
        previous_conversations: Flat list from ChatState.
        conversation_summary: Pre-computed summary from the orchestrator
            (stored in ``state["conversation_summary"]``).  May be None
            for short conversations.
        log: Logger.
        max_recent_pairs: Number of recent user+bot pairs to include as
            full messages (default 3).
        is_multimodal_llm: Whether the LLM supports multimodal content.
        blob_store: BlobStorage instance for fetching image and PDF attachments.
        org_id: Organisation ID for blob storage lookups.
        ref_mapper: Shared CitationRefMapper so historical PDF citation IDs
            are consistent with those used for retrieval results.  Pass
            ``state["citation_ref_mapper"]``; a fresh one is created if not
            provided.
        out_records: If provided, historical PDF records fetched from blob
            storage are stored here keyed by virtualRecordId so callers can
            populate ``virtual_record_id_to_result`` for citation resolution.

    Returns:
        List of HumanMessage / AIMessage.
    """
    messages: list = []

    # 1. Older context via summary (compact — avoids flooding with long
    #    bot responses from previous turns)
    if conversation_summary:
        messages.append(
            HumanMessage(content=f"[Previous conversation context]\n{conversation_summary}")
        )

    # 2. Recent turns as actual messages (for immediate conversational
    #    flow).  Bot responses are truncated so they don't overwhelm the
    #    retrieval blocks / tool results that follow.
    if not previous_conversations:
        return messages

    keep_count = max_recent_pairs * 2
    recent = previous_conversations[-keep_count:]

    for conv in recent:
        role = conv.get("role", "")
        content = conv.get("content", "")
        if not content:
            continue
        if role == "user_query":
            attachments = conv.get("attachments") or []
            if is_multimodal_llm and attachments and blob_store and org_id:
                from app.utils.chat_helpers import build_multimodal_user_content
                content = await build_multimodal_user_content(
                    content, attachments, blob_store, org_id,
                )
            # Add PDF attachments from conversation history
            pdf_attachments = [
                att for att in attachments
                if isinstance(att, dict)
                and (att.get("mimeType") or "").lower() == "application/pdf"
            ]
            if pdf_attachments and blob_store and org_id:
                from app.utils.chat_helpers import record_to_message_content, CitationRefMapper
                if ref_mapper is None:
                    ref_mapper = CitationRefMapper()
                pdf_blocks: List[Dict[str, Any]] = []
                for att in pdf_attachments:
                    vrid = att.get("virtualRecordId") or ""
                    if not vrid:
                        continue
                    try:
                        record = await blob_store.get_record_from_storage(vrid, org_id)
                        if not record:
                            continue
                        if out_records is not None and vrid not in out_records:
                            out_records[vrid] = record
                        blocks, ref_mapper = record_to_message_content(record, ref_mapper=ref_mapper, is_multimodal_llm=is_multimodal_llm)
                        pdf_blocks.extend(blocks)
                    except Exception as exc:
                        log.warning("Failed to resolve historical PDF attachment vrid=%s: %s", vrid, exc)
                if pdf_blocks:
                    parts: list = list(content) if isinstance(content, list) else (
                        [{"type": "text", "text": content}] if content else []
                    )
                    parts.append({"type": "text", "text": "Attached PDF documents:"})
                    parts.extend(pdf_blocks)
                    content = parts
            messages.append(HumanMessage(content=content))
        elif role == "bot_response":
            # Truncate long bot responses — the current task's data is
            # the priority, not full previous answers.
            if isinstance(content, str) and len(content) > 500:
                content = content[:500] + "\n... [previous response truncated]"
            messages.append(AIMessage(content=content))

    if messages:
        log.debug(
            "Respond conversation context: summary=%s, %d recent messages",
            "yes" if conversation_summary else "no",
            len(recent),
        )

    return messages


# ---------------------------------------------------------------------------
# Conversation Compaction
# ---------------------------------------------------------------------------

def compact_conversation_history(
    previous_conversations: List[Dict[str, Any]],
    llm: BaseChatModel,
    log: logging.Logger,
    max_recent_pairs: int = MAX_RECENT_PAIRS,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Split conversation history into a summary + recent messages.

    previous_conversations uses the FLAT format from ChatState:
        [{"role": "user_query", "content": "..."}, {"role": "bot_response", "content": "..."}, ...]

    Each pair = 2 items (one user_query + one bot_response), so we keep
    the last max_recent_pairs*2 items and summarize the rest.

    Returns:
        (summary_text_or_None, recent_messages)
    """
    if not previous_conversations:
        return None, []

    # Each pair = 2 flat items; compare by pair count
    keep_count = max_recent_pairs * 2
    if len(previous_conversations) <= keep_count:
        return None, previous_conversations

    # Split: older messages get summarized, recent ones kept verbatim
    older = previous_conversations[:-keep_count]
    recent = previous_conversations[-keep_count:]

    # Build summary text from older messages
    summary = _summarize_conversations_sync(older, log)
    return summary, recent


async def compact_conversation_history_async(
    previous_conversations: List[Dict[str, Any]],
    llm: BaseChatModel,
    log: logging.Logger,
    max_recent_pairs: int = MAX_RECENT_PAIRS,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Async version of compact_conversation_history.

    previous_conversations uses the FLAT format from ChatState:
        [{"role": "user_query", "content": "..."}, {"role": "bot_response", "content": "..."}, ...]

    When *is_multimodal_llm* is True and *blob_store* / *org_id* are set, image
    attachments on summarized (older) user turns are included in the summary
    LLM call.

    Returns:
        (summary_text_or_None, recent_messages)
    """
    if not previous_conversations:
        return None, []

    keep_count = max_recent_pairs * 2
    if len(previous_conversations) <= keep_count:
        return None, previous_conversations

    older = previous_conversations[:-keep_count]
    recent = previous_conversations[-keep_count:]

    summary = await _summarize_conversations_async(
        older,
        llm,
        log,
        is_multimodal_llm=is_multimodal_llm,
        blob_store=blob_store,
        org_id=org_id,
    )
    return summary, recent


def _summarize_conversations_sync(
    conversations: List[Dict[str, Any]],
    log: logging.Logger,
) -> str:
    """
    Build a simple text summary without LLM (fast path).

    Handles the FLAT format: each item has "role" and "content" keys.
    """
    parts = []
    for conv in conversations:
        role = conv.get("role", "")
        if role == "user_query":
            line = _user_plain_summary_line(conv, _USER_MSG_TRUNCATE)
            if line:
                parts.append(line)
        elif role == "bot_response":
            content = conv.get("content", "")
            if not content:
                continue
            short = content[:_BOT_MSG_TRUNCATE] + "..." if len(content) > _BOT_MSG_TRUNCATE else content
            parts.append(f"Assistant: {short}")

    return "Previous conversation summary:\n" + "\n".join(parts)


async def _summarize_conversations_async(
    conversations: List[Dict[str, Any]],
    llm: BaseChatModel,
    log: logging.Logger,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
) -> str:
    """Summarize older conversations using LLM for higher quality.

    Handles the FLAT format: each item has "role" and "content" keys.
    When *is_multimodal_llm* and blob/org are set, user turns with image
    and PDF attachments are replayed alongside the text so the LLM
    produces a complete summary that captures document context.
    """
    from app.modules.agents.deep.prompts import SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS


    from app.utils.chat_helpers import build_multimodal_user_content
    from app.utils.attachment_utils import resolve_pdf_blocks_simple

    summary_messages: list = [SystemMessage(content=SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS)]
    has_any_turn = False
    for conv in conversations:
        role = conv.get("role", "")
        if role == "user_query":
            content_raw = conv.get("content", "") or ""
            attachments = conv.get("attachments") or []
            n_img = _image_attachment_count(conv)
            pdf_attachments = [
                att for att in attachments
                if isinstance(att, dict)
                and (att.get("mimeType") or "").lower() == "application/pdf"
            ]
            if not content_raw.strip() and not n_img and not pdf_attachments:
                continue
            has_any_turn = True
            text_part = content_raw[:500] + ("..." if len(content_raw) > 500 else "")

            # Build base content: text + images
            if n_img and is_multimodal_llm:
                mc = await build_multimodal_user_content(
                    text_part, attachments, blob_store, org_id,
                )
                msg_content: list | str = mc
            else:
                msg_content = text_part

            # Append PDF blocks so the LLM captures document context in summary
            if pdf_attachments:
                pdf_blocks: List[Dict[str, Any]] = []
                for att in pdf_attachments:
                    vrid = att.get("virtualRecordId") or ""
                    if not vrid:
                        continue
                    try:
                        record = await blob_store.get_record_from_storage(vrid, org_id)
                        if not record:
                            continue
                        pdf_blocks.extend(resolve_pdf_blocks_simple(record, is_multimodal_llm))
                    except Exception as exc:
                        log.warning(
                            "Failed to resolve PDF for summarization vrid=%s: %s", vrid, exc
                        )
                if pdf_blocks:
                    parts: list = list(msg_content) if isinstance(msg_content, list) else (
                        [{"type": "text", "text": msg_content}] if msg_content else []
                    )
                    parts.append({"type": "text", "text": "Attached PDF documents:"})
                    parts.extend(pdf_blocks)
                    msg_content = parts

            summary_messages.append(HumanMessage(content=msg_content))
        elif role == "bot_response":
            content = conv.get("content", "")
            if not content:
                continue
            has_any_turn = True
            short = content[:500] + ("..." if len(content) > 500 else "")
            summary_messages.append(AIMessage(content=short))

    if not has_any_turn:
        return ""

    summary_messages.append(HumanMessage(content="Provide the summary now."))
    try:
        from app.modules.agents.deep.state import get_opik_config

        response = await llm.ainvoke(summary_messages, config=get_opik_config())
        summary = response.content if hasattr(response, "content") else str(response)
        log.debug(
            "Conversation summary (multimodal replay): %d chars from %d messages",
            len(summary) if summary else 0,
            len(conversations),
        )
        return summary.strip()
    except Exception as e:
        log.warning(f"LLM summary failed, using simple summary: {e}")
        return _summarize_conversations_sync(conversations, log)



# ---------------------------------------------------------------------------
# Tool Result Compaction
# ---------------------------------------------------------------------------

def compact_tool_results(
    tool_results: List[Dict[str, Any]],
    max_chars: int = MAX_RESULT_CHARS,
) -> List[Dict[str, Any]]:
    """
    Compact tool results by preserving structure but truncating large payloads.

    Keeps: tool_name, status, duration_ms, and a truncated result.
    Preserves IDs, keys, and small structural data fully.
    """
    compacted = []
    for result in tool_results:
        entry: Dict[str, Any] = {
            "tool_name": result.get("tool_name", "unknown"),
            "status": result.get("status", "unknown"),
        }

        if result.get("duration_ms"):
            entry["duration_ms"] = result["duration_ms"]

        raw = result.get("result")
        if raw is None:
            entry["result"] = None
        elif isinstance(raw, str):
            entry["result"] = _truncate_string(raw, max_chars)
        elif isinstance(raw, dict):
            entry["result"] = _compact_dict(raw, max_chars)
        elif isinstance(raw, list):
            entry["result"] = _compact_list(raw, max_chars)
        else:
            entry["result"] = _truncate_string(str(raw), max_chars)

        if result.get("error"):
            entry["error"] = str(result["error"])[:500]

        compacted.append(entry)

    return compacted


def _truncate_string(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + TRUNCATION_MARKER


def _compact_dict(d: Dict, max_chars: int) -> Dict:
    """Compact a dict, keeping keys but truncating large values."""
    try:
        serialized = json.dumps(d, default=str)
        if len(serialized) <= max_chars:
            return d
    except (TypeError, ValueError):
        pass

    # Selectively keep important fields, truncate the rest
    compacted = {}
    budget = max_chars
    priority_keys = {"id", "key", "_key", "name", "title", "status", "url", "email",
                     "type", "success", "error", "total", "count", "nextPageToken"}

    for key in d:
        if budget <= 0:
            compacted["_truncated"] = True
            break

        value = d[key]
        if key in priority_keys:
            compacted[key] = value
            budget -= len(str(value))
        elif isinstance(value, (str, int, float, bool, type(None))):
            val_str = str(value)
            if len(val_str) <= _STR_VALUE_MAX_LEN:
                compacted[key] = value
                budget -= len(val_str)
            else:
                compacted[key] = val_str[:_STR_VALUE_MAX_LEN] + "..."
                budget -= _STR_VALUE_MAX_LEN
        elif isinstance(value, dict):
            if budget > _MIN_BUDGET_FOR_NESTED:
                compacted[key] = _compact_dict(value, min(budget, 500))
                budget -= 500
        elif isinstance(value, list):
            if budget > _MIN_BUDGET_FOR_NESTED:
                compacted[key] = _compact_list(value, min(budget, 500))
                budget -= 500

    return compacted


def _compact_list(lst: List, max_chars: int) -> List:
    """Compact a list, keeping first few items."""
    try:
        serialized = json.dumps(lst, default=str)
        if len(serialized) <= max_chars:
            return lst
    except (TypeError, ValueError):
        pass

    # Keep first few items, note total
    result = lst[:_LIST_PREVIEW_ITEMS]
    if len(lst) > _LIST_PREVIEW_ITEMS:
        result.append({"_note": f"... and {len(lst) - _LIST_PREVIEW_ITEMS} more items"})
    return result


# ---------------------------------------------------------------------------
# Sub-Agent Context Building
# ---------------------------------------------------------------------------

async def build_sub_agent_context(
    task: SubAgentTask,
    completed_tasks: List[SubAgentTask],
    conversation_summary: Optional[str],
    query: str,
    log: logging.Logger,
    recent_conversations: Optional[List[Dict[str, Any]]] = None,
    *,
    is_multimodal_llm: bool = False,
    blob_store: Any = None,
    org_id: str = "",
    ref_mapper: Any = None,
) -> List[Dict[str, Any]]:
    """
    Build isolated context for a sub-agent as a list of content blocks.

    Returns a list of content blocks compatible with SystemMessage content:
    [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]

    The sub-agent receives ONLY:
    - Its specific task description
    - Results from dependency tasks (compacted)
    - A compact conversation summary (not full history)
    - The original user query for reference
    - Recent conversation turns with images and PDF documents (for retrieval tasks that need context)

    This prevents context bloating - each sub-agent sees only what it needs.
    """
    content_blocks: List[Dict[str, Any]] = []

    # Original query
    content_blocks.append({"type": "text", "text": f"Original user query: {query}"})

    # Recent conversation turns (for retrieval tasks — helps the LLM
    # understand follow-up queries and formulate meaningful search terms)
    # Images are interleaved in conversation order alongside their text.
    if recent_conversations:
        use_multimodal = bool(is_multimodal_llm and blob_store and org_id)
        if use_multimodal:
            from app.utils.chat_helpers import build_multimodal_user_content
        from app.utils.attachment_utils import resolve_pdf_blocks_simple  # noqa: PLC0415

        content_blocks.append({"type": "text", "text": "\nRecent conversation (for context):"})

        for conv in recent_conversations[-3:]:
            role = conv.get("role", "")
            content = conv.get("content", "")

            if role == "user_query":
                if content:
                    content_blocks.append({"type": "text", "text": f"User: {content[:300]}"})

                attachments = conv.get("attachments") or []

                # Attach images right after the user text they belong to
                if use_multimodal:
                    n_img = _image_attachment_count(conv)
                    if n_img:
                        mc = await build_multimodal_user_content(
                            "", attachments, blob_store, org_id,
                        )
                        if isinstance(mc, list):
                            for block in mc:
                                if block.get("type") == "image_url":
                                    content_blocks.append(block)

                # Attach PDF documents right after images
                pdf_attachments = [
                    att for att in attachments
                    if isinstance(att, dict)
                    and (att.get("mimeType") or "").lower() == "application/pdf"
                ]
                if pdf_attachments and blob_store and org_id:
                    pdf_blocks: List[Dict[str, Any]] = []
                    for att in pdf_attachments:
                        vrid = att.get("virtualRecordId") or ""
                        if not vrid:
                            continue
                        try:
                            record = await blob_store.get_record_from_storage(vrid, org_id)
                            if not record:
                                continue
                            pdf_blocks.extend(resolve_pdf_blocks_simple(record, is_multimodal_llm))
                        except Exception as exc:
                            log.warning("Failed to resolve historical PDF attachment vrid=%s: %s", vrid, exc)
                    if pdf_blocks:
                        content_blocks.append({"type": "text", "text": "Attached PDF documents:"})
                        content_blocks.extend(pdf_blocks)

            elif role == "bot_response" and content:
                content_blocks.append({"type": "text", "text": f"Assistant: {content[:500]}"})

    # Conversation summary (if any)
    if conversation_summary:
        content_blocks.append({"type": "text", "text": f"\n{conversation_summary}"})

    # Dependency results
    dep_ids = set(task.get("depends_on", []))
    if dep_ids and completed_tasks:
        dep_parts = []
        dep_results = [
            t for t in completed_tasks
            if t.get("task_id") in dep_ids and t.get("status") == "success"
        ]
        if dep_results:
            dep_parts.append("\nResults from previous steps:")
            for dep in dep_results:
                dep_result = dep.get("result", {})
                if isinstance(dep_result, dict):
                    result_text = dep_result.get("response", "")
                    if not result_text:
                        try:
                            result_text = json.dumps(dep_result, default=str)[:2000]
                        except (TypeError, ValueError):
                            result_text = str(dep_result)[:2000]
                else:
                    result_text = str(dep_result)[:2000]

                dep_parts.append(f"[{dep.get('task_id', 'unknown')}]: {result_text}")

        # Note failed dependencies
        failed_deps = [
            t for t in completed_tasks
            if t.get("task_id") in dep_ids and t.get("status") == "error"
        ]
        if failed_deps:
            for dep in failed_deps:
                dep_parts.append(
                    f"[{dep.get('task_id', 'unknown')}] FAILED: "
                    f"{dep.get('error', 'Unknown error')[:200]}"
                )

        if dep_parts:
            content_blocks.append({"type": "text", "text": "\n".join(dep_parts)})

    # Ensure at least one text block if nothing was added
    if not content_blocks:
        content_blocks.append({"type": "text", "text": f"Original user query: {query}"})

    return content_blocks


# ---------------------------------------------------------------------------
# Batch Summarization Helpers (for complex sub-agent execution)
# ---------------------------------------------------------------------------

_MAX_BATCH_CHARS = 20000  # Max chars per batch for summarization


def group_tool_results_into_batches(
    messages: List,
    max_chars_per_batch: int = _MAX_BATCH_CHARS,
) -> List[str]:
    """
    Extract tool result messages and group them into text batches for summarization.

    Each batch is a string containing one or more tool results,
    capped at max_chars_per_batch to fit in the summarization prompt.
    """
    # Import here to avoid circular imports at module level
    from langchain_core.messages import ToolMessage as LCToolMessage

    tool_texts: List[str] = []
    for msg in messages:
        if not isinstance(msg, LCToolMessage):
            continue
        tool_name = msg.name if hasattr(msg, "name") else "unknown"
        content = msg.content
        if isinstance(content, dict):
            try:
                content = json.dumps(content, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)
        elif isinstance(content, list):
            try:
                content = json.dumps(content, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)
        elif not isinstance(content, str):
            content = str(content)

        tool_texts.append(f"[Tool: {tool_name}]\n{content}")

    if not tool_texts:
        return []

    # Group into batches by character budget
    batches: List[str] = []
    current_batch: List[str] = []
    current_size = 0

    for text in tool_texts:
        text_size = len(text)
        if current_size + text_size > max_chars_per_batch and current_batch:
            batches.append("\n\n---\n\n".join(current_batch))
            current_batch = []
            current_size = 0
        current_batch.append(text)
        current_size += text_size

    if current_batch:
        batches.append("\n\n---\n\n".join(current_batch))

    return batches


async def summarize_batch(
    batch_text: str,
    batch_number: int,
    total_batches: int,
    data_type: str,
    llm: "BaseChatModel",
    log: logging.Logger,
) -> str:
    """
    Summarize a single batch of tool results using the LLM.

    Returns a JSON string with structured summary, or a fallback
    text summary on error.
    """
    from app.modules.agents.deep.prompts import BATCH_SUMMARIZATION_PROMPT
    from app.modules.agents.deep.state import get_opik_config

    prompt = BATCH_SUMMARIZATION_PROMPT.format(
        data_type=data_type,
        batch_number=batch_number,
        total_batches=total_batches,
        raw_data=batch_text[:25000],  # Safety cap per batch — keep generous for detail preservation
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)], config=get_opik_config())
        content = response.content if hasattr(response, "content") else str(response)
        return content.strip()
    except Exception as e:
        log.warning("Batch %d/%d summarization failed: %s", batch_number, total_batches, e)
        # Fallback: return a compact version of the raw data
        return json.dumps({
            "item_count": 0,
            "error": f"Summarization failed: {str(e)[:200]}",
            "raw_preview": batch_text[:1000],
        })


async def consolidate_batch_summaries(
    batch_summaries: List[str],
    domain: str,
    task_description: str,
    time_context: str,
    llm: "BaseChatModel",
    log: logging.Logger,
) -> str:
    """
    Consolidate multiple batch summaries into a single domain-level summary.

    Returns a markdown string with the consolidated domain report.
    """
    from app.modules.agents.deep.prompts import DOMAIN_CONSOLIDATION_PROMPT
    from app.modules.agents.deep.state import get_opik_config

    # Build batch summaries text with labels
    summaries_parts = []
    for i, summary in enumerate(batch_summaries, 1):
        summaries_parts.append(f"### Batch {i}\n{summary}")
    summaries_text = "\n\n".join(summaries_parts)

    # Cap total input to prevent context overflow
    if len(summaries_text) > _MAX_SUMMARIES_TEXT_LEN:
        summaries_text = summaries_text[:_MAX_SUMMARIES_TEXT_LEN] + "\n\n[... additional batches truncated]"

    prompt = DOMAIN_CONSOLIDATION_PROMPT.format(
        domain=domain,
        task_description=task_description,
        time_context=time_context or "Not specified",
        batch_summaries=summaries_text,
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)], config=get_opik_config())
        content = response.content if hasattr(response, "content") else str(response)
        return content.strip()
    except Exception as e:
        log.warning("Domain consolidation for %s failed: %s", domain, e)
        # Fallback: concatenate batch summaries without aggressive truncation
        return f"## {domain.title()} Summary\n\n" + "\n\n".join(
            f"**Batch {i}**:\n{s}" for i, s in enumerate(batch_summaries, 1)
        )
