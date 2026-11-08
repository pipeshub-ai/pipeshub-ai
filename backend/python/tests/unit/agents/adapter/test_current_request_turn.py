"""One chat turn through the real factory and agent loop, with a scripted model: what the model
is told the person asked (docs/context-engineering.md, CE-07, CE-08, CE-14).

- SC-12: outside quick mode the intent step's briefing is context for the model, in the
  turn-volatile band of the system prompt; the person's own words are the current user message.
- The system prompt carries a verbatim anchor of the current request, so it survives any
  compaction of the message list.
- HG-05: nothing tells the model a short new question is a follow-up of an earlier topic.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.core.messages import UserMessage
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.intent import IntentRouteDecision
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

QUERY = "What did the audit find?"
BRIEFING = "## Request\nExplain what the Zentora refund audit found.\n## Success Criteria\n- Name each finding."
HISTORY = [
    {"role": "user_query", "content": "What is the refund window for Zentora enterprise customers?"},
    {"role": "bot_response", "content": "45 days.", "tool_results": [
        {"tool_name": "knowledgehub__search", "result": "Refund policy: 45 days", "status": "success",
         "tool_id": "call_1", "args": {"query": "refund window"}},
    ]},
]


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


def _intent_returns(monkeypatch: pytest.MonkeyPatch, briefing: str, calls: list | None = None) -> None:
    async def _fake(*_args: Any, **kwargs: Any) -> IntentRouteDecision:  # noqa: ANN401
        if calls is not None:
            calls.append(kwargs)
        return IntentRouteDecision(rewritten_query=briefing, route="react")

    monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake)


async def _run_turn(chat_mode: str, query: str = QUERY) -> tuple[list[dict], Any]:
    context = make_context(llm=FakeChatModel(), previous_conversations=[dict(t) for t in HISTORY])
    agent, runtime, goal, _clarifying = await PipesHubAgentFactory().create(
        context, context.llm, chat_mode, query=query,
    )
    transport = ScriptedTransport().add_text("The audit found two issues.")
    runtime.transport_registry.register(agent.spec.model.provider, lambda: transport)
    await agent.run(goal)
    assert transport.calls, "the model was never called"
    return transport.calls, goal


def _last_human(messages: list) -> UserMessage:
    return [m for m in messages if isinstance(m, UserMessage) and not m.injected][-1]


class TestUniversalModeKeepsThePersonsWords:
    """SC-12."""

    async def test_current_message_is_the_raw_query_and_the_briefing_is_band_c(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _intent_returns(monkeypatch, BRIEFING)

        calls, goal = await _run_turn("auto")

        first = calls[0]
        assert _last_human(first["messages"]).content == QUERY
        assert goal.user_message == QUERY
        assert goal.interpretation == BRIEFING
        system = first["system"]
        assert "How the request was interpreted" in system
        assert "Explain what the Zentora refund audit found." in system

    async def test_quick_mode_message_is_unchanged(self) -> None:
        calls, goal = await _run_turn("quick")

        assert _last_human(calls[0]["messages"]).content == QUERY
        assert goal.interpretation is None


class TestCurrentRequestAnchor:
    """CE-08: the request is in the system prompt as well as the message list."""

    @pytest.mark.parametrize("chat_mode", ["quick", "auto"])
    async def test_every_model_call_carries_the_verbatim_request(
        self, chat_mode: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _intent_returns(monkeypatch, BRIEFING)

        calls, _goal = await _run_turn(chat_mode)

        for call in calls:
            assert "Current request (verbatim)" in call["system"]
            assert QUERY in call["system"]


class TestNoFollowUpSteering:
    """HG-05: a short new question is not pulled back to the earlier topic."""

    @pytest.mark.parametrize("query", [QUERY, "Summarize the security review", "How do I submit expenses?", "yes"])
    async def test_nothing_injects_recent_topic_or_reuse_hints(self, query: str) -> None:
        calls, goal = await _run_turn("quick", query)

        sent = calls[0]["system"] + "\n".join(str(m.content) for m in calls[0]["messages"])
        assert "Recent topic" not in sent
        assert "reuse previously fetched" not in sent
        assert not any("follow-up" in c for c in goal.constraints)


class TestResumesSkipTheIntentCall:
    """B9: an answer to an ask-user or approval card continues the turn it answers; the intent
    briefing for it was thrown away, so the call is not made."""

    async def test_ask_user_resume_in_an_explicit_mode_makes_no_intent_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list = []
        _intent_returns(monkeypatch, BRIEFING, calls)
        context = make_context(llm=FakeChatModel(), previous_conversations=[
            {"role": "user_query", "content": "Create a ticket for the outage"},
            {"role": "bot_response", "content": "", "tool_results": [{
                "tool_id": "call_ask", "tool_name": "internaltools__ask_user_question",
                "result": '{"questions": [{"question": "Which project?"}]}', "status": "success",
            }]},
        ])

        _agent, _runtime, goal, _clarifying = await PipesHubAgentFactory().create(
            context, context.llm, "react", query='User selections:\n1. "Which project?" → Billing',
        )

        assert calls == []
        assert goal.user_message == "Create a ticket for the outage"
