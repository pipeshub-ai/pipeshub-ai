"""
Sub-Agent Execution - Isolated Context Task Execution

Each sub-agent runs with an isolated context window:
- Only its specific task description
- Only its assigned tools
- Results from dependency tasks (compacted)
- A compact conversation summary (not full history)

Independent tasks run in parallel via asyncio.gather.
Dependent tasks run sequentially after their dependencies complete.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.types import StreamWriter

from app.modules.agents.deep.context_manager import build_sub_agent_context
from app.modules.agents.deep.prompts import SUB_AGENT_SYSTEM_PROMPT
from app.modules.agents.deep.state import DeepAgentState, SubAgentTask
from app.modules.agents.deep.tool_router import get_tools_for_sub_agent
from app.modules.agents.qna.stream_utils import safe_stream_write

logger = logging.getLogger(__name__)

# Constants
SUB_AGENT_TIMEOUT_SECONDS = 120.0
MAX_SUB_AGENT_RECURSION = 25
_MAX_TOOL_RESULT_CHARS = 8000   # Max chars per tool result within sub-agent
_MAX_TOOL_CALLS_PER_AGENT = 15  # Max tool calls before budget exhaustion


async def execute_sub_agents_node(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> DeepAgentState:
    """
    Execute all sub-agent tasks respecting dependencies.

    1. Group tasks by dependency level
    2. Execute independent tasks in parallel (asyncio.gather)
    3. Execute dependent tasks sequentially (after dependencies complete)
    4. Collect results into state for the aggregator
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    tasks = state.get("sub_agent_tasks", [])

    if not tasks:
        log.warning("No sub-agent tasks to execute")
        return state

    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "executing",
            "message": f"Executing {len(tasks)} task(s)...",
        },
    }, config)

    # Organize tasks into execution levels by dependencies
    levels = _build_execution_levels(tasks, log)
    completed: List[SubAgentTask] = list(state.get("completed_tasks", []))

    for level_idx, level_tasks in enumerate(levels):
        log.info(
            "Executing level %d: %d task(s) [%s]",
            level_idx,
            len(level_tasks),
            ", ".join(t["task_id"] for t in level_tasks),
        )

        if len(level_tasks) == 1:
            # Single task - execute directly
            result = await _execute_single_sub_agent(
                level_tasks[0], state, completed, config, writer, log,
            )
            completed.append(result)
        else:
            # Multiple independent tasks - execute in parallel
            coros = [
                _execute_single_sub_agent(
                    task, state, completed, config, writer, log,
                )
                for task in level_tasks
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    log.error("Sub-agent %s raised exception: %s", level_tasks[i]["task_id"], result)
                    failed_task = {**level_tasks[i], "status": "error", "error": str(result)}
                    completed.append(failed_task)
                else:
                    completed.append(result)

    state["completed_tasks"] = completed

    # Collect all tool results from ALL completed tasks across iterations
    # (for respond_node compatibility)
    all_tool_results = []
    for task in completed:
        task_result = task.get("result", {})
        if isinstance(task_result, dict):
            tool_results_list = task_result.get("tool_results", [])
            all_tool_results.extend(tool_results_list)

    state["all_tool_results"] = all_tool_results
    state["tool_results"] = all_tool_results

    # Collect sub-agent response analyses for respond_node
    # This gives the respond_node LLM a pre-analyzed summary alongside raw data
    sub_agent_analyses = []
    for task in completed:
        if task.get("status") == "success":
            task_result = task.get("result", {})
            if isinstance(task_result, dict):
                response_text = task_result.get("response", "")
                if response_text:
                    task_id = task.get("task_id", "unknown")
                    domains = ", ".join(task.get("domains", []))
                    sub_agent_analyses.append(
                        f"[{task_id} ({domains})]: {response_text}"
                    )
    state["sub_agent_analyses"] = sub_agent_analyses

    duration_ms = (time.perf_counter() - start_time) * 1000
    success_count = sum(1 for t in completed if t.get("status") == "success")
    error_count = sum(1 for t in completed if t.get("status") == "error")
    log.info(
        "Sub-agents completed: %d success, %d errors in %.0fms",
        success_count, error_count, duration_ms,
    )

    return state


async def _execute_single_sub_agent(
    task: SubAgentTask,
    state: DeepAgentState,
    completed_tasks: List[SubAgentTask],
    config: RunnableConfig,
    writer: StreamWriter,
    log: logging.Logger,
) -> SubAgentTask:
    """
    Execute a single sub-agent with isolated context.

    Uses LangChain's create_agent() with:
    - Only the tools assigned to this task
    - A focused system prompt for the specific task
    - Isolated message history (just the task + dependencies)
    """
    task_id = task.get("task_id", "unknown")
    task_desc = task.get("description", "")
    start_time = time.perf_counter()

    log.info("Starting sub-agent: %s", task_id)

    # Check if dependencies all succeeded
    dep_ids = set(task.get("depends_on", []))
    if dep_ids:
        failed_deps = [
            t for t in completed_tasks
            if t.get("task_id") in dep_ids and t.get("status") != "success"
        ]
        if failed_deps:
            dep_names = ", ".join(t["task_id"] for t in failed_deps)
            log.warning("Skipping %s: dependencies failed [%s]", task_id, dep_names)
            return {
                **task,
                "status": "skipped",
                "error": f"Dependencies failed: {dep_names}",
                "duration_ms": (time.perf_counter() - start_time) * 1000,
            }

    # Stream status
    task_display = task_desc[:80] + "..." if len(task_desc) > 80 else task_desc
    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "executing", "message": task_display},
    }, config)

    try:
        # Build isolated context for this sub-agent
        context_text = build_sub_agent_context(
            task=task,
            completed_tasks=completed_tasks,
            conversation_summary=state.get("conversation_summary"),
            query=state.get("query", ""),
            log=log,
        )

        # Get filtered tools for this sub-agent (StructuredTools with args_schema)
        tools = get_tools_for_sub_agent(task.get("tools", []), state)

        # Wrap tools with output truncation and call budget to prevent context overflow
        budget = _ToolCallBudget(_MAX_TOOL_CALLS_PER_AGENT)
        tools = _wrap_tools_with_truncation(tools, budget, _MAX_TOOL_RESULT_CHARS, log)

        # Build tool schemas description for the system prompt
        tool_schemas_text = _format_tools_for_prompt(tools, log)

        # Build tool guidance for this task's domains
        tool_guidance = _build_sub_agent_tool_guidance(task, state)

        # Build time context
        time_ctx = ""
        current_time = state.get("current_time")
        timezone = state.get("timezone")
        if current_time:
            time_ctx += f"Current time: {current_time}"
        if timezone:
            time_ctx += f"\nTimezone: {timezone}"

        # Build agent instructions prefix
        agent_instructions = _build_sub_agent_instructions(state)

        # Build system prompt
        system_prompt = SUB_AGENT_SYSTEM_PROMPT.format(
            task_description=task_desc,
            task_context=context_text,
            tool_schemas=tool_schemas_text or "No tool schemas available.",
            tool_guidance=tool_guidance,
            time_context=time_ctx or "Not provided",
            agent_instructions=agent_instructions,
        )

        if not tools:
            log.warning("No tools loaded for sub-agent %s", task_id)
            return {
                **task,
                "status": "error",
                "error": "No tools available for this task",
                "duration_ms": (time.perf_counter() - start_time) * 1000,
            }

        log.info("Sub-agent %s: %d tools loaded", task_id, len(tools))

        # Create isolated agent
        from langchain.agents import create_agent

        agent = create_agent(
            state["llm"],
            tools,
            system_prompt=system_prompt,
        )

        # Build ISOLATED messages - only the task, not full conversation
        messages = [HumanMessage(content=task_desc)]

        # Create streaming callback for tool events
        streaming_cb = _SubAgentStreamingCallback(
            writer, config, log, task_id,
        )

        agent_config = {
            "recursion_limit": MAX_SUB_AGENT_RECURSION,
            "callbacks": [streaming_cb],
        }

        # Execute with timeout
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": messages}, config=agent_config),
            timeout=SUB_AGENT_TIMEOUT_SECONDS,
        )

        # Extract results
        final_messages = result.get("messages", [])
        response_text = _extract_response(final_messages, log)
        tool_results = _extract_tool_results(final_messages, state, log)

        duration_ms = (time.perf_counter() - start_time) * 1000

        success_count = sum(1 for r in tool_results if r.get("status") == "success")
        error_count = sum(1 for r in tool_results if r.get("status") == "error")
        task_status = "success" if success_count > 0 or not tool_results else "error"

        log.info(
            "Sub-agent %s: %s in %.0fms (%d tools: %d ok, %d err)",
            task_id, task_status, duration_ms, len(tool_results),
            success_count, error_count,
        )

        return {
            **task,
            "status": task_status,
            "result": {
                "response": response_text,
                "tool_results": tool_results,
                "tool_count": len(tool_results),
                "success_count": success_count,
                "error_count": error_count,
            },
            "error": None if task_status == "success" else f"{error_count} tool(s) failed",
            "duration_ms": duration_ms,
        }

    except asyncio.TimeoutError:
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.error("Sub-agent %s timed out after %.0fms", task_id, duration_ms)
        return {
            **task,
            "status": "error",
            "error": f"Task timed out after {SUB_AGENT_TIMEOUT_SECONDS}s",
            "duration_ms": duration_ms,
        }

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.error("Sub-agent %s failed: %s", task_id, e, exc_info=True)
        return {
            **task,
            "status": "error",
            "error": str(e),
            "duration_ms": duration_ms,
        }


# ---------------------------------------------------------------------------
# Result extraction helpers
# ---------------------------------------------------------------------------

def _extract_response(messages: List, log: logging.Logger) -> str:
    """Extract the final text response from agent messages."""
    # Walk backwards to find the last AIMessage
    for msg in reversed(messages):
        if hasattr(msg, "content") and not isinstance(msg, ToolMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                if text_parts:
                    return " ".join(text_parts).strip()
    return ""


def _extract_tool_results(
    messages: List,
    state: DeepAgentState,
    log: logging.Logger,
) -> List[Dict[str, Any]]:
    """Extract tool results from agent messages and process retrieval outputs."""
    tool_results = []

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue

        tool_name = msg.name if hasattr(msg, "name") else "unknown"
        result_content = msg.content

        # Process retrieval results to extract final_results
        if "retrieval" in tool_name.lower():
            try:
                from app.modules.agents.qna.nodes import _process_retrieval_output
                if isinstance(result_content, str):
                    try:
                        parsed = json.loads(result_content)
                        _process_retrieval_output(parsed, state, log)
                    except json.JSONDecodeError:
                        _process_retrieval_output(result_content, state, log)
                elif isinstance(result_content, dict):
                    _process_retrieval_output(result_content, state, log)
            except Exception as e:
                log.warning("Failed to process retrieval output: %s", e)

        # Detect status
        status = _detect_status(result_content)
        tool_results.append({
            "tool_name": tool_name,
            "status": status,
            "result": result_content,
            "tool_call_id": getattr(msg, "tool_call_id", None),
        })

    return tool_results


def _detect_status(result_content: Any) -> str:
    """Detect success/error from tool result content."""
    try:
        from app.modules.agents.qna.nodes import _detect_tool_result_status
        return _detect_tool_result_status(result_content)
    except ImportError:
        # Fallback detection
        text = str(result_content).lower()[:500]
        error_markers = ["error", "failed", "unauthorized", "forbidden", "not found"]
        return "error" if any(m in text for m in error_markers) else "success"


# ---------------------------------------------------------------------------
# Tool call budget and output truncation
# ---------------------------------------------------------------------------

class _ToolCallBudget:
    """Shared counter that limits tool calls within a single sub-agent."""

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.count = 0

    def consume(self) -> bool:
        """Increment counter. Returns True if within budget."""
        self.count += 1
        return self.count <= self.max_calls


def _wrap_tools_with_truncation(
    tools: List,
    budget: _ToolCallBudget,
    max_chars: int = _MAX_TOOL_RESULT_CHARS,
    log: logging.Logger = logger,
) -> List:
    """
    Wrap tools with output truncation and call budget to prevent context overflow.

    Each tool result is truncated to max_chars, and the total number of tool
    calls is capped by the budget. When the budget is exhausted, tools return
    a stop message instructing the LLM to produce its final answer.
    """
    from langchain_core.tools import StructuredTool as LCStructuredTool

    wrapped = []
    for tool in tools:
        orig_coro = getattr(tool, "coroutine", None)
        orig_func = getattr(tool, "func", None)
        tool_name = getattr(tool, "name", "unknown")

        truncated = _make_truncated_coro(
            orig_coro, orig_func, budget, max_chars, tool_name, log,
        )

        try:
            new_tool = LCStructuredTool.from_function(
                func=truncated,
                coroutine=truncated,
                name=tool_name,
                description=getattr(tool, "description", ""),
                args_schema=getattr(tool, "args_schema", None),
                return_direct=getattr(tool, "return_direct", False),
            )
            if hasattr(tool, "_original_name"):
                new_tool._original_name = tool._original_name
            wrapped.append(new_tool)
        except Exception as e:
            log.warning("Failed to wrap tool %s: %s, using original", tool_name, e)
            wrapped.append(tool)

    return wrapped


def _make_truncated_coro(orig_coro, orig_func, budget, max_chars, tool_name, log):
    """Factory: create a truncated async wrapper for a tool coroutine."""

    async def _coro(**kwargs):
        if not budget.consume():
            log.warning(
                "Tool call budget exhausted (%d/%d) for %s",
                budget.count, budget.max_calls, tool_name,
            )
            return (
                f"TOOL CALL BUDGET EXHAUSTED ({budget.max_calls} calls reached). "
                "You have already collected sufficient data. Provide your FINAL ANSWER "
                "now using the data from previous tool calls. Do NOT call any more tools."
            )

        result = await orig_coro(**kwargs) if orig_coro else orig_func(**kwargs)
        return _truncate_tool_output(result, max_chars)

    return _coro


def _truncate_tool_output(result: Any, max_chars: int) -> Any:
    """
    Truncate tool output while preserving complete items and URL fields.

    For lists/dicts, truncates at item boundaries so each kept item is
    intact (with all its URL fields). Falls back to character truncation
    for plain strings.
    """
    if result is None:
        return result

    # --- Plain strings ---
    if isinstance(result, str):
        if len(result) <= max_chars:
            return result
        # Try to parse as JSON for smarter truncation
        try:
            parsed = json.loads(result)
            if isinstance(parsed, (list, dict)):
                truncated = _smart_truncate_structured(parsed, max_chars)
                return json.dumps(truncated, default=str, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass
        return result[:max_chars] + (
            f"\n\n[Output truncated: {len(result):,} -> {max_chars:,} chars.]"
        )

    # --- Structured data (dict / list) ---
    if isinstance(result, (list, dict)):
        try:
            text = json.dumps(result, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(result)
        if len(text) <= max_chars:
            return result
        return _smart_truncate_structured(result, max_chars)

    text = str(result)
    if len(text) <= max_chars:
        return result
    return text[:max_chars] + (
        f"\n\n[Output truncated: {len(text):,} -> {max_chars:,} chars.]"
    )


def _smart_truncate_structured(data: Any, max_chars: int) -> Any:
    """Truncate dicts/lists at item boundaries, keeping complete items."""
    if isinstance(data, list):
        return _truncate_list_items(data, max_chars)

    if isinstance(data, dict):
        # Find the largest list value (the main payload) and truncate it
        # while preserving all scalar/metadata fields (counts, tokens, etc.)
        list_key = None
        list_len = 0
        for key, value in data.items():
            if isinstance(value, list) and len(value) > list_len:
                list_key = key
                list_len = len(value)

        if list_key and list_len > 2:
            # Budget: reserve space for non-list fields, give rest to list
            non_list_size = 0
            for key, value in data.items():
                if key != list_key:
                    try:
                        non_list_size += len(
                            json.dumps({key: value}, default=str, ensure_ascii=False)
                        )
                    except (TypeError, ValueError):
                        non_list_size += len(str(value)) + len(key) + 10
            list_budget = max(max_chars - non_list_size - 100, max_chars // 2)
            result = dict(data)
            result[list_key] = _truncate_list_items(data[list_key], list_budget)
            return result

        # No large lists — fall back to char truncation of serialized form
        try:
            text = json.dumps(data, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(data)
        return text[:max_chars] + "\n[Truncated]"

    return data


def _truncate_list_items(items: list, max_chars: int) -> list:
    """Keep complete items from a list up to the char budget."""
    if not items:
        return items

    kept: List = []
    used = 2  # opening/closing brackets
    for item in items:
        try:
            item_json = json.dumps(item, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            item_json = str(item)
        cost = len(item_json) + (2 if kept else 0)  # comma + space
        if used + cost > max_chars and kept:
            break
        kept.append(item)
        used += cost

    if len(kept) < len(items):
        kept.append(
            f"[... {len(items) - len(kept)} more items not shown. "
            f"Total: {len(items)} items. Use the items above to complete your task.]"
        )

    return kept


# ---------------------------------------------------------------------------
# Execution level builder
# ---------------------------------------------------------------------------

def _build_execution_levels(
    tasks: List[SubAgentTask],
    log: logging.Logger,
) -> List[List[SubAgentTask]]:
    """
    Organize tasks into execution levels based on dependencies.

    Level 0: Tasks with no dependencies (run in parallel)
    Level 1: Tasks depending on Level 0 tasks (run after Level 0)
    etc.

    Returns list of levels, each containing tasks that can run in parallel.
    """
    task_map = {t["task_id"]: t for t in tasks}
    resolved: set = set()
    levels: List[List[SubAgentTask]] = []

    remaining = list(tasks)
    max_depth = len(tasks) + 1  # Prevent infinite loops

    for _ in range(max_depth):
        if not remaining:
            break

        # Find tasks whose dependencies are all resolved
        current_level = []
        still_remaining = []

        for task in remaining:
            deps = set(task.get("depends_on", []))
            if deps.issubset(resolved):
                current_level.append(task)
            else:
                still_remaining.append(task)

        if not current_level:
            # Deadlock: remaining tasks have unresolvable dependencies
            log.warning(
                "Dependency deadlock: %s have unresolvable deps, forcing execution",
                [t["task_id"] for t in still_remaining],
            )
            current_level = still_remaining
            still_remaining = []

        levels.append(current_level)
        resolved.update(t["task_id"] for t in current_level)
        remaining = still_remaining

    return levels


# ---------------------------------------------------------------------------
# Agent instructions builder
# ---------------------------------------------------------------------------

def _build_sub_agent_instructions(state: DeepAgentState) -> str:
    """Build agent instructions prefix for sub-agent prompts.

    Includes the agent's configured instructions so sub-agents
    follow the same behavioral constraints and workflow rules
    as the overall agent.
    """
    parts = []

    # Agent instructions (workflow-specific behavior)
    instructions = state.get("instructions", "")
    if instructions and instructions.strip():
        parts.append(f"## Agent Instructions\n{instructions.strip()}")

    if parts:
        return "\n\n".join(parts) + "\n\n"
    return ""


# ---------------------------------------------------------------------------
# Tool guidance builder
# ---------------------------------------------------------------------------

def _build_sub_agent_tool_guidance(
    task: SubAgentTask,
    state: DeepAgentState,
) -> str:
    """Build concise tool guidance for a sub-agent based on its domains."""
    domains = {d.lower() for d in task.get("domains", [])}
    parts = []

    if "jira" in domains:
        parts.append(
            "JIRA: Add time filters to JQL (e.g., `updated >= -7d`). "
            "Use `jira.search_users` to get accountIds before JQL assignee queries. "
            "Use `maxResults=50` to get more results per call. "
            "**Links**: Each issue in search results has a `url` field with the browse link — "
            "use it as `[ISSUE-KEY: Summary](url)` for EVERY issue."
        )
    if "confluence" in domains:
        parts.append(
            "CONFLUENCE: Use `confluence.search_pages(title=...)` to find pages. "
            "Use `confluence.get_page_content(page_id=...)` for full page content. "
            "**Links**: Each page has a `url` or `_links.webui` field — "
            "use it as `[Page Title](url)` for EVERY page."
        )
    if "slack" in domains:
        parts.append(
            "SLACK: Write messages in Slack mrkdwn format (*bold*, _italic_, bullets). "
            "NEVER pass raw HTML or JSON as message content. "
            "`set_user_status` uses `duration_seconds`, not timestamps."
        )
    if "gmail" in domains:
        parts.append(
            "GMAIL: Use `max_results=50` to get more results and reduce pagination. "
            "Search results already contain subject, from, to, date, snippet — "
            "do NOT call `get_email_details` for every email. "
            "**Links**: Each email has an `id` field. Construct Gmail links as "
            "`https://mail.google.com/mail/u/0/#inbox/<id>` and include as "
            "`[Subject](https://mail.google.com/mail/u/0/#inbox/<id>)` for EVERY email."
        )
    if "outlook" in domains:
        parts.append(
            "OUTLOOK: Use `seriesMasterId` for recurring event operations. "
            "Preserve existing recurrence pattern when updating. "
            "**Links**: Each event has a `webLink` field — use it as "
            "`[Event Subject](webLink)` for EVERY event/meeting."
        )
    if "teams" in domains:
        parts.append(
            "TEAMS: Use `teams.get_meeting_transcript(event_id=..., joinUrl=...)` "
            "for transcripts. Write your own summary, never pass raw transcript."
        )
    if "calendar" in domains:
        parts.append(
            "GOOGLE CALENDAR: Use time range filters to limit results. "
            "Use `max_results` parameter to control page size."
        )

    return "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Tool schema formatter for sub-agent prompts
# ---------------------------------------------------------------------------

def _format_tools_for_prompt(tools: List, log: logging.Logger) -> str:
    """
    Format StructuredTool objects with their parameter schemas for the
    sub-agent's system prompt.

    This mirrors _format_tool_descriptions from nodes.py but is focused
    on the sub-agent's assigned tools only.
    """
    if not tools:
        return ""

    lines = []
    for tool in tools[:20]:  # Safety limit
        name = getattr(tool, "name", str(tool))
        description = getattr(tool, "description", "")

        lines.append(f"### {name}")
        if description:
            desc_text = description[:300] if len(description) > 300 else description
            lines.append(f"  {desc_text}")

        # Extract parameter schema
        try:
            schema = getattr(tool, "args_schema", None)
            if schema:
                from app.modules.agents.deep.tool_router import _extract_params
                params = _extract_params(schema)
                if params:
                    lines.append("  **Parameters:**")
                    for param_name, param_info in params.items():
                        required_marker = "**required**" if param_info.get("required") else "optional"
                        param_type = param_info.get("type", "any").upper()
                        param_desc = param_info.get("description", "")
                        if param_desc:
                            lines.append(f"  - `{param_name}` ({required_marker}): {param_desc[:100]} [{param_type}]")
                        else:
                            lines.append(f"  - `{param_name}` ({required_marker}) [{param_type}]")
        except Exception as e:
            log.debug("Could not extract schema for %s: %s", name, e)

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Streaming callback for sub-agents
# ---------------------------------------------------------------------------

class _SubAgentStreamingCallback(AsyncCallbackHandler):
    """Streams tool events from a sub-agent to the frontend."""

    def __init__(
        self,
        writer: StreamWriter,
        config: RunnableConfig,
        log: logging.Logger,
        task_id: str,
    ):
        super().__init__()
        self.writer = writer
        self.config = config
        self.log = log
        self.task_id = task_id
        self._tool_names: Dict[str, str] = {}

    def _write(self, event_data: Dict[str, Any]) -> None:
        token = var_child_runnable_config.set(self.config)
        try:
            self.writer(event_data)
        except Exception:
            pass
        finally:
            var_child_runnable_config.reset(token)

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        tool_name = serialized.get("name", kwargs.get("name", "unknown"))
        self._tool_names[str(run_id)] = tool_name
        display = tool_name.replace("_", " ").title()
        self._write({
            "event": "status",
            "data": {"status": "executing", "message": f"Executing {display}..."},
        })

    async def on_tool_end(self, output, *, run_id, **kwargs):
        tool_name = self._tool_names.pop(str(run_id), "unknown")
        status = _detect_status(output)
        self._write({
            "event": "tool_result",
            "data": {"tool": tool_name, "status": status},
        })

    async def on_tool_error(self, error, *, run_id, **kwargs):
        tool_name = self._tool_names.pop(str(run_id), "unknown")
        self._write({
            "event": "status",
            "data": {
                "status": "executing",
                "message": f"Retrying {tool_name.replace('_', ' ')}...",
            },
        })
