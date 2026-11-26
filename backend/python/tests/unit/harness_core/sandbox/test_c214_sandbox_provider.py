"""C2.14: ``ISandboxProvider`` over the in-memory broker and over the broker's HTTP client (SP-1..SP-7 with SB-*),
and ``SandboxProviderFactory`` keyed by the deployment's ``SANDBOX_PROVIDER`` (SPF-1..SPF-4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.errors import SandboxProviderUnavailable
from harness_core.sandbox.provider import (
    PROVIDER_VERBS,
    BrokerSandboxProvider,
    SandboxProviderFactory,
    SandboxProviderKind,
    provider_kind,
)
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeContractEnv, FakeSandboxBroker
from harness_core.testing.sandbox.provider_contract import SandboxProviderContract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TOKEN = "provider-contract-token"


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def sandbox_env(clock: ManualClock) -> FakeContractEnv:
    return FakeContractEnv(clock)


def _fakes(clock: ManualClock) -> SandboxProviderFactory[None, FakeSandboxBroker]:
    async def build(_: None) -> FakeSandboxBroker:
        return FakeSandboxBroker(clock=clock)

    return SandboxProviderFactory({SandboxProviderKind.DOCKER: build}, where="this test")


class TestProviderOverTheInMemoryBroker(SandboxProviderContract):
    @pytest.fixture
    async def provider(self, clock: ManualClock) -> BrokerSandboxProvider[FakeSandboxBroker]:
        return await _fakes(clock).open(SandboxProviderKind.DOCKER, None)


class TestProviderOverTheBrokerClient(SandboxProviderContract):
    """The worker's path: the provider over the HTTP client of a broker process."""

    @pytest.fixture
    async def provider(self, clock: ManualClock) -> AsyncIterator[BrokerSandboxProvider[RemoteSandboxBroker]]:
        app = create_broker_app(FakeSandboxBroker(clock=clock), StaticBearerAuth(TOKEN))
        async with TestServer(app) as server:

            async def build(_: None) -> RemoteSandboxBroker:
                return await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth(TOKEN))

            factory = SandboxProviderFactory({SandboxProviderKind.DOCKER: build}, where="this test")
            provider = await factory.open(SandboxProviderKind.DOCKER, None)
            try:
                yield provider
            finally:
                await provider.broker.aclose()


@pytest.mark.parametrize(("raw", "kind"), [(None, "docker"), ("", "docker"), (" Docker ", "docker"), ("k8s", "k8s")])
def test_spf1_the_deployment_names_the_kind_and_docker_is_the_default(raw: str | None, kind: str) -> None:
    assert provider_kind(raw) is SandboxProviderKind(kind)


def test_spf2_a_value_that_names_no_provider_is_refused_never_read_as_docker() -> None:
    with pytest.raises(SandboxProviderUnavailable) as refused:
        provider_kind("dokcer")
    assert refused.value.reason_code == "provider_unknown"
    assert "SANDBOX_PROVIDER='dokcer' names no sandbox provider" in str(refused.value)
    assert "docker, k8s, managed, microvm" in str(refused.value)


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (SandboxProviderKind.KUBERNETES, "Kubernetes"),
        (SandboxProviderKind.MANAGED, "managed (hosted)"),
        (SandboxProviderKind.MICROVM, "microVM"),
    ],
)
async def test_spf3_a_provider_this_deployment_cannot_run_is_a_plain_refusal_before_anything_is_built(
    clock: ManualClock, kind: SandboxProviderKind, label: str
) -> None:
    built: list[None] = []

    async def build(_: None) -> FakeSandboxBroker:
        built.append(None)
        return FakeSandboxBroker(clock=clock)

    factory = SandboxProviderFactory({SandboxProviderKind.DOCKER: build}, where="this worker")
    with pytest.raises(SandboxProviderUnavailable) as refused:
        await factory.open(kind, None)
    assert refused.value.reason_code == "provider_unavailable"
    assert str(refused.value) == (
        f"The {label} sandbox provider is not available in this deployment: this worker runs docker. "
        "Set SANDBOX_PROVIDER to one of those, or leave it unset for Docker."
    )
    assert built == []
    assert factory.available() == frozenset({SandboxProviderKind.DOCKER})


async def test_spf4_the_opened_provider_carries_its_kind_and_its_backend(clock: ManualClock) -> None:
    provider = await _fakes(clock).open(SandboxProviderKind.DOCKER, None)
    assert provider.kind is SandboxProviderKind.DOCKER
    assert isinstance(provider.broker, FakeSandboxBroker)
    assert provider.capabilities().verbs == PROVIDER_VERBS
