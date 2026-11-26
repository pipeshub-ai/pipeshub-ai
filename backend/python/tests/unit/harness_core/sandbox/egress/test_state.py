"""DS-1..DS-10: the proxy's bindings and token grants survive a restart; what cannot be verified does not (P5.4b)."""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from typing import override

import pytest

from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.state import (
    DEFAULT_BINDING_TTL_S,
    INSTANCE_KEY,
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
    binding_key,
    grant_key,
)
from harness_core.sandbox.errors import BrokerUnavailable
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

R = EgressRefusalReason
SECRET = b"ds-token-secret"
KEY = b"ds-state-key"
_STARTS = itertools.count(1)
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}


def policy(placeholder: str) -> EgressPolicy:
    return EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:model"),),
    )


PH_A, PH_B = "ph-model-a-0123456789abcdef", "ph-model-b-0123456789abcdef"
A = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", policy(PH_A), LABELS)
B = EgressBinding("sbx-b", "10.213.0.10", "10.213.0.11", policy(PH_B), {**LABELS, "runId": "r2"})


def call(placeholder: str, *, extra: tuple[tuple[str, str], ...] = ()) -> ProxyRequest:
    return ProxyRequest(
        "POST", "/v1/chat", (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {placeholder}"), *extra), b"{}"
    )


@dataclass
class FlakyStore(InMemoryEgressStateStore):
    fail_put: bool = False
    fail_delete: bool = False
    puts: list[str] = field(default_factory=list[str])

    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self.fail_put = self.fail_delete = False
        self.puts = []

    @override
    async def put(self, key: str, value: str, *, ttl_s: int | None) -> None:
        if self.fail_put:
            raise ConnectionError("store down")
        self.puts.append(key)
        await super().put(key, value, ttl_s=ttl_s)

    @override
    async def delete(self, key: str) -> None:
        if self.fail_delete:
            raise ConnectionError("store down")
        await super().delete(key)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def store(clock: ManualClock) -> FlakyStore:
    return FlakyStore(clock)


@pytest.fixture
def tokens(clock: ManualClock) -> InMemoryTokenSource:
    return InMemoryTokenSource(SECRET, clock, ttl_s=600)


async def start(store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource, *, key: bytes = KEY) -> tuple[EgressProxy, str]:
    """One proxy process: load the state, then build the proxy over it (as the process roles do)."""
    bindings = DurableEgressBindings(store, StateSealer(key), clock)
    loaded = await bindings.load(f"egress-{next(_STARTS)}")
    proxy = EgressProxy(bindings=bindings, tokens=tokens, resolver=StaticResolver(), upstreams={"model-gateway": "http://gw:9"},
                        clock=clock, ids=SeededIds())
    return proxy, loaded.instance


def auth_of(headers: tuple[tuple[str, str], ...]) -> str:
    return next(v for k, v in headers if k == "Authorization")


async def refused(proxy: EgressProxy, request: ProxyRequest, binding: EgressBinding) -> R:
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize(request, local=binding.proxy_address, peer=binding.sandbox_address)
    return caught.value.reason


async def test_ds1_a_restarted_proxy_serves_the_same_sandbox_with_the_same_token(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    first, _ = await start(store, clock, tokens)
    await first.bindings.register(A)
    await first.bindings.register(B)
    before = await first.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    assert tokens.issued == 1

    second, _ = await start(store, clock, tokens)
    after = await second.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    assert auth_of(after.headers) == auth_of(before.headers)
    assert tokens.issued == 1, "the restarted proxy reused the stored grant"
    token = auth_of(before.headers).removeprefix("Bearer ")
    assert await refused(second, call(PH_B, extra=(("X-Leak", token),)), B) == R.TOKEN_REPLAYED


async def test_ds2_an_unregistered_sandbox_stays_gone_after_a_restart(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    first, _ = await start(store, clock, tokens)
    await first.bindings.register(A)
    _ = await first.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    await first.bindings.unregister("sbx-a")
    assert dict(await store.entries()).keys() == {INSTANCE_KEY}
    second, _ = await start(store, clock, tokens)
    assert await refused(second, call(PH_A), A) == R.UNBOUND_SOURCE


def _tamper(value: str) -> str:
    version, _, body = value.partition(".")
    flipped = "A" if body[10] != "A" else "B"
    return f"{version}.{body[:10]}{flipped}{body[11:]}"


@pytest.mark.parametrize(
    ("case", "reason"),
    [("other_key", "seal_mismatch"), ("tampered", "seal_mismatch"), ("not_sealed", "unknown_format"), ("moved", "seal_mismatch")],
)
async def test_ds3_a_record_the_proxy_cannot_verify_is_refused_and_logged(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource, caplog: pytest.LogCaptureFixture, case: str, reason: str
) -> None:
    first, instance = await start(store, clock, tokens)
    await first.bindings.register(A)
    await first.bindings.register(B)
    held = dict(await store.entries())
    if case == "other_key":
        forged = DurableEgressBindings(InMemoryEgressStateStore(clock), StateSealer(b"another-key"), clock)
        await forged.register(A)
        await store.put(binding_key("sbx-a"), dict(await forged._store.entries())[binding_key("sbx-a")], ttl_s=60)  # pyright: ignore[reportPrivateUsage]
    elif case == "tampered":
        await store.put(binding_key("sbx-a"), _tamper(held[binding_key("sbx-a")]), ttl_s=60)
    elif case == "not_sealed":
        await store.put(binding_key("sbx-a"), json.dumps({"binding": {}}), ttl_s=60)
    else:
        # B's genuine record under A's key: the associated data does not match.
        await store.put(binding_key("sbx-a"), held[binding_key("sbx-b")], ttl_s=60)
    with caplog.at_level(logging.INFO, logger="harness_core.sandbox.egress"):
        second, rotated = await start(store, clock, tokens)
    records = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.state_unverified"]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert records[0].harness["reason"] == reason  # type: ignore[attr-defined]
    assert await refused(second, call(PH_A), A) == R.UNBOUND_SOURCE
    _ = await second.authorize(call(PH_B), local=B.proxy_address, peer=B.sandbox_address)
    assert rotated != instance, "brokers must re-register what they hold"
    assert binding_key("sbx-a") not in dict(await store.entries())


async def test_ds4_records_past_their_own_expiry_are_not_trusted(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    first, _ = await start(store, clock, tokens)
    await first.bindings.register(A)
    _ = await first.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    sealed = dict(await store.entries())
    # A store that kept the records past their TTL (no expiry honoured).
    _ = clock.advance(DEFAULT_BINDING_TTL_S + 1)
    for key, value in sealed.items():
        await store.put(key, value, ttl_s=None)
    second, _ = await start(store, clock, tokens)
    assert await refused(second, call(PH_A), A) == R.UNBOUND_SOURCE


async def test_ds4_an_expired_grant_is_reissued(store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource) -> None:
    first, _ = await start(store, clock, tokens)
    await first.bindings.register(A)
    _ = await first.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    grant = dict(await store.entries())[grant_key("sbx-a", "gateway:model")]
    _ = clock.advance(601)
    await store.put(grant_key("sbx-a", "gateway:model"), grant, ttl_s=None)
    second, _ = await start(store, clock, tokens)
    forward = await second.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    assert auth_of(forward.headers) == "Bearer " + InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 1)


async def test_ds5_a_store_that_refuses_the_write_refuses_the_binding(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource, caplog: pytest.LogCaptureFixture
) -> None:
    proxy, _ = await start(store, clock, tokens)
    store.fail_put = True
    with pytest.raises(BrokerUnavailable):
        await proxy.bindings.register(A)
    assert await proxy.bindings.bound() == frozenset()
    assert await refused(proxy, call(PH_A), A) == R.UNBOUND_SOURCE
    store.fail_put = False
    await proxy.bindings.register(A)
    store.fail_delete = True
    with caplog.at_level(logging.WARNING, logger="harness_core.sandbox.egress"):
        await proxy.bindings.unregister("sbx-a")
    assert await refused(proxy, call(PH_A), A) == R.UNBOUND_SOURCE
    assert any(getattr(r, "harness_event", None) == "egress.state_write_failed" for r in caplog.records)


async def test_ds6_the_instance_survives_a_clean_restart_only(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    bindings = DurableEgressBindings(store, StateSealer(KEY), clock)
    first = await bindings.load("egress-1")
    await bindings.register(A)
    again = await DurableEgressBindings(store, StateSealer(KEY), clock).load("egress-2")
    assert (first.instance, again.instance, again.bindings) == ("egress-1", "egress-1", 1)
    await store.put(binding_key("sbx-x"), "v1.garbage", ttl_s=60)
    rotated = await DurableEgressBindings(store, StateSealer(KEY), clock).load("egress-3")
    assert (rotated.instance, rotated.previous_instance, rotated.unverified) == ("egress-3", "egress-1", 1)
    empty = await DurableEgressBindings(InMemoryEgressStateStore(clock), StateSealer(KEY), clock).load("egress-4")
    assert (empty.instance, empty.previous_instance) == ("egress-4", None)


async def test_ds7_bindings_carry_a_ttl_and_are_renewed_while_held(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    proxy, _ = await start(store, clock, tokens)
    bindings = proxy.bindings
    assert isinstance(bindings, DurableEgressBindings)
    await bindings.register(A)
    _ = await proxy.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    _ = clock.advance(601)
    assert grant_key("sbx-a", "gateway:model") not in dict(await store.entries()), "a grant lives as long as its token"
    _ = clock.advance(DEFAULT_BINDING_TTL_S - 1000)
    assert await bindings.renew() == 1
    _ = clock.advance(1000)
    assert binding_key("sbx-a") in dict(await store.entries())
    _ = clock.advance(DEFAULT_BINDING_TTL_S)
    assert binding_key("sbx-a") not in dict(await store.entries())


async def test_ds8_a_grant_the_store_refuses_is_still_used(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource, caplog: pytest.LogCaptureFixture
) -> None:
    proxy, _ = await start(store, clock, tokens)
    await proxy.bindings.register(A)
    store.fail_put = True
    with caplog.at_level(logging.WARNING, logger="harness_core.sandbox.egress"):
        forward = await proxy.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    assert auth_of(forward.headers) == "Bearer " + InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    failed = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.state_write_failed"]
    assert len(failed) == 1 and failed[0].harness["kind"] == "grant"  # type: ignore[attr-defined]


async def test_ds9_the_store_holds_no_token_or_placeholder_in_clear(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    proxy, _ = await start(store, clock, tokens)
    await proxy.bindings.register(A)
    forward = await proxy.authorize(call(PH_A), local=A.proxy_address, peer=A.sandbox_address)
    token = auth_of(forward.headers).removeprefix("Bearer ")
    stored = "\n".join(f"{k}={v}" for k, v in await store.entries())
    for secret in (token, PH_A, "gateway:model", "10.213.0.3", "sessionId"):
        assert secret not in stored


async def test_ds10_of_two_bindings_on_one_address_the_newer_is_restored(
    store: FlakyStore, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    older = DurableEgressBindings(store, StateSealer(KEY), clock)
    _ = await older.load("egress-1")
    await older.register(A)
    # A second proxy process wrote a newer binding on the same address before A's unregister landed.
    _ = clock.advance(5)
    newer = DurableEgressBindings(InMemoryEgressStateStore(clock), StateSealer(KEY), clock)
    moved = EgressBinding("sbx-c", A.proxy_address, A.sandbox_address, policy("ph-model-c-0123456789abcdef"), LABELS)
    await newer.register(moved)
    record = dict(await newer._store.entries())[binding_key("sbx-c")]  # pyright: ignore[reportPrivateUsage]
    await store.put(binding_key("sbx-c"), record, ttl_s=60)
    restored = DurableEgressBindings(store, StateSealer(KEY), clock)
    loaded = await restored.load("egress-2")
    assert await restored.bound() == {"sbx-c"} and loaded.unverified == 0
    assert binding_key("sbx-a") not in dict(await store.entries())
