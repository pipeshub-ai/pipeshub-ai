"""`HookRegistry`: the kernel that owns one `Pipeline`/`Wrapper` per lifecycle event.

This is the direct replacement for the old `HookChain` (see the removed
`hooks/middleware.py`): instead of a fixed set of `Hook` ABC methods called
in registration order, every lifecycle point is an independently composable
`Pipeline` (or, for true onion-style wrapping like LLM retry, a `Wrapper`)
that middleware attaches to via `kernel.on(event).use(...)` /
`kernel.wrapper(event).use(...)`.

Each `AgentConfig` gets its own `HookRegistry` instance (built fresh by
`ControlPlane`, mirroring how `HookChain` was built fresh per run) — nothing
here is process-global, so tests and concurrent runs never share middleware
state.
"""

from __future__ import annotations

from collections.abc import Callable

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.decisions import PostDecision, PreDecision
from app.agent_loop_lib.hooks.middleware.pipeline import Pipeline
from app.agent_loop_lib.hooks.middleware.wrapper import Wrapper
from app.agent_loop_lib.tools.errors import UnknownHookEventError

__all__ = ["HookRegistry"]

# Wrap-style events use `Wrapper` (nested closures, re-invocable `next()`)
# instead of `Pipeline` (single-pass `next()`) — see `hooks/events.py`'s
# module docstring for why PRE_MODEL_CALL can't be Pipeline-backed.
_WRAPPER_EVENTS: frozenset[HookEvent] = frozenset({HookEvent.PRE_MODEL_CALL})

# Terminal predicates + fail-closed policy per Pipeline-backed event.
_PIPELINE_FACTORIES: dict[HookEvent, Callable[[], Pipeline]] = {
    HookEvent.PRE_TOOL_USE: lambda: Pipeline(
        is_terminal=lambda ctx: ctx.decision == PreDecision.DENY, fail_closed=True
    ),
    HookEvent.POST_TOOL_USE: lambda: Pipeline(
        is_terminal=lambda ctx: ctx.decision == PostDecision.BLOCK, fail_closed=False
    ),
    HookEvent.PRE_AGENT: lambda: Pipeline(
        is_terminal=lambda ctx: ctx.decision == PreDecision.DENY, fail_closed=True
    ),
    HookEvent.POST_AGENT: lambda: Pipeline(is_terminal=lambda ctx: False, fail_closed=False),
    HookEvent.PRE_TURN: lambda: Pipeline(
        is_terminal=lambda ctx: ctx.decision == PreDecision.DENY, fail_closed=True
    ),
    HookEvent.POST_TURN: lambda: Pipeline(is_terminal=lambda ctx: False, fail_closed=False),
    # Pure reducer: every shaper runs, in registration order, mutating
    # ctx.messages in place — see hooks/middleware/context.py::ModelCallContext.
    HookEvent.PRE_MODEL: lambda: Pipeline(is_terminal=lambda ctx: False, fail_closed=False),
    # Pure reducer, same shape as PRE_MODEL but observing the response
    # instead of shaping the request — see hooks/middleware/context.py::ModelResponseContext.
    HookEvent.POST_MODEL: lambda: Pipeline(is_terminal=lambda ctx: False, fail_closed=False),
    HookEvent.GUARDRAIL_INPUT: lambda: Pipeline(
        is_terminal=lambda ctx: ctx.decision == PostDecision.BLOCK, fail_closed=False
    ),
    HookEvent.GUARDRAIL_OUTPUT: lambda: Pipeline(
        is_terminal=lambda ctx: ctx.decision == PostDecision.BLOCK, fail_closed=False
    ),
}


class HookRegistry:
    """Owns one `Pipeline` (or `Wrapper`) per `HookEvent`, built fresh per instance."""

    def __init__(self) -> None:
        self._pipelines: dict[HookEvent, Pipeline] = {
            event: factory() for event, factory in _PIPELINE_FACTORIES.items()
        }
        self._wrappers: dict[HookEvent, Wrapper] = {event: Wrapper() for event in _WRAPPER_EVENTS}
        self._custom_events: dict[str, Pipeline] = {}

    def on(self, event: "HookEvent | str") -> Pipeline:
        """Return the `Pipeline` for a gate/observe/reducer event.

        Raises:
            UnknownHookEventError: if `event` isn't Pipeline-backed (either
                built in, or previously added via `register_event`).
        """
        if event in self._pipelines:
            return self._pipelines[event]
        if isinstance(event, str) and event in self._custom_events:
            return self._custom_events[event]
        raise UnknownHookEventError(event, self.known_events())

    def wrapper(self, event: "HookEvent") -> Wrapper:
        """Return the `Wrapper` for a true onion-style wrap event (PRE_MODEL_CALL)."""
        if event not in self._wrappers:
            raise UnknownHookEventError(event, self.known_events())
        return self._wrappers[event]

    def register_event(self, name: str, pipeline: Pipeline) -> None:
        """Register a custom Pipeline-backed event beyond the builtin `HookEvent`s.

        Raises:
            ValueError: if `name` collides with a builtin or already-registered
                custom event — silently overwriting an existing pipeline would
                let a late `register_event` call swap out live middleware.
        """
        if name in self._pipelines or name in self._wrappers or name in self._custom_events:
            raise ValueError(f"hook event already registered: {name!r}")
        self._custom_events[name] = pipeline

    def known_events(self) -> list[str]:
        return (
            [event.value for event in self._pipelines]
            + [event.value for event in self._wrappers]
            + list(self._custom_events)
        )
