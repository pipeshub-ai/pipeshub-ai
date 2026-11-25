"""R38EGRESS-FU-1 (ADR-SEC-03 D3): a flow hold pushed mid-turn survives a proxy that restarts without its sealed state.

The worker told only the proxy. A proxy that restarted and could not open its state changed its instance id, so the
broker's resync gave it the turn's first binding back: the hold was lost until the next turn, and the turn could
upload what it had just read. The hold now goes through the sandbox's broker, which narrows the binding it holds
(the one ``resync`` re-registers) before it tells the proxy; a broker or handle that cannot (no proxy behind it, an
older broker) leaves the worker to tell the proxy itself, as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.harness.sandbox.binding_proof import BindingAttester
from app.harness.sandbox.docker_egress import DockerEgressWiring
from app.harness.websites import LiveFlowHold, RunSites
from harness_core.effects.flow import FlowRule
from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefused,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress import EgressRefusalReason as R
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
)
from harness_core.sandbox.errors import InvalidSandboxRequest, SandboxNotFound
from harness_core.sandbox.remote import wire
from harness_core.sandbox.remote.client import RemoteSandboxBroker, RemoteSandboxHandle
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.sandbox.types import EgressPolicy
from harness_core.security.classification import UNCONFIGURED
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.testing.sandbox.fake import FakeSandboxHandle
from tests.unit.harness.security.test_sec_a_sandbox_egress import (
    CANARY,
    LABELS,
    PUBLIC,
    _Ledger,  # pyright: ignore[reportPrivateUsage]
    _Policy,  # pyright: ignore[reportPrivateUsage]
    _send,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.sandbox.types import EgressDestination

SITE = "mirror.example.org"


def _proxy(table: DurableEgressBindings, clock: ManualClock) -> EgressProxy:
    return EgressProxy(
        bindings=table,
        tokens=InMemoryTokenSource(b"fu1", clock, ttl_s=600),
        resolver=StaticResolver({SITE: [PUBLIC]}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=True),
    )


@dataclass
class _ControlPlane:
    """The proxy's control plane as the broker's ``RemoteEgressBindings`` reaches it; ``restart`` swaps the process."""

    proxy: EgressProxy
    instance: str
    holds: list[str] = field(default_factory=list[str])

    async def health(self) -> object:
        return type("Health", (), {"instance": self.instance})()

    async def register(self, binding: EgressBinding) -> None:
        await self.proxy.bindings.register(binding)

    async def bound(self) -> set[str]:
        return {b.sandbox_id for b in self.proxy.bindings.held()}

    async def unregister(self, sandbox_id: str) -> None:
        await self.proxy.bindings.unregister(sandbox_id)

    async def hold_flow(self, *, sandbox_id: str, run_id: str, hold: str) -> None:
        self.holds.append(hold)
        _ = await self.proxy.hold_flow(sandbox_id, run_id=run_id, hold=hold)


@dataclass
class _BrokerShelf:
    """The turn's sandbox as the worker reaches it: its broker's wiring takes the hold (the broker route's end)."""

    wiring: DockerEgressWiring

    def sandbox_id(self) -> str | None:
        return "sbx-a"

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None:
        return await self.wiring.hold_flow("sbx-a", run_id=run_id, hold=hold)


def _wiring(plane: _ControlPlane) -> DockerEgressWiring:
    return DockerEgressWiring(
        provider=None,  # type: ignore[arg-type]
        clock=ManualClock(),
        proxy_container="egress-proxy",
        bindings=plane,  # type: ignore[arg-type]
        pool=None,  # type: ignore[arg-type]
        network_prefix="pxh-",
        attester=BindingAttester(None),
    )


async def _turn(destinations: tuple[EgressDestination, ...]) -> tuple[EgressBinding, _ControlPlane, DockerEgressWiring, ManualClock]:
    clock = ManualClock()
    binding = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", EgressPolicy(destinations=destinations), LABELS)
    table = DurableEgressBindings(InMemoryEgressStateStore(clock), StateSealer(b"state-key-1"), clock)
    loaded = await table.load("egress-1")
    plane = _ControlPlane(_proxy(table, clock), loaded.instance)
    wiring = _wiring(plane)
    # The turn's bind, as ``DockerEgressWiring.bind`` registers it.
    await plane.register(binding)
    wiring._held["sbx-a"] = binding  # pyright: ignore[reportPrivateUsage]
    wiring._proxy_instance = loaded.instance  # pyright: ignore[reportPrivateUsage]
    return binding, plane, wiring, clock


async def test_a_hold_pushed_mid_turn_is_given_back_to_a_proxy_that_lost_its_state() -> None:
    ledger = _Ledger()
    sites = RunSites(
        _Policy([{"host": SITE, "access": "read-write", "readWriteConfirmed": True}]),
        UNCONFIGURED, ledger, "r1", (FlowRule("ask"),),
    )
    live = LiveFlowHold(sites, None, _BrokerShelf(None))  # type: ignore[arg-type]
    binding, plane, wiring, clock = await _turn(await live.sites())
    live.sandbox = _BrokerShelf(wiring)
    ledger.records = frozenset({"rec-private"})
    await live.narrow()
    assert plane.holds == ["held"], "the broker told the proxy"

    # The proxy restarts and cannot open its sealed state (another key): every binding is gone, the instance changes.
    table = DurableEgressBindings(plane.proxy.bindings._store, StateSealer(b"state-key-2"), clock)  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    loaded = await table.load("egress-2")
    assert loaded.unverified == 1 and table.get("sbx-a") is None
    plane.proxy, plane.instance = _proxy(table, clock), loaded.instance
    assert await wiring.resync()

    with pytest.raises(EgressRefused) as held:
        _ = await _send(plane.proxy, binding, SITE, "POST", "/upload", CANARY.encode())
    assert held.value.reason is R.SITE_UPLOAD_HELD


async def test_the_broker_narrows_only_its_own_sandbox_for_its_own_run() -> None:
    _binding, plane, wiring, _clock = await _turn(())
    with pytest.raises(InvalidSandboxRequest):
        _ = await wiring.hold_flow("sbx-a", run_id="another-run", hold="held")
    with pytest.raises(SandboxNotFound):
        _ = await wiring.hold_flow("sbx-other", run_id="r1", hold="held")
    with pytest.raises(InvalidSandboxRequest):
        _ = await wiring.hold_flow("sbx-a", run_id="r1", hold="released")
    assert plane.holds == []


@dataclass
class _Control:
    calls: list[str] = field(default_factory=list[str])

    async def hold_flow(self, *, sandbox_id: str, run_id: str, hold: str) -> None:
        self.calls.append(hold)


@dataclass
class _NoBroker:
    def sandbox_id(self) -> str | None:
        return "sbx-a"

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None:
        return None


async def test_a_sandbox_whose_broker_cannot_hold_falls_back_to_the_proxy() -> None:
    ledger = _Ledger()
    sites = RunSites(_Policy([{"host": "docs.example.com", "access": "read"}]), UNCONFIGURED, ledger, "r1", (FlowRule("ask"),))
    control = _Control()
    live = LiveFlowHold(sites, control, _NoBroker())
    _ = await live.sites()
    ledger.records = frozenset({"rec-private"})
    await live.narrow()
    assert control.calls == ["held"]


# --- the broker route ------------------------------------------------------------------------------------------------

TOKEN = "test-broker-token"
SPEC = SandboxSpec(image_digest="engine@sha256:" + "0" * 64, mode=RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/w"),))


@pytest.fixture
async def remote() -> AsyncIterator[RemoteSandboxBroker]:
    server = TestServer(create_broker_app(FakeSandboxBroker(clock=ManualClock()), StaticBearerAuth(TOKEN)))
    await server.start_server()
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))
    yield client
    await client.aclose()
    await server.close()


async def test_the_route_carries_the_hold_to_the_broker_s_handle(
    remote: RemoteSandboxBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    async def hold_flow(self: FakeSandboxHandle, *, run_id: str, hold: str) -> bool | None:
        seen.append((run_id, hold))
        return True

    handle = await remote.acquire(SPEC, lease=LeaseRef("w", 1), idempotency_key="k")
    try:
        assert await handle.hold_flow(run_id="r1", hold="held") is None, "a handle without it: the broker cannot"
        monkeypatch.setattr(FakeSandboxHandle, "hold_flow", hold_flow, raising=False)
        assert await handle.hold_flow(run_id="r1", hold="forbidden") is True
        assert seen == [("r1", "forbidden")]
        with pytest.raises(InvalidSandboxRequest):
            _ = await handle.hold_flow(run_id="r1", hold="nothing")
    finally:
        await remote.release(handle)
    assert await RemoteSandboxHandle(remote, "sbx-released", RuntimeMode.GVISOR).hold_flow(run_id="r1", hold="held") is None


async def test_a_broker_without_the_route_reads_as_cannot() -> None:
    async def modes(_: web.Request) -> web.Response:
        return web.json_response(wire.modes_body(frozenset({RuntimeMode.GVISOR})))

    older = web.Application()
    _ = older.router.add_get(wire.MODES, modes)
    server = TestServer(older)
    await server.start_server()
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))
    try:
        assert await RemoteSandboxHandle(client, "sbx-old", RuntimeMode.GVISOR).hold_flow(run_id="r1", hold="held") is None
    finally:
        await client.aclose()
        await server.close()
