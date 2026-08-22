from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionStore(ABC):
    @abstractmethod
    async def create(self, session: Session) -> str: ...

    @abstractmethod
    async def get(self, session_id: str) -> Session | None: ...

    @abstractmethod
    async def add_run(self, session_id: str, run_id: str) -> None: ...

    @abstractmethod
    async def list_runs(self, session_id: str) -> list[str]: ...

    @abstractmethod
    async def delete(self, session_id: str) -> None: ...
