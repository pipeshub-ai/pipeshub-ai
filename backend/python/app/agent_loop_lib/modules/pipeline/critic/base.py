from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.types import Confidence


class CritiqueIssue(BaseModel):
    severity: str  # "error", "warning", "suggestion"
    description: str
    location: str | None = None


class CritiqueResult(BaseModel):
    passed: bool
    confidence: Confidence
    issues: list[CritiqueIssue] = Field(default_factory=list)
    summary: str


class Critic(ABC):
    """Evaluates a plan or execution result and returns a CritiqueResult."""

    @abstractmethod
    async def critique(self, subject: Any) -> CritiqueResult: ...
