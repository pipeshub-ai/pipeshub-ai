# pyright: basic
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from harness_core import bindings
from harness_core.kernel.core.interfaces import AgentHandle
from harness_core.kernel.core.types import Message, ToolCall
from harness_core.kernel.core.types import ToolResult as CoreToolResult

if TYPE_CHECKING:
    from harness_core.kernel.agent.spec import AgentSpec
    from harness_core.kernel.core.context import RunContext
    from harness_core.kernel.core.scope import ToolScope
    from harness_core.kernel.core.types import Goal, Todo
    from harness_core.kernel.runtime import AgentRuntime

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

`AgentHandle` itself is defined in `core/interfaces.py` (re-exported here
for this module's own public surface — see that module's docstring for why
it lives there instead of here): both `spec`/`runtime`/`todos`/
`visible_tools`/`run_ctx` used to live on a copy of this Protocol here too,
but are all reachable via `RouteContext.scope` now (see the read-through
properties below) — `emit()`/`extract_text()` are genuine Agent BEHAVIOR,
not state, so they stay part of the handle surface.
"""


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
        if registry is not None and registry.has(name):  # pyright: ignore[reportAttributeAccessIssue]
            tool = registry.resolve_by_name(name)  # pyright: ignore[reportAttributeAccessIssue]
            if isinstance(tool, SpecialRouteHandler):
                return tool
        return _builtin_fallback(name, registry)


def _builtin_fallback(name: str, tool_registry: object | None) -> SpecialRouteHandler | None:
    """The host's builtin handler for `name`, if one is bound in
    `bindings.special_route_fallback` (the framework's coordination and
    planning tools live outside the kernel)."""
    fallback = bindings.current().special_route_fallback
    if fallback is None:
        return None
    return fallback(name, tool_registry)
