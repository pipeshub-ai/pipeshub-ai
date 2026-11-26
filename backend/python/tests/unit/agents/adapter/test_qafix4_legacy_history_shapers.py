"""QAFIX4 B2 on today's (non-durable) loop: the PRE_MODEL shapers that cut history never fold or drop the user's latest
request or the first message, and the person is told when history was reduced.

QA-FP12 run 8cbf6950 showed the durable loop's copy of the bug: on the fifth zero-based turn L4 (loop compaction) kept
the first message and the last six, folding the batch the user had pasted into a 150-character note, and the model
asked what to do. The legacy loop runs the same shapers, so the same request is shaped here through the factory's own
registry: a short earlier exchange, the pasted batch, five tool rounds, a small window.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage, UserMessage
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import shape_auto_compact
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from harness_core.kernel.agent.hook_dispatch import dispatch_pre_model
from harness_core.kernel.context.base import ContextBudget
from harness_core.kernel.core.messages import ToolCall
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

BATCH = "Tally these labels:\n" + " ".join(f"label-{i}" for i in range(1_500))


def _session(rounds: int = 5) -> list[Any]:
    messages: list[Any] = [UserMessage(content="hi"), AssistantMessage(content="Hello."), UserMessage(content=BATCH)]
    for i in range(rounds):
        messages.append(AssistantMessage(content="", tool_calls=[ToolCall(id=f"c{i}", name="run_code", arguments={"code": "x" * 400})]))
        messages.append(ToolMessage(content=f"result {i} " + "y" * 2_000, tool_call_id=f"c{i}"))
    return messages


def _texts(messages: list[Any]) -> list[str]:
    return [m.content for m in messages if isinstance(m, UserMessage) and isinstance(m.content, str)]


@pytest.mark.parametrize("turn_index", [1, 5])
async def test_the_legacy_shapers_keep_the_latest_request_and_the_first_message(turn_index: int) -> None:
    sink = AsyncMock()
    context = make_context(llm=FakeChatModel(), event_sink=sink)
    hooks = PipesHubAgentFactory._build_hooks(context)  # pyright: ignore[reportPrivateUsage]
    shaped = await dispatch_pre_model(hooks, _session(), ContextBudget(max_tokens=6_000), turn_index=turn_index)
    texts = _texts(shaped)
    assert BATCH in texts, "the request the run answers is sent verbatim"
    assert texts[0] == "hi", "the first message stays first"
    assert shaped.index(next(m for m in shaped if m.content == BATCH)) < len(shaped)


# Small enough that the history layers (L4-L9) cut after L1 has capped each result (main's per-batch share).
HISTORY_CUT = ContextBudget(max_tokens=4_000)


async def test_the_person_is_told_when_history_was_reduced() -> None:
    sink = AsyncMock()
    context = make_context(llm=FakeChatModel(), event_sink=sink)
    hooks = PipesHubAgentFactory._build_hooks(context)  # pyright: ignore[reportPrivateUsage]
    await dispatch_pre_model(hooks, _session(), HISTORY_CUT, turn_index=5)
    frames = [c.args[0] for c in sink.write.await_args_list]
    shown = [f for f in frames if f.get("data", {}).get("status") == "context_compacted"]
    assert len(shown) == 1, frames
    data = shown[0]["data"]
    assert data["afterTokens"] < data["beforeTokens"]
    assert "Context compacted" in data["message"]


async def test_nothing_is_said_when_nothing_was_reduced() -> None:
    sink = AsyncMock()
    context = make_context(llm=FakeChatModel(), event_sink=sink)
    hooks = PipesHubAgentFactory._build_hooks(context)  # pyright: ignore[reportPrivateUsage]
    small = [UserMessage(content="hi"), AssistantMessage(content="Hello."), UserMessage(content="and now?")]
    await dispatch_pre_model(hooks, small, ContextBudget(max_tokens=100_000), turn_index=5)
    assert sink.write.await_args_list == []


async def test_auto_compaction_never_summarises_the_latest_request() -> None:
    """L7 (registered only with a transport): its tail starts no later than the user's latest request."""
    ctx = ModelCallContext(messages=_session(), budget=ContextBudget(max_tokens=6_000), turn_index=5)

    async def done() -> None:
        return None

    await shape_auto_compact(summarizer=None, trigger_ratio=0.1, max_tail_ratio=0.05)(ctx, done)
    texts = _texts(ctx.messages)
    # Main #4533 protects the current request, not the first message: the turns before it may be summarised.
    assert BATCH in texts
    assert [t for t in texts if not t.startswith("[Auto-compacted")][-1] == BATCH


async def test_the_saved_transcript_keeps_one_context_compacted_part() -> None:
    """The legacy turn's saved parts (``TranscriptCollector``, stored with the answer) carry the row the live frame
    showed, in the durable part's shape, so it is there after a reload; a later reduction in the run adds none."""
    from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector

    collector = TranscriptCollector()
    context = make_context(llm=FakeChatModel(), event_sink=AsyncMock())
    context.transcript_collector = collector
    hooks = PipesHubAgentFactory._build_hooks(context)  # pyright: ignore[reportPrivateUsage]
    await dispatch_pre_model(hooks, _session(), HISTORY_CUT, turn_index=5)
    await dispatch_pre_model(hooks, _session(), HISTORY_CUT, turn_index=6)
    saved = [p for p in collector.parts if p.get("type") == "context_compacted"]
    assert len(saved) == 1, collector.parts
    compacted = saved[0]["compacted"]
    assert (compacted["executor"], compacted["outcome"], compacted["userMessagesKept"]) == ("loop", "compacted", False)
    assert 0 < compacted["afterTokens"] < compacted["beforeTokens"]
    assert collector.parts[0] is saved[0], "first, as the live row is"


def test_on_ag_ui_the_notice_is_the_durable_loops_context_compacted_frame() -> None:
    """The web app negotiates AG-UI for every turn, so today's loop sends the frame the durable projection sends for
    ``x.pipeshub.context.compacted``: the chat's existing row renders it in the reader's language. ``message`` is
    the server's English text, kept for a client that renders no row."""
    from app.agents.agent_loop.protocol.formatter import AGUIFormatter

    context = make_context(llm=FakeChatModel())
    (sent,) = AGUIFormatter().context_compacted(
        context, before_tokens=108_000, after_tokens=36_000, message="Context compacted · 108k → 36k tokens",
    )
    frame = sent["data"]
    assert frame["type"] == "CUSTOM" and frame["name"] == "context_compacted"
    assert frame["value"] == {
        "executor": "loop",
        "outcome": "compacted",
        "beforeTokens": 108_000,
        "afterTokens": 36_000,
        "userMessagesKept": False,
        "message": "Context compacted · 108k → 36k tokens",
    }
