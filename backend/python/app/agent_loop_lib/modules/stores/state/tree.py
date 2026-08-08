from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agent_loop_lib.modules.stores.state.base import (
    AgentState,
    AgentStatus,
    StateStore,
)


class AgentNode(BaseModel):
    run_id: str
    parent_run_id: str | None
    role_name: str
    status: AgentStatus
    goal_description: str
    started_at: str
    completed_at: str | None = None
    children: list["AgentNode"] = Field(default_factory=list)
    result_summary: str | None = None
    turn_count: int = 0
    tool_calls_count: int = 0


class AgentTree(BaseModel):
    trace_id: str
    root: AgentNode
    snapshot_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AgentTreeBuilder:
    def __init__(self, state_store: StateStore) -> None:
        self._store = state_store

    async def build(self, trace_id: str) -> AgentTree:
        all_states = await self._store.get_by_trace(trace_id)
        roots = [s for s in all_states if s.parent_run_id is None]
        if not roots:
            raise ValueError(f"No root agent found for trace_id={trace_id!r}")
        root_node = self._build_node(roots[0], all_states)
        return AgentTree(trace_id=trace_id, root=root_node)

    async def build_for_run(self, run_id: str) -> AgentTree:
        state = await self._store.get(run_id)
        if state is None:
            raise KeyError(run_id)
        return await self.build(state.trace_id)

    def _build_node(self, state: AgentState, all_states: list[AgentState]) -> AgentNode:
        children_states = [s for s in all_states if s.parent_run_id == state.run_id]
        return AgentNode(
            run_id=state.run_id,
            parent_run_id=state.parent_run_id,
            role_name=state.role_name,
            status=state.status,
            goal_description=state.goal_description,
            started_at=state.started_at,
            children=[self._build_node(c, all_states) for c in children_states],
        )
