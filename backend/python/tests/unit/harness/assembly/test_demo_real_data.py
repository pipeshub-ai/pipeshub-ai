"""#3609 on the durable route: an agent run records whether anything besides
the demo is indexed, as legacy's `run_agent_loop_stream` does, so a demo-only
workspace gets the same prompt on both routes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import build_tool_environment
from app.modules.agents.context.source_catalog import ORG_HAS_REAL_DATA_KEY
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

DEMO = {"connectorId": "demo-1", "type": "Demo"}


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


async def _tool_state(knowledge: list[dict[str, str]], real_data: AsyncMock) -> dict[str, object]:
    services = w.Services(agent=w.agent_doc(knowledge=knowledge))
    spec = w.pinned_spec(
        tool_ids=[],
        apps=[k["connectorId"] for k in knowledge if k["type"] != "KB"],
        kb=[k["connectorId"] for k in knowledge if k["type"] == "KB"],
    )
    run = RunContext.from_claim(w.claim(spec))
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    model = await w.model_factory(port)(run, services)
    with patch("app.modules.demo_data.chat.org_real_data_state", real_data):
        env = await build_tool_environment(run, services, model)
    return env.context.tool_state


@pytest.mark.asyncio
async def test_a_demo_agent_run_learns_the_org_has_nothing_else_indexed() -> None:
    real_data = AsyncMock(return_value=False)
    state = await _tool_state([{"connectorId": "kb-1", "type": "KB"}, DEMO], real_data)
    real_data.assert_awaited_once()
    assert real_data.await_args.args[1] == w.ORG
    assert state[ORG_HAS_REAL_DATA_KEY] is False


@pytest.mark.asyncio
async def test_without_the_demo_nothing_is_looked_up() -> None:
    real_data = AsyncMock(return_value=False)
    state = await _tool_state([{"connectorId": "kb-1", "type": "KB"}], real_data)
    real_data.assert_not_awaited()
    assert ORG_HAS_REAL_DATA_KEY not in state
