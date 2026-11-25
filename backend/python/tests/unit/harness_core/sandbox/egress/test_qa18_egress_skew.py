"""QA18-5 (VS-7): the egress proxy and the sandbox broker one release apart, in both orders.

The broker registers bindings over the proxy's control plane, and the proxy restores them from its durable state after
a restart, possibly onto the other release's image. A binding field this proxy does not know is a rule it cannot
enforce: refused over HTTP (the broker learns at once), and on restore treated as unverifiable (the instance changes,
so the brokers that hold the sandbox register it again). A binding an older broker or proxy wrote, without fields added
since, is restored and served.
"""

from __future__ import annotations

import itertools
import json
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
    wire,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
    binding_key,
)
from harness_core.sandbox.errors import InvalidSandboxRequest
from harness_core.sandbox.remote.wire import error_from
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

CONTROL = "qa18-control"
KEY = b"qa18-state-key"
PH = "ph-model-0123456789abcdef"
POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)),),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
    forbidden_patterns=("SECRET",),
)
BINDING = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, {"runId": "r1"})
WHERE = ["binding", "policy", "destination", "token_route"]
_STARTS = itertools.count(1)


def _dumped() -> dict[str, Any]:
    return wire.BINDING.dump_python(BINDING, mode="json")


def _with_newer_field(where: str) -> dict[str, Any]:
    body = _dumped()
    target = {
        "binding": body,
        "policy": body["policy"],
        "destination": body["policy"]["destinations"][0],
        "token_route": body["policy"]["token_routes"][0],
    }[where]
    target["added_in_n_plus_1"] = "deny-more"
    return body


def _proxy(bindings: InMemoryEgressBindings, clock: ManualClock) -> EgressProxy:
    return EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"qa18-token-secret", clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": "http://gw:9"},
        clock=clock,
        ids=SeededIds(),
    )


def _call() -> ProxyRequest:
    return ProxyRequest("POST", "/v1/chat", (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {PH}")), b"{}")


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def control(clock: ManualClock) -> AsyncIterator[tuple[EgressServer, str]]:
    server = EgressServer(_proxy(InMemoryEgressBindings(), clock), StaticBearerAuth(CONTROL), instance="qa18")
    _, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    yield server, f"http://127.0.0.1:{control_port}"
    await server.stop()


async def _put(base: str, body: dict[str, Any]) -> tuple[int, Any]:
    async with aiohttp.ClientSession() as session, session.put(
        f"{base}{wire.binding_path(body['sandbox_id'])}", json=body, headers={"Authorization": f"Bearer {CONTROL}"}
    ) as response:
        return response.status, await response.json() if response.content_type == "application/json" else None


@pytest.mark.parametrize("where", WHERE)
async def test_qa18_5_a_newer_brokers_binding_field_is_refused_over_http_never_dropped(
    control: tuple[EgressServer, str], where: str
) -> None:
    server, base = control
    status, body = await _put(base, _with_newer_field(where))
    assert status == 400, body
    assert isinstance(error_from(body, status=status), InvalidSandboxRequest)
    assert await server.proxy.bindings.bound() == frozenset()


async def test_qa18_5_the_same_binding_without_the_newer_field_is_bound(control: tuple[EgressServer, str]) -> None:
    server, base = control
    status, _ = await _put(base, _dumped())
    assert status == 204
    assert await server.proxy.bindings.bound() == {"sbx-a"}


async def _seal_record(store: InMemoryEgressStateStore, clock: ManualClock, binding: dict[str, Any]) -> None:
    """A record exactly as another release's proxy writes it (``DurableEgressBindings._put_binding``)."""
    now_ms = int(clock.now().timestamp() * 1000)
    key = binding_key(binding["sandbox_id"])
    body = {"binding": binding, "savedAt": now_ms, "expiresAt": now_ms + 3_600_000}
    sealed = StateSealer(KEY).seal(key, json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    await store.put(key, sealed, ttl_s=3600)


async def _start(store: InMemoryEgressStateStore, clock: ManualClock) -> tuple[EgressProxy, str, int]:
    bindings = DurableEgressBindings(store, StateSealer(KEY), clock)
    loaded = await bindings.load(f"egress-{next(_STARTS)}")
    return _proxy(bindings, clock), loaded.instance, loaded.unverified


@pytest.mark.parametrize("where", WHERE)
async def test_qa18_5_a_proxy_rolled_back_under_a_newer_record_does_not_serve_it_and_changes_instance(
    clock: ManualClock, where: str
) -> None:
    store = InMemoryEgressStateStore(clock)
    first, instance, _ = await _start(store, clock)
    await first.bindings.register(EgressBinding("sbx-b", "10.213.0.10", "10.213.0.11", EgressPolicy(), {}))
    await _seal_record(store, clock, _with_newer_field(where))

    older, restarted_as, unverified = await _start(store, clock)

    assert unverified == 1
    assert await older.bindings.bound() == {"sbx-b"}
    with pytest.raises(EgressRefused) as refused:
        _ = await older.authorize(_call(), local=BINDING.proxy_address, peer=BINDING.sandbox_address)
    assert refused.value.reason == EgressRefusalReason.UNBOUND_SOURCE
    assert restarted_as != instance, "the brokers holding sbx-a must register it again"


async def test_qa18_5_a_binding_an_older_release_wrote_is_restored_with_the_defaults_of_fields_added_since(
    clock: ManualClock,
) -> None:
    store = InMemoryEgressStateStore(clock)
    older_shape = _dumped()
    del older_shape["labels"]
    del older_shape["policy"]["forbidden_patterns"]
    await _seal_record(store, clock, older_shape)

    newer, _, unverified = await _start(store, clock)

    assert unverified == 0
    assert await newer.bindings.bound() == {"sbx-a"}
    allowed = await newer.authorize(_call(), local=BINDING.proxy_address, peer=BINDING.sandbox_address)
    assert ("Authorization", f"Bearer {PH}") not in allowed.headers


async def test_qa18_5_a_restart_onto_the_same_record_format_keeps_every_binding(clock: ManualClock) -> None:
    store = InMemoryEgressStateStore(clock)
    first, instance, _ = await _start(store, clock)
    await first.bindings.register(BINDING)

    again, restarted_as, unverified = await _start(store, clock)

    assert (unverified, restarted_as) == (0, instance)
    assert await again.bindings.bound() == {"sbx-a"}
    _ = await again.authorize(_call(), local=BINDING.proxy_address, peer=BINDING.sandbox_address)
