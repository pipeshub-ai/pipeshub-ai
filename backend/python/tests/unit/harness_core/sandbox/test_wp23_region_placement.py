"""RES-5..RES-8 (OWD-12, WP-23): sandbox placement respects the tenant's region.

A worker names its region on every sandbox it asks for (the region rides on the sandbox's labels, so the container
keeps it); a broker given a region creates a sandbox only for a caller in that region, and refuses one that names
another region or none. Which region a process is in is the ``RegionPort`` extension point: ``default`` here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from app.harness.placement import deployment_region
from harness_core.region import REGION_LABEL, SINGLE_REGION, RegionPort, is_region
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.errors import SandboxRefusal
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import REGION_MISMATCH, create_broker_app
from harness_core.sandbox.types import MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeEgressContractEnv, FakeSandboxBroker
from harness_core.testing.sandbox.contract import LEASE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

TOKEN = "wp23-broker-token"
SPEC = SandboxSpec(
    image_digest="sha256:" + "a" * 64,
    mode=RuntimeMode.DOCKER_HARDENED,
    mounts=(MountSpec("/work"),),
    labels={"runId": "r1"},
)


@pytest.fixture
def fake() -> FakeSandboxBroker:
    clock = ManualClock()
    return FakeSandboxBroker(clock=clock, egress_proxy=FakeEgressContractEnv(clock).proxy)


@pytest.fixture
async def eu_broker(fake: FakeSandboxBroker) -> AsyncIterator[str]:
    server = TestServer(create_broker_app(fake, StaticBearerAuth(TOKEN), region="eu-west"))
    await server.start_server()
    yield str(server.make_url("")).rstrip("/")
    await server.close()


async def _client(url: str, region: str | None) -> RemoteSandboxBroker:
    return await RemoteSandboxBroker.open(url, StaticBearerAuth(TOKEN), region=region)


def test_res5_a_process_runs_in_default_unless_an_extension_point_names_its_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert deployment_region() == "default"
    assert SINGLE_REGION.region({"PIPESHUB_REGION": "eu-west"}) == "default"

    class Placed(RegionPort):
        def region(self, env: Mapping[str, str]) -> str:
            return "eu-west"

    from harness_core.edition import HarnessEdition

    monkeypatch.setattr("app.edition_services.harness_edition", lambda: HarnessEdition(ports={RegionPort: Placed()}))
    assert deployment_region() == "eu-west"
    for bad in ("eu west", "-eu", "eu_", "EU", "x" * 65):
        assert not is_region(bad)


async def test_res6_a_caller_in_the_brokers_region_gets_a_sandbox_labelled_with_it(
    eu_broker: str, fake: FakeSandboxBroker
) -> None:
    client = await _client(eu_broker, "eu-west")
    try:
        _ = await client.acquire(SPEC, lease=LEASE, idempotency_key="k1")
        [state] = fake.sandboxes.values()
        assert state.spec.labels[REGION_LABEL] == "eu-west"
    finally:
        await client.aclose()


@pytest.mark.parametrize("caller", ["us-east", None])
async def test_res7_a_caller_in_another_region_or_naming_none_is_refused(eu_broker: str, caller: str | None) -> None:
    client = await _client(eu_broker, caller)
    try:
        with pytest.raises(SandboxRefusal) as refused:
            _ = await client.acquire(SPEC, lease=LEASE, idempotency_key="k2")
        assert refused.value.reason == REGION_MISMATCH
    finally:
        await client.aclose()


async def test_res8_a_spec_that_already_names_a_region_keeps_it(eu_broker: str) -> None:
    client = await _client(eu_broker, "eu-west")
    named = SandboxSpec(
        image_digest=SPEC.image_digest, mode=SPEC.mode, mounts=SPEC.mounts, labels={**SPEC.labels, REGION_LABEL: "us-east"}
    )
    try:
        with pytest.raises(SandboxRefusal) as refused:
            _ = await client.acquire(named, lease=LEASE, idempotency_key="k3")
        assert refused.value.reason == REGION_MISMATCH
    finally:
        await client.aclose()
