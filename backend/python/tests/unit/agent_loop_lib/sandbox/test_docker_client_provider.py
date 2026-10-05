"""Tests for ``DockerClientProvider`` — the process-wide lazy Docker
client, image/network caches, and bounded executor."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding.docker_client import (
    DockerClientProvider,
)


def _network(name: str, subnet: str = "172.30.0.0/16") -> MagicMock:
    network = MagicMock()
    network.name = name
    network.attrs = {
        "Options": {"com.docker.network.bridge.enable_icc": "false"},
        "IPAM": {"Config": [{"Subnet": subnet}]},
    }
    return network


class TestDockerClientProvider:
    def test_lazy_client_creation(self) -> None:
        provider = DockerClientProvider()
        assert provider._client is None

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = MagicMock()
        with patch.dict(sys.modules, {"docker": mock_docker}):
            _ = provider.client
            mock_docker.from_env.assert_called_once()
            assert provider._client is not None
        provider.close()

    def test_client_is_singleton(self) -> None:
        provider = DockerClientProvider()
        mock_docker = MagicMock()
        fake_client = MagicMock()
        mock_docker.from_env.return_value = fake_client
        with patch.dict(sys.modules, {"docker": mock_docker}):
            c1 = provider.client
            c2 = provider.client
            assert c1 is c2
            mock_docker.from_env.assert_called_once()
        provider.close()

    async def test_ensure_image_caches(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.images.get.return_value = MagicMock()
        provider._client = fake_client

        result1 = await provider.ensure_image("test:latest")
        assert result1 is True
        assert "test:latest" in provider._image_cache

        result2 = await provider.ensure_image("test:latest")
        assert result2 is True
        fake_client.images.get.assert_called_once_with("test:latest")
        provider.close()

    async def test_ensure_image_returns_false_when_missing(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.images.get.side_effect = Exception("not found")
        provider._client = fake_client

        result = await provider.ensure_image("missing:latest")
        assert result is False
        assert "missing:latest" not in provider._image_cache
        provider.close()

    async def test_egress_network_subnets_are_cached(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        existing = _network("sandbox_egress")
        fake_client.networks.list.return_value = [existing]
        fake_client.networks.get.return_value = existing
        provider._client = fake_client

        assert await provider.ensure_egress_network("sandbox_egress") == "sandbox_egress"
        assert await provider.egress_network_cidrs("sandbox_egress") == ["172.30.0.0/16"]
        fake_client.networks.list.assert_called_once()
        provider.close()

    async def test_ensure_egress_network_creates_when_missing(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        created = _network("new_net")
        fake_client.networks.list.side_effect = [[], [created]]
        fake_client.networks.get.return_value = created
        provider._client = fake_client

        assert await provider.ensure_egress_network("new_net") == "new_net"
        # A user-defined bridge with the sandbox label — never the caller's
        # default network, or compose siblings (mongo, arango, redis) would
        # be reachable by name. ICC off keeps one org's sandbox from
        # reaching another's on the same bridge.
        fake_client.networks.create.assert_called_once_with(
            name="new_net",
            driver="bridge",
            internal=False,
            enable_ipv6=False,
            options={"com.docker.network.bridge.enable_icc": "false"},
            labels={"agent_loop.sandbox": "egress"},
            check_duplicate=True,
        )
        provider.close()

    async def test_unreadable_subnet_is_not_cached(self) -> None:
        """An empty answer means callers run offline; a later call must be
        able to pick up the subnet once it is readable."""
        provider = DockerClientProvider()
        fake_client = MagicMock()
        net = _network("egress")
        net.attrs["IPAM"]["Config"] = []
        fake_client.networks.list.return_value = [net]
        fake_client.networks.get.return_value = net
        provider._client = fake_client

        assert await provider.egress_network_cidrs("egress") == []
        net.attrs["IPAM"]["Config"] = [{"Subnet": "172.30.0.0/16"}]
        assert await provider.egress_network_cidrs("egress") == ["172.30.0.0/16"]
        provider.close()

    async def test_ensure_egress_network_tolerates_a_creation_race(self) -> None:
        """Another process may create the network between our look and our
        create; that is success, not failure."""
        provider = DockerClientProvider()
        fake_client = MagicMock()
        raced = _network("raced_net")
        fake_client.networks.list.side_effect = [[], [raced], [raced]]
        fake_client.networks.get.return_value = raced
        fake_client.networks.create.side_effect = RuntimeError("already exists")
        provider._client = fake_client

        assert await provider.ensure_egress_network("raced_net") == "raced_net"
        provider.close()

    async def test_ensure_egress_network_reraises_a_real_failure(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.networks.list.return_value = []
        fake_client.networks.create.side_effect = RuntimeError("daemon refused")
        provider._client = fake_client

        with pytest.raises(RuntimeError, match="daemon refused"):
            await provider.ensure_egress_network("bad_net")
        assert "bad_net" not in provider._network_cidrs
        provider.close()

    async def test_run_blocking_uses_executor(self) -> None:
        provider = DockerClientProvider()
        result = await provider.run_blocking(lambda x: x * 2, 21)
        assert result == 42
        provider.close()

    async def test_ping_success(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.ping.return_value = True
        provider._client = fake_client

        assert await provider.ping() is True
        provider.close()

    async def test_ping_failure(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.ping.side_effect = Exception("unreachable")
        provider._client = fake_client

        assert await provider.ping() is False
        provider.close()

    def test_close_is_idempotent(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        provider._client = fake_client

        provider.close()
        provider.close()
        fake_client.close.assert_called_once()

    def test_executor_bounded(self) -> None:
        provider = DockerClientProvider(max_workers=2)
        assert provider._executor._max_workers == 2
        provider.close()
