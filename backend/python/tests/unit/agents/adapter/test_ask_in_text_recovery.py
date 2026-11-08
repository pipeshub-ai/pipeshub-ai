"""`ask_in_text_recovery` (`app/agents/agent_loop/hooks/ask_user_question.py`): a reply that asks
the user in its text while the run could show a question card is re-prompted once, so the
question reaches the user as a card (or the reply drops it)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.hooks.ask_user_question import (
    _ASK_IN_TEXT_NUDGE,
    ask_in_text_recovery,
    asked_to_be_asked,
    asks_user_in_text,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.hook_helpers import run_post_model
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_ASK_TOOL = "internaltools__ask_user_question"
_LONG_ANSWER = (
    "Project Atlas ships on 15 December 2026, Beacon on 28 February 2027 and Cobalt on 30 November "
    "2026. Atlas is on track, Beacon is at risk with a forecast overrun of about $60,000, and Cobalt "
    "slipped two weeks because of an API change in the support tool. "
) * 2


class _NamedTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def path(self) -> str:
        return f"/tools/test/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="text", type=ParameterType.STRING, description="text", required=False)]

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data="ok")


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register_tool(_NamedTool(name))
    return registry


def _scope(*, tools: tuple[str, ...] = (_ASK_TOOL,), granted: list[str] | None = None, depth: int = 0) -> TurnScope:
    spec = AgentSpec(
        name="assistant", system_prompt="x", tool_names=granted or [],
        model=ModelSpec(provider="scripted", model="m"),
    )
    run = RunScope(
        identity=RunContext(role_name="assistant", model="m", spawn_depth=depth),
        spec=spec, runtime=AgentRuntime(tool_registry=_registry(*tools)), goal=Goal(description="g"),
    )
    return TurnScope(run=run, turn_index=0)


def _recovery(**context_overrides: Any) -> Any:  # noqa: ANN401
    return ask_in_text_recovery(make_context(has_ui_client=True, **context_overrides))


class TestAsksUserInText:
    @pytest.mark.parametrize("text", [
        "Which project do you mean: Atlas, Beacon or Cobalt?",
        "Could you tell me which report you mean?\n- Q2 2026 board report\n- Q3 2026 board report",
        "I need a bit more detail. **Which report should I help with?**",
        "Which quarter do you want?\n\n---\nConfidence: Medium",
        "Would you like the summary for Q2 or for Q3?",
        "I found two board reports. Would you like the Q2 or the Q3 one?",
        f"{_LONG_ANSWER}\n\nWhich of these should I update?\n- Atlas\n- Beacon",
        # Seen live for "Can you update it?": input asked for without a question mark.
        "I don't see an existing artifact in this conversation to update. Please upload the file or tell me "
        "what “it” refers to and what changes you want.",
        "Please choose one of these:\n- Q2 2026 board report\n- Q3 2026 board report",
        "Could you tell me which project you mean.",
        "Please let me know which policy you mean.",
        # Seen live: with 16 documents indexed, a reply that only asked for an upload instead of offering them.
        "I don't see a document attached or available in the conversation. Please upload the document or "
        "paste its text, and I'll summarize it.",
        # Seen live for "Summarize the document." with 16 documents indexed.
        "Please upload the document or share its link/title, and I'll summarize it.",
        "你指的是哪个项目？",  # Chinese: "Which project do you mean?"
    ])
    def test_a_reply_that_is_a_question_for_the_user(self, text: str) -> None:
        assert asks_user_in_text(text)

    @pytest.mark.parametrize("text", [
        "",
        "The deadline for Atlas is 15 December 2026.",
        "Hi! How can I help you today?",
        "Let me know if you need anything else?",
        "Here are the deadlines:\n- Atlas: 15 December\n- Beacon: 28 February",
        f"{_LONG_ANSWER}\n\nWould you like me to draft an email about this?",
        f"{_LONG_ANSWER}\n\nWould you like me to:\n- draft an email\n- create a ticket",
        f"{_LONG_ANSWER}\n\nWhat else would you like to know?",
        "Why does this matter? Because the old warehouse costs $38,000 a month after March.",
        "The Atlas public launch is on 15 December 2026, and the beta reaches 500 users on 10 November. "
        "Want me to add these to your calendar?",
        "The international per diem is $120 a day. Let me know if you need the domestic rate too.",
        "I couldn't reach Jira just now. Please try again in a minute.",
        "Version 2.1 shipped on 3 March. Would you like the notes for v2.1?",
        "The Q3 revenue was $20.1M, up 9% on Q2 and 15% on last year. Would you like the regional split for Q3?",
        f"{_LONG_ANSWER}\n\nPlease share this summary with the project owners.",
        # Seen live: an explanation that ends by listing the questions a document answers.
        f"{_LONG_ANSWER}\n\nMost databases cannot answer:\n- What was true at a past moment?\n- What changed between two dates?",
    ])
    def test_answers_and_offers_are_not(self, text: str) -> None:
        assert not asks_user_in_text(text)


class TestAskInTextRecoveryMiddleware:
    async def test_nudges_once_per_run(self) -> None:
        middleware = _recovery()
        scope = _scope()
        question = AssistantMessage(content="Which project do you mean?")

        first = await run_post_model(middleware, question, scope=scope)
        second = await run_post_model(middleware, question, scope=scope)

        assert isinstance(first.recovery_message, UserMessage)
        assert first.recovery_message.injected is True
        assert first.recovery_message.content == _ASK_IN_TEXT_NUDGE
        assert second.recovery_message is None

    async def test_no_nudge_for_an_answer(self) -> None:
        ctx = await run_post_model(_recovery(), AssistantMessage(content="Atlas ships on 15 December."), scope=_scope())
        assert ctx.recovery_message is None

    async def test_no_nudge_when_the_turn_calls_a_tool(self) -> None:
        ctx = await run_post_model(
            _recovery(), AssistantMessage(content="Which project do you mean?"),
            tool_calls=[ToolCall(id="1", name=_ASK_TOOL, arguments={})], scope=_scope(),
        )
        assert ctx.recovery_message is None

    async def test_no_nudge_for_a_client_that_cannot_show_cards(self) -> None:
        middleware = ask_in_text_recovery(make_context(has_ui_client=False))
        ctx = await run_post_model(middleware, AssistantMessage(content="Which project do you mean?"), scope=_scope())
        assert ctx.recovery_message is None

    async def test_no_nudge_in_a_spawned_agent(self) -> None:
        ctx = await run_post_model(
            _recovery(), AssistantMessage(content="Which project do you mean?"), scope=_scope(depth=1),
        )
        assert ctx.recovery_message is None

    @pytest.mark.parametrize(("tools", "granted"), [
        (("knowledgegraph__search",), None),
        ((_ASK_TOOL, "knowledgegraph__search"), ["knowledgegraph__search"]),
    ])
    async def test_no_nudge_when_the_run_has_no_question_tool(
        self, tools: tuple[str, ...], granted: list[str] | None,
    ) -> None:
        ctx = await run_post_model(
            _recovery(), AssistantMessage(content="Which project do you mean?"), scope=_scope(tools=tools, granted=granted),
        )
        assert ctx.recovery_message is None

    async def test_no_nudge_past_the_card_limit(self) -> None:
        card = {"role": "bot_response", "content": "", "tool_results": [
            {"tool_name": _ASK_TOOL, "status": "success", "args": {"questions": [{"question": "Which?"}]}},
        ]}
        answer = {"role": "user_query", "content": 'User selections:\n1. "Which?" → A'}
        context = make_context(
            has_ui_client=True,
            previous_conversations=[{"role": "user_query", "content": "file a bug"}, card, answer, card],
        )
        context.tool_state["ask_user_question_resume"] = True
        ctx = await run_post_model(
            ask_in_text_recovery(context), AssistantMessage(content="Which project do you mean?"), scope=_scope(),
        )
        assert ctx.recovery_message is None

    @pytest.mark.parametrize("query", [
        "ask me one question",
        "aske me two questions",
        "Quiz me on the Q3 plan",
        "interview me about my week",
        "mujhse ek sawal puchho",
    ])
    async def test_no_nudge_when_the_user_asked_to_be_asked(self, query: str) -> None:
        context = make_context(has_ui_client=True, query=query)
        ctx = await run_post_model(
            ask_in_text_recovery(context),
            AssistantMessage(content="What are you working on today?"), scope=_scope(),
        )
        assert ctx.recovery_message is None

    @pytest.mark.parametrize("query", [
        "My manager asked me to summarise the Q3 roadmap",
        "Priya asks me for the deck every Monday",
        "Don't ask me anything, just send the email",
        "do not ask me, pick the usual project",
        "Never quiz me before lunch",
    ])
    def test_reporting_or_refusing_a_question_is_not_a_request_to_be_asked(self, query: str) -> None:
        assert not asked_to_be_asked(query)

    async def test_a_report_of_being_asked_is_still_nudged(self) -> None:
        context = make_context(has_ui_client=True, query="My manager asked me to update the roadmap page")
        ctx = await run_post_model(
            ask_in_text_recovery(context), AssistantMessage(content="Which project do you mean?"), scope=_scope(),
        )
        assert isinstance(ctx.recovery_message, UserMessage)

    async def test_a_request_that_only_mentions_asking_is_still_nudged(self) -> None:
        context = make_context(has_ui_client=True, query="Update the roadmap page")
        ctx = await run_post_model(
            ask_in_text_recovery(context), AssistantMessage(content="Which project do you mean?"), scope=_scope(),
        )
        assert isinstance(ctx.recovery_message, UserMessage)

    def test_the_nudge_does_not_offer_to_drop_the_question(self) -> None:
        assert "without that question" not in _ASK_IN_TEXT_NUDGE

    async def test_no_nudge_without_a_scope(self) -> None:
        ctx = await run_post_model(_recovery(), AssistantMessage(content="Which project do you mean?"))
        assert ctx.recovery_message is None


def _agent(transport: ScriptedTransport) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=_registry(_ASK_TOOL))
    runtime.hooks.on(HookEvent.POST_MODEL).use(_recovery())
    spec = AgentSpec(
        name="assistant", system_prompt="You help.",
        model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=4,
    )
    return Agent(spec, runtime)


def _nudges(transport: ScriptedTransport) -> list[Any]:
    return [
        m for m in transport.calls[-1]["messages"]
        if isinstance(m, UserMessage) and str(m.content) == _ASK_IN_TEXT_NUDGE
    ]


class TestAskInTextRecoveryInTheLoop:
    async def test_the_reply_after_the_nudge_replaces_the_text_question(self) -> None:
        transport = (
            ScriptedTransport()
            .add_text("Which report do you mean?")
            .add_text("Here is the Q3 2026 board report summary: revenue was $20.1M.")
        )

        result = await _agent(transport).run(Goal(description="Summarize the board report"))

        assert result.success is True
        assert result.output == "Here is the Q3 2026 board report summary: revenue was $20.1M."
        assert len(_nudges(transport)) == 1

    async def test_a_second_text_question_stands(self) -> None:
        transport = ScriptedTransport().add_text("Which report do you mean?").add_text("Which quarter?")

        result = await _agent(transport).run(Goal(description="Summarize the board report"))

        assert result.output == "Which quarter?"
        assert len(_nudges(transport)) == 1
