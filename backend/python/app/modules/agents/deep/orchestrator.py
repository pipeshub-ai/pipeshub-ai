"""
Orchestrator Node - Task Decomposition and Dispatch

The brain of the deep agent system. Analyzes the user query,
decomposes it into focused sub-tasks, and manages execution flow.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.deep.context_manager import (
    compact_conversation_history_async,
)
from app.modules.agents.deep.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from app.modules.agents.deep.state import DeepAgentState, SubAgentTask
from app.modules.agents.deep.tool_router import (
    build_domain_description,
    group_tools_by_domain,
)
from app.modules.agents.qna.stream_utils import safe_stream_write

logger = logging.getLogger(__name__)


async def orchestrator_node(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> DeepAgentState:
    """
    Orchestrator node: decomposes query into sub-tasks.

    Flow:
    1. Compact conversation history (prevent context bloat)
    2. Group available tools by domain
    3. Ask LLM to decompose query into focused sub-tasks
    4. Assign tools to each sub-task
    5. Store plan in state for execute_sub_agents_node

    Simple queries (greetings, factual) -> direct answer (skip sub-agents)
    Single-domain queries -> single sub-agent task
    Multi-domain/complex queries -> multiple sub-agent tasks with dependencies
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    llm = state.get("llm")
    query = state.get("query", "")
    iteration = state.get("deep_iteration_count", 0)

    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "planning",
            "message": "Analyzing your request and planning actions..."
            if iteration == 0
            else f"Planning next steps (step {iteration + 1})..."
        },
    }, config)

    try:
        # Step 1: Compact conversation history
        previous = state.get("previous_conversations", [])
        summary, recent_convs = await compact_conversation_history_async(
            previous, llm, log,
        )
        if summary:
            state["conversation_summary"] = summary
            log.info("Compacted %d conversations into summary", len(previous) - len(recent_convs))

        # Step 2: Group tools by domain (also captures tool descriptions)
        tool_groups = group_tools_by_domain(state)
        domain_desc = build_domain_description(tool_groups, state)

        # Step 3: Build orchestrator prompt
        knowledge_context = _build_knowledge_context(state, log)
        tool_guidance = _build_tool_guidance(state)
        agent_instructions = _build_agent_instructions(state)

        system_prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(
            tool_domains=domain_desc,
            knowledge_context=knowledge_context,
            tool_guidance=tool_guidance,
            agent_instructions=agent_instructions,
        )

        # Build messages
        messages = [SystemMessage(content=system_prompt)]

        # Add conversation summary as context
        if summary:
            messages.append(HumanMessage(content=f"[Conversation context]\n{summary}"))

        # Add continue/retry context from previous iterations
        if iteration > 0:
            continue_ctx = _build_iteration_context(state, log)
            if continue_ctx:
                messages.append(HumanMessage(content=continue_ctx))

        # Add current query
        user_content = query
        time_ctx = _build_time_context(state)
        if time_ctx:
            user_content += f"\n\n{time_ctx}"

        messages.append(HumanMessage(content=user_content))

        # Step 4: Get plan from LLM
        log.info("Requesting task plan from LLM...")
        response = await llm.ainvoke(messages)
        plan = _parse_orchestrator_response(
            response.content if hasattr(response, "content") else str(response),
            log,
        )

        # Stream the orchestrator's reasoning to the user
        reasoning = plan.get("reasoning", "")
        if reasoning:
            safe_stream_write(writer, {
                "event": "status",
                "data": {
                    "status": "planning",
                    "message": reasoning[:200],
                },
            }, config)

        # Step 5: Handle direct answer
        if plan.get("can_answer_directly"):
            state["task_plan"] = plan
            state["sub_agent_tasks"] = []
            state["execution_plan"] = {"can_answer_directly": True}
            state["reflection_decision"] = "respond_success"
            log.info("Orchestrator: direct answer (no tools needed)")

            duration_ms = (time.perf_counter() - start_time) * 1000
            log.info(f"Orchestrator completed in {duration_ms:.0f}ms")
            return state

        # Step 6: Validate and normalize tasks
        raw_tasks = plan.get("tasks", [])
        normalized_tasks = _normalize_tasks(raw_tasks, tool_groups, log)

        # Step 7: Build sub-agent tasks from plan
        from app.modules.agents.deep.tool_router import assign_tools_to_tasks

        tasks: List[SubAgentTask] = []
        for task_spec in normalized_tasks:
            task: SubAgentTask = {
                "task_id": task_spec.get("task_id", f"task_{len(tasks) + 1}"),
                "description": task_spec.get("description", ""),
                "domains": task_spec.get("domains", []),
                "depends_on": task_spec.get("depends_on", []),
                "status": "pending",
                "tools": [],
                "result": None,
                "error": None,
                "duration_ms": None,
            }
            tasks.append(task)

        # Assign tools to tasks
        tasks = assign_tools_to_tasks(tasks, tool_groups, state)

        # Validate: skip tasks with no tools assigned (unless they're knowledge tasks)
        valid_tasks = []
        for task in tasks:
            if task["tools"] or any(d.lower() in ("retrieval", "knowledge") for d in task.get("domains", [])):
                valid_tasks.append(task)
            else:
                log.warning(
                    "Skipping task %s: no tools for domains %s",
                    task["task_id"],
                    task.get("domains"),
                )

        state["task_plan"] = plan
        state["sub_agent_tasks"] = valid_tasks
        state["execution_plan"] = {"can_answer_directly": False}

        # Stream task plan summary to user
        if valid_tasks:
            task_summaries = []
            for t in valid_tasks:
                domains = ", ".join(t.get("domains", []))
                desc = t.get("description", "")[:100]
                task_summaries.append(f"{domains}: {desc}")
            plan_msg = f"Plan: {len(valid_tasks)} task(s) — " + "; ".join(task_summaries)
            safe_stream_write(writer, {
                "event": "status",
                "data": {"status": "planning", "message": plan_msg[:300]},
            }, config)

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(
            "Orchestrator: %d tasks planned in %.0fms (domains: %s)",
            len(valid_tasks),
            duration_ms,
            [t.get("domains", []) for t in valid_tasks],
        )

    except Exception as e:
        log.error(f"Orchestrator error: {e}", exc_info=True)
        state["error"] = {
            "status": "error",
            "message": f"Failed to plan task: {e}",
            "status_code": 500,
        }

    return state


# ---------------------------------------------------------------------------
# Routing function
# ---------------------------------------------------------------------------

def should_dispatch(state: DeepAgentState) -> Literal["dispatch", "respond"]:
    """Route: dispatch to sub-agents or respond directly."""
    if state.get("error"):
        return "respond"

    plan = state.get("execution_plan", {})
    if plan.get("can_answer_directly"):
        return "respond"

    tasks = state.get("sub_agent_tasks", [])
    if not tasks:
        return "respond"

    return "dispatch"


# ---------------------------------------------------------------------------
# Task normalization - enforce single domain per task
# ---------------------------------------------------------------------------

def _normalize_tasks(
    raw_tasks: List[Dict[str, Any]],
    tool_groups: Dict[str, List[str]],
    log: logging.Logger,
) -> List[Dict[str, Any]]:
    """
    Normalize LLM-generated tasks to enforce single domain per task.

    If the LLM puts multiple domains in one task, split it into
    separate tasks (one per domain) to ensure proper tool isolation.
    """
    normalized: List[Dict[str, Any]] = []
    available_domains = set(tool_groups.keys())

    for task_spec in raw_tasks:
        domains = task_spec.get("domains", [])

        if len(domains) <= 1:
            # Already single-domain — keep as is
            normalized.append(task_spec)
            continue

        # Multi-domain task: split into one task per domain
        log.info(
            "Splitting multi-domain task %s (%s) into %d sub-tasks",
            task_spec.get("task_id", "?"),
            domains,
            len(domains),
        )
        original_id = task_spec.get("task_id", f"task_{len(normalized) + 1}")
        original_deps = task_spec.get("depends_on", [])
        description = task_spec.get("description", "")

        split_ids = []
        for i, domain in enumerate(domains):
            split_id = f"{original_id}_{domain}"
            split_ids.append(split_id)
            normalized.append({
                "task_id": split_id,
                "description": f"[{domain} part] {description}",
                "domains": [domain],
                "depends_on": list(original_deps),
            })

        # Update any later tasks that depend on the original task_id
        # to depend on ALL split sub-tasks instead
        for later_task in raw_tasks:
            deps = later_task.get("depends_on", [])
            if original_id in deps:
                deps.remove(original_id)
                deps.extend(split_ids)

    return normalized


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _parse_orchestrator_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse the orchestrator LLM response into a plan dict."""
    # Try to extract JSON from the response
    try:
        # Strip markdown code blocks
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (```json and ```)
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.strip() == "```" and in_block:
                    break
                if in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        plan = json.loads(text)

        # Validate structure
        if not isinstance(plan, dict):
            log.warning("Orchestrator response is not a dict, using fallback")
            return {"can_answer_directly": True, "reasoning": content, "tasks": []}

        return plan

    except json.JSONDecodeError:
        # Try to find JSON within the text
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        log.warning("Could not parse orchestrator response as JSON")
        return {"can_answer_directly": True, "reasoning": content, "tasks": []}


def _build_knowledge_context(state: DeepAgentState, log: logging.Logger) -> str:
    """Build knowledge context for the orchestrator prompt."""
    has_knowledge = bool(
        state.get("kb") or state.get("apps") or state.get("agent_knowledge")
    )
    has_tools = bool(state.get("tools"))

    if not has_knowledge and not has_tools:
        return (
            "## No Knowledge or Tools Configured\n"
            "This agent has no knowledge sources or service tools. "
            "For org-specific questions, inform the user to configure "
            "knowledge sources or toolsets."
        )

    parts = []
    if has_knowledge:
        parts.append(
            "## Knowledge Base Available\n"
            "An internal knowledge base is configured with indexed documents. "
            "Create a task with domain 'retrieval' to search it.\n\n"
            "**Hybrid strategy**: For services that have both indexed content AND API tools "
            "(e.g., Confluence pages may be indexed AND accessible via API), you can create "
            "BOTH a retrieval task and an API task in parallel. The retrieval finds indexed content "
            "quickly, while the API fetches the latest live version. This gives the most comprehensive results."
        )
    else:
        parts.append(
            "## No Knowledge Base\n"
            "No knowledge sources configured. Do NOT create retrieval tasks."
        )

    return "\n".join(parts)


def _build_tool_guidance(state: DeepAgentState) -> str:
    """Build tool-specific guidance based on active toolsets."""
    parts = []
    tools = state.get("tools", []) or []
    tools_lower = {t.lower() for t in tools if isinstance(t, str)}

    # Check which domains are active
    domains = set()
    for tool_name in tools_lower:
        if "." in tool_name:
            domains.add(tool_name.split(".", 1)[0])

    # Add concise guidance for each active domain
    if "jira" in domains:
        parts.append(
            "**Jira**: Use JQL with time filters (e.g., `updated >= -30d`). "
            "Get accountIds via `jira.search_users` before using in JQL."
        )
    if "confluence" in domains:
        parts.append(
            "**Confluence**: Use `confluence.search_pages` to find pages and "
            "`confluence.get_page_content` for full page content via API. "
            "If knowledge base is also configured, BOTH retrieval and Confluence API "
            "can be used in parallel for comprehensive results."
        )
    if "slack" in domains:
        parts.append(
            "**Slack**: Never use retrieval for Slack queries. "
            "Use `slack.set_user_status` with `duration_seconds`, not timestamps."
        )
    if "outlook" in domains:
        parts.append(
            "**Outlook**: Use `outlook.search_calendar_events_in_range` for finding events. "
            "Use `seriesMasterId` for recurring event operations."
        )
    if "teams" in domains:
        parts.append(
            "**Teams**: Use `teams.get_meetings` for meetings, "
            "`teams.get_meeting_transcript` for transcripts."
        )
    if "gmail" in domains:
        parts.append(
            "**Gmail**: Use `gmail.search_emails` with `max_results=50` for efficient fetching. "
            "Search results include subject, from, to, date, snippet — do NOT call "
            "`get_email_details` for every email. Only fetch details for specific emails "
            "that need full body content."
        )
    if "calendar" in domains:
        parts.append(
            "**Google Calendar**: Use time range filters. "
            "Prefer `get_calendar_events` with date bounds over unbounded queries."
        )

    if parts:
        return "## Domain-Specific Guidance\n" + "\n".join(parts)
    return ""


def _build_agent_instructions(state: DeepAgentState) -> str:
    """Build agent instructions prefix from state for the orchestrator prompt."""
    parts = []

    # Agent's custom system prompt (persona / role)
    base_prompt = state.get("system_prompt", "")
    if base_prompt and base_prompt.strip() and base_prompt != "You are an enterprise questions answering expert":
        parts.append(f"## Agent Role\n{base_prompt.strip()}")

    # Agent instructions (workflow-specific behavior)
    instructions = state.get("instructions", "")
    if instructions and instructions.strip():
        parts.append(f"## Agent Instructions\n{instructions.strip()}")

    if parts:
        return "\n\n".join(parts) + "\n\n"
    return ""


def _build_time_context(state: DeepAgentState) -> str:
    """Build time context string."""
    parts = []
    current_time = state.get("current_time")
    timezone = state.get("timezone")
    if current_time:
        parts.append(f"Current time: {current_time}")
    if timezone:
        parts.append(f"Timezone: {timezone}")
    return "\n".join(parts) if parts else ""


def _build_iteration_context(state: DeepAgentState, log: logging.Logger) -> str:
    """Build context from previous iteration results for re-planning.

    Provides rich context so the orchestrator can make informed decisions
    about what to do next in retry/continue scenarios.
    """
    completed = state.get("completed_tasks", [])
    evaluation = state.get("evaluation", {})

    if not completed and not evaluation:
        return ""

    parts = ["[Previous iteration results]"]

    for task in completed:
        status = task.get("status", "unknown")
        task_id = task.get("task_id", "unknown")
        domains = ", ".join(task.get("domains", []))
        desc = task.get("description", "")[:150]

        if status == "success":
            result = task.get("result", {})
            response_text = ""
            tool_count = 0
            if isinstance(result, dict):
                response_text = result.get("response", "")[:1000]
                tool_count = result.get("tool_count", 0)
                success_count = result.get("success_count", 0)
                error_count = result.get("error_count", 0)
            else:
                response_text = str(result)[:1000]

            header = f"- {task_id} [{domains}] (SUCCESS"
            if tool_count:
                header += f", {tool_count} tools: {success_count} ok, {error_count} err"
            header += f"): {desc}"
            parts.append(header)
            if response_text:
                parts.append(f"  Result: {response_text}")
        elif status == "error":
            error_text = task.get("error", "Unknown error")[:300]
            duration = task.get("duration_ms")
            duration_str = f" ({duration:.0f}ms)" if duration else ""
            parts.append(f"- {task_id} [{domains}] (FAILED{duration_str}): {error_text}")
        elif status == "skipped":
            parts.append(f"- {task_id} [{domains}] (SKIPPED): {task.get('error', 'Dependencies failed')[:200]}")

    if evaluation:
        decision = evaluation.get("decision", "")
        reasoning = evaluation.get("reasoning", "")
        if decision == "continue":
            continue_desc = evaluation.get("continue_description", "")
            parts.append(f"\n**Next step needed**: {continue_desc or reasoning}")
            parts.append("Create NEW sub-agent tasks for the next step. Do NOT repeat tasks that already succeeded.")
        elif decision == "retry":
            retry_fix = evaluation.get("retry_fix", "")
            retry_task = evaluation.get("retry_task_id", "")
            parts.append(f"\n**Retry needed**: {retry_fix or reasoning}")
            if retry_task:
                parts.append(f"Focus on fixing task: {retry_task}")
            parts.append("Create corrected sub-agent tasks. Apply the suggested fix.")

    return "\n".join(parts)
