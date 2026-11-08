"""Batched compaction with a low-water mark (context/watermark.py): a layer
that compacts goes well below its trigger in one pass, a pass too small to be
worth a batch waits, and the calls in between only append."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.context.compaction_memo import CompactionMemo
from app.agent_loop_lib.context.watermark import (
    LOW_WATER_FRACTION,
    Watermarks,
    watermarks,
)
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CURRENT = "Which practice is my default, and how do I change it?"


def _round(n: int, chars: int, name: str = "search") -> list:
    call = ToolCall(id=f"c{n}", name=name, arguments={"q": n})
    return [
        AssistantMessage(content=f"step {n}", tool_calls=[call]),
        ToolMessage(content=f"result {n} " + "word " * (chars // 5), tool_call_id=call.id),
    ]


def _scope() -> TurnScope:
    run = RunScope(identity=RunContext(role_name="assistant", model="m"), spec=None, runtime=None,
                   goal=Goal(description=CURRENT))
    return TurnScope(run=run, turn_index=0)


async def _shape(
    layer: Callable[..., Awaitable[None]], messages: list, budget: ContextBudget, scope: TurnScope | None = None,
) -> list:
    ctx = ModelCallContext(messages=list(messages), budget=budget, scope=scope)

    async def _next() -> None:
        return None

    await layer(ctx, _next)
    return ctx.messages


def _wire(messages: list) -> list[str]:
    return [json.dumps(m.model_dump(mode="json"), sort_keys=True) for m in messages]


def _current_request_intact(messages: list) -> bool:
    humans = [m for m in messages if isinstance(m, UserMessage) and not m.injected]
    return bool(humans) and humans[-1].content == CURRENT


def test_the_low_water_mark_is_a_fixed_fraction_of_the_budget_below_the_trigger() -> None:
    budget = ContextBudget(max_tokens=10_000)
    marks = watermarks(budget, 5_000)
    assert marks.low_water == 5_000 - int(10_000 * LOW_WATER_FRACTION)
    assert marks.band == int(10_000 * LOW_WATER_FRACTION)
    assert watermarks(budget, 100).low_water == 0


def test_a_pass_runs_only_over_the_trigger_and_when_it_frees_a_whole_band() -> None:
    marks = Watermarks(trigger=5_000, low_water=2_000)
    assert not marks.worth_compacting(5_000, 10_000)
    assert not marks.worth_compacting(6_000, 2_999)
    assert marks.worth_compacting(6_000, 3_000)
    # A layer set to fire at any size has no band and compacts on every call.
    assert Watermarks(trigger=0, low_water=0).worth_compacting(1, -5)


@pytest.mark.asyncio
async def test_a_batch_clears_down_to_the_low_water_mark_and_keeps_the_request() -> None:
    budget = ContextBudget(max_tokens=20_000)
    marks = watermarks(budget, budget.trigger_tokens(0.5))
    messages = [UserMessage(content=CURRENT)]
    for n in range(12):
        messages.extend(_round(n, 4_000))
    assert count_tokens(messages) > marks.trigger

    shaped = await _shape(shape_tool_result_clearing(keep_last_n_turns=2), messages, budget, _scope())

    assert count_tokens(shaped) <= marks.low_water
    assert _current_request_intact(shaped)
    assert shaped[-4:] == messages[-4:], "the last two turns are the floor and stay verbatim"


@pytest.mark.asyncio
async def test_a_pass_too_small_for_a_batch_waits_and_the_next_calls_only_append() -> None:
    """Once the kept turns alone sit over the trigger, each call ages one
    more result out. That result waits until enough have aged to free a
    band, so the calls in between resend the previous call as their prefix."""
    budget = ContextBudget(max_tokens=20_000)
    layer = shape_tool_result_clearing(keep_last_n_turns=3)
    scope = _scope()
    stored = [UserMessage(content=CURRENT)]
    previous: list[str] | None = None
    breaks = 0
    for n in range(20):
        stored.extend(_round(n, 9_000))
        shaped = await _shape(layer, stored, budget, scope)
        assert _current_request_intact(shaped)
        wire = _wire(shaped)
        if previous is not None and wire[: len(previous)] != previous:
            breaks += 1
        previous = wire
    # One result is about a tenth of this budget and the band three tenths:
    # before, every call from the trigger on cleared one result (12 breaks).
    assert breaks <= 6


@pytest.mark.asyncio
async def test_eviction_goes_to_the_low_water_mark_and_stays_evicted() -> None:
    budget = ContextBudget(max_tokens=6_000)
    low_water = watermarks(budget, budget.effective_max_tokens).low_water
    layer = shape_sliding_window()
    scope = _scope()
    stored = [UserMessage(content=CURRENT)]
    for n in range(8):
        stored.extend(_round(n, 3_000))
    assert count_tokens(stored) > budget.effective_max_tokens

    first = await _shape(layer, stored, budget, scope)
    assert count_tokens(first) <= low_water
    assert _current_request_intact(first)

    stored.extend(_round(8, 1_000))
    second = await _shape(layer, stored, budget, scope)
    assert second[: len(first)] == first, "the next call appends instead of evicting one more group"
    assert count_tokens(second) <= budget.effective_max_tokens


def test_a_message_another_layer_compacted_is_held_as_it_is() -> None:
    memo = CompactionMemo()
    messages = [UserMessage(content=CURRENT), *_round(0, 100), *_round(1, 100)]
    memo.remember("loop_compaction", messages[1:3], [UserMessage(content="summary", injected=True)])
    memo.remember("tool_result_clearing", [messages[4]], [messages[4]])

    assert memo.held_by_others("tool_result_clearing", messages) == frozenset({1, 2})
    assert memo.held_by_others("loop_compaction", messages) == frozenset({4})


def test_an_eviction_is_recalled_as_no_message() -> None:
    memo = CompactionMemo()
    [call, result] = _round(0, 100)
    memo.remember("sliding_window", [call], [])
    assert memo.recall("sliding_window", call) == ()
    assert memo.recall("sliding_window", result) is None
    assert memo.has_compacted("sliding_window")
    assert not memo.has_compacted("loop_compaction")
