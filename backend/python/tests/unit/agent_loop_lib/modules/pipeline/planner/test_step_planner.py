"""`StepPlanner` — calls `complete()` and returns the model's raw text
verbatim as `Plan.text`, with zero parsing/narrowing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.responses import ModelResponse
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.step_planner import StepPlanner


@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.complete = AsyncMock(return_value=ModelResponse(
        message=AssistantMessage(content="1. **Fetch data**: call the API"),
    ))
    return t


async def test_no_model_returns_goal_description_as_text() -> None:
    p = StepPlanner(model=None)
    goal = Goal(description="do the thing")
    plan = await p.plan(goal)
    assert plan.text == "do the thing"


async def test_calls_complete_not_complete_structured(mock_transport) -> None:
    p = StepPlanner(model=mock_transport)
    await p.plan(Goal(description="task"))
    mock_transport.complete.assert_called_once()
    mock_transport.complete_structured.assert_not_called()


async def test_returns_raw_text_verbatim(mock_transport) -> None:
    p = StepPlanner(model=mock_transport)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == "1. **Fetch data**: call the API"


async def test_odd_format_response_never_raises(mock_transport) -> None:
    mock_transport.complete = AsyncMock(
        return_value=ModelResponse(message=AssistantMessage(content="just call the search tool"))
    )
    p = StepPlanner(model=mock_transport)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == "just call the search tool"
