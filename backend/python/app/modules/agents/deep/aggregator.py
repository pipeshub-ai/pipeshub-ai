"""
Aggregator Node - Result Collection and Quality Evaluation

Collects sub-agent results, merges tool outputs into a format
compatible with respond_node, evaluates quality, and decides
the next action: respond | retry | continue.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Literal

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.modules.agents.deep.prompts import EVALUATOR_PROMPT
from app.modules.agents.deep.state import DeepAgentState, SubAgentTask, get_opik_config
from app.modules.agents.qna.stream_utils import safe_stream_write

logger = logging.getLogger(__name__)

# Max iterations to prevent infinite loops
DEFAULT_MAX_ITERATIONS = 3


async def aggregator_node(
    state: DeepAgentState,
    config: RunnableConfig,
    writer: StreamWriter,
) -> DeepAgentState:
    """
    Aggregate sub-agent results and decide next action.

    Flow:
    1. Collect all completed task results
    2. Merge tool results for respond_node compatibility
    3. Evaluate quality (fast-path for clear cases, LLM for ambiguous)
    4. Set reflection_decision + reflection for respond_node
    5. Return routing decision via state
    """
    start_time = time.perf_counter()
    log = state.get("logger", logger)
    completed = state.get("completed_tasks", [])
    iteration = state.get("deep_iteration_count", 0)
    max_iter = state.get("deep_max_iterations", DEFAULT_MAX_ITERATIONS)

    safe_stream_write(writer, {
        "event": "status",
        "data": {
            "status": "evaluating",
            "message": "Evaluating results...",
        },
    }, config)

    # Count task outcomes
    success_tasks = [t for t in completed if t.get("status") == "success"]
    error_tasks = [t for t in completed if t.get("status") == "error"]
    skipped_tasks = [t for t in completed if t.get("status") == "skipped"]

    log.info(
        "Aggregator: %d tasks (%d success, %d error, %d skipped), iteration %d/%d",
        len(completed), len(success_tasks), len(error_tasks),
        len(skipped_tasks), iteration + 1, max_iter,
    )

    # Fast-path: all tasks succeeded
    if len(success_tasks) == len(completed) and completed:
        log.info("All tasks succeeded — responding directly")
        _set_respond_success(state, success_tasks, log)
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"Aggregator completed in {duration_ms:.0f}ms (fast-path success)")
        return state

    # Fast-path: all tasks failed
    if len(error_tasks) == len(completed) and completed:
        # Check if we can retry
        if iteration < max_iter - 1 and _has_retryable_errors(error_tasks):
            log.info("All tasks failed with retryable errors — will retry")
            _set_retry(state, error_tasks, log)
        else:
            log.info("All tasks failed — responding with error")
            _set_respond_error(state, error_tasks, log)

        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"Aggregator completed in {duration_ms:.0f}ms (fast-path error)")
        return state

    # Fast-path: at least one success and we're at max iterations
    if success_tasks and iteration >= max_iter - 1:
        log.info("Partial success at max iterations — responding with available data")
        _set_respond_success(state, success_tasks, log)
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"Aggregator completed in {duration_ms:.0f}ms (partial success, max iter)")
        return state

    # Fast-path: at least one success, no errors need retry
    if success_tasks and not _has_retryable_errors(error_tasks):
        log.info("Partial success, no retryable errors — responding with available data")
        _set_respond_success(state, success_tasks, log)
        duration_ms = (time.perf_counter() - start_time) * 1000
        log.info(f"Aggregator completed in {duration_ms:.0f}ms (partial success)")
        return state

    # Ambiguous case: use LLM to evaluate
    try:
        evaluation = await _evaluate_with_llm(state, completed, log)
        state["evaluation"] = evaluation
        decision = evaluation.get("decision", "respond_success")

        if decision == "respond_success":
            _set_respond_success(state, success_tasks, log)
        elif decision == "respond_error":
            _set_respond_error(state, error_tasks, log)
        elif decision == "retry" and iteration < max_iter - 1:
            _set_retry(state, error_tasks, log)
        elif decision == "continue" and iteration < max_iter - 1:
            _set_continue(state, evaluation, log)
        else:
            # Fallback: respond with whatever we have
            if success_tasks:
                _set_respond_success(state, success_tasks, log)
            else:
                _set_respond_error(state, error_tasks, log)

    except Exception as e:
        log.warning("LLM evaluation failed: %s, using fast-path", e)
        if success_tasks:
            _set_respond_success(state, success_tasks, log)
        else:
            _set_respond_error(state, error_tasks, log)

    duration_ms = (time.perf_counter() - start_time) * 1000
    log.info(
        "Aggregator completed in %.0fms (decision: %s)",
        duration_ms, state.get("reflection_decision"),
    )
    return state


# ---------------------------------------------------------------------------
# Routing function
# ---------------------------------------------------------------------------

def route_after_evaluation(
    state: DeepAgentState,
) -> Literal["respond", "retry", "continue"]:
    """Route based on aggregator's evaluation decision."""
    decision = state.get("reflection_decision", "respond_success")

    if decision in ("respond_success", "respond_error", "respond_clarify"):
        return "respond"
    if decision == "retry":
        return "retry"
    if decision == "continue":
        return "continue"

    # Fallback
    return "respond"


# ---------------------------------------------------------------------------
# Decision setters
# ---------------------------------------------------------------------------

def _set_respond_success(
    state: DeepAgentState,
    success_tasks: List[SubAgentTask],
    log: logging.Logger,
) -> None:
    """Set state for a successful response."""
    state["reflection_decision"] = "respond_success"
    state["reflection"] = {
        "decision": "respond_success",
        "reasoning": f"{len(success_tasks)} task(s) completed successfully",
    }


def _set_respond_error(
    state: DeepAgentState,
    error_tasks: List[SubAgentTask],
    log: logging.Logger,
) -> None:
    """Set state for an error response."""
    error_details = []
    for task in error_tasks[:3]:
        error_details.append(
            f"{task.get('task_id', 'unknown')}: {task.get('error', 'Unknown error')[:200]}"
        )
    error_context = "; ".join(error_details) if error_details else "Tasks failed"

    state["reflection_decision"] = "respond_error"
    state["reflection"] = {
        "decision": "respond_error",
        "error_context": error_context,
        "reasoning": f"{len(error_tasks)} task(s) failed",
    }


def _set_retry(
    state: DeepAgentState,
    error_tasks: List[SubAgentTask],
    log: logging.Logger,
) -> None:
    """Set state for a retry loop."""
    # Build fix description from error details
    fixes = []
    for task in error_tasks[:3]:
        error = task.get("error", "")
        if "timeout" in error.lower():
            fixes.append(f"{task['task_id']}: increase timeout or simplify task")
        elif "unauthorized" in error.lower() or "forbidden" in error.lower():
            fixes.append(f"{task['task_id']}: check permissions")
        else:
            fixes.append(f"{task['task_id']}: retry with adjusted parameters")

    state["reflection_decision"] = "retry"
    state["reflection"] = {
        "decision": "retry",
        "retry_fix": "; ".join(fixes),
        "reasoning": f"Retrying {len(error_tasks)} failed task(s)",
    }
    state["deep_iteration_count"] = state.get("deep_iteration_count", 0) + 1
    # Clear sub_agent_tasks so orchestrator creates fresh tasks for retry
    state["sub_agent_tasks"] = []


def _set_continue(
    state: DeepAgentState,
    evaluation: Dict[str, Any],
    log: logging.Logger,
) -> None:
    """Set state for a continue loop (more steps needed).

    The orchestrator will re-plan with the previous results and the
    continue_description, creating NEW sub-agents for the next steps.
    """
    state["reflection_decision"] = "continue"
    state["reflection"] = {
        "decision": "continue",
        "continue_description": evaluation.get("continue_description", "More steps needed"),
        "reasoning": evaluation.get("reasoning", ""),
    }
    state["deep_iteration_count"] = state.get("deep_iteration_count", 0) + 1
    # Clear sub_agent_tasks so orchestrator creates fresh tasks for the next iteration
    state["sub_agent_tasks"] = []


# ---------------------------------------------------------------------------
# LLM-based evaluation
# ---------------------------------------------------------------------------

async def _evaluate_with_llm(
    state: DeepAgentState,
    completed_tasks: List[SubAgentTask],
    log: logging.Logger,
) -> Dict[str, Any]:
    """Use LLM to evaluate ambiguous results and decide next action."""
    llm = state.get("llm")
    query = state.get("query", "")
    plan = state.get("task_plan", {})

    # Build results summary with enough detail for complex workflows
    results_parts = []
    for task in completed_tasks:
        status = task.get("status", "unknown")
        task_id = task.get("task_id", "unknown")
        domains = ", ".join(task.get("domains", []))
        desc = task.get("description", "")[:200]
        duration = task.get("duration_ms")

        if status == "success":
            result = task.get("result", {})
            if isinstance(result, dict):
                response = result.get("response", "")[:1500]
                tool_count = result.get("tool_count", 0)
                success_count = result.get("success_count", 0)
                error_count = result.get("error_count", 0)
                results_parts.append(
                    f"### {task_id} [{domains}] — SUCCESS ({tool_count} tools: {success_count} ok, {error_count} err)"
                )
                if desc:
                    results_parts.append(f"  Task: {desc}")
                if response:
                    results_parts.append(f"  Response: {response}")
            else:
                results_parts.append(f"### {task_id} [{domains}] — SUCCESS")
                results_parts.append(f"  {str(result)[:1000]}")
        elif status == "error":
            error_text = task.get("error", "Unknown error")[:500]
            duration_str = f" in {duration:.0f}ms" if duration else ""
            results_parts.append(
                f"### {task_id} [{domains}] — FAILED{duration_str}"
            )
            if desc:
                results_parts.append(f"  Task: {desc}")
            results_parts.append(f"  Error: {error_text}")
        elif status == "skipped":
            results_parts.append(
                f"### {task_id} [{domains}] — SKIPPED: {task.get('error', 'Dependencies failed')[:300]}"
            )

    results_summary = "\n".join(results_parts)

    # Build plan summary
    plan_summary = ""
    if plan:
        try:
            plan_summary = json.dumps(
                {k: v for k, v in plan.items() if k != "can_answer_directly"},
                default=str,
            )[:1500]
        except (TypeError, ValueError):
            plan_summary = str(plan)[:1500]

    # Build agent instructions context for the evaluator
    agent_instructions = _build_evaluator_instructions(state)

    prompt = EVALUATOR_PROMPT.format(
        query=query,
        task_plan=plan_summary,
        results_summary=results_summary,
        agent_instructions=agent_instructions,
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)], config=get_opik_config())
    content = response.content if hasattr(response, "content") else str(response)

    return _parse_evaluation_response(content, log)


def _build_evaluator_instructions(state: DeepAgentState) -> str:
    """Build agent instructions context for the evaluator prompt."""
    parts = []

    instructions = state.get("instructions", "")
    if instructions and instructions.strip():
        parts.append(f"## Agent Instructions (consider when evaluating completeness)\n{instructions.strip()}")

    if parts:
        return "\n\n".join(parts) + "\n\n"
    return ""


def _parse_evaluation_response(content: str, log: logging.Logger) -> Dict[str, Any]:
    """Parse the evaluator LLM response into a decision dict."""
    try:
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
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

        result = json.loads(text)
        if isinstance(result, dict) and "decision" in result:
            return result
    except json.JSONDecodeError:
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            try:
                result = json.loads(json_match.group())
                if isinstance(result, dict) and "decision" in result:
                    return result
            except json.JSONDecodeError:
                pass

    log.warning("Could not parse evaluation response, defaulting to respond_success")
    return {"decision": "respond_success", "reasoning": content[:200]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_retryable_errors(error_tasks: List[SubAgentTask]) -> bool:
    """Check if any failed tasks have errors that might succeed on retry."""
    retryable_patterns = [
        "timeout", "rate limit", "429", "503", "502",
        "temporary", "transient", "connection",
    ]

    for task in error_tasks:
        error = (task.get("error") or "").lower()
        if any(p in error for p in retryable_patterns):
            return True

    return False
