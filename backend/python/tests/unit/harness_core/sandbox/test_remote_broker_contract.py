"""RB-1, RB-3..RB-6: the contract suite through the HTTP client and server, over the fake broker."""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.errors import (
    ERROR_TYPES,
    BrokerUnauthorized,
    ImageNotAllowed,
    InvalidSandboxRequest,
    LeaseFenced,
    SandboxError,
    SandboxRefusal,
)
from harness_core.sandbox.remote import wire
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import (
    FakeContractEnv,
    FakeEgressContractEnv,
    FakeSandboxBroker,
    SandboxBrokerContract,
)
from harness_core.testing.sandbox.contract import LEASE
from harness_core.testing.sandbox.erase_contract import SessionFolderEraserContract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOKEN = "test-broker-token"


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def fake(clock: ManualClock) -> FakeSandboxBroker:
    return FakeSandboxBroker(clock=clock)


@pytest.fixture
async def server(fake: FakeSandboxBroker) -> AsyncIterator[TestServer]:
    test_server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
    await test_server.start_server()
    yield test_server
    await test_server.close()


@pytest.fixture
async def broker(server: TestServer) -> AsyncIterator[RemoteSandboxBroker]:
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))
    yield client
    await client.aclose()


@pytest.fixture
def sandbox_env(clock: ManualClock) -> FakeContractEnv:
    return FakeContractEnv(clock)


class TestRemoteOverFake(SandboxBrokerContract):
    @pytest.fixture
    def egress_env(self, clock: ManualClock) -> FakeEgressContractEnv:
        return FakeEgressContractEnv(clock)

    @pytest.fixture
    async def egress_broker(
        self, clock: ManualClock, egress_env: FakeEgressContractEnv
    ) -> AsyncIterator[RemoteSandboxBroker]:
        fake = FakeSandboxBroker(clock=clock, egress_proxy=egress_env.proxy)
        test_server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
        await test_server.start_server()
        client = await RemoteSandboxBroker.open(str(test_server.make_url("")), StaticBearerAuth(TOKEN))
        yield client
        await client.aclose()
        await test_server.close()


class TestRemoteSessionFolderEraser(SessionFolderEraserContract):
    """E1: erase-session over HTTP, authenticated like every route, refused for unsafe ids on both sides."""

    @pytest.fixture
    async def eraser(self, clock: ManualClock) -> AsyncIterator[RemoteSandboxBroker]:
        fake = FakeSandboxBroker(clock=clock, persistent_mounts=True)
        test_server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
        await test_server.start_server()
        client = await RemoteSandboxBroker.open(str(test_server.make_url("")), StaticBearerAuth(TOKEN))
        yield client
        await client.aclose()
        await test_server.close()


async def test_erase_session_needs_the_credential_and_safe_ids_at_the_server(
    server: TestServer, fake: FakeSandboxBroker
) -> None:
    import aiohttp

    url = server.make_url(wire.ERASE_SESSION)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"orgId": "o", "sessionId": "s"}) as response:
            assert response.status == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with session.post(url, json={"orgId": "o", "sessionId": "../s"}, headers=headers) as response:
            assert response.status == 400
        async with session.post(url, json={"orgId": "o"}, headers=headers) as response:
            assert response.status == 400


async def test_a_wrong_credential_is_refused_and_nothing_is_created(
    server: TestServer, fake: FakeSandboxBroker, sandbox_env: FakeContractEnv
) -> None:
    for auth in (StaticBearerAuth("wrong"),):
        client = RemoteSandboxBroker(str(server.make_url("")), auth)
        try:
            with pytest.raises(BrokerUnauthorized):
                await client.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k")
            with pytest.raises(BrokerUnauthorized):
                await client.health()
        finally:
            await client.aclose()
    assert fake.sandboxes == {}


async def test_e2e2_fb3_opening_a_broker_that_refuses_closes_the_clients_session(
    server: TestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A worker retries the open while the broker starts (E2E2 FB-1); each failed open must not leak a session.
    sessions: list[object] = []
    real_http = RemoteSandboxBroker._http  # pyright: ignore[reportPrivateUsage]

    def recording_http(self: RemoteSandboxBroker) -> object:
        made = real_http(self)
        if made not in sessions:
            sessions.append(made)
        return made

    monkeypatch.setattr(RemoteSandboxBroker, "_http", recording_http)
    with pytest.raises(BrokerUnauthorized):
        await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth("wrong"))
    assert len(sessions) == 1
    assert getattr(sessions[0], "closed") is True


async def test_h1_ei7_the_engines_a_broker_cannot_run_cross_the_wire(
    fake: FakeSandboxBroker, broker: RemoteSandboxBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_core.sandbox.types import (
        ENGINES_UNAVAILABLE,
        BrokerHealth,
        engines_unavailable,
    )

    real = fake.health

    async def health() -> BrokerHealth:
        seen = await real()
        return BrokerHealth(ok=seen.ok, modes=seen.modes, detail={ENGINES_UNAVAILABLE: "claude_code@2.1.289"})

    monkeypatch.setattr(fake, "health", health)
    assert engines_unavailable(await broker.health()) == frozenset({"claude_code@2.1.289"})


async def test_a_request_without_a_credential_is_401(server: TestServer) -> None:
    client = server.make_url(wire.HEALTH)
    async with __import__("aiohttp").ClientSession() as session, session.get(client) as response:
        assert response.status == 401
        assert (await response.json())["error"]["code"] == "unauthorized"


async def test_liveness_needs_no_credential_and_says_nothing_else(server: TestServer) -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session, session.get(server.make_url(wire.LIVENESS)) as response:
        assert response.status == 200
        assert await response.json() == {"ok": True}


async def test_every_route_is_versioned(server: TestServer) -> None:
    import aiohttp

    routes = [r.resource.canonical for r in server.app.router.routes() if r.resource is not None]
    assert all(path.startswith("/v1/") for path in routes if path != wire.LIVENESS)
    async with aiohttp.ClientSession() as session, session.get(
        server.make_url("/v2/health"), headers={"Authorization": f"Bearer {TOKEN}"}
    ) as response:
        assert response.status == 404


@pytest.mark.parametrize("error_type", ERROR_TYPES)
def test_every_typed_error_crosses_the_wire_as_itself(error_type: type[SandboxError]) -> None:
    error = error_type("msg", reason="why") if issubclass(error_type, SandboxRefusal) else error_type("msg")
    back = wire.error_from(wire.error_body(error), status=wire.status_for(error))
    assert type(back) is error_type
    assert str(back) == "msg"
    if isinstance(back, SandboxRefusal):
        assert back.reason == "why"


async def test_refusals_keep_their_reason_over_http(broker: RemoteSandboxBroker, fake: FakeSandboxBroker, sandbox_env: FakeContractEnv) -> None:
    async def refuse(*_: object, **__: object) -> None:
        raise ImageNotAllowed("image x is not allowed", reason="image_not_allowed")

    fake.acquire = refuse  # type: ignore[method-assign]
    with pytest.raises(ImageNotAllowed) as refused:
        await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k")
    assert refused.value.reason == "image_not_allowed"


async def test_a_stale_fence_is_lease_fenced_over_http(broker: RemoteSandboxBroker, sandbox_env: FakeContractEnv) -> None:
    from harness_core.sandbox.types import LeaseRef

    handle = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("a", 3), idempotency_key="k")
    with pytest.raises(LeaseFenced):
        await broker.acquire(sandbox_env.spec(), lease=LeaseRef("b", 2), idempotency_key="k")
    await broker.release(handle)


async def test_writing_without_stdin_is_refused_client_side(broker: RemoteSandboxBroker, sandbox_env: FakeContractEnv) -> None:
    handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k")
    process = await handle.exec(sandbox_env.argv_sleep(), env={})
    with pytest.raises(InvalidSandboxRequest):
        await process.write(b"x")
    await process.signal(signal.SIGKILL)
    assert await process.wait(timeout_s=5) == 128 + signal.SIGKILL
    await broker.release(handle)


async def test_a_client_that_disconnects_mid_run_gets_its_process_killed(
    server: TestServer, broker: RemoteSandboxBroker, fake: FakeSandboxBroker, sandbox_env: FakeContractEnv
) -> None:
    import aiohttp

    handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k")
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(
            server.make_url(wire.sandbox_path(handle.id, wire.EXEC)),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        await ws.send_str(wire.control("start", argv=sandbox_env.argv_sleep(), env={}, stdin=False))
        assert wire.parse_control((await ws.receive()).data)["op"] == wire.OP_STARTED
        [state] = fake.sandboxes.values()
        [process] = state.processes
        await ws.close()
    assert await process.wait(timeout_s=5) == 128 + signal.SIGKILL
    await broker.release(handle)
