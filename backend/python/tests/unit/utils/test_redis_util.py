"""Unit tests for app.utils.redis_util.build_redis_url()."""

from unittest.mock import patch

import pytest

from app.utils.redis_util import build_redis_url


class TestBuildRedisUrl:
    """Tests for build_redis_url()."""

    def test_basic_no_auth(self):
        config = {"host": "localhost", "port": 6379, "db": 0}
        result = build_redis_url(config)
        assert result == "redis://localhost:6379/0"

    def test_with_password_only(self):
        config = {"host": "redis.example.com", "port": 6380, "password": "secret", "db": 1}
        result = build_redis_url(config)
        assert result == "redis://:secret@redis.example.com:6380/1"

    def test_with_username_and_password(self):
        config = {
            "host": "redis.example.com",
            "port": 6379,
            "username": "admin",
            "password": "pass123",
            "db": 2,
        }
        result = build_redis_url(config)
        assert result == "redis://admin:pass123@redis.example.com:6379/2"

    def test_with_username_no_password(self):
        config = {
            "host": "redis.example.com",
            "port": 6379,
            "username": "admin",
            "db": 0,
        }
        result = build_redis_url(config)
        assert result == "redis://admin@redis.example.com:6379/0"

    def test_tls_enabled(self):
        config = {"host": "secure.redis.io", "port": 6380, "tls": True, "db": 0}
        result = build_redis_url(config)
        assert result.startswith("rediss://")
        assert result == "rediss://secure.redis.io:6380/0"

    def test_tls_disabled(self):
        config = {"host": "redis.local", "port": 6379, "tls": False, "db": 0}
        result = build_redis_url(config)
        assert result.startswith("redis://")
        assert not result.startswith("rediss://")

    def test_tls_with_auth(self):
        config = {
            "host": "secure.redis.io",
            "port": 6380,
            "tls": True,
            "username": "user",
            "password": "pw",
            "db": 3,
        }
        result = build_redis_url(config)
        assert result == "rediss://user:pw@secure.redis.io:6380/3"

    def test_defaults_when_keys_missing(self):
        """Missing keys should use defaults: host=localhost, port=6379, db=RedisConfig.REDIS_DB."""
        config = {}
        result = build_redis_url(config)
        assert "localhost" in result
        assert "6379" in result
        # db defaults to RedisConfig.REDIS_DB.value which is 0
        assert result.endswith("/0")

    def test_empty_password_ignored(self):
        """Empty or whitespace-only password should not appear in URL."""
        config = {"host": "h", "port": 6379, "password": "   ", "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_empty_username_ignored(self):
        """Empty or whitespace-only username should not appear in URL."""
        config = {"host": "h", "port": 6379, "username": "  ", "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_empty_username_with_valid_password(self):
        """Empty username with valid password yields `:pass@` form."""
        config = {"host": "h", "port": 6379, "username": "", "password": "pw", "db": 0}
        result = build_redis_url(config)
        assert result == "redis://:pw@h:6379/0"

    def test_none_password(self):
        """None password should not appear in URL."""
        config = {"host": "h", "port": 6379, "password": None, "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_none_username(self):
        """None username should not appear in URL."""
        config = {"host": "h", "port": 6379, "username": None, "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_custom_db_number(self):
        config = {"host": "h", "port": 6379, "db": 15}
        result = build_redis_url(config)
        assert result.endswith("/15")

    def test_tls_default_is_false(self):
        """When tls key is missing, scheme should be redis:// not rediss://."""
        config = {"host": "h", "port": 6379, "db": 0}
        result = build_redis_url(config)
        assert result.startswith("redis://")
        assert not result.startswith("rediss://")


# ---------------------------------------------------------------------------
# Cluster / mode plumbing — added when REDIS_MODE=cluster opt-in landed.
# ---------------------------------------------------------------------------


class TestParseRedisNodes:
    def test_empty_returns_empty_list(self):
        from app.utils.redis_util import parse_redis_nodes

        assert parse_redis_nodes(None) == []
        assert parse_redis_nodes("") == []
        assert parse_redis_nodes(",,,") == []

    def test_host_port_pairs(self):
        from app.utils.redis_util import parse_redis_nodes

        parsed = parse_redis_nodes("a:7000, b:7001 ,c:7002")
        assert parsed == [("a", 7000), ("b", 7001), ("c", 7002)]

    def test_defaults_port_when_missing(self):
        from app.utils.redis_util import parse_redis_nodes

        assert parse_redis_nodes("host-only") == [("host-only", 6379)]

    def test_strips_ipv6_brackets(self):
        from app.utils.redis_util import parse_redis_nodes

        assert parse_redis_nodes("[::1]:6379") == [("::1", 6379)]
        assert parse_redis_nodes("fe80::1:7000") == [("fe80::1", 7000)]


class TestIsClusterMode:
    def test_explicit_modes(self):
        from app.utils.redis_util import is_cluster_mode

        assert is_cluster_mode({"mode": "cluster"}) is True
        assert is_cluster_mode({"mode": "CLUSTER"}) is True
        assert is_cluster_mode({"mode": "standalone"}) is False
        assert is_cluster_mode({}) is False


class TestBuildRedisClient:
    def test_returns_redis_in_standalone_mode(self):
        from redis.asyncio import Redis
        from redis.asyncio.cluster import RedisCluster

        from app.utils.redis_util import build_redis_client

        client = build_redis_client({"host": "localhost", "port": 6379, "mode": "standalone"})
        assert isinstance(client, Redis)
        assert not isinstance(client, RedisCluster)

    def test_returns_cluster_in_cluster_mode(self):
        from redis.asyncio.cluster import RedisCluster

        from app.utils.redis_util import build_redis_client

        client = build_redis_client(
            {
                "mode": "cluster",
                "nodes": [("127.0.0.1", 7000), ("127.0.0.1", 7001)],
                "password": None,
            }
        )
        assert isinstance(client, RedisCluster)

    def test_raises_when_cluster_nodes_missing(self):
        from app.utils.redis_util import build_redis_client

        with pytest.raises(ValueError, match="REDIS_NODES"):
            build_redis_client({"mode": "cluster", "nodes": []})

    def test_accepts_string_nodes(self):
        from redis.asyncio.cluster import RedisCluster

        from app.utils.redis_util import build_redis_client

        client = build_redis_client(
            {"mode": "cluster", "nodes": "127.0.0.1:7000,127.0.0.1:7001"}
        )
        assert isinstance(client, RedisCluster)

    def test_accepts_dict_nodes(self):
        from redis.asyncio.cluster import RedisCluster

        from app.utils.redis_util import build_redis_client

        client = build_redis_client(
            {
                "mode": "cluster",
                "nodes": [
                    {"host": "127.0.0.1", "port": 7000},
                    {"host": "127.0.0.1", "port": 7001},
                ],
            }
        )
        assert isinstance(client, RedisCluster)


class TestClusterAwareScanIter:
    def test_falls_through_for_standalone(self):
        """Standalone clients should use the plain scan_iter path, with no
        target_nodes argument."""
        import asyncio

        from app.utils.redis_util import cluster_aware_scan_iter

        captured = {}

        class FakeStandalone:
            async def scan_iter(self, **kwargs):
                captured.update(kwargs)
                for k in ("a", "b", "c"):
                    yield k

        async def collect():
            out = []
            async for k in cluster_aware_scan_iter(FakeStandalone(), match="pat", count=50):
                out.append(k)
            return out

        result = asyncio.run(collect())
        assert result == ["a", "b", "c"]
        assert captured.get("match") == "pat"
        assert captured.get("count") == 50
        assert "target_nodes" not in captured

    def test_targets_primaries_for_cluster(self):
        """RedisCluster path must explicitly target PRIMARIES so partial-shard
        scans cannot regress silently across redis-py versions."""
        import asyncio

        from redis.asyncio.cluster import RedisCluster

        from app.utils.redis_util import cluster_aware_scan_iter

        captured = {}

        class FakeCluster(RedisCluster):
            # Bypass real __init__ so we don't need a live cluster.
            def __init__(self):  # noqa: D401, ANN001
                pass

            async def scan_iter(self, *, match=None, count=100, target_nodes=None):  # type: ignore[override]
                captured["match"] = match
                captured["count"] = count
                captured["target_nodes"] = target_nodes
                for k in ("x", "y"):
                    yield k

        async def collect():
            out = []
            async for k in cluster_aware_scan_iter(FakeCluster(), match="pat"):
                out.append(k)
            return out

        result = asyncio.run(collect())
        assert result == ["x", "y"]
        assert captured["target_nodes"] == RedisCluster.PRIMARIES
        assert captured["match"] == "pat"
