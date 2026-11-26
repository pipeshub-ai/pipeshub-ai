"""R31GVISO-FU-3: whether a sandbox's memory limit ended it reaches the worker through the broker's HTTP API, and a
broker without the route (older than R32-SECCOMP) reads as "cannot tell", never as an error."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.remote import wire
from harness_core.sandbox.remote.client import RemoteSandboxBroker, RemoteSandboxHandle
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOKEN = "test-broker-token"
SPEC = SandboxSpec(image_digest="engine@sha256:" + "0" * 64, mode=RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/w"),))


@pytest.fixture
async def served() -> AsyncIterator[tuple[FakeSandboxBroker, RemoteSandboxBroker]]:
    fake = FakeSandboxBroker(clock=ManualClock())
    server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
    await server.start_server()
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))
    yield fake, client
    await client.aclose()
    await server.close()


async def test_rm_1_the_remote_handle_reports_what_the_broker_found(
    served: tuple[FakeSandboxBroker, RemoteSandboxBroker],
) -> None:
    fake, remote = served
    handle = await remote.acquire(SPEC, lease=LeaseRef("w", 1), idempotency_key="k")
    try:
        assert await handle.memory_exhausted() is False
        [state] = fake.sandboxes.values()
        state.out_of_memory = True
        assert await handle.memory_exhausted() is True
    finally:
        await remote.release(handle)


async def test_rm_2_a_released_sandbox_or_a_broker_without_the_route_cannot_tell(
    served: tuple[FakeSandboxBroker, RemoteSandboxBroker],
) -> None:
    _, remote = served
    assert await RemoteSandboxHandle(remote, "sbx-released", RuntimeMode.GVISOR).memory_exhausted() is False

    async def modes(_: web.Request) -> web.Response:
        return web.json_response(wire.modes_body(frozenset({RuntimeMode.GVISOR})))

    older = web.Application()
    _ = older.router.add_get(wire.MODES, modes)
    server = TestServer(older)
    await server.start_server()
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))
    try:
        assert await RemoteSandboxHandle(client, "sbx-old", RuntimeMode.GVISOR).memory_exhausted() is False
    finally:
        await client.aclose()
        await server.close()
