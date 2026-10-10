"""`AnswerFinalizer` when the chat window is the newer (AG-UI) client, which
also saves the run as a list of parts: narration the model wrote between
tool calls, tool calls, and the final answer.

What users rely on here: citation numbers must agree across every text part
(narration that cites a source and the final answer that cites it again
must show the same [1]); the saved answer is only the final part; download
cards for files produced during the run are attached to the final answer
and nowhere else; and a stopped run keeps what was already on screen.

The transcript is built by feeding the real `TranscriptCollector` the same
events a real run emits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.agents.agent_loop.respond import AnswerFinalizer
from app.utils.conversation_tasks import register_task
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

_REPORT = "https://example.com/report"
_BLOG = "https://example.com/blog"


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


def _context(**overrides: object) -> AgentContext:
    context = make_context(protocol="agui", transcript_collector=TranscriptCollector(), **overrides)
    context.tool_state["web_records"] = [
        {"url": _REPORT, "title": "Report", "content": "Report body."},
        {"url": _BLOG, "title": "Blog", "content": "Blog body."},
    ]
    return context


async def _stream_turns(collector: TranscriptCollector, *texts: str) -> None:
    run = RunContext(role_name="root", model="m")

    async def emit(event_type: EventType, **payload: object) -> None:
        await collector.emit(AgentEvent(event_type=event_type, run_context=run, payload=payload))

    await emit(EventType.RUN_STARTED)
    for i, text in enumerate(texts):
        await emit(EventType.TEXT_MESSAGE_START)
        await emit(EventType.TEXT_MESSAGE_CONTENT, delta=text)
        await emit(EventType.TEXT_MESSAGE_END)
        if i < len(texts) - 1:
            await emit(EventType.TOOL_CALL_START, tool_call_id=f"t{i}", tool="web_search", args={"q": "x"})
            await emit(EventType.TOOL_CALL_END, tool_call_id=f"t{i}", content="results")


def _text_parts(context: AgentContext) -> list[dict]:
    return [p for p in context.transcript_collector.parts if p["type"] == "text"]


class TestCitationNumbersAgreeAcrossParts:
    async def test_same_source_gets_the_same_number_in_narration_and_answer(self) -> None:
        context = _context()
        narration = f"The [report]({_REPORT}) mentions a delay."
        final = f"Per the [blog]({_BLOG}) and the [report]({_REPORT}), it slipped a week."
        await _stream_turns(context.transcript_collector, narration, final)
        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output=final, streamed_answer=final, event_sink=_Sink(),
        )

        narration_part, final_part = _text_parts(context)
        assert "[1]" in narration_part["content"]
        assert "[2]" in final_part["content"] and "[1]" in final_part["content"]
        assert final_part["content"].index("[2]") < final_part["content"].index("[1]")
        assert final_part["isFinal"] is True
        assert result["answer"] == final_part["content"]
        assert "mentions a delay" not in result["answer"]
        assert len(result["citations"]) == 2
        saved_texts = [p["content"] for p in result["parts"] if p["type"] == "text"]
        assert saved_texts == [narration_part["content"], final_part["content"]]

    async def test_single_text_part_is_normalised_in_place(self) -> None:
        context = _context()
        final = f"See the [report]({_REPORT})."
        await _stream_turns(context.transcript_collector, final)

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output=final, streamed_answer=final, event_sink=_Sink(),
        )

        [only] = _text_parts(context)
        assert only["content"] == result["answer"]
        assert "[1]" in result["answer"]
        assert len(result["citations"]) == 1


class TestTheSignInCard:
    SERVER = {"instanceId": "inst-drive", "serverName": "Drive", "scopes": ["files.write"]}

    async def test_the_saved_parts_end_with_it(self) -> None:
        context = _context(client_name="pipeshub-ai", conversation_id="conv-agentloop-sign-in")
        context.mcp_sign_in_needed.append(dict(self.SERVER))
        final = "Drive needs more permission to upload that."
        await _stream_turns(context.transcript_collector, final)

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output=final, streamed_answer=final, event_sink=_Sink(),
        )

        assert result["parts"][-1] == {"type": "mcp_sign_in", "servers": [self.SERVER]}
        assert result["answer"] == final
        # The transcript itself is unchanged: the card isn't activity.
        assert all(part["type"] != "mcp_sign_in" for part in context.transcript_collector.parts)

    async def test_without_a_server_to_sign_in_to_nothing_is_added(self) -> None:
        context = _context(client_name="pipeshub-ai", conversation_id="conv-agentloop-no-sign-in")
        final = "Done."
        await _stream_turns(context.transcript_collector, final)

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output=final, streamed_answer=final, event_sink=_Sink(),
        )

        assert all(part["type"] != "mcp_sign_in" for part in result["parts"])


class TestATurnThatEndedWaitingForApproval:
    async def test_the_models_text_stays_and_the_approval_message_comes_last(self) -> None:
        from unittest.mock import patch

        from app.agents.agent_loop.tool_approvals import PENDING_MESSAGE

        context = _context()
        message = "Waiting for your approval to run create_issue on Jira."
        context.tool_state[PENDING_MESSAGE] = message
        await _stream_turns(context.transcript_collector, "I found the bug report; filing it now.")

        with patch("app.agents.agent_loop.respond._record_answer_generated") as recorded:
            result = await AnswerFinalizer(context, CitationCollector(context)).run(
                agent_success=True, agent_error=None, agent_output=message,
                streamed_answer="I found the bug report; filing it now.", event_sink=_Sink(),
            )

        narration, final = _text_parts(context)
        assert narration["content"] == "I found the bug report; filing it now."
        assert final == {"type": "text", "content": message, "isFinal": True}
        assert result["answer"] == message
        assert result["answerMatchType"] == "Approval Needed"
        recorded.assert_not_called()

    async def test_a_call_a_sub_agent_left_waiting_still_shows_in_the_answer(self) -> None:
        """A sub-agent run without the parent's scope (static composition) can ask while the parent
        carries on: its card is showing, so the answer says a call is waiting."""
        from app.agents.agent_loop.tool_approvals import PENDING_MESSAGE

        context = _context()
        message = "Waiting for your approval to run create_issue on Jira."
        context.tool_state[PENDING_MESSAGE] = message
        await _stream_turns(context.transcript_collector, "All done.")

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output="All done.", streamed_answer="All done.", event_sink=_Sink(),
        )

        [only] = _text_parts(context)
        assert only["content"] == result["answer"] == f"All done.\n\n{message}"
        assert result["answerMatchType"] == "Approval Needed"

    async def test_an_ordinary_answer_is_unaffected(self) -> None:
        context = _context()
        await _stream_turns(context.transcript_collector, "All done.")

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output="All done.", streamed_answer="All done.", event_sink=_Sink(),
        )

        [only] = _text_parts(context)
        assert only["content"] == "All done."
        assert result.get("answerMatchType") != "Approval Needed"


class TestDownloadCards:
    async def test_files_made_during_the_run_are_attached_to_the_final_answer_only(self) -> None:
        context = _context(conversation_id="conv-agentloop-downloads")
        narration = "Building the spreadsheet now."
        final = "Here is your spreadsheet."
        await _stream_turns(context.transcript_collector, narration, final)

        async def _upload() -> dict:
            return {"type": "artifacts", "artifacts": [
                {"fileName": "sales.xlsx", "mimeType": "application/vnd.ms-excel", "recordId": "rec-9"},
            ]}

        register_task("conv-agentloop-downloads", asyncio.create_task(_upload()))

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output=final, streamed_answer=final, event_sink=_Sink(),
        )

        narration_part, final_part = _text_parts(context)
        assert "sales.xlsx" in result["answer"]
        assert "record:rec-9" in result["answer"]
        assert final_part["content"] == result["answer"]
        assert "sales.xlsx" not in narration_part["content"]

    async def test_markers_the_model_typed_itself_are_removed(self) -> None:
        context = _context(conversation_id="conv-agentloop-no-tasks")
        final = "Done.\n::download_conversation_task[fake.csv](https://evil.example/x)"
        await _stream_turns(context.transcript_collector, final)

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output=final, streamed_answer=final, event_sink=_Sink(),
        )

        assert "evil.example" not in result["answer"]
        assert result["answer"].startswith("Done.")


class TestStoppedRunWithTranscript:
    async def test_stop_keeps_the_streamed_parts_with_normalised_citations(self) -> None:
        context = _context()
        narration = f"Checking the [report]({_REPORT})"
        partial = f"So far the [report]({_REPORT}) says"
        await _stream_turns(context.transcript_collector, narration, partial)
        sink = _Sink()

        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=False, agent_error="Cancelled", agent_cancelled=True,
            streamed_answer=partial, event_sink=sink,
        )

        assert result["status"] == "stopped"
        narration_part, final_part = _text_parts(context)
        assert "[1]" in narration_part["content"] and "[1]" in final_part["content"]
        assert result["answer"] == final_part["content"]
        assert len(result["citations"]) == 1


class TestAskUserQuestionFallbackPayloads:
    async def test_non_json_tool_result_is_passed_through_as_is(self) -> None:
        context = make_context(has_ui_client=True)
        context.tool_state["all_tool_results"] = [
            {"tool_name": "search", "status": "success", "result": "ignored"},
            {"tool_name": "internaltools_ask_user_question", "status": "success", "result": "not json {"},
        ]
        sink = _Sink()

        await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output="Which one?", streamed_answer="", event_sink=sink,
        )

        asks = [e for e in sink.events if e["event"] == "ask_user_question"]
        assert [a["data"]["toolData"] for a in asks] == ["not json {"]
        assert context.tool_state["ask_user_question_emitted"] is True
