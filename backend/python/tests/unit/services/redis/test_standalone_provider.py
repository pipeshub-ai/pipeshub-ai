"""Unit tests for app.services.redis.standalone_provider.StandaloneRedisProvider."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.standalone_provider import StandaloneRedisProvider


def _config(**overrides) -> RedisConnectionConfig:
    base = RedisConnectionConfig(host="localhost", port=6379)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture
def mock_redis_cls():
    with patch("app.services.redis.standalone_provider.Redis") as mock_cls:
        mock_cls.side_effect = lambda **kwargs: MagicMock(**{"aclose": AsyncMock()})
        yield mock_cls


class TestCreateClient:
    def test_create_client_returns_fresh_instance_each_time(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        c1 = provider.create_client()
        c2 = provider.create_client()
        assert c1 is not c2
        assert mock_redis_cls.call_count == 2

    def test_create_client_passes_decode_responses_and_pool_options(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        provider.create_client(ClientOptions(decode_responses=False, max_connections=25))

        kwargs = mock_redis_cls.call_args.kwargs
        assert kwargs["decode_responses"] is False

    def test_blocking_option_uses_blocking_connection_pool(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        with patch(
            "app.services.redis.standalone_provider.BlockingConnectionPool"
        ) as mock_pool_cls:
            mock_pool_cls.return_value = MagicMock()
            provider.create_client(ClientOptions(blocking=True))

        mock_pool_cls.assert_called_once()
        kwargs = mock_redis_cls.call_args.kwargs
        assert "connection_pool" in kwargs

    def test_tls_sets_ssl_kwargs(self, mock_redis_cls):
        provider = StandaloneRedisProvider(
            _config(tls=True, tls_reject_unauthorized=False, tls_ca_path="/ca.pem")
        )
        provider.create_client()

        kwargs = mock_redis_cls.call_args.kwargs
        assert kwargs["ssl"] is True
        assert kwargs["ssl_cert_reqs"] is None
        assert kwargs["ssl_ca_certs"] == "/ca.pem"

    def test_username_and_password_forwarded(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config(username="u", password="p"))
        provider.create_client()

        kwargs = mock_redis_cls.call_args.kwargs
        assert kwargs["username"] == "u"
        assert kwargs["password"] == "p"


class TestGetClientPerLoopCaching:
    @pytest.mark.asyncio
    async def test_same_loop_returns_cached_client(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        c1 = provider.get_client()
        c2 = provider.get_client()
        assert c1 is c2
        assert mock_redis_cls.call_count == 1

    def test_no_running_loop_still_creates_client(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        client = provider.get_client()
        assert client is not None

    @pytest.mark.asyncio
    async def test_stale_client_bound_to_closed_loop_is_replaced(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        first = provider.get_client()

        # Simulate the loop this client was bound to having since closed.
        thread_id = list(provider._clients.keys())[0]
        client, _ = provider._clients[thread_id]
        fake_closed_loop = MagicMock()
        fake_closed_loop.is_closed.return_value = True
        provider._clients[thread_id] = (client, fake_closed_loop)

        second = provider.get_client()
        assert second is not first
        assert mock_redis_cls.call_count == 2


class TestCreatePubsubClient:
    def test_uses_blocking_options(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        with patch.object(provider, "create_client", wraps=provider.create_client) as spy:
            provider.create_pubsub_client()
        options_arg = spy.call_args[0][0]
        assert options_arg.blocking is True


class TestScanKeys:
    @pytest.mark.asyncio
    async def test_decodes_bytes_keys(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        fake_client = MagicMock()

        async def _scan_iter(match, count):
            for k in [b"key:1", "key:2"]:
                yield k

        fake_client.scan_iter = _scan_iter
        with patch.object(provider, "get_client", return_value=fake_client):
            keys = [k async for k in provider.scan_keys("key:*")]

        assert keys == ["key:1", "key:2"]


class TestLoadScript:
    @pytest.mark.asyncio
    async def test_decodes_sha_bytes(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        fake_client = MagicMock()
        fake_client.script_load = AsyncMock(return_value=b"deadbeef")
        with patch.object(provider, "get_client", return_value=fake_client):
            sha = await provider.load_script("return 1")

        assert sha == "deadbeef"


class TestKeySlot:
    def test_always_zero(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        assert provider.key_slot("any-key") == 0
        assert provider.key_slot("{tag}other-key") == 0


class TestConnectionUrl:
    def test_plain_no_auth(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config(host="h", port=1234, db=2))
        assert provider.connection_url() == "redis://h:1234/2"

    def test_password_only(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config(host="h", port=1234, password="pw"))
        assert provider.connection_url() == "redis://:pw@h:1234/0"

    def test_username_and_password(self, mock_redis_cls):
        provider = StandaloneRedisProvider(
            _config(host="h", port=1234, username="u", password="pw")
        )
        assert provider.connection_url() == "redis://u:pw@h:1234/0"

    def test_tls_uses_rediss_scheme(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config(host="h", port=1234, tls=True))
        assert provider.connection_url().startswith("rediss://")

    def test_password_with_reserved_characters_is_percent_encoded(self, mock_redis_cls):
        provider = StandaloneRedisProvider(
            _config(host="h", port=1234, username="u@1", password="p@ss:w/rd#1")
        )
        url = provider.connection_url()
        # Un-encoded '@'/'/'/'#' in the credentials would otherwise be
        # mistaken for the authority separator, path, or fragment delimiter.
        assert url == "redis://u%401:p%40ss%3Aw%2Frd%231@h:1234/0"


class TestPing:
    @pytest.mark.asyncio
    async def test_true_when_client_pings_successfully(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        fake_client = MagicMock()
        fake_client.ping = AsyncMock(return_value=True)
        with patch.object(provider, "get_client", return_value=fake_client):
            assert await provider.ping() is True

    @pytest.mark.asyncio
    async def test_false_on_exception(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        fake_client = MagicMock()
        fake_client.ping = AsyncMock(side_effect=ConnectionError("down"))
        with patch.object(provider, "get_client", return_value=fake_client):
            assert await provider.ping() is False


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_every_created_client(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        c1 = provider.create_client()
        c2 = provider.create_client()

        await provider.close()

        c1.aclose.assert_awaited_once()
        c2.aclose.assert_awaited_once()
        assert provider._created_clients == []
        assert provider._clients == {}

    @pytest.mark.asyncio
    async def test_one_failing_close_does_not_block_the_rest(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        c1 = provider.create_client()
        c1.aclose = AsyncMock(side_effect=RuntimeError("boom"))
        c2 = provider.create_client()

        await provider.close()

        c2.aclose.assert_awaited_once()


class TestModeAndCluster:
    def test_is_cluster_false(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        assert provider.is_cluster is False

    def test_mode_is_standalone(self, mock_redis_cls):
        provider = StandaloneRedisProvider(_config())
        assert provider.mode == "standalone"


class TestDbDeprecationWarning:
    def test_warns_when_db_is_set(self, mock_redis_cls):
        with patch("app.services.redis.standalone_provider.logger") as mock_logger:
            StandaloneRedisProvider(_config(db=1))
            mock_logger.warning.assert_called_once()

    def test_no_warning_when_db_is_zero(self, mock_redis_cls):
        with patch("app.services.redis.standalone_provider.logger") as mock_logger:
            StandaloneRedisProvider(_config(db=0))
            mock_logger.warning.assert_not_called()
