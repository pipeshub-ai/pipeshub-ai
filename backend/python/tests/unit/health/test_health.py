"""Unit tests for app.health.health.Health class."""

import os
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.health.health import Health


def _make_container(config_overrides=None):
    """Build a mock container with logger and config_service."""
    container = MagicMock()
    container.logger.return_value = MagicMock()
    mock_config_service = MagicMock()
    mock_config_service.get_config = AsyncMock(return_value=config_overrides or {})
    container.config_service.return_value = mock_config_service
    return container


class _SmallRetry(Enum):
    """Override HealthCheckConfig with small retry counts for testing."""
    CONNECTOR_HEALTH_CHECK_MAX_RETRIES = 2
    CONNECTOR_HEALTH_CHECK_RETRY_INTERVAL_SECONDS = 0


class _OneRetry(Enum):
    """Single retry for quick failure tests."""
    CONNECTOR_HEALTH_CHECK_MAX_RETRIES = 1
    CONNECTOR_HEALTH_CHECK_RETRY_INTERVAL_SECONDS = 0


def _mock_aiohttp_session(response_status=200, response_text="OK"):
    """Create a properly-mocked aiohttp session for use with async context managers."""
    mock_response = MagicMock()
    mock_response.status = response_status
    mock_response.text = AsyncMock(return_value=response_text)

    # response context manager (async with session.get(...) as response)
    mock_response_cm = MagicMock()
    mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response_cm.__aexit__ = AsyncMock(return_value=False)

    # session mock
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response_cm)

    # session context manager (async with aiohttp.ClientSession() as session)
    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_session_cm


# ---------------------------------------------------------------------------
# system_health_check
# ---------------------------------------------------------------------------
class TestSystemHealthCheck:
    """Tests for Health.system_health_check()."""

    @pytest.mark.asyncio
    async def test_dispatches_all_sub_checks(self):
        container = _make_container()
        with (
            patch.object(Health, "health_check_kv_store", new_callable=AsyncMock) as kv,
            patch.object(Health, "health_check_arango", new_callable=AsyncMock) as arango,
            patch.object(Health, "health_check_kafka", new_callable=AsyncMock) as kafka,
            patch.object(Health, "health_check_redis", new_callable=AsyncMock) as redis_hc,
            patch.object(Health, "health_check_vector_db", new_callable=AsyncMock) as vdb,
        ):
            await Health.system_health_check(container)
            kv.assert_awaited_once_with(container)
            arango.assert_awaited_once_with(container)
            kafka.assert_awaited_once_with(container)
            redis_hc.assert_awaited_once_with(container)
            vdb.assert_awaited_once_with(container)


# ---------------------------------------------------------------------------
# health_check_connector_service
# ---------------------------------------------------------------------------
class TestHealthCheckConnectorService:
    """Tests for Health.health_check_connector_service()."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "http://conn:8088"}}
        )

        mock_session_cm = _mock_aiohttp_session(response_status=200)

        with patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm):
            await Health.health_check_connector_service(container)

    @pytest.mark.asyncio
    async def test_uses_default_endpoint_when_config_empty(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(return_value=None)

        mock_session_cm = _mock_aiohttp_session(response_status=200)

        with patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm):
            await Health.health_check_connector_service(container)

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_fail(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "http://conn:8088"}}
        )

        mock_session_cm = _mock_aiohttp_session(
            response_status=503, response_text="Service Unavailable"
        )

        with (
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            with pytest.raises(Exception, match="health check failed"):
                await Health.health_check_connector_service(container)

    @pytest.mark.asyncio
    async def test_raises_on_connection_error(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "http://conn:8088"}}
        )

        # Simulate aiohttp.ClientError
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("connect failed"))
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="health check"):
                await Health.health_check_connector_service(container)


# ---------------------------------------------------------------------------
# health_check_kv_store
# ---------------------------------------------------------------------------
class TestHealthCheckKvStore:
    """Tests for Health.health_check_kv_store() dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatches_to_redis_when_type_redis(self):
        container = _make_container()
        with (
            patch.dict(os.environ, {"KV_STORE_TYPE": "redis"}),
            patch.object(Health, "_health_check_redis_kv_store", new_callable=AsyncMock) as redis_kv,
        ):
            await Health.health_check_kv_store(container)
            redis_kv.assert_awaited_once_with(container)

    @pytest.mark.asyncio
    async def test_dispatches_to_etcd_when_type_etcd(self):
        container = _make_container()
        with (
            patch.dict(os.environ, {"KV_STORE_TYPE": "etcd"}),
            patch.object(Health, "health_check_etcd", new_callable=AsyncMock) as etcd,
        ):
            await Health.health_check_kv_store(container)
            etcd.assert_awaited_once_with(container)

    @pytest.mark.asyncio
    async def test_defaults_to_etcd_when_env_missing(self):
        container = _make_container()
        env = os.environ.copy()
        env.pop("KV_STORE_TYPE", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(Health, "health_check_etcd", new_callable=AsyncMock) as etcd,
        ):
            await Health.health_check_kv_store(container)
            etcd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_case_insensitive_redis(self):
        container = _make_container()
        with (
            patch.dict(os.environ, {"KV_STORE_TYPE": "REDIS"}),
            patch.object(Health, "_health_check_redis_kv_store", new_callable=AsyncMock) as redis_kv,
        ):
            await Health.health_check_kv_store(container)
            redis_kv.assert_awaited_once()


# ---------------------------------------------------------------------------
# _health_check_redis_kv_store
# ---------------------------------------------------------------------------
class TestHealthCheckRedisKvStore:
    """Tests for Health._health_check_redis_kv_store()."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        container = _make_container()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.Redis", return_value=mock_redis),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            await Health._health_check_redis_kv_store(container)
            mock_redis.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_after_retries_exhausted(self):
        container = _make_container()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("connection refused"))
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.Redis", return_value=mock_redis),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            with pytest.raises(Exception, match="connection refused"):
                await Health._health_check_redis_kv_store(container)

    @pytest.mark.asyncio
    async def test_closes_client_on_failure(self):
        container = _make_container()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("refused"))
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.Redis", return_value=mock_redis),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception):
                await Health._health_check_redis_kv_store(container)
            mock_redis.close.assert_awaited()


# ---------------------------------------------------------------------------
# health_check_etcd
# ---------------------------------------------------------------------------
class TestHealthCheckEtcd:
    """Tests for Health.health_check_etcd()."""

    @pytest.mark.asyncio
    async def test_raises_when_etcd_url_not_set(self):
        container = _make_container()
        env = os.environ.copy()
        env.pop("ETCD_URL", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(Exception, match="ETCD_URL"):
                await Health.health_check_etcd(container)

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        container = _make_container()
        mock_session_cm = _mock_aiohttp_session(
            response_status=200, response_text='{"health":"true"}'
        )

        with (
            patch.dict(os.environ, {"ETCD_URL": "http://etcd:2379"}),
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            await Health.health_check_etcd(container)

    @pytest.mark.asyncio
    async def test_raises_after_retries(self):
        container = _make_container()
        mock_session_cm = _mock_aiohttp_session(response_status=503)

        with (
            patch.dict(os.environ, {"ETCD_URL": "http://etcd:2379"}),
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            with pytest.raises(Exception, match="health check failed"):
                await Health.health_check_etcd(container)


# ---------------------------------------------------------------------------
# health_check_arango
# ---------------------------------------------------------------------------
class TestHealthCheckArango:
    """Tests for Health.health_check_arango()."""

    @pytest.mark.asyncio
    async def test_skips_when_data_store_not_arangodb(self):
        container = _make_container()
        with patch.dict(os.environ, {"DATA_STORE": "neo4j"}):
            await Health.health_check_arango(container)

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"username": "root", "password": "pw"}
        )

        mock_sys_db = MagicMock()
        mock_sys_db.version.return_value = "3.11.0"
        mock_client = MagicMock()
        mock_client.db.return_value = mock_sys_db
        container.arango_client = AsyncMock(return_value=mock_client)

        with patch.dict(os.environ, {"DATA_STORE": "arangodb"}):
            await Health.health_check_arango(container)

        mock_client.db.assert_called_once_with(
            "_system", username="root", password="pw"
        )

    @pytest.mark.asyncio
    async def test_raises_on_connection_failure(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"username": "root", "password": "pw"}
        )
        container.arango_client = AsyncMock(side_effect=Exception("conn error"))

        with patch.dict(os.environ, {"DATA_STORE": "arangodb"}):
            with pytest.raises(Exception, match="ArangoDB health check failed"):
                await Health.health_check_arango(container)

    @pytest.mark.asyncio
    async def test_defaults_to_arangodb(self):
        """When DATA_STORE env is not set, defaults to arangodb."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"username": "root", "password": "pw"}
        )

        mock_sys_db = MagicMock()
        mock_sys_db.version.return_value = "3.11.0"
        mock_client = MagicMock()
        mock_client.db.return_value = mock_sys_db
        container.arango_client = AsyncMock(return_value=mock_client)

        env = os.environ.copy()
        env.pop("DATA_STORE", None)
        with patch.dict(os.environ, env, clear=True):
            await Health.health_check_arango(container)


# ---------------------------------------------------------------------------
# health_check_kafka
# ---------------------------------------------------------------------------
class TestHealthCheckKafka:
    """Tests for Health.health_check_kafka()."""

    @pytest.mark.asyncio
    async def test_success_basic(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        mock_consumer = AsyncMock()
        mock_cluster = MagicMock()
        mock_cluster.topics.return_value = ["topic1"]
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster = mock_cluster
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer):
            await Health.health_check_kafka(container)

        mock_consumer.start.assert_awaited_once()
        mock_consumer.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ssl_sasl_config(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={
                "brokers": ["broker1:9092"],
                "ssl": True,
                "sasl": {"username": "user", "password": "pw", "mechanism": "SCRAM-SHA-512"},
            }
        )

        mock_consumer = AsyncMock()
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster.topics.return_value = []
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer) as mock_cls:
            await Health.health_check_kafka(container)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["security_protocol"] == "SASL_SSL"
        assert call_kwargs["sasl_mechanism"] == "SCRAM-SHA-512"
        assert call_kwargs["sasl_plain_username"] == "user"
        assert call_kwargs["sasl_plain_password"] == "pw"

    @pytest.mark.asyncio
    async def test_ssl_without_sasl(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={
                "brokers": ["broker1:9092"],
                "ssl": True,
                "sasl": {},
            }
        )

        mock_consumer = AsyncMock()
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster.topics.return_value = []
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer) as mock_cls:
            await Health.health_check_kafka(container)

        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["security_protocol"] == "SSL"

    @pytest.mark.asyncio
    async def test_no_ssl_config(self):
        """When ssl is not set, no ssl/sasl configs should be added."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        mock_consumer = AsyncMock()
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster.topics.return_value = []
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer) as mock_cls:
            await Health.health_check_kafka(container)

        call_kwargs = mock_cls.call_args.kwargs
        assert "security_protocol" not in call_kwargs

    @pytest.mark.asyncio
    async def test_raises_on_connection_error(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock(side_effect=Exception("no broker"))
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer):
            with pytest.raises(Exception, match="no broker"):
                await Health.health_check_kafka(container)

    @pytest.mark.asyncio
    async def test_metadata_failure_still_passes(self):
        """If cluster metadata fails but connection works, the check still passes."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        mock_consumer = AsyncMock()
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster.topics.side_effect = Exception("metadata error")
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer):
            await Health.health_check_kafka(container)

    @pytest.mark.asyncio
    async def test_consumer_cleanup_on_success(self):
        """Consumer should be stopped even on success."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        mock_consumer = AsyncMock()
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster.topics.return_value = []
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer):
            await Health.health_check_kafka(container)

        mock_consumer.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# health_check_redis
# ---------------------------------------------------------------------------
class TestHealthCheckRedis:
    """Tests for Health.health_check_redis()."""

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"host": "localhost", "port": 6379}
        )

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.build_redis_url", return_value="redis://localhost:6379/0"),
            patch("app.health.health.Redis") as mock_redis_cls,
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            await Health.health_check_redis(container)
            mock_redis.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_after_retries(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"host": "localhost", "port": 6379}
        )

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("refused"))
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.build_redis_url", return_value="redis://localhost:6379/0"),
            patch("app.health.health.Redis") as mock_redis_cls,
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            with pytest.raises(Exception, match="refused"):
                await Health.health_check_redis(container)

    @pytest.mark.asyncio
    async def test_closes_redis_client_on_failure(self):
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"host": "localhost", "port": 6379}
        )

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("refused"))
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.build_redis_url", return_value="redis://localhost:6379/0"),
            patch("app.health.health.Redis") as mock_redis_cls,
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            with pytest.raises(Exception):
                await Health.health_check_redis(container)
            mock_redis.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_redis_error_type(self):
        """RedisError should be caught and retried."""
        from redis.asyncio import RedisError

        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"host": "localhost", "port": 6379}
        )

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RedisError("redis error"))
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.build_redis_url", return_value="redis://localhost:6379/0"),
            patch("app.health.health.Redis") as mock_redis_cls,
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            with pytest.raises(Exception, match="Failed to connect to Redis"):
                await Health.health_check_redis(container)


# ---------------------------------------------------------------------------
# health_check_vector_db
# ---------------------------------------------------------------------------
class TestHealthCheckVectorDb:
    """Tests for Health.health_check_vector_db()."""

    @pytest.mark.asyncio
    async def test_skips_when_no_vector_db_service(self):
        container = _make_container()
        del container.vector_db_service
        await Health.health_check_vector_db(container)

    @pytest.mark.asyncio
    async def test_success(self):
        container = _make_container()
        mock_vdb = AsyncMock()
        mock_vdb.get_collections = AsyncMock(return_value=["col1"])
        container.vector_db_service = AsyncMock(return_value=mock_vdb)

        await Health.health_check_vector_db(container)
        mock_vdb.get_collections.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_on_vdb_failure(self):
        container = _make_container()
        mock_vdb = AsyncMock()
        mock_vdb.get_collections = AsyncMock(side_effect=Exception("vdb down"))
        container.vector_db_service = AsyncMock(return_value=mock_vdb)

        with pytest.raises(Exception, match="vdb down"):
            await Health.health_check_vector_db(container)

    @pytest.mark.asyncio
    async def test_raises_on_service_init_failure(self):
        """If vector_db_service() itself fails, the error propagates."""
        container = _make_container()
        container.vector_db_service = AsyncMock(side_effect=Exception("init failed"))

        with pytest.raises(Exception, match="init failed"):
            await Health.health_check_vector_db(container)


# ---------------------------------------------------------------------------
# Additional tests to cover missing branches and statements
# ---------------------------------------------------------------------------


class TestHealthCheckConnectorServiceAdditional:
    """Additional tests for partial branches in health_check_connector_service."""

    @pytest.mark.asyncio
    async def test_generic_exception_during_request(self):
        """Generic (non-aiohttp) exception is caught and retried (lines 81-83)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "http://conn:8088"}}
        )

        # session.get raises a generic Exception (not aiohttp.ClientError)
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=RuntimeError("unexpected failure"))
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="unexpected failure"):
                await Health.health_check_connector_service(container)

    @pytest.mark.asyncio
    async def test_retries_on_non_200_then_succeeds(self):
        """First attempt returns non-200, second attempt succeeds (partial branch 86)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "http://conn:8088"}}
        )

        # First call: 503, second call: 200
        call_count = 0

        def make_session_cm():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return _mock_aiohttp_session(response_status=503, response_text="Unavailable")
            return _mock_aiohttp_session(response_status=200)

        with (
            patch("app.health.health.aiohttp.ClientSession", side_effect=lambda: make_session_cm()),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _SmallRetry),
        ):
            await Health.health_check_connector_service(container)

    @pytest.mark.asyncio
    async def test_last_error_msg_none_fallback(self):
        """When no error is generated, the fallback message is used (line 91)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "http://conn:8088"}}
        )

        # Force a scenario where status is 200 on all attempts but the mock
        # still triggers the retry path. We cannot easily test line 91 in
        # isolation, but the "last_error_msg or" branch is already tested
        # by the non-200 test. Verify the default final message path:
        # Patch HealthCheckConfig to 0 retries to skip loop entirely.
        class _ZeroRetry:
            CONNECTOR_HEALTH_CHECK_MAX_RETRIES = type("V", (), {"value": 0})()
            CONNECTOR_HEALTH_CHECK_RETRY_INTERVAL_SECONDS = type("V", (), {"value": 0})()

        with patch("app.health.health.HealthCheckConfig", _ZeroRetry):
            with pytest.raises(Exception, match="failed after retries"):
                await Health.health_check_connector_service(container)


class TestHealthCheckRedisKvStoreAdditional:
    """Additional tests to cover missing branches in _health_check_redis_kv_store."""

    @pytest.mark.asyncio
    async def test_redis_error_caught(self):
        """RedisError is caught and retried (lines 152-154)."""
        from redis.asyncio import RedisError

        container = _make_container()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RedisError("connection lost"))
        mock_redis.close = AsyncMock()

        with (
            patch("app.health.health.Redis", return_value=mock_redis),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="Failed to connect to Redis KV store"):
                await Health._health_check_redis_kv_store(container)

    @pytest.mark.asyncio
    async def test_close_exception_suppressed(self):
        """Exception during redis_client.close() is suppressed (line 162)."""
        container = _make_container()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("ping fail"))
        mock_redis.close = AsyncMock(side_effect=RuntimeError("close fail"))

        with (
            patch("app.health.health.Redis", return_value=mock_redis),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="ping fail"):
                await Health._health_check_redis_kv_store(container)
            # The test passes without RuntimeError("close fail") propagating

    @pytest.mark.asyncio
    async def test_redis_client_none_skips_close(self):
        """When Redis() constructor raises, redis_client is None; close is skipped (line 159->166)."""
        container = _make_container()

        with (
            patch("app.health.health.Redis", side_effect=Exception("constructor fail")),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="constructor fail"):
                await Health._health_check_redis_kv_store(container)


class TestHealthCheckEtcdAdditional:
    """Additional tests for etcd health check missing branches (lines 214-219)."""

    @pytest.mark.asyncio
    async def test_aiohttp_client_error(self):
        """aiohttp.ClientError during etcd health check (lines 214-216)."""
        container = _make_container()

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("connect refused"))
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.dict(os.environ, {"ETCD_URL": "http://etcd:2379"}),
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="Connection error during etcd"):
                await Health.health_check_etcd(container)

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        """Generic exception during etcd health check (lines 217-219)."""
        container = _make_container()

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=RuntimeError("timeout"))
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.dict(os.environ, {"ETCD_URL": "http://etcd:2379"}),
            patch("app.health.health.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            with pytest.raises(Exception, match="etcd health check failed.*timeout"):
                await Health.health_check_etcd(container)


class TestHealthCheckKafkaAdditional:
    """Additional tests for kafka health check missing branches (lines 336-337)."""

    @pytest.mark.asyncio
    async def test_consumer_stop_exception_suppressed(self):
        """Exception stopping consumer in finally is logged but not raised (lines 336-337)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        mock_consumer = AsyncMock()
        mock_consumer._client = MagicMock()
        mock_consumer._client.cluster.topics.return_value = []
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock(side_effect=RuntimeError("stop failed"))

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer):
            # Should not raise despite stop failure
            await Health.health_check_kafka(container)

    @pytest.mark.asyncio
    async def test_consumer_none_when_start_fails(self):
        """When consumer start raises, consumer is still in scope for finally (line 332->exit)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"brokers": ["broker1:9092"]}
        )

        # Make AIOKafkaConsumer constructor succeed but start fail
        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock(side_effect=Exception("start fail"))
        mock_consumer.stop = AsyncMock()

        with patch("app.health.health.AIOKafkaConsumer", return_value=mock_consumer):
            with pytest.raises(Exception, match="start fail"):
                await Health.health_check_kafka(container)
        # Consumer.stop should have been called in finally
        mock_consumer.stop.assert_awaited()


class TestHealthCheckRedisAdditional:
    """Additional tests for redis health check missing branches (lines 375-378)."""

    @pytest.mark.asyncio
    async def test_redis_close_exception_suppressed(self):
        """Exception during redis_client.close() in finally is suppressed (line 378)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"host": "localhost", "port": 6379}
        )

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("refused"))
        mock_redis.close = AsyncMock(side_effect=RuntimeError("close error"))

        with (
            patch("app.health.health.build_redis_url", return_value="redis://localhost:6379/0"),
            patch("app.health.health.Redis") as mock_redis_cls,
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            with pytest.raises(Exception, match="refused"):
                await Health.health_check_redis(container)
            # close was called and its RuntimeError was suppressed

    @pytest.mark.asyncio
    async def test_redis_client_none_skips_close(self):
        """When Redis.from_url raises, redis_client is None; close is skipped (line 375->382)."""
        container = _make_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(
            return_value={"host": "localhost", "port": 6379}
        )

        with (
            patch("app.health.health.build_redis_url", return_value="redis://localhost:6379/0"),
            patch("app.health.health.Redis") as mock_redis_cls,
            patch("app.health.health.asyncio.sleep", new_callable=AsyncMock),
            patch("app.health.health.HealthCheckConfig", _OneRetry),
        ):
            mock_redis_cls.from_url.side_effect = Exception("from_url fail")
            with pytest.raises(Exception, match="from_url fail"):
                await Health.health_check_redis(container)
