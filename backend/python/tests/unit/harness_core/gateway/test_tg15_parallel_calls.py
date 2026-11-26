"""TG-15 (C3.40) and PF-18 (C7.08) at the Tool Gateway endpoint.

TG-15-1 ten concurrent calls from one run: each answers its own arguments under its own invocation, every one is in
        the effect log once, and no more than the run's cap execute at once.
TG-15-2 calls past the cap wait their turn, and a waiting call still sends progress, so the engine never times out
        a call that only queued.
TG-15-3 the cap is per run: one run at its cap never slows another run's calls.
TG-15-4 a cap below one refuses to build.
TG-15-5 a call waiting on a person (an approval hold, up to two minutes) gives its slot back meanwhile.
PF-18-G three parallel reads of 100, 200 and 300 ms take about 300 ms through the endpoint, not 600; ten at the
        default cap all run at once.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

import pytest

from harness_core.effects import EffectRunner, RequestEffectLog, ToolExecution
from harness_core.gateway.endpoint import RUN_CALL_CONCURRENCY, RunToolEndpoint
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.ids import SeededIds
from harness_core.testing.virtual_time import on_virtual_time, run_on_virtual_time

if TYPE_CHECKING:
    from harness_core.effects import ToolContext
    from harness_core.gateway.types import GatewayBinding
    from harness_core.kernel.core.messages import ToolCall

LOOKUP = offered("lookup")
_ARGS = {"type": "object", "properties": {"q": {"type": "string"}, "ms": {"type": "integer"}}}


class Tools:
    """A read that sleeps ``ms`` and records how many calls ran at once."""

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0

    async def lookup(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await asyncio.sleep(int(call.arguments.get("ms", 50)) / 1000)
        finally:
            self.running -= 1
        return ToolExecution(status="ok", content=f"{call.arguments['q']}@{ctx.invocation_id}")


def _endpoint(
    tools: Tools, *, binding: GatewayBinding = BINDING, **options: object
) -> tuple[RunToolEndpoint, RequestEffectLog]:
    log = RequestEffectLog()
    catalog = InMemoryToolCatalog(
        [CatalogTool(ToolSchema(name="lookup", description="Look up.", input_schema=_ARGS), LOOKUP.effect, tools.lookup)],
        [LOOKUP.effect.tool_id],
    )
    endpoint = RunToolEndpoint(
        binding=binding,
        catalog=catalog,
        effects=EffectRunner(log, lambda _call, _effect: "allow"),
        scope=InvocationScope(binding.run_id, binding.tenant, getattr(binding, "attempt_id", None)),
        ids=SeededIds(),
        citations=InMemoryCitationSink(),
        **options,  # pyright: ignore[reportArgumentType]
    )
    return endpoint, log


async def test_tg15_1_ten_concurrent_calls_are_isolated_audited_and_capped() -> None:
    tools = Tools()
    endpoint, log = _endpoint(tools, max_concurrent_calls=4)
    answers = await asyncio.gather(
        *(endpoint.call_tool(claims_for(BINDING), "lookup", {"q": f"q{i}", "ms": 50}) for i in range(10))
    )
    assert tools.peak == 4
    assert [a.text.split("@")[0] for a in answers] == [f"q{i}" for i in range(10)], "each call got its own answer"
    invocations = [a.invocation_id for a in answers]
    assert len(set(invocations)) == 10 and all(a.text.endswith(f"@{a.invocation_id}") for a in answers)
    assert all(not a.is_error and a.disposition == "executed" for a in answers)
    assert len(log.outcomes()) == 10, "every call is in the effect log once"


@on_virtual_time
async def test_tg15_2_a_call_waiting_for_a_slot_still_sends_progress() -> None:
    tools = Tools()
    endpoint, _ = _endpoint(tools, max_concurrent_calls=1, progress_every_s=0.02)
    ticks: list[float] = []

    async def progress(elapsed: float) -> None:
        ticks.append(elapsed)

    first = asyncio.ensure_future(endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "slow", "ms": 200}))
    await asyncio.sleep(0.01)
    queued = await endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "next", "ms": 1}, progress=progress)
    assert (await first).text.startswith("slow") and queued.text.startswith("next")
    assert len(ticks) >= 3, "the queued call reported progress while it waited"
    assert tools.peak == 1


def test_tg15_3_the_cap_is_per_run() -> None:
    async def main() -> None:
        other = dataclasses.replace(BINDING, run_id="run-2", attempt_id="run-2.a1")
        tools_a, tools_b = Tools(), Tools()
        a, _ = _endpoint(tools_a, max_concurrent_calls=1)
        b, _ = _endpoint(tools_b, binding=other, max_concurrent_calls=1)
        busy = [
            asyncio.ensure_future(a.call_tool(claims_for(BINDING), "lookup", {"q": f"a{i}", "ms": 200})) for i in range(3)
        ]
        await asyncio.sleep(0.01)
        loop = asyncio.get_running_loop()
        started = loop.time()
        answer = await b.call_tool(claims_for(other), "lookup", {"q": "b", "ms": 10})
        assert loop.time() - started < 0.15, "run b waited for run a's slots"
        assert answer.text.startswith("b")
        _ = await asyncio.gather(*busy)

    run_on_virtual_time(main)


def test_tg15_4_a_cap_below_one_refuses_to_build() -> None:
    with pytest.raises(ValueError, match="concurrent"):
        _ = _endpoint(Tools(), max_concurrent_calls=0)


def test_pf18_g_three_parallel_reads_take_about_as_long_as_the_slowest() -> None:
    async def main() -> None:
        tools = Tools()
        endpoint, _ = _endpoint(tools)
        loop = asyncio.get_running_loop()
        started = loop.time()
        answers = await asyncio.gather(
            *(endpoint.call_tool(claims_for(BINDING), "lookup", {"q": f"r{ms}", "ms": ms}) for ms in (100, 200, 300))
        )
        elapsed = loop.time() - started
        assert all(not a.is_error for a in answers)
        assert 0.29 <= elapsed < 0.45, f"{elapsed:.3f} s: the reads ran one after another"
        assert tools.peak == 3

    run_on_virtual_time(main)


async def test_pf18_g_ten_calls_at_the_default_cap_all_run_at_once() -> None:
    assert RUN_CALL_CONCURRENCY >= 10
    tools = Tools()
    endpoint, _ = _endpoint(tools)
    _ = await asyncio.gather(
        *(endpoint.call_tool(claims_for(BINDING), "lookup", {"q": f"q{i}", "ms": 100}) for i in range(10))
    )
    assert tools.peak == 10


def test_tg15_5_a_call_waiting_on_a_person_gives_its_slot_back() -> None:
    """An approval can wait up to the hold (two minutes): meanwhile the run's other calls are not queued behind it."""
    class SlowHold:
        async def hold(self, call: object) -> str:
            await asyncio.sleep(0.4)
            return "approved"

    async def main() -> None:
        world = build_endpoint(approvals={"transfer": "ask"}, hold=SlowHold(), hold_s=5.0, max_concurrent_calls=1)  # pyright: ignore[reportArgumentType]
        endpoint = world.endpoint
        held = asyncio.ensure_future(endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"}))
        await asyncio.sleep(0.05)
        loop = asyncio.get_running_loop()
        started = loop.time()
        read = await endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "while-held"})
        assert loop.time() - started < 0.2, "the read queued behind a call waiting for approval"
        assert not read.is_error
        assert (await held).disposition == "executed"

    run_on_virtual_time(main)
