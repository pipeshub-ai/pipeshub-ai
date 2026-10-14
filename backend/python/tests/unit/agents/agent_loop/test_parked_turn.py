"""`parked_turn`: a turn that stopped for the person (an approval card or an ask-user question) is
kept as the model had it and put back, in place of the history's summarised copy, for the request
that answers it."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agents.agent_loop import parked_turn
from app.agents.agent_loop.factory import inject_ask_user_question_resume
from tests.unit.agents.adapter.conftest import make_context

_QUESTION = "Create a page in Testspace, then ask me whether to add a footer comment"
_PAGE = "Created page 98765 in space TS."
_KEY = "/services/agents/parked-turns/conv-1"
_CREATE = ToolCall(id="c1", name="mcp_atlassian_createConfluencePage", arguments={"title": "Plan"})


def _ask(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="internaltools__ask_user_question", arguments={"questions": [{"question": "Comment?"}]})


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int | None] = {}

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, keep_in_cache: bool = True) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None, keep_in_cache: bool = True) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        self.ttls[key] = ttl_seconds
        return True


def _context(store: _Store, **overrides: Any) -> Any:  # noqa: ANN401
    return make_context(config_service=store, conversation_id="conv-1", **overrides)


async def _history(*messages: Message) -> ContextManager:
    history = ContextManager()
    for message in messages:
        await history.add(message)
    return history


def _as_the_model_had_it(page: str = _PAGE) -> list[Message]:
    return [
        UserMessage(content=_QUESTION),
        AssistantMessage(content="", tool_calls=[_CREATE]),
        ToolMessage(content=page, tool_call_id="c1"),
        AssistantMessage(content="", tool_calls=[_ask("q1")]),
        ToolMessage(content='{"questions": ["Comment?"]}', tool_call_id="q1"),
    ]


def _as_the_history_has_it() -> list[Message]:
    """The next request's copy of the same turn: each result only as its summary."""
    return [
        UserMessage(content=_QUESTION),
        AssistantMessage(content="", tool_calls=[_CREATE]),
        ToolMessage(content="Plan", tool_call_id="c1"),
        AssistantMessage(content="", tool_calls=[_ask("q1")]),
        ToolMessage(content="Asked 1 question", tool_call_id="q1"),
    ]


async def _keep_question(store: _Store, run: list[Message] | None = None) -> None:
    agent = SimpleNamespace(
        context=await _history(*(run or _as_the_model_had_it())), visible_tools={"mcp_atlassian_createConfluencePage"},
    )
    await parked_turn.keep_question(_context(store), agent)  # type: ignore[arg-type]


async def test_an_answered_question_carries_on_with_the_turn_as_the_model_had_it() -> None:
    store = _Store()
    await _keep_question(store)
    context = _context(store)
    agent = SimpleNamespace(context=await _history(*_as_the_history_has_it()))

    await parked_turn.restore_question(context, agent.context)
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Comment?" → yes')  # type: ignore[arg-type]

    messages = await agent.context.messages()
    assert any(isinstance(m, ToolMessage) and m.content == _PAGE for m in messages)
    assert isinstance(messages[-1], ToolMessage) and messages[-1].tool_call_id == "q1" and "yes" in messages[-1].content
    assert context.tool_state[parked_turn.VISIBLE_TOOLS] == ["mcp_atlassian_createConfluencePage"]
    assert store.ttls[_KEY] == parked_turn.QUESTION_TTL_SECONDS


async def test_an_earlier_answer_in_the_history_is_not_taken_for_the_question() -> None:
    """The history keeps each answer to a question as a user message; the turn still starts at the
    person's question, so the calls before that answer aren't there twice."""
    store = _Store()
    run = [
        *_as_the_model_had_it()[:3],
        AssistantMessage(content="", tool_calls=[_ask("q0")]),
        ToolMessage(content='{"status": "answered"}', tool_call_id="q0"),
        *_as_the_model_had_it()[3:],
    ]
    await _keep_question(store, run)
    history = await _history(
        *_as_the_history_has_it()[:3],
        AssistantMessage(content="", tool_calls=[_ask("q0")]),
        ToolMessage(content="Asked 1 question", tool_call_id="q0"),
        UserMessage(content='User selections:\n1. "Which space?" → Testspace'),
        *_as_the_history_has_it()[3:],
    )

    await parked_turn.restore_question(_context(store), history)

    messages = await history.messages()
    assert [c.id for m in messages if isinstance(m, AssistantMessage) for c in m.tool_calls or []] == ["c1", "q0", "q1"]
    assert [m.content for m in messages if isinstance(m, UserMessage)] == [_QUESTION]


async def test_someone_elses_answer_gets_no_turn() -> None:
    store = _Store()
    await _keep_question(store)
    history = await _history(*_as_the_history_has_it())

    assert await parked_turn.restore(_context(store, user_id="user-2"), history, call_id="q1") is None
    assert not any(isinstance(m, ToolMessage) and m.content == _PAGE for m in await history.messages())


async def test_a_later_question_does_not_get_an_earlier_turn() -> None:
    store = _Store()
    await _keep_question(store)
    later = [*_as_the_history_has_it()[:3], AssistantMessage(content="", tool_calls=[_ask("q2")])]

    assert await parked_turn.restore(_context(store), await _history(*later), call_id="q2") is None


async def test_one_record_answers_an_approval_and_a_question_from_the_same_run() -> None:
    store = _Store()
    context = _context(store)
    agent = SimpleNamespace(visible_tools=set(), context=await _history(
        UserMessage(content=_QUESTION),
        AssistantMessage(content="", tool_calls=[_CREATE, _ask("q1")]),
        ToolMessage(content="Waiting for your approval to run createConfluencePage on Atlassian.", tool_call_id="c1"),
        ToolMessage(content='{"questions": ["Comment?"]}', tool_call_id="q1"),
    ))

    await parked_turn.keep(context, agent, call_id="c1", ttl_seconds=60)  # type: ignore[arg-type]
    await parked_turn.keep_question(context, agent)  # type: ignore[arg-type]

    assert store.values[_KEY]["callIds"] == ["c1", "q1"]
    assert store.ttls[_KEY] == parked_turn.QUESTION_TTL_SECONDS
    for call_id in ("c1", "q1"):
        assert await parked_turn.restore(_context(store), await _history(UserMessage(content=_QUESTION)), call_id=call_id) == []


async def test_a_long_result_is_kept_cut() -> None:
    store = _Store()
    await _keep_question(store, _as_the_model_had_it(page="x" * 30_000))

    kept_result = store.values[_KEY]["messages"][1]["content"]
    assert kept_result.endswith("[result cut]") and len(kept_result) < 21_000
