"""What a model call costs a run, and what to reserve before it (pure).

Prices come from ``kernel.budget.pricing`` (the one table the loop's budget
tracker uses). USD is charged only on routes the org or user pays through
PipesHub (§4.6.2); tokens always count. Amounts are the ledger's (P6.5).

The token dimension (``budgets.maxTokens``) counts a prompt token a cache served
at the provider's cache-read price ratio, not as a fresh token (ENGBUDGET): an
agent resends its whole history on every call, so counting each resent, cached
token in full charged one Codex child 773k tokens for USD 0.019 of work and
stopped it on the token cap with USD 4.95 of 5 left. USD is the provider's own
arithmetic either way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.access.token_count import counter_for
from harness_core.access.types import MICRO_USD
from harness_core.budget.types import Amount, LedgerUsage
from harness_core.kernel.budget.pricing import (
    MODEL_PRICING,
    get_pricing,
    max_output_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import TokenUsage
    from harness_core.kernel.core.tool_schema import ToolSchema


def priced(model: str) -> bool:
    """False when ``get_pricing`` falls back to its default prices for ``model``."""
    return model in MODEL_PRICING


def _micros(usd: float) -> int:
    # Rounded to whole micro-dollars first so float noise never rounds a charge up by one.
    return math.ceil(round(usd * MICRO_USD, 6))


def input_estimate(
    messages: Sequence[Message], system: str | None, tools: Sequence[ToolSchema], model: str | None = None
) -> int:
    """A call's input tokens before it is made, by ``model``'s counter (``token_count``; the provider reports the
    truth after): what its reservation is made on, and what it is charged when the provider reports nothing."""
    return counter_for(model).call_input(messages, system, tools)


def output_estimate(text: str, model: str | None = None) -> int:
    """The output tokens of what a call streamed, by the same counter as its input."""
    return counter_for(model).text(text)


def cache_read_weight(model: str, *, prompt_tokens: int = 0) -> float:
    """What one cached prompt token weighs against a fresh one: the provider's cache-read price over its input
    price (0.1 for OpenAI and Anthropic today), never more than 1."""
    pricing = get_pricing(model, prompt_tokens=prompt_tokens)
    if pricing.input_price_per_mtok <= 0:
        return 1.0
    return min(1.0, pricing.cache_read_price_per_mtok / pricing.input_price_per_mtok)


def budget_tokens(usage: TokenUsage, model: str) -> int:
    """A call's charge on the token dimension: fresh and cache-written prompt tokens and output in full, cache
    reads at ``cache_read_weight``."""
    weight = cache_read_weight(model, prompt_tokens=usage.input_tokens)
    return usage.uncached_input_tokens + usage.cache_write_tokens + _weighed(usage.cache_read_tokens, weight) + usage.output_tokens


def _weighed(tokens: int, weight: float) -> int:
    # Rounded first, as ``_micros`` is: a ratio such as 0.3 / 3.0 must not round a charge up by one.
    return math.ceil(round(tokens * weight, 6))


def usage_spend(usage: TokenUsage, model: str, *, charges_usd: bool) -> Amount:
    tokens = budget_tokens(usage, model)
    if not charges_usd:
        return Amount(0, tokens)
    pricing = get_pricing(model, prompt_tokens=usage.input_tokens)
    cost = pricing.cost_usd(usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens)
    return Amount(_micros(cost), tokens)


def reported_nothing(usage: TokenUsage) -> bool:
    """A response that carries no provider usage (a stream the harness cut, a transport that reports none): its
    spend is the estimate, never zero."""
    return usage.input_tokens == 0 and usage.output_tokens == 0


def ledger_usage(
    usage: TokenUsage | None,
    *,
    input_estimate: int,
    streamed: str,
    model: str | None = None,
    streamed_tokens: int | None = None,
) -> LedgerUsage:
    """What a settled call tells the ledger it used: the provider's counts, or, when it reported none, the same
    estimate its charge was made from (so a run's ledger usage and its charge never disagree). ``streamed_tokens``:
    ``streamed`` already counted, off the event loop."""
    if usage is None or reported_nothing(usage):
        produced = streamed_tokens if streamed_tokens is not None else output_estimate(streamed, model)
        return LedgerUsage(input_estimate, produced)
    return LedgerUsage(usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens)


def estimate_spend(input_tokens: int, output_tokens: int, model: str, *, charges_usd: bool) -> Amount:
    tokens = input_tokens + output_tokens
    if not charges_usd:
        return Amount(0, tokens)
    return Amount(_micros(get_pricing(model, prompt_tokens=input_tokens).cost_usd(input_tokens, output_tokens)), tokens)


@dataclass(frozen=True, slots=True)
class OutputAllowance:
    """How many output tokens a call reserves. A call's output is bounded exactly by the limit it sent on the wire
    (P4.17), else by the model's published maximum; ``default_tokens`` stands in for a model the table does not
    know; ``cap_tokens`` trades that bound for smaller reservations when set."""

    default_tokens: int = 32_768
    cap_tokens: int | None = None

    def tokens(self, model: str, limit: int | None = None) -> int:
        known = max_output_tokens(model)
        allowance = known if known is not None else self.default_tokens
        if limit is not None:
            allowance = min(allowance, limit)
        return min(allowance, self.cap_tokens) if self.cap_tokens is not None else allowance


def reservation_for(
    input_tokens: int,
    model: str,
    allowance: OutputAllowance,
    *,
    charges_usd: bool,
    output_limit: int | None = None,
) -> tuple[Amount, Amount]:
    """(requested, minimum) of a call's reservation: its input plus the output allowance (at most the call's own
    output limit), and its input alone (E3's pre-check: a call whose input the run cannot pay is refused).

    The minimum's tokens are the least ``budget_tokens`` can charge that input: all of it read from the cache.
    Sized at the full prompt instead, the minimum refused a call the ledger would have charged a tenth of (in one
    run, a 76k-token prompt, 69k of it cached, refused with 25k tokens left). The money minimum stays the input at
    the fresh price, so a run never starts a call its money may not cover."""
    full = estimate_spend(input_tokens, 0, model, charges_usd=charges_usd)
    floor = _weighed(input_tokens, cache_read_weight(model, prompt_tokens=input_tokens))
    return (
        estimate_spend(input_tokens, allowance.tokens(model, output_limit), model, charges_usd=charges_usd),
        Amount(full.usd_micros, floor),
    )


__all__ = [
    "OutputAllowance",
    "budget_tokens",
    "cache_read_weight",
    "estimate_spend",
    "input_estimate",
    "priced",
    "reported_nothing",
    "reservation_for",
    "usage_spend",
]
