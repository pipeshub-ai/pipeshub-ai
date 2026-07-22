"""`AnswerFinalizer` (`app/agents/agent_loop/respond.py`) — deterministic,
no-LLM post-processing of the agent's OWN final-turn answer. Validates that
it: never calls an LLM (there's nothing left in this class that could —
`finalize_agent_answer` is pure Python), emits a single full-text fallback
`answer_chunk` only when nothing (or the wrong thing) was already streamed
live by `TerminalAnswerStreamer`, and correctly handles completion_data
shape and the empty-answer / agent-failure fallback paths."""

from __future__ import annotations

from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.respond import AnswerFinalizer
from tests.unit.agents.adapter.conftest import make_context


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


class TestErrorPath:
    async def test_agent_failure_emits_error_and_returns_error_response(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(agent_success=False, agent_error="tool exploded", event_sink=sink)

        assert result["answer"] == "I encountered an issue while processing your request. Please try again."
        assert result["answerMatchType"] == "Error"
        assert result["errorCode"] == "unknown"
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["answer_chunk", "complete"]
        assert context.tool_state["response"] == result["answer"]

    async def test_agent_failure_with_no_error_message_uses_generic_text(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(agent_success=False, agent_error=None, event_sink=sink)

        assert result["errorCode"] == "unknown"
        assert result["answer"] == "I encountered an issue while processing your request. Please try again."

    async def test_agent_failure_classifies_rate_limit_error(self) -> None:
        """LLM 429s (see `error_classification.py`) must surface as a
        distinct `errorCode` with a friendly, non-technical message —
        regression guard for the raw `LLM call failed: ... 429 ...` dump
        this classification replaced."""
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(
            agent_success=False,
            agent_error="LLM call failed: LangChain transport error (complete): Error code: 429 - rate limit exceeded",
            event_sink=sink,
        )

        assert result["errorCode"] == "rate_limit"
        assert result["answer"] == "The AI service is currently rate limited. Please try again in a moment."


class TestSuccessPath:
    async def test_streamed_answer_matches_agent_output_emits_authoritative_chunk_then_complete(self) -> None:
        """When `TerminalAnswerStreamer` already streamed the text live, the
        finalizer still emits one authoritative `answer_chunk` (confidence-
        stripped, task-marker-appended) so the streaming state is fully
        corrected before `complete`. The `chunk` field is empty (content was
        already streamed), `accumulated` carries the finalized text."""
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(
            agent_success=True, agent_error=None, agent_output="The answer is 42.",
            event_sink=sink, streamed_answer="The answer is 42.",
        )

        assert result["answer"] == "The answer is 42."
        assert result["citations"] == []
        assert context.tool_state["completion_data"] == result
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["answer_chunk", "complete"]
        assert sink.events[0]["data"]["chunk"] == ""
        assert sink.events[0]["data"]["accumulated"] == "The answer is 42."

    async def test_mismatched_streamed_answer_emits_single_fallback_chunk(self) -> None:
        """Nothing was live-streamed for the terminal turn (streaming
        disabled, or produced no text) — the final text must still reach
        the client, as ONE full-text chunk rather than a per-word replay."""
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(
            agent_success=True, agent_error=None, agent_output="final answer",
            event_sink=sink, streamed_answer="",
        )

        assert result["answer"] == "final answer"
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["answer_chunk", "complete"]
        assert sink.events[0]["data"]["accumulated"] == "final answer"

    async def test_none_agent_output_falls_back_to_default_response(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(agent_success=True, agent_error=None, agent_output=None, event_sink=sink)

        assert result["answerMatchType"] == "Fallback Response"

    async def test_empty_answer_falls_back_to_default_response(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(
            agent_success=True, agent_error=None, agent_output="", event_sink=sink,
        )

        assert result["answerMatchType"] == "Fallback Response"
        event_types = [e["event"] for e in sink.events]
        assert event_types == ["answer_chunk", "complete"]

    async def test_exception_during_finalization_yields_error_response(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        # `CitationCollector.citation_ref_mapper` normally returns `None` or
        # a `CitationRefMapper`; force a broken shape (no `.ref_to_url`) to
        # exercise the class's own exception -> error-response fallback.
        context.tool_state["citation_ref_mapper"] = object()

        result = await finalizer.run(
            agent_success=True, agent_error=None, agent_output="hi", event_sink=sink,
            streamed_answer="",
        )

        assert result["answerMatchType"] == "Error"
        assert result["answer"] == "I encountered an issue while processing your request. Please try again."

    async def test_citations_and_confidence_normalized_from_agent_output(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        result = await finalizer.run(
            agent_success=True, agent_error=None,
            agent_output="The answer is 42.\n---\nConfidence: High",
            event_sink=sink, streamed_answer="",
        )

        assert result["answer"] == "The answer is 42."
        assert result["confidence"] == "High"
        assert result["citations"] == []
        # The old structured-JSON-only fields no longer appear on the
        # success path (see respond.py's module docstring).
        assert "referenceData" not in result

    async def test_answer_chunk_carries_citations_and_confidence(self) -> None:
        context = make_context()
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        await finalizer.run(
            agent_success=True, agent_error=None,
            agent_output="42.\n---\nConfidence: Medium",
            event_sink=sink, streamed_answer="",
        )

        chunk_event = next(e for e in sink.events if e["event"] == "answer_chunk")
        assert chunk_event["data"]["confidence"] == "Medium"
        assert chunk_event["data"]["citations"] == []


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
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        await finalizer.run(
            agent_success=True, agent_error=None, agent_output="42",
            event_sink=sink, streamed_answer="42",
        )

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
        finalizer = AnswerFinalizer(context, CitationCollector(context))
        sink = _RecordingSink()

        await finalizer.run(
            agent_success=True, agent_error=None, agent_output="42",
            event_sink=sink, streamed_answer="42",
        )

        assert not [e for e in sink.events if e["event"] == "ask_user_question"]
