"""`shape_sliding_window` — cache-boundary interaction (prompt-caching Phase 6).

Every message before the LAST TWO cache-breakpoint-eligible messages
participates in a provider's cached prefix (see `AnthropicCacheStrategy`'s
advancing last-and-second-to-last placement, and the LangChain
`cache_control` invoke kwarg which lands on the last cacheable block).
Evicting ANYTHING before that point rewrites every byte after it, forcing
a full cold write on the very next call — there is no way around that
with a literal-byte-match cache, so the goal is not to avoid it but to
make sure it only happens when the token budget actually requires it,
never as an incremental "shave a few tokens" squeeze that would force a
cold write today for a marginal saving and then ANOTHER cold write next
turn once the window fills again.

These tests pin the two guarantees the current design already provides:
1. Under budget => zero eviction, zero byte change (no gratuitous cache
   invalidation).
2. Over budget => resolved in one shaper pass, down to fully under
   budget — a single deliberate cold write, not a slow drip of
   marginal ones across several turns.
3. `pin_first_n` messages are never touched, so at least that portion of
   the prefix survives eviction forever regardless of how much history
   accumulates after it.
"""

from __future__ import annotations

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, SystemMessage, ToolCall, ToolMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import shape_sliding_window
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def _budget(max_tokens: int = 1_000) -> ContextBudget:
    return ContextBudget(max_tokens=max_tokens, model="test")


def _tokens(n: int) -> str:
    """Text whose estimated token count (per `count_message_tokens`) is
    exactly `n` — overhead(4) + chars//4, so chars = (n - 4) * 4."""
    return "a" * max((n - 4) * 4, 0)


def _user(n_tokens: int) -> UserMessage:
    return UserMessage(content=_tokens(n_tokens))


def _assistant(n_tokens: int) -> AssistantMessage:
    return AssistantMessage(content=_tokens(n_tokens))


async def _run(middleware, messages, budget=None):
    ctx = ModelCallContext(messages=list(messages), budget=budget or _budget())
    called = False

    async def next_fn():
        nonlocal called
        called = True

    await middleware(ctx, next_fn)
    assert called
    return ctx


class TestNoGratuitousEviction:
    async def test_under_budget_leaves_every_message_untouched(self) -> None:
        messages = [_user(50), _assistant(50), _user(50)]
        ctx = await _run(shape_sliding_window(), messages, budget=_budget(1_000))
        assert ctx.messages == messages

    async def test_exactly_at_budget_evicts_nothing(self) -> None:
        """The trigger is a strict `>`, not `>=` — landing exactly on the
        ceiling must not itself count as "over budget" and force a cold
        write for zero actual overflow."""
        messages = [_user(400), _assistant(600)]
        ctx = await _run(
            shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000)
        )
        assert ctx.messages == messages


class TestOverBudgetResolvesInOneShaperPass:
    async def test_single_pass_lands_fully_under_budget(self) -> None:
        """A second call to the transport for the SAME turn must never see
        a still-over-budget context — that would mean another eviction
        (another cold write) is still pending rather than having happened
        once, already, here."""
        messages = [_user(200)] + [_assistant(200) for _ in range(10)]
        ctx = await _run(shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000))
        total = sum_tokens(ctx.messages)
        assert total <= 1_000

    async def test_eviction_is_oldest_first_not_newest_first(self) -> None:
        """The most-recently-written turns are what the advancing cache
        breakpoint actually targets (last/second-to-last message) — an
        eviction policy that dropped recent turns instead of old ones
        would defeat caching far more badly by invalidating content the
        provider was about to read from cache on THIS very call."""
        messages = [_user(100), _assistant(400), _assistant(400), _assistant(400)]
        ctx = await _run(shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000))
        # The oldest non-pinned message (index 1) is evicted first; the
        # newest (index 3) must survive.
        assert messages[3] in ctx.messages
        assert ctx.messages[0] == messages[0]


class TestPinnedPrefixNeverTouched:
    async def test_pinned_head_is_byte_identical_regardless_of_downstream_eviction(self) -> None:
        pinned_head = _user(50)
        messages = [pinned_head] + [_assistant(300) for _ in range(10)]
        ctx = await _run(shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000))
        assert ctx.messages[0] == pinned_head

    async def test_system_message_is_pinned_even_beyond_pin_first_n(self) -> None:
        system = SystemMessage(content=_tokens(50))
        messages = [_user(50), system] + [_assistant(300) for _ in range(10)]
        ctx = await _run(shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000))
        assert system in ctx.messages

    async def test_explicitly_pinned_message_beyond_pin_first_n_survives(self) -> None:
        pinned_note = _user(50)
        pinned_note.pinned = True
        messages = [_user(50), pinned_note] + [_assistant(300) for _ in range(10)]
        ctx = await _run(shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000))
        assert pinned_note in ctx.messages


class TestToolCallPairingSurvivesEviction:
    async def test_assistant_and_its_tool_results_are_evicted_together(self) -> None:
        """A cache-breaking rewrite is bad; a rewrite that ALSO orphans a
        tool_call/tool_result pair is a provider-rejected 400, strictly
        worse — the atomic-group eviction must hold even under the
        cache-boundary lens this test suite is otherwise about."""
        call = ToolCall(id="tc_1", name="some_tool", arguments={})
        assistant_with_call = AssistantMessage(content=_tokens(300), tool_calls=[call])
        tool_result = ToolMessage(content=_tokens(300), tool_call_id="tc_1")
        messages = [
            _user(50), assistant_with_call, tool_result,
            *[_assistant(300) for _ in range(3)],
        ]
        ctx = await _run(shape_sliding_window(pin_first_n=1), messages, budget=_budget(1_000))
        remaining_ids = {getattr(m, "tool_call_id", None) for m in ctx.messages}
        remaining_has_assistant_call = any(
            getattr(m, "tool_calls", None) for m in ctx.messages
        )
        # Either both the call and its result are gone, or both remain —
        # never one without the other.
        if remaining_has_assistant_call:
            assert "tc_1" in remaining_ids
        else:
            assert "tc_1" not in remaining_ids


def sum_tokens(messages) -> int:
    from app.agent_loop_lib.core.tokens import count_message_tokens

    return sum(count_message_tokens(m) for m in messages)
