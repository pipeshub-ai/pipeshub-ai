"""OPS-04 (ADR-OPS-01 D4.3): the egress proxy enforces the operator egress stops its ``EgressStopsFeed`` holds.

A held stop (every org, or one org) refuses a binding serving a run it covers with ``egress_stopped`` and revokes the
live ones; a binding that names no run attempt is never covered. Clearing a stop binds new sandboxes again. This
build's feed, ``NO_EGRESS_STOPS``, holds none; an edition's feed replaces it.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.harness.sandbox.egress_config import (
    NO_EGRESS_STOPS,
    EgressStopsFeed,
    NoEgressStops,
)
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import EgressBinding, InMemoryEgressBindings
from harness_core.sandbox.egress.bindings import (
    EGRESS_REVOKED,
    EGRESS_STOPPED,
    EVERY_RUN,
    Revocation,
)
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.server import create_control_app
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
)
from harness_core.sandbox.errors import SandboxRefusal
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from tests.support.settle import settle
from tests.unit.harness_core.sandbox.egress.test_wp14_revocation import (
    RefusingBindings,
    proxy_over,
    wiring_over,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

CREDENTIAL = "ops04-worker-credential"


def policy(placeholder: str) -> EgressPolicy:
    return EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:model"),),
    )


def run(org: str, run_id: str) -> dict[str, str]:
    return {"orgId": org, "sessionId": f"ses-{run_id}", "runId": run_id, "attemptId": f"{run_id}-a1"}


def binding(sandbox_id: str, n: int, labels: Mapping[str, str]) -> EgressBinding:
    return EgressBinding(sandbox_id, f"10.214.0.{4 * n + 2}", f"10.214.0.{4 * n + 3}", policy(f"ph-{sandbox_id}-0123456789ab"), labels)


A1 = binding("sbx-a1", 0, run("org-a", "r1"))
A2 = binding("sbx-a2", 1, run("org-a", "r2"))
B1 = binding("sbx-b1", 2, run("org-b", "r3"))
LEGACY = binding("sbx-legacy", 3, {"legacyExecutor": "run", "orgId": "org-a"})


async def refused_reason(bindings: InMemoryEgressBindings, item: EgressBinding) -> str:
    with pytest.raises(SandboxRefusal) as refused:
        await bindings.register(item)
    return refused.value.reason


async def test_ops_04_a_held_global_stop_refuses_a_run_binding_and_not_the_standard_chats() -> None:
    bindings = InMemoryEgressBindings()
    _ = await bindings.hold_stops(frozenset({EVERY_RUN}))
    for item in (A1, B1):
        assert await refused_reason(bindings, item) == EGRESS_STOPPED
    await bindings.register(LEGACY)
    assert await bindings.bound() == {"sbx-legacy"}


async def test_ops_04_an_org_stop_covers_only_that_org() -> None:
    bindings = InMemoryEgressBindings()
    _ = await bindings.hold_stops(frozenset({Revocation(org_id="org-a")}))
    assert await refused_reason(bindings, A1) == EGRESS_STOPPED
    await bindings.register(B1)
    assert await bindings.bound() == {"sbx-b1"}


async def test_ops_04_holding_a_list_revokes_every_live_binding_a_stop_covers() -> None:
    bindings = InMemoryEgressBindings()
    for item in (A1, A2, B1, LEGACY):
        await bindings.register(item)
    revoked = await bindings.hold_stops(frozenset({Revocation(org_id="org-a")}))
    assert revoked == ("sbx-a1", "sbx-a2")
    assert await bindings.bound() == {"sbx-b1", "sbx-legacy"}
    assert bindings.revoked() == {"sbx-a1", "sbx-a2"}


async def test_ops_04_clearing_a_stop_binds_new_sandboxes_and_revoked_ones_stay_revoked() -> None:
    bindings = InMemoryEgressBindings()
    await bindings.register(A1)
    _ = await bindings.hold_stops(frozenset({EVERY_RUN}))
    assert await bindings.hold_stops(frozenset()) == ()
    await bindings.register(A2)
    assert await bindings.bound() == {"sbx-a2"}
    assert await refused_reason(bindings, A1) == EGRESS_REVOKED


async def test_ops_04_a_durable_table_writes_the_revocation_so_a_restart_keeps_it() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    first = DurableEgressBindings(store, StateSealer(b"ops04-key"), clock)
    _ = await first.load("egress-1")
    await first.register(A1)
    assert await first.hold_stops(frozenset({EVERY_RUN})) == ("sbx-a1",)
    restarted = DurableEgressBindings(store, StateSealer(b"ops04-key"), clock)
    _ = await restarted.load("egress-2")
    assert await refused_reason(restarted, A1) == EGRESS_REVOKED


async def test_ops_04_the_broker_resync_drops_a_stopped_sandbox_and_gives_the_rest_back() -> None:
    fake = RefusingBindings({"sbx-a1": EGRESS_STOPPED})
    wiring = wiring_over(fake)
    wiring._held.update({item.sandbox_id: item for item in (A1, A2, LEGACY)})
    assert await wiring.resync() is True
    assert fake.registered == ["sbx-a2", "sbx-legacy"]
    assert "sbx-a1" not in wiring._held


async def test_ops_04_the_health_route_reports_how_many_stops_the_proxy_holds() -> None:
    bindings = InMemoryEgressBindings()
    _ = await bindings.hold_stops(frozenset({EVERY_RUN, Revocation(org_id="org-a")}))
    proxy = proxy_over(bindings, ManualClock(), "http://127.0.0.1:9")
    async with TestClient(TestServer(create_control_app(proxy, StaticBearerAuth(CREDENTIAL), instance="egress-ops04"))) as http:
        control = RemoteEgressBindings(str(http.make_url("")), StaticBearerAuth(CREDENTIAL))
        try:
            health = await control.health()
        finally:
            await control.aclose()
    assert (health.ok, health.egress_stops, health.kill_switch) == (True, 2, False)


async def test_ops_04_this_builds_feed_holds_no_stops_and_returns_once_stopped() -> None:
    bindings = InMemoryEgressBindings()
    await bindings.register(A1)
    stopped = asyncio.Event()
    task = asyncio.create_task(NO_EGRESS_STOPS.run(None, bindings, stopped))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    assert not task.done()
    stopped.set()
    await settle(task)
    assert bindings.stops() == set()
    assert await bindings.bound() == {"sbx-a1"}


def test_ops_04_the_proxy_reads_its_feed_through_the_edition_port() -> None:
    assert isinstance(NO_EGRESS_STOPS, EgressStopsFeed)
    assert NO_HARNESS_EDITION.port(EgressStopsFeed, NO_EGRESS_STOPS) is NO_EGRESS_STOPS

    class _Feed(NoEgressStops):
        pass

    feed = _Feed()
    assert HarnessEdition({EgressStopsFeed: feed}).port(EgressStopsFeed, NO_EGRESS_STOPS) is feed
    spec = importlib.util.find_spec("app.egress_proxy_main")
    assert spec is not None and spec.origin is not None
    assert "port(EgressStopsFeed, NO_EGRESS_STOPS)" in Path(spec.origin).read_text(encoding="utf-8")
