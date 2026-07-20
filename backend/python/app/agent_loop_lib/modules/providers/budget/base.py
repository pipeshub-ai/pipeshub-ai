from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class BudgetSnapshot(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: int = 0
    turns: int = 0
    cost_usd: float = 0.0


class BudgetManager(ABC):
    """Tracks and enforces token/cost budgets.

    Every method is `async` even though today's only implementation
    (`BudgetTracker`) is pure in-memory bookkeeping — the interface must
    accommodate implementations backed by shared/durable storage (a
    Redis-backed budget shared across a fleet of workers, a SQL row updated
    per call) without a breaking signature change later. This mirrors every
    other interface an `Agent` awaits mid-turn (`ContextWindow`, stores,
    transports) — deterministic-but-async, never a sync escape hatch.
    """

    @abstractmethod
    async def record_turn(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None: ...

    @abstractmethod
    async def record_tool_call(self) -> None: ...

    @abstractmethod
    async def check(self) -> None:
        """Raise BudgetExceeded if any limit is hit."""
        ...

    @abstractmethod
    async def snapshot(self) -> BudgetSnapshot: ...

    @abstractmethod
    async def restore(self, snapshot: BudgetSnapshot) -> None:
        """Reset internal counters to match a previously saved snapshot.

        Used by Agent.resume() so budget limits keep enforcing correctly
        across a pause/resume boundary instead of silently resetting to zero.
        """
        ...
