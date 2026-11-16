"""The real gateway, fleet breaker and stage worker against a fake provider: an outage of any kind pauses, and recovery completes."""

import pytest

from app.services.llm_gateway.fleet_redis import FleetRedis, bind_fleet_redis
from tests.support.gateway_world import (
    OUTAGE_KINDS,
    GatewayWorld,
    patch_gateway_ceiling,
)
from tests.support.spend_world import SimClock

pytestmark = pytest.mark.timeout(600)
HOUR = 3600.0


@pytest.fixture(autouse=True)
def _ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gateway_ceiling(monkeypatch)


async def test_lone_poison_ends_at_the_absolute_cap(fleet, sim_clock) -> None:
    """A lone poison document with no other traffic is never charged (the key's success rate is unknown), so the fake
    receives exactly 20 requests for it (5 to open the breaker, then the trials it carries) and it ends FAILED with the
    absolute-cap reason. On the main line it loops until the 24 h wall."""
    world = GatewayWorld(fleet, sim_clock)
    world.provider.poison = {"poison-doc"}
    world.add_job("poison-doc")
    outcomes = await world.run()
    assert world.provider.requests["poison-doc"] == 20
    assert outcomes["poison-doc"].status == "FAILED"
    assert "20 times" in (outcomes["poison-doc"].reason or "") and "Reindex failed" in (outcomes["poison-doc"].reason or "")
    pause = await world.store.get(world.jobs["poison-doc"].state_key)
    assert pause is not None and pause.charged == 0 and pause.sends == 20 and pause.trials >= 14


def _jobs_for(count: int) -> list[str]:
    return [f"doc-{i}" for i in range(count)]


@pytest.mark.parametrize("duration_h", [0.5, 4.0], ids=["30min", "4h"])
@pytest.mark.parametrize("kind", OUTAGE_KINDS)
@pytest.mark.parametrize("workers", [1, 2, 4], ids=["W1", "W2", "W4"])
@pytest.mark.parametrize("jobs", [1, 20])
async def test_outages_are_waited_out(fleet, sim_clock, jobs: int, workers: int, kind: str, duration_h: float) -> None:
    """0 FAILED and every job COMPLETED after the outage, whatever the failure looks like on the wire and however many
    worker processes share the breaker. (With per-process breakers, W = 2 FAILs a lone job at about 16 minutes.)"""
    world = GatewayWorld(fleet, sim_clock, workers=workers)
    world.provider.kind = kind
    world.provider.outage_until_s = duration_h * HOUR
    for i, doc in enumerate(_jobs_for(jobs)):
        world.add_job(doc, arrive_s=i * 0.5)
    outcomes = await world.run()
    failed = {d: o.reason for d, o in outcomes.items() if o.status != "OK"}
    assert not failed, failed
    # nothing finished before the outage ended: the jobs waited for it
    assert min(o.finished_s for o in outcomes.values()) >= duration_h * HOUR


@pytest.mark.parametrize("duration_h", [0.5, 4.0], ids=["30min", "4h"])
@pytest.mark.parametrize("kind", ["503", "hang"])
@pytest.mark.parametrize("workers", [1, 2, 4], ids=["W1", "W2", "W4"])
async def test_two_hundred_jobs_are_waited_out(fleet, sim_clock, workers: int, kind: str, duration_h: float) -> None:
    world = GatewayWorld(fleet, sim_clock, workers=workers)
    world.provider.kind = kind
    world.provider.outage_until_s = duration_h * HOUR
    for i, doc in enumerate(_jobs_for(200)):
        world.add_job(doc, arrive_s=i * 0.1)
    outcomes = await world.run()
    assert not [d for d, o in outcomes.items() if o.status != "OK"]
    assert max(o.deliveries for o in outcomes.values()) < 80


async def test_the_result_does_not_depend_on_the_worker_count(fleet, sim_clock) -> None:
    """The same outage with 1, 2 and 4 workers puts the same number of requests on the wire for a lone job."""
    sent = []
    for workers in (1, 2, 4):
        # Each run from the same start: its own namespace (inside the fixture's, so its keys are cleaned) and clock.
        clock = SimClock()
        handle = FleetRedis(fleet.client, f"{fleet.namespace}:w{workers}", clock_ms=clock)
        bind_fleet_redis(handle)
        world = GatewayWorld(handle, clock, workers=workers)
        world.provider.kind = "hang"
        world.provider.outage_until_s = 4 * HOUR
        world.add_job(f"lone-{workers}")
        await world.run()
        sent.append(world.provider.requests[f"lone-{workers}"])
    bind_fleet_redis(fleet)
    assert len(set(sent)) == 1, sent
