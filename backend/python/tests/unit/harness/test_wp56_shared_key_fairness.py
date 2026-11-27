"""WP56-FAIR (G-45, after WP-61): orgs that share one upstream key share its limit fairly.

WP-61 keyed the provider limit by the upstream account and endpoint a call reaches. A deployment that serves many
orgs on one account (one key the operator configured for all of them) has one limit for all of them, and inside a key
the queue knew no org: one org's burst of background or batch calls took every slot and queued every other org's
call behind all of it. Inside a class a freed slot now goes to the waiting call of the org holding fewest of the
key's slots (oldest first among equals), in the process gate and in the deployment's shared Redis gate alike; the
model gateway and the loop name the run's org when they ask. The contract cases are
``ProviderAdmissionContract.test_wp56_fair_*``; these pin the wiring and the shared gate across processes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from app.harness import provider_gate
from app.harness.assembly.model import ResolvedModel, admitted
from app.harness.provider_gate import RedisProviderGate
from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.model_gateway import ChatCompletion
from harness_core.gateway.priority_gate import upstream_key
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.testing import ManualClock
from harness_core.testing.model_gateway import (
    ROUTE,
    GatewayRig,
    RecordedTransport,
    body_of,
    org_key_route,
)
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    import pytest

    from harness_core.priority import PriorityClass

SHARED = upstream_key("openai", {"apiKey": "sk-test-shared-account-not-real"})


@dataclass
class _Slot:
    waited: bool = False

    def release(self) -> None:
        pass


@dataclass
class _Recording:
    limit: int = 1
    asked: list[tuple[str, PriorityClass | None, str | None]] = field(default_factory=list)

    async def acquire(self, key: str, priority_class: PriorityClass | None, *, org: str | None = None) -> _Slot:
        self.asked.append((key, priority_class, org))
        return _Slot()


async def test_wp56_fair_5_the_model_gateway_names_the_runs_org_when_it_asks_for_a_slot() -> None:
    gate = _Recording()
    rig = GatewayRig(gate=gate)  # type: ignore[arg-type]
    token = rig.token(rig.pin("run-a", tenant="org-a"))
    rig.resolver.add("org-a", ROUTE, replace(org_key_route(ROUTE, rig.transport), admission_key=SHARED))
    body = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "stream": False}
    assert isinstance(await rig.gateway.chat(token, {}, body_of(body)), ChatCompletion)
    assert [(k, o) for k, _, o in gate.asked] == [(SHARED, "org-a")]


async def test_wp56_fair_6_a_loop_runs_calls_name_its_org() -> None:
    gate = _Recording()
    base = ResolvedModel(
        raw_llm=None, provider="openAI", is_multimodal=False, context_length=None, is_reasoning=False,
        port=None,  # type: ignore[arg-type]
        transport=RecordedTransport(), admission_key=SHARED,
    )
    out = admitted(base, gate, "background", None, org="org-b")  # type: ignore[arg-type]
    assert isinstance(out.transport, AdmittedTransport)
    _ = await out.transport.admit()
    assert gate.asked == [(SHARED, "background", "org-b")]


async def test_wp56_fair_7_across_processes_a_freed_slot_goes_to_the_org_holding_fewest() -> None:
    clock = ManualClock()
    redis = FakeRedisConnectionProvider(is_cluster=True, key_namespace="ns")

    def process() -> RedisProviderGate:
        return RedisProviderGate(redis, 2, floor_every=0, monotonic=clock.monotonic, sleep=clock.sleep,
                                 now_ms=lambda: int(clock.monotonic() * 1000))

    async def tick(n: int = 10) -> None:
        for _ in range(n):
            _ = clock.advance(0.05)
            for _ in range(200):
                await asyncio.sleep(0)

    worker, gateway = process(), process()
    held = [await worker.acquire(SHARED, "background", org="org-a") for _ in range(2)]
    burst = [asyncio.ensure_future(worker.acquire(SHARED, "background", org="org-a")) for _ in range(5)]
    await tick()
    other = asyncio.ensure_future(gateway.acquire(SHARED, "background", org="org-b"))
    await tick()
    assert not other.done()
    held[0].release()
    await tick()
    assert other.done() and not any(w.done() for w in burst), "org b's one call went before org a's older burst"
    # Neither the queue nor the held set names an org in the clear.
    client = redis.get_client()
    members: list[object] = []
    for name in await client.keys("*"):
        text = name.decode() if isinstance(name, bytes) else str(name)
        if text.endswith((":held", ":queue")):
            members.extend(await client.zrange(name, 0, -1))
    assert members and not any("org-a" in str(m) or "org-b" in str(m) for m in members)
    other.result().release()
    held[1].release()
    for w in burst:
        _ = w.cancel()
    await tick()
    for gate in (worker, gateway):
        await gate.aclose()


def test_wp56_fair_9_the_fair_scan_is_bounded_at_256_queue_entries() -> None:
    assert provider_gate.FAIR_SCAN == 256


async def test_wp56_fair_9_a_waiter_beyond_the_fair_scan_keeps_fifo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # The lead accepted the bound (WP-56, 2026-11-02): the scan stays O(256) per ask whatever the backlog; a waiter
    # past it is served in queue order, as before fairness. Shrunk to 2 here so the third waiter is past it.
    monkeypatch.setattr(provider_gate, "FAIR_SCAN", 2)
    clock = ManualClock()
    redis = FakeRedisConnectionProvider(is_cluster=True, key_namespace="ns")
    gate = RedisProviderGate(redis, 2, floor_every=0, monotonic=clock.monotonic, sleep=clock.sleep,
                             now_ms=lambda: int(clock.monotonic() * 1000))

    async def tick(n: int = 10) -> None:
        for _ in range(n):
            _ = clock.advance(0.05)
            for _ in range(200):
                await asyncio.sleep(0)

    held = [await gate.acquire(SHARED, "background", org="org-a") for _ in range(2)]
    first = asyncio.ensure_future(gate.acquire(SHARED, "background", org="org-a"))
    await tick()
    second = asyncio.ensure_future(gate.acquire(SHARED, "background", org="org-a"))
    await tick()
    beyond = asyncio.ensure_future(gate.acquire(SHARED, "background", org="org-b"))
    await tick()
    held[0].release()
    await tick()
    assert first.done() and not second.done() and not beyond.done(), "past the scan, the oldest waiter goes first"
    first.result().release()
    held[1].release()
    await tick()
    assert beyond.done(), "once inside the scan, org b's call is chosen fairly again"
    for w in (second, beyond):
        if w.done():
            w.result().release()
        else:
            _ = w.cancel()
    await tick()
    await gate.aclose()
