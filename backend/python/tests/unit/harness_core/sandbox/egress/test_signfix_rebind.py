"""SF-7..SF-9 (SIGNFIX): a live sandbox bound again for another attempt gets that attempt's token, at once and after
a restart, and keeps its connections.

The Docker broker re-binds a kept sign-in sandbox for each message by registering its new binding over the old one
(no unbind, so the engine's tunnel to the vendor stays up). The proxy's token cache is keyed by sandbox and credential,
so the previous attempt's still-fresh token was served to the next one.
"""

from __future__ import annotations

import dataclasses
import itertools

from harness_core.engines.attempt_labels import bound_labels
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    StaticResolver,
)
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
    grant_key,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.sandbox.egress import AttemptTokenSource
from tests.unit.harness_core.sandbox.egress.test_state import auth_of, call

_STARTS = itertools.count(1)
PH_1, PH_2 = "ph-tools-1-0123456789abcdef", "ph-tools-2-0123456789abcdef"
KEPT = {"orgId": "o1", "sessionId": "s1", "pipeshub.signin.kept": "1"}


def _binding(placeholder: str, run: str, attempt: str) -> EgressBinding:
    policy = EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:tools"),),
    )
    return EgressBinding("sbx-k", "10.213.0.2", "10.213.0.3", policy, {**KEPT, **bound_labels(run, attempt)})


FIRST, SECOND = _binding(PH_1, "run-1", "run-1.a1"), _binding(PH_2, "run-2", "run-2.a1")


def _proxy(bindings: InMemoryEgressBindings, tokens: AttemptTokenSource, clock: ManualClock) -> EgressProxy:
    return EgressProxy(
        bindings=bindings, tokens=tokens, resolver=StaticResolver(), upstreams={"model-gateway": "http://gw:9"},
        clock=clock, ids=SeededIds(),
    )


async def _attempt_seen(proxy: EgressProxy, tokens: AttemptTokenSource, binding: EgressBinding) -> tuple[str, str] | None:
    [placeholder] = binding.placeholders()
    forward = await proxy.authorize(call(placeholder), local=binding.proxy_address, peer=binding.sandbox_address)
    return tokens.attempt_of(auth_of(forward.headers).removeprefix("Bearer "))


async def test_sf7_a_rebound_sandbox_gets_the_new_attempts_token_and_keeps_its_connections() -> None:
    clock = ManualClock()
    tokens = AttemptTokenSource(b"sf7", clock)
    bindings = InMemoryEgressBindings()
    closed: list[str] = []
    bindings.on_unregister(closed.append)
    proxy = _proxy(bindings, tokens, clock)
    await bindings.register(FIRST)
    assert await _attempt_seen(proxy, tokens, FIRST) == ("run-1", "run-1.a1")
    await bindings.register(SECOND)
    assert await _attempt_seen(proxy, tokens, SECOND) == ("run-2", "run-2.a1")
    assert closed == [], "a re-bind is not an unbind: the engine's tunnels stay up"


async def test_sf8_the_same_binding_registered_again_keeps_its_token() -> None:
    clock = ManualClock()
    tokens = AttemptTokenSource(b"sf8", clock)
    bindings = InMemoryEgressBindings()
    proxy = _proxy(bindings, tokens, clock)
    await bindings.register(FIRST)
    _ = await _attempt_seen(proxy, tokens, FIRST)
    await bindings.register(dataclasses.replace(FIRST))
    _ = await _attempt_seen(proxy, tokens, FIRST)
    assert len(tokens.issued) == 1


async def test_sf9_a_restart_after_a_rebind_never_restores_the_previous_attempts_token() -> None:
    clock = ManualClock()
    tokens = AttemptTokenSource(b"sf9", clock)
    store = InMemoryEgressStateStore(clock)

    async def start() -> EgressProxy:
        bindings = DurableEgressBindings(store, StateSealer(b"sf9-state"), clock)
        _ = await bindings.load(f"egress-sf9-{next(_STARTS)}")
        return _proxy(bindings, tokens, clock)

    first = await start()
    await first.bindings.register(FIRST)
    _ = await _attempt_seen(first, tokens, FIRST)
    await first.bindings.register(SECOND)
    assert grant_key("sbx-k", "gateway:tools") not in dict(await store.entries())
    second = await start()
    assert await _attempt_seen(second, tokens, SECOND) == ("run-2", "run-2.a1")
