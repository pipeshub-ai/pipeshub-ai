from __future__ import annotations

import logging
from typing import Any

from app.agent_loop_lib.core.types import Confidence, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.modules.pipeline.planner.base import parse_confidence
from app.agent_loop_lib.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext
from app.agent_loop_lib.tools.tags import TAG_PLANNING_CREATE_PLAN

"""`create_plan` — exposes `planner.default.DefaultPlanner` as a tool call
instead of only a programmatic pre-loop step (see `.claude/rules/
principles.md`'s gap map: "Planner via tool call ... Should become a tool
the agent calls mid-loop"). `AgentConfig.planner` + `agent/preamble.py`'s
upfront call remain for backward-compatible, deterministic pre-loop
planning; this tool is the additive, probabilistic path — any agent
(including a sub-agent with no preamble planner configured) can decide FOR
ITSELF, mid-run, that it wants a decomposed plan and call this instead of a
program deciding it on its behalf.

When ``steps`` is provided, the tool validates the structured plan graph
(unique IDs, valid dependency refs, acyclicity, non-empty descriptions)
and stores it in ``STRUCTURED_PLAN_SLOT`` for programmatic dispatch by
``OrchestratorLoop``.  When ``steps`` is omitted, the tool delegates to
``DefaultPlanner`` (backward compatible for ``PlanExecuteLoop`` etc.).

Confidence, either path, is never forced through a structured JSON *plan*
output — many models degrade when forced into strict JSON generation for
open-ended content. The structured-steps path takes an optional
``confidence`` enum argument alongside the already-structured ``steps``
(a one-field ask on top of function-calling args that exist anyway, not a
whole-plan schema) and returns it as a small dict payload. The free-form
path instead asks the model, via `DefaultPlanner`'s own system prompt, to
end its plain-markdown plan with a `Confidence: low|medium|high` line,
extracted deterministically by `extract_trailing_confidence()` — the plan
text itself streams to the caller completely unchanged.
`supervisor_confidence_gate` (`hooks/middleware/builtin/supervisor_gate.py`)
reads confidence off whichever of the two shapes this tool actually
returned.

Real work happens in `handle()` (needs the run's resolved transport, same
pattern as `tools/builtin/planning/replan.py`); `execute()` stays a harmless default
for direct/standalone invocation.
"""

__all__ = ["CreatePlanTool"]

logger = logging.getLogger(__name__)


def _validate_steps(raw_steps: list[dict[str, Any]]) -> tuple[list[Any], str | None]:
    """Parse and validate a list of raw step dicts.

    Returns ``(parsed_steps, error_message)``.  On success ``error_message``
    is ``None``.  On failure ``parsed_steps`` is empty and
    ``error_message`` describes all validation issues the LLM should fix.
    """
    from pydantic import ValidationError

    from app.agent_loop_lib.modules.pipeline.planner.base import PlanStep
    from app.agent_loop_lib.tools.builtin.coordination.graph_utils import find_cycle

    steps: list[PlanStep] = []
    errors: list[str] = []

    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            errors.append(f"Step {i}: expected an object, got {type(raw).__name__}")
            continue
        try:
            steps.append(PlanStep(**raw))
        except ValidationError as e:
            errors.append(f"Step {i}: {e}")
    if errors:
        return [], "Validation failed:\n" + "\n".join(errors)

    if not steps:
        return [], "Validation failed: steps array is empty."

    # Unique IDs
    ids_seen: dict[str, int] = {}
    for i, step in enumerate(steps):
        if not step.id.strip():
            errors.append(f"Step {i}: id must not be empty.")
        elif step.id in ids_seen:
            errors.append(
                f"Step {i}: duplicate id '{step.id}' (first seen at step {ids_seen[step.id]})."
            )
        else:
            ids_seen[step.id] = i

    # Non-empty descriptions
    for step in steps:
        if not step.description.strip():
            errors.append(f"Step '{step.id}': description must not be empty.")

    # Dependency refs
    all_ids = set(ids_seen.keys())
    for step in steps:
        for dep in step.depends_on:
            if dep == step.id:
                errors.append(f"Step '{step.id}': cannot depend on itself.")
            elif dep not in all_ids:
                errors.append(
                    f"Step '{step.id}' depends on '{dep}' which does not exist. "
                    f"Available step IDs: {sorted(all_ids)}"
                )

    if errors:
        return [], "Validation failed:\n" + "\n".join(errors)

    # Cycle detection
    adjacency = {step.id: list(step.depends_on) for step in steps}
    cycle = find_cycle(adjacency)
    if cycle is not None:
        return [], (
            "Validation failed: circular dependency detected: "
            + " -> ".join(cycle)
        )

    return steps, None


def _steps_to_text(steps: list[Any]) -> str:
    """Render validated steps as a human-readable plan summary — also what
    `critique_plan` sees, so a step's `boundaries`/`output_format` must
    show up here for the critique to actually judge scope overlap/gaps,
    not just for a human reading the plan after the fact."""
    lines = []
    for i, step in enumerate(steps, 1):
        deps = f" (depends on: {', '.join(step.depends_on)})" if step.depends_on else ""
        tools = f" [tools: {', '.join(step.tool_names)}]" if step.tool_names else ""
        lines.append(f"{i}. **{step.id}** — {step.description}{deps}{tools}")
        if step.boundaries:
            lines.append("   - Boundaries: " + "; ".join(step.boundaries))
        if step.output_format:
            lines.append(f"   - Output format: {step.output_format}")
    return "\n".join(lines)


class CreatePlanTool(Tool):
    @property
    def name(self) -> str:
        return "create_plan"

    @property
    def short_description(self) -> str:
        return "Decompose the current goal into ordered execution phases."

    @property
    def description(self) -> str:
        return (
            "Decompose the current goal into an ordered execution plan. "
            "When called with a `steps` array, validates the structured plan "
            "(unique IDs, valid dependency refs, no cycles) and stores it for "
            "programmatic dispatch — the preferred path for orchestrated "
            "multi-domain work. When called without `steps`, delegates to an "
            "LLM-driven planner for free-form plan generation."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/create_plan"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_PLANNING_CREATE_PLAN]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="steps",
                type=ParameterType.ARRAY,
                description=(
                    "Structured plan steps. Each step: "
                    '{id: string, description: string, domain: string, '
                    'tool_names: [string], depends_on: [string], '
                    'boundaries: [string], output_format: string}. '
                    "When provided, the plan is validated and stored for "
                    "programmatic dispatch. `id` must be unique. "
                    "`depends_on` lists IDs of steps whose output this step needs. "
                    "`tool_names` must be exact tool names from Available Domains. "
                    "`domain` identifies which domain handles the step. "
                    "`boundaries` (optional but recommended for 2+ step plans): explicit "
                    "'do NOT also...' exclusions that separate this step's scope from every "
                    "other step's — prevents two steps from redundantly covering the same "
                    "ground or silently leaving a gap between them. `output_format` (optional): "
                    "the exact shape the sub-agent should return (e.g. 'a table with columns "
                    "Ticket, Assignee, Status' or 'a JSON list of {id, title}') — steps that feed "
                    "a downstream step especially benefit from a stated format the next step can "
                    "rely on."
                ),
                required=False,
                default=None,
                items={"type": "object"},
            ),
            ToolParameter(
                name="confidence",
                type=ParameterType.STRING,
                description=(
                    "Only used together with `steps`. How confident you are that this "
                    "structured plan is complete and correct for the goal. LOW-confidence "
                    "plans are blocked by a deterministic review gate — revise the plan or "
                    "call request_review instead of proceeding. Defaults to medium if omitted."
                ),
                required=False,
                default=None,
                enum=["low", "medium", "high"],
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        raw_steps = call.arguments.get("steps")

        # --- Structured path: validate + store ---
        if raw_steps is not None:
            if not isinstance(raw_steps, list):
                return CoreToolResult(
                    tool_call_id=call.id, name=call.name,
                    content="steps must be an array of step objects.", is_error=True,
                )
            steps, error = _validate_steps(raw_steps)
            if error is not None:
                return CoreToolResult(
                    tool_call_id=call.id, name=call.name,
                    content=error, is_error=True,
                )

            from app.agent_loop_lib.modules.pipeline.planner.base import (
                STRUCTURED_PLAN_SLOT,
                Plan,
            )

            # Structured-steps confidence is a plain enum tool argument, not
            # a forced free-form JSON plan — see this module's docstring.
            # Absent -> MEDIUM, same default `parse_confidence()` uses for
            # any other unrecognised/missing value.
            raw_confidence = call.arguments.get("confidence")
            confidence = parse_confidence(raw_confidence) if raw_confidence is not None else Confidence.MEDIUM

            plan = Plan(goal=ctx.goal, text=_steps_to_text(steps), steps=steps, confidence=confidence)
            ctx.scope.turn.run.set(STRUCTURED_PLAN_SLOT, plan)
            logger.info(
                "create_plan: stored structured plan with %d steps (confidence=%s)",
                len(steps), confidence.value,
            )
            # Dict payload (not the model's problem to produce — this tool
            # composes it itself from an already-structured call) so
            # `supervisor_confidence_gate` can read `confidence` directly.
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content={"plan": plan.text, "confidence": confidence.value},
            )

        # --- Legacy path: delegate to DefaultPlanner ---
        from app.agent_loop_lib.modules.pipeline.planner.default import DefaultPlanner

        model = None
        if ctx.runtime.transport_registry is not None:
            try:
                model = ctx.spec.model.resolve(ctx.runtime.transport_registry)
            except Exception:
                model = None
        if model is None:
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content="No model available to plan", is_error=True,
            )

        try:
            plan = await DefaultPlanner(model).plan(ctx.goal)
            # Verbatim plan text, trailing `Confidence:` line and all — the
            # whole point of this path is that it streams to the caller
            # exactly as the model wrote it. `supervisor_confidence_gate`
            # extracts confidence from this same string via
            # `extract_trailing_confidence()`.
            content: object = plan.text
            is_error = False
        except Exception as e:
            content = str(e)
            is_error = True

        return CoreToolResult(tool_call_id=call.id, name=call.name, content=content, is_error=is_error)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"plan": ""})
