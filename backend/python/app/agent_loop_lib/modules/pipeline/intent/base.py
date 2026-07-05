from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_loop_lib.core.types import Goal, Intent


class IntentParser(ABC):
    """Top-level only — converts raw user message into a Goal."""

    @abstractmethod
    async def parse(self, message: str, context: dict | None = None) -> Intent: ...

    @abstractmethod
    async def to_goal(self, intent: Intent) -> Goal: ...
