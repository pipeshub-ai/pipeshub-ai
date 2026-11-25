"""R41OPS-FU-6 (ADR-CRED-01 D6): a binding restored from sealed state written before the broker's proof is signed again.

A proxy upgraded alone kept its sealed bindings and its instance id, so no broker resynced: a record written before
D6 carries no ``attestation`` and Node refused that run's gateway token until the sandbox was bound again. A restored
binding record with no ``attestation`` key at all now changes the instance id, so every broker re-registers its
sandboxes, signed; a record written since D6 (``attestation`` named, even ``null``) keeps the instance.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

from app.harness.sandbox.binding_proof import BindingAttester
from app.harness.sandbox.docker_egress import DockerEgressWiring
from harness_core.sandbox.egress import wire
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
    binding_key,
)
from harness_core.testing.clock import ManualClock
from tests.unit.harness_core.sandbox.egress.test_state import KEY, A

if TYPE_CHECKING:
    from harness_core.sandbox.egress.types import EgressBinding


def _pre_d6_record(clock: ManualClock, binding: EgressBinding) -> str:
    """A binding record as a proxy from before D6 sealed it: the binding without the ``attestation`` field."""
    body = wire.BINDING.dump_python(binding, mode="json")
    del body["attestation"]
    now = clock.now()
    record = {
        "binding": body,
        "savedAt": int(now.timestamp() * 1000),
        "expiresAt": int((now + timedelta(hours=1)).timestamp() * 1000),
    }
    key = binding_key(binding.sandbox_id)
    return StateSealer(KEY).seal(key, json.dumps(record, separators=(",", ":"), sort_keys=True).encode())


async def _store_with(clock: ManualClock, value: str) -> InMemoryEgressStateStore:
    store = InMemoryEgressStateStore(clock)
    await store.put("instance", "egress-old", ttl_s=None)
    await store.put(binding_key(A.sandbox_id), value, ttl_s=3600)
    return store


async def test_a_binding_written_before_d6_changes_the_instance_and_is_still_served() -> None:
    clock = ManualClock()
    store = await _store_with(clock, _pre_d6_record(clock, A))
    bindings = DurableEgressBindings(store, StateSealer(KEY), clock)
    loaded = await bindings.load("egress-new")
    assert (loaded.instance, loaded.previous_instance, loaded.bindings) == ("egress-new", "egress-old", 1)
    assert bindings.get(A.sandbox_id) is not None, "egress goes on until the broker signs it again"


async def test_a_binding_written_since_d6_keeps_the_instance() -> None:
    clock = ManualClock()
    first = DurableEgressBindings(InMemoryEgressStateStore(clock), StateSealer(KEY), clock)
    await first.register(A)
    record = dict(await first._store.entries())[binding_key(A.sandbox_id)]  # pyright: ignore[reportPrivateUsage]
    store = await _store_with(clock, record)
    loaded = await DurableEgressBindings(store, StateSealer(KEY), clock).load("egress-new")
    assert loaded.instance == "egress-old"


class _Proxy:
    """The control plane as the broker's resync reaches it, over the restarted proxy's own table."""

    def __init__(self, bindings: DurableEgressBindings, instance: str) -> None:
        self.table = bindings
        self.instance = instance

    async def health(self) -> object:
        return type("Health", (), {"instance": self.instance})()

    async def register(self, binding: EgressBinding) -> None:
        await self.table.register(binding)

    async def bound(self) -> set[str]:
        return {b.sandbox_id for b in self.table.held()}

    async def unregister(self, sandbox_id: str) -> None:
        await self.table.unregister(sandbox_id)


async def test_the_broker_resync_signs_the_restored_binding() -> None:
    clock = ManualClock()
    store = await _store_with(clock, _pre_d6_record(clock, A))
    table = DurableEgressBindings(store, StateSealer(KEY), clock)
    loaded = await table.load("egress-new")
    attester = BindingAttester(b"broker-binding-key-0123456789abcdef")
    wiring = DockerEgressWiring(
        provider=None,  # type: ignore[arg-type]
        clock=clock,
        proxy_container="egress-proxy",
        bindings=_Proxy(table, loaded.instance),  # type: ignore[arg-type]
        pool=None,  # type: ignore[arg-type]
        network_prefix="pxh-",
        attester=attester,
    )
    wiring._held[A.sandbox_id] = attester.attest(A)  # pyright: ignore[reportPrivateUsage]
    wiring._proxy_instance = "egress-old"  # pyright: ignore[reportPrivateUsage]
    assert table.get(A.sandbox_id).attestation is None  # type: ignore[union-attr]
    assert await wiring.resync()
    restored = table.get(A.sandbox_id)
    assert restored is not None
    assert restored.attestation == attester.attest(A).attestation
    assert restored.attestation is not None
