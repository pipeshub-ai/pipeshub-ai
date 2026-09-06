"""Tests for ``DockerClientProvider`` — the process-wide lazy Docker
client, image/network caches, and bounded executor."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding.docker_client import (
    DockerClientProvider,
)


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

    async def test_ensure_egress_network_caches(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        existing = MagicMock()
        existing.name = "sandbox_egress"
        fake_client.networks.list.return_value = [existing]
        provider._client = fake_client

        name = await provider.ensure_egress_network("sandbox_egress")
        assert name == "sandbox_egress"
        assert "sandbox_egress" in provider._network_cache

        name2 = await provider.ensure_egress_network("sandbox_egress")
        assert name2 == "sandbox_egress"
        fake_client.networks.list.assert_called_once()
        provider.close()

    async def test_ensure_egress_network_creates_when_missing(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.networks.list.return_value = []
        fake_client.networks.create.return_value = MagicMock()
        provider._client = fake_client

        name = await provider.ensure_egress_network("new_net")
        assert name == "new_net"
        # A user-defined bridge with the sandbox label — never the caller's
        # default network, or compose siblings (mongo, arango, redis) would
        # be reachable by name from an install container. The label is what
        # lets orphan cleanup find these later.
        fake_client.networks.create.assert_called_once_with(
            name="new_net",
            driver="bridge",
            internal=False,
            labels={"agent_loop.sandbox": "egress"},
            check_duplicate=True,
        )
        assert "new_net" in provider._network_cache
        provider.close()

    async def test_ensure_egress_network_tolerates_a_creation_race(self) -> None:
        """Another process may create the network between our list and our
        create; that is success, not failure."""
        provider = DockerClientProvider()
        fake_client = MagicMock()
        # `name` is a MagicMock constructor kwarg, so it has to be set after
        # construction to become a real attribute.
        raced = MagicMock()
        raced.name = "raced_net"
        fake_client.networks.list.side_effect = [[], [raced]]
        fake_client.networks.create.side_effect = RuntimeError("already exists")
        provider._client = fake_client

        assert await provider.ensure_egress_network("raced_net") == "raced_net"
        assert "raced_net" in provider._network_cache
        provider.close()

    async def test_ensure_egress_network_reraises_a_real_failure(self) -> None:
        provider = DockerClientProvider()
        fake_client = MagicMock()
        fake_client.networks.list.return_value = []
        fake_client.networks.create.side_effect = RuntimeError("daemon refused")
        provider._client = fake_client

        with pytest.raises(RuntimeError, match="daemon refused"):
            await provider.ensure_egress_network("bad_net")
        assert "bad_net" not in provider._network_cache
        provider.close()

    async def test_substring_match_is_not_mistaken_for_the_network(self) -> None:
        """Docker's `names` filter matches on SUBSTRING, so a host running
        the PipesHub compose stack (`pipeshub_sandbox_egress`) answers a
        query for `sandbox_egress` with it. Treating that as a hit skips
        creation and every later container start fails with
        `network sandbox_egress not found`."""
        provider = DockerClientProvider()
        fake_client = MagicMock()
        near_miss = MagicMock()
        near_miss.name = "pipeshub_sandbox_egress"
        fake_client.networks.list.return_value = [near_miss]
        provider._client = fake_client

        assert await provider.ensure_egress_network("sandbox_egress") == "sandbox_egress"
        fake_client.networks.create.assert_called_once()
        assert fake_client.networks.create.call_args.kwargs["name"] == "sandbox_egress"
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
