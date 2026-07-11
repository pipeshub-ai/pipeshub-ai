"""`DefaultPlanner` — calls `complete()` and returns the model's raw text
verbatim as `Plan.text`, with zero parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.responses import ModelResponse
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.default import DefaultPlanner

_PLAN_TEXT = "1. **Research**: Gather data\n2. **Implement**: Write code"


@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.complete = AsyncMock(return_value=ModelResponse(
        message=AssistantMessage(content=_PLAN_TEXT),
    ))
    return t


async def test_calls_complete_not_complete_structured(mock_transport) -> None:
    p = DefaultPlanner(mock_transport)
    await p.plan(Goal(description="task"))
    mock_transport.complete.assert_called_once()
    mock_transport.complete_structured.assert_not_called()


async def test_returns_raw_text_verbatim(mock_transport) -> None:
    p = DefaultPlanner(mock_transport)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == _PLAN_TEXT


async def test_prompt_includes_goal_requirements_and_success_criteria(mock_transport) -> None:
    p = DefaultPlanner(mock_transport)
    goal = Goal(description="ship it", requirements=["fast"], success_criteria=["works"])
    await p.plan(goal)
    prompt = mock_transport.complete.call_args.kwargs["messages"][0].content
    assert "ship it" in prompt
    assert "fast" in prompt
    assert "works" in prompt


async def test_odd_format_response_never_raises(mock_transport) -> None:
    mock_transport.complete = AsyncMock(
        return_value=ModelResponse(message=AssistantMessage(content="just do it, no list here"))
    )
    p = DefaultPlanner(mock_transport)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == "just do it, no list here"
