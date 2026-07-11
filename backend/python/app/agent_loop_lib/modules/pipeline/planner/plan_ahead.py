from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, Planner

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsComplete


_PLAN_SYSTEM = (
    "You are a planning agent. Produce a complete, ordered multi-phase plan to accomplish the goal, "
    "as a numbered list, one phase per line: `1. Phase Name: description`. Mention the "
    "tools a phase needs, if any specific one applies, in that phase's own description. "
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
        model: "SupportsComplete | None" = None,
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
            return Plan(goal=goal, text="")

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"Success criteria: {'; '.join(goal.success_criteria) or 'none'}\n"
            f"Constraints: {'; '.join(goal.constraints) or 'none'}"
        )
        msg = UserMessage(content=prompt)
        system = _PLAN_SYSTEM
        if self._tool_names:
            # `coding_agent`/`web_agent` are PipesHub's composed delegates
            # (see `app.agents.agent_loop.domain_agents`) that wrap the same
            # `run_code`/`web_search`+`fetch_url` capabilities behind one
            # tool call each — when the caller composed the top-level
            # agent's tools, `run_code`/`web_search`/`fetch_url` are no
            # longer directly callable, so the plan must reference the
            # delegate instead or the executing ReAct loop has no tool
            # matching the phase's own work.
            code_tool = "coding_agent" if "coding_agent" in self._tool_names else "run_code"
            if "web_agent" in self._tool_names:
                web_phrase, web_verb = "`web_agent`", "is"
            else:
                web_phrase, web_verb = "`web_search`/`fetch_url`", "are"

            system += (
                "\n\nAvailable tools for execution: "
                f"{', '.join(self._tool_names)}. Reference these EXACT tool "
                "names in a phase's description whenever that phase's work "
                "maps onto one of them — e.g. any phase that must generate "
                "a downloadable file (PDF, spreadsheet, chart, document, "
                f"...) from data already gathered MUST reference `{code_tool}` if it is in this list. "
            )
            if self._sandbox_has_network:
                system += (
                    f"`{code_tool}` CAN reach the network — for a query needing live, "
                    "real-time public data that a well-known REST API serves, a "
                    f"single phase may reference `{code_tool}` directly to call that "
                    "API and analyze the response, instead of a separate fetch "
                    f"phase. {web_phrase} (if in this list) {web_verb} still "
                    "the better choice for discovery/research questions with no "
                    "single authoritative API, or for reading one known page."
                )
            else:
                system += (
                    f"`{code_tool}` has NO network access — any phase that needs "
                    "live/external data (an API, a webpage, search results) "
                    f"MUST reference {web_phrase} (if in this list) "
                    f"as an earlier phase, never `{code_tool}`, to fetch that data."
                )
        response = await self._model.complete(messages=[msg], system=system)
        return Plan(goal=goal, text=response.message.text)
