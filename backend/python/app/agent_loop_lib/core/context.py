from __future__ import annotations

import asyncio
import uuid

from pydantic import BaseModel, Field


class RunContext(BaseModel):
    """Identity and tracing context threaded through every agent run.

    trace_id is shared across the entire agent tree (root + all sub-agents).
    run_id is unique per agent invocation.
    parent_run_id links sub-agents back to their spawning agent.
    """

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_run_id: str | None = None
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role_name: str
    model: str
    spawn_depth: int = 0
    # Agent teams (Phase 4): siblings spawned together from the SAME parallel
    # spawn_agent batch (agent/__init__.py's pre-launch block) share one
    # team_id, letting their automatic turn-memory writes (see
    # agent/observability.py's write_turn_memory) be retrieved together via
    # a team-scoped MemoryScope query even though each still has its own
    # agent_id. None for solo spawns / the root agent — "team" only means
    # something for a genuinely concurrent sibling group.
    team_id: str | None = None

    def child(self, role_name: str, model: str | None = None, team_id: str | None = None) -> "RunContext":
        """Create a RunContext for a sub-agent, inheriting the trace_id."""
        return RunContext(
            parent_run_id=self.run_id,
            trace_id=self.trace_id,
            role_name=role_name,
            model=model or self.model,
            spawn_depth=self.spawn_depth + 1,
            team_id=team_id,
        )


class CancellationToken:
    """Cooperative cancellation signal. Pass into Agent.run() to support graceful abort."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()
