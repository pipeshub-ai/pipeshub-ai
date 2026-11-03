from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.agent_loop_lib.core.messages import Message
from app.agent_loop_lib.modules.providers.budget.pricing import get_context_window

# The budget the shapers' trigger ratios were tuned on: the 128K default window
# less the 8K output reserve, before the prompt was reserved out of it.
_RATIO_TUNED_BUDGET = 128_000 - 8_000


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

    `max_tokens` is what the shaper pipeline aims to fit the conversation
    messages under before the call goes out — derived from the model's real
    context window (see modules/providers/budget/pricing.py) minus headroom for
    the model's own output and minus `reserved_prompt_tokens`, the system
    prompt and tool schemas sent beside the messages, not an arbitrary flat
    number.

    `reserved_artifact_tokens` carves out a dedicated budget for artifact
    content that may be injected back into context on demand (via
    ``retrieve_artifact_content``).  When most tools declare
    ``result_schema``, this can be lowered significantly because schema
    proxies replace full data in context.
    """

    max_tokens: int
    model: str | None = None
    reserved_output_tokens: int = 8_000
    reserved_artifact_tokens: int = 0
    reserved_prompt_tokens: int = 0

    @property
    def effective_max_tokens(self) -> int:
        """Budget available to the shaping pipeline after all reserves."""
        return max(self.max_tokens - self.reserved_artifact_tokens, min(1_000, self.max_tokens))

    @property
    def hard_limit_tokens(self) -> int:
        """Message tokens past which the request no longer fits the window.

        Between the budget and this limit the request still fits; it only
        leaves the reply less than its reserve, so it is sent.
        """
        return self.max_tokens + self.reserved_output_tokens

    def trigger_tokens(self, ratio: float) -> int:
        """Message tokens above which a shaper set to fire at `ratio` of the
        budget acts.

        Up to the tuned budget this is that fraction. Past it the headroom the
        ratio left there is kept instead: what has to fit in it is the next
        turn's tool results, which are capped per result and do not grow with
        the window, so a 300K window is not cleared when half of it is free.
        """
        budget = self.effective_max_tokens
        return int(max(budget * ratio, budget - (1 - ratio) * _RATIO_TUNED_BUDGET))

    @classmethod
    def for_model(
        cls,
        model: str | None,
        reserved_output_tokens: int = 8_000,
        reserved_artifact_tokens: int = 0,
        *,
        context_window: int | None = None,
        reserved_prompt_tokens: int = 0,
    ) -> "ContextBudget":
        window = context_window if context_window and context_window > 0 else get_context_window(model)
        prompt = max(reserved_prompt_tokens, 0)
        left = window - prompt
        # A small window cannot give 8k to the reply: at most a quarter of it,
        # and at most half of what the system prompt and tool schemas leave, so
        # prompt + messages + reply fit whenever the prompt itself does. A prompt
        # over the window leaves 1 token; the provider rejects that request with
        # its own context-length error, which callers already explain.
        reserve = max(min(reserved_output_tokens, window // 4, left // 2), 0)
        max_tokens = max(left - reserve, 1)
        return cls(
            max_tokens=max_tokens,
            model=model,
            reserved_output_tokens=reserve,
            reserved_artifact_tokens=reserved_artifact_tokens,
            reserved_prompt_tokens=prompt,
        )
