"""CPD6-33: a worker joins ``control-plane-events`` with its first pinned run, never before, and runs without it.

Joining is started in the background (a Kafka group join takes seconds); until it has settled, and when the broker
cannot be joined at all, the cache's own reloads are what a pinned run reads (NFR-37).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.control_plane.contracts.generated.change_event import ChangeEvent
from app.control_plane.runtime.live_layer import ReloadingLiveLayerCache
from app.control_plane.testing.contracts import LiveLayerCacheContract
from app.control_plane.testing.memory import InMemoryLiveLayerSource
from app.harness.run_policy.live import JOIN_RETRY_S, WorkerLiveLayer
from harness_core.testing import ManualClock
from tests.unit.harness.assembly import world as w

from .support import DEPLOYMENT_DENIES_WRITES, ControlPlane

if TYPE_CHECKING:
    from collections.abc import Callable


class Broker:
    """The deployment's broker as a worker joins it: up or down, and what it hands the worker's handler."""

    def __init__(self) -> None:
        self.up = True
        self.joins = 0
        self.stopped = 0
        self.handler: Callable[[ChangeEvent], None] | None = None

    async def subscribe(self, handler: Callable[[ChangeEvent], None]) -> Broker:
        self.joins += 1
        if not self.up:
            raise ConnectionError("broker unreachable")
        self.handler = handler
        return self

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped += 1


def _worker() -> tuple[WorkerLiveLayer, Broker, ControlPlane, ManualClock]:
    clock = ManualClock()
    control = ControlPlane(w.Services(), clock)
    control.notifier(up=False)  # the seam's own delivery is off: events reach the worker through `Broker` only
    broker = Broker()
    return WorkerLiveLayer(ReloadingLiveLayerCache(control, clock), broker.subscribe, clock), broker, control, clock


def _event(revision: int) -> ChangeEvent:
    return ChangeEvent.model_validate(
        {
            "type": "cp.changed",
            "orgId": w.ORG,
            "revision": revision,
            "previous": revision - 1,
            "touched": [{"kind": "policy-section", "id": "approvals"}],
            "narrowing": False,
        }
    )


@dataclass
class _WorkerHarness:
    """The contract's harness over a worker's live layer: the same cache, read through the joining wrapper."""

    clock: ManualClock = field(default_factory=ManualClock)
    backstop: InMemoryLiveLayerSource = field(default_factory=InMemoryLiveLayerSource)

    def __post_init__(self) -> None:
        self.cache = WorkerLiveLayer(
            ReloadingLiveLayerCache(self.backstop, self.clock, bound=2), Broker().subscribe, self.clock
        )

    def loads(self) -> int:
        return len(self.backstop.calls)


class TestWorkerLiveLayerContract(LiveLayerCacheContract):
    @pytest.fixture
    def live_harness(self) -> _WorkerHarness:
        return _WorkerHarness()


async def test_cpd6_33_nothing_is_joined_until_a_pinned_run_reads_the_live_layer() -> None:
    live, broker, _, _ = _worker()
    assert broker.joins == 0

    _ = await live.current(w.ORG)
    _ = await live.current(w.ORG)

    assert await live.joined()
    assert broker.joins == 1


async def test_cpd6_33_an_event_from_the_topic_reloads_the_org_before_its_next_read() -> None:
    live, broker, control, _ = _worker()
    first = await live.current(w.ORG)
    assert await live.joined() and broker.handler is not None

    control.approvals(DEPLOYMENT_DENIES_WRITES)
    assert (await live.current(w.ORG)).revision == first.revision, "nothing told the worker yet"
    broker.handler(_event(first.revision + 1))

    assert (await live.current(w.ORG)).revision == first.revision + 1


async def test_cpd6_33_a_broker_that_is_down_leaves_the_poll_and_is_tried_again_later() -> None:
    live, broker, control, clock = _worker()
    broker.up = False

    first = await live.current(w.ORG)
    assert not await live.joined()
    _ = await live.current(w.ORG)
    assert not await live.joined()
    assert broker.joins == 1, "not on every read"

    control.approvals(DEPLOYMENT_DENIES_WRITES)
    broker.up = True
    _ = clock.advance(JOIN_RETRY_S)
    assert (await live.current(w.ORG)).revision == first.revision + 1, "the time to live reloaded it"
    assert await live.joined()
    assert broker.joins == 2


async def test_cpd6_33_stopping_the_worker_leaves_the_topic() -> None:
    live, broker, _, _ = _worker()
    _ = await live.current(w.ORG)

    await live.aclose()

    assert broker.stopped == 1
