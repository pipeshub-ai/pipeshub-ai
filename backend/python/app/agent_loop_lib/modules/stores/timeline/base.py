from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.modules.stores.state.base import AgentStatus


class TimelineEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sequence_id: int
    trace_id: str
    run_id: str
    agent_id: str
    parent_run_id: str | None = None
    timestamp: str
    status: AgentStatus
    event_type: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    role_name: str = ""
    model: str = ""


class TimelineStore(ABC):
    @abstractmethod
    async def append(self, entry: TimelineEntry) -> None: ...

    @abstractmethod
    async def get_by_trace(self, trace_id: str) -> list[TimelineEntry]: ...

    @abstractmethod
    async def get_by_run(self, run_id: str) -> list[TimelineEntry]: ...

    @abstractmethod
    async def clear(self, trace_id: str) -> None: ...
