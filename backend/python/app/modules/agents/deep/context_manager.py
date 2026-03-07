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

    from app.modules.agents.deep.state import DeepAgentState, SubAgentTask

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_RESULT_CHARS = 3000  # Max chars per tool result in compacted form
MAX_SUMMARY_WORDS = 200
MAX_RECENT_PAIRS = 5  # Keep last N conversation pairs verbatim
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
            user_short = user_msg[:200] + "..." if len(user_msg) > 200 else user_msg
            parts.append(f"User: {user_short}")

        if bot_msg:
            bot_short = bot_msg[:300] + "..." if len(bot_msg) > 300 else bot_msg
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
        prompt = SUMMARY_PROMPT.format(conversation=conv_text)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
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
            if len(val_str) <= 200:
                compacted[key] = value
                budget -= len(val_str)
            else:
                compacted[key] = val_str[:200] + "..."
                budget -= 200
        elif isinstance(value, dict):
            if budget > 100:
                compacted[key] = _compact_dict(value, min(budget, 500))
                budget -= 500
        elif isinstance(value, list):
            if budget > 100:
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

    # Keep first 3 items, note total
    result = lst[:3]
    if len(lst) > 3:
        result.append({"_note": f"... and {len(lst) - 3} more items"})
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
) -> str:
    """
    Build isolated context for a sub-agent.

    The sub-agent receives ONLY:
    - Its specific task description
    - Results from dependency tasks (compacted)
    - A compact conversation summary (not full history)
    - The original user query for reference

    This prevents context bloating - each sub-agent sees only what it needs.
    """
    parts = []

    # Original query
    parts.append(f"Original user query: {query}")

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
