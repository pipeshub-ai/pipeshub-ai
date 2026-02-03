import asyncio
import os
import ssl

import aiohttp  # type: ignore
from aiokafka import AIOKafkaConsumer  #type: ignore
from redis.asyncio import Redis, RedisError  #type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    HealthCheckConfig,
    config_node_constants,
)
from app.utils.redis_util import build_redis_url


class Health:

    @staticmethod
    async def system_health_check(container) -> None:
        """Health check method that verifies external services health"""
        logger = container.logger()
        logger.info("🔍 Starting system health check...")
        await Health.health_check_kv_store(container)
        await Health.health_check_arango(container)
        await Health.health_check_kafka(container)
        await Health.health_check_redis(container)
        await Health.health_check_vector_db(container)
        logger.info("✅ External services health check passed")

    @staticmethod
    async def health_check_connector_service(container) -> None:
        """Health check connector service via /health with simple retry logic.

        Polls up to CONNECTOR_HEALTH_CHECK_MAX_RETRIES times with a configurable interval
        before raising an exception.
        """
        logger = container.logger()
        logger.info("🔍 Starting Connector service health check...")

        # Get connector endpoint from config service
        config_service: ConfigurationService = container.config_service()
        endpoints = await config_service.get_config(config_node_constants.ENDPOINTS.value)
        connector_endpoint = (
            (endpoints or {}).get("connectors", {}).get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
        )
        connector_url = f"{connector_endpoint}/health"

        max_retries = HealthCheckConfig.CONNECTOR_HEALTH_CHECK_MAX_RETRIES.value
        retry_interval = HealthCheckConfig.CONNECTOR_HEALTH_CHECK_RETRY_INTERVAL_SECONDS.value
        last_error_msg = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    f"Checking connector service health at endpoint: {connector_url} "
                    f"(attempt {attempt}/{max_retries})"
                )

                # TODO: remove aiohttp dependency and use http client from sources module
                async with aiohttp.ClientSession() as session:
                    async with session.get(connector_url) as response:
                        if response.status == HttpStatusCode.SUCCESS.value:
                            logger.info("✅ Connector service health check passed")
                            return
                        else:
                            error_body = await response.text()
                            last_error_msg = (
                                f"Connector service health check failed with status "
                                f"{response.status}: {error_body}"
                            )
                            logger.warning(f"❌ {last_error_msg}")

            except aiohttp.ClientError as e:
                last_error_msg = (
                    f"Connection error during connector service health check: {str(e)}"
                )
                logger.warning(f"⚠️ {last_error_msg}")
            except Exception as e:
                last_error_msg = f"Connector service health check failed: {str(e)}"
                logger.error(f"❌ {last_error_msg}")

            # If not the last attempt, wait before retrying
            if attempt < max_retries:
                logger.info(f"⏳ Retrying connector service health check in {retry_interval} seconds...")
                await asyncio.sleep(retry_interval)

        # All attempts failed
        final_msg = last_error_msg or "Connector service health check failed after retries"
        logger.error(f"❌ {final_msg}")
        raise Exception(final_msg)

    @staticmethod
    async def health_check_kv_store(container) -> None:
        """Health check method that verifies KV store health based on KV_STORE_TYPE.

        Checks either etcd or Redis depending on the configured store type.
        """
        logger = container.logger()
        kv_store_type = os.getenv("KV_STORE_TYPE", "etcd").lower()
        logger.info(f"🔍 Starting KV store health check (type: {kv_store_type})...")

        if kv_store_type == "redis":
            await Health._health_check_redis_kv_store(container)
        else:
            # TODO: Remove health_check_etcd when all deployments migrate to Redis KV store
            await Health.health_check_etcd(container)

    @staticmethod
    async def _health_check_redis_kv_store(container) -> None:
        """Health check method that verifies Redis KV store health with retry logic.

        Uses environment variables directly to avoid circular dependency
        with config service (which depends on Redis as KV store).

        Retries connection with linear 1 second backoff before failing.
        """
        logger = container.logger()
        logger.info("🔍 Starting Redis KV store health check...")

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD") or None
        redis_db = int(os.getenv("REDIS_DB", "0"))

        max_retries = HealthCheckConfig.CONNECTOR_HEALTH_CHECK_MAX_RETRIES.value
        last_error_msg = None

        for attempt in range(1, max_retries + 1):
            redis_client = None
            try:
                logger.debug(
                    f"Checking Redis KV store at: {redis_host}:{redis_port}, db={redis_db} "
                    f"(attempt {attempt}/{max_retries})"
                )

                # Create Redis client and attempt to ping
                redis_client = Redis(
                    host=redis_host,
                    port=redis_port,
                    password=redis_password,
                    db=redis_db,
                    socket_timeout=5.0,
                )

                await redis_client.ping()
                logger.info("✅ Redis KV store health check passed")
                return

            except RedisError as re:
                last_error_msg = f"Failed to connect to Redis KV store: {str(re)}"
                logger.warning(f"⚠️ {last_error_msg}")
            except Exception as e:
                last_error_msg = f"Redis KV store health check failed: {str(e)}"
                logger.warning(f"⚠️ {last_error_msg}")
            finally:
                if redis_client:
                    try:
                        await redis_client.close()
                    except Exception:
                        pass

            # If not the last attempt, wait before retrying with linear backoff
            if attempt < max_retries:
                logger.info("⏳ Retrying Redis KV store health check in 1 second...")
                await asyncio.sleep(1)

        # All attempts failed
        final_msg = last_error_msg or "Redis KV store health check failed after retries"
        logger.error(f"❌ {final_msg}")
        raise Exception(final_msg)

    @staticmethod
    async def health_check_etcd(container) -> None:
        """Health check method that verifies etcd service health with retry logic.

        TODO: Remove this method when all deployments migrate to Redis KV store.
        """
        logger = container.logger()
        logger.info("🔍 Starting etcd health check...")

        etcd_url = os.getenv("ETCD_URL")
        if not etcd_url:
            error_msg = "ETCD_URL environment variable is not set"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)

        max_retries = HealthCheckConfig.CONNECTOR_HEALTH_CHECK_MAX_RETRIES.value
        last_error_msg = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    f"Checking etcd health at endpoint: {etcd_url}/health "
                    f"(attempt {attempt}/{max_retries})"
                )

                # TODO: remove aiohttp dependency and use http client from sources module
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{etcd_url}/health", timeout=aiohttp.ClientTimeout(total=5.0)) as response:
                        if response.status == HttpStatusCode.SUCCESS.value:
                            response_text = await response.text()
                            logger.info("✅ etcd health check passed")
                            logger.debug(f"etcd health response: {response_text}")
                            return
                        else:
                            last_error_msg = (
                                f"etcd health check failed with status {response.status}"
                            )
                            logger.warning(f"⚠️ {last_error_msg}")

            except aiohttp.ClientError as e:
                last_error_msg = f"Connection error during etcd health check: {str(e)}"
                logger.warning(f"⚠️ {last_error_msg}")
            except Exception as e:
                last_error_msg = f"etcd health check failed: {str(e)}"
                logger.warning(f"⚠️ {last_error_msg}")

            # If not the last attempt, wait before retrying with linear backoff
            if attempt < max_retries:
                logger.info("⏳ Retrying etcd health check in 1 second...")
                await asyncio.sleep(1)

        # All attempts failed
        final_msg = last_error_msg or "etcd health check failed after retries"
        logger.error(f"❌ {final_msg}")
        raise Exception(final_msg)

    @staticmethod
    async def health_check_arango(container) -> None:
        """Health check method that verifies arango service health"""
        logger = container.logger()

        # Skip ArangoDB health check if using a different graph database
        data_store = os.getenv("DATA_STORE", "neo4j").lower()
        if data_store != "arangodb":
            logger.info(f"⏭️ Skipping ArangoDB health check (DATA_STORE={data_store})")
            return

        logger.info("🔍 Starting ArangoDB health check...")
        try:
            # Get the config_service instance first, then call get_config
            config_service = container.config_service()
            arangodb_config = await config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            username = arangodb_config["username"]
            password = arangodb_config["password"]

            logger.debug("Checking ArangoDB connection using ArangoClient")

            # Get the ArangoClient from the container
            client = await container.arango_client()

            # Connect to system database
            sys_db = client.db("_system", username=username, password=password)

            # Check server version to verify connection
            server_version = sys_db.version()
            logger.info("✅ ArangoDB health check passed")
            logger.debug(f"ArangoDB server version: {server_version}")

        except Exception as e:
            error_msg = f"ArangoDB health check failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)

    @staticmethod
    async def health_check_kafka(container) -> None:
        """Health check method that verifies kafka service health"""
        logger = container.logger()
        logger.info("🔍 Starting Kafka health check...")
        consumer = None
        try:
            kafka_config = await container.config_service().get_config(
                config_node_constants.KAFKA.value
            )
            brokers = kafka_config["brokers"]
            logger.debug(f"Checking Kafka connection at: {brokers}")

            # Try to create a consumer with aiokafka
            try:
                config = {
                    "bootstrap_servers": ",".join(brokers),  # aiokafka uses bootstrap_servers
                    "group_id": "health_check_test",
                    "auto_offset_reset": "earliest",
                    "enable_auto_commit": True,
                }

                # Add SSL/SASL configuration for AWS MSK
                if kafka_config.get("ssl"):
                    config["ssl_context"] = ssl.create_default_context()
                    sasl_config = kafka_config.get("sasl", {})
                    if sasl_config.get("username"):
                        config["security_protocol"] = "SASL_SSL"
                        config["sasl_mechanism"] = sasl_config.get("mechanism", "SCRAM-SHA-512").upper()
                        config["sasl_plain_username"] = sasl_config["username"]
                        config["sasl_plain_password"] = sasl_config["password"]
                    else:
                        config["security_protocol"] = "SSL"

                # Create and start consumer to test connection
                consumer = AIOKafkaConsumer(**config)
                await consumer.start()

                # Try to get cluster metadata to verify connection
                try:
                    # TODO: remove this private access
                    cluster_metadata = consumer._client.cluster
                    available_topics = list(cluster_metadata.topics())
                    logger.debug(f"Available Kafka topics: {available_topics}")
                except Exception as e:
                    logger.warning(f"Error getting Kafka cluster metadata: {str(e)}")
                    # If metadata fails, just try basic connection test
                    logger.debug("Basic Kafka connection test passed")

                logger.info("✅ Kafka health check passed")

            except Exception as e:
                error_msg = f"Failed to connect to Kafka: {str(e)}"
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)

        except Exception as e:
            error_msg = f"Kafka health check failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise
        finally:
            # Clean up consumer
            if consumer:
                try:
                    await consumer.stop()
                    logger.debug("Health check consumer stopped")
                except Exception as e:
                    logger.warning(f"Error stopping health check consumer: {e}")

    @staticmethod
    async def health_check_redis(container) -> None:
        """Health check method that verifies redis service health with retry logic."""
        logger = container.logger()
        logger.info("🔍 Starting Redis health check...")

        max_retries = HealthCheckConfig.CONNECTOR_HEALTH_CHECK_MAX_RETRIES.value
        last_error_msg = None

        for attempt in range(1, max_retries + 1):
            redis_client = None
            try:
                config_service: ConfigurationService = container.config_service()
                redis_config = await config_service.get_config(
                    config_node_constants.REDIS.value
                )
                # Build Redis URL with password if provided
                redis_url = build_redis_url(redis_config)
                logger.debug(
                    f"Checking Redis connection at: {redis_url} "
                    f"(attempt {attempt}/{max_retries})"
                )

                # Create Redis client and attempt to ping
                redis_client = Redis.from_url(redis_url, socket_timeout=5.0)
                await redis_client.ping()
                logger.info("✅ Redis health check passed")
                return

            except RedisError as re:
                last_error_msg = f"Failed to connect to Redis: {str(re)}"
                logger.warning(f"⚠️ {last_error_msg}")
            except Exception as e:
                last_error_msg = f"Redis health check failed: {str(e)}"
                logger.warning(f"⚠️ {last_error_msg}")
            finally:
                if redis_client:
                    try:
                        await redis_client.close()
                    except Exception:
                        pass

            # If not the last attempt, wait before retrying with linear backoff
            if attempt < max_retries:
                logger.info("⏳ Retrying Redis health check in 1 second...")
                await asyncio.sleep(1)

        # All attempts failed
        final_msg = last_error_msg or "Redis health check failed after retries"
        logger.error(f"❌ {final_msg}")
        raise Exception(final_msg)

    @staticmethod
    async def health_check_vector_db(container) -> None:
        """Health check method that verifies vector db service health"""
        logger = container.logger()
        logger.info("🔍 Starting vector db service health check...")
        try:
            # Check if vector_db_service is available in the container
            if not hasattr(container, 'vector_db_service'):
                logger.info("⚠️ vector_db_service not available in this container, skipping health check")
                return

            vector_db_service = await container.vector_db_service()
            try:
                # Fetch collections to check connectivity
                await vector_db_service.get_collections()
                logger.info("✅ vector db service is healthy!")
            except Exception as e:
                error_msg = f"vector db service health check failed: {str(e)}"
                logger.error(f"❌ {error_msg}")
                raise
        except Exception as e:
            error_msg = f"vector db service health check failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise
