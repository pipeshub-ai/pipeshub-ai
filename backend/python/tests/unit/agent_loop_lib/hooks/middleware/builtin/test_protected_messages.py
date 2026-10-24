"""Every compaction layer keeps what `context/protection.py` protects.

Each layer is forced to fire on a chat where the current question comes after
an earlier turn: the layout in which keeping "the first message" kept the
earlier question and lost the current one.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import shape_auto_compact
from app.agent_loop_lib.hooks.middleware.builtin.deterministic_compact import (
    shape_deterministic_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    shape_loop_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.builtin.synthesis_guard import (
    shape_synthesis_guard,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

QUESTION_A = "QUESTION A: how do I set up SMS appointment reminders?"
QUESTION_B = "QUESTION B: which practice do I log into, and how do I set a default?"
PLAN = "PLAN:\n" + "\n".join(f"{i}. step {i}: read the next manual carefully" for i in range(60))

LAYERS = {
    "L4 loop_compaction": lambda: shape_loop_compaction(keep_recent=2, trigger_ratio=0.0),
    "L5 sliding_window": shape_sliding_window,
    "L6 deterministic_compact": lambda: shape_deterministic_compact(trigger_ratio=0.0, keep_last_n_messages=2),
    "L7 auto_compact": lambda: shape_auto_compact(trigger_ratio=0.0, max_tail_ratio=0.1),
}


def _chat(*, plan: bool = False, rounds: int = 8) -> list:
    messages: list = [
        UserMessage(content=QUESTION_A + " " + "a" * 2_000),
        AssistantMessage(content=[TextPart(text="Reminders: enable them in Practice Settings. " + "b" * 2_000)]),
        UserMessage(content=QUESTION_B),
    ]
    if plan:
        messages.append(UserMessage(content=PLAN, injected=True, pinned=True))
    for i in range(rounds):
        messages.append(AssistantMessage(
            content=[TextPart(text=f"Reading manual {i}.")],
            tool_calls=[ToolCall(id=f"c{i}", name="knowledgegraph__fetch_record", arguments={"id": i})],
        ))
        messages.append(ToolMessage(content=f"manual {i} " + "m" * 3_000, tool_call_id=f"c{i}"))
    return messages


async def _run(layer_name: str, messages: list, budget_tokens: int = 3_000) -> list:
    ctx = ModelCallContext(
        messages=list(messages),
        budget=ContextBudget(max_tokens=budget_tokens, model="test"),
        turn_index=10,
    )

    async def _next() -> None:
        return None

    await LAYERS[layer_name]()(ctx, _next)
    return ctx.messages


def _humans(messages: list) -> list[str]:
    return [m.content for m in messages if isinstance(m, UserMessage) and not m.injected]


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", LAYERS)
async def test_layer_fires_on_this_chat(layer) -> None:
    messages = _chat()
    sent = await _run(layer, messages)
    assert count_tokens(sent) < count_tokens(messages), f"{layer} must act for the tests below to mean anything"


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", LAYERS)
async def test_current_question_is_sent_verbatim_as_the_last_human_message(layer) -> None:
    sent = await _run(layer, _chat())
    assert _humans(sent)[-1] == QUESTION_B


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", LAYERS)
async def test_pinned_plan_is_sent_verbatim(layer) -> None:
    sent = await _run(layer, _chat(plan=True), budget_tokens=4_000)
    plans = [m for m in sent if isinstance(m, UserMessage) and m.pinned]
    assert [m.content for m in plans] == [PLAN]


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", LAYERS)
async def test_stored_messages_are_not_modified(layer) -> None:
    messages = _chat(plan=True)
    before = [m.model_dump() for m in messages]
    await _run(layer, messages)
    assert [m.model_dump() for m in messages] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", LAYERS)
async def test_messages_a_layer_writes_are_marked_injected(layer) -> None:
    messages = _chat()
    sent = await _run(layer, messages)
    written = [m for m in sent if isinstance(m, UserMessage) and all(m is not original for original in messages)]
    assert all(m.injected for m in written)


FETCH = "knowledgegraph__fetch_record"
PROTECTING_LAYERS = {
    "L3 tool_result_clearing": lambda: shape_tool_result_clearing(
        keep_last_n_turns=1, trigger_ratio=0.0, protected_tools=frozenset({FETCH}),
    ),
    "L4 loop_compaction": lambda: shape_loop_compaction(
        keep_recent=2, trigger_ratio=0.0, protected_tools=frozenset({FETCH}),
    ),
    "L6 deterministic_compact": lambda: shape_deterministic_compact(
        trigger_ratio=0.0, keep_last_n_messages=2, protected_tools=frozenset({FETCH}),
    ),
    "L7 auto_compact": lambda: shape_auto_compact(
        trigger_ratio=0.0, max_tail_ratio=0.1, protected_tools=frozenset({FETCH}),
    ),
    "L8 synthesis_guard": lambda: shape_synthesis_guard(
        keep_last_n_tool_results=1, protected_tools=frozenset({FETCH}),
    ),
}


def _fetch_chat_with_a_search(rounds: int = 6) -> list:
    """`_chat` whose fetches carry citation ids, plus unprotected searches
    the layers are free to compact."""
    messages: list = [UserMessage(content=QUESTION_A), UserMessage(content=QUESTION_B)]
    for i in range(rounds):
        name = FETCH if i % 2 == 0 else "knowledgegraph__search"
        messages.append(AssistantMessage(
            content=[TextPart(text=f"Reading {i}.")],
            tool_calls=[ToolCall(id=f"c{i}", name=name, arguments={"id": i})],
        ))
        messages.append(ToolMessage(content="m" * 3_000 + f" [ref{i}]", tool_call_id=f"c{i}"))
    return messages


@pytest.mark.asyncio
@pytest.mark.parametrize("layer", PROTECTING_LAYERS)
async def test_protected_tool_results_are_sent_verbatim(layer) -> None:
    """UC-03 (CE-13): every layer leaves results of protected tools whole,
    so the citation ids they carry stay readable, while still compacting
    the unprotected ones."""
    messages = _fetch_chat_with_a_search()
    ctx = ModelCallContext(
        messages=list(messages), budget=ContextBudget(max_tokens=3_000, model="test"), turn_index=10,
    )

    async def _next() -> None:
        return None

    await PROTECTING_LAYERS[layer]()(ctx, _next)

    fetched = [m.content for m in messages if isinstance(m, ToolMessage) and m.tool_call_id in {"c0", "c2", "c4"}]
    sent = [m.content for m in ctx.messages if isinstance(m, ToolMessage)]
    assert all(content in sent for content in fetched)
    assert count_tokens(ctx.messages) < count_tokens(messages), f"{layer} must act on the unprotected results"
