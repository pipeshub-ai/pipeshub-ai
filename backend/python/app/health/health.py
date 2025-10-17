import os

import aiohttp  # type: ignore
from aiokafka import AIOKafkaConsumer  #type: ignore
from redis.asyncio import Redis, RedisError  #type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
from app.utils.redis_util import build_redis_url


class Health:
    @staticmethod
    async def system_health_check(container) -> None:
        """Health check method that verifies external services health"""
        logger = container.logger()
        logger.info("🔍 Starting system health check...")
        await Health.health_check_etcd(container)
        await Health.health_check_arango(container)
        await Health.health_check_kafka(container)
        await Health.health_check_redis(container)
        await Health.health_check_vector_db(container)
        logger.info("✅ External services health check passed")

    @staticmethod
    async def health_check_etcd(container) -> None:
        """Health check method that verifies etcd service health"""
        logger = container.logger()
        logger.info("🔍 Starting etcd health check...")
        try:
            etcd_url = os.getenv("ETCD_URL")
            if not etcd_url:
                error_msg = "ETCD_URL environment variable is not set"
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)

            logger.debug(f"Checking etcd health at endpoint: {etcd_url}/health")
            # TODO: remove aiohttp dependency and use http client from sources module
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{etcd_url}/health") as response:
                    if response.status == HttpStatusCode.SUCCESS.value:
                        response_text = await response.text()
                        logger.info("✅ etcd health check passed")
                        logger.debug(f"etcd health response: {response_text}")
                    else:
                        error_msg = (
                            f"etcd health check failed with status {response.status}"
                        )
                        logger.error(f"❌ {error_msg}")
                        raise Exception(error_msg)
        except aiohttp.ClientError as e:
            error_msg = f"Connection error during etcd health check: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise
        except Exception as e:
            error_msg = f"etcd health check failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise

    @staticmethod
    async def health_check_arango(container) -> None:
        """Health check method that verifies arango service health"""
        logger = container.logger()
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
        """Health check method that verifies redis service health"""
        logger = container.logger()
        logger.info("🔍 Starting Redis health check...")
        try:
            config_service : ConfigurationService = container.config_service()
            redis_config = await config_service.get_config(
                config_node_constants.REDIS.value
            )
            # Build Redis URL with password if provided
            redis_url = build_redis_url(redis_config)
            logger.debug(f"Checking Redis connection at: {redis_url}")
            # Create Redis client and attempt to ping
            redis_client = Redis.from_url(redis_url, socket_timeout=5.0)
            try:
                await redis_client.ping()
                logger.info("✅ Redis health check passed")
            except RedisError as re:
                error_msg = f"Failed to connect to Redis: {str(re)}"
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
            finally:
                await redis_client.close()

        except Exception as e:
            error_msg = f"Redis health check failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise

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
