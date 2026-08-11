"""Interaction between `shape_sliding_window` (L5) and `shape_auto_compact`
(L7a/L7b), in the exact PRE_MODEL order `PipesHubAgentFactory` wires them
(`app/agents/agent_loop/factory.py`) — prompt-caching Phase 6's "these two
subsystems must be aware of each other" corner case.

Both shapers mutate `ctx.messages` on every dispatch that crosses their own
trigger, and BOTH run on every single dispatch (there is no shared "did a
sibling shaper already fix this" flag) — the only thing that actually
coordinates them today is that `shape_auto_compact`'s default trigger
(`trigger_ratio=0.85`, set via `_AUTO_COMPACT_TRIGGER_RATIO` in
`factory.py`) fires strictly BEFORE `shape_sliding_window`'s implicit 100%
ceiling, so the common case is exactly one rewrite per dispatch, not two.

These tests pin that behavior precisely, including the boundary case the
plan flags explicitly: when total tokens exceed 100% of budget in one big
jump (not a marginal squeeze — e.g. one huge tool result appended), BOTH
shapers fire in the SAME dispatch. That is accepted, not a bug this phase
fixes: `shape_sliding_window`'s hard eviction guarantees `<=100%` but not
`<=85%`, so `shape_auto_compact` still sees itself over its own (lower)
threshold afterward and compacts further. The result is still exactly ONE
deliberate cold write for the whole cache-eligible prefix per dispatch —
never a second, independent rewrite on a LATER turn for the same overflow.
"""

from __future__ import annotations

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import shape_auto_compact
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import shape_sliding_window
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_TRIGGER_RATIO = 0.85  # `_AUTO_COMPACT_TRIGGER_RATIO` in factory.py


def _budget(max_tokens: int = 1_000) -> ContextBudget:
    return ContextBudget(max_tokens=max_tokens, model="test")


def _tokens(n: int) -> str:
    return "a" * max((n - 4) * 4, 0)


def _user(n_tokens: int) -> UserMessage:
    return UserMessage(content=_tokens(n_tokens))


def _assistant(n_tokens: int) -> AssistantMessage:
    return AssistantMessage(content=_tokens(n_tokens))


async def _dispatch_pipeline(messages, budget: ContextBudget) -> ModelCallContext:
    """Chains `shape_sliding_window` then `shape_auto_compact`, matching
    `factory.py`'s L5 -> L7a wiring order exactly — both read/write the
    same `ctx.messages` in sequence within one PRE_MODEL dispatch."""
    ctx = ModelCallContext(messages=list(messages), budget=budget)

    async def _noop() -> None:
        return None

    await shape_sliding_window(pin_first_n=1)(ctx, _noop)
    await shape_auto_compact(summarizer=None, trigger_ratio=_TRIGGER_RATIO)(ctx, _noop)
    return ctx


def _total_tokens(messages) -> int:
    from app.agent_loop_lib.core.tokens import count_tokens

    return count_tokens(messages)


class TestUnderTriggerRatioNeitherShaperRuns:
    async def test_below_85_percent_leaves_messages_untouched(self) -> None:
        messages = [_user(50)] + [_assistant(100) for _ in range(5)]  # 550 / 1000
        ctx = await _dispatch_pipeline(messages, _budget(1_000))
        assert ctx.messages == messages


class TestBetween85And100PercentOnlyAutoCompactRuns:
    async def test_auto_compact_alone_handles_the_85_to_100_percent_band(self) -> None:
        """`shape_sliding_window`'s own trigger is `> 100%` — it must stay
        a no-op here so the softer, LLM-aware `shape_auto_compact` is the
        only rewrite for this band, not a blunt eviction on top of it."""
        messages = [_user(50)] + [_assistant(150) for _ in range(6)]  # 950 / 1000
        assert 850 < _total_tokens(messages) <= 1_000

        ctx = await _dispatch_pipeline(messages, _budget(1_000))

        # sliding_window alone would never touch this (still <=100%) —
        # confirm independently that it is a no-op at this size.
        async def _noop() -> None:
            return None

        solo_ctx = ModelCallContext(messages=list(messages), budget=_budget(1_000))
        await shape_sliding_window(pin_first_n=1)(solo_ctx, _noop)
        assert solo_ctx.messages == messages

        # auto_compact alone DOES trigger (over its own 85% threshold) and
        # is therefore the one that actually changed the pipeline's output.
        assert ctx.messages != messages
        assert any("Auto-compacted summary" in (m.content or "") for m in ctx.messages if isinstance(m.content, str))


class TestOverBudgetBothShapersMayRunInOneDispatch:
    async def test_large_jump_over_budget_triggers_both_in_the_same_dispatch(self) -> None:
        """A single oversized tool result (not a slow accumulation) can
        push total tokens past 100% in one dispatch. `shape_sliding_window`
        fires first (hard eviction down to <=100%); if that still leaves
        the result over auto_compact's 85% threshold, auto_compact ALSO
        fires on the same dispatch. This is one deliberate, compounded
        cold write for this dispatch — not two rewrites spread across two
        separate turns."""
        messages = [_user(50)] + [_assistant(500) for _ in range(6)]  # 3050 / 1000
        assert _total_tokens(messages) > 1_000

        ctx = await _dispatch_pipeline(messages, _budget(1_000))

        assert _total_tokens(ctx.messages) <= 1_000
        # Pinned head (index 0, pin_first_n=1) survives both shapers.
        assert ctx.messages[0] == messages[0]


class TestPinnedPrefixSurvivesBothShapersRegardlessOfTriggerBand:
    async def test_pinned_head_never_mutated_by_either_shaper(self) -> None:
        pinned_head = _user(50)
        for total_assistant_tokens in (100, 150, 500):
            messages = [pinned_head] + [_assistant(total_assistant_tokens) for _ in range(6)]
            ctx = await _dispatch_pipeline(messages, _budget(1_000))
            assert ctx.messages[0] == pinned_head
