"""`TerminalAnswerStreamer` (`app/agents/agent_loop/answer_streamer.py`) —
live token streaming of the agent's own final-turn answer. Covers: deltas
becoming `answer_chunk` SSE writes with progressively normalized
`accumulated` text and citations resolved via the `ref_to_url` mapping,
a tool-calling turn's preamble being cleared instead of left on screen,
and `streamed_answer` reflecting whichever turn last ended via
`AGENT_COMPLETE`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agents.agent_loop.answer_streamer import TerminalAnswerStreamer
from app.agents.agent_loop.hooks.citations import CitationCollector
from tests.unit.agents.adapter.conftest import make_context

_RUN_CTX = RunContext(role_name="pipeshub-agent", model="gpt-4")


def _event(event_type: EventType, payload: dict | None = None) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=_RUN_CTX, payload=payload or {})


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


def _make_streamer(context=None) -> tuple[TerminalAnswerStreamer, _RecordingSink]:
    context = context or make_context()
    collector = CitationCollector(context)
    sink = _RecordingSink()
    return TerminalAnswerStreamer(context, collector, sink), sink


class TestTextDeltaStreaming:
    async def test_single_delta_streams_answer_chunk(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Hello"}))

        assert len(sink.events) == 1
        assert sink.events[0]["event"] == "answer_chunk"
        assert sink.events[0]["data"]["chunk"] == "Hello"
        assert sink.events[0]["data"]["accumulated"] == "Hello"
        assert sink.events[0]["data"]["citations"] == []

    async def test_multiple_deltas_accumulate(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "The answer"}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": " is 42."}))

        assert len(sink.events) == 2
        assert sink.events[0]["data"]["accumulated"] == "The answer"
        assert sink.events[1]["data"]["accumulated"] == "The answer is 42."
        assert sink.events[1]["data"]["chunk"] == " is 42."

    async def test_empty_delta_is_ignored(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": ""}))

        assert sink.events == []

    async def test_new_turn_resets_buffer(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "First turn text"}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Second"}))

        assert sink.events[-1]["data"]["accumulated"] == "Second"


class TestToolCallPreambleClearing:
    async def test_tool_call_start_clears_streamed_preamble(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Let me check that..."}))
        await streamer.on_event(_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))

        assert len(sink.events) == 2
        clearing = sink.events[-1]
        assert clearing["event"] == "answer_chunk"
        assert clearing["data"]["accumulated"] == ""
        assert clearing["data"]["chunk"] == ""

    async def test_second_tool_call_in_same_turn_does_not_double_clear(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "preamble"}))
        await streamer.on_event(_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))
        await streamer.on_event(_event(EventType.TOOL_CALL_START, {"tool": "confluence_search"}))

        assert len(sink.events) == 2

    async def test_tool_call_start_with_no_preceding_text_is_a_no_op(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TOOL_CALL_START, {"tool": "jira_search"}))

        assert sink.events == []


class TestStreamedAnswerTracking:
    async def test_agent_complete_records_terminal_turn_text(self) -> None:
        streamer, _sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "The answer"}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": " is 42."}))
        await streamer.on_event(_event(EventType.AGENT_COMPLETE, {"output": "The answer is 42."}))

        assert streamer.streamed_answer == "The answer is 42."

    async def test_streamed_answer_defaults_to_empty_when_nothing_streamed(self) -> None:
        streamer, _sink = _make_streamer()

        assert streamer.streamed_answer == ""


class TestCitationResolutionDuringStreaming:
    """Citations must be resolved live via `normalize_citations_and_chunks`
    using the `ref_to_url` mapping from `CitationCollector`, not deferred."""

    async def test_turn_start_snapshots_citation_state_for_the_normalizer(self) -> None:
        context = make_context()
        context.tool_state["citation_ref_mapper"] = SimpleNamespace(
            ref_to_url={"ref1": "https://example.com/report"}
        )
        context.tool_state["final_results"] = ["result-marker"]
        context.tool_state["tool_records"] = ["record-marker"]
        context.tool_state["virtual_record_id_to_result"] = {"v1": {}}
        streamer, _sink = _make_streamer(context)

        with patch(
            "app.agents.agent_loop.answer_streamer.normalize_citations_and_chunks",
            return_value=("normalized", []),
        ) as mock_normalize:
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "hi"}))

        mock_normalize.assert_called_once_with(
            "hi", ["result-marker"], ["record-marker"],
            ref_to_url={"ref1": "https://example.com/report"},
            virtual_record_id_to_result={"v1": {}},
            web_records=[],
        )

    async def test_no_ref_mapper_passes_none_ref_to_url(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Plain text, no citations."}))

        assert sink.events[0]["data"]["accumulated"] == "Plain text, no citations."
        assert sink.events[0]["data"]["citations"] == []

    async def test_citations_returned_by_normalizer_are_forwarded(self) -> None:
        context = make_context()
        fake_citation = {"content": "some content", "chunkIndex": 1, "metadata": {}, "citationType": "vectordb|document"}
        streamer, sink = _make_streamer(context)

        with patch(
            "app.agents.agent_loop.answer_streamer.normalize_citations_and_chunks",
            return_value=("resolved [1](url)", [fake_citation]),
        ):
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "text [source](ref1)"}))

        assert sink.events[0]["data"]["accumulated"] == "resolved [1](url)"
        assert sink.events[0]["data"]["citations"] == [fake_citation]
