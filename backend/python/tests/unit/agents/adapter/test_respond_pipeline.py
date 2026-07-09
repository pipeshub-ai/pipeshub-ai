"""`RespondPipeline` (`app/agents/agent_loop/respond.py`) — Phase 6: formats
the agent's OWN final-turn answer with citations and streams it out. No
longer a second LLM call (see `respond.py`'s module docstring) — this test
suite mocks `stream_llm_response_with_tools` (already covered by its own
tests) to validate RespondPipeline's OWN logic: it must NOT run tools, must
feed the agent's raw output through as the last message, and must correctly
handle event sequencing, completion_data shape, and the empty-answer /
agent-failure fallback paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.respond import RespondPipeline
from tests.unit.agents.adapter.conftest import make_context


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


def _patch_stream(stream_events: list[dict[str, Any]], captured_kwargs: dict[str, Any] | None = None):
    async def _fake_stream(**kwargs):
        if captured_kwargs is not None:
            captured_kwargs.update(kwargs)
        for event in stream_events:
            yield event

    return patch("app.agents.agent_loop.respond.stream_llm_response_with_tools", new=_fake_stream)


class TestErrorPath:
    async def test_agent_failure_emits_error_and_returns_error_response(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await pipeline.run(agent_success=False, agent_error="tool exploded", event_sink=sink)

        assert result["answer"] == "I encountered an issue while processing your request. Please try again."
        assert result["answerMatchType"] == "Error"
        assert result["errorCode"] == "unknown"
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["answer_chunk", "complete"]
        assert context.tool_state["response"] == result["answer"]

    async def test_agent_failure_with_no_error_message_uses_generic_text(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await pipeline.run(agent_success=False, agent_error=None, event_sink=sink)

        assert result["errorCode"] == "unknown"
        assert result["answer"] == "I encountered an issue while processing your request. Please try again."

    async def test_agent_failure_classifies_rate_limit_error(self) -> None:
        """LLM 429s (see `error_classification.py`) must surface as a
        distinct `errorCode` with a friendly, non-technical message —
        regression guard for the raw `LLM call failed: ... 429 ...` dump
        this classification replaced."""
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await pipeline.run(
            agent_success=False,
            agent_error="LLM call failed: LangChain transport error (complete): Error code: 429 - rate limit exceeded",
            event_sink=sink,
        )

        assert result["errorCode"] == "rate_limit"
        assert result["answer"] == "The AI service is currently rate limited. Please try again in a moment."


class TestSuccessPath:
    async def test_produces_completion_data_from_streamed_complete_event(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {
            "event": "complete",
            "data": {"answer": "The answer is 42.", "citations": [], "confidence": "High"},
        }
        with _patch_stream([complete_event]):
            result = await pipeline.run(
                agent_success=True, agent_error=None, agent_output="The answer is 42.", event_sink=sink,
            )

        assert result["answer"] == "The answer is 42."
        assert result["confidence"] == "High"
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["status", "complete"]
        assert context.tool_state["completion_data"] == result

    async def test_agent_output_passed_through_as_last_message_with_no_tools(self) -> None:
        """The whole point of this fix: no tools, no second LLM call — just
        the agent's own output text streamed through as the final AI
        message."""
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()
        captured: dict[str, Any] = {}

        complete_event = {"event": "complete", "data": {"answer": "final answer", "citations": []}}
        with _patch_stream([complete_event], captured):
            await pipeline.run(
                agent_success=True, agent_error=None, agent_output="final answer", event_sink=sink,
            )

        assert captured["tools"] is None
        assert captured["tool_runtime_kwargs"] is None
        assert captured["mode"] == "simple"
        messages = captured["messages"]
        assert len(messages) == 1
        assert isinstance(messages[0], AIMessage)
        assert messages[0].content == "final answer"

    async def test_none_agent_output_coerced_to_empty_string(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()
        captured: dict[str, Any] = {}

        complete_event = {"event": "complete", "data": {"answer": "", "citations": []}}
        with _patch_stream([complete_event], captured):
            await pipeline.run(agent_success=True, agent_error=None, agent_output=None, event_sink=sink)

        assert captured["messages"][0].content == ""

    async def test_empty_answer_falls_back_to_default_response(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {"event": "complete", "data": {"answer": "", "citations": []}}
        with _patch_stream([complete_event]):
            result = await pipeline.run(
                agent_success=True, agent_error=None, agent_output="", event_sink=sink,
            )

        assert result["answerMatchType"] == "Fallback Response"
        event_types = [e["event"] for e in sink.events]
        # "status" (generating) -> the stream's own empty "complete" event,
        # written as-is -> RespondPipeline's own fallback "answer_chunk" +
        # "complete" pair once it notices the answer was empty.
        assert event_types == ["status", "complete", "answer_chunk", "complete"]

    async def test_exception_during_streaming_yields_error_response(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        with patch(
            "app.agents.agent_loop.respond.stream_llm_response_with_tools",
            side_effect=RuntimeError("boom"),
        ):
            result = await pipeline.run(
                agent_success=True, agent_error=None, agent_output="hi", event_sink=sink,
            )

        assert result["answerMatchType"] == "Error"
        assert result["answer"] == "I encountered an issue while processing your request. Please try again."

    async def test_citations_and_confidence_passed_through_from_complete_event(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {
            "event": "complete",
            "data": {"answer": "42", "citations": [{"id": "c1"}], "confidence": "Medium"},
        }
        with _patch_stream([complete_event]):
            result = await pipeline.run(
                agent_success=True, agent_error=None, agent_output="42", event_sink=sink,
            )

        assert result["citations"] == [{"id": "c1"}]
        assert result["confidence"] == "Medium"
        # The old structured-JSON-only fields no longer appear on the
        # success path (see respond.py's module docstring).
        assert "referenceData" not in result
        assert "answerMatchType" not in result

    async def test_answer_chunk_events_forwarded_before_complete(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        events = [
            {"event": "answer_chunk", "data": {"chunk": "42", "accumulated": "42", "citations": []}},
            {"event": "complete", "data": {"answer": "42", "citations": []}},
        ]
        with _patch_stream(events):
            await pipeline.run(agent_success=True, agent_error=None, agent_output="42", event_sink=sink)

        event_types = [e["event"] for e in sink.events]
        assert event_types == ["status", "answer_chunk", "complete"]


class TestAskUserQuestionFallback:
    async def test_emits_ask_user_question_when_ui_client_and_not_already_emitted(self) -> None:
        context = make_context(has_ui_client=True)
        context.tool_state["all_tool_results"] = [
            {
                "tool_name": "internaltools_ask_user_question",
                "status": "success",
                "result": '{"question": "which one?"}',
            }
        ]
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {"event": "complete", "data": {"answer": "42", "citations": []}}
        with _patch_stream([complete_event]):
            await pipeline.run(agent_success=True, agent_error=None, agent_output="42", event_sink=sink)

        ask_events = [e for e in sink.events if e["event"] == "ask_user_question"]
        assert len(ask_events) == 1
        assert ask_events[0]["data"]["toolData"] == {"question": "which one?"}
        assert context.tool_state["ask_user_question_emitted"] is True

    async def test_skips_when_already_emitted(self) -> None:
        context = make_context(has_ui_client=True)
        context.tool_state["ask_user_question_emitted"] = True
        context.tool_state["all_tool_results"] = [
            {
                "tool_name": "internaltools_ask_user_question",
                "status": "success",
                "result": "{}",
            }
        ]
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {"event": "complete", "data": {"answer": "42", "citations": []}}
        with _patch_stream([complete_event]):
            await pipeline.run(agent_success=True, agent_error=None, agent_output="42", event_sink=sink)

        assert not [e for e in sink.events if e["event"] == "ask_user_question"]
