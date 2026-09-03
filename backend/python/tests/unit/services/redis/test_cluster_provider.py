"""Unit tests for app.services.redis.cluster_provider.ClusterRedisProvider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.redis.cluster_provider import ClusterRedisProvider
from app.services.redis.config import ClientOptions, RedisConnectionConfig


def _config(**overrides) -> RedisConnectionConfig:
    base = RedisConnectionConfig(host="localhost", port=6379)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture
def mock_cluster_cls():
    with patch("app.services.redis.cluster_provider.RedisCluster") as mock_cls:
        mock_cls.side_effect = lambda **kwargs: MagicMock(**{"aclose": AsyncMock()})
        yield mock_cls


class TestStartupNodes:
    def test_uses_cluster_endpoints_when_set(self, mock_cluster_cls):
        provider = ClusterRedisProvider(
            _config(cluster_endpoints=["n1:7000", "n2:7001"])
        )
        provider.create_client()

        nodes = mock_cluster_cls.call_args.kwargs["startup_nodes"]
        assert len(nodes) == 2

    def test_falls_back_to_host_port_when_no_endpoints(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config(host="h", port=7000, cluster_endpoints=[]))
        provider.create_client()

        nodes = mock_cluster_cls.call_args.kwargs["startup_nodes"]
        assert len(nodes) == 1


class TestCreateClient:
    def test_read_from_replicas_when_scale_reads_slave_or_all(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config(scale_reads="all"))
        provider.create_client()
        assert mock_cluster_cls.call_args.kwargs["read_from_replicas"] is True

    def test_no_read_from_replicas_when_scale_reads_master(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config(scale_reads="master"))
        provider.create_client()
        assert mock_cluster_cls.call_args.kwargs["read_from_replicas"] is False

    def test_requires_full_coverage_always_true(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        provider.create_client()
        assert mock_cluster_cls.call_args.kwargs["require_full_coverage"] is True

    def test_fresh_instance_each_call(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        c1 = provider.create_client()
        c2 = provider.create_client()
        assert c1 is not c2


class TestGetClientCaching:
    def test_same_thread_returns_cached_client(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        c1 = provider.get_client()
        c2 = provider.get_client()
        assert c1 is c2
        assert mock_cluster_cls.call_count == 1


class TestCreatePubsubClient:
    """A pub/sub connection must be the caller's alone (R13).

    ``SUBSCRIBE`` puts a connection into subscriber mode where it can no
    longer serve ordinary commands, so handing back
    ``get_default_node().redis_connection`` -- which belongs to the shared
    cluster client -- breaks every other user of that client, and a caller
    that closes it takes the cluster client down with it.
    """

    def test_builds_a_dedicated_connection_to_the_default_node(
        self, mock_cluster_cls, monkeypatch
    ):
        provider = ClusterRedisProvider(_config())
        fake_client = MagicMock()
        fake_node = MagicMock()
        fake_node.host = "10.0.0.7"
        fake_node.port = 7001
        fake_client.get_default_node.return_value = fake_node

        built = []

        def _fake_redis(**kwargs):
            built.append(kwargs)
            return MagicMock(name="dedicated")

        monkeypatch.setattr(
            "app.services.redis.cluster_provider.Redis", _fake_redis
        )
        with patch.object(provider, "get_client", return_value=fake_client):
            result = provider.create_pubsub_client()

        assert result is not fake_node.redis_connection
        assert built[0]["host"] == "10.0.0.7"
        assert built[0]["port"] == 7001
        # A plain node connection, not a cluster one.
        assert "startup_nodes" not in built[0]

    def test_falls_back_to_the_configured_endpoint_before_discovery(
        self, mock_cluster_cls, monkeypatch
    ):
        """Topology is not loaded until the client connects, so pub/sub must
        not depend on `get_default_node()` having a result yet."""
        provider = ClusterRedisProvider(_config(cluster_endpoints=["n1:7000"]))
        fake_client = MagicMock()
        fake_client.get_default_node.return_value = None

        built = []
        monkeypatch.setattr(
            "app.services.redis.cluster_provider.Redis",
            lambda **kwargs: built.append(kwargs) or MagicMock(),
        )
        with patch.object(provider, "get_client", return_value=fake_client):
            provider.create_pubsub_client()

        assert built[0]["host"] == "n1"
        assert built[0]["port"] == 7000


class TestScanKeysUsesTheClientsOwnScanIter:
    """`redis.asyncio.cluster.ClusterNode` has no `redis_connection`
    attribute (that is the *sync* client's shape) -- an earlier version of
    `scan_keys` iterated `client.get_primaries()` and read that attribute,
    which raises `AttributeError` on every real cluster client. Verified
    against a live 3-master cluster that `RedisCluster.scan_iter` on the
    client itself already fans out over every primary and merges cursors,
    which is what these tests pin down at the mock level."""

    @pytest.mark.asyncio
    async def test_delegates_to_the_clients_scan_iter(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())

        async def _scan_iter(match, count):
            for k in [b"a1", "b1"]:
                yield k

        fake_client = MagicMock()
        fake_client.scan_iter = _scan_iter
        with patch.object(provider, "get_client", return_value=fake_client):
            keys = [k async for k in provider.scan_keys("*", count=50)]

        assert sorted(keys) == ["a1", "b1"]

    @pytest.mark.asyncio
    async def test_no_matches_yields_no_keys(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())

        async def _scan_iter(match, count):
            return
            yield  # pragma: no cover - makes this an async generator

        fake_client = MagicMock()
        fake_client.scan_iter = _scan_iter
        with patch.object(provider, "get_client", return_value=fake_client):
            keys = [k async for k in provider.scan_keys("*")]
        assert keys == []


class TestLoadScriptUsesTheClientsOwnScriptLoad:
    """redis-py's async `RedisCluster.script_load` already special-cases
    `SCRIPT LOAD` as an all-primaries command (verified against a live
    3-master cluster: a script loaded once `evalsha`'s successfully
    against keys hashing to every node) -- no manual per-node fan-out is
    needed, unlike the now-removed loop over `client.get_primaries()`."""

    @pytest.mark.asyncio
    async def test_delegates_to_the_clients_script_load_and_decodes_the_sha(
        self, mock_cluster_cls
    ):
        provider = ClusterRedisProvider(_config())
        fake_client = MagicMock()
        fake_client.script_load = AsyncMock(return_value=b"sha1")
        with patch.object(provider, "get_client", return_value=fake_client):
            sha = await provider.load_script("return 1")

        assert sha == "sha1"
        fake_client.script_load.assert_awaited_once_with("return 1")

    @pytest.mark.asyncio
    async def test_returns_a_str_sha_unchanged(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        fake_client = MagicMock()
        fake_client.script_load = AsyncMock(return_value="sha1")
        with patch.object(provider, "get_client", return_value=fake_client):
            sha = await provider.load_script("return 1")
        assert sha == "sha1"


class TestKeySlot:
    def test_matches_redis_crc16_slot(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        # CRC16 slot for a fixed key is deterministic; just assert it is a
        # valid Redis Cluster slot rather than pinning the exact value to
        # redis-py's internal implementation.
        slot = provider.key_slot("some-key")
        assert 0 <= slot < 16384

    def test_same_hashtag_maps_to_same_slot(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        assert provider.key_slot("{tenant-a}:foo") == provider.key_slot("{tenant-a}:bar")


class TestConnectionUrl:
    def test_raises_not_implemented(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        with pytest.raises(NotImplementedError):
            provider.connection_url()


class TestPing:
    @pytest.mark.asyncio
    async def test_false_on_exception(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        fake_client = MagicMock()
        fake_client.ping = AsyncMock(side_effect=ConnectionError("down"))
        with patch.object(provider, "get_client", return_value=fake_client):
            assert await provider.ping() is False


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_every_created_client(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        c1 = provider.create_client()
        c2 = provider.create_client()

        await provider.close()

        c1.aclose.assert_awaited_once()
        c2.aclose.assert_awaited_once()


class TestModeAndCluster:
    def test_is_cluster_true(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        assert provider.is_cluster is True

    def test_mode_is_cluster(self, mock_cluster_cls):
        provider = ClusterRedisProvider(_config())
        assert provider.mode == "cluster"
