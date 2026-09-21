"""Token cost from one pinned price table (`RunConfig.pricing`), applied the
same way to every system — never a provider SDK's own table, which lags new
models and would price systems differently."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from benchmarks.harness.config import ModelPrice
from benchmarks.harness.models import CallUsage

_PER = 1_000_000


def call_cost(price: ModelPrice, call: CallUsage) -> float:
    cached = min(call.cached_tokens, call.input_tokens)
    uncached = call.input_tokens - cached
    cached_rate = price.cached_input_per_mtok if price.cached_input_per_mtok is not None else price.input_per_mtok
    long_context = price.long_context_threshold is not None and call.input_tokens > price.long_context_threshold
    in_mult = price.long_context_input_multiplier if long_context else 1.0
    out_mult = price.long_context_output_multiplier if long_context else 1.0
    return (
        (uncached * price.input_per_mtok + cached * cached_rate) * in_mult
        + call.output_tokens * price.output_per_mtok * out_mult
    ) / _PER


def calls_cost(price: ModelPrice | None, calls: Iterable[CallUsage]) -> float | None:
    calls = list(calls)
    if price is None or not calls:
        return None
    return sum(call_cost(price, call) for call in calls)


def price_for(pricing: Mapping[str, ModelPrice], model_name: str) -> ModelPrice | None:
    return pricing.get(model_name)
