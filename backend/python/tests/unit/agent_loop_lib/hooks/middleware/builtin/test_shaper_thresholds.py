"""Where the ratio-triggered context shapers fire, by context window.

The ratios were tuned when every budget was the 128K default window less the
8K output reserve. A 300K window must not be cleared at the same fraction of
its far larger budget; a 128K window must fire exactly where it did.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    shape_artifact_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import shape_auto_compact
from app.agent_loop_lib.hooks.middleware.builtin.deterministic_compact import (
    shape_deterministic_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    shape_loop_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_TURNS = 8
_CURRENT_TURN = 10  # a multiple of loop_compaction's default 5, past artifact keep-last-2

# (shaper factory, the trigger ratio it is registered with by default)
_SHAPERS = {
    "artifact_compaction": (lambda: shape_artifact_compaction(keep_last_n_turns=2), 0.5),
    "tool_result_clearing": (lambda: shape_tool_result_clearing(), 0.5),
    "loop_compaction": (lambda: shape_loop_compaction(), 0.6),
    "deterministic_compact": (lambda: shape_deterministic_compact(), 0.85),
    "auto_compact": (lambda: shape_auto_compact(), 0.85),
}


def _conversation(total_tokens: int) -> list:
    per_result_chars = (total_tokens // _TURNS) * 4
    messages: list = [UserMessage(content="find it")]
    for turn in range(_TURNS):
        call_id = f"c{turn}"
        messages.append(AssistantMessage(tool_calls=[ToolCall(id=call_id, name="search", arguments={})]))
        messages.append(ToolMessage(
            content="r" * per_result_chars,
            tool_call_id=call_id,
            artifact_meta=ToolMessageMeta(
                artifact_id=f"a{turn}", summary="rows", original_token_count=per_result_chars // 4,
                turn_index=turn,
            ),
        ))
    return messages


async def _fires(name: str, budget: ContextBudget, total_tokens: int) -> bool:
    factory, _ = _SHAPERS[name]
    messages = _conversation(total_tokens)
    ctx = ModelCallContext(messages=list(messages), budget=budget, turn_index=_CURRENT_TURN)

    async def _next() -> None:
        return None

    await factory()(ctx, _next)
    return ctx.messages != messages


def _budget(window: int) -> ContextBudget:
    return ContextBudget.for_model(None, context_window=window)


def _scaled(budget: ContextBudget, ratio: float) -> int:
    tokens = budget.effective_max_tokens
    return int(max(tokens * ratio, tokens - (1 - ratio) * 120_000))


class TestScaledThreshold:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(_SHAPERS))
    async def test_300k_window_does_not_fire_at_the_old_fraction(self, name) -> None:
        budget = _budget(300_000)
        ratio = _SHAPERS[name][1]
        old = budget.effective_max_tokens * ratio
        scaled = _scaled(budget, ratio)
        assert scaled > old
        between = int((old + scaled) / 2)
        assert old < count_tokens(_conversation(between)) < scaled

        assert not await _fires(name, budget, between)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(_SHAPERS))
    async def test_300k_window_fires_past_the_scaled_threshold(self, name) -> None:
        budget = _budget(300_000)
        above = _scaled(budget, _SHAPERS[name][1]) + 4_000
        assert count_tokens(_conversation(above)) < budget.effective_max_tokens

        assert await _fires(name, budget, above)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(_SHAPERS))
    async def test_128k_window_fires_where_it_always_did(self, name) -> None:
        budget = _budget(128_000)
        old = int(budget.effective_max_tokens * _SHAPERS[name][1])
        assert _scaled(budget, _SHAPERS[name][1]) == old

        assert await _fires(name, budget, old + 2_000)
        assert not await _fires(name, budget, old - 2_000)


class TestTriggerTokens:
    def test_budget_at_or_below_the_tuned_one_keeps_the_fraction(self) -> None:
        for max_tokens in (10_000, 60_000, 120_000):
            budget = ContextBudget(max_tokens=max_tokens)
            assert budget.trigger_tokens(0.5) == max_tokens // 2

    def test_larger_budget_keeps_the_tuned_headroom(self) -> None:
        budget = ContextBudget(max_tokens=292_000)
        assert budget.trigger_tokens(0.5) == 292_000 - 60_000
        assert budget.trigger_tokens(0.85) == 292_000 - 18_000

    def test_large_prompt_on_a_large_window_is_unchanged_below_the_tuned_budget(self) -> None:
        # 200K window, 90K of system prompt and tool schemas: 102K left.
        budget = ContextBudget.for_model(None, context_window=200_000, reserved_prompt_tokens=90_000)
        assert budget.trigger_tokens(0.5) == budget.effective_max_tokens // 2

    def test_order_of_ratios_is_kept(self) -> None:
        budget = ContextBudget(max_tokens=900_000)
        assert budget.trigger_tokens(0.5) < budget.trigger_tokens(0.6) < budget.trigger_tokens(0.85)
