from __future__ import annotations

from app.agent_loop_lib.modules.stores.state.base import AgentState, StateStore


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}

    async def set(self, state: AgentState) -> None:
        self._states[state.run_id] = state

    async def get(self, run_id: str) -> AgentState | None:
        return self._states.get(run_id)

    async def get_children(self, parent_run_id: str) -> list[AgentState]:
        return [s for s in self._states.values() if s.parent_run_id == parent_run_id]

    async def get_by_trace(self, trace_id: str) -> list[AgentState]:
        return [s for s in self._states.values() if s.trace_id == trace_id]

    async def delete(self, run_id: str) -> None:
        self._states.pop(run_id, None)
