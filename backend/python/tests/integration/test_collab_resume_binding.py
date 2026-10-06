"""J-09 Python half (PH-08 PR-08c/08d): a parked `ask_user_question` card in a
shared chat resumes only for the person it was put to, through the real
`POST /chat/stream` handler.

Only the outer boundaries are faked, as in `test_chat_stream_agent_loop_e2e.py`:
the LLM (scripted), retrieval/config services and the connector toolset
loader (one stub mail tool). `ChatQuery` parsing, `build_initial_state`,
`AgentContext`, `PipesHubAgentFactory.create` (resume resolution, write
guard, history labels) and the agent loop all run for real.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import factory as factory_module
from app.api.routes.chatbot import askAIStream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ADDRESS = "carol@partner.example"
B_GOAL = f"Email the Q3 deck to {ADDRESS}, ask me to confirm first"
SELECTIONS = 'User selections:\n1. "Send the Q3 deck now?" → Yes'
A_TEXT = 'User selections:\n1. "Send the Q3 deck now?" → Yes, and summarise it'
SEND = "gmail__send_email"


def _collaboration(sender: str) -> dict:
    return {
        "participants": [
            {"ref": "participant_1", "displayName": "Ann", "isCurrentSender": sender == "participant_1"},
            {"ref": "participant_2", "displayName": "Ben", "isCurrentSender": sender == "participant_2"},
        ],
        "currentSenderRef": sender,
    }


# What Node sends after B's turn parked a card: B's question, then the answer row
# that carries the parked ask_user_question call (Node's withTurnAskToolResults).
PREVIOUS = [
    {"role": "user_query", "content": "hello team", "authorRef": "participant_1"},
    {"role": "bot_response", "content": "Hi!"},
    {"role": "user_query", "content": B_GOAL, "authorRef": "participant_2"},
    {
        "role": "bot_response",
        "content": "",
        "tool_results": [{
            "tool_name": "internaltools__ask_user_question",
            "tool_id": "ask-1",
            "status": "success",
            "args": {"questions": [{"question": f"Send the Q3 deck to {ADDRESS} now?", "options": ["Yes", "No"]}]},
            "result": '{"status": "waiting"}',
        }],
    },
]


class _SendEmail(Tool):
    calls: list = []

    name = SEND
    short_description = "Send an email"
    description = "Send an email"
    path = "/tools/gmail/send_email"
    parameters = [ToolParameter("to", ParameterType.STRING, "recipient")]

    @property
    def tags(self) -> list[Tag]:
        return [Tag("type", "write"), Tag("category", "email")]

    async def execute(self, **kw: object) -> ToolOutput:
        type(self).calls.append(kw)
        return ToolOutput(success=True, data="sent")


class _ScriptedLLM:
    """LangChain chat-model double: proposes the send once, then answers."""

    def __init__(self) -> None:
        self.calls: list[list] = []
        self._responses = [
            AIMessage(content="", tool_calls=[{"name": SEND, "args": {"to": ADDRESS}, "id": "send-1"}]),
            AIMessage(content="Done."),
        ]

    def bind_tools(self, tools: object, **_: object) -> "_ScriptedLLM":
        return self

    def with_structured_output(self, *_a: object, **_k: object) -> "_ScriptedLLM":
        return self

    async def ainvoke(self, messages: list, config: object = None, **_: object) -> AIMessage:
        self.calls.append(messages)
        return self._responses[min(len(self.calls), len(self._responses)) - 1]

    async def astream(self, messages: list, config: object = None, **_: object) -> AsyncIterator[AIMessageChunk]:
        message = await self.ainvoke(messages, config)
        yield AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ],
        )


def _request(body: dict, user_id: str) -> MagicMock:
    request = MagicMock(spec=Request)
    request.state.user = {"orgId": "org-1", "userId": user_id, "email": f"{user_id}@corp.example"}
    request.query_params = {}
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


def _registry() -> AsyncMock:
    registry = AsyncMock()
    registry.is_active = AsyncMock(return_value=False)
    return registry


def _retrieval() -> AsyncMock:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
    return retrieval


async def _stream(body: dict, user_id: str) -> tuple[_ScriptedLLM, list[str], list[str]]:
    _SendEmail.calls = []
    llm = _ScriptedLLM()
    injected: list[str] = []
    real_inject = factory_module.inject_ask_user_question_resume

    async def _spy_inject(agent, answers) -> None:
        injected.append(answers)
        await real_inject(agent, answers)

    async def _load(self: object, ctx: object, skip_apps: object = None) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(_SendEmail())
        return registry

    config_service = AsyncMock()
    config_service.get_config.return_value = {"providers": []}
    with (
        patch("app.api.routes.chatbot.get_llm_for_chat", new=AsyncMock(return_value=(
            llm, {"provider": "openai", "isMultimodal": False, "contextLength": 128000}, {},
        ))),
        patch("app.utils.execute_query.has_sql_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.utils.fetch_slack_thread.has_slack_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.agents.agent_loop.factory.PipesHubToolLoader.load", new=_load),
        patch.object(factory_module, "inject_ask_user_question_resume", new=_spy_inject),
    ):
        response = await askAIStream(
            request=_request(body, user_id),
            retrieval_service=_retrieval(),
            graph_provider=MagicMock(),
            config_service=config_service,
            cancellation_registry=_registry(),
        )
        chunks = [chunk async for chunk in response.body_iterator]
    return llm, injected, chunks


def _human_texts(messages: list) -> list[str]:
    return [str(m.content) for m in messages if getattr(m, "type", "") == "human"]


def _tool_texts(messages: list) -> list[str]:
    return [str(m.content) for m in messages if getattr(m, "type", "") == "tool"]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.delenv("SANDBOX_MODE", raising=False)


def _body(query: str, sender: str, *, resume: bool) -> dict:
    body = {
        "query": query,
        "chatMode": "agent",
        "conversationId": "conv-1",
        "previousConversations": PREVIOUS,
        "collaboration": _collaboration(sender),
    }
    if resume:
        body["resume"] = {"toolCallMessageId": "65f000000000000000000001"}
    return body


async def test_other_participant_answer_runs_as_a_new_query_and_sends_nothing() -> None:
    llm, injected, _chunks = await _stream(_body(A_TEXT, "participant_1", resume=False), "user-a")

    assert injected == []
    assert _SendEmail.calls == []
    first = llm.calls[0]
    assert A_TEXT in _human_texts(first)[-1]
    assert not any(B_GOAL == text for text in _human_texts(first)[-1:])
    assert any("collaboration_write_guard" in t for t in _tool_texts(llm.calls[1]))


async def test_other_participant_cannot_resume_even_with_a_resume_field() -> None:
    # Node refuses this with 403 RESUME_NOT_ALLOWED; Python must not resume it either.
    llm, injected, _chunks = await _stream(_body(SELECTIONS, "participant_1", resume=True), "user-a")

    assert injected == []
    assert _SendEmail.calls == []
    assert SELECTIONS in _human_texts(llm.calls[0])[-1]


async def test_asker_resume_runs_their_goal_and_the_confirmed_send() -> None:
    llm, injected, _chunks = await _stream(_body(SELECTIONS, "participant_2", resume=True), "user-b")

    assert injected == [SELECTIONS]
    assert _SendEmail.calls == [{"to": ADDRESS}]
    humans = _human_texts(llm.calls[0])
    assert B_GOAL in humans[-1]
    assert any("Yes" in t for t in _tool_texts(llm.calls[0]))


async def test_malformed_collaboration_is_rejected_before_any_run() -> None:
    body = _body("hi", "participant_1", resume=False)
    body["collaboration"]["participants"][0]["userId"] = "u-1"
    with pytest.raises(HTTPException) as err:
        await _stream(body, "user-a")
    assert err.value.status_code in (400, 422)
    assert json.dumps(err.value.detail, default=str)
