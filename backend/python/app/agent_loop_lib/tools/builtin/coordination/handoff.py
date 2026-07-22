from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.exceptions import ToolError
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.special_route import RouteContext


class HandoffTool(Tool):
    """A2A-aligned "colleague loop" handoff — runs via `SpecialRouteHandler.
    handle()` (below), not the sentinel `execute()`.

    Distinct from spawn_agent: spawn_agent delegates a subordinate sub-task
    to a NEW child run and waits for its result (vertical/parent-child).
    handoff transfers ownership of THIS SAME run to another role, which
    picks up the full existing conversation on the very next turn
    (horizontal/peer-to-peer) — no child process, no separate AgentResult.
    """

    @property
    def name(self) -> str:
        return "handoff"

    @property
    def short_description(self) -> str:
        return "Hand off ownership of the current conversation to another role."

    @property
    def description(self) -> str:
        return (
            "Hand off ownership of the CURRENT conversation to a colleague role better "
            "suited to continue it (e.g. researcher -> writer once research is done). "
            "The colleague sees the full conversation history so far and takes over "
            "starting next turn — unlike spawn_agent, this does not create a separate "
            "sub-task or wait for a separate result."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/handoff"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="to_role", type=ParameterType.STRING,
                description="Name of the role to hand off to",
                required=True,
            ),
            ToolParameter(
                name="reason", type=ParameterType.STRING,
                description="Why this handoff is needed right now",
                required=True,
            ),
            ToolParameter(
                name="note", type=ParameterType.STRING,
                description="Context or instructions for the colleague picking this up",
                required=False,
                default=None,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        # Mutates `ctx.spec` (the SAME AgentSpec instance `agent` is
        # running with — AgentSpec is a plain, non-frozen pydantic model)
        # IN PLACE: since `Agent.step()` rebuilds tool_schemas/system_prompt
        # from `self._spec` fresh every turn, the very next turn is served
        # by the new role with no further plumbing needed.
        agent = ctx.agent
        spec = ctx.spec
        to_role_name = call.arguments.get("to_role", "")
        reason = call.arguments.get("reason", "")
        note = call.arguments.get("note", "")
        from_role_name = spec.name

        role_registry = ctx.runtime.role_registry
        if role_registry is None or not role_registry.has(to_role_name):
            available = role_registry.names() if role_registry is not None else []
            return CoreToolResult(
                tool_call_id=call.id, name=call.name,
                content=f"Unknown role: {to_role_name!r}. Available: {', '.join(available)}",
                is_error=True,
            )
        new_role = role_registry.resolve(to_role_name)

        spec.name = new_role.name
        spec.system_prompt = new_role.system_prompt
        spec.description = new_role.description
        spec.capabilities = list(new_role.capabilities)
        if new_role.allowed_tools:
            spec.tool_names = list(new_role.allowed_tools)
        # Progressive tool disclosure: the new role's own essentials should
        # be visible immediately, on top of whatever the prior role had
        # already unlocked via fetch_tools this run.
        if agent.visible_tools is not None and ctx.runtime.tool_registry is not None:
            from app.agent_loop_lib.agent.tool_loop import initial_visible_tools
            agent.visible_tools = agent.visible_tools | initial_visible_tools(spec, ctx.runtime)

        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="handoff")
        await obs.append_timeline(
            agent, "handoff", f"Handed off from {from_role_name!r} to {to_role_name!r}: {reason}",
            "running_tool",
            {"from_role": from_role_name, "to_role": to_role_name, "reason": reason, "note": note},
        )
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content={"handed_off_to": to_role_name, "reason": reason},
        )

    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ToolError("handoff is a special route (see SpecialRouteHandler.handle()) and must not be executed directly")
