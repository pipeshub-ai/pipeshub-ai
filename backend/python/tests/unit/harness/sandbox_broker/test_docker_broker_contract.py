"""SB-1..SB-30 on the Docker broker, in process (runc lane; gVisor lane when runsc exists).

SB-27..SB-30 run against a broker wired to a real egress proxy container (P5.4). C2.14: the Docker provider, opened
through a ``SandboxProviderFactory`` as the broker process opens it, passes SP-1..SP-7 and every SB case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.provider import (
    BrokerSandboxProvider,
    SandboxProviderFactory,
    SandboxProviderKind,
)
from harness_core.testing.sandbox import SandboxBrokerContract
from harness_core.testing.sandbox.provider_contract import SandboxProviderContract

if TYPE_CHECKING:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.sandbox_broker.egress_stack import DockerEgressContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]


@pytest.fixture
def broker(docker_broker: DockerSandboxBroker) -> DockerSandboxBroker:
    return docker_broker


class TestDockerBroker(SandboxBrokerContract):
    @pytest.fixture
    def egress_broker(self, egress_docker_broker: DockerSandboxBroker) -> DockerSandboxBroker:
        return egress_docker_broker

    @pytest.fixture
    def egress_env(self, docker_egress_env: DockerEgressContractEnv) -> DockerEgressContractEnv:
        return docker_egress_env


class TestDockerProvider(SandboxProviderContract):
    @pytest.fixture
    async def provider(self, docker_broker: DockerSandboxBroker) -> BrokerSandboxProvider[DockerSandboxBroker]:
        async def build(_: None) -> DockerSandboxBroker:
            return docker_broker

        factory = SandboxProviderFactory({SandboxProviderKind.DOCKER: build}, where="this sandbox broker")
        return await factory.open(SandboxProviderKind.DOCKER, None)
