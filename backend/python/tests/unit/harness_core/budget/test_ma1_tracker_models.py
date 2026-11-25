"""MA1 QA37-U11 (USAGE1 F2): the loop's ``BudgetTracker`` charges each turn to the model that ran it, at the tier
its prompt size selects.

Before, the tracker priced its cumulative totals at one model's short-context tier: a sub-agent on a cheaper model
sharing its parent's budget was charged the parent's price, and a prompt above a model's long-context threshold
cost less in the tracker than in the ledger. Expected costs are worked from the price rows by hand.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from harness_core.access.spend import usage_spend
from harness_core.kernel.budget.base import BudgetSnapshot
from harness_core.kernel.budget.tracker import BudgetTracker
from harness_core.kernel.core.exceptions import BudgetExceeded
from harness_core.kernel.core.responses import TokenUsage
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# gpt-6-sol: 2.00 in, 10.00 out, 0.20 cached read, 2.50 cache write; above 272,000 prompt tokens 4.00, 15.00.
A = "gpt-6-sol"
# claude-haiku-4-5: 1.00 in, 5.00 out.
B = "claude-haiku-4-5"


def _usd(snapshot: BudgetSnapshot) -> float:
    return round(snapshot.cost_usd, 9)


@pytest.mark.parametrize(
    ("turns", "expected_usd"),
    [
        pytest.param([(None, 1_000, 100, 0, 0)], 0.003, id="the_trackers_own_model"),
        pytest.param([(B, 1_000, 100, 0, 0)], 0.0015, id="the_turns_model"),
        pytest.param([(None, 1_000, 100, 0, 0), (B, 1_000, 100, 0, 0)], 0.0045, id="a_parent_on_A_and_a_sub_agent_on_B"),
        pytest.param([(None, 300_000, 1_000, 0, 0)], 1.215, id="a_prompt_above_the_long_context_threshold"),
        pytest.param(
            [(None, 300_000, 1_000, 0, 0), (None, 1_000, 100, 0, 0)],
            1.218,
            id="a_short_prompt_after_a_long_one_is_priced_short",
        ),
        pytest.param([(None, 272_000, 0, 0, 0)], 0.544, id="at_the_threshold_is_still_short"),
        pytest.param([(None, 5_300, 200, 4_000, 300)], 0.00555, id="cached_tokens_at_their_own_prices"),
        pytest.param([(B, 5_300, 200, 4_000, 300)], 0.002775, id="cached_tokens_of_a_model_without_cache_prices"),
        pytest.param([("a-model-the-table-does-not-list", 1_000, 100, 0, 0)], 0.0045, id="an_unlisted_model"),
    ],
)
async def test_qa37_u11_a_turn_is_priced_at_its_model_and_tier(
    turns: list[tuple[str | None, int, int, int, int]], expected_usd: float
) -> None:
    tracker = BudgetTracker(model=A)
    ledger_micros = 0
    for model, fresh_and_cached, output, read, written in turns:
        await tracker.record_turn(fresh_and_cached, output, read, written, model=model)
        usage = TokenUsage(
            input_tokens=fresh_and_cached, output_tokens=output, cache_read_tokens=read, cache_write_tokens=written
        )
        ledger_micros += usage_spend(usage, model or A, charges_usd=True).usd_micros

    snapshot = await tracker.snapshot()

    assert _usd(snapshot) == expected_usd
    assert round(snapshot.cost_usd * 1_000_000) == ledger_micros, "the tracker and the ledger charge the same"
    assert snapshot.turns == len(turns)
    assert snapshot.input_tokens == sum(turn[1] for turn in turns)


async def test_qa37_u11_the_cost_limit_holds_the_cost_of_the_models_that_ran() -> None:
    tracker = BudgetTracker(model=A, max_cost_usd=0.005)
    await tracker.record_turn(1_000, 100)
    await tracker.record_turn(1_000, 100, model=B)
    await tracker.check()

    await tracker.record_turn(1_000, 100, model=B)
    with pytest.raises(BudgetExceeded, match="max_cost_usd"):
        await tracker.check()


async def test_qa37_u11_a_snapshot_restores_the_cost_of_every_model() -> None:
    first = BudgetTracker(model=A)
    await first.record_turn(300_000, 1_000)
    await first.record_turn(1_000, 100, model=B)
    saved = BudgetSnapshot.model_validate_json((await first.snapshot()).model_dump_json())

    resumed = BudgetTracker(model=A)
    await resumed.restore(saved)

    assert await resumed.snapshot() == saved
    assert _usd(saved) == 1.2165
    await resumed.record_turn(1_000, 100, model=B)
    assert _usd(await resumed.snapshot()) == 1.218


async def test_qa37_u11_a_snapshot_that_states_no_cost_restores_as_its_totals_were_priced_before() -> None:
    resumed = BudgetTracker(model=A)
    await resumed.restore(BudgetSnapshot(input_tokens=1_000, output_tokens=100, turns=1, input_includes_cached=True))

    assert _usd(await resumed.snapshot()) == 0.003


def _spec(name: str, model: str) -> AgentSpec:
    return AgentSpec(
        name=name,
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model=model),
        loop=ReActLoop(),
        max_turns=2,
    )


async def test_qa37_u11_a_sub_agent_on_its_own_model_sharing_its_parents_tracker_is_charged_its_own_price() -> None:
    usage = TokenUsage(input_tokens=1_000, output_tokens=100)
    transports = TransportRegistry()
    scripted = ScriptedTransport()
    for _turn in range(3):
        _ = scripted.add_text("done", usage=usage)
    transports.register("scripted", lambda: scripted)
    budget = BudgetTracker(model=A)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry(), budget=budget)

    _ = await Agent(_spec("parent", A), runtime).run(Goal(description="plan"))
    _ = await Agent(_spec("researcher", B), runtime).run(Goal(description="look it up"))
    _ = await Agent(_spec("parent", A), runtime).run(Goal(description="answer"))

    snapshot = await budget.snapshot()
    assert _usd(snapshot) == 0.0075
    assert snapshot.turns == 3
