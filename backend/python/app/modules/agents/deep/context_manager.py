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

from langchain_core.messages import HumanMessage

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

    For short histories (<=max_recent_pairs), returns (None, all_messages).
    For longer histories, summarizes older messages via LLM.

    Returns:
        (summary_text_or_None, recent_messages)
    """
    if not previous_conversations:
        return None, []

    if len(previous_conversations) <= max_recent_pairs:
        return None, previous_conversations

    # Split: older messages get summarized, recent ones kept verbatim
    older = previous_conversations[:-max_recent_pairs]
    recent = previous_conversations[-max_recent_pairs:]

    # Build summary text from older messages
    summary = _summarize_conversations_sync(older, log)
    return summary, recent


async def compact_conversation_history_async(
    previous_conversations: List[Dict[str, Any]],
    llm: BaseChatModel,
    log: logging.Logger,
    max_recent_pairs: int = MAX_RECENT_PAIRS,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Async version: summarize older messages using LLM.

    Returns:
        (summary_text_or_None, recent_messages)
    """
    if not previous_conversations:
        return None, []

    if len(previous_conversations) <= max_recent_pairs:
        return None, previous_conversations

    older = previous_conversations[:-max_recent_pairs]
    recent = previous_conversations[-max_recent_pairs:]

    summary = await _summarize_conversations_async(older, llm, log)
    return summary, recent


def _summarize_conversations_sync(
    conversations: List[Dict[str, Any]],
    log: logging.Logger,
) -> str:
    """
    Build a simple text summary without LLM (fast path).

    Extracts key points from older messages to create a compact context.
    """
    parts = []
    for conv in conversations:
        user_msg = conv.get("user", conv.get("query", ""))
        bot_msg = conv.get("bot", conv.get("response", conv.get("answer", "")))

        if user_msg:
            # Truncate long messages
            user_short = user_msg[:_USER_MSG_TRUNCATE] + "..." if len(user_msg) > _USER_MSG_TRUNCATE else user_msg
            parts.append(f"User: {user_short}")

        if bot_msg:
            bot_short = bot_msg[:_BOT_MSG_TRUNCATE] + "..." if len(bot_msg) > _BOT_MSG_TRUNCATE else bot_msg
            parts.append(f"Assistant: {bot_short}")

    return "Previous conversation summary:\n" + "\n".join(parts)


async def _summarize_conversations_async(
    conversations: List[Dict[str, Any]],
    llm: BaseChatModel,
    log: logging.Logger,
) -> str:
    """Summarize older conversations using LLM for higher quality."""
    from app.modules.agents.deep.prompts import SUMMARY_PROMPT

    # Build conversation text
    conv_text = ""
    for conv in conversations:
        user_msg = conv.get("user", conv.get("query", ""))
        bot_msg = conv.get("bot", conv.get("response", conv.get("answer", "")))
        if user_msg:
            conv_text += f"User: {user_msg[:500]}\n"
        if bot_msg:
            conv_text += f"Assistant: {bot_msg[:500]}\n"

    if not conv_text.strip():
        return ""

    try:
        from app.modules.agents.deep.state import get_opik_config

        prompt = SUMMARY_PROMPT.format(conversation=conv_text)
        response = await llm.ainvoke([HumanMessage(content=prompt)], config=get_opik_config())
        summary = response.content if hasattr(response, "content") else str(response)
        log.debug(f"Conversation summary: {len(summary)} chars from {len(conversations)} messages")
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

def build_sub_agent_context(
    task: SubAgentTask,
    completed_tasks: List[SubAgentTask],
    conversation_summary: Optional[str],
    query: str,
    log: logging.Logger,
    recent_conversations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Build isolated context for a sub-agent.

    The sub-agent receives ONLY:
    - Its specific task description
    - Results from dependency tasks (compacted)
    - A compact conversation summary (not full history)
    - The original user query for reference
    - Recent conversation turns (for retrieval tasks that need context)

    This prevents context bloating - each sub-agent sees only what it needs.
    """
    parts = []

    # Original query
    parts.append(f"Original user query: {query}")

    # Recent conversation turns (for retrieval tasks — helps the LLM
    # understand follow-up queries and formulate meaningful search terms)
    if recent_conversations:
        conv_parts = []
        for conv in recent_conversations[-3:]:
            role = conv.get("role", "")
            content = conv.get("content", "")
            if role == "user_query" and content:
                conv_parts.append(f"User: {content[:300]}")
            elif role == "bot_response" and content:
                # Include enough of the response to understand the topic
                conv_parts.append(f"Assistant: {content[:500]}")
        if conv_parts:
            parts.append(
                "\nRecent conversation (for context):\n"
                + "\n".join(conv_parts)
            )

    # Conversation summary (if any)
    if conversation_summary:
        parts.append(f"\n{conversation_summary}")

    # Dependency results
    dep_ids = set(task.get("depends_on", []))
    if dep_ids and completed_tasks:
        dep_results = [
            t for t in completed_tasks
            if t.get("task_id") in dep_ids and t.get("status") == "success"
        ]
        if dep_results:
            parts.append("\nResults from previous steps:")
            for dep in dep_results:
                dep_result = dep.get("result", {})
                # Compact the dependency result
                if isinstance(dep_result, dict):
                    result_text = dep_result.get("response", "")
                    if not result_text:
                        try:
                            result_text = json.dumps(dep_result, default=str)[:2000]
                        except (TypeError, ValueError):
                            result_text = str(dep_result)[:2000]
                else:
                    result_text = str(dep_result)[:2000]

                parts.append(f"[{dep.get('task_id', 'unknown')}]: {result_text}")

        # Note failed dependencies
        failed_deps = [
            t for t in completed_tasks
            if t.get("task_id") in dep_ids and t.get("status") == "error"
        ]
        if failed_deps:
            for dep in failed_deps:
                parts.append(
                    f"[{dep.get('task_id', 'unknown')}] FAILED: "
                    f"{dep.get('error', 'Unknown error')[:200]}"
                )

    return "\n".join(parts)


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
