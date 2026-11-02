"""The per-run compaction memo (CE-12) and the eviction order of protected
tool groups (CE-13)."""

from __future__ import annotations

import logging

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.context.compaction_memo import (
    CompactionMemo,
    assemble,
    plan_runs,
    reapply_replacements,
)
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def _group(n: int, name: str = "search", chars: int = 10) -> list:
    return [
        AssistantMessage(content=f"call {n}", tool_calls=[ToolCall(id=f"c{n}", name=name, arguments={})]),
        ToolMessage(content=f"result {n} " + "x" * chars, tool_call_id=f"c{n}"),
    ]


def _summary(text: str) -> UserMessage:
    return UserMessage(content=text, injected=True, compacted_by="test")


def test_a_message_replaced_once_is_replaced_the_same_way_again() -> None:
    memo = CompactionMemo()
    messages = [UserMessage(content="q"), *_group(1)]
    cleared = messages[2].model_copy(update={"content": "cleared", "compacted_by": "test"})
    memo.remember("test", [messages[2]], [cleared])

    again, replaced = reapply_replacements(memo, "test", list(messages))

    assert replaced == 1
    assert again[2] is cleared
    assert reapply_replacements(memo, "other", list(messages))[1] == 0
    assert reapply_replacements(memo, "test", list(messages), keep=frozenset({2}))[1] == 0


def test_a_run_reuses_its_earlier_summaries_and_leaves_the_rest() -> None:
    memo = CompactionMemo()
    messages = [*_group(1), *_group(2), *_group(3)]
    first = _summary("S1")
    memo.remember("test", messages[0:4], [first])

    plans = plan_runs(memo, "test", messages, [range(0, 6)])

    assert [(hit.start, hit.stop) for hit in plans[0].hits] == [(0, 4)]
    assert plans[0].rest == range(4, 6)
    assert assemble(messages, plans) == [first, *messages[4:]]
    second = _summary("S2")
    assert assemble(messages, plans, {4: [second]}) == [first, second]


def test_a_memoized_run_that_would_split_a_tool_group_is_not_reused() -> None:
    memo = CompactionMemo()
    messages = [*_group(1), *_group(2)]
    memo.remember("test", messages[0:3], [_summary("would orphan result 2")])

    plans = plan_runs(memo, "test", messages, [range(0, 4)])

    assert plans[0].hits == ()
    assert plans[0].rest == range(0, 4)


def test_without_a_memo_every_run_is_new() -> None:
    messages = [*_group(1)]
    plans = plan_runs(None, "test", messages, [range(0, 2)])
    assert plans[0].hits == () and plans[0].rest == range(0, 2)


@pytest.mark.asyncio
async def test_protected_tool_groups_are_evicted_last_and_the_eviction_is_logged(caplog) -> None:
    """The sliding window keeps protected tool groups while anything else can
    go, then evicts them oldest first rather than send a request that does not
    fit; that degraded path is logged at WARNING."""
    messages = [
        *_group(0, name="fetch", chars=4_000),
        *_group(1, chars=4_000),
        UserMessage(content="current question"),
        *_group(2, name="fetch", chars=4_000),
    ]
    ctx = ModelCallContext(messages=list(messages), budget=ContextBudget(max_tokens=1_500, model="test"))

    async def _next() -> None:
        return None

    with caplog.at_level(logging.WARNING):
        await shape_sliding_window(protected_tools=frozenset({"fetch"}))(ctx, _next)

    sent_calls = [m.tool_call_id for m in ctx.messages if isinstance(m, ToolMessage)]
    assert sent_calls == ["c2"]
    assert any(m.content == "current question" for m in ctx.messages if isinstance(m, UserMessage))
    assert "evicted 2 protected tool message(s)" in caplog.text
