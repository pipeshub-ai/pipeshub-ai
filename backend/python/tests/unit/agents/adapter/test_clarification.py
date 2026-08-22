"""`emit_pre_run_clarification` (`app/agents/agent_loop/clarification.py`) —
the pre-run short-circuit `stream_bridge.py` calls instead of `Agent.run()`/
`RespondPipeline` when `IntentRouteDecision.clarifying_questions` is
non-empty. Covers:

- The `ask_user_question` SSE event only fires for UI clients, matching
  `ask_user_question_sse`'s own `has_ui_client` gate.
- The `toolData` payload shape matches what `InternalTools.ask_user_question()`
  produces for a real mid-run tool call (uuid/option-id normalization).
- `answer_chunk`/`complete` always fire regardless of `has_ui_client`, with
  `answerMatchType: "Clarification Needed"`.
- `AgentContext.tool_state` bookkeeping (`response`/`completion_data`/
  `ask_user_question_emitted`) matches what `RespondPipeline` sets on its
  own completion paths, so downstream conversation-history persistence
  (which reads `tool_state`) behaves identically either way.
"""

from __future__ import annotations

from typing import Any

from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
from app.agents.agent_loop.clarification import emit_pre_run_clarification
from tests.unit.agents.adapter.conftest import make_context


class _FakeEventSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


def _question(text: str = "Which project should this apply to?") -> AskUserQuestionItemInput:
    return AskUserQuestionItemInput(
        question=text,
        options=[{"label": "Project A"}, {"label": "Project B"}, {"label": "Project C"}],
        multiSelect=False,
    )


class TestEmitPreRunClarification:
    async def test_emits_ask_user_question_event_for_ui_client(self) -> None:
        context = make_context(has_ui_client=True)
        sink = _FakeEventSink()

        await emit_pre_run_clarification(
            context, "user wants to post an update", [_question()], event_sink=sink,
        )

        ask_events = [e for e in sink.events if e["event"] == "ask_user_question"]
        assert len(ask_events) == 1
        tool_data = ask_events[0]["data"]["toolData"]
        assert tool_data["name"] == "ask_user_question"
        assert tool_data["userIntent"] == "user wants to post an update"
        assert len(tool_data["questions"]) == 1
        question = tool_data["questions"][0]
        assert question["question"] == "Which project should this apply to?"
        assert {opt["label"] for opt in question["options"]} == {"Project A", "Project B", "Project C"}
        assert all("id" in opt for opt in question["options"])
        assert "uuid" in question

    async def test_skips_ask_user_question_event_without_ui_client(self) -> None:
        context = make_context(has_ui_client=False)
        sink = _FakeEventSink()

        await emit_pre_run_clarification(
            context, "user intent", [_question()], event_sink=sink,
        )

        assert not [e for e in sink.events if e["event"] == "ask_user_question"]

    async def test_always_emits_answer_chunk_and_complete(self) -> None:
        context = make_context(has_ui_client=False)
        sink = _FakeEventSink()

        result = await emit_pre_run_clarification(
            context, "user intent", [_question("Which channel?")], event_sink=sink,
        )

        event_types = [e["event"] for e in sink.events]
        assert event_types == ["answer_chunk", "complete"]
        complete_data = sink.events[-1]["data"]
        assert complete_data["answer"] == "Which channel?"
        assert complete_data["answerMatchType"] == "Clarification Needed"
        assert complete_data["citations"] == []
        assert result == complete_data

    async def test_falls_back_to_user_intent_when_no_questions(self) -> None:
        context = make_context(has_ui_client=False)
        sink = _FakeEventSink()

        result = await emit_pre_run_clarification(
            context, "fallback text", [], event_sink=sink,
        )

        assert result["answer"] == "fallback text"

    async def test_sets_tool_state_bookkeeping(self) -> None:
        context = make_context(has_ui_client=True)
        sink = _FakeEventSink()

        await emit_pre_run_clarification(
            context, "user intent", [_question()], event_sink=sink,
        )

        assert context.tool_state["response"] == "Which project should this apply to?"
        assert context.tool_state["completion_data"]["answerMatchType"] == "Clarification Needed"
        assert context.tool_state["ask_user_question_emitted"] is True

    async def test_multiple_questions_all_included_in_payload(self) -> None:
        context = make_context(has_ui_client=True)
        sink = _FakeEventSink()
        questions = [_question("Which channel?"), _question("Which priority?")]

        await emit_pre_run_clarification(context, "user intent", questions, event_sink=sink)

        tool_data = [e for e in sink.events if e["event"] == "ask_user_question"][0]["data"]["toolData"]
        assert len(tool_data["questions"]) == 2
