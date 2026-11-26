"""CH-14: a clock jump on a worker node moves no lease, deadline, retry time or budget.

A run's deadline and a yield's ``notBefore`` are instants on the store's clock (R-8). The worker reads them on the
store's clock carried from its claim (``Claim.store_time``), never its own wall clock, so a stepped node clock
neither ends a run's working time early nor parks a retry for as long as the step. Leases already self-fence on the
suspend-aware clock (``Lease``). Node's own predicates read DB time (``time/mongo.ts``, ``time/pg.ts``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from harness_core.budget.run_budget import scoped_bound_s
from harness_core.clock import StoreTime
from harness_core.driver import CapabilityLost, RunDriver
from harness_core.journal.types import RunNotClaimable
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.capability import CAPABILITY_RECHECK_S
from harness_core.testing import ManualClock
from harness_core.testing.run_spec import minimal_run_spec

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.driver import AssembledRuntime
    from harness_core.journal import Claim
    from harness_core.kernel.durable import ToolContext

HOUR = 3600.0
JUMPS = [pytest.param(2 * HOUR, id="forward"), pytest.param(-2 * HOUR, id="backward")]


def test_ch14_1_store_time_follows_the_store_through_a_wall_clock_jump() -> None:
    clock = ManualClock()
    store_ms = int(clock.now().timestamp() * 1000) + 5_000
    time = StoreTime(clock, store_ms=store_ms, at=clock.suspend_aware())
    clock.jump(2 * HOUR)
    clock.jump(-5 * HOUR)
    assert time.now_s() == store_ms / 1000
    _ = clock.suspend(30)  # a host that slept: the store's clock kept going
    _ = clock.advance(10)
    assert time.now_s() == store_ms / 1000 + 40
    assert time.now() == datetime.fromtimestamp(store_ms / 1000 + 40, tz=UTC)


def test_ch14_2_a_journal_that_names_no_store_reading_keeps_the_wall_clock() -> None:
    clock = ManualClock()
    time = StoreTime(clock, store_ms=None, at=clock.suspend_aware())
    clock.jump(HOUR)
    assert time.now() == clock.now()


class _Lost:
    async def build(self, claim: Claim) -> AssembledRuntime:
        raise CapabilityLost(missing=["mcp:inst-1"])


@pytest.mark.parametrize("jump_s", JUMPS)
async def test_ch14_3_a_yield_is_due_on_the_stores_clock_whatever_the_worker_clock_says(jump_s: float) -> None:
    """Forward, the retry would wait the step out (two hours stalled); backward, it would be due at once."""
    world = World(worker_clock=ManualClock())
    world.admit()
    world.clock.jump(jump_s)
    driver = RunDriver(journal=world.journal, assembler=_Lost(), clock=world.clock)
    done = await driver.run_claimed(await world.claim())
    assert (done.status, done.reason) == ("yielded", "capability_wait")
    delay = CAPABILITY_RECHECK_S[0]
    _ = world.db_clock.advance(delay - 1)
    with pytest.raises(RunNotClaimable):
        _ = await world.journal.claim(RUN, worker_id="w")
    _ = world.db_clock.advance(1)
    assert await world.journal.claim(RUN, worker_id="w") is not None


@pytest.mark.parametrize("jump_s", JUMPS)
async def test_ch14_4_a_jump_mid_run_moves_no_tool_time_bound(jump_s: float) -> None:
    """The run's deadline is 30 minutes off on the store's clock. A tool call is bounded by it (ADR-RUN-01 D4): read
    on a wall clock two hours ahead, every later call would get no time at all."""
    world = World(worker_clock=ManualClock())
    spec = minimal_run_spec()
    deadline = world.db_clock.now() + timedelta(minutes=30)
    budgets = spec.budgets.model_copy(update={"calendar_deadline": deadline.isoformat().replace("+00:00", "Z")})
    world.admit(spec=spec.model_copy(update={"budgets": budgets}))
    bounds: list[float | None] = []

    async def probe(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        bounds.append(scoped_bound_s(600.0))
        if len(bounds) == 1:
            world.clock.jump(jump_s)
        return ToolExecution(status="ok", content={"ok": True})

    world.tools.add("probe", probe, tags=[("category", "read")])
    _, port = scripted(reply("", ("probe", {})), reply("", ("probe", {})), reply("done"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed", done
    assert bounds == [600.0, 600.0]
    assert [e.data for e in world.events("run.completed")] == [{"outcome": "answered"}]
