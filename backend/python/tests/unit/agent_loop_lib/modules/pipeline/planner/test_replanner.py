"""`Replanner` — calls `complete()` and returns the model's raw text
verbatim as `Plan.text`; `prior_plan_text` is forwarded into the prompt
as plain text, with zero parsing on either side."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.responses import ModelResponse
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.replanner import Replanner


@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.complete = AsyncMock(return_value=ModelResponse(
        message=AssistantMessage(content="1. **Redo**: fix the bug"),
    ))
    return t


async def test_no_model_returns_empty_plan() -> None:
    p = Replanner(model=None)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == ""


async def test_calls_complete_not_complete_structured(mock_transport) -> None:
    p = Replanner(model=mock_transport)
    await p.plan(Goal(description="task"))
    mock_transport.complete.assert_called_once()
    mock_transport.complete_structured.assert_not_called()


async def test_returns_raw_text_verbatim(mock_transport) -> None:
    p = Replanner(model=mock_transport)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == "1. **Redo**: fix the bug"


async def test_prior_plan_text_included_in_prompt(mock_transport) -> None:
    p = Replanner(model=mock_transport, prior_plan_text="did this already")
    await p.plan(Goal(description="task"))
    prompt = mock_transport.complete.call_args.kwargs["messages"][0].content
    assert "did this already" in prompt


async def test_no_prior_plan_text_omits_prior_summary(mock_transport) -> None:
    p = Replanner(model=mock_transport, prior_plan_text=None)
    await p.plan(Goal(description="task"))
    prompt = mock_transport.complete.call_args.kwargs["messages"][0].content
    assert "Prior plan" not in prompt


async def test_goal_already_achieved_response_passed_through_verbatim(mock_transport) -> None:
    mock_transport.complete = AsyncMock(
        return_value=ModelResponse(message=AssistantMessage(content="The goal is already achieved."))
    )
    p = Replanner(model=mock_transport)
    plan = await p.plan(Goal(description="task"))
    assert plan.text == "The goal is already achieved."
