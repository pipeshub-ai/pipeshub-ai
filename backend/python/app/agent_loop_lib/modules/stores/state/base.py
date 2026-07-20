from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel


class AgentStatus(str, Enum):
    IDLE           = "idle"
    STARTING       = "starting"
    CALLING_LLM    = "calling_llm"
    RUNNING_TOOL   = "running_tool"
    SPAWNING_AGENT = "spawning_agent"
    WAITING_HIL    = "waiting_hil"
    CHECKPOINTING  = "checkpointing"
    COMPLETED      = "completed"
    FAILED         = "failed"
    CANCELLED      = "cancelled"


class AgentState(BaseModel):
    run_id: str
    agent_id: str
    trace_id: str
    parent_run_id: str | None = None
    role_name: str
    status: AgentStatus = AgentStatus.IDLE
    current_turn: int = 0
    current_tool: str | None = None
    current_child_run_id: str | None = None
    hil_request_id: str | None = None
    goal_description: str
    started_at: str
    updated_at: str


class StateStore(ABC):
    @abstractmethod
    async def set(self, state: AgentState) -> None: ...

    @abstractmethod
    async def get(self, run_id: str) -> AgentState | None: ...

    @abstractmethod
    async def get_children(self, parent_run_id: str) -> list[AgentState]: ...

    @abstractmethod
    async def get_by_trace(self, trace_id: str) -> list[AgentState]: ...

    @abstractmethod
    async def delete(self, run_id: str) -> None: ...
