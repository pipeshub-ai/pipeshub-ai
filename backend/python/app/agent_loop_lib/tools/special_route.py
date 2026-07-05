from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.agent_loop_lib.core.types import Message, ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.core.scope import ToolScope
    from app.agent_loop_lib.core.types import Goal, Todo
    from app.agent_loop_lib.events.base import EventType
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

__all__ = ["AgentHandle", "RouteContext", "SpecialRouteHandler", "SpecialRouteRegistry"]

"""Special-route dispatch: the typed replacement for a string-keyed if/elif
chain over tool names.

A "special route" is a tool call whose real behavior needs Agent-level
state (`agent.todos`, `agent.visible_tools`, `agent.run_ctx`, ...) that a
plain, stateless `Tool.execute(**kwargs)` can't hold — spawn_agent,
best_of_n, clarify, handoff, replan, write_todos, fetch_tools, search_tools.
Implementing `SpecialRouteHandler.handle()` on a `Tool` subclass lets it opt into this
without changing how it's registered or scheduled: `ToolExecutor.call_tool()`
still runs PreToolUse -> execute -> PostToolUse uniformly, just with
`handle()` swapped in for `execute()` via the existing `override_execute`
hook — so permission/mode/approval/audit-log middleware apply exactly as
they would for an ordinary tool.
"""


@runtime_checkable
class AgentHandle(Protocol):
    """The narrow surface a `SpecialRouteHandler` needs from the calling
    `Agent` that ISN'T already reachable off the scope chain — a Protocol
    instead of `Any` duck-typing against private attributes, per Interface
    Segregation: a route depends on exactly this, never on `Agent`'s full
    internals (turn-loop state, transport, ...). `spec`/`runtime`/`todos`/
    `visible_tools`/`run_ctx` used to live here too, but are all reachable
    via `RouteContext.scope` now (see the read-through properties below) —
    `emit()`/`extract_text()` are genuine Agent BEHAVIOR, not state, so they
    stay part of the handle surface.
    """

    def extract_text(self, msg: Message) -> str: ...

    async def emit(self, event_type: "EventType", payload: dict) -> None: ...


@dataclass
class RouteContext:
    """Everything a `SpecialRouteHandler` needs beyond its own `ToolCall`.

    `agent` is typed as `AgentHandle` (see above) rather than the concrete
    `Agent` class, so a route only ever depends on the narrow surface it
    actually uses for BEHAVIOR (`emit`/`extract_text`). Everything else —
    `spec`/`runtime`/`goal`/`turn_index`/`todos`/`visible_tools`/`run_ctx` —
    is ambient STATE, reached through `scope` (a `ToolScope`, since a route
    handler IS a tool call) via the read-through properties below, instead
    of `RouteContext` duplicating fields `Agent`/`RunScope` already hold.

    `scope.messages` is the FRESH post-response snapshot `ToolScope` carries
    (see `core/scope.py`) — `handle_clarify`'s HIL checkpoint depends on
    exactly this snapshot, which is why `messages` reads through `scope`
    rather than `scope.turn` (`TurnScope` deliberately carries no snapshot
    of its own).
    """

    agent: AgentHandle
    scope: "ToolScope"

    @property
    def runtime(self) -> "AgentRuntime":
        return self.scope.turn.run.runtime

    @property
    def spec(self) -> "AgentSpec":
        return self.scope.turn.run.spec

    @property
    def goal(self) -> "Goal":
        return self.scope.turn.run.goal

    @property
    def messages(self) -> list[Message]:
        return self.scope.messages

    @property
    def turn_index(self) -> int:
        return self.scope.turn.turn_index

    @property
    def started_at(self) -> str | None:
        return self.scope.turn.run.started_at

    @property
    def run_ctx(self) -> "RunContext":
        return self.scope.turn.run.identity

    @property
    def session_id(self) -> str | None:
        return self.scope.turn.run.session_id

    @property
    def todos(self) -> list["Todo"]:
        return self.scope.turn.run.todos

    @todos.setter
    def todos(self, value: list["Todo"]) -> None:
        self.scope.turn.run.todos = value

    @property
    def visible_tools(self) -> set[str] | None:
        return self.scope.turn.run.visible_tools

    @visible_tools.setter
    def visible_tools(self, value: set[str] | None) -> None:
        self.scope.turn.run.visible_tools = value


@runtime_checkable
class SpecialRouteHandler(Protocol):
    """A `Tool` that handles its own dispatch given `RouteContext`, instead
    of relying on `Tool.execute(**kwargs)` alone."""

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult: ...


class SpecialRouteRegistry:
    """Looks up the `SpecialRouteHandler` for a tool name.

    Prefers whatever `Tool` is actually registered under that name in the
    `ToolRegistry` (so a custom or overridden implementation always wins);
    falls back to the framework's own builtin handler for the small, fixed
    set of names the turn loop has always special-cased, so runs that
    don't wire up an explicit `tool_registry` still get correct behavior.
    """

    def __init__(self, tool_registry: object | None) -> None:
        self._tool_registry = tool_registry

    def get(self, name: str) -> SpecialRouteHandler | None:
        registry = self._tool_registry
        if registry is not None and registry.has(name):
            tool = registry.resolve_by_name(name)
            if isinstance(tool, SpecialRouteHandler):
                return tool
        return _builtin_fallback(name, registry)


def _builtin_fallback(name: str, tool_registry: object | None) -> SpecialRouteHandler | None:
    """Constructs a fresh instance of the framework's own builtin handler
    for `name`, if any. Every one of these tools is stateless (all real
    state lives on the `Agent`/`RouteContext`), so building a throwaway
    instance per lookup is cheap and side-effect-free. Imports are deferred
    to call time to avoid a circular import through `agent_loop.tools.builtin`.
    """
    if name == "fetch_tools":
        if tool_registry is None:
            return None
        from app.agent_loop_lib.tools.builtin.lazy_toolsets import FetchToolsTool
        return FetchToolsTool(tool_registry)

    if name == "search_tools":
        if tool_registry is None:
            return None
        from app.agent_loop_lib.tools.builtin.lazy_toolsets import SearchToolsTool
        return SearchToolsTool(tool_registry)

    from app.agent_loop_lib.tools.builtin.coordination.best_of_n import BestOfNTool
    from app.agent_loop_lib.tools.builtin.coordination.clarify import ClarifyTool
    from app.agent_loop_lib.tools.builtin.coordination.handoff import HandoffTool
    from app.agent_loop_lib.tools.builtin.coordination.route_task import RouteTaskTool
    from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
    from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
    from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
    from app.agent_loop_lib.tools.builtin.planning.replan import ReplanTool
    from app.agent_loop_lib.tools.builtin.planning.request_review import (
        RequestReviewTool,
    )
    from app.agent_loop_lib.tools.builtin.planning.todos import WriteTodosTool
    from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool

    builtins: dict[str, type] = {
        "spawn_agent": SpawnAgentTool,
        "best_of_n": BestOfNTool,
        "clarify": ClarifyTool,
        "handoff": HandoffTool,
        "replan": ReplanTool,
        "write_todos": WriteTodosTool,
        "route_task": RouteTaskTool,
        "create_plan": CreatePlanTool,
        "critique_plan": CritiquePlanTool,
        "verify_result": VerifyResultTool,
        "request_review": RequestReviewTool,
    }
    factory = builtins.get(name)
    return factory() if factory is not None else None
