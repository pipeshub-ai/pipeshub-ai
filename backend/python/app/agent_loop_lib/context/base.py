from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.agent_loop_lib.core.messages import Message
from app.agent_loop_lib.modules.providers.budget.pricing import get_context_window


class ContextWindow(ABC):
    """Manages the active message window passed to the LLM.

    Every method is `async` — not because today's implementations
    (`ContextManager`, `SlidingWindowContext`) do any I/O, but because the
    interface must accommodate ones that do (a durable, storage-backed
    conversation history shared across process restarts) without a
    breaking signature change later. `Agent` already `await`s every other
    collaborator it calls mid-turn (`BudgetManager`, stores, the `Model`
    itself); an in-memory-only synchronous `add()`/`messages()` here would
    be the one inconsistent exception.

    Named `ContextWindow` (not `ContextProvider`): this interface owns and
    shapes the ACTIVE window of messages sent to the model, not just
    "provides" some unspecified something — the name now matches what the
    two implementations (full-history / sliding-eviction) actually do.
    """

    @abstractmethod
    async def add(self, message: Message) -> None: ...

    @abstractmethod
    async def messages(self) -> list[Message]: ...

    @abstractmethod
    async def token_count(self) -> int: ...

    @abstractmethod
    async def clear(self) -> None: ...


class ContextBudget(BaseModel):
    """Passed to every `pre_model` shaper hook alongside the message list.

    `max_tokens` is what the shaper pipeline aims to fit under before the
    call goes out — derived from the model's real context window (see
    modules/providers/budget/pricing.py) minus headroom for the model's own output, not an
    arbitrary flat number.
    """

    max_tokens: int
    model: str | None = None
    reserved_output_tokens: int = 8_000

    @classmethod
    def for_model(cls, model: str | None, reserved_output_tokens: int = 8_000) -> "ContextBudget":
        window = get_context_window(model)
        max_tokens = max(window - reserved_output_tokens, 1_000)
        return cls(max_tokens=max_tokens, model=model, reserved_output_tokens=reserved_output_tokens)
