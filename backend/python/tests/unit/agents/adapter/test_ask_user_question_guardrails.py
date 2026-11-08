"""Ask-user guardrails: the tool's input limits, the per-request card limit, no
re-asking, a typed reply answering the open card, an answer for a card that
already got its reply, the card's expiry, and which clients can see the tool."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.actions.internal_tools.intrim_tools import InternalTools
from app.agents.agent_loop import parked_turn
from app.agents.agent_loop.clarification import emit_already_answered
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import (
    card_already_answered,
    cards_in_request,
    inject_ask_user_question_resume,
    is_open_card_reply,
    last_real_user_query,
    questions_asked_in_request,
)
from app.agents.agent_loop.hooks.ask_user_question import (
    MAX_CARDS_PER_REQUEST,
    ask_user_question_gate,
)
from tests.unit.agents.adapter.support.hook_helpers import (
    assert_allowed,
    assert_denied,
    run_pre_tool,
)

_ASK_PATH = "/tools/internaltools/ask_user_question"


def _options(*labels: str) -> list[dict[str, Any]]:
    return [{"label": label} for label in labels]


def _q(text: str, *labels: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"question": text, "options": _options(*(labels or ("A", "B"))), **extra}


async def _ask(*questions: dict[str, Any]) -> str | ToolOutput:
    return await InternalTools().ask_user_question(user_intent="You asked me to file a bug.", questions=list(questions))


# History rows as Node sends them (`formatPreviousConversations`).
def _card(question: str, *, status: str = "success") -> dict[str, Any]:
    payload = {"name": "ask_user_question", "questions": [_q(question)]}
    return {
        "role": "bot_response", "content": "",
        "tool_results": [{
            "tool_id": "ask_user_question", "tool_name": "internaltools__ask_user_question",
            "result": json.dumps(payload), "status": status,
        }],
    }


def _user(text: str) -> dict[str, Any]:
    return {"role": "user_query", "content": text}


def _bot(text: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"role": "bot_response", "content": text, **extra}


def _selections(question: str, answer: str) -> str:
    return f'User selections:\n1. "{question}" → {answer}'


def _context(previous: list[dict[str, Any]], *, resuming: bool) -> AgentContext:
    context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())
    context.previous_conversations = previous
    if resuming:
        context.tool_state["ask_user_question_resume"] = "User selections:\n..."
    return context


class TestToolInput:
    async def test_two_options_are_enough_for_a_card(self) -> None:
        payload = json.loads(await _ask(_q("Which ticket should I close?", "PROJ-42", "PROJ-57")))
        assert [o["label"] for o in payload["questions"][0]["options"]] == ["PROJ-42", "PROJ-57"]

    @pytest.mark.parametrize("labels", [("Only one",), ("A", "B", "C", "D", "E")])
    async def test_option_counts_outside_two_to_four_are_refused_with_how_to_fix(self, labels: tuple[str, ...]) -> None:
        result = await _ask(_q("Which one?", *labels))
        assert isinstance(result, ToolOutput) and result.success is False
        assert "2 to 4" in result.error

    async def test_more_than_three_questions_are_refused(self) -> None:
        result = await _ask(*(_q(f"Question {i}?") for i in range(4)))
        assert isinstance(result, ToolOutput) and result.success is False
        assert "1 to 3 questions" in result.error

    async def test_options_must_be_different_choices(self) -> None:
        result = await _ask(_q("Which?", "EU", "eu "))
        assert isinstance(result, ToolOutput) and "different label" in result.error

    async def test_catch_all_options_are_left_to_the_app(self) -> None:
        """Seen live: "Make it better." got a card with its own "Something else" beside the app's."""
        payload = json.loads(await _ask(_q(
            "What should I make better?", "A document", "An email", "Something else", "Other (please specify)",
        )))
        assert [o["label"] for o in payload["questions"][0]["options"]] == ["A document", "An email"]

    async def test_a_real_choice_that_starts_with_other_is_kept(self) -> None:
        payload = json.loads(await _ask(_q("Which region?", "Other region", "EU")))
        assert [o["label"] for o in payload["questions"][0]["options"]] == ["Other region", "EU"]

    async def test_a_card_left_with_one_real_option_is_refused(self) -> None:
        result = await _ask(_q("What should I make better?", "A document", "Something else"))
        assert isinstance(result, ToolOutput) and result.success is False
        assert "concrete options" in result.error

    async def test_no_option_reaches_the_card_marked_as_recommended(self) -> None:
        payload = json.loads(await _ask({"question": "Which region?", "options": [
            {"label": "EU (Recommended)", "recommended": True}, {"label": "US [recommended]"}, {"label": "APAC"},
        ]}))
        options = payload["questions"][0]["options"]
        assert [o["label"] for o in options] == ["EU", "US", "APAC"]
        assert all("recommended" not in o for o in options)

    async def test_a_label_that_only_differs_by_the_mark_is_still_a_repeat(self) -> None:
        result = await _ask(_q("Which?", "EU (Recommended)", "EU"))
        assert isinstance(result, ToolOutput) and "different label" in result.error

    @pytest.mark.parametrize(("field", "value"), [
        ("question", "x" * 301), ("label", "x" * 121), ("description", "x" * 201), ("header", "x" * 21),
    ])
    async def test_text_past_its_length_limit_is_refused(self, field: str, value: str) -> None:
        question: dict[str, Any] = {"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}
        if field in ("label", "description"):
            question["options"][0][field] = value
        else:
            question[field] = value
        result = await _ask(question)
        assert isinstance(result, ToolOutput) and result.success is False

    async def test_new_fields_reach_the_card_only_when_set(self) -> None:
        payload = json.loads(await _ask({
            "question": "Which project?", "header": "Project",
            "options": [
                {"label": "PLAT", "description": "Platform board"},
                {"label": "WEB"},
            ],
        }))
        question = payload["questions"][0]
        assert question["header"] == "Project"
        assert question["options"][0] == {
            "id": "opt_plat", "label": "PLAT", "isUserInput": False,
            "description": "Platform board",
        }
        assert question["options"][1] == {"id": "opt_web", "label": "WEB", "isUserInput": False}

    async def test_a_call_without_the_new_fields_gives_the_card_older_clients_know(self) -> None:
        payload = json.loads(await _ask(_q("Which channel?", "#general", "#random")))
        assert set(payload) == {"name", "userIntent", "questions"}
        assert set(payload["questions"][0]) == {"uuid", "question", "options", "multiSelect"}
        assert all(set(o) == {"id", "label", "isUserInput"} for o in payload["questions"][0]["options"])

    def test_the_schema_the_model_sees_carries_the_limits(self) -> None:
        meta = getattr(InternalTools.ask_user_question, TOOL_META_ATTR)
        items = next(p for p in meta.parameters if p.name == "questions").items
        options = items["properties"]["options"]
        assert (options["minItems"], options["maxItems"]) == (2, 4)
        assert "description" in options["items"]["properties"]
        assert "recommended" not in options["items"]["properties"]
        assert "recommended or best" in meta.description
        assert "header" in items["properties"]
        assert "MANDATORY" not in meta.description
        assert "state your assumption" in meta.description

    def test_the_model_is_told_not_to_ask_for_details_of_an_action_no_tool_can_do(self) -> None:
        # Seen: "send an email" with no mail tool asked for the recipient, then could not send.
        description = getattr(InternalTools.ask_user_question, TOOL_META_ATTR).description
        assert "only when you can do that action" in description
        assert "tell the user you cannot do it instead of asking" in description


class TestCardLimit:
    async def test_a_third_card_for_one_request_is_refused(self) -> None:
        previous = [
            _user("file a bug"), _card("Which project?"),
            _user(_selections("Which project?", "PLAT")), _card("Which priority?"),
        ]
        gate = ask_user_question_gate(_context(previous, resuming=True))
        ctx = await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("Which assignee?")]})
        assert_denied(ctx)
        assert f"{MAX_CARDS_PER_REQUEST} times" in (ctx.decision_reason or "")

    async def test_a_second_card_for_one_request_is_allowed(self) -> None:
        previous = [_user("file a bug"), _card("Which project?")]
        gate = ask_user_question_gate(_context(previous, resuming=True))
        assert_allowed(await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("Which priority?")]}))

    async def test_a_new_request_starts_its_own_count(self) -> None:
        previous = [
            _user("file a bug"), _card("Which project?"),
            _user(_selections("Which project?", "PLAT")), _card("Which priority?"),
            _user(_selections("Which priority?", "High")), _bot("Filed PLAT-9."),
        ]
        gate = ask_user_question_gate(_context(previous, resuming=False))
        assert_allowed(await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("Which board?")]}))

    def test_a_failed_call_is_not_a_card(self) -> None:
        assert cards_in_request([_user("file a bug"), _card("Which project?", status="error")]) == 0

    def test_a_typed_reply_does_not_start_a_new_request(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user("PLAT, please"), _card("Which priority?")]
        assert cards_in_request(previous) == 2


class TestAMessageWithFilesAnswersTheCard:
    """PR #355 review: a message sent with files while a card is open is that card's answer, for
    every reader of the history, the same as the server's resume."""

    _HISTORY = [
        _user("file a bug"), _card("Which project?"),
        {**_user("see the log"), "attachments": [{"recordId": "r1", "recordName": "log.txt"}]},
        _card("Which service?"),
    ]

    def test_the_goal_stays_the_request_the_cards_belong_to(self) -> None:
        answers = _selections("Which service?", "API")
        assert last_real_user_query(self._HISTORY, answers, answers=answers) == "file a bug"

    def test_both_cards_count_toward_that_request(self) -> None:
        assert cards_in_request([*self._HISTORY, _user(_selections("Which service?", "API"))]) == 2


class TestNoReAsking:
    async def test_a_question_this_request_already_asked_is_refused(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user(_selections("Which project?", "[No preference]"))]
        gate = ask_user_question_gate(_context(previous, resuming=True))
        ctx = await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("which project")]})
        assert_denied(ctx)
        assert '"which project"' in (ctx.decision_reason or "")

    async def test_a_new_question_goes_through(self) -> None:
        previous = [_user("file a bug"), _card("Which project?")]
        gate = ask_user_question_gate(_context(previous, resuming=True))
        assert_allowed(await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("Which priority?")]}))

    async def test_a_new_request_may_ask_what_an_earlier_request_asked(self) -> None:
        # A second bug needs its own project; the first bug's answer may not fit it.
        previous = [
            _user("file a bug about login"), _card("Which project?"),
            _user(_selections("Which project?", "PLAT")), _bot("Filed PLAT-9."),
        ]
        gate = ask_user_question_gate(_context(previous, resuming=False))
        assert_allowed(await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("Which project?")]}))

    async def test_answering_a_later_requests_card_does_not_block_an_earlier_requests_question(self) -> None:
        previous = [
            _user("file a bug about login"), _card("Which project?"),
            _user(_selections("Which project?", "PLAT")), _bot("Filed PLAT-9."),
            _user("file a bug about search"), _card("Which priority?"),
        ]
        gate = ask_user_question_gate(_context(previous, resuming=True))
        assert_allowed(await run_pre_tool(gate, tool_path=_ASK_PATH, tool_input={"questions": [_q("Which project?")]}))

    def test_only_this_requests_cards_the_user_saw_count_as_asked(self) -> None:
        assert questions_asked_in_request([_user("x"), _card("Which project?", status="error")]) == set()
        assert questions_asked_in_request([_user("x"), _card("Which project?")]) == {"which project"}
        earlier = [_user("x"), _card("Which project?"), _user(_selections("Which project?", "A")), _bot("Done.")]
        assert questions_asked_in_request([*earlier, _user("y"), _card("Which priority?")]) == {"which priority"}

    async def test_other_tools_are_not_limited(self) -> None:
        previous = [_user("x"), _card("A?"), _user(_selections("A?", "A")), _card("B?")]
        gate = ask_user_question_gate(_context(previous, resuming=True))
        assert_allowed(await run_pre_tool(gate, tool_path="/tools/jira/create_issue", tool_input={}))


class TestTypedReply:
    def test_text_typed_while_the_card_is_open_answers_it(self) -> None:
        assert is_open_card_reply([_user("file a bug"), _card("Which project?")], "PLAT, please")

    @pytest.mark.parametrize("query", ["", _selections("Which project?", "PLAT"), "Tool approval: allow"])
    def test_tapped_answers_and_approvals_are_not_typed_replies(self, query: str) -> None:
        assert not is_open_card_reply([_user("file a bug"), _card("Which project?")], query)

    def test_text_after_a_failed_call_or_a_closed_card_is_a_new_request(self) -> None:
        assert not is_open_card_reply([_user("file a bug"), _card("Which project?", status="error")], "hi")
        assert not is_open_card_reply([_user("file a bug"), _card("Which project?"), _user("PLAT"), _bot("Done.")], "hi")
        assert not is_open_card_reply([], "hi")

    def test_the_goal_stays_the_request_the_card_belongs_to(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user("PLAT"), _card("Which priority?")]
        assert last_real_user_query(previous, "High", answers=_selections("Which priority?", "High")) == "file a bug"
        assert last_real_user_query(previous, "High", answers="") == "file a bug"

    async def test_the_typed_text_is_bound_to_the_open_call(self) -> None:
        ctx = ContextManager()
        await ctx.add(UserMessage(content="file a bug"))
        await ctx.add(AssistantMessage(content=[], tool_calls=[
            ToolCall(id="call_ask", name="internaltools__ask_user_question", arguments={"questions": [_q("Which project?")]}),
        ]))
        await ctx.add(ToolMessage(content='{"questions": []}', tool_call_id="call_ask"))
        agent = MagicMock()
        agent.context = ctx

        await inject_ask_user_question_resume(agent, "PLAT, the platform one", typed=True)

        last = (await ctx.messages())[-1]
        assert isinstance(last, ToolMessage) and last.tool_call_id == "call_ask"
        result = json.loads(last.content)
        assert result["status"] == "replied"
        assert result["reply"] == "PLAT, the platform one"
        assert "If it asks for something else, do that instead" in result["next"]

    async def test_a_skipped_answer_tells_the_model_to_use_its_best_guess(self) -> None:
        ctx = ContextManager()
        await ctx.add(UserMessage(content="file a bug"))
        await ctx.add(AssistantMessage(content=[], tool_calls=[
            ToolCall(id="call_ask", name="internaltools__ask_user_question", arguments={"questions": [_q("Which project?")]}),
        ]))
        agent = MagicMock()
        agent.context = ctx

        await inject_ask_user_question_resume(agent, _selections("Which project?", "[No preference]"))

        result = json.loads((await ctx.messages())[-1].content)
        assert result["status"] == "answered"
        assert "use your best guess and say in one line which you chose" in result["next"]
        assert "recommended" not in result["next"]


class TestTypedReplyThroughTheFactory:
    """`PipesHubAgentFactory.create` with a history whose last reply ended on a card."""

    @pytest.fixture(autouse=True)
    def _skills_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")

    async def _create(self, query: str, previous: list[dict[str, Any]], **state: Any) -> tuple[Any, Any, Any]:  # noqa: ANN401
        from app.agents.agent_loop.factory import PipesHubAgentFactory
        from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

        context = make_context(llm=FakeChatModel(), previous_conversations=previous)
        context.tool_state.update(state)
        agent, _runtime, goal, _questions = await PipesHubAgentFactory().create(context, context.llm, "react", query=query)
        return context, agent, goal

    async def test_typed_text_resumes_the_card_with_the_original_request(self) -> None:
        context, agent, _goal = await self._create("PLAT, please", [_user("file a bug"), _card("Which project?")])

        assert context.tool_state["ask_user_question_resume"] == "PLAT, please"
        last = (await agent.context.messages())[-1]
        assert isinstance(last, ToolMessage)
        assert json.loads(last.content) == {
            "status": "replied", "reply": "PLAT, please",
            "next": json.loads(last.content)["next"],
        }

    async def test_a_message_with_files_answers_the_card_and_keeps_the_files(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every reader of the history counts the next message as the card's answer, files or
        not (PR #355 review), so the server resumes the card too, with the files in the request."""
        import app.agents.agent_loop.factory as factory

        async def _files(_context: Any, _logger: Any, **_budget: Any) -> tuple[str, list[Any]]:  # noqa: ANN401
            return "\n\nAttached files from the user:\nlog.txt: connection pool exhausted", []

        monkeypatch.setattr(factory, "resolve_attachments_for_goal", _files)
        context, agent, goal = await self._create(
            "see the attached log", [_user("file a bug"), _card("Which project?")],
            attachments=[{"recordId": "r1"}],
        )

        assert context.tool_state["ask_user_question_resume"] == "see the attached log"
        # Seen live: a resumed run adds no goal message, so the files must travel on the answer.
        result = json.loads((await agent.context.messages())[-1].content)
        assert result["status"] == "replied"
        assert "connection pool exhausted" in result["files"]
        assert 'They attached files with it, in "files".' in result["next"]

    async def test_a_clarification_on_the_resumed_request_does_not_drop_the_files(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.agents.agent_loop.factory as factory

        real_select = factory.select_loop_and_goal

        async def _clarifying(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            loop, goal, _questions, mode = await real_select(*args, **kwargs)
            return loop, goal, [{"question": "Which project?"}], mode

        async def _files(_context: Any, _logger: Any, **_budget: Any) -> tuple[str, list[Any]]:  # noqa: ANN401
            return "\n\nAttached files from the user:\nlog.txt", []

        monkeypatch.setattr(factory, "select_loop_and_goal", _clarifying)
        monkeypatch.setattr(factory, "resolve_attachments_for_goal", _files)
        from app.agents.agent_loop.factory import PipesHubAgentFactory
        from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

        context = make_context(llm=FakeChatModel(), previous_conversations=[_user("file a bug"), _card("Which project?")])
        context.tool_state["attachments"] = [{"recordId": "r1"}]
        _agent, _runtime, goal, questions = await PipesHubAgentFactory().create(
            context, context.llm, "react", query="see the attached log",
        )

        assert questions == []
        result = json.loads((await _agent.context.messages())[-1].content)
        assert "log.txt" in result["files"]

    async def test_text_after_a_closed_card_is_a_new_request(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user("PLAT"), _bot("Filed PLAT-9.")]
        context, _agent, _goal = await self._create("now close it", previous)
        assert "ask_user_question_resume" not in context.tool_state

    async def test_text_after_a_failed_ask_call_is_a_new_request(self) -> None:
        context, _agent, _goal = await self._create("hello", [_user("file a bug"), _card("Which project?", status="error")])
        assert "ask_user_question_resume" not in context.tool_state

    async def test_answers_with_no_card_in_the_history_are_a_new_request(self) -> None:
        """Sent by hand, or the card's turn is not in this requester's history (a shared chat's
        other turns reach the model without their tool rows): nothing to resume, so the message is
        its own request, not a continuation of the earlier one."""
        answers = _selections("Which project?", "PLAT")
        context, _agent, goal = await self._create(answers, [_user("file a bug"), _bot("Filed PLAT-9.")])

        assert "ask_user_question_resume" not in context.tool_state
        assert goal.user_message == answers

    async def test_tapped_answers_to_an_open_card_still_resume_it(self) -> None:
        answers = _selections("Which project?", "PLAT")
        context, agent, goal = await self._create(answers, [_user("file a bug"), _card("Which project?")])

        assert context.tool_state["ask_user_question_resume"] == answers
        assert goal.user_message == "file a bug"
        assert isinstance((await agent.context.messages())[-1], ToolMessage)


class TestOneResolutionPerCard:
    def test_an_answer_for_a_card_that_already_got_its_reply_is_a_repeat(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user(_selections("Which project?", "PLAT")), _bot("Filed PLAT-9.")]
        assert card_already_answered(previous, _selections("Which project?", "WEB"))

    def test_a_card_answered_by_typing_is_answered(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user("PLAT"), _bot("Filed PLAT-9.")]
        assert card_already_answered(previous, _selections("Which project?", "WEB"))

    def test_the_first_answer_runs(self) -> None:
        assert not card_already_answered([_user("file a bug"), _card("Which project?")], _selections("Which project?", "PLAT"))

    def test_an_answer_whose_run_failed_can_be_sent_again(self) -> None:
        # Errors are not in the history, so a failed answer leaves no reply after it.
        previous = [_user("file a bug"), _card("Which project?"), _user(_selections("Which project?", "PLAT"))]
        assert not card_already_answered(previous, _selections("Which project?", "PLAT"))

    def test_an_answer_whose_run_was_stopped_can_be_sent_again(self) -> None:
        previous = [
            _user("file a bug"), _card("Which project?"),
            _user(_selections("Which project?", "PLAT")), _bot("Working on PLAT", status="stopped"),
        ]
        assert not card_already_answered(previous, _selections("Which project?", "PLAT"))

    def test_another_cards_answers_do_not_answer_this_card(self) -> None:
        previous = [
            _user("file a bug"), _card("Which project?"),
            _user(_selections("Which priority?", "High")), _bot("Set to High."),
        ]
        assert not card_already_answered(previous, _selections("Which project?", "PLAT"))

    def test_a_later_request_and_its_card_do_not_answer_an_older_card(self) -> None:
        previous = [
            _user("file a bug"), _card("Which project?"),
            _user(_selections("Which priority?", "High")), _bot("Set to High."),
            _user("now draft the email"), _card("Who is it for?"),
        ]
        assert not card_already_answered(previous, _selections("Which project?", "PLAT"))

    def test_answering_the_follow_up_card_is_not_a_repeat(self) -> None:
        previous = [
            _user("file a bug"), _card("Which project?"),
            _user(_selections("Which project?", "PLAT")), _card("Which priority?"),
        ]
        assert not card_already_answered(previous, _selections("Which priority?", "High"))
        assert card_already_answered(previous, _selections("Which project?", "WEB"))

    def test_answers_sent_with_no_card_before_them_were_a_request_of_their_own(self) -> None:
        answers = _selections("Which project?", "PLAT")
        previous = [_user("file a bug"), _bot("Filed PLAT-9."), _user(answers), _bot("Noted.")]
        assert last_real_user_query(previous, "fallback") == answers

    def test_a_typed_message_is_never_a_repeat(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user("PLAT"), _bot("Filed PLAT-9.")]
        assert not card_already_answered(previous, "PLAT")

    async def test_the_repeat_ends_the_turn_with_a_notice_and_no_card(self) -> None:
        events: list[dict[str, Any]] = []

        class _Sink:
            async def write(self, event: dict[str, Any]) -> bool:
                events.append(event)
                return True

        context = _context([], resuming=False)
        completion = await emit_already_answered(context, event_sink=_Sink())

        assert completion["answerMatchType"] == "Already Answered"
        assert "already answered" in completion["answer"]
        assert not any(e.get("event") == "ask_user_question" for e in events)
        assert context.tool_state["response"] == completion["answer"]


class TestRepeatThroughTheStream:
    async def _stream(self, query: str, previous: list[dict[str, Any]]) -> tuple[str, Any]:
        from unittest.mock import AsyncMock, patch

        from app.agents.agent_loop.stream_bridge import run_agent_loop_stream

        create = AsyncMock(side_effect=AssertionError("the agent must not be built for a repeat"))
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": query, "previous_conversations": previous},
            ),
            patch("app.utils.connector_instances.fetch_user_connector_instances", new=AsyncMock(return_value=[])),
            patch("app.agents.agent_loop.stream_bridge.PipesHubAgentFactory.create", new=create),
        ):
            stream = run_agent_loop_stream(
                query_info={"query": query, "chatMode": "quick"},
                user_info={"userId": "user-1", "orgId": "org-1"},
                llm=MagicMock(), log=MagicMock(), retrieval_service=MagicMock(),
                graph_provider=MagicMock(), reranker_service=MagicMock(),
                config_service=MagicMock(), model_name="gpt-4",
            )
            frames = "".join([chunk async for chunk in stream])
        return frames, create

    async def test_a_repeat_answer_streams_the_notice_without_running_the_agent(self) -> None:
        previous = [_user("file a bug"), _card("Which project?"), _user(_selections("Which project?", "PLAT")), _bot("Filed PLAT-9.")]
        frames, create = await self._stream(_selections("Which project?", "WEB"), previous)

        create.assert_not_awaited()
        assert "already answered" in frames

    async def test_a_first_answer_runs_the_agent(self) -> None:
        previous = [_user("file a bug"), _card("Which project?")]
        _frames, create = await self._stream(_selections("Which project?", "PLAT"), previous)

        create.assert_awaited_once()


class TestExpiry:
    def test_a_card_is_stamped_with_when_its_kept_turn_runs_out(self) -> None:
        before = datetime.now(timezone.utc)
        stamped = parked_turn.with_expiry({"name": "ask_user_question", "questions": []})
        expires = datetime.fromisoformat(stamped["expiresAt"].replace("Z", "+00:00"))
        assert (expires - before).total_seconds() == pytest.approx(parked_turn.QUESTION_TTL_SECONDS, abs=5)

    def test_an_existing_expiry_and_non_card_data_are_left_alone(self) -> None:
        assert parked_turn.with_expiry({"expiresAt": "x", "questions": []}) == {"expiresAt": "x", "questions": []}
        assert parked_turn.with_expiry("raw text") == "raw text"


class TestTypedReplyInTheKeptTurn:
    def test_a_typed_reply_after_a_shown_card_is_not_the_persons_question(self) -> None:
        messages = [
            UserMessage(content="file a bug"),
            AssistantMessage(content=[], tool_calls=[ToolCall(id="ask", name="internaltools__ask_user_question", arguments={})]),
            ToolMessage(content="{}", tool_call_id="ask"),
            UserMessage(content="PLAT, please"),
        ]
        assert parked_turn.question_index(messages) == 0

    def test_a_message_after_a_failed_ask_call_is_the_persons_question(self) -> None:
        messages = [
            UserMessage(content="file a bug"),
            AssistantMessage(content=[], tool_calls=[ToolCall(id="ask", name="internaltools__ask_user_question", arguments={})]),
            ToolMessage(content="bad input", tool_call_id="ask", is_error=True),
            AssistantMessage(content="Filed it."),
            UserMessage(content="thanks, now close it"),
        ]
        assert parked_turn.question_index(messages) == 4


class TestWhoCanSeeTheTool:
    @pytest.mark.parametrize(("client_name", "can_answer"), [
        ("pipeshub-ai", True), ("slack", True), ("api", False), (None, False), ("desktop", False),
    ])
    def test_only_clients_that_show_cards_get_the_tool(self, client_name: str | None, can_answer: bool) -> None:
        from app.modules.agents.qna.chat_state import build_initial_state

        state = build_initial_state(
            {"query": "hi", "previousConversations": []}, {"userId": "u", "orgId": "o"},
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), "m", "k",
            has_sql_connector=False, client_name=client_name,
        )
        assert state["has_ui_client"] is can_answer
