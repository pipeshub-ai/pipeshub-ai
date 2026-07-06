from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import PlanningError
from app.agent_loop_lib.core.types import Confidence, Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Phase, Plan, Planner, parse_confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_PLAN_SCHEMA = {
    "type": "object",
    "required": ["phases", "confidence"],
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

_PLAN_SYSTEM = (
    "You are a planning agent. Produce a complete, ordered multi-phase plan to accomplish the goal. "
    "Each phase has a clear name, description, and the tools required. "
    "Plan all phases upfront before any execution begins."
)


class PlanAheadPlanner(Planner):
    """
    Produces a full multi-phase plan upfront.
    Suitable for high-complexity goals that benefit from global planning
    before any execution begins.
    """

    def __init__(
        self,
        model: "SupportsStructuredComplete | None" = None,
        tool_names: list[str] | None = None,
        *,
        sandbox_has_network: bool = False,
    ) -> None:
        self._model = model
        # Without this, the planner only ever sees the goal text and has no
        # idea `run_code` (or any other real tool) exists, so it produces
        # abstract phases ("fetch data", "create document") the executing
        # ReAct loop has no obligation to map back onto an actual tool call.
        self._tool_names = tool_names or []
        # Whether `run_code`'s sandbox has network access — mirrors the
        # SAME flag threaded into the tool's own description/CodeRequest
        # (see `sandbox_bridge.sandbox_network_enabled()`), so the upfront
        # plan and the executing tool never disagree about what `run_code`
        # can do.
        self._sandbox_has_network = sandbox_has_network

    async def plan(self, goal: Goal) -> Plan:
        if self._model is None:
            return Plan(goal=goal, phases=[], confidence=Confidence.LOW)

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"Success criteria: {'; '.join(goal.success_criteria) or 'none'}\n"
            f"Constraints: {'; '.join(goal.constraints) or 'none'}"
        )
        msg = UserMessage(content=prompt)
        system = _PLAN_SYSTEM
        if self._tool_names:
            system += (
                "\n\nAvailable tools for execution: "
                f"{', '.join(self._tool_names)}. Reference these EXACT tool "
                "names in a phase's `tools` list (and its description) "
                "whenever that phase's work maps onto one of them — e.g. "
                "any phase that must generate a downloadable file (PDF, "
                "spreadsheet, chart, document, ...) from data already "
                "gathered MUST reference `run_code` if it is in this list. "
            )
            if self._sandbox_has_network:
                system += (
                    "`run_code` CAN reach the network — for a query needing live, "
                    "real-time public data that a well-known REST API serves, a "
                    "single phase may reference `run_code` directly to call that "
                    "API and analyze the response, instead of a separate fetch "
                    "phase. `web_search`/`fetch_url` (if in this list) are still "
                    "the better choice for discovery/research questions with no "
                    "single authoritative API, or for reading one known page."
                )
            else:
                system += (
                    "`run_code` has NO network access — any phase that needs "
                    "live/external data (an API, a webpage, search results) "
                    "MUST reference `web_search`/`fetch_url` (if in this list) "
                    "as an earlier phase, never `run_code`, to fetch that data."
                )
        try:
            response = await self._model.complete_structured(
                messages=[msg],
                output_schema=_PLAN_SCHEMA,
                system=system,
            )
            result = response.data
            phases = [
                Phase(name=p["name"], description=p["description"], tools=p.get("tools") or [])
                for p in result["phases"]
            ]
            return Plan(goal=goal, phases=phases, confidence=parse_confidence(result.get("confidence", "medium")))
        except (KeyError, ValueError) as e:
            raise PlanningError(f"PlanAheadPlanner failed to parse plan: {e}") from e
