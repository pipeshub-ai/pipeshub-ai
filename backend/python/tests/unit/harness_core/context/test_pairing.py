"""P4.1 CC-10, CC-11: no reduction, however its range falls, sends a call without its result."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness_core.context import (
    Compaction,
    CompilerInput,
    PairingBroken,
    SeqSpan,
    assert_paired,
    compile_context,
)
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)

from .support import answer, ask, called, host


@st.composite
def histories(draw: st.DrawFn) -> list:
    items = [ask(1, "start", own=True)]
    seq = 2
    for turn in range(1, draw(st.integers(1, 8)) + 1):
        kind = draw(st.sampled_from(["calls", "answer", "ask"]))
        if kind == "calls":
            results = [f"r{turn}.{i}" for i in range(draw(st.integers(1, 3)))]
            items.append(called(seq, turn, *results))
            seq += len(results) + 1
        elif kind == "answer":
            items.append(answer(seq, f"a{turn}"))
            seq += 1
        else:
            items.append(ask(seq, f"q{turn}"))
            seq += 1
    return items


spans = st.tuples(st.integers(1, 40), st.integers(0, 40)).map(lambda p: SeqSpan(p[0], p[0] + p[1]))


@settings(max_examples=300, deadline=None)
@given(histories(), st.lists(spans, max_size=3), st.lists(spans, max_size=3))
def test_cc10_any_clear_and_compact_ranges_keep_every_call_paired(items: list, compact: list, clear: list) -> None:
    compiled = compile_context(
        CompilerInput(
            host=host(),
            history=tuple(items),
            compactions=tuple(Compaction(span, "summary") for span in compact),
            cleared=tuple(clear),
        )
    )
    assert_paired(compiled.history)
    calls = [c.id for m in compiled.history if isinstance(m, AssistantMessage) for c in m.tool_calls or []]
    results = [m.tool_call_id for m in compiled.history if isinstance(m, ToolMessage)]
    assert calls == results


def _call(*ids: str) -> AssistantMessage:
    return AssistantMessage(content=[TextPart(text="")], tool_calls=[ToolCall(id=i, name="t", arguments={}) for i in ids])


@pytest.mark.parametrize(
    "messages",
    [
        [_call("a")],
        [_call("a"), UserMessage(content="x"), ToolMessage(content="r", tool_call_id="a")],
        [UserMessage(content="x"), ToolMessage(content="r", tool_call_id="a")],
        [_call("a"), ToolMessage(content="r", tool_call_id="a"), ToolMessage(content="r", tool_call_id="a")],
        [_call("a", "b"), ToolMessage(content="r", tool_call_id="a"), _call("c")],
    ],
    ids=["no_result", "user_between", "orphan_result", "twice", "next_call_before_result"],
)
def test_cc11_the_guard_refuses_an_unpaired_list(messages: list) -> None:
    with pytest.raises(PairingBroken):
        assert_paired(messages)
