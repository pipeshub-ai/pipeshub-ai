"""
Sub-Agent Execution - Isolated Context Task Execution

Each sub-agent runs with an isolated context window:
- Only its specific task description
- Only its assigned tools
- Results from dependency tasks (compacted)
- A compact conversation summary (not full history)

Independent tasks run in parallel via asyncio.gather.
Dependent tasks run sequentially after their dependencies complete.

Complex tasks use a phased execution model:
  Phase 1: FETCH  — ReAct agent fetches paginated data (generous budget)
  Phase 2: SUMMARIZE — Per-batch LLM summarization (parallel, no tools)
  Phase 3: CONSOLIDATE — Merge batch summaries into domain summary
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables.config import var_child_runnable_config

from app.modules.agents.deep.context_manager import build_sub_agent_context
from app.modules.agents.deep.prompts import SUB_AGENT_SYSTEM_PROMPT
from app.modules.agents.deep.state import DeepAgentState, SubAgentTask, _opik_tracer
from app.modules.agents.deep.tool_router import get_tools_for_sub_agent
from app.modules.agents.qna.stream_utils import safe_stream_write, send_keepalive

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from uuid import UUID

    from langchain_core.runnables import RunnableConfig
    from langgraph.types import StreamWriter

logger = logging.getLogger(__name__)

# Constants — simple tasks
MAX_SUB_AGENT_RECURSION = 25
_MAX_TOOL_CALLS_PER_AGENT = 20  # Max tool calls before budget exhaustion

# Constants — retrieval tasks (allows 3-5 diverse searches + retries/refinement)
_MAX_TOOL_CALLS_RETRIEVAL = 10

# Constants — complex tasks (higher budgets for data-heavy work)
_MAX_TOOL_CALLS_COMPLEX = 35

# Display / truncation constants
_TASK_DESC_DISPLAY_LEN = 80
_TOOL_DESC_TRUNCATE_LEN = 300
_WARM_LOG_THRESHOLD_MS = 50


async def execute_sub_agents_node(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> DeepAgentState:
    """
    Execute all sub-agent tasks respecting dependencies.

    Uses event-based dependency resolution: ALL tasks launch immediately,
    each waits only for its specific dependencies to complete.
    This is faster than level-based scheduling when tasks at different
    levels have varied completion times (task C depends only on A,
    so it starts as soon as A finishes — doesn't wait for B).
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

    completed: list[SubAgentTask] = list(state.get("completed_tasks", []))

    # ------------------------------------------------------------------
    # Pre-warm API clients for all domains in parallel.
    # Without this, the first tool call for each domain blocks while
    # creating the OAuth/MSAL client. Pre-warming moves that latency
    # out of the critical path.
    # ------------------------------------------------------------------
    await _prewarm_clients(tasks, state, log)

    # ------------------------------------------------------------------
    # Event-based dependency resolution.
    # Each task gets an asyncio.Event that is set when the task finishes.
    # Dependent tasks await their dependencies' events, then execute.
    # ------------------------------------------------------------------
    task_events: dict[str, asyncio.Event] = {}

    # Already-completed tasks (from prior aggregator iterations) are done
    for t in completed:
        tid = t.get("task_id", "")
        if tid:
            evt = asyncio.Event()
            evt.set()
            task_events[tid] = evt

    # Create events for new tasks
    for t in tasks:
        task_events[t["task_id"]] = asyncio.Event()

    async def _run_when_ready(task: SubAgentTask) -> None:
        task_id = task.get("task_id", "unknown")
        try:
            # Wait for this task's specific dependencies only
            dep_ids = task.get("depends_on", [])
            if dep_ids:
                await asyncio.gather(
                    *[task_events[d].wait() for d in dep_ids if d in task_events]
                )

            result = await _execute_single_sub_agent(
                task, state, completed, config, writer, log,
            )
            completed.append(result)
        except Exception as exc:
            log.error("Sub-agent %s raised exception: %s", task_id, exc)
            completed.append({**task, "status": "error", "error": str(exc)})
        finally:
            # Always signal completion so dependents don't hang
            task_events[task_id].set()

    log.info(
        "Launching %d task(s) in parallel: [%s]",
        len(tasks), ", ".join(t["task_id"] for t in tasks),
    )

    await asyncio.gather(*[_run_when_ready(t) for t in tasks])

    state["completed_tasks"] = completed

    # Collect all tool results from ALL completed tasks across iterations
    all_tool_results = []
    for task in completed:
        task_result = task.get("result", {})
        if isinstance(task_result, dict):
            tool_results_list = task_result.get("tool_results", [])
            all_tool_results.extend(tool_results_list)

    state["all_tool_results"] = all_tool_results
    state["tool_results"] = all_tool_results

    # Collect sub-agent response analyses for respond_node
    # Prefer domain_summary for complex tasks (already consolidated and concise)
    sub_agent_analyses = []
    for task in completed:
        if task.get("status") != "success":
            continue

        task_id = task.get("task_id", "unknown")
        domains = ", ".join(task.get("domains", []))

        # Complex tasks: use the consolidated domain summary
        domain_summary = task.get("domain_summary")
        if domain_summary:
            log.debug(
                "Task %s: using domain_summary (%d chars) for analysis",
                task_id, len(domain_summary),
            )
            sub_agent_analyses.append(
                f"[{task_id} ({domains})]: {domain_summary}"
            )
            continue

        # Simple tasks: use the response text
        task_result = task.get("result", {})
        if isinstance(task_result, dict):
            response_text = task_result.get("response", "")
            if response_text:
                log.debug(
                    "Task %s: using response text (%d chars) for analysis",
                    task_id, len(response_text),
                )
                sub_agent_analyses.append(
                    f"[{task_id} ({domains})]: {response_text}"
                )
            else:
                log.warning(
                    "Task %s: success but empty response (domain_summary=%s, result keys=%s)",
                    task_id, repr(domain_summary), list(task_result.keys()),
                )

    state["sub_agent_analyses"] = sub_agent_analyses

    duration_ms = (time.perf_counter() - start_time) * 1000
    success_count = sum(1 for t in completed if t.get("status") == "success")
    error_count = sum(1 for t in completed if t.get("status") == "error")
    complex_count = sum(1 for t in completed if t.get("complexity") == "complex")
    log.info(
        "Sub-agents completed: %d success, %d errors, %d complex, %d analyses in %.0fms",
        success_count, error_count, complex_count, len(sub_agent_analyses), duration_ms,
    )

    return state


async def _execute_single_sub_agent(
    task: SubAgentTask,
    state: DeepAgentState,
    completed_tasks: list[SubAgentTask],
    config: RunnableConfig,
    writer: StreamWriter,
    log: logging.Logger,
) -> SubAgentTask:
    """
    Execute a single sub-agent with isolated context.

    Routes to complex execution for tasks marked with complexity="complex",
    otherwise uses the standard ReAct agent execution.
    """
    task_id = task.get("task_id", "unknown")
    task.get("description", "")
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

    # Route by complexity / multi-step
    complexity = task.get("complexity", "simple")
    task_domains = [d.lower() for d in task.get("domains", [])]

    # Retrieval tasks ALWAYS use simple execution — the respond node handles
    # citation pipeline (R-labels, fetch_full_record). Complex phased execution
    # would summarize the raw blocks, losing detail needed for citations.
    is_retrieval_task = any(d in ("retrieval", "knowledge") for d in task_domains)

    # Multi-step takes priority over complex: it executes sequential steps
    # where each step's result feeds the next (e.g., find space → list pages
    # → fetch content). Complex phased execution is for bulk fetch+summarize
    # with no inter-step dependencies.
    if task.get("multi_step") and task.get("sub_steps"):
        log.info("Sub-agent %s: using multi-step execution (%d steps)", task_id, len(task["sub_steps"]))
        try:
            return await _execute_multi_step_sub_agent(
                task, state, completed_tasks, config, writer, log,
            )
        except Exception as e:
            log.warning(
                "Multi-step execution failed for %s: %s — falling back to simple mode",
                task_id, e,
            )
            # Fall through to simple execution

    if complexity == "complex" and not is_retrieval_task:
        log.info("Sub-agent %s: using complex phased execution", task_id)
        try:
            return await _execute_complex_sub_agent(
                task, state, completed_tasks, config, writer, log,
            )
        except Exception as e:
            log.warning(
                "Complex execution failed for %s: %s — falling back to simple mode",
                task_id, e,
            )
            # Fall through to simple execution
    elif complexity == "complex" and is_retrieval_task:
        log.info(
            "Sub-agent %s: forcing simple execution for retrieval task "
            "(respond node handles citation pipeline)", task_id,
        )

    return await _execute_simple_sub_agent(
        task, state, completed_tasks, config, writer, log,
    )


async def _execute_simple_sub_agent(
    task: SubAgentTask,
    state: DeepAgentState,
    completed_tasks: list[SubAgentTask],
    config: RunnableConfig,
    writer: StreamWriter,
    log: logging.Logger,
) -> SubAgentTask:
    """
    Execute a simple sub-agent with standard ReAct agent.

    Uses LangChain's create_agent() with:
    - Only the tools assigned to this task
    - A focused system prompt for the specific task
    - Isolated message history (just the task + dependencies)
    """
    task_id = task.get("task_id", "unknown")
    task_desc = task.get("description", "")
    start_time = time.perf_counter()

    # Stream status
    task_display = task_desc[:_TASK_DESC_DISPLAY_LEN] + "..." if len(task_desc) > _TASK_DESC_DISPLAY_LEN else task_desc
    safe_stream_write(writer, {
        "event": "status",
        "data": {"status": "executing", "message": task_display},
    }, config)

    try:
        # Build isolated context for this sub-agent.
        # All sub-agents get recent conversation turns so they can interpret
        # follow-up queries correctly (e.g., "tell me more about each file"
        # needs context about what files were discussed previously).
        context_text = build_sub_agent_context(
            task=task,
            completed_tasks=completed_tasks,
            conversation_summary=state.get("conversation_summary"),
            query=state.get("query", ""),
            log=log,
            recent_conversations=state.get("previous_conversations", [])[-3:],
        )

        # Get filtered tools for this sub-agent (StructuredTools with args_schema)
        tools = get_tools_for_sub_agent(task.get("tools", []), state)

        # Wrap tools with call budget to prevent runaway tool loops
        is_retrieval = any(d in ("retrieval", "knowledge") for d in task.get("domains", []))
        max_calls = _MAX_TOOL_CALLS_RETRIEVAL if is_retrieval else _MAX_TOOL_CALLS_PER_AGENT
        budget = _ToolCallBudget(max_calls)
        tools = _wrap_tools_with_budget(tools, budget, log)

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

        callbacks = [streaming_cb]
        if _opik_tracer:
            callbacks.append(_opik_tracer)
        agent_config = {
            "recursion_limit": MAX_SUB_AGENT_RECURSION,
            "callbacks": callbacks,
        }

        # Execute — no wall-clock timeout for deep agent; tool call budget
        # (_ToolCallBudget) stops the agent after _MAX_TOOL_CALLS_PER_AGENT calls.
        # Keepalive prevents proxy/nginx from closing the SSE connection during
        # long-running API calls.
        keepalive_task = asyncio.create_task(
            send_keepalive(writer, config, task_display)
        )
        try:
            result = await agent.ainvoke({"messages": messages}, config=agent_config)
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task

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
# Complex sub-agent execution (phased: fetch → summarize → consolidate)
# ---------------------------------------------------------------------------



async def _execute_complex_sub_agent(
    task: SubAgentTask,
    state: DeepAgentState,
    completed_tasks: list[SubAgentTask],
    config: RunnableConfig,
    writer: StreamWriter,
    log: logging.Logger,
) -> SubAgentTask:
    """
    Execute a complex sub-agent with phased execution for high-volume data tasks.

    Phase 1: FETCH — ReAct agent fetches paginated data with generous budget
    Phase 2: SUMMARIZE — Per-batch LLM summarization (parallel, no tools)
    Phase 3: CONSOLIDATE — Merge batch summaries into domain summary

    This produces a concise domain_summary instead of raw tool results,
    dramatically reducing context for the respond node.
    """
    from app.modules.agents.deep.context_manager import (
        consolidate_batch_summaries,
        group_tool_results_into_batches,
        summarize_batch,
    )

    task_id = task.get("task_id", "unknown")
    task_desc = task.get("description", "")
    domains = task.get("domains", [])
    domain_name = domains[0] if domains else "unknown"
    start_time = time.perf_counter()

    # Stream status
    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "executing",
            "message": f"Fetching {domain_name} data...",
        },
    }, config)

    # =========================================================================
    # Phase 1: FETCH — Run ReAct agent with generous budget to gather raw data
    # =========================================================================
    log.info("Phase 1 (FETCH): sub-agent %s starting data collection", task_id)

    context_text = build_sub_agent_context(
        task=task,
        completed_tasks=completed_tasks,
        conversation_summary=state.get("conversation_summary"),
        query=state.get("query", ""),
        log=log,
        recent_conversations=state.get("previous_conversations", [])[-3:],
    )

    tools = get_tools_for_sub_agent(task.get("tools", []), state)

    # Higher budgets for complex tasks
    budget = _ToolCallBudget(_MAX_TOOL_CALLS_COMPLEX)
    tools = _wrap_tools_with_budget(tools, budget, log)

    tool_schemas_text = _format_tools_for_prompt(tools, log)
    tool_guidance = _build_sub_agent_tool_guidance(task, state)

    # Build time context
    time_ctx = ""
    current_time = state.get("current_time")
    timezone = state.get("timezone")
    if current_time:
        time_ctx += f"Current time: {current_time}"
    if timezone:
        time_ctx += f"\nTimezone: {timezone}"

    agent_instructions = _build_sub_agent_instructions(state)

    # Augment task description with batch strategy hints
    augmented_desc = task_desc
    batch_strategy = task.get("batch_strategy")
    if batch_strategy:
        hints = []
        if batch_strategy.get("page_size"):
            hints.append(f"Use page_size/max_results={batch_strategy['page_size']}")
        if batch_strategy.get("max_pages"):
            hints.append(f"Fetch up to {batch_strategy['max_pages']} pages")
        if batch_strategy.get("scope_query"):
            hints.append(f"Search/filter query: {batch_strategy['scope_query']}")
        if hints:
            augmented_desc += "\n\nExecution hints: " + ". ".join(hints) + "."

    system_prompt = SUB_AGENT_SYSTEM_PROMPT.format(
        task_description=augmented_desc,
        task_context=context_text,
        tool_schemas=tool_schemas_text or "No tool schemas available.",
        tool_guidance=tool_guidance,
        time_context=time_ctx or "Not provided",
        agent_instructions=agent_instructions,
    )

    if not tools:
        log.warning("No tools loaded for complex sub-agent %s", task_id)
        return {
            **task,
            "status": "error",
            "error": "No tools available for this task",
            "duration_ms": (time.perf_counter() - start_time) * 1000,
        }

    log.info("Complex sub-agent %s: %d tools loaded (budget=%d)", task_id, len(tools), _MAX_TOOL_CALLS_COMPLEX)

    # Create and run the ReAct agent for data fetching
    from langchain.agents import create_agent

    agent = create_agent(
        state["llm"],
        tools,
        system_prompt=system_prompt,
    )

    messages = [HumanMessage(content=augmented_desc)]

    streaming_cb = _SubAgentStreamingCallback(writer, config, log, task_id)

    complex_callbacks = [streaming_cb]
    if _opik_tracer:
        complex_callbacks.append(_opik_tracer)
    agent_config = {
        "recursion_limit": MAX_SUB_AGENT_RECURSION,
        "callbacks": complex_callbacks,
    }

    # Execute — no wall-clock timeout for deep agent; tool call budget
    # (_ToolCallBudget) stops the agent after _MAX_TOOL_CALLS_COMPLEX calls.
    # Keepalive prevents proxy/nginx from closing the SSE connection during
    # long-running API calls (Phase 1 can run 60-180+ seconds).
    keepalive_task = asyncio.create_task(
        send_keepalive(writer, config, f"Fetching {domain_name} data...")
    )
    try:
        result = await agent.ainvoke({"messages": messages}, config=agent_config)
    finally:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task

    final_messages = result.get("messages", [])
    tool_results = _extract_tool_results(final_messages, state, log)

    success_count = sum(1 for r in tool_results if r.get("status") == "success")
    if success_count == 0:
        # No successful tool calls — return as error
        duration_ms = (time.perf_counter() - start_time) * 1000
        error_count = sum(1 for r in tool_results if r.get("status") == "error")
        log.warning("Complex sub-agent %s: all %d tool calls failed", task_id, error_count)
        return {
            **task,
            "status": "error",
            "result": {
                "response": _extract_response(final_messages, log),
                "tool_results": tool_results,
                "tool_count": len(tool_results),
                "success_count": 0,
                "error_count": error_count,
            },
            "error": f"All {error_count} tool call(s) failed",
            "duration_ms": duration_ms,
        }

    fetch_duration = (time.perf_counter() - start_time) * 1000
    log.info(
        "Phase 1 (FETCH) complete for %s: %d tool calls (%d ok) in %.0fms",
        task_id, len(tool_results), success_count, fetch_duration,
    )

    # =========================================================================
    # Phase 2: SUMMARIZE — Batch-summarize tool results in parallel
    # =========================================================================
    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "executing",
            "message": f"Summarizing {domain_name} data...",
        },
    }, config)

    log.info("Phase 2 (SUMMARIZE): batching and summarizing results for %s", task_id)

    batches = group_tool_results_into_batches(final_messages)

    if not batches:
        # No tool results to summarize — use the agent's response directly
        response_text = _extract_response(final_messages, log)
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info("Complex sub-agent %s: no batches to summarize, using agent response", task_id)
        return {
            **task,
            "status": "success",
            "result": {
                "response": response_text,
                "tool_results": tool_results,
                "tool_count": len(tool_results),
                "success_count": success_count,
                "error_count": len(tool_results) - success_count,
            },
            "error": None,
            "duration_ms": duration_ms,
        }

    # Infer data type from domain for the summarization prompt
    # Use the domain name directly — no hardcoded mapping needed.
    # The LLM understands domain names (gmail, jira, outlook, etc.) without translation.
    data_type = domain_name

    llm = state.get("llm")
    total_batches = len(batches)

    # Summarize all batches in parallel
    summarize_coros = [
        summarize_batch(
            batch_text=batch,
            batch_number=i + 1,
            total_batches=total_batches,
            data_type=data_type,
            llm=llm,
            log=log,
        )
        for i, batch in enumerate(batches)
    ]

    keepalive_task = asyncio.create_task(
        send_keepalive(writer, config, f"Summarizing {domain_name} data...")
    )
    try:
        batch_summaries = await asyncio.gather(*summarize_coros, return_exceptions=True)
    finally:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task

    # Filter out failures
    valid_summaries = []
    for i, summary in enumerate(batch_summaries):
        if isinstance(summary, Exception):
            log.warning("Batch %d/%d summarization raised exception: %s", i + 1, total_batches, summary)
            valid_summaries.append(json.dumps({
                "item_count": 0,
                "error": str(summary)[:200],
            }))
        else:
            valid_summaries.append(summary)

    summarize_duration = (time.perf_counter() - start_time) * 1000 - fetch_duration
    log.info(
        "Phase 2 (SUMMARIZE) complete for %s: %d/%d batches in %.0fms",
        task_id, len(valid_summaries), total_batches, summarize_duration,
    )

    # =========================================================================
    # Phase 3: CONSOLIDATE — Merge batch summaries into domain summary
    # =========================================================================
    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "executing",
            "message": f"Consolidating {domain_name} summary...",
        },
    }, config)

    log.info("Phase 3 (CONSOLIDATE): merging batch summaries for %s", task_id)

    keepalive_task = asyncio.create_task(
        send_keepalive(writer, config, f"Consolidating {domain_name} summary...")
    )
    try:
        domain_summary = await consolidate_batch_summaries(
            batch_summaries=valid_summaries,
            domain=domain_name,
            task_description=task_desc,
            time_context=time_ctx,
            llm=llm,
            log=log,
        )
    finally:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task

    duration_ms = (time.perf_counter() - start_time) * 1000
    consolidate_duration = duration_ms - fetch_duration - summarize_duration

    log.info(
        "Complex sub-agent %s: completed in %.0fms (fetch=%.0fms, summarize=%.0fms, consolidate=%.0fms)",
        task_id, duration_ms, fetch_duration, summarize_duration, consolidate_duration,
    )

    return {
        **task,
        "status": "success",
        "result": {
            "response": domain_summary,
            "tool_results": tool_results,  # Keep raw results for link extraction in respond node
            "tool_count": len(tool_results),
            "success_count": success_count,
            "error_count": len(tool_results) - success_count,
        },
        "domain_summary": domain_summary,
        "batch_summaries": valid_summaries,
        "error": None,
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# Multi-step sub-agent execution (3-level hierarchy)
# ---------------------------------------------------------------------------

_MAX_TOOL_CALLS_PER_STEP = 10  # per sub-sub-agent step


async def _execute_multi_step_sub_agent(
    task: SubAgentTask,
    state: DeepAgentState,
    completed_tasks: list[SubAgentTask],
    config: RunnableConfig,
    writer: StreamWriter,
    log: logging.Logger,
) -> SubAgentTask:
    """
    Execute a multi-step sub-agent (3-level hierarchy).

    Acts as a mini-orchestrator: executes each step sequentially,
    passing results from earlier steps as context to later ones.
    Each step is a sub-sub-agent with the same tools.

    Level 1: Orchestrator (created the task plan)
    Level 2: This function (mini-orchestrator for the task)
    Level 3: Individual step executors (sub-sub-agents)
    """
    from app.modules.agents.deep.prompts import MINI_ORCHESTRATOR_PROMPT

    task_id = task.get("task_id", "unknown")
    task_desc = task.get("description", "")
    sub_steps = task.get("sub_steps", [])
    start_time = time.perf_counter()

    log.info("Multi-step sub-agent %s: %d steps planned", task_id, len(sub_steps))

    # Build context and tools (shared across all steps)
    context_text = build_sub_agent_context(
        task=task,
        completed_tasks=completed_tasks,
        conversation_summary=state.get("conversation_summary"),
        query=state.get("query", ""),
        log=log,
        recent_conversations=state.get("previous_conversations", [])[-3:],
    )

    tools = get_tools_for_sub_agent(task.get("tools", []), state)
    if not tools:
        log.warning("No tools for multi-step sub-agent %s", task_id)
        return {
            **task,
            "status": "error",
            "error": "No tools available for this task",
            "duration_ms": (time.perf_counter() - start_time) * 1000,
        }

    tool_schemas_text = _format_tools_for_prompt(tools, log)
    tool_guidance = _build_sub_agent_tool_guidance(task, state)
    agent_instructions = _build_sub_agent_instructions(state)

    time_ctx = ""
    current_time = state.get("current_time")
    timezone = state.get("timezone")
    if current_time:
        time_ctx += f"Current time: {current_time}"
    if timezone:
        time_ctx += f"\nTimezone: {timezone}"

    # Execute each step sequentially, accumulating results
    all_tool_results = []
    step_results = []  # collected step response texts

    for step_idx, step_desc in enumerate(sub_steps):
        step_num = step_idx + 1
        step_label = f"{task_id}/step_{step_num}"

        safe_stream_write(writer, {
            "event": "status",
            "data": {
                "status": "executing",
                "message": f"Step {step_num}/{len(sub_steps)}: {step_desc[:80]}",
            },
        }, config)

        log.info("Multi-step %s: executing step %d/%d — %s",
                 task_id, step_num, len(sub_steps), step_desc[:100])

        # Build step context including results from previous steps
        step_context = context_text
        if step_results:
            prev_results_text = "\n\n".join(
                f"### Step {i+1} Result\n{r}" for i, r in enumerate(step_results)
            )
            step_context += f"\n\n## Results from Previous Steps\n{prev_results_text}"

        # Build system prompt for this step
        steps_text = "\n".join(
            f"{'→ ' if i == step_idx else '  '}{i+1}. {s}"
            for i, s in enumerate(sub_steps)
        )

        system_prompt = MINI_ORCHESTRATOR_PROMPT.format(
            task_description=task_desc,
            sub_steps=steps_text,
            tool_schemas=tool_schemas_text or "No tool schemas available.",
            task_context=step_context,
            time_context=time_ctx or "Not provided",
            tool_guidance=tool_guidance,
            agent_instructions=agent_instructions,
        )

        # Wrap tools with budget for this step
        budget = _ToolCallBudget(_MAX_TOOL_CALLS_PER_STEP)
        step_tools = _wrap_tools_with_budget(tools, budget, log)

        try:
            from langchain.agents import create_agent

            agent = create_agent(
                state["llm"],
                step_tools,
                system_prompt=system_prompt,
            )

            step_message = f"Execute step {step_num}: {step_desc}"
            messages = [HumanMessage(content=step_message)]

            streaming_cb = _SubAgentStreamingCallback(
                writer, config, log, step_label,
            )
            callbacks = [streaming_cb]
            if _opik_tracer:
                callbacks.append(_opik_tracer)

            agent_config = {
                "recursion_limit": MAX_SUB_AGENT_RECURSION,
                "callbacks": callbacks,
            }

            # No wall-clock timeout — budget per step limits tool calls.
            # Keepalive prevents proxy timeout during each step's execution.
            keepalive_task = asyncio.create_task(
                send_keepalive(
                    writer, config,
                    f"Step {step_num}/{len(sub_steps)}: {step_desc[:80]}",
                )
            )
            try:
                result = await agent.ainvoke({"messages": messages}, config=agent_config)
            finally:
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task

            final_messages = result.get("messages", [])
            response_text = _extract_response(final_messages, log)
            step_tool_results = _extract_tool_results(final_messages, state, log)

            step_results.append(response_text)
            all_tool_results.extend(step_tool_results)

            step_ok = sum(1 for r in step_tool_results if r.get("status") == "success")
            step_err = sum(1 for r in step_tool_results if r.get("status") == "error")
            log.info(
                "Multi-step %s step %d: done (%d tools: %d ok, %d err)",
                task_id, step_num, len(step_tool_results), step_ok, step_err,
            )

        except Exception as e:
            log.error("Multi-step %s step %d failed: %s", task_id, step_num, e, exc_info=True)
            step_results.append(f"Step {step_num} failed: {e}")

    # Combine all step results into the final response
    combined_response = "\n\n---\n\n".join(
        f"**Step {i+1}**: {sub_steps[i]}\n\n{r}"
        for i, r in enumerate(step_results)
    )

    duration_ms = (time.perf_counter() - start_time) * 1000
    success_count = sum(1 for r in all_tool_results if r.get("status") == "success")
    error_count = sum(1 for r in all_tool_results if r.get("status") == "error")
    task_status = "success" if success_count > 0 or not all_tool_results else "error"

    log.info(
        "Multi-step sub-agent %s: %s in %.0fms (%d steps, %d tools: %d ok, %d err)",
        task_id, task_status, duration_ms, len(sub_steps),
        len(all_tool_results), success_count, error_count,
    )

    return {
        **task,
        "status": task_status,
        "result": {
            "response": combined_response,
            "tool_results": all_tool_results,
            "tool_count": len(all_tool_results),
            "success_count": success_count,
            "error_count": error_count,
        },
        "error": None if task_status == "success" else f"{error_count} tool(s) failed across steps",
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# Result extraction helpers
# ---------------------------------------------------------------------------

def _extract_response(messages: list, log: logging.Logger) -> str:
    """Extract the final text response from agent messages.

    Falls back to summarizing tool results if no final text AIMessage exists,
    which happens when the ReAct agent stops after tool calls without
    producing a concluding message.
    """
    # Walk backwards to find the last AIMessage with text content
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            continue
        if not hasattr(msg, "content"):
            continue
        # Skip HumanMessage (the task prompt) — only want AIMessage responses
        if isinstance(msg, HumanMessage):
            continue

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
                joined = " ".join(text_parts).strip()
                if joined:
                    return joined

    # Fallback: no final text AIMessage found (agent ended after tool calls).
    # Build a summary from tool results so sub_agent_analyses isn't empty.
    # Include ALL data — the sub-agent analysis is the primary data source
    # for the respond node, so nothing should be truncated here.
    tool_summaries = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = msg.name if hasattr(msg, "name") else "unknown"
        content = msg.content
        if isinstance(content, str):
            result_text = content
        elif isinstance(content, (dict, list)):
            try:
                result_text = json.dumps(content, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                result_text = str(content)
        else:
            result_text = str(content)
        tool_summaries.append(f"[{tool_name}]: {result_text}")

    if tool_summaries:
        log.warning("No final AI response found, building from %d tool results", len(tool_summaries))
        return "\n\n".join(tool_summaries)

    return ""


def _extract_tool_results(
    messages: list,
    state: DeepAgentState,
    log: logging.Logger,
) -> list[dict[str, Any]]:
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


def _detect_status(result_content: object) -> str:
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
# Tool call budget
# ---------------------------------------------------------------------------

class _ToolCallBudget:
    """Shared counter that limits tool calls within a single sub-agent."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.count = 0

    def consume(self) -> bool:
        """Increment counter. Returns True if within budget."""
        self.count += 1
        return self.count <= self.max_calls


def _wrap_tools_with_budget(
    tools: list,
    budget: _ToolCallBudget,
    log: logging.Logger = logger,
) -> list:
    """
    Wrap tools with a call budget to prevent runaway tool loops.

    The total number of tool calls is capped by the budget. When the budget
    is exhausted, tools return a stop message instructing the LLM to produce
    its final answer. Tool results are returned in full — no truncation.
    Sub-agents already summarize data, so truncation at the tool level loses
    critical data (items, URLs, IDs).
    """
    from langchain_core.tools import StructuredTool as LCStructuredTool

    wrapped = []
    for tool in tools:
        orig_coro = getattr(tool, "coroutine", None)
        orig_func = getattr(tool, "func", None)
        tool_name = getattr(tool, "name", "unknown")

        budgeted = _make_budgeted_coro(
            orig_coro, orig_func, budget, tool_name, log,
        )

        try:
            new_tool = LCStructuredTool.from_function(
                func=budgeted,
                coroutine=budgeted,
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


def _make_budgeted_coro(
    orig_coro: Callable[..., Coroutine[Any, Any, str]] | None,
    orig_func: Callable[..., str] | None,
    budget: _ToolCallBudget,
    tool_name: str,
    log: logging.Logger,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Factory: create a budget-enforced async wrapper for a tool coroutine."""

    async def _coro(**kwargs: object) -> str:
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

        return await orig_coro(**kwargs) if orig_coro else orig_func(**kwargs)

    return _coro


# ---------------------------------------------------------------------------
# Client pre-warming
# ---------------------------------------------------------------------------

async def _prewarm_clients(
    tasks: list[SubAgentTask],
    state: DeepAgentState,
    log: logging.Logger,
) -> None:
    """
    Pre-create and cache API clients for all domains before sub-agents start.

    Without this, the first tool call in each domain blocks while creating
    the OAuth/MSAL client (ETCD lookup + token refresh + API discovery).
    Pre-warming moves that latency out of the critical path so sub-agents
    start with warm caches and hit zero client-creation delays.
    """
    from app.agents.tools.factories.registry import ClientFactoryRegistry
    from app.agents.tools.wrapper import ToolInstanceCreator

    # Collect one representative tool per (domain, toolset_id) pair
    tool_to_toolset_map = state.get("tool_to_toolset_map", {})
    seen: dict[tuple, str] = {}  # (app_name, toolset_id) -> tool_full_name
    for task in tasks:
        for tool_name in task.get("tools", []):
            app_name = tool_name.split(".")[0] if "." in tool_name else tool_name.split("_")[0]
            toolset_id = tool_to_toolset_map.get(tool_name, "default")
            key = (app_name, toolset_id)
            if key not in seen:
                seen[key] = tool_name

    if not seen:
        return

    creator = ToolInstanceCreator(state)

    async def _warm_one(app_name: str, tool_full_name: str) -> None:
        try:
            factory = ClientFactoryRegistry.get_factory(app_name)
            if not factory:
                return

            toolset_config = creator._get_toolset_config(tool_full_name)
            config = toolset_config if toolset_config else {}

            toolset_id = tool_to_toolset_map.get(tool_full_name)
            user_id = state.get("user_id", "default")
            cache_key = (app_name, toolset_id or "default", user_id)

            if creator._client_cache.get(cache_key) is not None:
                return

            # Use the same lock as _create_with_factory_async
            if cache_key not in creator._cache_locks:
                creator._cache_locks[cache_key] = asyncio.Lock()
            async with creator._cache_locks[cache_key]:
                if creator._client_cache.get(cache_key) is not None:
                    return
                client = await factory.create_client(
                    creator.config_service, log, config, state,
                )
                creator._client_cache[cache_key] = client
                log.debug("Pre-warmed client for %s (toolset: %s)", app_name, toolset_id)
        except Exception as e:
            log.debug("Client pre-warm skipped for %s: %s", app_name, e)

    warm_start = time.perf_counter()
    await asyncio.gather(
        *[_warm_one(app, tool) for (app, _), tool in seen.items()],
        return_exceptions=True,
    )
    warm_ms = (time.perf_counter() - warm_start) * 1000
    if warm_ms > _WARM_LOG_THRESHOLD_MS:
        log.info("Pre-warmed %d API client(s) in %.0fms", len(seen), warm_ms)


# ---------------------------------------------------------------------------
# Agent instructions builder
# ---------------------------------------------------------------------------

def _build_sub_agent_instructions(state: DeepAgentState) -> str:
    """Build agent instructions prefix for sub-agent prompts.

    Includes the agent's configured instructions and current user context
    so sub-agents follow the same behavioral constraints and know who
    the current user is (critical for "my" / "me" queries).
    """
    parts = []

    # Agent instructions (workflow-specific behavior)
    instructions = state.get("instructions", "")
    if instructions and instructions.strip():
        parts.append(f"## Agent Instructions\n{instructions.strip()}")

    # Current user context — sub-agents need this to resolve "my space",
    # "my tickets", "assigned to me", etc. Without it, the LLM guesses
    # based on token ownership or the first result, which is often wrong.
    user_info = state.get("user_info", {})
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
        or (
            f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
            if user_info.get("firstName") or user_info.get("lastName")
            else ""
        )
    )
    if user_name or user_email:
        user_parts = ["## Current User"]
        if user_name:
            user_parts.append(f"- Name: {user_name}")
        if user_email:
            user_parts.append(f"- Email: {user_email}")
        user_parts.append(
            'When the query says "my", "me", or "I", it refers to this user.'
        )
        parts.append("\n".join(user_parts))

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
    """
    Build generic tool guidance for a sub-agent from its assigned tools.

    This is app-agnostic — it reads tool names and descriptions dynamically
    rather than hardcoding per-domain guidance. Works for any app/service.
    """
    domains = {d.lower() for d in task.get("domains", [])}
    tool_names = task.get("tools", [])

    parts = []

    # Generic guidance for all sub-agents
    parts.append(
        "## Tool Usage Guidance\n"
        "- Use the LARGEST supported page size (e.g., `max_results=50`, `maxResults=100`, `limit=50`) "
        "to minimize API calls.\n"
        "- Prefer bulk search/list operations over individual item lookups. "
        "Search results usually contain enough fields (subject, from, date, status, snippet) "
        "— avoid fetching individual item details unless the full body/content is specifically needed.\n"
        "- Use time-range filters, status filters, and search queries to scope results precisely.\n"
        "- If results include a `nextPageToken` or pagination indicator, fetch additional pages "
        "only if the task requires comprehensive data (reports, summaries)."
    )

    # Retrieval-specific guidance — maximise coverage via diverse queries
    is_retrieval = any(d in ("retrieval", "knowledge") for d in domains)
    if is_retrieval:
        parts.append(
            "\n## Knowledge Base Search Strategy\n"
            "Your goal is to retrieve the MOST COMPREHENSIVE set of relevant information.\n"
            "1. **Derive search queries from the TASK DESCRIPTION**, not the raw user message. "
            "The task description contains the resolved topic.\n"
            "2. **Make 3-5 diverse search calls** with DIFFERENT query formulations:\n"
            "   - First: a broad semantic query capturing the main topic\n"
            "   - Then: rephrase using synonyms, related terms, or different angles\n"
            "   - Then: targeted queries for specific sub-topics or details\n"
            "3. **Use limit=100** on each call to maximise results per query.\n"
            "4. **You have a hard budget of 5 search calls.** The retrieval system "
            "returns ALL matching blocks per query, so additional queries with similar "
            "terms will return the same results. Quality of query diversity matters "
            "more than quantity of calls.\n"
            "5. The retrieval results will be processed downstream for citations — "
            "your job is to surface as much relevant content as possible."
        )

    # Generic link extraction guidance (for non-retrieval tasks)
    if not is_retrieval:
        parts.append(
            "\n## Link Extraction (MANDATORY)\n"
            "For EVERY item in your results, you MUST include a clickable link.\n"
            "1. **Scan ALL result fields** for URLs — common field names: "
            "`url`, `webLink`, `webViewLink`, `htmlUrl`, `permalink`, `link`, `href`, "
            "`self`, `joinUrl`, `joinWebUrl`, `htmlLink`, `alternateLink`.\n"
            "2. **If a direct URL field exists**, use it: `[Item Title](url_value)`\n"
            "3. **If only an ID is available**, check if the tool description mentions "
            "a URL pattern. Many services follow `https://<service-domain>/<path>/<id>`.\n"
            "4. **If no URL can be determined**, include the item ID prominently so "
            "the user can find it manually."
        )

    # List available tools for reference
    if tool_names:
        tool_list = ", ".join(f"`{t}`" for t in tool_names[:15])
        parts.append(f"\n## Available Tools\n{tool_list}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool schema formatter for sub-agent prompts
# ---------------------------------------------------------------------------

def _format_tools_for_prompt(tools: list, log: logging.Logger) -> str:
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
            desc_text = description
            lines.append(f"  {desc_text}")

        # Extract parameter schema
        try:
            schema = getattr(tool, "args_schema", None)
            if schema:
                from app.modules.agents.deep.tool_router import _extract_params
                params = _extract_params(schema)
                if params:
                    lines.append("")
                    lines.append("  **Parameters:**")
                    for param_name, param_info in params.items():
                        required_marker = "**required**" if param_info.get("required") else "optional"
                        param_type = param_info.get("type", "any").upper()
                        param_desc = param_info.get("description", "")
                        if param_desc:
                            lines.append(f"  - `{param_name}` ({required_marker}): {param_desc} [{param_type}]")
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
    ) -> None:
        super().__init__()
        self.writer = writer
        self.config = config
        self.log = log
        self.task_id = task_id
        self._tool_names: dict[str, str] = {}
        self.collected_results: list[dict[str, Any]] = []

    def _write(self, event_data: dict[str, Any]) -> None:
        token = var_child_runnable_config.set(self.config)
        try:
            self.writer(event_data)
        except Exception:
            pass
        finally:
            var_child_runnable_config.reset(token)

    async def on_tool_start(self, serialized: dict[str, Any], input_str: str, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = serialized.get("name", kwargs.get("name", "unknown"))
        self._tool_names[str(run_id)] = tool_name
        display = tool_name.replace("_", " ").title()
        self._write({
            "event": "status",
            "data": {"status": "executing", "message": f"Executing {display}..."},
        })

    async def on_tool_end(self, output: object, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = self._tool_names.pop(str(run_id), "unknown")
        status = _detect_status(output)
        # Collect tool results for partial recovery on timeout
        self.collected_results.append({
            "tool_name": tool_name,
            "output": output,
            "status": status,
        })
        self._write({
            "event": "tool_result",
            "data": {"tool": tool_name, "status": status},
        })

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: object) -> None:
        tool_name = self._tool_names.pop(str(run_id), "unknown")
        self._write({
            "event": "status",
            "data": {
                "status": "executing",
                "message": f"Retrying {tool_name.replace('_', ' ')}...",
            },
        })
