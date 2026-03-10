"""
Deep Agent Respond Node - Dedicated response generator for the deep agent.

ROOT CAUSE (why a dedicated node is needed):
    LangGraph StateGraph filters state keys based on node function type annotations.
    The shared respond_node in qna/nodes.py is typed as `ChatState`, so LangGraph
    strips all DeepAgentState-only keys (completed_tasks, sub_agent_analyses, etc.)
    before passing state to it. This node is typed as `DeepAgentState`, so it
    receives ALL state keys correctly.

Pipeline:
    1. Collects analyses from sub_agent_analyses + completed_tasks
    2. Includes raw tool results as supplementary data for links/details
    3. Includes conversation history for context
    4. Builds a comprehensive prompt and streams the response
    5. Extracts reference links for the frontend
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.deep.state import DeepAgentState, get_opik_config
from app.modules.agents.qna.stream_utils import safe_stream_write

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """{instructions_prefix}{role_prefix}You are a highly capable AI assistant. Your job is to synthesize data from multiple specialized sub-agents into a comprehensive, well-organized response.

## Objectives
- **Reuse pre-formatted content** — the sub-agent analyses below may already contain well-formatted markdown tables and lists. Incorporate them directly into your response. Do NOT rebuild tables from scratch when a good one already exists.
- **Present ALL data collected** — every item the sub-agents found must appear in your response. Do not drop, skip, or over-summarize items.
- **Merge overlapping data** — if multiple analyses cover the same items, merge them into a single deduplicated list/table. Do not repeat the same item twice.
- **Every item must be a clickable markdown link** `[Title](url)` using URLs from the data.
- **Use tables** for lists of 5+ items. Each column must contain the CORRECT field value — Status column shows status, Priority column shows priority, etc. Never put the title/link in every column.
- **Be precise** — exact dates, times, names, statuses, counts. No vague phrases like "several" or "multiple".
- **Address every part** of the user's query. Note explicitly if data for any part is missing.
- **Output clean markdown only** — no JSON, no code fences around the whole response.
- **Do not fabricate** — only use data provided below.
{user_context}"""

_USER_PROMPT = """**User Query**: {query}

## Sub-Agent Analyses (primary data — reuse formatting directly)
{analyses_text}

{tool_results_section}

{conversation_context}Synthesize the sub-agent analyses into a single comprehensive response. The analyses already contain well-formatted tables and lists — reuse them directly, merging overlapping items. Do not rebuild tables from raw data. Ensure each table column contains the correct field value."""


# ---------------------------------------------------------------------------
# Main respond node
# ---------------------------------------------------------------------------

async def deep_respond_node(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> DeepAgentState:
    """
    Generate the final response for the deep agent pipeline.

    This node is typed as DeepAgentState (not ChatState) so LangGraph passes
    ALL state keys including completed_tasks, sub_agent_analyses, etc.
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")

    # ---------------------------------------------------------------
    # Diagnostic: log state keys available to this node
    # ---------------------------------------------------------------
    _log_state_diagnostic(state, log)

    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "generating", "message": "Generating response..."},
    }, config)

    # ---------------------------------------------------------------
    # Handle error state
    # ---------------------------------------------------------------
    if state.get("error"):
        return _handle_error_state(state, writer, config, log)

    # ---------------------------------------------------------------
    # Handle clarification
    # ---------------------------------------------------------------
    reflection_decision = state.get("reflection_decision", "respond_success")
    reflection = state.get("reflection", {})

    if reflection_decision == "respond_clarify":
        return _handle_clarify(state, reflection, writer, config, log)

    # ---------------------------------------------------------------
    # Handle error decision from aggregator
    # ---------------------------------------------------------------
    if reflection_decision == "respond_error":
        return _handle_error_decision(state, reflection, writer, config, log)

    # ---------------------------------------------------------------
    # Handle direct answer (orchestrator decided no tools needed)
    # ---------------------------------------------------------------
    task_plan = state.get("task_plan") or {}
    if task_plan.get("can_answer_directly"):
        return await _handle_direct_answer(state, llm, writer, config, log)

    # ---------------------------------------------------------------
    # Main path: collect ALL available data
    # ---------------------------------------------------------------
    analyses = _collect_analyses(state, log)
    tool_results = _collect_tool_results(state, log)

    if not analyses and not tool_results:
        log.warning("deep_respond_node: no analyses or tool results, generating fallback")
        return await _handle_no_data(state, llm, writer, config, log)

    log.info(
        "deep_respond_node: %d analyses, %d tool results — generating response",
        len(analyses), len(tool_results),
    )

    # Build prompt with all collected data
    query = state.get("query", "")
    messages = _build_response_messages(state, query, analyses, tool_results, log)

    # Log prompt size for diagnostics
    total_chars = sum(len(m.content) if isinstance(m.content, str) else 0 for m in messages)
    log.info("deep_respond_node: prompt built, %d chars total", total_chars)

    # Stream response
    full_content = await _stream_llm_response(llm, messages, writer, config, log)

    if not full_content.strip():
        log.warning("LLM returned empty response, using fallback")
        full_content = _build_fallback_response(analyses)
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": full_content, "accumulated": full_content, "citations": []},
        }, config)

    # Build completion data
    completion_data = {
        "answer": full_content.strip(),
        "citations": [],
        "confidence": "High",
        "answerMatchType": "Derived From Tool Execution",
    }

    # Extract reference links from both analyses and tool results
    reference_data = _extract_reference_links(analyses, tool_results)
    if reference_data:
        completion_data["referenceData"] = reference_data

    safe_stream_write(writer, {"event": "complete", "data": completion_data}, config)
    state["response"] = full_content.strip()
    state["completion_data"] = completion_data

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info("deep_respond_node completed in %.0fms (%d chars response)", duration_ms, len(full_content))
    return state


# ---------------------------------------------------------------------------
# Diagnostic logging
# ---------------------------------------------------------------------------

def _log_state_diagnostic(state: DeepAgentState, log: logging.Logger) -> None:
    """Log which deep-agent keys are present and their sizes."""
    diag = {}

    completed = state.get("completed_tasks")
    if completed:
        success = sum(1 for t in completed if t.get("status") == "success")
        error = sum(1 for t in completed if t.get("status") == "error")
        diag["completed_tasks"] = f"{len(completed)} ({success} ok, {error} err)"
    else:
        diag["completed_tasks"] = "EMPTY"

    analyses = state.get("sub_agent_analyses")
    diag["sub_agent_analyses"] = len(analyses) if analyses else "EMPTY"

    tools = state.get("tool_results")
    diag["tool_results"] = len(tools) if tools else "EMPTY"

    diag["reflection_decision"] = state.get("reflection_decision", "NOT_SET")

    plan = state.get("task_plan")
    diag["task_plan"] = "present" if plan else "EMPTY"

    log.info("deep_respond_node state: %s", diag)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _collect_analyses(state: DeepAgentState, log: logging.Logger) -> List[str]:
    """
    Collect analyses from all available sources.

    Priority:
    1. sub_agent_analyses (pre-built by execute_sub_agents_node)
    2. Rebuild from completed_tasks (fallback)
    """
    # Source 1: sub_agent_analyses
    analyses = state.get("sub_agent_analyses") or []
    if analyses:
        log.info("Collected %d analyses from sub_agent_analyses", len(analyses))
        return list(analyses)

    # Source 2: rebuild from completed_tasks
    completed = state.get("completed_tasks") or []
    if not completed:
        log.warning("No sub_agent_analyses and no completed_tasks in state")
        return []

    log.info("Rebuilding analyses from %d completed_tasks", len(completed))
    rebuilt = []
    for task in completed:
        if task.get("status") != "success":
            continue

        task_id = task.get("task_id", "unknown")
        domains = ", ".join(task.get("domains", []))
        label = f"[{task_id} ({domains})]"

        # Complex tasks: use the consolidated domain summary
        domain_summary = task.get("domain_summary")
        if domain_summary:
            rebuilt.append(f"{label}: {domain_summary}")
            continue

        # Simple tasks: use the response text
        task_result = task.get("result", {})
        if isinstance(task_result, dict):
            response_text = task_result.get("response", "")
            if response_text:
                rebuilt.append(f"{label}: {response_text}")

    if rebuilt:
        log.info("Rebuilt %d analyses from completed_tasks", len(rebuilt))
    else:
        log.warning("completed_tasks present but no usable data found")

    return rebuilt


def _collect_tool_results(state: DeepAgentState, log: logging.Logger) -> List[Dict[str, Any]]:
    """
    Collect raw tool results for supplementary data (links, exact values).

    SMART CONSOLIDATION: Skip raw tool results when analyses already contain
    substantial formatted data for the same domains. Raw JSON injected
    alongside well-formatted analyses confuses the LLM and produces corrupted
    tables (e.g., every column filled with the title link).

    Rules:
    1. Complex tasks with domain_summary → always skip raw results for that domain
    2. Simple tasks with substantial analyses (>500 chars) → skip raw results
       for that domain (the sub-agent already formatted the data well)
    3. Only include raw results when analyses are missing or very short
    """
    completed = state.get("completed_tasks") or []
    analyses = state.get("sub_agent_analyses") or []

    # Build set of domains that already have comprehensive data in analyses.
    covered_domains: set = set()

    for t in completed:
        if t.get("status") != "success":
            continue
        task_domains = [d.lower() for d in t.get("domains", [])]

        # Complex tasks with domain_summary → always covered
        if t.get("domain_summary"):
            for d in task_domains:
                covered_domains.add(d)
            continue

        # Simple tasks: check if the analysis is substantial enough
        task_result = t.get("result", {})
        response_text = ""
        if isinstance(task_result, dict):
            response_text = task_result.get("response", "")

        # If the sub-agent produced a substantial formatted response (>500 chars),
        # the raw API data would just be redundant and confuse the LLM.
        if len(response_text) > 500:
            for d in task_domains:
                covered_domains.add(d)

    if covered_domains:
        log.info("Domains covered by analyses (skipping raw results): %s",
                 ", ".join(sorted(covered_domains)))

    # If ALL domains are covered, skip raw results entirely
    all_domains = set()
    for t in completed:
        if t.get("status") == "success":
            for d in t.get("domains", []):
                all_domains.add(d.lower())

    if all_domains and all_domains <= covered_domains:
        log.info("All %d domains covered by analyses — skipping all raw tool results",
                 len(all_domains))
        return []

    all_results = state.get("tool_results") or state.get("all_tool_results") or []
    if not all_results:
        return []

    useful = []
    for r in all_results:
        if r.get("status") != "success":
            continue
        tool_name = r.get("tool_name", "")
        # Skip retrieval results (they don't have API data with links)
        if "retrieval" in tool_name.lower() or "knowledge" in tool_name.lower():
            continue
        # Skip results from domains that already have comprehensive analyses
        if covered_domains:
            tool_domain = tool_name.split(".")[0].lower() if "." in tool_name else ""
            if tool_domain and tool_domain in covered_domains:
                continue
        useful.append(r)

    if useful:
        log.info("Collected %d useful tool results for supplementary data", len(useful))

    return useful


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_response_messages(
    state: DeepAgentState,
    query: str,
    analyses: List[str],
    tool_results: List[Dict[str, Any]],
    log: logging.Logger,
) -> list:
    """Build the complete LLM message list for response generation."""
    messages = []

    # --- System message ---
    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    role_prefix = ""
    base_system_prompt = state.get("system_prompt", "")
    if (base_system_prompt
            and base_system_prompt.strip()
            and base_system_prompt != "You are an enterprise questions answering expert"):
        role_prefix = f"{base_system_prompt.strip()}\n\n"

    user_context = _format_user_context(state)
    user_context_section = ""
    if user_context:
        user_context_section = (
            f"\n\n## Current User\n{user_context}\n"
            "When the user asks about themselves, use the provided info DIRECTLY."
        )

    system_content = _SYSTEM_PROMPT.format(
        instructions_prefix=instructions_prefix,
        role_prefix=role_prefix,
        user_context=user_context_section,
    )
    messages.append(SystemMessage(content=system_content))

    # --- Conversation history (sliding window) ---
    previous = state.get("previous_conversations", [])
    if previous:
        history = _build_conversation_history(previous)
        messages.extend(history)

    # --- User message with analyses + tool results ---
    analyses_text = "\n\n".join(analyses)

    # Build tool results section if we have supplementary API data
    tool_results_section = _format_tool_results_for_prompt(tool_results, log)

    # Build conversation context hint
    conversation_context = ""
    conversation_summary = state.get("conversation_summary")
    if conversation_summary:
        conversation_context = f"## Previous Conversation Context\n{conversation_summary}\n\n"

    user_content = _USER_PROMPT.format(
        query=query,
        analyses_text=analyses_text,
        tool_results_section=tool_results_section,
        conversation_context=conversation_context,
    )

    messages.append(HumanMessage(content=user_content))

    return messages


def _format_tool_results_for_prompt(
    tool_results: List[Dict[str, Any]],
    log: logging.Logger,
) -> str:
    """
    Format raw tool results as supplementary data for the LLM.

    These are only included when analyses are missing or insufficient
    (smart consolidation in _collect_tool_results skips results for
    domains that already have comprehensive summaries). What remains
    here is the primary data source and must be included in full.
    """
    if not tool_results:
        return ""

    parts = ["## Raw API Data (use for extracting exact links, details, and any items not in the analyses above)"]

    for r in tool_results:
        tool_name = r.get("tool_name", "unknown")
        content = r.get("result", "")

        # Convert to string
        if isinstance(content, (dict, list)):
            try:
                content_str = json.dumps(content, indent=2, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                content_str = str(content)
        else:
            content_str = str(content)

        section = f"### {tool_name}\n```json\n{content_str}\n```"
        parts.append(section)

    return "\n\n".join(parts)


def _format_user_context(state: DeepAgentState) -> str:
    """Format user info for the prompt."""
    user_info = state.get("user_info") or {}
    org_info = state.get("org_info") or {}

    user_email = (
        state.get("user_email")
        or user_info.get("userEmail")
        or user_info.get("email")
        or ""
    )
    user_name = (
        user_info.get("fullName")
        or user_info.get("name")
        or user_info.get("displayName")
        or ""
    )

    if not user_email and not user_name:
        return ""

    parts = []
    if user_name:
        parts.append(f"Name: {user_name}")
    if user_email:
        parts.append(f"Email: {user_email}")
    if org_info.get("name"):
        parts.append(f"Organization: {org_info['name']}")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# LLM streaming
# ---------------------------------------------------------------------------

async def _stream_llm_response(
    llm: Any,
    messages: list,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> str:
    """Stream LLM response to the frontend."""
    full_content = ""
    opik_config = get_opik_config()

    try:
        if hasattr(llm, "astream"):
            async for chunk in llm.astream(messages, config=opik_config):
                if not chunk:
                    continue

                chunk_text = _extract_chunk_text(chunk)
                if chunk_text:
                    full_content += chunk_text
                    safe_stream_write(writer, {
                        "event": "answer_chunk",
                        "data": {
                            "chunk": chunk_text,
                            "accumulated": full_content,
                            "citations": [],
                        },
                    }, config)
        else:
            response = await llm.ainvoke(messages, config=opik_config)
            full_content = response.content if hasattr(response, "content") else str(response)
            safe_stream_write(writer, {
                "event": "answer_chunk",
                "data": {
                    "chunk": full_content,
                    "accumulated": full_content,
                    "citations": [],
                },
            }, config)

    except Exception as e:
        log.error("LLM response generation failed: %s", e, exc_info=True)
        full_content = (
            "I encountered an error while generating the response. "
            "The data was successfully collected but could not be formatted. "
            "Please try again."
        )
        safe_stream_write(writer, {
            "event": "answer_chunk",
            "data": {"chunk": full_content, "accumulated": full_content, "citations": []},
        }, config)

    return full_content


def _extract_chunk_text(chunk: Any) -> str:
    """Extract text from an LLM streaming chunk."""
    if not hasattr(chunk, "content"):
        return ""
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Fallback response (when LLM returns empty)
# ---------------------------------------------------------------------------

def _build_fallback_response(analyses: List[str]) -> str:
    """Build a fallback response directly from analyses when LLM fails."""
    parts = ["Here's what I found:\n"]
    for analysis in analyses:
        # Strip the [task_id (domains)]: prefix for cleaner output
        if "]: " in analysis:
            content = analysis.split("]: ", 1)[1]
        else:
            content = analysis
        parts.append(content)
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Reference link extraction
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(r'https?://[^\s\)\]>]+')


def _extract_reference_links(
    analyses: List[str],
    tool_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract unique URLs from analyses and tool results for frontend referenceData."""
    seen: set = set()
    links: List[Dict[str, Any]] = []

    # From analyses text
    for text in analyses:
        for url in _URL_PATTERN.findall(text):
            url = url.rstrip(".,;:!?\"'")
            if url not in seen:
                seen.add(url)
                links.append({"url": url})

    # From tool results
    for r in tool_results:
        _extract_urls_from_value(r.get("result", ""), seen, links)

    return links[:100]


def _extract_urls_from_value(value: Any, seen: set, links: list, depth: int = 0) -> None:
    """Recursively extract URLs from tool result values."""
    if depth > 3:
        return

    if isinstance(value, str):
        for url in _URL_PATTERN.findall(value):
            url = url.rstrip(".,;:!?\"'")
            if url not in seen:
                seen.add(url)
                links.append({"url": url})
    elif isinstance(value, dict):
        # Check common URL fields first
        url_fields = ("url", "webLink", "webViewLink", "htmlUrl", "permalink",
                       "link", "href", "self", "joinUrl", "joinWebUrl")
        for field in url_fields:
            val = value.get(field)
            if isinstance(val, str) and val.startswith("http"):
                if val not in seen:
                    seen.add(val)
                    links.append({"url": val})
        # Recurse into values
        for v in value.values():
            _extract_urls_from_value(v, seen, links, depth + 1)
    elif isinstance(value, list):
        for item in value[:20]:  # Cap list traversal
            _extract_urls_from_value(item, seen, links, depth + 1)


# ---------------------------------------------------------------------------
# Special case handlers
# ---------------------------------------------------------------------------

def _handle_error_state(
    state: DeepAgentState,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle pre-existing error in state."""
    error = state["error"]
    error_msg = error.get("message", error.get("detail", "An error occurred"))
    completion = {
        "answer": error_msg,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "Error",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = error_msg
    state["completion_data"] = completion
    return state


def _handle_clarify(
    state: DeepAgentState,
    reflection: Dict[str, Any],
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle clarification request."""
    question = reflection.get("clarifying_question", "Could you provide more details?")
    completion = {
        "answer": question,
        "citations": [],
        "confidence": "Medium",
        "answerMatchType": "Clarification Needed",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": question, "accumulated": question, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = question
    state["completion_data"] = completion
    return state


def _handle_error_decision(
    state: DeepAgentState,
    reflection: Dict[str, Any],
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle aggregator's error decision."""
    error_context = reflection.get("error_context", "")
    if error_context:
        error_msg = f"I wasn't able to complete that request. {error_context}\n\nPlease try again."
    else:
        error_msg = "I encountered errors while processing your request. Please try again."

    completion = {
        "answer": error_msg,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "Tool Execution Failed",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = error_msg
    state["completion_data"] = completion
    return state


async def _handle_direct_answer(
    state: DeepAgentState,
    llm: Any,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle direct answer (no tools needed)."""
    query = state.get("query", "")

    instructions_prefix = ""
    agent_instructions = state.get("instructions")
    if agent_instructions and agent_instructions.strip():
        instructions_prefix = f"## Agent Instructions\n{agent_instructions.strip()}\n\n"

    system_content = f"{instructions_prefix}You are a helpful, friendly AI assistant. Respond naturally and concisely."

    user_context = _format_user_context(state)
    user_content = query
    if user_context:
        user_content += f"\n\n{user_context}"
        system_content += "\n\nWhen the user asks about themselves, use the provided info DIRECTLY."

    messages = [SystemMessage(content=system_content)]

    # Include conversation history
    previous = state.get("previous_conversations", [])
    if previous:
        messages.extend(_build_conversation_history(previous))

    messages.append(HumanMessage(content=user_content))

    full_content = await _stream_llm_response(llm, messages, writer, config, log)

    completion = {
        "answer": full_content.strip() or "I'm here to help! How can I assist you?",
        "citations": [],
        "confidence": "High",
        "answerMatchType": "Direct Response",
    }
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = full_content.strip()
    state["completion_data"] = completion
    return state


async def _handle_no_data(
    state: DeepAgentState,
    llm: Any,
    writer: StreamWriter,
    config: RunnableConfig,
    log: logging.Logger,
) -> DeepAgentState:
    """Handle case where no analyses or tool results are available."""
    completed = state.get("completed_tasks") or []
    error_tasks = [t for t in completed if t.get("status") == "error"]

    if error_tasks:
        error_details = []
        for t in error_tasks[:3]:
            tid = t.get("task_id", "unknown")
            err = t.get("error", "Unknown error")[:200]
            error_details.append(f"- **{tid}**: {err}")

        error_msg = (
            "I wasn't able to retrieve the data needed to answer your question.\n\n"
            "**Issues encountered:**\n"
            + "\n".join(error_details)
            + "\n\nPlease try again or rephrase your question."
        )
    else:
        error_msg = (
            "I wasn't able to find relevant data to answer your question. "
            "Please try rephrasing or providing more details."
        )

    completion = {
        "answer": error_msg,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "No Data Available",
    }
    safe_stream_write(writer, {
        "event": "answer_chunk",
        "data": {"chunk": error_msg, "accumulated": error_msg, "citations": []},
    }, config)
    safe_stream_write(writer, {"event": "complete", "data": completion}, config)
    state["response"] = error_msg
    state["completion_data"] = completion
    return state


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

def _build_conversation_history(
    previous_conversations: List[Dict[str, Any]],
    max_pairs: int = 5,
) -> list:
    """Build LangChain messages from previous conversations (sliding window)."""
    messages = []
    recent = previous_conversations[-max_pairs:]

    for conv in recent:
        user_msg = conv.get("user", conv.get("query", ""))
        bot_msg = conv.get("bot", conv.get("response", conv.get("answer", "")))

        if user_msg:
            messages.append(HumanMessage(content=str(user_msg)[:1000]))
        if bot_msg:
            messages.append(AIMessage(content=str(bot_msg)[:1500]))

    return messages
