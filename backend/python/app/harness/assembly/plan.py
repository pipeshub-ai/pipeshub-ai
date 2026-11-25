"""The loop's plan (UXR-B1): ``write_todos`` on root quick, react and auto runs, journaled as the run's ``task.*``
events. Quick joined in R49-PLAN (ADR-OWD-07 D-PLAN-1): it is the only strategy the chat offers, so the default chat's
Agent mode and every saved agent had no plan at all.

The built-in assistant's search modes (one source, one answer), deep mode (``create_plan``) and deep children are never
offered it, so their requests are unchanged; a short answer the model does not plan has no plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast, override

from app.harness.assembly.tool_sources import PLATFORM, SourcedTool
from harness_core.kernel.core.types import ToolResult as CoreToolResult
from harness_core.kernel.task_ledger import TaskLedger, asks_first, plan_items
from harness_core.provenance.rules import NO_CONTENT_RULE
from harness_core.tools.durable_port import Route, RouteResult
from harness_core.tools.tags import TAG_EFFECT_READ

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.harness.assembly.context import RunContext
    from harness_core.effects.types import ApprovalPolicy, ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.tools.registry import ToolRegistry

PLAN_TOOL: Final = "write_todos"
PLAN_MODES: Final = frozenset({"auto", "quick", "react"})
PLAN_DESCRIPTION: Final = (
    "Show the user your plan for work that takes three or more steps; never call it for a question you can "
    "answer directly. Pass the COMPLETE list each time (it replaces the last one), one status per item: "
    "'pending', 'in_progress', 'completed' or 'cancelled', exactly one 'in_progress'. Update it as steps "
    "finish or when the user's new instruction changes the plan."
)
# A run that may hand work to an engine: the engine plans that work in its own run, so the chat must not plan it twice.
HAND_OFF_PLAN_NOTE: Final = " Work you hand off with code_task is planned there; do not plan it here first."


def plan_todos_tool(*, hands_off: bool = False) -> Any:  # noqa: ANN401 - a WriteTodosTool; the agent stack loads with the run, not the idle worker (D-7)
    from app.agent_loop_lib.tools.base import ParameterType, ToolParameter
    from app.agent_loop_lib.tools.builtin.planning.todos import WriteTodosTool

    class PlanTodosTool(WriteTodosTool):
        """``write_todos`` as the durable loop offers it: when to plan, and the tools a step will use."""

        @property
        @override
        def short_description(self) -> str:
            return "Show the user your plan for multi-step work."

        @property
        @override
        def description(self) -> str:
            return PLAN_DESCRIPTION + (HAND_OFF_PLAN_NOTE if hands_off else "")

        @property
        @override
        def parameters(self) -> list[ToolParameter]:
            return [
                ToolParameter(
                    name="todos",
                    type=ParameterType.ARRAY,
                    required=True,
                    description=(
                        "Full task list: objects with 'content' (a short step title), 'status', and optionally 'tools' "
                        "(the names of the tools the step will call)."
                    ),
                    items={"type": "object"},
                )
            ]

    return PlanTodosTool()


def offers_plan(run: RunContext) -> bool:
    return (
        run.chat is None
        and run.sub_agent is None
        and not run.deep
        and run.loop_mode.strip().lower() in PLAN_MODES
    )


def plan_tool(run: RunContext, registry: ToolRegistry) -> SourcedTool | None:
    """Registers ``write_todos`` for a run offered it and names it for the run's catalog; ``None`` otherwise."""
    if not offers_plan(run):
        return None
    from app.harness.assembly.code_task import offers_code_task

    tool = plan_todos_tool(hands_off=offers_code_task(run))
    _ = registry.register_tool_if_absent(tool, extra_tags=(TAG_EFFECT_READ,))
    return SourcedTool(PLAN_TOOL, f"{PLATFORM}@builtin/{PLAN_TOOL}", None, provenance=NO_CONTENT_RULE)


def step_asks(effects: Mapping[str, ToolEffect], policy: ApprovalPolicy) -> Callable[[list[str]], bool]:
    return lambda names: asks_first(names, effects, policy)


class PlanRoutes:
    """``write_todos`` through the run's ledger: the list's changes journaled with the call's outcome."""

    def __init__(self, ledger: TaskLedger, asks: Callable[[list[str]], bool]) -> None:
        self._ledger = ledger
        self._asks = asks

    def routes(self) -> dict[str, Route]:
        return {PLAN_TOOL: self._route}

    async def _route(self, call: ToolCall) -> RouteResult:
        items = plan_items(cast("Mapping[str, object]", call.arguments).get("todos"), ("content", "title"), asks=self._asks)  # type: ignore[arg-type]
        if items is None:
            return RouteResult(
                CoreToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content="todos must be the full list of steps",
                    is_error=True,
                    error_code="invalid_call",
                )
            )
        events = self._ledger.replace(items)
        return RouteResult(
            CoreToolResult(tool_call_id=call.id, name=call.name, content={"count": len(items)}),
            self._ledger.slot(),
            tuple(events),
        )


__all__ = ["HAND_OFF_PLAN_NOTE", "PLAN_DESCRIPTION", "PLAN_TOOL", "PlanRoutes", "offers_plan", "plan_todos_tool", "plan_tool", "step_asks"]
