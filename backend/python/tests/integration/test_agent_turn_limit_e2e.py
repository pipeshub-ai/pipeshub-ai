"""End-to-end test of an agent run that reaches its turn limit, through
`POST /chat/stream` and `POST /chat`.

Everything below the HTTP route runs for real: `run_chat_stream()`, the
real `PipesHubAgentFactory` (hooks, prompt builder, tool registry), the
agent loop, `LangChainTransport` (`bind_tools` + `astream`), the AG-UI
stream and `AnswerFinalizer`. Only what a CI runner cannot reach is
replaced: the chat model is a scripted LangChain `BaseChatModel`, and the
retrieval/graph/config services are mocks, as in
`test_chat_stream_agent_loop_e2e.py`.

The scenario is a benchmark failure: the agent spent its last allowed turn
on a tool call, the limit was reached before the model saw that result,
and the user got the turn's narration ("I'll join the club member
directory to the ZIP-code reference ...") as the answer, or an error when
there was no narration.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from app.agents.agent_loop.factory import _MAX_TURNS
from app.api.routes.chatbot import askAI, askAIStream
from tests.integration.test_chat_stream_agent_loop_e2e import (
    _drain,
    _events_by_name,
    _mock_cancellation_registry,
    _mock_config_service,
    _mock_request,
    _mock_retrieval_service,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_QUESTION = "List the full names of the Student_Club members that grew up in Illinois state."
_NARRATION = (
    "I'll join the club member directory to the ZIP-code reference and return "
    "the distinct full names whose ZIP code maps to Illinois."
)
_ANSWER = "Trent Smith, Tyler Hewitt and Annabella Warren grew up in Illinois."
# Always granted and needs no external service, so every turn is a real,
# successful tool call that stall/duplicate detection leaves alone.
_TOOL = "search_tools"


class _ScriptedChatModel(BaseChatModel):
    """Replays one scripted `AIMessage` per model call and records what each
    call was sent. Streams like a provider: content and tool-call chunks
    arrive in one `AIMessageChunk`."""

    responses: list[AIMessage] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tool_names: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: list[Any], **kwargs: object) -> _ScriptedChatModel:
        self.bound_tool_names.append([_tool_name(tool) for tool in tools])
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError(f"unscripted model call #{index + 1}")
        return self.responses[index]

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: object = None, **kwargs: object,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: object = None, **kwargs: object,
    ) -> AsyncIterator[ChatGenerationChunk]:
        message = self._next(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": i}
                for i, call in enumerate(message.tool_calls)
            ],
        ))


def _tool_name(tool: object) -> str:
    if isinstance(tool, dict):
        return tool.get("function", {}).get("name") or tool.get("name", "")
    return getattr(tool, "name", "")


def _tool_turn(index: int, narration: str = "") -> AIMessage:
    return AIMessage(
        content=narration,
        tool_calls=[{"name": _TOOL, "args": {"query": f"tools for step {index}"}, "id": f"call-{index}"}],
    )


def _turn_limit_script(*, narration: str, final: AIMessage) -> list[AIMessage]:
    """Every allowed turn calls a tool, the last one narrating before its call;
    `final` answers the extra turn after the limit."""
    return [_tool_turn(i) for i in range(_MAX_TURNS - 1)] + [_tool_turn(_MAX_TURNS - 1, narration), final]


def _graph_provider() -> AsyncMock:
    graph = AsyncMock()
    graph.get_nodes_by_filters.return_value = []
    return graph


def _request(body: dict) -> MagicMock:
    request = _mock_request(body)
    # A real logger, so a failure inside the run shows its traceback here.
    request.app.container.logger.return_value = logging.getLogger(__name__)
    request.is_disconnected = AsyncMock(return_value=False)
    return request


def _route_kwargs() -> dict[str, Any]:
    return {
        "retrieval_service": _mock_retrieval_service(),
        "graph_provider": _graph_provider(),
        "config_service": _mock_config_service(),
        "cancellation_registry": _mock_cancellation_registry(),
    }


@pytest.fixture
def chat_model(monkeypatch: pytest.MonkeyPatch) -> _ScriptedChatModel:
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    model = _ScriptedChatModel()
    with (
        patch("app.utils.execute_query.has_sql_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.utils.fetch_slack_thread.has_slack_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.api.routes.chatbot.get_llm_for_chat", new=AsyncMock(return_value=(
            model, {"provider": "openai", "isMultimodal": False, "contextLength": 128000}, {},
        ))),
    ):
        yield model


async def _stream(question: str = _QUESTION) -> dict[str, list[dict]]:
    response = await askAIStream(request=_request({"query": question, "chatMode": "agent"}), **_route_kwargs())
    return _events_by_name(await _drain(response))


def _answer(events: dict[str, list[dict]]) -> str:
    assert "RUN_ERROR" not in events, events.get("RUN_ERROR")
    [finished] = events["RUN_FINISHED"]
    return finished["result"]["answer"]


def _last_message_text(call: list[BaseMessage]) -> str:
    content = call[-1].content
    return content if isinstance(content, str) else json.dumps(content)


class TestTurnLimitEndToEnd:
    @pytest.mark.parametrize(
        "narration",
        [_NARRATION, ""],
        ids=["narration-was-returned-as-the-answer", "error-was-returned-without-narration"],
    )
    async def test_the_answer_after_the_limit_comes_from_the_last_tool_results(
        self, chat_model: _ScriptedChatModel, narration: str,
    ) -> None:
        chat_model.responses = _turn_limit_script(narration=narration, final=AIMessage(content=_ANSWER))

        events = await _stream()

        assert _answer(events) == _ANSWER
        assert len(events["TOOL_CALL_RESULT"]) == _MAX_TURNS
        assert len(chat_model.calls) == _MAX_TURNS + 1
        streamed = "".join(e.get("delta", "") for e in events["TEXT_MESSAGE_CONTENT"])
        assert _ANSWER in streamed

    async def test_the_extra_turn_asks_for_an_answer_and_keeps_the_tool_list(
        self, chat_model: _ScriptedChatModel,
    ) -> None:
        chat_model.responses = _turn_limit_script(narration=_NARRATION, final=AIMessage(content=_ANSWER))

        await _stream()

        instruction = _last_message_text(chat_model.calls[-1])
        assert "maximum number of steps" in instruction
        assert "Do not call any more tools" in instruction
        # Providers reject tool history without tool definitions.
        assert _TOOL in chat_model.bound_tool_names[-1]

    async def test_a_tool_call_in_the_extra_turn_is_not_run(self, chat_model: _ScriptedChatModel) -> None:
        chat_model.responses = _turn_limit_script(narration=_NARRATION, final=_tool_turn(_MAX_TURNS))

        events = await _stream()

        assert len(events["TOOL_CALL_RESULT"]) == _MAX_TURNS
        # No answer from the extra turn: the run keeps the narration, as before.
        assert _answer(events) == _NARRATION

    async def test_the_two_turns_left_warning_names_no_tool_the_agent_lacks(
        self, chat_model: _ScriptedChatModel,
    ) -> None:
        chat_model.responses = _turn_limit_script(narration=_NARRATION, final=AIMessage(content=_ANSWER))

        await _stream()

        assert "task_complete" not in chat_model.bound_tool_names[0]
        warned = [i for i, call in enumerate(chat_model.calls) if "turns remaining" in _last_message_text(call)]
        assert warned == [_MAX_TURNS - 2]
        warning = _last_message_text(chat_model.calls[_MAX_TURNS - 2])
        assert "task_complete" not in warning
        assert "Reply with your final answer" in warning

    async def test_non_streaming_chat_returns_the_final_answer(self, chat_model: _ScriptedChatModel) -> None:
        chat_model.responses = _turn_limit_script(narration=_NARRATION, final=AIMessage(content=_ANSWER))

        response = await askAI(request=_request({"query": _QUESTION, "chatMode": "agent"}), **_route_kwargs())

        assert response.status_code == 200
        assert json.loads(response.body)["answer"] == _ANSWER
