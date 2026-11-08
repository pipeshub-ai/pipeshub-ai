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
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory, _convert_conversation_turn


def _make_context(**overrides: object) -> AgentContext:
    defaults: dict[str, object] = {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "u@example.com",
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


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

    def test_v1_system_note_marker_is_scrubbed_from_replayed_history(self) -> None:
        """Conversations persisted under the V1 `[SYSTEM NOTE ...]` header
        replay that marker inside tool results on every later turn — text
        impersonating the system inside a tool result is exactly what
        tripped Azure OpenAI's jailbreak prompt shield (400
        `content_filter`, whole request rejected). The replay path must
        neutralize it or an affected conversation stays permanently broken."""
        legacy = (
            "Findings: seats grew ~9x.\n---\n"
            "[SYSTEM NOTE — how to use the findings above in your answer]\n"
            "Rules: ..."
        )
        turn = {
            "role": "bot_response",
            "content": f"Summary based on: {legacy}",
            "tool_results": [
                {"tool_name": "internal_exploration_agent", "result": legacy,
                 "status": "success", "tool_id": "c1", "args": {}},
            ],
        }

        messages = _convert_conversation_turn(turn)

        tool_result, final_answer = messages[1], messages[2]
        assert "[SYSTEM NOTE" not in tool_result.content
        assert "About these findings:" in tool_result.content
        assert "Findings: seats grew ~9x." in tool_result.content
        assert "[SYSTEM NOTE" not in final_answer.text

    def test_v2_guidance_header_is_scrubbed_from_replayed_history(self) -> None:
        """Conversations persisted under the V2 header ("Guidance for using
        the findings above ... Rules:") still triggered Azure's content
        filter in `planExecute` mode — the imperative bullets inside the
        tool result, combined with injected phase-instruction user messages,
        crossed the filter threshold. The replay path must neutralize this
        header too."""
        v2 = (
            "Findings: seats grew ~9x.\n---\n"
            "Guidance for using the findings above in the final answer:\n"
            "Rules: ..."
        )
        turn = {
            "role": "bot_response",
            "content": "Summary",
            "tool_results": [
                {"tool_name": "internal_exploration_agent", "result": v2,
                 "status": "success", "tool_id": "c1", "args": {}},
            ],
        }

        messages = _convert_conversation_turn(turn)

        tool_result = messages[1]
        assert "Guidance for using the findings above" not in tool_result.content
        assert "About these findings:" in tool_result.content
        assert "Findings: seats grew ~9x." in tool_result.content


class _FakeAgent:
    def __init__(self) -> None:
        self._context = None

    @property
    def context(self):
        return self._context

    def seed_context(self, context) -> None:
        self._context = context


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

        await PipesHubAgentFactory._seed_conversation_history(
            agent, previous_conversations, _make_context()
        )

        messages = await agent.context.messages()
        assert len(messages) == 6
        assert isinstance(messages[0], UserMessage)
        assert isinstance(messages[1], AssistantMessage) and messages[1].tool_calls
        assert isinstance(messages[2], ToolMessage)
        assert isinstance(messages[3], AssistantMessage) and not messages[3].tool_calls
        assert isinstance(messages[4], UserMessage)
        # A question with no answer after it: the run's own question comes next.
        assert messages[5].text == "(No answer was recorded for this turn.)"

    async def test_keeps_the_ask_user_question_answers_in_history(self) -> None:
        """A later turn ("what did I pick?") can only answer if the card's
        answers are still in context — the questions payload alone reads as
        an unanswered card."""
        agent = _FakeAgent()
        previous_conversations = [
            {"role": "user_query", "content": "ask me a question"},
            {"role": "bot_response", "content": ""},
            {"role": "user_query", "content": 'User selections:\n1. "Which step?" → Quiet break'},
            {"role": "bot_response", "content": "You picked the quiet break."},
        ]

        await PipesHubAgentFactory._seed_conversation_history(
            agent, previous_conversations, _make_context()
        )

        messages = await agent.context.messages()
        assert any(
            isinstance(msg, UserMessage) and "Quiet break" in (msg.content or "")
            for msg in messages
        )

    async def test_an_answered_approval_card_reads_as_one_call_with_one_result(self) -> None:
        """The reply that answered the card repeats the stopped call, settled, under its id: the
        result replaces the waiting one, the answer row and the waiting words go, and no call id
        appears twice."""
        agent = _FakeAgent()
        waiting = "Waiting for your approval to run create_issue on Jira."
        previous_conversations = [
            {"role": "user_query", "content": "file a bug"},
            {"role": "bot_response", "content": waiting, "tool_results": [
                {"tool_name": "search", "result": "no duplicates", "status": "success", "tool_id": "c0", "args": {}},
                {"tool_name": "mcp_jira_create_issue", "result": waiting, "status": "error", "tool_id": "c1", "args": {}},
            ]},
            {"role": "user_query", "content": "Tool approval: allow"},
            {"role": "bot_response", "content": "Filed it as JIRA-7.", "tool_results": [
                {"tool_name": "mcp_jira_create_issue", "result": "created JIRA-7", "status": "success", "tool_id": "c1", "args": {}},
                {"tool_name": "mcp_jira_add_label", "result": "labelled", "status": "success", "tool_id": "c3", "args": {}},
            ]},
            {"role": "user_query", "content": "thanks"},
        ]

        await PipesHubAgentFactory._seed_conversation_history(agent, previous_conversations, _make_context())

        messages = await agent.context.messages()
        shape = [
            (type(m).__name__, [c.id for c in m.tool_calls or []] if isinstance(m, AssistantMessage) else getattr(m, "tool_call_id", None))
            for m in messages
        ]
        assert shape == [
            ("UserMessage", None),
            ("AssistantMessage", ["c0", "c1"]), ("ToolMessage", "c0"), ("ToolMessage", "c1"),
            ("AssistantMessage", ["c3"]), ("ToolMessage", "c3"),
            ("AssistantMessage", []),
            ("UserMessage", None),
            ("AssistantMessage", []),
        ]
        assert (messages[3].content, messages[3].is_error) == ("created JIRA-7", False)
        assert messages[6].text == "Filed it as JIRA-7."
        assert not any("Tool approval:" in str(m.content) or waiting in str(m.content) for m in messages)

    async def test_an_id_repeated_outside_an_answer_is_a_different_call(self) -> None:
        """Some providers number call ids per response: the same id in two ordinary turns is two calls."""
        agent = _FakeAgent()
        previous_conversations = [
            {"role": "user_query", "content": "one"},
            {"role": "bot_response", "content": "a", "tool_results": [
                {"tool_name": "search", "result": "first", "status": "success", "tool_id": "call_0"},
            ]},
            {"role": "user_query", "content": "two"},
            {"role": "bot_response", "content": "b", "tool_results": [
                {"tool_name": "search", "result": "second", "status": "success", "tool_id": "call_0"},
            ]},
        ]

        await PipesHubAgentFactory._seed_conversation_history(agent, previous_conversations, _make_context())

        results = [m.content for m in await agent.context.messages() if isinstance(m, ToolMessage)]
        assert results == ["first", "second"]

    async def test_ids_the_history_makes_up_are_never_folded(self) -> None:
        agent = _FakeAgent()
        previous_conversations = [
            {"role": "user_query", "content": "one"},
            {"role": "bot_response", "content": "a", "tool_results": [{"tool_name": "t", "result": "1", "status": "success"}]},
            {"role": "user_query", "content": "two"},
            {"role": "bot_response", "content": "b", "tool_results": [{"tool_name": "t", "result": "2", "status": "success"}]},
        ]

        await PipesHubAgentFactory._seed_conversation_history(agent, previous_conversations, _make_context())

        results = [m.content for m in await agent.context.messages() if isinstance(m, ToolMessage)]
        assert results == ["1", "2"]

    async def test_empty_history_yields_empty_context(self) -> None:
        agent = _FakeAgent()

        await PipesHubAgentFactory._seed_conversation_history(agent, [], _make_context())

        assert await agent.context.messages() == []


async def _seeded(previous_conversations: list[dict]) -> list:
    agent = _FakeAgent()
    await PipesHubAgentFactory._seed_conversation_history(agent, previous_conversations, _make_context())
    return await agent.context.messages()


def _no_two_human_turns_in_a_row(messages: list) -> bool:
    humans = [isinstance(m, UserMessage) and not m.injected for m in messages]
    return not any(a and b for a, b in zip(humans, humans[1:]))


class TestUnansweredTurnsAreClosed:
    """HG-02: a turn that failed or was stopped replays with a short placeholder answer, so the
    model never reads its question as still open, and two human turns are never adjacent."""

    async def test_a_failed_turn_gets_a_failed_placeholder(self) -> None:
        messages = await _seeded([
            {"role": "user_query", "content": "list the billing tickets"},
            {"role": "bot_response", "content": "", "status": "failed"},
        ])

        assert [type(m).__name__ for m in messages] == ["UserMessage", "AssistantMessage"]
        assert messages[1].text == "(This turn failed before an answer.)"

    async def test_a_stopped_turn_with_no_answer_gets_a_stopped_placeholder(self) -> None:
        messages = await _seeded([
            {"role": "user_query", "content": "compare every Jira epic"},
            {"role": "bot_response", "content": "", "status": "stopped", "tool_results": [
                {"tool_name": "jira_search", "result": "", "status": "success", "tool_id": "s1", "args": {}},
            ]},
        ])

        assert isinstance(messages[-1], AssistantMessage)
        assert messages[-1].text == "(Stopped by the user.)"
        assert messages[-2].tool_call_id == "s1"

    async def test_a_stopped_partial_answer_says_it_was_stopped(self) -> None:
        messages = await _seeded([
            {"role": "user_query", "content": "summarise the deck"},
            {"role": "bot_response", "content": "The deck covers revenue", "status": "stopped"},
        ])

        assert messages[-1].text == "The deck covers revenue\n\n(Stopped by the user.)"

    async def test_an_unanswered_question_from_an_old_payload_is_closed(self) -> None:
        """Payloads from a Node that still drops error rows carry no status: two questions in a
        row mean the first one got no answer."""
        messages = await _seeded([
            {"role": "user_query", "content": "summarise the deck"},
            {"role": "bot_response", "content": "It covers revenue."},
            {"role": "user_query", "content": "list the billing tickets"},
            {"role": "user_query", "content": "who presented the deck?"},
        ])

        assert _no_two_human_turns_in_a_row(messages)
        assert messages[3].text == "(No answer was recorded for this turn.)"

    async def test_history_never_ends_on_an_open_question(self) -> None:
        """The run adds the current question next, so a trailing unanswered one is closed too."""
        messages = await _seeded([
            {"role": "user_query", "content": "summarise the deck"},
            {"role": "bot_response", "content": "It covers revenue."},
            {"role": "user_query", "content": "list the billing tickets"},
        ])

        assert isinstance(messages[-1], AssistantMessage)
        assert messages[-1].text == "(No answer was recorded for this turn.)"

    async def test_card_answers_are_not_mistaken_for_unanswered_questions(self) -> None:
        messages = await _seeded([
            {"role": "user_query", "content": "ask me a question"},
            {"role": "bot_response", "content": ""},
            {"role": "user_query", "content": 'User selections:\n1. "Which step?" → Quiet break'},
            {"role": "bot_response", "content": "You picked the quiet break."},
        ])

        assert not any("No answer was recorded" in m.text for m in messages if isinstance(m, AssistantMessage))

    async def test_placeholders_are_logged_with_counts_only(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.INFO, logger="app.agents.agent_loop.factory"):
            await _seeded([
                {"role": "user_query", "content": "secret question one"},
                {"role": "bot_response", "content": "", "status": "failed"},
                {"role": "user_query", "content": "secret question two"},
            ])

        records = [r for r in caplog.records if "history_seeded" in r.getMessage()]
        assert len(records) == 1
        line = records[0].getMessage()
        assert "failed=1" in line and "turns=3" in line
        assert "secret" not in line
