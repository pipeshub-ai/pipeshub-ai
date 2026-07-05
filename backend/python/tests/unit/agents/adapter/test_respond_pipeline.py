"""`RespondPipeline` (`app/agents/agent_loop/respond.py`) — Phase 6:
post-agent response synthesis. Heavily mocks the content-bearing helpers it
reuses unchanged from the legacy path (`create_response_messages`,
`stream_llm_response_with_tools`, citation normalization) since those already
have their own test coverage; these tests validate RespondPipeline's OWN
logic — event sequencing, completion_data shape, and the empty-answer /
agent-failure fallback paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.respond import RespondPipeline
from tests.unit.agents.adapter.conftest import make_context


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


def _empty_web_tools(_state: dict) -> list:
    return []


def _patches(*, stream_events: list[dict[str, Any]], messages: list | None = None):
    async def _fake_stream(**_kwargs):
        for event in stream_events:
            yield event

    return (
        patch("app.agents.agent_loop.respond.create_response_messages", new=AsyncMock(return_value=messages or [])),
        patch("app.agents.agent_loop.respond._ensure_attachment_blocks", new=AsyncMock(return_value=[])),
        patch("app.agents.agent_loop.respond._inject_attachment_blocks", new=MagicMock()),
        patch("app.agents.agent_loop.respond._extract_web_records_from_tool_results", return_value=[]),
        patch("app.agents.agent_loop.respond._tool_names_and_results_from_state", return_value={"tool_results": []}),
        patch("app.agents.agent_loop.respond.stream_llm_response_with_tools", new=_fake_stream),
        patch("app.modules.agents.qna.tool_system._create_web_tools", new=_empty_web_tools),
    )


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
            "data": {"answer": "The answer is 42.", "citations": [], "reason": "found it", "confidence": "High"},
        }
        patches = _patches(stream_events=[complete_event])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await pipeline.run(agent_success=True, agent_error=None, event_sink=sink)

        assert result["answer"] == "The answer is 42."
        assert result["confidence"] == "High"
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["status", "complete"]
        assert context.tool_state["completion_data"] == result

    async def test_empty_answer_falls_back_to_default_response(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {"event": "complete", "data": {"answer": "", "citations": []}}
        patches = _patches(stream_events=[complete_event])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await pipeline.run(agent_success=True, agent_error=None, event_sink=sink)

        assert result["answerMatchType"] == "Fallback Response"
        event_types = [e["event"] for e in sink.events]
        # "status" (generating) -> the stream's own empty "complete" event,
        # written as-is -> RespondPipeline's own fallback "answer_chunk" +
        # "complete" pair once it notices the answer was empty.
        assert event_types == ["status", "complete", "answer_chunk", "complete"]

    async def test_exception_during_synthesis_yields_error_response(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        with patch(
            "app.agents.agent_loop.respond.create_response_messages",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await pipeline.run(agent_success=True, agent_error=None, event_sink=sink)

        assert result["answerMatchType"] == "Error"
        assert result["answer"] == "I encountered an issue while processing your request. Please try again."

    async def test_reference_data_normalized_when_present(self) -> None:
        context = make_context()
        pipeline = RespondPipeline(context, CitationCollector(context))
        sink = _RecordingSink()

        complete_event = {
            "event": "complete",
            "data": {"answer": "42", "citations": [{"id": "c1"}], "referenceData": [{"raw": "item"}]},
        }
        patches = _patches(stream_events=[complete_event])
        normalize_patch = patch(
            "app.agents.agent_loop.respond.normalize_reference_data_items",
            return_value=[{"normalized": "item"}],
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], normalize_patch as norm_mock:
            result = await pipeline.run(agent_success=True, agent_error=None, event_sink=sink)

        norm_mock.assert_called_once_with([{"raw": "item"}])
        assert result["referenceData"] == [{"normalized": "item"}]
