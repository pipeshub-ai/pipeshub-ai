"""`ProtocolFormatter` (AG-UI migration plan, Phase 1b) — the wire-shape
strategy `AnswerFinalizer`/`TerminalAnswerStreamer`/`emit_pre_run_clarification`/
the `ask_user_question` hook/`tool_adapter`/`sandbox_bridge` all delegate to
via `AgentContext.formatter` instead of hand-building `{event, data}` dicts.
`LegacyFormatter` must stay byte-identical to the pre-migration literals;
`AGUIFormatter` must produce valid AG-UI frames carrying `context.run_id`.
"""

from __future__ import annotations

from app.agents.agent_loop.protocol.formatter import AGUIFormatter, LegacyFormatter
from tests.unit.agents.adapter.conftest import make_context


class TestLegacyFormatter:
    def setup_method(self) -> None:
        self.formatter = LegacyFormatter()
        self.context = make_context()

    def test_answer_delta_without_confidence(self) -> None:
        frames = self.formatter.answer_delta(
            self.context, chunk="Hel", accumulated="Hel", citations=[{"id": 1}],
        )

        assert frames == [
            {"event": "answer_chunk", "data": {"chunk": "Hel", "accumulated": "Hel", "citations": [{"id": 1}]}}
        ]

    def test_answer_delta_with_confidence(self) -> None:
        frames = self.formatter.answer_delta(
            self.context, chunk="lo", accumulated="Hello", citations=[], confidence="high",
        )

        assert frames[0]["data"]["confidence"] == "high"

    def test_answer_final_passes_completion_data_through_unchanged(self) -> None:
        completion_data = {"answer": "42", "citations": []}

        frames = self.formatter.answer_final(self.context, completion_data=completion_data)

        assert frames == [{"event": "complete", "data": completion_data}]

    def test_ask_user_question(self) -> None:
        frames = self.formatter.ask_user_question(self.context, status="pending", tool_data={"question": "Which?"})

        assert frames == [
            {"event": "ask_user_question", "data": {"status": "pending", "toolData": {"question": "Which?"}}}
        ]

    def test_artifact(self) -> None:
        frames = self.formatter.artifact(self.context, artifact_data={"name": "report.pdf"})

        assert frames == [{"event": "artifact", "data": {"name": "report.pdf"}}]

    def test_tool_unavailable(self) -> None:
        frames = self.formatter.tool_unavailable(
            self.context, tool="jira_search", toolset="jira", reason="not_authenticated", message="Connect Jira",
        )

        assert frames == [{
            "event": "tool_unavailable",
            "data": {"tool": "jira_search", "toolset": "jira", "reason": "not_authenticated", "message": "Connect Jira"},
        }]

    def test_error(self) -> None:
        frames = self.formatter.error(self.context, message="Rate limited", code="rate_limit")

        assert frames == [{"event": "error", "data": {"message": "Rate limited", "type": "rate_limit"}}]


class TestAGUIFormatter:
    def setup_method(self) -> None:
        self.formatter = AGUIFormatter()
        self.context = make_context(protocol="agui", run_id="run-1", conversation_id="thread-1")

    def test_answer_delta_emits_state_delta_with_replace_ops(self) -> None:
        frames = self.formatter.answer_delta(
            self.context, chunk="Hel", accumulated="Hel", citations=[{"id": 1}],
        )

        assert len(frames) == 1
        frame = frames[0]
        assert frame["event"] == "STATE_DELTA"
        assert frame["data"]["runId"] == "run-1"
        ops = {op["path"]: op["value"] for op in frame["data"]["delta"]}
        assert ops["/citations"] == [{"id": 1}]
        assert ops["/normalizedAnswer"] == "Hel"

    def test_answer_delta_includes_confidence_when_given(self) -> None:
        frames = self.formatter.answer_delta(
            self.context, chunk="lo", accumulated="Hello", citations=[], confidence="high",
        )

        ops = {op["path"]: op["value"] for op in frames[0]["data"]["delta"]}
        assert ops["/confidence"] == "high"

    def test_answer_final_emits_snapshot_then_run_finished(self) -> None:
        completion_data = {"answer": "42", "citations": []}

        frames = self.formatter.answer_final(self.context, completion_data=completion_data)

        assert [f["event"] for f in frames] == ["STATE_SNAPSHOT", "RUN_FINISHED"]
        assert frames[0]["data"]["snapshot"] == {"final": True, **completion_data}
        assert frames[0]["data"]["runId"] == "run-1"
        assert frames[1]["data"]["runId"] == "run-1"
        assert frames[1]["data"]["threadId"] == "thread-1"
        assert frames[1]["data"]["result"] == completion_data

    def test_ask_user_question_wraps_in_custom_event(self) -> None:
        frames = self.formatter.ask_user_question(self.context, status="pending", tool_data={"question": "Which?"})

        assert frames[0]["event"] == "CUSTOM"
        assert frames[0]["data"]["name"] == "ask_user_question"
        assert frames[0]["data"]["value"] == {"status": "pending", "toolData": {"question": "Which?"}}
        assert frames[0]["data"]["runId"] == "run-1"

    def test_artifact_wraps_in_custom_event(self) -> None:
        frames = self.formatter.artifact(self.context, artifact_data={"name": "report.pdf"})

        assert frames[0]["event"] == "CUSTOM"
        assert frames[0]["data"]["name"] == "artifact"
        assert frames[0]["data"]["value"] == {"name": "report.pdf"}

    def test_tool_unavailable_wraps_in_custom_event(self) -> None:
        frames = self.formatter.tool_unavailable(
            self.context, tool="jira_search", toolset="jira", reason="not_authenticated", message="Connect Jira",
        )

        assert frames[0]["event"] == "CUSTOM"
        assert frames[0]["data"]["name"] == "tool_unavailable"
        assert frames[0]["data"]["value"] == {
            "tool": "jira_search", "toolset": "jira", "reason": "not_authenticated", "message": "Connect Jira",
        }

    def test_error_emits_run_error(self) -> None:
        frames = self.formatter.error(self.context, message="Rate limited", code="rate_limit")

        assert frames == [{
            "event": "RUN_ERROR",
            "data": {"type": "RUN_ERROR", "runId": "run-1", "message": "Rate limited", "code": "rate_limit"},
        }]


class TestAgentContextFormatterSelection:
    def test_default_protocol_selects_legacy_formatter(self) -> None:
        context = make_context()

        assert isinstance(context.formatter, LegacyFormatter)

    def test_agui_protocol_selects_agui_formatter(self) -> None:
        context = make_context(protocol="agui")

        assert isinstance(context.formatter, AGUIFormatter)
