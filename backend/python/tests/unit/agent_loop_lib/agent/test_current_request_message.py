"""The current request a run adds is the person's own words (docs/context-engineering/REPORT.md,
CE-07), and every default system prompt says what a context note is (CE-05)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent import goal as goal_module
from app.agent_loop_lib.agent.goal import GoalBuilder
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.prompt import build_system_prompt
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.types import Goal, Intent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    import pytest


def _spec() -> AgentSpec:
    return AgentSpec(
        name="a",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=2,
    )


async def _first_call_messages(goal: Goal) -> list:
    transport = ScriptedTransport().add_text("done")
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    await Agent(_spec(), AgentRuntime(transport_registry=transports)).run(goal)
    return transport.calls[0]["messages"]


async def test_the_current_message_is_the_persons_words_not_the_interpretation() -> None:
    goal = Goal(
        description="## Request\nExplain the refund audit findings.",
        user_message="What did the audit find?",
        interpretation="## Request\nExplain the refund audit findings.",
    )

    messages = await _first_call_messages(goal)

    assert messages[-1] == UserMessage(content="What did the audit find?")


async def test_attached_content_follows_the_words() -> None:
    goal = Goal(description="d", user_message="Summarise this", attachment_text="<record>Q3 deck</record>")

    messages = await _first_call_messages(goal)

    assert messages[-1].content == "Summarise this\n\n<record>Q3 deck</record>"


async def test_without_user_message_the_description_is_the_request() -> None:
    messages = await _first_call_messages(Goal(description="What is 3 + 4?"))

    assert messages[-1] == UserMessage(content="What is 3 + 4?")


def test_the_default_prompt_says_context_notes_come_from_the_runtime() -> None:
    prompt = build_system_prompt(_spec(), AgentRuntime(), Goal(description="x"))

    assert '<context_note source="agent">' in prompt
    assert "not written by the user" in prompt


async def test_a_goal_built_from_an_intent_keeps_the_raw_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _structured(**_: object) -> dict:
        return {"description": "Explain the refund audit findings."}

    monkeypatch.setattr(goal_module, "run_structured_single_shot", _structured)
    intent = Intent(raw_message="What did the audit find?", parsed_intent="Explain the refund audit findings.")

    built = await GoalBuilder(object(), object()).build(intent)  # type: ignore[arg-type]

    assert built.request_text() == "What did the audit find?"
    assert built.description == "Explain the refund audit findings."


async def test_a_goal_built_without_a_model_keeps_the_raw_message() -> None:
    intent = Intent(raw_message="What did the audit find?", parsed_intent="audit findings")

    built = await GoalBuilder().build(intent)

    assert built.request_text() == "What did the audit find?"
