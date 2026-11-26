"""WP-14 (C6.14, SEC-CR-03, J-19): an incident stop ends live sandbox egress, and what it ended stays ended.

The kill switch (``AGENT_HARNESS_KILL_SWITCH`` on the proxy) refuses every binding that serves a harness run, and the
control plane's revocation (one org, one session, or every run) unbinds the live ones now: their tokens, their open
streams, and any later registration of the same sandbox (a broker's resync after a restart) are refused. A sandbox no
run attempt names (the standard chat's code sandbox, where the stop sends new chats) keeps its egress.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import aiohttp
import pytest
from aiohttp import web

from app.harness.sandbox.docker_egress import DockerEgressWiring
from harness_core.engines.attempt_labels import bound_labels
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.addresses import DEFAULT_SUBNET_POOL, SubnetPool
from harness_core.sandbox.egress.bindings import (
    EGRESS_REVOKED,
    EVERY_RUN,
    KILL_SWITCH,
    Revocation,
)
from harness_core.sandbox.egress.client import EgressProxyHealth, RemoteEgressBindings
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
    binding_key,
    revoked_key,
)
from harness_core.sandbox.errors import BrokerUnauthorized, SandboxRefusal
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

SECRET = b"wp14-secret"
KEY = b"wp14-state-key"
CONTROL = "wp14-control"


def policy(placeholder: str) -> EgressPolicy:
    return EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:model"),),
    )


def run(org: str, session: str, run_id: str) -> dict[str, str]:
    return {"orgId": org, "sessionId": session, "runId": run_id, "attemptId": f"{run_id}-a1"}


def binding(sandbox_id: str, n: int, labels: Mapping[str, str]) -> EgressBinding:
    return EgressBinding(sandbox_id, f"10.213.0.{4 * n + 2}", f"10.213.0.{4 * n + 3}", policy(f"ph-{sandbox_id}-0123456789ab"), labels)


A1 = binding("sbx-a1", 0, run("org-a", "ses-1", "r1"))
A2 = binding("sbx-a2", 1, run("org-a", "ses-2", "r2"))
B1 = binding("sbx-b1", 2, run("org-b", "ses-3", "r3"))
# A conversation's kept sign-in sandbox names its attempt under the bound keys (SIGNFIX).
KEPT = binding("sbx-kept", 3, {"orgId": "org-a", "sessionId": "ses-1", **bound_labels("r4", "r4-a1")})
# The standard chat's code sandbox: no run attempt, so no stop of the harness covers it.
LEGACY = binding("sbx-legacy", 4, {"legacyExecutor": "run", "orgId": "org-a"})
ALL = (A1, A2, B1, KEPT, LEGACY)


def call(placeholder: str) -> ProxyRequest:
    return ProxyRequest("POST", "/v1/chat", (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {placeholder}")), b"{}")


def proxy_over(bindings: InMemoryEgressBindings, clock: ManualClock, upstream: str = "http://gw") -> EgressProxy:
    return EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": upstream},
        clock=clock,
        ids=SeededIds(),
    )


async def bound_all(bindings: InMemoryEgressBindings) -> None:
    for item in ALL:
        await bindings.register(item)


async def refused_reason(bindings: InMemoryEgressBindings, item: EgressBinding) -> str:
    with pytest.raises(SandboxRefusal) as caught:
        await bindings.register(item)
    return caught.value.reason


@pytest.mark.parametrize(
    ("revocation", "revoked"),
    [
        (Revocation(org_id="org-a"), {"sbx-a1", "sbx-a2", "sbx-kept"}),
        (Revocation(org_id="org-a", session_id="ses-1"), {"sbx-a1", "sbx-kept"}),
        (Revocation(session_id="ses-3"), {"sbx-b1"}),
        (EVERY_RUN, {"sbx-a1", "sbx-a2", "sbx-b1", "sbx-kept"}),
        (Revocation(org_id="org-none"), set()),
    ],
    ids=["org", "session", "session-only", "every-run", "nothing"],
)
async def test_sec_cr_03_a_revocation_unbinds_exactly_the_runs_it_names(revocation: Revocation, revoked: set[str]) -> None:
    bindings = InMemoryEgressBindings()
    await bound_all(bindings)
    assert set(await bindings.revoke(revocation)) == revoked
    assert await bindings.bound() == {item.sandbox_id for item in ALL} - revoked
    assert "sbx-legacy" in await bindings.bound()


async def test_sec_cr_03_a_revoked_sandbox_loses_its_tokens_now_and_is_never_bound_again() -> None:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    proxy = proxy_over(bindings, clock)
    await bound_all(bindings)
    forward = await proxy.authorize(call(next(iter(A1.placeholders()))), local=A1.proxy_address, peer=A1.sandbox_address)
    token = InMemoryTokenSource.derive(SECRET, "sbx-a1", "gateway:model", 0)
    assert token in proxy.live_tokens() and forward.sandbox_id == "sbx-a1"
    assert await bindings.revoke(Revocation(org_id="org-a", session_id="ses-1")) == ("sbx-a1", "sbx-kept")
    assert token not in proxy.live_tokens()
    with pytest.raises(Exception) as caught:
        _ = await proxy.authorize(call(next(iter(A1.placeholders()))), local=A1.proxy_address, peer=A1.sandbox_address)
    assert getattr(caught.value, "reason", None) == "unbound_source"
    # A broker's resync (or anyone) registering it again is refused; a fresh sandbox of the same session is not.
    assert await refused_reason(bindings, A1) == EGRESS_REVOKED
    fresh = binding("sbx-a1-next", 6, run("org-a", "ses-1", "r5"))
    await bindings.register(fresh)
    assert "sbx-a1-next" in await bindings.bound()


async def test_j_19_the_kill_switch_binds_no_sandbox_serving_a_run_but_the_standard_chat_keeps_its_own() -> None:
    bindings = InMemoryEgressBindings(kill_switch=True)
    for item in (A1, B1, KEPT):
        assert await refused_reason(bindings, item) == KILL_SWITCH
    await bindings.register(LEGACY)
    assert await bindings.bound() == {"sbx-legacy"}


async def test_sec_cr_03_a_revocation_survives_a_proxy_restart_and_the_kill_switch_drops_restored_runs() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    first = DurableEgressBindings(store, StateSealer(KEY), clock)
    _ = await first.load("egress-1")
    await bound_all(first)
    assert await first.revoke(Revocation(org_id="org-b")) == ("sbx-b1",)
    keys = {key for key, _ in await store.entries()}
    assert revoked_key("sbx-b1") in keys and binding_key("sbx-b1") not in keys

    second = DurableEgressBindings(store, StateSealer(KEY), clock)
    _ = await second.load("egress-2")
    assert "sbx-b1" not in await second.bound()
    assert await refused_reason(second, B1) == EGRESS_REVOKED

    stopped = DurableEgressBindings(store, StateSealer(KEY), clock, kill_switch=True)
    loaded = await stopped.load("egress-3")
    assert await stopped.bound() == {"sbx-legacy"}
    assert loaded.bindings == 1
    keys = {key for key, _ in await store.entries()}
    assert {binding_key(i) for i in ("sbx-a1", "sbx-a2", "sbx-kept")}.isdisjoint(keys)
    assert binding_key("sbx-legacy") in keys


async def test_sec_cr_03_a_revocation_record_that_does_not_open_revokes_nothing() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    await store.put(revoked_key("sbx-a1"), "v1.forged", ttl_s=60)
    bindings = DurableEgressBindings(store, StateSealer(KEY), clock)
    loaded = await bindings.load("egress-1")
    assert loaded.unverified == 1 and bindings.revoked() == frozenset()


# The control plane, over HTTP on loopback.


class SlowUpstream:
    """Streams one event, then holds the response open: a model answer still being generated."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finish = asyncio.Event()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        _ = await response.prepare(request)
        await response.write(b"data: first\n\n")
        self.started.set()
        with contextlib.suppress(ConnectionError):
            _ = await asyncio.wait_for(self.finish.wait(), 30)
            await response.write_eof()
        return response


@pytest.fixture
async def served() -> AsyncIterator[tuple[EgressServer, int, int, SlowUpstream]]:
    upstream = SlowUpstream()
    app = web.Application()
    _ = app.router.add_route("*", "/{tail:.*}", upstream.handle)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    up_port = runner.addresses[0][1]
    proxy = proxy_over(InMemoryEgressBindings(), ManualClock(), f"http://127.0.0.1:{up_port}")
    server = EgressServer(proxy, StaticBearerAuth(CONTROL), instance="egress-wp14")
    # The sandbox here is on 127.0.0.1, so the control plane listens on another loopback address, as it does in a deployment.
    data_port, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0, control_host="127.0.0.2")
    yield server, data_port, control_port, upstream
    upstream.finish.set()
    await server.stop()
    await runner.cleanup()


async def test_sec_cr_03_revoking_cuts_a_stream_in_flight_and_the_next_call_is_refused(
    served: tuple[EgressServer, int, int, SlowUpstream],
) -> None:
    server, data_port, control_port, upstream = served
    live = EgressBinding("sbx-live", "127.0.0.1", "127.0.0.1", policy("ph-live-0123456789abcdef"), run("org-a", "ses-1", "r1"))
    await server.proxy.bindings.register(live)
    control = RemoteEgressBindings(f"http://127.0.0.2:{control_port}", StaticBearerAuth(CONTROL))
    headers = {"Host": "model-gateway:8097", "Authorization": "Bearer ph-live-0123456789abcdef"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.post(f"http://127.0.0.1:{data_port}/v1/chat", headers=headers, data=b"{}") as response:
                assert response.status == 200
                assert await response.content.readline() == b"data: first\n"
                await upstream.started.wait()
                assert server.in_flight.count("sbx-live") == 1
                assert await control.revoke(Revocation(org_id="org-a")) == ("sbx-live",)
                with pytest.raises(aiohttp.ClientPayloadError):
                    _ = await asyncio.wait_for(response.content.read(), 5)
            assert server.in_flight.count("sbx-live") == 0
            async with session.post(f"http://127.0.0.1:{data_port}/v1/chat", headers=headers, data=b"{}") as again:
                assert (again.status, again.headers.get("X-Egress-Refusal")) == (403, "unbound_source")
        with pytest.raises(SandboxRefusal) as caught:
            await control.register(live)
        assert caught.value.reason == EGRESS_REVOKED
    finally:
        await control.aclose()


async def test_sec_cr_03_the_revocation_route_needs_the_control_credential_and_a_strict_body(
    served: tuple[EgressServer, int, int, SlowUpstream],
) -> None:
    server, _, control_port, _ = served
    await server.proxy.bindings.register(A1)
    url = f"http://127.0.0.2:{control_port}"
    stranger = RemoteEgressBindings(url, StaticBearerAuth("not-the-credential"))
    control = RemoteEgressBindings(url, StaticBearerAuth(CONTROL))
    try:
        with pytest.raises(BrokerUnauthorized):
            _ = await stranger.revoke(EVERY_RUN)
        assert await server.proxy.bindings.bound() == {"sbx-a1"}
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {CONTROL}"}) as session:
            for body in ({"orgId": ""}, {"orgId": 7}, {"runId": "r1"}, ["org-a"]):
                async with session.post(f"{url}/v1/revocations", json=body) as response:
                    assert response.status == 400, body
        assert await server.proxy.bindings.bound() == {"sbx-a1"}
        assert await control.revoke(EVERY_RUN) == ("sbx-a1",)
        assert await control.health() == EgressProxyHealth(ok=True, instance="egress-wp14", bindings=0, kill_switch=False)
    finally:
        await stranger.aclose()
        await control.aclose()


# The broker's side: a resync after a proxy restart gives back every binding but the ones a stop ended.


class RefusingBindings:
    def __init__(self, refused: Mapping[str, str]) -> None:
        self.refused = dict(refused)
        self.registered: list[str] = []

    async def health(self) -> EgressProxyHealth:
        return EgressProxyHealth(ok=True, instance="egress-after-restart", bindings=0)

    async def register(self, item: EgressBinding) -> None:
        if item.sandbox_id in self.refused:
            raise SandboxRefusal("refused", reason=self.refused[item.sandbox_id])
        self.registered.append(item.sandbox_id)

    async def bound(self) -> frozenset[str]:
        return frozenset(self.registered)

    async def unregister(self, sandbox_id: str) -> None:
        raise AssertionError(f"nothing is stale: {sandbox_id}")


def wiring_over(fake: RefusingBindings) -> DockerEgressWiring:
    return DockerEgressWiring(
        provider=None,  # type: ignore[arg-type]
        clock=ManualClock(),
        proxy_container="pxh-unused",
        bindings=fake,  # type: ignore[arg-type]
        pool=SubnetPool(DEFAULT_SUBNET_POOL),
        network_prefix="pxh-unused-",
    )


@pytest.mark.parametrize("reason", [EGRESS_REVOKED, KILL_SWITCH])
async def test_sec_cr_03_a_resync_skips_a_revoked_binding_and_gives_back_the_rest(reason: str) -> None:
    fake = RefusingBindings({"sbx-a1": reason})
    wiring = wiring_over(fake)
    wiring._held.update({item.sandbox_id: item for item in (A1, A2, LEGACY)})
    assert await wiring.resync() is True
    assert fake.registered == ["sbx-a2", "sbx-legacy"]
    assert "sbx-a1" not in wiring._held


async def test_sec_cr_03_a_resync_still_fails_on_any_other_refusal() -> None:
    fake = RefusingBindings({"sbx-a1": "placeholder_in_use"})
    wiring = wiring_over(fake)
    wiring._held.update({"sbx-a1": A1})
    with pytest.raises(SandboxRefusal):
        _ = await wiring.resync()

