"""WP-51 (C9.06 follow-up): an answer a shared budget cut short names that budget's scope on its finalized answer,
so an extension's stop line can name it; the run's own spending limit, or another limit, names none."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_answer_finalized import (
    PipeshubAnswerFinalizedData,
)
from app.harness.presentation import PipesHubPresenter
from harness_core.kernel.durable.ports import RunEnding
from harness_core.tools.registry import ToolRegistry
from tests.unit.agents.adapter.conftest import make_context

FINALIZED = "x.pipeshub.answer.finalized"


async def _finalized(ending: RunEnding) -> dict[str, Any]:
    context = make_context(conversation_id="s-1", protocol="agui", run_id="run-1")
    events = await PipesHubPresenter(context, ToolRegistry()).finalize(ending)
    [event] = [e for e in events if e.type == FINALIZED]
    return dict(event.data)


@pytest.mark.parametrize("scope", ["pool-a", "pool_b", "p"])
async def test_wp51_a_shared_budget_stop_names_its_scope(scope: str) -> None:
    data = await _finalized(RunEnding(outcome="answered", texts=("So far…",), truncated_by="budget", budget_scope=scope))
    assert (data["truncatedBy"], data["budgetScope"]) == ("budget", scope)


@pytest.mark.parametrize(
    ("truncated_by", "scope"), [("budget", None), ("turns", None), (None, None)], ids=["run-cap", "turns", "none"]
)
async def test_wp51_no_shared_budget_names_none(truncated_by: str | None, scope: str | None) -> None:
    data = await _finalized(
        RunEnding(outcome="answered", texts=("Done.",), truncated_by=truncated_by, budget_scope=scope)  # type: ignore[arg-type]
    )
    assert "budgetScope" not in data


def test_wp51_the_contract_carries_the_scope_and_refuses_one_not_shaped_as_one() -> None:
    base = {"outcome": "answered", "answer": "a", "partTexts": ["a"], "citations": []}
    parsed = PipeshubAnswerFinalizedData.model_validate({**base, "truncatedBy": "budget", "budgetScope": "pool-a"})
    assert parsed.budget_scope == "pool-a"
    with pytest.raises(ValueError):
        PipeshubAnswerFinalizedData.model_validate({**base, "truncatedBy": "budget", "budgetScope": "Pool A"})


@pytest.mark.parametrize("scope", ["pool-a", "p", None])
def test_wp56_stop_1_an_engine_answer_names_whose_budget(scope: str | None) -> None:
    """WP56-STOP-1: the engine path's finalized answer carries the scope as the loop's does, and stays valid."""
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from harness_core.engines.durable.ports import ProgressStamp

    stamp = ProgressStamp("task-1", "turn-1", iter(range(100)).__next__)
    [_, answer] = PipesHubEngineRunPresenter().finished(
        stamp, success=True, reason=None, output="So far…", truncated_by="budget", budget_scope=scope
    )
    assert answer.type == FINALIZED
    assert answer.data.get("budgetScope") == scope
    PipeshubAnswerFinalizedData.model_validate(answer.data)
    [_, turns] = PipesHubEngineRunPresenter().finished(
        stamp, success=True, reason=None, output="x", truncated_by="turns", budget_scope="pool-a"
    )
    assert "budgetScope" not in turns.data
