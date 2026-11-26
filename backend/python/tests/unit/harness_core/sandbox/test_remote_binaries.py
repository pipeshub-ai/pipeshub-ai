"""EH2-14 (EH2, SBX-33): what the broker vouches for reaches the remote client's handle, and nothing malformed
does: a dropped entry only means the engine hashes that file itself."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.remote import wire
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOKEN = "test-broker-token"
IMAGE = "engine@sha256:" + "0" * 64
PATH = "/usr/local/bin/codex-app-server"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
async def remote() -> AsyncIterator[RemoteSandboxBroker]:
    fake = FakeSandboxBroker(clock=ManualClock(), verified_binaries={IMAGE: {PATH: DIGEST}})
    server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN)))
    await server.start_server()
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))
    yield client
    await client.aclose()
    await server.close()


async def test_eh2_14_the_client_handle_carries_what_the_broker_vouches_for(remote: RemoteSandboxBroker) -> None:
    spec = SandboxSpec(image_digest=IMAGE, mode=RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/workspace"),))
    handle = await remote.acquire(spec, lease=LeaseRef("w", 1), idempotency_key="k")
    try:
        assert dict(handle.verified_binaries) == {PATH: DIGEST}
    finally:
        await remote.release(handle)


@pytest.mark.parametrize(
    "raw",
    [
        {"usr/local/bin/codex-app-server": DIGEST},
        {PATH: "sha256:" + "A" * 64},
        {PATH: "sha256:" + "a" * 63},
        {PATH: "md5:" + "a" * 32},
        {PATH: 7},
        [PATH, DIGEST],
        None,
    ],
    ids=["relative-path", "upper-hex", "short-hex", "other-algorithm", "not-a-string", "a-list", "absent"],
)
def test_eh2_14_a_malformed_voucher_is_dropped(raw: object) -> None:
    assert wire.parse_verified_binaries(raw) == {}


def test_eh2_14_well_formed_entries_survive_beside_dropped_ones() -> None:
    assert wire.parse_verified_binaries({PATH: DIGEST, "relative": DIGEST}) == {PATH: DIGEST}
