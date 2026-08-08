from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.types import Goal, Message, Todo
from app.agent_loop_lib.modules.providers.budget.base import BudgetSnapshot


class CheckpointKind(str, Enum):
    TURN_START     = "turn_start"
    PRE_TOOL       = "pre_tool"
    POST_TOOL      = "post_tool"
    TURN_COMPLETE  = "turn_complete"
    HIL_PAUSE      = "hil_pause"
    AGENT_COMPLETE = "agent_complete"


class AgentCheckpoint(BaseModel):
    """Full resumable snapshot of an agent at a turn boundary.

    Saved automatically at post_turn. Allows resume after crash, context
    limit, HIL pause, or any other interruption.
    """

    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    agent_id: str
    parent_run_id: str | None = None
    trace_id: str
    role_name: str
    model: str
    goal: Goal
    messages: list[Message]       # full context window at checkpoint time
    turn_index: int
    budget_snapshot: BudgetSnapshot
    kind: CheckpointKind = CheckpointKind.TURN_COMPLETE
    session_id: str | None = None
    status: str | None = None          # AgentStatus value as string (avoids circular import)
    current_tool: str | None = None
    hil_request_id: str | None = None
    # The ORIGINAL tool_use id of the clarify() call that triggered the HIL
    # pause. Needed on resume to build a valid TOOL-role message — providers
    # like Anthropic require tool_result.tool_use_id to match the assistant's
    # tool_use block, which is call.id, NOT the internal hil_request_id.
    pending_tool_call_id: str | None = None
    started_at: str | None = None         # original run start time, carried across resume
    system_prompt_override: str | None = None  # plan-injected system prompt, if any
    spawn_depth: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    todos: list[Todo] = Field(default_factory=list)
    # `RunScope.snapshot_extensions()` — `persist=True` `StateSlot` values,
    # keyed by `slot.key`. Rehydrated via `RunScope.restore_extensions()`
    # on resume (see `agent/resume.py`); unknown keys (a slot that no
    # longer exists in this process) are silently dropped there.
    extensions: dict[str, Any] = Field(default_factory=dict)


class CheckpointStore(ABC):
    """Pluggable checkpoint persistence.

    Backends: InMemory (testing), File (JSONL), SQLite, Redis, Postgres.
    """

    @abstractmethod
    async def save(self, checkpoint: AgentCheckpoint) -> str:
        """Persist checkpoint. Returns checkpoint_id."""
        ...

    @abstractmethod
    async def load(self, checkpoint_id: str) -> AgentCheckpoint:
        """Load a specific checkpoint. Raises KeyError if not found."""
        ...

    @abstractmethod
    async def latest(self, run_id: str) -> AgentCheckpoint | None:
        """Return the most recently saved checkpoint for a run, or None."""
        ...

    @abstractmethod
    async def history(self, run_id: str) -> list[AgentCheckpoint]:
        """Return all checkpoints for a run, oldest first."""
        ...

    @abstractmethod
    async def delete_run(self, run_id: str) -> None:
        """Delete all checkpoints for a run."""
        ...
