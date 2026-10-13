"""`includeRetrievalContext` end to end through `askAIStream`: the measurement
frames appear only when asked for, never change the answer or the rest of the
stream, and a fault in them never costs the user the answer."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.api.routes.chatbot import askAIStream
from tests.integration.test_agent_turn_limit_e2e import (  # noqa: F401  (fixture)
    _TOOL,
    _answer,
    _request,
    _route_kwargs,
    _ScriptedChatModel,
    chat_model,
)
from tests.integration.test_chat_stream_agent_loop_e2e import _drain, _events_by_name

_QUESTION = "Which tools can search indexed knowledge?"
_ANSWER = "The knowledge search tool can."
_TELEMETRY = {"retrieval_context", "run_usage"}


def _script() -> list[AIMessage]:
    return [
        AIMessage(content="", tool_calls=[
            {"name": _TOOL, "args": {"query": "search indexed knowledge"}, "id": "call-1"},
        ]),
        AIMessage(content=_ANSWER),
    ]


async def _stream(**body: Any) -> dict[str, list[dict]]:  # noqa: ANN401
    request = _request({"query": _QUESTION, "chatMode": "agent", **body})
    return _events_by_name(await _drain(await askAIStream(request=request, **_route_kwargs())))


def _custom(events: dict[str, list[dict]], name: str) -> list[dict]:
    return [e["value"] for e in events.get("CUSTOM", []) if e.get("name") == name]


def _without_telemetry(events: dict[str, list[dict]]) -> dict[str, int]:
    """Event counts by name, CUSTOM telemetry frames left out."""
    counts = {name: len(frames) for name, frames in events.items() if name != "CUSTOM"}
    other_custom = [e for e in events.get("CUSTOM", []) if e.get("name") not in _TELEMETRY]
    if other_custom:
        counts["CUSTOM"] = len(other_custom)
    return counts


class TestRetrievalContextOnTheStream:
    async def test_no_frames_unless_asked_for(self, chat_model: _ScriptedChatModel) -> None:  # noqa: F811  (fixture imported above)
        chat_model.responses = _script()

        events = await _stream()

        assert _answer(events) == _ANSWER
        assert _custom(events, "retrieval_context") == []
        assert _custom(events, "run_usage") == []

    async def test_frames_change_nothing_else_on_the_stream(self, chat_model: _ScriptedChatModel) -> None:  # noqa: F811  (fixture imported above)
        chat_model.responses = _script()
        plain = await _stream()
        chat_model.responses, chat_model.calls = _script(), []

        measured = await _stream(includeRetrievalContext=True)

        assert _answer(measured) == _answer(plain) == _ANSWER
        assert _without_telemetry(measured) == _without_telemetry(plain)
        [usage] = _custom(measured, "run_usage")
        assert usage["llmCalls"] == 2
        assert usage["turns"] == 1, "one tool round; the final reply is not a round"
        assert len(usage["calls"]) == 2
        prefetch = [f for f in _custom(measured, "retrieval_context") if f["source"] == "prefetch"]
        assert len(prefetch) == 1, "one prefetch frame per opted-in turn, even when it ran nothing"

    async def test_a_fault_in_the_trace_still_delivers_the_answer(self, chat_model: _ScriptedChatModel) -> None:  # noqa: F811  (fixture imported above)
        chat_model.responses = _script()

        with patch(
            "app.agents.agent_loop.retrieval_ledger.RetrievalContextLedger.take_delta",
            side_effect=RuntimeError("ledger bug"),
        ), patch(
            "app.agents.agent_loop.protocol.run_usage.RunUsagePayload.from_usage",
            side_effect=RuntimeError("usage bug"),
        ):
            events = await _stream(includeRetrievalContext=True)

        assert _answer(events) == _ANSWER
        assert _custom(events, "retrieval_context") == []
        assert _custom(events, "run_usage") == []

