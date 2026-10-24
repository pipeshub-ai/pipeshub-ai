"""ask_user_question resume: original Goal + tool_result, not a new user turn."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
    dedupe_tool_call_ids,
)
from app.agent_loop_lib.tools.decorators import _default_terminal_outcome
from app.agents.agent_loop.factory import (
    _convert_conversation_turn,
    inject_ask_user_question_resume,
    is_ask_user_question_resume_query,
    last_real_user_query,
)


def test_detects_synthetic_resume_query() -> None:
    assert is_ask_user_question_resume_query('User selections:\n1. "Which?" → A')
    assert not is_ask_user_question_resume_query("See below attached file(s).")
    assert not is_ask_user_question_resume_query(None)


def test_last_real_user_query_skips_resume_rows() -> None:
    previous = [
        {"role": "user_query", "content": "See below attached file(s)."},
        {"role": "bot_response", "content": "Which editor?"},
        {"role": "user_query", "content": 'User selections:\n1. "Which?" → Vim'},
    ]
    assert last_real_user_query(previous, "fallback") == "See below attached file(s)."


def test_last_real_user_query_skips_tool_approval_answers_too() -> None:
    previous = [
        {"role": "user_query", "content": "file a bug"},
        {"role": "bot_response", "content": "Waiting for your approval to run create_issue on Jira."},
        {"role": "user_query", "content": "Tool approval: allow"},
        {"role": "bot_response", "content": "Waiting for your approval to run add_label on Jira."},
    ]
    assert last_real_user_query(previous, "Tool approval: deny") == "file a bug"


def test_terminal_ask_user_question_sets_needs_input() -> None:
    tr = SimpleNamespace(content="{}")
    call = SimpleNamespace(name="internaltools__ask_user_question")
    outcome = _default_terminal_outcome(tr, call, "")
    assert outcome.task_done is True
    assert outcome.needs_input == "Waiting for user answers"


def test_terminal_other_tool_has_no_needs_input() -> None:
    tr = SimpleNamespace(content="done")
    call = SimpleNamespace(name="final_answer")
    outcome = _default_terminal_outcome(tr, call, "done")
    assert outcome.needs_input is None


@pytest.mark.asyncio
async def test_injects_answers_on_existing_ask_tool_call() -> None:
    ctx = ContextManager()
    await ctx.add(AssistantMessage(
        content=[],
        tool_calls=[ToolCall(
            id="tc-ask-1",
            name="internaltools__ask_user_question",
            arguments={"user_intent": "pick"},
        )],
    ))
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Which?" → Vim')
    messages = await ctx.messages()
    assert len(messages) == 2
    assert isinstance(messages[0], AssistantMessage)
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].tool_call_id == "tc-ask-1"
    payload = json.loads(messages[1].content)
    assert payload["status"] == "answered"
    assert "Vim" in payload["answers"]
    assert "do the next step now" in payload["next"]


@pytest.mark.asyncio
async def test_a_call_made_alongside_the_question_keeps_its_result() -> None:
    ctx = ContextManager()
    await ctx.add(UserMessage(content="look up the space, then ask me what to write"))
    await ctx.add(AssistantMessage(content=[], tool_calls=[
        ToolCall(id="tc-lookup", name="knowledgegraph__lookup_record", arguments={}),
        ToolCall(id="tc-ask-1", name="internaltools__ask_user_question", arguments={}),
    ]))
    await ctx.add(ToolMessage(content="Space TS, id 4242", tool_call_id="tc-lookup"))
    await ctx.add(ToolMessage(content='{"questions":[]}', tool_call_id="tc-ask-1"))
    await ctx.add(AssistantMessage(content="I'll ask first."))

    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "About?" → RAG')

    messages = await ctx.messages()
    assert [(type(m).__name__, getattr(m, "tool_call_id", None)) for m in messages[2:]] == [
        ("ToolMessage", "tc-lookup"), ("ToolMessage", "tc-ask-1"),
    ]
    assert messages[2].content == "Space TS, id 4242"
    assert "RAG" in json.loads(messages[3].content)["answers"]


@pytest.mark.asyncio
async def test_inject_replaces_parked_tool_result_and_drops_empty_fallback() -> None:
    ctx = ContextManager()
    await ctx.add(UserMessage(content="hi i ask questions"))
    await ctx.add(AssistantMessage(
        content=[],
        tool_calls=[ToolCall(
            id="tc-ask-1",
            name="internaltools__ask_user_question",
            arguments={},
        )],
    ))
    await ctx.add(ToolMessage(content='{"questions":[]}', tool_call_id="tc-ask-1"))
    await ctx.add(AssistantMessage(
        content="I wasn't able to generate a response. Please try rephrasing.",
    ))
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(
        agent, 'User selections:\n1. "Which?" → Work or career',
    )
    messages = await ctx.messages()
    assert len(messages) == 3
    assert isinstance(messages[0], UserMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].tool_calls and messages[1].tool_calls[0].id == "tc-ask-1"
    assert isinstance(messages[2], ToolMessage)
    payload = json.loads(messages[2].content)
    assert payload["status"] == "answered"
    assert "Work or career" in payload["answers"]
    assert not any(
        isinstance(m, AssistantMessage) and "wasn't able" in m.text
        for m in messages
    )


@pytest.mark.asyncio
async def test_inject_continues_an_older_card_not_the_later_chat() -> None:
    """Later chat stays in the UI; this run continues the unanswered card."""
    ctx = ContextManager()
    await ctx.add(UserMessage(content="which editor should i use"))
    await ctx.add(AssistantMessage(
        content=[],
        tool_calls=[ToolCall(
            id="tc-ask-1",
            name="internaltools__ask_user_question",
            arguments={},
        )],
    ))
    await ctx.add(ToolMessage(content='{"questions":[]}', tool_call_id="tc-ask-1"))
    await ctx.add(AssistantMessage(
        content="I wasn't able to generate a response. Please try rephrasing.",
    ))
    await ctx.add(UserMessage(content="actually, who owns billing?"))
    await ctx.add(AssistantMessage(content="Priya owns billing."))
    agent = SimpleNamespace(context=ctx)

    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Which?" → Vim')

    messages = await ctx.messages()
    assert not any(
        isinstance(m, AssistantMessage) and m.text == "Priya owns billing."
        for m in messages
    )
    assert isinstance(messages[-1], ToolMessage)
    assert messages[-1].tool_call_id == "tc-ask-1"
    payload = json.loads(messages[-1].content)
    assert payload["status"] == "answered"
    assert "Vim" in payload["answers"]


@pytest.mark.asyncio
async def test_inject_drops_every_later_turn_not_just_the_last() -> None:
    ctx = ContextManager()
    await ctx.add(UserMessage(content="ask me one question"))
    await ctx.add(AssistantMessage(
        content=[],
        tool_calls=[ToolCall(
            id="tc-ask-1",
            name="internaltools__ask_user_question",
            arguments={},
        )],
    ))
    await ctx.add(ToolMessage(content='{"questions":[]}', tool_call_id="tc-ask-1"))
    await ctx.add(UserMessage(content="help me find information"))
    await ctx.add(AssistantMessage(content="I can help you find information."))
    await ctx.add(UserMessage(content="what are the things i asked u to do"))
    await ctx.add(AssistantMessage(content="You asked me to: send an email."))
    agent = SimpleNamespace(context=ctx)

    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Which?" → Work')

    messages = await ctx.messages()
    texts = [m.text for m in messages if isinstance(m, AssistantMessage)]
    assert "I can help you find information." not in texts
    assert "You asked me to: send an email." not in texts
    assert messages[-1].tool_call_id == "tc-ask-1"


@pytest.mark.asyncio
async def test_inject_keeps_research_on_an_older_card_when_later_chat_exists() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "which editor should i use"},
        {
            "role": "bot_response",
            "content": "",
            "tool_results": [_tool_result(0), _ask_result("tc-ask-1")],
        },
        {"role": "user_query", "content": "actually, who owns billing?"},
        {"role": "bot_response", "content": "Priya owns billing."},
    )
    await inject_ask_user_question_resume(
        SimpleNamespace(context=ctx), 'User selections:\n1. "Which?" → Vim',
    )

    messages = await ctx.messages()
    assert any(isinstance(m, ToolMessage) and m.content == "finding 0" for m in messages)
    assert not any(
        isinstance(m, AssistantMessage) and m.text == "Priya owns billing."
        for m in messages
    )
    assert messages[-1].tool_call_id == "tc-ask-1"


def _tool_result(i: int, prefix: str = "call") -> dict:
    return {
        "tool_id": f"{prefix}_{i}",
        "tool_name": "search_internal_knowledge",
        "result": f"finding {i}",
        "status": "success",
    }


def _ask_result(call_id: str = "call_ask") -> dict:
    return {
        "tool_id": call_id,
        "tool_name": "internaltools__ask_user_question",
        "result": json.dumps({"questions": [{"question": "Which fiscal year?"}]}),
        "status": "success",
    }


async def _replay(*turns: dict) -> ContextManager:
    ctx = ContextManager()
    for turn in turns:
        for message in _convert_conversation_turn(turn):
            await ctx.add(message)
    return ctx


def _calls_without_a_result(messages: list) -> list[str]:
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    return [
        call.id
        for m in messages
        if isinstance(m, AssistantMessage)
        for call in (m.tool_calls or [])
        if call.id not in answered
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_before", [1, 28])
async def test_inject_keeps_the_results_of_tools_run_before_the_question(tools_before: int) -> None:
    """One turn replays as a single assistant message carrying every call, so
    cutting after it must not take the other calls' results with it."""
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {
            "role": "bot_response",
            "content": "",
            "tool_results": [_tool_result(i) for i in range(tools_before)] + [_ask_result()],
        },
    )
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "FY?" → FY2024')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    researched = [
        m.content for m in messages
        if isinstance(m, ToolMessage) and m.tool_call_id.startswith("call_") and m.tool_call_id != "call_ask"
    ]
    assert researched == [f"finding {i}" for i in range(tools_before)]
    ask_results = [m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == "call_ask"]
    assert len(ask_results) == 1
    assert json.loads(ask_results[0].content)["status"] == "answered"


@pytest.mark.asyncio
async def test_inject_keeps_the_results_of_an_earlier_resume_in_the_same_turn() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0, "a"), _ask_result("ask_one")]},
        {"role": "user_query", "content": 'User selections:\n1. "FY?" → FY2024'},
        {
            "role": "bot_response",
            "content": "",
            "tool_results": [_tool_result(i, "b") for i in range(5)] + [_ask_result("ask_two")],
        },
    )
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "Scope?" → Revenue')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    assert messages[-1].tool_call_id == "ask_two"


@pytest.mark.asyncio
async def test_inject_leaves_an_older_turns_results_alone() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "first"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0, "old"), _ask_result("old_ask")]},
        {"role": "user_query", "content": 'User selections:\n1. "FY?" → FY2024'},
        {"role": "bot_response", "content": "first answer"},
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0, "new"), _ask_result("new_ask")]},
    )
    older = [m.model_dump_json() for m in (await ctx.messages())[:5]]

    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "FY?" → FY2025')

    messages = await ctx.messages()
    assert [m.model_dump_json() for m in messages[:5]] == older
    assert _calls_without_a_result(messages[5:]) == []


def test_replayed_tool_call_ids_stay_unique_across_turns() -> None:
    """Node falls back to one fixed id for every saved question, and a provider
    rejects two tool calls that share an id."""
    seen: set[str] = set()
    replayed: list = []
    for turn in (
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("ask_user_question")]},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("ask_user_question")]},
    ):
        messages = _convert_conversation_turn(turn)
        dedupe_tool_call_ids(messages, seen)
        replayed.extend(messages)

    call_ids = [c.id for m in replayed if isinstance(m, AssistantMessage) for c in (m.tool_calls or [])]
    assert len(call_ids) == len(set(call_ids)) == 2
    assert _calls_without_a_result(replayed) == []


@pytest.mark.asyncio
async def test_inject_strips_fallback_when_history_has_no_tool_call() -> None:
    ctx = ContextManager()
    await ctx.add(UserMessage(content="hi i ask questions"))
    await ctx.add(AssistantMessage(
        content="I wasn't able to generate a response. Please try rephrasing.",
    ))
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "Which?" → Work')
    messages = await ctx.messages()
    assert isinstance(messages[0], UserMessage)
    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].tool_calls
    assert "ask_user_question" in messages[1].tool_calls[0].name
    assert isinstance(messages[2], ToolMessage)
    assert not any("wasn't able" in m.text for m in messages if isinstance(m, AssistantMessage))

def _answers_in(messages: list, call_id: str) -> str:
    result = next(m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == call_id)
    return json.loads(result.content)["answers"]


@pytest.mark.parametrize(
    "text",
    [
        'User selections:\n1. "Which fiscal year?" → FY2024',
        'User selections:\n1. "Which fiscal year?" → FY2024\n2. "Revenue or headcount?" → Revenue',
        'User selections:\n1. "What should I analyze?" → Summarize the dataset, Calculate totals',
        'User selections:\n1. "Which fiscal year?" → [No preference]',
        'User selections:\n1. "Which fiscal year?" → [No preference]\n2. "Revenue or headcount?" → Headcount',
        'User selections:\n1. "Where?" → Pune, \u0939\u093f\u0928\u094d\u0926\u0940 \u2013 \u00fcber caf\u00e9',
    ],
    ids=["single", "two-questions", "multi-select", "skip", "skip-one-of-two", "free-text-unicode"],
)
@pytest.mark.asyncio
async def test_inject_hands_the_model_the_answers_exactly_as_the_card_sent_them(text: str) -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0), _ask_result()]},
    )
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), text)

    messages = await ctx.messages()
    assert _answers_in(messages, "call_ask") == text
    assert _calls_without_a_result(messages) == []


@pytest.mark.asyncio
async def test_inject_answers_a_skipped_card_without_inventing_a_choice() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result()]},
    )
    await inject_ask_user_question_resume(
        SimpleNamespace(context=ctx), 'User selections:\n1. "Which fiscal year?" → [No preference]',
    )

    answers = _answers_in(await ctx.messages(), "call_ask")
    assert "[No preference]" in answers
    assert "FY" not in answers.split("\u2192")[-1]


@pytest.mark.asyncio
async def test_inject_drops_the_empty_fallback_but_keeps_the_research() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0), _tool_result(1), _ask_result()]},
    )
    await ctx.add(AssistantMessage(content="I wasn't able to generate a response. Please try rephrasing."))
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "FY?" → FY2024')

    messages = await ctx.messages()
    assert not any(isinstance(m, AssistantMessage) and "wasn't able" in m.text for m in messages)
    assert [m.content for m in messages if isinstance(m, ToolMessage) and m.tool_call_id.startswith("call_") and m.tool_call_id != "call_ask"] == ["finding 0", "finding 1"]
    assert _calls_without_a_result(messages) == []


@pytest.mark.asyncio
async def test_inject_is_safe_to_run_twice_on_the_same_history() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0), _ask_result()]},
    )
    agent = SimpleNamespace(context=ctx)
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "FY?" → FY2024')
    await inject_ask_user_question_resume(agent, 'User selections:\n1. "FY?" → FY2025')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    assert len([m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == "call_ask"]) == 1
    assert "FY2025" in _answers_in(messages, "call_ask")


@pytest.mark.asyncio
async def test_inject_gives_every_question_asked_in_one_turn_a_result() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {
            "role": "bot_response",
            "content": "",
            "tool_results": [_tool_result(0), _ask_result("ask_one"), _ask_result("ask_two")],
        },
    )
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "FY?" → FY2024')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    assert len([m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == "ask_two"]) == 1


@pytest.mark.asyncio
async def test_inject_after_a_second_card_in_the_same_conversation_binds_the_newest() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "first"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("ask_one")]},
        {"role": "user_query", "content": 'User selections:\n1. "FY?" → FY2024'},
        {"role": "bot_response", "content": "first answer"},
        {"role": "user_query", "content": "second"},
        {"role": "bot_response", "content": "", "tool_results": [_tool_result(0, "s"), _ask_result("ask_two")]},
    )
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "Scope?" → Revenue')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    assert messages[-1].tool_call_id == "ask_two"
    assert "Scope?" in _answers_in(messages, "ask_two")
    assert any(isinstance(m, AssistantMessage) and m.text == "first answer" for m in messages)


@pytest.mark.asyncio
async def test_inject_without_history_still_leaves_a_valid_conversation() -> None:
    ctx = ContextManager()
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "FY?" → FY2024')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    assert isinstance(messages[-1], ToolMessage)


@pytest.mark.asyncio
async def test_inject_does_nothing_when_the_agent_has_no_context() -> None:
    await inject_ask_user_question_resume(SimpleNamespace(context=None), 'User selections:\n1. "FY?" → FY2024')


@pytest.mark.asyncio
async def test_inject_ignores_a_regular_tool_whose_name_only_looks_similar() -> None:
    ctx = await _replay(
        {"role": "user_query", "content": "find it"},
        {
            "role": "bot_response",
            "content": "",
            "tool_results": [{**_tool_result(0), "tool_name": "search_questions"}],
        },
    )
    await inject_ask_user_question_resume(SimpleNamespace(context=ctx), 'User selections:\n1. "FY?" → FY2024')

    messages = await ctx.messages()
    assert _calls_without_a_result(messages) == []
    assert any(isinstance(m, ToolMessage) and m.content == "finding 0" for m in messages)


def test_a_turn_without_saved_tool_ids_still_replays_with_a_result_per_call() -> None:
    messages = _convert_conversation_turn({
        "role": "bot_response",
        "content": "done",
        "tool_results": [{"tool_name": "search", "result": "a"}, {"tool_name": "search", "result": "b"}],
    })

    assert _calls_without_a_result(messages) == []
    assert [m.content for m in messages if isinstance(m, ToolMessage)] == ["a", "b"]


def test_replayed_ids_stay_unique_when_turns_fall_back_to_positional_ids() -> None:
    seen: set[str] = set()
    replayed: list = []
    for _ in range(3):
        messages = _convert_conversation_turn({
            "role": "bot_response",
            "content": "",
            "tool_results": [{"tool_name": "search", "result": "a"}, {"tool_name": "search", "result": "b"}],
        })
        dedupe_tool_call_ids(messages, seen)
        replayed.extend(messages)

    call_ids = [c.id for m in replayed if isinstance(m, AssistantMessage) for c in (m.tool_calls or [])]
    assert len(call_ids) == len(set(call_ids)) == 6
    assert _calls_without_a_result(replayed) == []


def test_replayed_ids_stay_unique_when_one_turn_repeats_an_id() -> None:
    """Two saved questions in one turn share Node's fixed fallback id; each
    result must stay with its own call."""
    messages = _convert_conversation_turn({
        "role": "bot_response",
        "content": "",
        "tool_results": [_ask_result("ask_user_question"), _ask_result("ask_user_question")],
    })
    messages[1].content = "first"
    messages[2].content = "second"
    dedupe_tool_call_ids(messages, set())

    call_ids = [c.id for c in messages[0].tool_calls]
    assert len(set(call_ids)) == 2
    assert [(m.tool_call_id, m.content) for m in messages[1:3]] == list(zip(call_ids, ["first", "second"]))


def test_replaying_an_untouched_history_leaves_ids_alone() -> None:
    messages = _convert_conversation_turn({
        "role": "bot_response",
        "content": "done",
        "tool_results": [_tool_result(0), _ask_result("ask_a")],
    })
    before = [m.model_dump_json() for m in messages]
    dedupe_tool_call_ids(messages, set())

    assert [m.model_dump_json() for m in messages] == before


def test_resume_prefix_is_found_after_leading_whitespace_only() -> None:
    assert is_ask_user_question_resume_query('  \nUser selections:\n1. "Which?" → A')
    assert not is_ask_user_question_resume_query('Please list the User selections: for last week')
    assert not is_ask_user_question_resume_query("")


def test_goal_survives_several_cards_in_a_row() -> None:
    previous = [
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": ""},
        {"role": "user_query", "content": 'User selections:\n1. "FY?" → FY2024'},
        {"role": "bot_response", "content": ""},
        {"role": "user_query", "content": 'User selections:\n1. "Scope?" → Revenue'},
        {"role": "bot_response", "content": ""},
    ]
    assert last_real_user_query(previous, 'User selections:\n1. "Next?" → Yes') == "Compare Asana Q3 vs Q4"


def test_goal_stays_on_the_unanswered_card_when_the_user_chatted_on() -> None:
    previous = [
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result()]},
        {"role": "user_query", "content": "actually, who owns billing?"},
        {"role": "bot_response", "content": "Priya owns billing."},
    ]
    assert last_real_user_query(previous, "fallback", answers=_FY_ANSWER) == "Compare Asana Q3 vs Q4"


def test_goal_after_two_later_chats_is_still_the_card() -> None:
    previous = [
        {"role": "user_query", "content": "ask me one question"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result()]},
        {"role": "user_query", "content": "help me find information"},
        {"role": "bot_response", "content": "I can help you find information."},
        {"role": "user_query", "content": "what are the things i asked u to do"},
        {"role": "bot_response", "content": "You asked me to: send an email."},
    ]
    assert last_real_user_query(previous, "fallback", answers=_FY_ANSWER) == "ask me one question"


def test_goal_is_the_newest_unanswered_card_not_an_older_answered_one() -> None:
    previous = [
        {"role": "user_query", "content": "plan the launch"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("ask_one")]},
        {"role": "user_query", "content": 'User selections:\n1. "FY?" → FY2024'},
        {"role": "bot_response", "content": "Planning EU."},
        {"role": "user_query", "content": "now the report"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("ask_two")]},
        {"role": "user_query", "content": "what are the things i asked u to do"},
        {"role": "bot_response", "content": "You asked me to: plan the launch."},
    ]
    assert last_real_user_query(previous, "fallback", answers=_FY_ANSWER) == "now the report"


_FY_ANSWER = 'User selections:\n1. "Which fiscal year?" → FY2024'


def _asked(call_id: str, question: str) -> dict:
    return {**_ask_result(call_id), "result": json.dumps({"questions": [{"question": question}]})}


def _older_card_left_open_newer_answered() -> list[dict]:
    return [
        {"role": "user_query", "content": "plan the launch"},
        {"role": "bot_response", "content": "", "tool_results": [_asked("ask_one", "Which region first?")]},
        {"role": "user_query", "content": "now the report"},
        {"role": "bot_response", "content": "", "tool_results": [_asked("ask_two", "Which quarter?")]},
        {"role": "user_query", "content": 'User selections:\n1. "Which quarter?" → Q3'},
        {"role": "bot_response", "content": "Here is the Q3 report."},
    ]


def test_goal_is_the_card_the_answers_name_not_the_newest_one() -> None:
    previous = _older_card_left_open_newer_answered()
    answers = 'User selections:\n1. "which region  first" → EU'
    assert last_real_user_query(previous, "fallback", answers=answers) == "plan the launch"


def test_a_tool_approval_answer_ignores_question_cards() -> None:
    previous = [
        *_older_card_left_open_newer_answered(),
        {"role": "user_query", "content": "file a bug"},
        {"role": "bot_response", "content": "Waiting for your approval to run create_issue on Jira."},
    ]
    assert last_real_user_query(previous, "Tool approval: allow") == "file a bug"


@pytest.mark.asyncio
async def test_inject_binds_the_answers_to_the_card_they_name() -> None:
    ctx = await _replay(*_older_card_left_open_newer_answered())
    await inject_ask_user_question_resume(
        SimpleNamespace(context=ctx), 'User selections:\n1. "Which region first?" → EU',
    )

    messages = await ctx.messages()
    assert messages[-1].tool_call_id == "ask_one"
    assert "EU" in json.loads(messages[-1].content)["answers"]
    assert not any(isinstance(m, UserMessage) and m.content == "now the report" for m in messages)
    assert _calls_without_a_result(messages) == []


def test_goal_survives_several_cards_when_each_bot_has_the_ask_tool() -> None:
    previous = [
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("a1")]},
        {"role": "user_query", "content": 'User selections:\n1. "FY?" → FY2024'},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("a2")]},
        {"role": "user_query", "content": 'User selections:\n1. "Scope?" → Revenue'},
        {"role": "bot_response", "content": "", "tool_results": [_ask_result("a3")]},
    ]
    assert last_real_user_query(previous, 'User selections:\n1. "Next?" → Yes') == "Compare Asana Q3 vs Q4"


def test_goal_is_the_latest_real_message_when_history_has_no_card() -> None:
    previous = [
        {"role": "user_query", "content": "Compare Asana Q3 vs Q4"},
        {"role": "bot_response", "content": ""},
        {"role": "user_query", "content": "actually, who owns billing?"},
        {"role": "bot_response", "content": "Priya owns billing."},
    ]
    assert last_real_user_query(previous, "fallback") == "actually, who owns billing?"


@pytest.mark.parametrize("previous", [None, [], [{"role": "bot_response", "content": "hi"}],
                                      [{"role": "user_query", "content": "   "}],
                                      [{"role": "user_query", "content": 'User selections:\n1. "A?" → B'}]])
def test_goal_falls_back_when_history_has_no_real_question(previous) -> None:
    assert last_real_user_query(previous, "fallback") == "fallback"
