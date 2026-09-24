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


def _make_streamer(context=None, *, emit_interval: float = 0.0) -> tuple[TerminalAnswerStreamer, _RecordingSink]:
    """Throttling is off by default so these tests see one emit per delta and
    can assert on emit CONTENT; `TestEmitRateLimit` covers the rate itself."""
    context = context or make_context()
    collector = CitationCollector(context)
    sink = _RecordingSink()
    streamer = TerminalAnswerStreamer(context, collector, sink)
    streamer._emit_interval = emit_interval
    return streamer, sink


class TestEmitRateLimit:
    """`_emit_state_delta` re-normalizes the whole accumulated answer and
    rebuilds every citation, so running it per token is quadratic in answer
    length — 22% of query-service CPU. It is rate-limited instead; the final
    state must still be emitted when the turn ends."""

    async def test_deltas_inside_the_interval_do_not_each_emit(self) -> None:
        streamer, sink = _make_streamer(emit_interval=10.0)

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        for i in range(50):
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": f"tok{i} "}))

        assert len(sink.events) == 1, "only the first delta of the window emits"

    async def test_withheld_delta_is_flushed_at_agent_complete(self) -> None:
        streamer, sink = _make_streamer(emit_interval=10.0)

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "The answer"}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": " is 42."}))
        assert sink.events[-1]["data"]["accumulated"] == "The answer"  # 2nd withheld

        await streamer.on_event(_event(EventType.AGENT_COMPLETE))

        assert sink.events[-1]["data"]["accumulated"] == "The answer is 42."
        assert streamer.streamed_answer == "The answer is 42."

    async def test_no_extra_emit_when_nothing_was_withheld(self) -> None:
        """A turn the limiter never throttled must not pay for a second
        full-size frame at AGENT_COMPLETE."""
        streamer, sink = _make_streamer(emit_interval=10.0)

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "The answer is 42."}))
        emitted = len(sink.events)

        await streamer.on_event(_event(EventType.AGENT_COMPLETE))

        assert len(sink.events) == emitted

    async def test_interval_elapsing_allows_the_next_emit(self, monkeypatch) -> None:
        """Drives a controlled clock rather than sleeping: a real wait makes the
        test both slow and dependent on how promptly the loop reschedules."""
        from app.agents.agent_loop import answer_streamer as mod

        now = [1000.0]
        monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
        streamer, sink = _make_streamer(emit_interval=0.01)

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "a"}))
        now[0] += 0.03
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "b"}))

        assert len(sink.events) == 2

    async def test_new_turn_resets_the_window(self) -> None:
        streamer, sink = _make_streamer(emit_interval=10.0)

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "first"}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "second"}))

        assert [e["data"]["accumulated"] for e in sink.events] == ["first", "second"]



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

    async def test_turn_start_reads_web_records_from_tool_state(self) -> None:
        """`web_records` now lives directly on `tool_state` (populated by
        `WebToolAdapter.execute()`), and `CitationCollector.web_records`
        exposes it — the streamer must forward it to the normalizer as-is."""
        context = make_context()
        web_record = {"url": "https://example.com/report", "title": "Report", "content": "..."}
        context.tool_state["web_records"] = [web_record]
        streamer, _sink = _make_streamer(context)

        with patch(
            "app.agents.agent_loop.answer_streamer.normalize_citations_and_chunks",
            return_value=("normalized", []),
        ) as mock_normalize:
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "hi"}))

        assert mock_normalize.call_args.kwargs["web_records"] == [web_record]

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


class TestConfidenceTrailerHiddenWhileStreaming:
    """The prompt asks the model to end its final answer with
    `---\\nConfidence: <level>` (`prompt_builder.py`'s `_ANSWER_CONFIDENCE`)
    so `AnswerFinalizer` can report a confidence level. Since the frontend
    renders `accumulated` by replacement, the marker must never appear there
    — not in its complete form, and not in the partial states it streams
    through token by token."""

    async def test_trailer_never_reaches_the_wire(self) -> None:
        streamer, sink = _make_streamer()
        deltas = ["The answer is 42.", "\n\n-", "--", "\nConfidence", ":", " High"]

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        for delta in deltas:
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": delta}))

        accumulated = [evt["data"]["accumulated"] for evt in sink.events]
        assert all(text == "The answer is 42." for text in accumulated), accumulated

    async def test_parsed_level_is_forwarded_once_the_trailer_completes(self) -> None:
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Done."}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "\n---\nConfidence: Medium"}))

        assert "confidence" not in sink.events[0]["data"]
        assert sink.events[-1]["data"]["confidence"] == "Medium"

    async def test_trailer_without_the_horizontal_rule_is_still_hidden(self) -> None:
        """Observed in production: the model ended with a bare
        `Confidence: High` line, reading the prompt's `---` as a separator
        belonging to the prompt rather than to its output. The rule-less shape
        used to reach the client verbatim as answer text."""
        streamer, sink = _make_streamer()
        deltas = ["The answer is 42.", "\n\n", "Conf", "idence:", " High"]

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        for delta in deltas:
            await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": delta}))

        accumulated = [evt["data"]["accumulated"] for evt in sink.events]
        assert all(text.strip() == "The answer is 42." for text in accumulated), accumulated
        assert sink.events[-1]["data"]["confidence"] == "High"

    async def test_emphasised_lowercase_trailer_is_normalized(self) -> None:
        """`**confidence:** high` must reach the frontend as `High` — its
        indicator switches on the exact-cased level and renders nothing for an
        unrecognized one (`chat/components/message-area/confidence-indicator.tsx`)."""
        streamer, sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Done."}))
        await streamer.on_event(
            _event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "\n\n**confidence:** very high"})
        )

        assert sink.events[-1]["data"]["accumulated"] == "Done."
        assert sink.events[-1]["data"]["confidence"] == "Very High"

    async def test_agui_state_delta_reports_raw_length_covering_the_trailer(self) -> None:
        """AG-UI clients accumulate raw TEXT_MESSAGE_CONTENT (which still
        carries the trailer) and only swap to `normalizedAnswer` when
        `rawLength` covers that buffer — without it the shorter stripped
        text is rejected and `Confidence: High` stays on screen."""
        context = make_context(protocol="agui")
        streamer, sink = _make_streamer(context)
        body = "The answer is 42."
        trailer = "\n---\nConfidence: High"

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": body}))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": trailer}))

        last = sink.events[-1]
        assert last["event"] == "STATE_DELTA"
        ops = {op["path"]: op["value"] for op in last["data"]["delta"]}
        assert ops["/normalizedAnswer"] == body
        assert ops["/rawLength"] == len(body + trailer)
        assert ops["/confidence"] == "High"

    async def test_streamed_answer_keeps_the_raw_trailer_for_the_finalizer(self) -> None:
        """`AnswerFinalizer` compares `streamed_answer` against
        `AgentResult.output` (both raw, trailer included) — stripping is a
        presentation concern only."""
        streamer, _sink = _make_streamer()

        await streamer.on_event(_event(EventType.TEXT_MESSAGE_START))
        await streamer.on_event(_event(EventType.TEXT_MESSAGE_CONTENT, {"delta": "Done.\n---\nConfidence: Low"}))
        await streamer.on_event(_event(EventType.AGENT_COMPLETE, {}))

        assert streamer.streamed_answer == "Done.\n---\nConfidence: Low"
