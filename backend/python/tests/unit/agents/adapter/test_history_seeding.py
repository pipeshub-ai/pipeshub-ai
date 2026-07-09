"""`_convert_conversation_turn`/`PipesHubAgentFactory._seed_conversation_history`
(`app/agents/agent_loop/factory.py`) — conversation history seeding for
turn 0 of the ReAct loop.

Covers the fix for the "text-only history seeding" non-standard
implementation: a `bot_response` turn that carries a `tool_results` list
(the same shape `result_accumulation.py`/`completion_data["tool_results"]`
already use) must be reconstructed as a proper `AssistantMessage(tool_calls=
[...])` -> `ToolMessage`(s) -> final `AssistantMessage` sequence, not
collapsed into a single plain-text message that silently drops the tool
calls."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage, UserMessage
from app.agents.agent_loop.factory import PipesHubAgentFactory, _convert_conversation_turn


class TestConvertConversationTurn:
    def test_user_query_becomes_user_message(self) -> None:
        messages = _convert_conversation_turn({"role": "user_query", "content": "hello there"})

        assert len(messages) == 1
        assert isinstance(messages[0], UserMessage)
        assert messages[0].content == "hello there"

    def test_empty_user_query_yields_nothing(self) -> None:
        assert _convert_conversation_turn({"role": "user_query", "content": "  "}) == []

    def test_bot_response_without_tool_results_becomes_single_assistant_message(self) -> None:
        messages = _convert_conversation_turn({"role": "bot_response", "content": "the answer is 42"})

        assert len(messages) == 1
        assert isinstance(messages[0], AssistantMessage)
        assert messages[0].text == "the answer is 42"
        assert messages[0].tool_calls is None

    def test_unrecognized_role_yields_nothing(self) -> None:
        assert _convert_conversation_turn({"role": "system_note", "content": "ignored"}) == []

    def test_bot_response_with_tool_results_reconstructs_tool_call_pairs(self) -> None:
        turn = {
            "role": "bot_response",
            "content": "Found 3 issues.",
            "tool_results": [
                {
                    "tool_name": "jira_search",
                    "result": "3 issues found",
                    "status": "success",
                    "tool_id": "call_1",
                    "args": {"query": "open bugs"},
                },
            ],
        }

        messages = _convert_conversation_turn(turn)

        assert len(messages) == 3
        assistant_call, tool_result, final_answer = messages

        assert isinstance(assistant_call, AssistantMessage)
        assert assistant_call.tool_calls is not None
        assert len(assistant_call.tool_calls) == 1
        assert assistant_call.tool_calls[0].id == "call_1"
        assert assistant_call.tool_calls[0].name == "jira_search"
        assert assistant_call.tool_calls[0].arguments == {"query": "open bugs"}
        assert assistant_call.text == ""

        assert isinstance(tool_result, ToolMessage)
        assert tool_result.tool_call_id == "call_1"
        assert tool_result.content == "3 issues found"
        assert tool_result.is_error is False

        assert isinstance(final_answer, AssistantMessage)
        assert final_answer.text == "Found 3 issues."
        assert final_answer.tool_calls is None

    def test_multiple_tool_results_preserve_order_and_ids(self) -> None:
        turn = {
            "role": "bot_response",
            "content": "done",
            "tool_results": [
                {"tool_name": "search", "result": "r1", "status": "success", "tool_id": "id1", "args": {}},
                {"tool_name": "fetch", "result": "r2", "status": "error", "tool_id": "id2", "args": {"x": 1}},
            ],
        }

        messages = _convert_conversation_turn(turn)

        assistant_call = messages[0]
        assert [tc.id for tc in assistant_call.tool_calls] == ["id1", "id2"]
        tool_msg_1, tool_msg_2 = messages[1], messages[2]
        assert tool_msg_1.tool_call_id == "id1"
        assert tool_msg_1.is_error is False
        assert tool_msg_2.tool_call_id == "id2"
        assert tool_msg_2.is_error is True

    def test_missing_tool_id_gets_synthetic_index_based_id(self) -> None:
        turn = {
            "role": "bot_response",
            "content": "done",
            "tool_results": [{"tool_name": "search", "result": "r1", "status": "success", "args": {}}],
        }

        messages = _convert_conversation_turn(turn)

        assert messages[0].tool_calls[0].id == "history_0"
        assert messages[1].tool_call_id == "history_0"

    def test_non_dict_tool_result_entry_is_skipped(self) -> None:
        turn = {"role": "bot_response", "content": "done", "tool_results": ["not-a-dict"]}

        messages = _convert_conversation_turn(turn)

        # No usable tool call reconstructed -> falls back to a plain final answer.
        assert len(messages) == 1
        assert isinstance(messages[0], AssistantMessage)
        assert messages[0].text == "done"

    def test_non_string_result_is_json_encoded(self) -> None:
        turn = {
            "role": "bot_response",
            "content": "done",
            "tool_results": [
                {"tool_name": "search", "result": {"count": 3}, "status": "success", "tool_id": "id1", "args": {}},
            ],
        }

        messages = _convert_conversation_turn(turn)

        assert messages[1].content == '{"count": 3}'

    def test_tool_calls_with_no_final_content_omits_trailing_assistant_message(self) -> None:
        turn = {
            "role": "bot_response",
            "content": "",
            "tool_results": [
                {"tool_name": "search", "result": "r1", "status": "success", "tool_id": "id1", "args": {}},
            ],
        }

        messages = _convert_conversation_turn(turn)

        assert len(messages) == 2
        assert isinstance(messages[0], AssistantMessage)
        assert isinstance(messages[1], ToolMessage)


class _FakeAgent:
    def __init__(self) -> None:
        self._context = None


class TestSeedConversationHistory:
    async def test_seeds_context_manager_with_converted_messages(self) -> None:
        agent = _FakeAgent()
        previous_conversations = [
            {"role": "user_query", "content": "find open bugs"},
            {
                "role": "bot_response",
                "content": "Found 1 bug.",
                "tool_results": [
                    {"tool_name": "jira_search", "result": "1 bug", "status": "success", "tool_id": "c1", "args": {}},
                ],
            },
            {"role": "user_query", "content": "thanks"},
        ]

        await PipesHubAgentFactory._seed_conversation_history(agent, previous_conversations)

        messages = await agent._context.messages()
        assert len(messages) == 5
        assert isinstance(messages[0], UserMessage)
        assert isinstance(messages[1], AssistantMessage) and messages[1].tool_calls
        assert isinstance(messages[2], ToolMessage)
        assert isinstance(messages[3], AssistantMessage) and not messages[3].tool_calls
        assert isinstance(messages[4], UserMessage)

    async def test_empty_history_yields_empty_context(self) -> None:
        agent = _FakeAgent()

        await PipesHubAgentFactory._seed_conversation_history(agent, [])

        assert await agent._context.messages() == []
