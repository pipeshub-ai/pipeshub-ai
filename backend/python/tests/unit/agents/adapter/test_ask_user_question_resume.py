"""ask_user_question resume: original Goal + tool_result, not a new user turn."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, ToolMessage, UserMessage
from app.agent_loop_lib.tools.decorators import _default_terminal_outcome
from app.agents.agent_loop.factory import (
    inject_ask_user_question_resume,
    is_ask_user_question_resume_query,
    last_real_user_query,
)


def test_detects_synthetic_resume_query() -> None:
    assert is_ask_user_question_resume_query('User selections:\n1. "Which?" → A')
    assert not is_ask_user_question_resume_query("See below attached file(s).")
    assert not is_ask_user_question_resume_query(None)


def test_last_real_user_query_skips_resume_rows() -> None:
    previous = [
        {"role": "user_query", "content": "See below attached file(s)."},
        {"role": "bot_response", "content": "Which editor?"},
        {"role": "user_query", "content": 'User selections:\n1. "Which?" → Vim'},
    ]
    assert last_real_user_query(previous, "fallback") == "See below attached file(s)."


def test_terminal_ask_user_question_sets_needs_input() -> None:
    tr = SimpleNamespace(content="{}")
    call = SimpleNamespace(name="internaltools__ask_user_question")
    outcome = _default_terminal_outcome(tr, call, "")
    assert outcome.task_done is True
    assert outcome.needs_input == "Waiting for user answers"


def test_terminal_other_tool_has_no_needs_input() -> None:
    tr = SimpleNamespace(content="done")
    call = SimpleNamespace(name="final_answer")
    outcome = _default_terminal_outcome(tr, call, "done")
    assert outcome.needs_input is None


@pytest.mark.asyncio
async def test_injects_answers_on_existing_ask_tool_call() -> None:
    ctx = ContextManager()
    await ctx.add(AssistantMessage(
        content=[],
        tool_calls=[ToolCall(
            id="tc-ask-1",
            name="internaltools__ask_user_question",
            arguments={"user_intent": "pick"},
        )],
    ))
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Which?" → Vim')
    messages = await ctx.messages()
    assert len(messages) == 2
    assert isinstance(messages[0], AssistantMessage)
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].tool_call_id == "tc-ask-1"
    payload = json.loads(messages[1].content)
    assert payload["status"] == "answered"
    assert "Vim" in payload["answers"]


@pytest.mark.asyncio
async def test_inject_replaces_parked_tool_result_and_drops_empty_fallback() -> None:
    ctx = ContextManager()
    await ctx.add(UserMessage(content="hi i ask questions"))
    await ctx.add(AssistantMessage(
        content=[],
        tool_calls=[ToolCall(
            id="tc-ask-1",
            name="internaltools__ask_user_question",
            arguments={},
        )],
    ))
    await ctx.add(ToolMessage(content='{"questions":[]}', tool_call_id="tc-ask-1"))
    await ctx.add(AssistantMessage(
        content="I wasn't able to generate a response. Please try rephrasing.",
    ))
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(
        agent, 'User selections:\n1. "Which?" → Work or career',
    )
    messages = await ctx.messages()
    assert len(messages) == 3
    assert isinstance(messages[0], UserMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].tool_calls and messages[1].tool_calls[0].id == "tc-ask-1"
    assert isinstance(messages[2], ToolMessage)
    payload = json.loads(messages[2].content)
    assert payload["status"] == "answered"
    assert "Work or career" in payload["answers"]
    assert not any(
        isinstance(m, AssistantMessage) and "wasn't able" in m.text
        for m in messages
    )


@pytest.mark.asyncio
async def test_inject_strips_fallback_when_history_has_no_tool_call() -> None:
    ctx = ContextManager()
    await ctx.add(UserMessage(content="hi i ask questions"))
    await ctx.add(AssistantMessage(
        content="I wasn't able to generate a response. Please try rephrasing.",
    ))
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Which?" → Work')
    messages = await ctx.messages()
    assert isinstance(messages[0], UserMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].tool_calls
    assert "ask_user_question" in messages[1].tool_calls[0].name
    assert isinstance(messages[2], ToolMessage)
    assert not any("wasn't able" in m.text for m in messages if isinstance(m, AssistantMessage))
