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
        """
        from app.agent_loop_lib.agent import Agent
        from app.agent_loop_lib.context.manager import ContextManager

        current_depth = getattr(parent_run_ctx, "spawn_depth", 0)
        if current_depth >= MAX_SPAWN_DEPTH:
            raise AgentError(
                f"Maximum spawn depth ({MAX_SPAWN_DEPTH}) reached — "
                "cannot spawn further sub-agents from this depth"
            )
        if current_depth >= MAX_SPAWN_DEPTH - 1:
            # One hop from the limit: strip the tools that would let the
            # child spawn further sub-agents at all — a hard constraint,
            # not left to the child's own prompt/judgment.
            spec = spec.model_copy(update={
                "tool_names": [t for t in spec.tool_names if t not in ("spawn_agent", "best_of_n")],
            })

        child = Agent(spec, self, session_id=session_id)
        child._context = ContextManager()
        if parent_run_ctx is not None:
            child._run_ctx = parent_run_ctx.child(
                role_name=spec.name, model=spec.model.model, team_id=team_id,
            )
        elif team_id is not None:
            child._run_ctx.team_id = team_id
        child._parent_scope = parent_scope

        return await child.run(goal)
