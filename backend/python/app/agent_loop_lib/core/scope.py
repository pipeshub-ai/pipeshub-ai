"""Context scope hierarchy: `RunScope` -> `TurnScope` -> `ToolScope`.

Separates **ambient state** (hierarchical, flows downward through a run —
identity, budget, todos, extension slots) from **decision state** (the
`hooks.middleware.context` types middleware actually mutates to influence
control flow via `deny`/`ask`/`block`). Each decision context gains an
optional `.scope` backref (see `hooks/middleware/context.py`) so middleware
that needs ambient state doesn't have to be handed it via closure capture at
registration time.

Lives in `core/` (beside `RunContext`, see `core/context.py`) rather than
`agent/`, because `hooks/middleware/context.py` — a lower layer that `agent/`
itself depends on — must be able to type its `.scope` fields without
importing anything from `agent/`. `core` is the one package every layer may
import, so this is the only placement that doesn't invert that dependency
direction or risk an import cycle. References to `AgentSpec`/`AgentRuntime`
(both live above `core/`) are `TYPE_CHECKING`-only, exactly like
`hooks/middleware/context.py` already does for `Goal`/`Message`.

Three levels, matching the three units of work the turn loop actually
performs:
    - `RunScope`  — one per `Agent.run()` call (spans every turn).
    - `TurnScope` — one per `Agent.step()` call (spans one model call + its
      tool dispatch).
    - `ToolScope` — one per tool call within a turn.

Each is a plain, mutable dataclass — a data *partition* of what used to
accumulate entirely on `Agent`, not a new accumulation point. See
`StateSlot` below for the extensibility mechanism that lets other modules
(hooks, tools, host applications) attach their own per-run state without
`RunScope` growing a field for every consumer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.core.types import AgentTurn, Goal, Message, Todo, ToolCall
    from app.agent_loop_lib.modules.providers.budget.base import BudgetManager
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.base import Tag

__all__ = ["StateSlot", "RunScope", "TurnScope", "ToolScope"]

T = TypeVar("T")


@dataclass(frozen=True)
class StateSlot(Generic[T]):
    """A typed, named handle for per-run extensible state on `RunScope`.

    Like `contextvars.ContextVar`, but scoped to one `Agent.run()` rather
    than one asyncio Task — several agents can run sequentially on the same
    Task (e.g. `run_child()` is `await`ed inline by its caller), so a
    `ContextVar` would leak across agent boundaries in a way a per-`RunScope`
    slot does not.

    Frozen (and therefore hashable): `RunScope` keys its extension storage
    by the slot OBJECT, not by `key` string, so two modules can never
    collide even if they pick the same display name, and each slot's
    inherit/persist policy travels with the slot itself — no separate
    registry to keep in sync.

    Concurrency rule: parallel tool calls within one turn run concurrently
    via `asyncio.gather` (see `Agent.step()`), interleaving at every
    `await` point. Middleware reading-then-writing a slot value MUST NOT
    `await` between the read and the write — treat slot read-modify-write
    as synchronous, same discipline as mutating a shared dict from multiple
    coroutines on one event loop.

    Attributes:
        key: Display/serialization name, e.g. "require_critique.state".
            Used for logging and as the JSON key when `persist=True`.
        default_factory: Produces the slot's value the first time a
            `RunScope` reads it via `get()`.
        inherit: If True, `RunScope._inherit_from()` copies this slot's
            value (by reference) into a child's `RunScope` when a run is
            spawned via `AgentRuntime.run_child()` with a `parent_scope`.
        persist: If True, this slot's value is included in
            `RunScope.snapshot_extensions()` and therefore saved into
            `AgentCheckpoint.extensions` — the value must be JSON-safe.
    """

    key: str
    default_factory: Callable[[], T]
    inherit: bool = False
    persist: bool = False

    def __post_init__(self) -> None:
        if self.persist:
            _PERSISTED_SLOTS.append(self)


# Import-time registry of every `persist=True` slot ever declared — the
# moment a module does `X = StateSlot(..., persist=True)` at module scope,
# it becomes a candidate for `RunScope.restore_extensions()` on resume (see
# `agent/resume.py`). This is intentionally the ONLY registry in this
# module: `inherit` needs no such list because `_inherit_from` reads it
# directly off values already stored on the PARENT scope, but restoring
# from a checkpoint has no live parent scope to read policy off of.
_PERSISTED_SLOTS: list["StateSlot[Any]"] = []


def known_persisted_slots() -> tuple["StateSlot[Any]", ...]:
    """Every `persist=True` `StateSlot` declared so far in this process —
    used by `agent/resume.py` to know which checkpoint `extensions` keys
    correspond to a real slot it can rehydrate."""
    return tuple(_PERSISTED_SLOTS)


@dataclass
class RunScope:
    """Per-`Agent.run()` ambient state — identity, the goal, shared-service
    refs, and the mutable state that used to live directly on `Agent`
    (`_todos`, `_visible_tools`, `_extra_prompt_sections`, `_turns`,
    `_resume_turn_index`).

    Plain dataclass, not a Pydantic model: this is in-process run state with
    no serialization boundary of its own (checkpointing goes through
    `AgentCheckpoint`, a Pydantic model, via `snapshot_extensions()`/
    `restore_extensions()` for the extension slots specifically).

    `identity`/`spec`/`runtime`/`goal` are set once, at construction, in
    `Agent.run()`. `spec` is a REFERENCE to the same `AgentSpec` object the
    owning `Agent` holds — `handoff` mutates that object in place, so
    `scope.spec` observes a role change immediately without `RunScope`
    itself changing.
    """

    identity: "RunContext"
    spec: "AgentSpec"
    runtime: "AgentRuntime"
    goal: "Goal"
    session_id: str | None = None
    started_at: str | None = None

    todos: "list[Todo]" = field(default_factory=list)
    visible_tools: set[str] | None = None
    extra_prompt_sections: dict[str, str] = field(default_factory=dict)
    turns: "list[AgentTurn]" = field(default_factory=list)
    resume_turn_index: int = 0
    # No `timeline_seq` field here on purpose — that counter has exactly one
    # consumer (`agent/observability.py::append_timeline`) and lives there
    # as a `StateSlot`, both for SRP and to dogfood the extension mechanism
    # for the framework's own internal bookkeeping.

    # Typed extension state, keyed by the `StateSlot` OBJECT (frozen =>
    # hashable). Private: always go through `get`/`set`, never touch this
    # dict directly from outside `RunScope`.
    _extensions: dict["StateSlot[Any]", object] = field(default_factory=dict, repr=False)

    def get(self, slot: "StateSlot[T]") -> T:
        """Read a typed slot, materializing `slot.default_factory()` into
        storage on first access. There is deliberately no `has()` — once a
        default is materialized, "explicitly set vs. defaulted" is not a
        distinction `RunScope` can honestly expose; modules needing
        tri-state should declare `StateSlot[X | None]` with
        `default_factory=lambda: None`."""
        if slot not in self._extensions:
            self._extensions[slot] = slot.default_factory()
        return self._extensions[slot]  # type: ignore[return-value]

    def set(self, slot: "StateSlot[T]", value: T) -> None:
        """Write a typed slot."""
        self._extensions[slot] = value

    def snapshot_extensions(self) -> dict[str, Any]:
        """`persist=True` slot values, keyed by `slot.key` — embedded into
        `AgentCheckpoint.extensions` by `observability.save_checkpoint()`.
        Values must be JSON-safe; a non-serializable value in a
        `persist=True` slot is a programming error that surfaces (loudly,
        via the checkpoint store's own serialization) at checkpoint time
        rather than silently on resume."""
        return {slot.key: value for slot, value in self._extensions.items() if slot.persist}

    def restore_extensions(
        self, snapshot: dict[str, Any], known_slots: "Iterable[StateSlot[Any]]" = ()
    ) -> None:
        """Rehydrate persisted slot values on resume. `known_slots` should
        be `known_persisted_slots()` (or a caller-filtered subset) — values
        for keys that don't match any known, currently-declared
        `persist=True` slot are dropped, the same "unknown field" policy
        applied to any other checkpoint schema drift."""
        by_key = {slot.key: slot for slot in known_slots if slot.persist}
        for key, value in snapshot.items():
            slot = by_key.get(key)
            if slot is not None:
                self._extensions[slot] = value

    def _inherit_from(self, parent: "RunScope") -> None:
        """Copy `inherit=True` slots from a parent scope, BY REFERENCE.
        Called once, at run start, by `Agent.run()` when `run_child()`
        stashed a parent scope on the child. Slots with mutable values
        (e.g. `require_critique`'s shared holder) are shared across the
        whole spawn tree this way, on purpose — see the module docstring
        of `hooks/middleware/builtin/require_critique.py`."""
        for slot, value in parent._extensions.items():
            if slot.inherit:
                self._extensions[slot] = value

    @property
    def budget(self) -> "BudgetManager | None":
        """Read-through to the shared `AgentRuntime.budget` — the SAME
        `BudgetManager` instance for every agent in a spawn tree (budget is
        tracked tree-wide, not per-run-node). Convenience only; `RunScope`
        never owns or shadows this state itself."""
        return self.runtime.budget

    @property
    def mode(self) -> str:
        return self.spec.mode

    @property
    def max_turns(self) -> int:
        return self.spec.max_turns


@dataclass
class TurnScope:
    """Per-`Agent.step()` ambient state: which turn this is, plus the
    duplicate-tool-call detection set that used to live as
    `Agent._seen_tool_calls`.

    Deliberately carries NO message snapshot: message lists captured at
    different points within one turn have different, non-interchangeable
    contents (before the model call vs. after the assistant's response is
    recorded), so a single snapshot living here would be an attractive but
    incorrect shortcut. The pre-shaping snapshot `Agent.step()` sends
    through `dispatch_pre_model` stays a local variable there (it's already
    carried on `ModelCallContext.messages`); the post-response snapshot
    tool dispatch needs is `ToolScope.messages` below.
    """

    run: RunScope
    turn_index: int
    seen_tool_calls: set[str] = field(default_factory=set)


@dataclass
class ToolScope:
    """Per-tool-call ambient state within a turn.

    `messages` is captured FRESH from `ContextManager.messages()` at
    tool-dispatch time in `Agent.step()` — i.e. AFTER the assistant's
    response (the message containing the pending `tool_use` block) has
    already been added to the conversation. This is load-bearing, not
    incidental: `handle_clarify` (see `agent/observability.py`) saves
    exactly this snapshot into a HIL_PAUSE checkpoint, and `resume()`
    rebuilds the conversation from it — a pre-model-call snapshot would be
    missing that assistant message and produce an invalid conversation
    (a dangling tool_result with no matching tool_use) on resume.
    """

    turn: TurnScope
    call: "ToolCall"
    tool_path: str
    tags: "tuple[Tag, ...]" = ()
    messages: "list[Message]" = field(default_factory=list)
