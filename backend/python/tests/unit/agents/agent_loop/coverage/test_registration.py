"""Where the factory puts the coverage gate among the POST_MODEL hooks."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.hooks.events import HookEvent
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory


def _post_model_hooks(chat_mode: str) -> list[str]:
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")
    context.tool_state["chat_mode"] = chat_mode
    pipeline = PipesHubAgentFactory._build_hooks(context).on(HookEvent.POST_MODEL)
    return [middleware.__qualname__.split(".")[0] for _matcher, middleware in pipeline._stack]


def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COVERAGE_LOOP", raising=False)
    assert "coverage_gate" not in _post_model_hooks("internal_search")


def test_on_for_enterprise_search_before_the_completion_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    hooks = _post_model_hooks("internal_search")
    assert hooks.index("coverage_gate") < hooks.index("completion_gate") < hooks.index("ask_in_text_recovery")


@pytest.mark.parametrize("chat_mode", ["agent", "web_search", "quick"])
def test_not_for_modes_that_can_answer_from_elsewhere(monkeypatch: pytest.MonkeyPatch, chat_mode: str) -> None:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    assert "coverage_gate" not in _post_model_hooks(chat_mode)


def test_the_gate_part_turns_off_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    monkeypatch.setenv("COVERAGE_LOOP_GATE", "false")
    assert "coverage_gate" not in _post_model_hooks("internal_search")
