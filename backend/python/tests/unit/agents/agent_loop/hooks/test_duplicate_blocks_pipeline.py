"""The duplicate-block step inside the real PRE_MODEL pipeline the factory builds.

Every other shaper runs first, so these check the step against what they do
to the view: nothing (no pressure), clearing old tool results (L3) and
compacting the middle of the history (L6). In every case each block the
model had must still reach it at least once.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, ToolMessage
from app.agents.agent_loop.factory import PipesHubAgentFactory
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.agent_loop.hooks.test_duplicate_blocks import (
    _Conversation,
    _text,
)


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


async def _model_view(chat: _Conversation, max_tokens: int) -> list:
    context = make_context()
    context.tool_state["content_manifests"] = chat.registry
    hooks = PipesHubAgentFactory._build_hooks(context)
    return await dispatch_pre_model(
        hooks, chat.messages, ContextBudget(max_tokens=max_tokens, reserved_output_tokens=0),
    )


def _filler(chat: _Conversation, turns: int, size: int) -> None:
    for turn in range(turns):
        call_id = f"filler-{turn}"
        chat.messages.append(AssistantMessage(
            content="", tool_calls=[ToolCall(id=call_id, name="calculator", arguments={})],
        ))
        chat.messages.append(ToolMessage(content="x" * size, tool_call_id=call_id))


def _visible(view: list, text: str) -> int:
    return sum(text in m.text for m in view if isinstance(m, ToolMessage))


@pytest.mark.asyncio
async def test_without_pressure_the_fetch_supersedes_the_search() -> None:
    chat = _Conversation()
    chat.search([_text("a", 0), _text("b", 1)])
    chat.fetch({"a": [0, 1]})

    view = await _model_view(chat, max_tokens=1_000_000)

    assert _visible(view, "a block 0 text") == 1, "shown once, by the fetch"
    assert _visible(view, "b block 1 text") == 1
    assert _visible(view, "fetch_record result below") == 1


@pytest.mark.asyncio
async def test_when_old_search_results_are_cleared_the_fetch_still_shows_its_blocks() -> None:
    chat = _Conversation()
    chat.search([_text("a", 0)])
    chat.fetch({"a": [0]})
    _filler(chat, turns=4, size=4_000)

    view = await _model_view(chat, max_tokens=6_000)

    assert _visible(view, "tool: knowledgegraph__search") == 1, "the search was cleared"
    assert _visible(view, "[ref] a block 0 text") == 1, "the fetch still shows the block"


@pytest.mark.asyncio
async def test_when_the_fetch_is_compacted_away_the_search_copy_stays() -> None:
    """Compaction ignores fetch's protection; once the fetch is gone the
    search copy is the only one, so it must not have been removed."""
    chat = _Conversation()
    chat.fetch({"a": [0], "b": list(range(1, 300))})
    _filler(chat, turns=3, size=200)
    chat.search([_text("a", 0)])

    view = await _model_view(chat, max_tokens=2_000)

    assert _visible(view, "[ref] a block 0 text") == 0, "the fetch was compacted away"
    assert _visible(view, "a block 0 text") == 1, "so the search keeps its copy"
