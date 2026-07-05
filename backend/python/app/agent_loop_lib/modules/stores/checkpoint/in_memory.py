from __future__ import annotations

from app.agent_loop_lib.modules.stores.checkpoint.base import (
    AgentCheckpoint,
    CheckpointStore,
)


class InMemoryCheckpointStore(CheckpointStore):
    """Non-persistent checkpoint store for testing and development."""

    def __init__(self) -> None:
        self._runs: dict[str, list[AgentCheckpoint]] = {}

    async def save(self, checkpoint: AgentCheckpoint) -> str:
        self._runs.setdefault(checkpoint.run_id, []).append(checkpoint)
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> AgentCheckpoint:
        for checkpoints in self._runs.values():
            for cp in checkpoints:
                if cp.checkpoint_id == checkpoint_id:
                    return cp
        raise KeyError(checkpoint_id)

    async def latest(self, run_id: str) -> AgentCheckpoint | None:
        checkpoints = self._runs.get(run_id)
        if not checkpoints:
            return None
        return checkpoints[-1]

    async def history(self, run_id: str) -> list[AgentCheckpoint]:
        return list(self._runs.get(run_id, []))

    async def delete_run(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
