"""
Orchestrator Node - Task Decomposition and Dispatch

The brain of the deep agent system. Analyzes the user query,
decomposes it into focused sub-tasks, and manages execution flow.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.modules.agents.capability_summary import (
    build_capability_summary,
    build_connector_routing_rules,
    classify_knowledge_sources,
)
from app.modules.agents.deep.context_manager import (
    build_conversation_messages,
    compact_conversation_history_async,
)
from app.modules.agents.deep.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from app.modules.agents.deep.state import DeepAgentState, SubAgentTask, get_opik_config
from app.modules.agents.deep.tool_router import (
    build_domain_description,
    group_tools_by_domain,
)
from app.modules.agents.deep.orchestrator_reflection import (
    OrchestratorReflectionError,
    run_orchestrator_with_reflection,
)
from app.modules.agents.qna.stream_utils import safe_stream_write, send_keepalive
from app.utils.time_conversion import build_llm_time_context

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langgraph.types import StreamWriter

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
        # Step 1: Build conversation context
        # Compact older history into a summary for sub-agents, but use the
        # same sliding-window approach as the react agent for the orchestrator
        # messages so the LLM sees the actual conversation flow.
        previous = state.get("previous_conversations", [])
        summary, _ = await compact_conversation_history_async(
            previous, llm, log,
        )
        if summary:
            state["conversation_summary"] = summary
            log.info("Compacted older conversations into summary for sub-agents")

        # Step 2: Group tools by domain (also captures tool descriptions)
        tool_groups = group_tools_by_domain(state)
        domain_desc = build_domain_description(tool_groups, state)

        # Step 3: Build orchestrator prompt
        knowledge_context = _build_knowledge_context(state, log)
        tool_guidance = _build_tool_guidance(state)
        agent_instructions = _build_agent_instructions(state)

        capability_summary = build_capability_summary(state)
        time_context = _build_time_context(state)

        system_prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(
            tool_domains=domain_desc,
            knowledge_context=knowledge_context,
            tool_guidance=tool_guidance,
            agent_instructions=agent_instructions,
            capability_summary=capability_summary,
            time_context=f"{time_context}\n\n" if time_context else "",
        )

        # Build messages
        messages = [SystemMessage(content=system_prompt)]

        # Add recent conversation history for follow-up resolution.
        # Cap at 10 pairs — enough to resolve references like "tell me more
        # about each file" without overwhelming the orchestrator's focus on
        # the current query.  Reference data (IDs, keys) is included so the
        # LLM can reuse them in task descriptions.
        conv_messages = build_conversation_messages(
            previous, log, max_pairs=10, include_reference_data=True,
        )
        if conv_messages:
            messages.extend(conv_messages)

        # Consume critic feedback whenever present. This must not depend on
        # iteration index because critic-driven re-plan can happen while
        # deep_iteration_count is still 0.
        critic_feedback = state.get("critic_feedback", "")
        if critic_feedback:
            from app.modules.agents.deep.orchestrator_critic import (
                inject_critic_feedback_into_messages,
            )
            messages = inject_critic_feedback_into_messages(messages, state)
            state["critic_feedback"] = ""      # consume — don't re-inject next time
            state["critic_issues"] = None

        # Add continue/retry context from previous iterations
        if iteration > 0:
            continue_ctx = _build_iteration_context(state, log)
            if continue_ctx:
                messages.append(HumanMessage(content=continue_ctx))

        # Add current query — this MUST be the last message so the LLM
        # focuses on it rather than getting distracted by conversation history.
        user_content = query

        # Append user context so the orchestrator can embed the correct user
        user_ctx = _build_user_context(state)
        if user_ctx:
            user_content += f"\n\n{user_ctx}"

        messages.append(HumanMessage(content=user_content))

        # Step 4: Get plan from LLM (keepalive prevents SSE timeout)
        log.info("Requesting task plan from LLM (with reflection)...")
        keepalive_task = asyncio.create_task(
            send_keepalive(writer, config, "Planning tasks...")
        )
    
        # Build the set of domains that actually have tools so the validator
        # can check domain references in the plan.
        available_domains: set[str] = set(tool_groups.keys())
        # Always allow 'retrieval' / 'knowledge' — these are virtual domains
        # handled by the knowledge layer, not tool_groups.
        available_domains.update({"retrieval", "knowledge"})
        state["_critic_available_domains"] = sorted(available_domains)
    
        try:
            plan = await run_orchestrator_with_reflection(
                llm=llm,
                messages=messages,
                available_domains=available_domains,
                log=log,
                config=get_opik_config(),
            )
        except OrchestratorReflectionError as reflection_err:
            # Retries exhausted — surface a real error to the user.
            log.error(
                "Orchestrator reflection exhausted all retries: %s", reflection_err
            )
            state["error"] = {
                "status": "error",
                "message": (
                    "I was unable to build a valid plan for your request after "
                    "multiple attempts. Please try rephrasing your query or "
                    "contact support if this persists."
                ),
                "status_code": 500,
                "detail": str(reflection_err),
            }
            return state
        finally:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task

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
        normalized_tasks = _normalize_tasks(raw_tasks, log)

        # Step 6b: Ensure retrieval task exists when knowledge base is configured
        has_knowledge = state.get("has_knowledge", False)
        if has_knowledge:
            has_retrieval = any(
                "retrieval" in (t.get("domains") or [])
                for t in normalized_tasks
            )
            if not has_retrieval:
                log.info(
                    "Orchestrator: injecting retrieval task — knowledge base is "
                    "configured but LLM plan has no retrieval task"
                )
                normalized_tasks.append(_create_retrieval_task(query))

        # Step 7: Build sub-agent tasks from plan
        from app.modules.agents.deep.tool_router import assign_tools_to_tasks

        tasks: list[SubAgentTask] = []
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
                "complexity": task_spec.get("complexity", "simple"),
                "batch_strategy": task_spec.get("batch_strategy"),
                "multi_step": bool(task_spec.get("multi_step", False)),
                "sub_steps": task_spec.get("sub_steps"),
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
# Helper: create a standard retrieval task dict (DRY)
# ---------------------------------------------------------------------------

def _create_retrieval_task(query: str) -> dict[str, Any]:
    """Return a standard retrieval task definition for the given query."""
    return {
        "task_id": "retrieval_search",
        "description": (
            f"Search the internal knowledge base thoroughly for: {query}. "
            "Use multiple diverse search queries with different keywords, "
            "phrasings, and angles to maximize coverage of relevant documents."
        ),
        "domains": ["retrieval"],
        "depends_on": [],
    }


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
    raw_tasks: list[dict[str, Any]],
    log: logging.Logger,
) -> list[dict[str, Any]]:
    """
    Normalize LLM-generated tasks to enforce single domain per task.

    If the LLM puts multiple domains in one task, split it into
    separate tasks (one per domain) to ensure proper tool isolation.
    """
    normalized: list[dict[str, Any]] = []

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
        for _i, domain in enumerate(domains):
            split_id = f"{original_id}_{domain}"
            split_ids.append(split_id)
            normalized.append({
                "task_id": split_id,
                "description": f"[{domain} part] {description}",
                "domains": [domain],
                "depends_on": list(original_deps),
                "complexity": task_spec.get("complexity", "simple"),
                "batch_strategy": task_spec.get("batch_strategy"),
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

def _build_knowledge_context(state: DeepAgentState, log: logging.Logger) -> str:
    """Build knowledge context for the orchestrator prompt.

    Uses shared `classify_knowledge_sources` and `build_connector_routing_rules`
    from capability_summary so the routing logic is maintained in one place.
    """
    has_knowledge = state.get("has_knowledge", False)
    has_tools = bool(state.get("tools"))

    if not has_knowledge and not has_tools:
        return (
            "## No Knowledge or Tools Configured\n"
            "This agent has no knowledge sources or service tools. "
            "For org-specific questions, inform the user to configure "
            "knowledge sources or toolsets."
        )

    if not has_knowledge:
        return (
            "## No Knowledge Base Configured\n"
            "No knowledge sources are configured for this agent. "
            "Do NOT create retrieval tasks — there is no knowledge base to search."
        )

    # ── Classify knowledge sources ─────────────────────────────────────────
    agent_knowledge: list = state.get("agent_knowledge", []) or []
    connector_configs = state.get("connector_configs") or {}
    kb_sources, indexed_connectors = classify_knowledge_sources(
        agent_knowledge,
        connector_configs=connector_configs if isinstance(connector_configs, dict) else None,
    )

    knowledge_lines: list[str] = [
        "## Knowledge Sources Available",
        "",
        "An internal knowledge base is configured with indexed documents.",
        "",
        "**MANDATORY RULE**: When a knowledge base is available you MUST set "
        "`can_answer_directly: false` and create retrieval task(s) for ANY substantive "
        "question — even if you believe you already know the answer. The knowledge base "
        "contains organisation-specific content your training data does not have. "
        "Only pure greetings and trivial arithmetic may skip retrieval. "
        "**The routing rules below still apply**: when the user explicitly names a "
        "specific connector (e.g. 'use Jira', 'from Confluence'), create retrieval "
        "tasks for ONLY that connector — do NOT search other sources.",
    ]

    # ── Routing rules with identity block (handles KB-only, connector-only, mixed) ──
    if kb_sources or indexed_connectors:
        routing = build_connector_routing_rules(
            indexed_connectors,
            kb_sources=kb_sources,
            call_format="orchestrator",
        )
        knowledge_lines.append(routing)
    else:
        # has_knowledge is True but no detailed sources resolved
        knowledge_lines.append(
            "\n- Internal knowledge sources are configured (details unavailable).\n"
            "  Create a generic retrieval task that searches the knowledge base."
        )

    # ── Retrieval task quality guidance ────────────────────────────────────
    knowledge_lines.append(
        "\n**Write rich retrieval task descriptions** — the description IS the "
        "instruction the retrieval sub-agent receives. Be specific:\n"
        "  • State the topic and key aspects to cover.\n"
        "  • Include the connector_id(s) and the connector label.\n"
        "  • Ask for multiple search query phrasings (different angles / synonyms).\n"
        "  Example: instead of \"Search KB for X\", write:\n"
        "  \"Search the Confluence knowledge base (connector_id: abc-123) for X. "
        "Cover features, pricing, integrations, and edition differences. "
        "Use at least 3 search queries with different phrasings.\"\n\n"
        "**Hybrid strategy**: When a service has BOTH indexed content AND live API tools "
        "(e.g., Confluence pages are indexed AND accessible via the API), create BOTH "
        "a retrieval task AND an API task in parallel — retrieval finds indexed snapshots "
        "quickly; the API fetches the latest live version."
    )

    return "\n".join(knowledge_lines)


def _build_tool_guidance(state: DeepAgentState) -> str:
    """
    Build tool guidance dynamically from the available tools.

    This is app-agnostic — it groups tools by domain and lists them
    so the orchestrator knows what's available without hardcoding per-app logic.
    """
    tools = state.get("tools", []) or []
    if not tools:
        return ""

    # Group tools by domain
    domain_tools: dict[str, list[str]] = {}
    for tool_name in tools:
        if not isinstance(tool_name, str):
            continue
        if "." in tool_name:
            domain, name = tool_name.split(".", 1)
            domain_tools.setdefault(domain, []).append(name)
        else:
            domain_tools.setdefault("other", []).append(tool_name)

    if not domain_tools:
        return ""

    parts = ["## Available Tool Domains"]
    parts.append(
        "Below are the tool domains available to sub-agents. "
        "Use the tool names to understand what each domain can do. "
        "Sub-agents should prefer bulk search/list tools with large page sizes "
        "over individual item lookups."
    )

    _MAX_TOOLS_DISPLAY = 10
    for domain, tool_list in sorted(domain_tools.items()):
        tool_names = ", ".join(f"`{domain}.{t}`" for t in tool_list[:_MAX_TOOLS_DISPLAY])
        if len(tool_list) > _MAX_TOOLS_DISPLAY:
            tool_names += f", ... ({len(tool_list) - _MAX_TOOLS_DISPLAY} more)"
        parts.append(f"- **{domain}**: {tool_names}")

    return "\n".join(parts)


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
    return build_llm_time_context(
        current_time=state.get("current_time"),
        time_zone=state.get("timezone"),
    )


def _build_user_context(state: DeepAgentState) -> str:
    """Build current user context so the orchestrator knows who 'my'/'me' refers to."""
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
    if not user_name and not user_email:
        return ""
    parts = ["Current user:"]
    if user_name:
        parts.append(f"  Name: {user_name}")
    if user_email:
        parts.append(f"  Email: {user_email}")
    return "\n".join(parts)




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
