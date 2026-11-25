"""SEC2 BT-1: the in-process ``BudgetTracker`` charges a cached turn what the ledger charges.

The loop reports each response's usage as its transport reported it: the whole prompt in
``input_tokens``, cached reads and cache writes inside it (USAGE1). The tracker costs the turn with
the price table the metering, the ledger and the gateway use (SEC1 H4), so cached tokens are priced
once. U1-14: a snapshot saved while ``input_tokens`` left cached tokens out restores to the same cost.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.providers.budget.base import BudgetSnapshot
from app.agent_loop_lib.modules.providers.budget.tracker import BudgetTracker
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from harness_core.access.spend import usage_spend
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport
from tests.unit.harness_core.budget.test_sec1_cache_charges import (
    EXPECTED_MICROS,
    MODEL,
    USAGE,
)


class _FamilyTransport(ScriptedTransport):
    def __init__(self, family: str) -> None:
        super().__init__()
        self._family = family

    @property
    def provider(self) -> str:
        return self._family


async def _tracked(family: str) -> BudgetTracker:
    transport = _FamilyTransport(family).add_text("done", usage=USAGE)
    transports = TransportRegistry()
    transports.register(family, lambda: transport)
    budget = BudgetTracker(model=MODEL)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry(), budget=budget)
    spec = AgentSpec(
        name="bt1",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider=family, model=MODEL),
        loop=ReActLoop(),
        max_turns=2,
    )
    await Agent(spec, runtime).run(Goal(description="hi"))
    return budget


@pytest.mark.parametrize("family", ["openai", "azure_direct", "gemini", "anthropic", "bedrock"])
async def test_bt1_the_tracker_costs_a_cached_turn_as_the_ledger_does(family: str) -> None:
    snapshot = await (await _tracked(family)).snapshot()
    assert usage_spend(USAGE, MODEL, charges_usd=True).usd_micros == EXPECTED_MICROS
    assert round(snapshot.cost_usd * 1_000_000) == EXPECTED_MICROS
    assert (snapshot.input_tokens, snapshot.cache_read_tokens, snapshot.cache_write_tokens) == (5_300, 4_000, 300)
    assert snapshot.input_includes_cached


async def test_u1_14_a_snapshot_saved_before_the_convention_restores_to_the_same_cost() -> None:
    saved = BudgetSnapshot(
        input_tokens=1_000, output_tokens=50, cache_read_tokens=4_000, cache_write_tokens=300, turns=1,
        cost_usd=EXPECTED_MICROS / 1_000_000,
    )
    budget = BudgetTracker(model=MODEL)
    await budget.restore(saved)
    restored = await budget.snapshot()
    assert round(restored.cost_usd * 1_000_000) == EXPECTED_MICROS
    assert restored.input_tokens == 5_300

    again = BudgetTracker(model=MODEL)
    await again.restore(restored)
    assert await again.snapshot() == restored
