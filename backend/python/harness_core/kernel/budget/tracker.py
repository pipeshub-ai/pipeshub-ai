# pyright: basic
from __future__ import annotations

from harness_core.kernel.budget.base import (
    BudgetManager,
    BudgetSnapshot,
)
from harness_core.kernel.budget.pricing import get_pricing
from harness_core.kernel.core.exceptions import BudgetExceeded


class BudgetTracker(BudgetManager):
    """Default budget tracker with configurable limits.

    Each turn is priced when it is recorded: at the model that ran it (the
    turn's own, else the one the tracker was opened for) and at the tier its
    prompt size selects, as the ledger prices a call. Falls back to
    Sonnet-class pricing when the model is None/unknown.
    """

    def __init__(
        self,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_tool_calls: int | None = None,
        max_turns: int | None = None,
        max_cost_usd: float | None = None,
        model: str | None = None,
    ) -> None:
        self._max_input_tokens = max_input_tokens
        self._max_output_tokens = max_output_tokens
        self._max_tool_calls = max_tool_calls
        self._max_turns = max_turns
        self._max_cost_usd = max_cost_usd
        self._model = model

        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._cache_read_tokens: int = 0
        self._cache_write_tokens: int = 0
        self._tool_calls: int = 0
        self._turns: int = 0
        self._cost: float = 0.0

    async def record_turn(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        model: str | None = None,
    ) -> None:
        turn = get_pricing(model or self._model, prompt_tokens=input_tokens).cost_usd(
            input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )
        self._cost += turn
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._cache_read_tokens += cache_read_tokens
        self._cache_write_tokens += cache_write_tokens
        self._turns += 1

    async def record_tool_call(self) -> None:
        self._tool_calls += 1

    async def restore(self, snapshot: BudgetSnapshot) -> None:
        self._input_tokens = snapshot.input_tokens
        if not snapshot.input_includes_cached:
            self._input_tokens += snapshot.cache_read_tokens + snapshot.cache_write_tokens
        self._output_tokens = snapshot.output_tokens
        self._cache_read_tokens = snapshot.cache_read_tokens
        self._cache_write_tokens = snapshot.cache_write_tokens
        self._tool_calls = snapshot.tool_calls
        self._turns = snapshot.turns
        # A snapshot that states no cost is priced as its totals were before turns were priced one by one.
        self._cost = (
            snapshot.cost_usd
            if "cost_usd" in snapshot.model_fields_set
            else get_pricing(self._model).cost_usd(
                self._input_tokens, self._output_tokens, self._cache_read_tokens, self._cache_write_tokens
            )
        )

    async def check(self) -> None:
        """Raise BudgetExceeded if any limit is hit."""
        if self._max_turns is not None and self._turns > self._max_turns:
            raise BudgetExceeded(
                f"max_turns limit exceeded: {self._turns} > {self._max_turns}"
            )
        if self._max_input_tokens is not None and self._input_tokens > self._max_input_tokens:
            raise BudgetExceeded(
                f"max_input_tokens limit exceeded: {self._input_tokens} > {self._max_input_tokens}"
            )
        if self._max_output_tokens is not None and self._output_tokens > self._max_output_tokens:
            raise BudgetExceeded(
                f"max_output_tokens limit exceeded: {self._output_tokens} > {self._max_output_tokens}"
            )
        if self._max_tool_calls is not None and self._tool_calls > self._max_tool_calls:
            raise BudgetExceeded(
                f"max_tool_calls limit exceeded: {self._tool_calls} > {self._max_tool_calls}"
            )
        if self._max_cost_usd is not None and self._cost > self._max_cost_usd:
            raise BudgetExceeded(
                f"max_cost_usd limit exceeded: {self._cost:.6f} > {self._max_cost_usd}"
            )

    async def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cache_read_tokens=self._cache_read_tokens,
            cache_write_tokens=self._cache_write_tokens,
            tool_calls=self._tool_calls,
            turns=self._turns,
            cost_usd=self._cost,
            input_includes_cached=True,
        )
