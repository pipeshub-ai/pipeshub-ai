"""R33JGAPS-FU-1: a chat turn's ``keywordSearchBudget`` on the durable path means what it means on today's loop.

The bundled ``@pipeshub-ai/mcp`` SDK sends ``keywordSearchBudget: "short"`` on every ``pipeshub_chat`` call. Node
carries it in the run input (one schema with the chat routes) while a live worker advertises
``keyword_search_budget``; the worker hands it to the loop state, where each knowledge search reads it
(``pattern_match_wait_budget``), exactly as legacy's ``ChatQuery.keywordSearchBudget``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.assembly.context import RunContext
from app.harness.contracts.state import RunInputSnapshot
from app.harness.worker.capabilities import PIPESHUB_FEATURES
from app.utils.pattern_match import pattern_match_wait_budget

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


def test_r34_ksb_1_the_wire_input_takes_the_budget_and_refuses_another_value() -> None:
    snapshot = RunInputSnapshot.model_validate(
        {"query": "q", "model": {"chatMode": "quick"}, "keywordSearchBudget": "short"}
    )
    assert snapshot.keyword_search_budget == "short"
    assert RunInputSnapshot.model_validate({"query": "q", "model": {"chatMode": "quick"}}).keyword_search_budget is None
    with pytest.raises(ValueError):
        _ = RunInputSnapshot.model_validate(
            {"query": "q", "model": {"chatMode": "quick"}, "keywordSearchBudget": "forever"}
        )


def test_r34_ksb_2_the_run_context_carries_it() -> None:
    spec = w.pinned_spec(tool_ids=[], kb=[])
    assert RunContext.from_claim(w.claim(spec, keyword_search_budget="short")).keyword_search_budget == "short"
    assert RunContext.from_claim(w.claim(spec)).keyword_search_budget is None


@pytest.mark.parametrize(("sent", "wait"), [("short", pattern_match_wait_budget("short")), (None, None)])
async def test_r34_ksb_3_the_loop_state_each_search_reads_holds_it(sent: str | None, wait: float | None) -> None:
    from app.harness.assembly.tool_environment import build_tool_environment
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    agent = w.agent_doc()
    spec = w.pinned_spec(tool_ids=[], kb=[])
    services = w.Services(agent=agent)
    run = RunContext.from_claim(w.claim(spec, keyword_search_budget=sent))
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    env = await build_tool_environment(run, services, await w.model_factory(port)(run, services))
    assert env.context.tool_state.get("keyword_search_budget") == sent
    assert pattern_match_wait_budget(env.context.tool_state.get("keyword_search_budget")) == wait


def test_r34_ksb_4_this_worker_advertises_the_feature() -> None:
    assert "keyword_search_budget" in PIPESHUB_FEATURES
