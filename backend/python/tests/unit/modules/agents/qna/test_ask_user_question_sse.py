"""Integration-style tests for ask_user_question SSE emission (QnA nodes)."""

import json
from unittest.mock import MagicMock, patch

from app.modules.agents.qna.nodes import _emit_ask_user_question_tool_event


def _tool_result_json() -> str:
    return json.dumps({
        "name": "ask_user_question",
        "userIntent": "Pick a channel",
        "questions": [
            {
                "uuid": "q-1",
                "question": "Which channel?",
                "options": [
                    {"id": "opt_general", "label": "#general", "isUserInput": False},
                ],
                "multiSelect": False,
            },
        ],
    })


def _state_with_ask_tool(*, tool_name: str = "internaltools.ask_user_question", result: str | dict | None = None) -> dict:
    raw = result if result is not None else _tool_result_json()
    return {
        "all_tool_results": [
            {
                "tool_name": tool_name,
                "status": "success",
                "result": raw,
            },
        ],
    }


class TestEmitAskUserQuestionToolEvent:
    def test_skips_emit_when_client_name_missing(self) -> None:
        writer = MagicMock()
        state = _state_with_ask_tool()
        config = {"configurable": {}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write") as mock_write:
            _emit_ask_user_question_tool_event(writer, state, config)

        mock_write.assert_not_called()
        assert "ask_user_question_emitted" not in state

    def test_emits_sse_event_when_client_name_present(self) -> None:
        writer = MagicMock()
        state = _state_with_ask_tool()
        config = {"configurable": {"client_name": "web"}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write") as mock_write:
            _emit_ask_user_question_tool_event(writer, state, config)

        mock_write.assert_called_once()
        assert mock_write.call_args[0][0] is writer
        event = mock_write.call_args[0][1]
        assert event["event"] == "ask_user_question"
        assert event["data"]["status"] == "success"
        tool_data = event["data"]["toolData"]
        assert tool_data["name"] == "ask_user_question"
        assert tool_data["userIntent"] == "Pick a channel"
        assert state["ask_user_question_emitted"] is True

    def test_skips_second_emit_when_already_emitted(self) -> None:
        writer = MagicMock()
        state = _state_with_ask_tool()
        state["ask_user_question_emitted"] = True
        config = {"configurable": {"client_name": "web"}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write") as mock_write:
            _emit_ask_user_question_tool_event(writer, state, config)

        mock_write.assert_not_called()

    def test_accepts_underscore_tool_name_variant(self) -> None:
        writer = MagicMock()
        state = _state_with_ask_tool(tool_name="internaltools_ask_user_question")
        config = {"configurable": {"client_name": "pipeshub-ui"}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write") as mock_write:
            _emit_ask_user_question_tool_event(writer, state, config)

        mock_write.assert_called_once()
        assert mock_write.call_args[0][1]["event"] == "ask_user_question"

    def test_forwards_raw_payload_when_result_is_not_json(self) -> None:
        writer = MagicMock()
        state = _state_with_ask_tool(result="not-valid-json{{{")
        config = {"configurable": {"client_name": "web"}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write") as mock_write:
            _emit_ask_user_question_tool_event(writer, state, config)

        mock_write.assert_called_once()
        assert mock_write.call_args[0][1]["data"]["toolData"] == "not-valid-json{{{"

    def test_does_not_emit_for_unrelated_tools(self) -> None:
        writer = MagicMock()
        state = {
            "all_tool_results": [
                {
                    "tool_name": "slack.send_message",
                    "status": "success",
                    "result": '{"ok": true}',
                },
            ],
        }
        config = {"configurable": {"client_name": "web"}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write") as mock_write:
            _emit_ask_user_question_tool_event(writer, state, config)

        mock_write.assert_not_called()
