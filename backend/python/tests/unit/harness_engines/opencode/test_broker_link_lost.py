"""FM-11 / FB-9 (CH-12): opencode started through the broker service while the link goes silent.

In P9's FM-11 the attempt's open probed opencode's health through a link that held every byte, then its cleanup
waited on the exec stream for the exit code, timed out, and that bare ``TimeoutError`` replaced the open's own
error: the kernel released the attempt ``engine_kernel_error``. The open must end with the broker's
``BrokerUnavailable`` (the kernel's ``sandbox_wait``), soon, and a failing cleanup must not replace it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.engines.errors import EngineUnhealthy
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.errors import BrokerUnavailable
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.contract import ENGINE_IMAGE, ENGINE_MOUNTS, open_rig
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.testing.sandbox.fake import FakeSandboxHandle
from harness_engines.opencode.testing.fake_server import install
from tests.unit.harness.engines.durable_stack import NodeOutage
from tests.unit.harness_engines.opencode.conftest import OpencodeHarness

if TYPE_CHECKING:
    from harness_core.sandbox import SandboxHandle
    from harness_core.testing.sandbox.egress import FakeEgress
    from harness_core.testing.sandbox.fake import FakeProcessContext

TOKEN = "fm11-token"
# Far below the start-up probing's own end (minutes), well above the link's heartbeat.
REPORTED_WITHIN_S = 10.0
# How long the engine takes to start listening.
START_S = 0.5


@dataclass
class RemoteOpencodeHarness(OpencodeHarness):
    """The opencode harness with its sandbox behind the broker service's HTTP API, through a forwarder that
    partitions the link as the engine starts."""

    closers: list[object] = field(default_factory=list[object])

    async def new_sandbox(self, egress: FakeEgress) -> SandboxHandle:  # type: ignore[override]
        fake = FakeSandboxBroker(
            clock=ManualClock(), ids=self.ids, egress=egress, modes=frozenset(RuntimeMode),
            verified_binaries={ENGINE_IMAGE: self.vouched},
        )
        server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
        await server.start_server()
        link = NodeOutage("127.0.0.1", ("127.0.0.1", server.port))
        await link.start()
        broker = await RemoteSandboxBroker.open(link.url, StaticBearerAuth(TOKEN), heartbeat_s=0.2)
        self.closers.extend((broker, link, server))
        spec = SandboxSpec(
            image_digest=ENGINE_IMAGE, mode=RuntimeMode.GVISOR, mounts=tuple(MountSpec(t) for t in ENGINE_MOUNTS)
        )
        sandbox = await broker.acquire(spec, lease=LeaseRef(holder="fm11", fence=1), idempotency_key="fm11")
        inside = FakeSandboxHandle(fake, fake.sandboxes[sandbox.id])
        opencode = install(inside, version=self.binary_version, digest=self.version.binary_digest)

        async def partitioned_as_it_starts(ctx: FakeProcessContext) -> int:
            # After the broker has said the process started: the open is past its exec, probing for health.
            _ = asyncio.get_running_loop().call_later(START_S / 2, link.partition)
            await asyncio.sleep(START_S)
            return await opencode.program(ctx)

        inside.install("opencode", partitioned_as_it_starts)
        return sandbox  # type: ignore[return-value]

    async def close(self) -> None:
        for closer in self.closers:
            if isinstance(closer, NodeOutage):
                closer.heal()
                await closer.stop()
            elif isinstance(closer, RemoteSandboxBroker):
                await closer.aclose()
            elif isinstance(closer, TestServer):
                await closer.close()


async def test_fm11_fb9_an_opencode_started_over_a_link_that_goes_silent_ends_broker_unavailable() -> None:
    harness = RemoteOpencodeHarness()
    clock = asyncio.get_running_loop()
    started = clock.time()
    try:
        with pytest.raises(BrokerUnavailable):
            await asyncio.wait_for(open_rig(harness), REPORTED_WITHIN_S)
        # Not the deadline's cancellation, replaced by a cleanup's error on the way out.
        assert clock.time() - started < REPORTED_WITHIN_S
    finally:
        await harness.close()


async def test_fm11_fb9_a_cleanup_that_fails_does_not_replace_the_opens_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FM-11's shape: the open failed, then the cleanup's wait for the exit code timed out."""
    harness = OpencodeHarness()

    async def never_healthy(*_args: object) -> str:
        raise EngineUnhealthy("opencode did not become healthy")

    async def exit_code_never_comes(*_args: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(harness.adapter, "_wait_healthy", never_healthy)
    monkeypatch.setattr(harness.adapter, "_stop", exit_code_never_comes)
    with pytest.raises(EngineUnhealthy):
        await open_rig(harness)
