from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HILRequestType(str, Enum):
    CLARIFICATION  = "clarification"
    TOOL_APPROVAL  = "tool_approval"
    PLAN_REVIEW    = "plan_review"
    CHECKPOINT_PAUSE = "checkpoint_pause"


class HILRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_type: HILRequestType
    run_id: str
    session_id: str | None = None
    question: str
    context: dict[str, Any] = Field(default_factory=dict)
    checkpoint_id: str | None = None


class HILResponse(BaseModel):
    request_id: str
    approved: bool = True
    answer: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HILStore(ABC):
    @abstractmethod
    async def submit(self, request: HILRequest) -> str:
        """Persist a HIL request. Returns request_id."""
        ...

    @abstractmethod
    async def get_request(self, request_id: str) -> HILRequest | None: ...

    @abstractmethod
    async def respond(self, response: HILResponse) -> None:
        """Record a HIL response and unblock any waiter."""
        ...

    @abstractmethod
    async def wait_for_response(self, request_id: str, timeout: float | None = None) -> HILResponse:
        """
        Block until a response arrives for request_id.
        Raises asyncio.TimeoutError if timeout elapses without a response.
        Raises KeyError if request_id is unknown.
        """
        ...

    @abstractmethod
    async def get_response(self, request_id: str) -> HILResponse | None:
        """Return the stored response immediately, or None if not yet answered."""
        ...

    @abstractmethod
    async def list_pending(self, session_id: str | None = None) -> list[HILRequest]:
        """Return all unanswered HIL requests, optionally filtered by session_id."""
        ...
