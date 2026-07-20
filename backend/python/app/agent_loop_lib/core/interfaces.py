"""Cross-cutting Protocols that don't belong to any single layer.

Lives in `core/` (the one package every layer may import) rather than
`agent/`, since both `AgentRunner` and `AgentHandle` need to be referenced
by code that sits BELOW `agent/` in the dependency graph (e.g.
`tools/special_route.py`) without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.types import AgentResult, Goal
    from app.agent_loop_lib.events.base import AgentEvent, EventType

__all__ = ["AgentRunner", "AgentHandle"]


@runtime_checkable
class AgentRunner(Protocol):
    """The narrow surface a caller needs to drive an agent run, regardless
    of concrete implementation — `Agent` is the only implementation today,
    but nothing outside `agent/` should depend on more than this (Dependency
    Inversion): orchestration code, the CLI, `serve/app.py`, and tests that
    just need "something runnable" can type against `AgentRunner` instead
    of importing the concrete `Agent` class.
    """

    async def run(self, goal: "Goal") -> "AgentResult":
        """Run to completion (or until blocked/cancelled/max_turns) and
        return the final `AgentResult`."""
        ...

    def stream(self, goal: "Goal", **run_kwargs) -> AsyncGenerator["AgentEvent", None]:
        """Run while yielding `AgentEvent`s as they occur."""
        ...

    async def resume(
        self, checkpoint_id: str, hil_responses: dict[str, str] | None = None
    ) -> "AgentResult":
        """Resume execution from a saved checkpoint."""
        ...

    @property
    def last_stream_result(self) -> "AgentResult | None":
        """The `AgentResult` of the most recent `stream()` call — `None`
        until one completes. `stream()` wraps `run()` in a background task
        (see `agent/streaming.py`) so the generator itself only yields
        `AgentEvent`s, never the final result; callers that need BOTH the
        live event stream AND the terminal result (e.g. `stream_bridge.py`)
        read it off here once the generator is exhausted."""
        ...


@runtime_checkable
class AgentHandle(Protocol):
    """The narrow surface a `SpecialRouteHandler` needs from the calling
    `Agent` that isn't already reachable off the scope chain — a Protocol
    instead of `Any` duck-typing against private attributes, per Interface
    Segregation: a route depends on exactly this, never on `Agent`'s full
    internals (turn-loop state, model, ...). `spec`/`runtime`/`todos`/
    `visible_tools`/`run_ctx` all live on `RouteContext.scope` instead (see
    `tools/special_route.py`) — `emit()`/`extract_text()` are genuine Agent
    BEHAVIOR, not state, so they stay part of the handle surface.

    Historically defined in `tools/special_route.py`; moved here so it sits
    beside its sibling `AgentRunner` Protocol and so lower layers can import
    it without reaching into `tools/`. Still re-exported from
    `tools/special_route.py` for that module's own public surface.
    """

    def extract_text(self, msg: "Message") -> str: ...

    async def emit(self, event_type: "EventType", payload: dict) -> None: ...
