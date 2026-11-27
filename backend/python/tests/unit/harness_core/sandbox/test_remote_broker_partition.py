"""FM-11 / FB-9 (CH-12): a silently partitioned worker-to-broker link surfaces as ``BrokerUnavailable``.

P9's FM-11 held every byte of the link for two minutes: the exec stream, the port tunnel and the HTTP calls each
hung with no error, until a cleanup wait timed out with a bare ``TimeoutError`` and the kernel released the attempt
``engine_kernel_error``. Here the same silent forwarder sits between the client and the broker server.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.errors import BrokerUnavailable
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.contract import ENGINE_IMAGE, ENGINE_MOUNTS
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.testing.sandbox.egress import FakeEgress
from harness_core.testing.sandbox.fake import FakeSandboxHandle
from tests.unit.harness.engines.durable_stack import NodeOutage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeStream

TOKEN = "fm11-token"
PORT = 4096
# Each case would hang for good on the old client: this bounds how long a lost link may take to be reported.
REPORTED_WITHIN_S = 5.0


async def _forever(_ctx: FakeProcessContext) -> int:
    await asyncio.sleep(3600)
    return 0


class Rig:
    def __init__(self, fake: FakeSandboxBroker, link: NodeOutage, broker: RemoteSandboxBroker) -> None:
        self.fake = fake
        self.link = link
        self.broker = broker


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    fake = FakeSandboxBroker(
        clock=ManualClock(), egress=FakeEgress(), modes=frozenset(RuntimeMode), verified_binaries={ENGINE_IMAGE: {}}
    )
    fake.programs["forever"] = _forever
    server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
    await server.start_server()
    link = NodeOutage("127.0.0.1", ("127.0.0.1", server.port))
    await link.start()
    broker = await RemoteSandboxBroker.open(
        link.url, StaticBearerAuth(TOKEN), heartbeat_s=0.2, handshake_s=0.5, request_timeout_s=0.5
    )
    try:
        yield Rig(fake, link, broker)
    finally:
        link.heal()
        await broker.aclose()
        await link.stop()
        await server.close()


async def _sandbox(rig: Rig):  # noqa: ANN202
    spec = SandboxSpec(
        image_digest=ENGINE_IMAGE, mode=RuntimeMode.GVISOR, mounts=tuple(MountSpec(t) for t in ENGINE_MOUNTS)
    )
    return await rig.broker.acquire(spec, lease=LeaseRef(holder="fm11", fence=1), idempotency_key="fm11")


async def test_fm11_fb9_a_running_process_whose_link_goes_silent_ends_broker_unavailable(rig: Rig) -> None:
    process = await (await _sandbox(rig)).exec(["forever"], env={})
    rig.link.partition()
    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(process.wait(timeout_s=REPORTED_WITHIN_S * 2), REPORTED_WITHIN_S)


async def test_fm11_fb9_a_signal_on_a_lost_exec_stream_is_broker_unavailable(rig: Rig) -> None:
    process = await (await _sandbox(rig)).exec(["forever"], env={})
    rig.link.partition()
    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(process.wait(timeout_s=REPORTED_WITHIN_S * 2), REPORTED_WITHIN_S)
    with pytest.raises(BrokerUnavailable):
        await process.signal(15)


async def test_fm11_fb9_a_stream_opened_over_a_silent_link_is_broker_unavailable(rig: Rig) -> None:
    sandbox = await _sandbox(rig)
    rig.link.partition()
    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(sandbox.connect(PORT), REPORTED_WITHIN_S)
    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(sandbox.exec(["forever"], env={}), REPORTED_WITHIN_S)


async def test_fm11_fb9_a_tunnel_whose_link_goes_silent_is_broker_unavailable_not_end_of_stream(rig: Rig) -> None:
    sandbox = await _sandbox(rig)
    served: asyncio.Queue[FakeStream] = asyncio.Queue()

    async def hold(stream: FakeStream) -> None:
        await served.put(stream)
        await asyncio.sleep(3600)

    FakeSandboxHandle(rig.fake, rig.fake.sandboxes[sandbox.id]).listen(PORT, hold)
    tunnel = await sandbox.connect(PORT)
    _ = await asyncio.wait_for(served.get(), REPORTED_WITHIN_S)
    rig.link.partition()
    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(tunnel.read(), REPORTED_WITHIN_S)


async def test_fm11_fb9_a_request_over_a_silent_link_is_broker_unavailable(rig: Rig) -> None:
    rig.link.partition()
    with pytest.raises(BrokerUnavailable):
        await asyncio.wait_for(rig.broker.refresh_modes(), REPORTED_WITHIN_S)
