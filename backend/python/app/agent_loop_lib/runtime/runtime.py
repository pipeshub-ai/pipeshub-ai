from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.context import CancellationToken, RunContext
from app.agent_loop_lib.core.exceptions import AgentError
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.events.base import EventEmitter
from app.agent_loop_lib.modules.providers.budget.base import BudgetManager
from app.agent_loop_lib.modules.providers.knowledge.base import KnowledgeProvider
from app.agent_loop_lib.modules.providers.memory.base import MemoryProvider
from app.agent_loop_lib.modules.stores.approval.base import ApprovalStore
from app.agent_loop_lib.modules.stores.checkpoint.base import CheckpointStore
from app.agent_loop_lib.modules.stores.hil.base import HILStore
from app.agent_loop_lib.modules.stores.session.base import SessionStore
from app.agent_loop_lib.modules.stores.state.base import StateStore
from app.agent_loop_lib.modules.stores.timeline.base import TimelineStore

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.scope import RunScope
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
    from app.agent_loop_lib.roles.registry import RoleRegistry
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agent_loop_lib.tools.summarizer import ToolSummarizer
    from app.agent_loop_lib.transport.registry import TransportRegistry

__all__ = ["AgentRuntime", "MAX_SPAWN_DEPTH"]

# How many spawn_agent/best_of_n/run_child hops deep a run may go before
# being refused — applies uniformly to dynamic spawn_agent fan-out AND to
# static agent_as_tool composition (see tools/builtin/coordination/agent_tool.py),
# since both ultimately call `run_child()`.
MAX_SPAWN_DEPTH = 3


@dataclass
class AgentRuntime:
    """Layer 2: shared services every `Agent` in a process draws on —
    transport/tool registries, the hook kernel, event emitter, budget,
    memory/knowledge/skills, and every durable store (checkpoint/session/
    state/hil/approval/timeline). One instance per `ControlPlane`,
    explicitly shared by every `Agent` it constructs (including children
    spawned via `run_child()`).

    `AgentSpec` (Layer 0) is reusable, definitional data with no I/O of its
    own; `AgentRuntime` is where the actual side-effecting collaborators
    live. An `Agent` is simply `AgentSpec` bound to one `AgentRuntime`.
    """

    transport_registry: "TransportRegistry | None" = None
    tool_registry: "ToolRegistry | None" = None
    hooks: "HookRegistry | None" = None
    event_emitter: EventEmitter | None = None
    budget: BudgetManager | None = None
    cancellation_token: CancellationToken | None = None
    # Set by `ControlPlane.start()` when `ControlPlaneConfig.opik` is active
    # (see `transport/opik_tracing.py`) — read by `Agent.run()` to decide
    # whether a ROOT run (no parent) should open its own Opik trace so every
    # turn's LLM-call span in that run nests under one trace instead of each
    # becoming its own standalone root trace.
    opik_enabled: bool = False
    opik_project_name: str | None = None

    memory: MemoryProvider | None = None
    knowledge: KnowledgeProvider | None = None
    skills: "SkillManager | None" = None

    checkpoint_store: CheckpointStore | None = None
    session_store: SessionStore | None = None
    state_store: StateStore | None = None
    hil_store: HILStore | None = None
    approval_store: ApprovalStore | None = None
    timeline_store: TimelineStore | None = None

    role_registry: "RoleRegistry | None" = None
    # Wired by ControlPlane to `AgentFactory.from_role` — lets dynamic
    # role-based spawning (spawn_agent/best_of_n/handoff) resolve a role
    # NAME to a full `AgentSpec` without this module depending on
    # `runtime/factory.py` (which itself depends on `roles/`), keeping the
    # dependency direction one-way (DIP: depend on this narrow callable,
    # not on the concrete factory).
    spec_factory: Callable[..., "AgentSpec"] | None = None

    # Same DIP shape as `spec_factory`: `agent/tool_loop.py` turns a tool
    # call's args/result into a short human-readable summary (for the
    # activity-timeline UI — see `tools/summarizer.py`'s docstring) via
    # this protocol only, never a concrete per-tool-aware implementation.
    # `None` (the default, e.g. `ControlPlane` standalone agents) means
    # "no summaries" — every downstream consumer already tolerates that.
    summarizer: "ToolSummarizer | None" = None

    def __post_init__(self) -> None:
        if self.hooks is None:
            from app.agent_loop_lib.hooks.registry import HookRegistry

            self.hooks = HookRegistry()
        if self.tool_registry is None:
            from app.agent_loop_lib.tools.registry import ToolRegistry

            self.tool_registry = ToolRegistry()

    def spec_for_role(self, role_name: str, **overrides: Any) -> "AgentSpec":
        """Resolve a role NAME to a full `AgentSpec` via the configured
        `spec_factory` — used by tools that only know a role by name
        (spawn_agent, best_of_n, handoff), never by static composition
        (which already holds a concrete `AgentSpec`)."""
        if self.spec_factory is None:
            raise AgentError(
                "No spec_factory configured on this AgentRuntime — cannot resolve a role by name. "
                "Wire one via ControlPlane, or build the AgentSpec directly and construct Agent(spec, runtime)."
            )
        return self.spec_factory(role_name, **overrides)

    async def run_child(
        self,
        spec: "AgentSpec",
        goal: Goal,
        parent_run_ctx: RunContext | None,
        *,
        team_id: str | None = None,
        session_id: str | None = None,
        parent_scope: "RunScope | None" = None,
        mirror_events: bool = True,
    ) -> AgentResult:
        """The single place a child agent is launched — used by both
        `agent_as_tool()`'s static composition and `spawn_agent`/`best_of_n`'s
        dynamic fan-out. Guards against runaway spawn depth, gives the
        child a fresh context, and propagates trace_id/team_id via
        `RunContext.child()` when a parent context is given.

        `session_id`, when given (callers pass the spawning `agent.session_id`),
        carries the parent's session identity onto the child — session-scoped
        middleware/stores (`require_critique`'s per-session pending-critique
        state, `ApprovalStore.get_session_decision`'s ASK_ONCE caching, HIL
        requests) need this to correctly treat a whole spawn tree as one
        session rather than the child looking unscoped (`session_id=None`).

        `parent_scope`, when given (callers pass the spawning agent's
        `RunScope` — e.g. `ctx.scope.turn.run` from a special-route handler,
        or `self._scope` from `Agent.step()`'s parallel spawn pre-launch),
        is stashed on the child and consumed ONCE at the start of the
        child's own `run()` (see `Agent.run()`) to copy `inherit=True`
        `StateSlot` values down — e.g. `require_critique`'s shared pending-
        critique holder (see `hooks/middleware/builtin/require_critique.py`).
        `AgentTool` (static composition) deliberately passes neither this
        nor a `parent_run_ctx` — its children always start with a clean
        scope, consistent with their fresh `RunContext`.

        `mirror_events` (default `True`): whether the child's own turns emit
        per-token `TEXT_MESSAGE_*`/`REASONING_MESSAGE_*` deltas through the
        shared `AgentRuntime.event_emitter`, same as the parent's. Set
        `False` for a child whose own token-level narration would be noise
        on the parent's stream (e.g. a silent judge/critique sub-agent) —
        it still emits ordinary lifecycle/tool events, just not the
        streaming text deltas.
        """
        from app.agent_loop_lib.agent import Agent
        from app.agent_loop_lib.context.manager import ContextManager
        from app.agent_loop_lib.transport.opik_tracing import (
            maybe_start_agent_span,
            record_agent_span_result,
        )

        current_depth = getattr(parent_run_ctx, "spawn_depth", 0)
        if current_depth >= MAX_SPAWN_DEPTH:
            raise AgentError(
                f"Maximum spawn depth ({MAX_SPAWN_DEPTH}) reached — "
                "cannot spawn further sub-agents from this depth"
            )

        # Every child, at ANY depth, loses every `TAG_UI_ONLY` tool
        # unconditionally — unlike the depth-gated `TAG_SPAWN` strip below,
        # this is not a resource limit, it's a category error: a UI-only
        # tool (e.g. a "ask the human a question" card) talks to whichever
        # human is watching the ROOT run's own stream. A spawned child has
        # no such audience and no way to route an answer back into its own
        # turn loop, so granting it one just lets the model stall an entire
        # spawn tree waiting on a question nobody will ever see, let alone
        # answer. Applies regardless of how `spec.tool_names` was built
        # (explicit `tools=[...]` on a `spawn_agent` call, a domain-agent's
        # claimed set, a spawn pool's `default_tool_names` residual) — this
        # is the one place every child agent's spec passes through before
        # `Agent.__init__` ever sees it.
        if self.tool_registry is not None and spec.tool_names:
            from app.agent_loop_lib.tools.tags import TAG_UI_ONLY
            filtered = [
                t for t in spec.tool_names if TAG_UI_ONLY not in self.tool_registry.tags_for_name(t)
            ]
            if len(filtered) != len(spec.tool_names):
                spec = spec.model_copy(update={"tool_names": filtered})

        if current_depth >= MAX_SPAWN_DEPTH - 1:
            # One hop from the limit: strip the tools that would let the
            # child spawn further sub-agents at all — a hard constraint,
            # not left to the child's own prompt/judgment. Dispatches on
            # TAG_SPAWN rather than the literal names "spawn_agent"/
            # "best_of_n" so a new spawn-like tool is covered by this guard
            # automatically, just by declaring the tag.
            from app.agent_loop_lib.tools.tags import TAG_SPAWN
            registry = self.tool_registry
            spec = spec.model_copy(update={
                "tool_names": [
                    t for t in spec.tool_names if TAG_SPAWN not in registry.tags_for_name(t)
                ],
            })

        child = Agent(spec, self, session_id=session_id)
        child.seed_context(ContextManager())
        if parent_run_ctx is not None:
            child._run_ctx = parent_run_ctx.child(
                role_name=spec.name, model=spec.model.model, team_id=team_id,
            )
        elif team_id is not None:
            child._run_ctx.team_id = team_id
        child._parent_scope = parent_scope
        # Without this, `child.run()`'s `step()` calls always take the
        # non-streaming `_model.complete()` branch — `_streaming` is only
        # ever flipped on by `Agent.stream()`, which nothing here calls
        # (this method awaits `child.run()` directly, for its `AgentResult`,
        # not an event generator). That means a sub-agent's own turns never
        # emit `TEXT_MESSAGE_*`/`REASONING_MESSAGE_*` — the exact gap the
        # "Parts-Based Agent Message Transcript" plan calls out. Flipping it
        # here (rather than switching to `child.stream()`) is sufficient:
        # `step()`'s streaming branch emits through `self.emit()`, which for
        # a plain `child.run()` call (no `_event_emitter_override` set)
        # reaches `self._runtime.event_emitter` directly — the SAME shared
        # emitter the parent's own events (and `TranscriptCollector`/
        # `AGUIEventEmitter`) already fan through. `child.stream()`'s extra
        # queue machinery exists only to hand the caller a consumable
        # `AgentEvent` generator, which no caller of `run_child()` needs.
        # Opt-out via `mirror_events=False` for a child whose token-level
        # narration shouldn't reach the parent's stream at all.
        child.streaming = mirror_events

        with maybe_start_agent_span(
            enabled=self.opik_enabled,
            role_name=spec.name,
            goal=goal,
            project_name=self.opik_project_name,
        ) as span:
            result = await child.run(goal)
            record_agent_span_result(span, result=result)
            return result
