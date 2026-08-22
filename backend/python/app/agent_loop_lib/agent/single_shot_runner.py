from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import SingleShotLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.transport.registry import TransportRegistry

__all__ = [
    "StructuredSingleShotError",
    "build_task_complete_runtime",
    "parse_json_task_output",
    "parse_text_task_output",
    "run_structured_single_shot",
    "run_text_single_shot",
    "run_single_shot",
]

_TASK_COMPLETE_SUFFIX = (
    "\n\nWhen finished, call task_complete exactly once with `output` set to a "
    "JSON string containing your structured result. Do not wrap the JSON in "
    "markdown code fences."
)

_TASK_COMPLETE_TEXT_SUFFIX = (
    "\n\nWhen finished, call task_complete exactly once with `output` set to "
    "your result as plain markdown text — NOT a JSON string, no code fences "
    "around the whole thing."
)


class StructuredSingleShotError(Exception):
    """Raised when a single-shot agent run fails or returns unparseable output."""


def build_task_complete_runtime(
    transport_registry: "TransportRegistry",
    *,
    opik_enabled: bool = False,
    opik_project_name: str | None = None,
) -> AgentRuntime:
    """Minimal `AgentRuntime` for auxiliary single-shot agents (intent, goal,
    judge, ...) — one `task_complete` tool and the caller's transport."""
    from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool

    tool_registry = ToolRegistry()
    tool_registry.register_tool(TaskCompleteTool())
    return AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=tool_registry,
        opik_enabled=opik_enabled,
        opik_project_name=opik_project_name,
    )


async def run_single_shot(
    spec: AgentSpec,
    runtime: AgentRuntime,
    goal: Goal,
    *,
    seed_messages: Sequence["Message"] | None = None,
    skip_start: bool = False,
    session_id: str | None = None,
) -> Any:
    """Drive one `Agent.run()` with `SingleShotLoop` — the shared entry for
    auxiliary agents that must not implement their own turn loops."""
    agent = Agent(spec, runtime, session_id=session_id)
    if seed_messages:
        ctx = ContextManager()
        for message in seed_messages:
            await ctx.add(message)
        agent.seed_context(ctx)
    return await agent.run(goal, _skip_start=skip_start)


def parse_json_task_output(result: Any) -> dict[str, Any]:
    """Extract a JSON object from a successful single-shot `AgentResult`."""
    if not getattr(result, "success", False):
        raise StructuredSingleShotError(getattr(result, "error", None) or "agent run failed")

    output = getattr(result, "output", None)
    if isinstance(output, dict):
        return output
    if not isinstance(output, str) or not output.strip():
        raise StructuredSingleShotError("single-shot agent returned empty output")

    text = output.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)```\s*$", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredSingleShotError(f"single-shot output was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise StructuredSingleShotError("single-shot output JSON must be an object")
    return parsed


def parse_text_task_output(result: Any) -> str:
    """Extract raw text from a successful single-shot `AgentResult` — the
    unstructured counterpart of `parse_json_task_output`: no `json.loads`,
    no fence stripping, no schema. Only a failed run or genuinely empty
    output raise; anything else the model wrote is returned as-is, since
    callers using this path parse leniently (or not at all) downstream."""
    if not getattr(result, "success", False):
        raise StructuredSingleShotError(getattr(result, "error", None) or "agent run failed")

    output = getattr(result, "output", None)
    if not isinstance(output, str) or not output.strip():
        raise StructuredSingleShotError("single-shot agent returned empty output")
    return output.strip()


async def run_structured_single_shot(
    *,
    name: str,
    system_prompt: str,
    goal: Goal,
    runtime: AgentRuntime,
    model_spec: ModelSpec,
    output_schema_hint: str = "",
    seed_messages: Sequence["Message"] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Run a one-turn agent via `SingleShotLoop` + `task_complete`, returning
    the parsed JSON object from `task_complete`'s `output` argument."""
    spec = AgentSpec(
        name=name,
        system_prompt=system_prompt + output_schema_hint + _TASK_COMPLETE_SUFFIX,
        tool_names=["task_complete"],
        model=model_spec,
        loop=SingleShotLoop(),
        max_turns=1,
    )
    result = await run_single_shot(
        spec,
        runtime,
        goal,
        seed_messages=seed_messages,
        skip_start=seed_messages is not None,
        session_id=session_id,
    )
    return parse_json_task_output(result)


async def run_text_single_shot(
    *,
    name: str,
    system_prompt: str,
    goal: Goal,
    runtime: AgentRuntime,
    model_spec: ModelSpec,
    output_format_hint: str = "",
    seed_messages: Sequence["Message"] | None = None,
    session_id: str | None = None,
) -> str:
    """Run a one-turn agent via `SingleShotLoop` + `task_complete`, returning
    the raw markdown/text from `task_complete`'s `output` argument verbatim.

    The unstructured counterpart of `run_structured_single_shot`: the
    prompt specifies a FORMAT (via `output_format_hint`, e.g. markdown
    headings or a numbered list) but nothing here asserts or parses that
    format — callers extract whatever fields they need leniently (regex/
    heading scan), so a model that drifts slightly from the suggested
    layout degrades gracefully instead of raising.
    """
    spec = AgentSpec(
        name=name,
        system_prompt=system_prompt + output_format_hint + _TASK_COMPLETE_TEXT_SUFFIX,
        tool_names=["task_complete"],
        model=model_spec,
        loop=SingleShotLoop(),
        max_turns=1,
    )
    result = await run_single_shot(
        spec,
        runtime,
        goal,
        seed_messages=seed_messages,
        skip_start=seed_messages is not None,
        session_id=session_id,
    )
    return parse_text_task_output(result)
