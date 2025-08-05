import os
from logging import Logger

import aiohttp
from aiokafka import AIOKafkaConsumer  #type: ignore
from qdrant_client import QdrantClient  #type: ignore
from redis.asyncio import Redis, RedisError  #type: ignore

from app.config.configuration_service import (
    ConfigurationService,
    RedisConfig,
    config_node_constants,
)
from app.config.utils.named_constants.http_status_code_constants import HttpStatusCode


class Health:
    def __init__(self, config_service: ConfigurationService, logger: Logger) -> None:
        self.config_service = config_service
        self.logger = logger

    # system health check
    async def system_health_check(self) -> None:
        """ System health check"""
        await self.health_check_etcd()
        await self.health_check_arango()
        await self.health_check_kafka()
        await self.health_check_redis()
        await self.health_check_qdrant()

    # etcd health check
    async def health_check_etcd(self) -> None:
        """Check the health of etcd via HTTP request."""
        self.logger.info("🔍 Starting etcd health check...")
        try:
            etcd_url = os.getenv("ETCD_URL")
            if not etcd_url:
                error_msg = "ETCD_URL environment variable is not set"
                self.logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)

            self.logger.debug(f"Checking etcd health at endpoint: {etcd_url}/health")

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{etcd_url}/health") as response:
                    if response.status == HttpStatusCode.SUCCESS.value:
                        response_text = await response.text()
                        self.logger.info("✅ etcd health check passed")
                        self.logger.debug(f"etcd health response: {response_text}")
                    else:
                        error_msg = (
                            f"etcd health check failed with status {response.status}"
                        )
                        self.logger.error(f"❌ {error_msg}")
                        raise Exception(error_msg)
        except aiohttp.ClientError as e:
            error_msg = f"Connection error during etcd health check: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"etcd health check failed: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            raise

    # arango health check
    async def health_check_arango(self) -> None:
        """Check the health of ArangoDB using ArangoClient."""
        self.logger.info("🔍 Starting ArangoDB health check...")
        try:
            # Get the config_service instance first, then call get_config
            arangodb_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            username = arangodb_config["username"]
            password = arangodb_config["password"]

            self.logger.debug("Checking ArangoDB connection using ArangoClient")

            # Get the ArangoClient from the container
            client = await self.arango_client()

            # Connect to system database
            sys_db = client.db("_system", username=username, password=password)

            # Check server version to verify connection
            server_version = sys_db.version()
            self.logger.info("✅ ArangoDB health check passed")
            self.logger.debug(f"ArangoDB server version: {server_version}")

        except Exception as e:
            error_msg = f"ArangoDB health check failed: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)

    # kafka health check
    async def health_check_kafka(self) -> None:
        """Check the health of Kafka by attempting to create a connection."""
        self.logger.info("🔍 Starting Kafka health check...")
        consumer = None
        try:
            kafka_config = await self.config_service.get_config(
                config_node_constants.KAFKA.value
            )
            brokers = kafka_config["brokers"]
            self.logger.debug(f"Checking Kafka connection at: {brokers}")

            # Try to create a consumer with aiokafka
            try:
                config = {
                    "bootstrap_servers": ",".join(brokers),
                    "group_id": "health_check_test",
                    "auto_offset_reset": "earliest",
                    "enable_auto_commit": True,
                }

                # Create and start consumer to test connection
                consumer = AIOKafkaConsumer(**config)
                await consumer.start()

                # Try to get cluster metadata to verify connection
                try:
                    cluster_metadata = await consumer._client.cluster
                    available_topics = list(cluster_metadata.topics())
                    self.logger.debug(f"Available Kafka topics: {available_topics}")
                except Exception:
                    # If metadata fails, just try basic connection test
                    self.logger.debug("Basic Kafka connection test passed")

                self.logger.info("✅ Kafka health check passed")

            except Exception as e:
                error_msg = f"Failed to connect to Kafka: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)

        except Exception as e:
            error_msg = f"Kafka health check failed: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            raise
        finally:
            # Clean up consumer
            if consumer:
                try:
                    await consumer.stop()
                    self.logger.debug("Health check consumer stopped")
                except Exception as e:
                    self.logger.warning(f"Error stopping health check consumer: {e}")

    # redis health check
    async def health_check_redis(self) -> None:
        """Check the health of Redis by attempting to connect and ping."""
        self.logger.info("🔍 Starting Redis health check...")
        try:
            redis_config = await self.config_service.get_config(
                config_node_constants.REDIS.value
            )
            redis_url = f"redis://{redis_config['host']}:{redis_config['port']}/{RedisConfig.REDIS_DB.value}"
            self.logger.debug(f"Checking Redis connection at: {redis_url}")
            # Create Redis client and attempt to ping
            redis_client = Redis.from_url(redis_url, socket_timeout=5.0)
            try:
                await redis_client.ping()
                self.logger.info("✅ Redis health check passed")
            except RedisError as re:
                error_msg = f"Failed to connect to Redis: {str(re)}"
                self.logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
            finally:
                await redis_client.close()

        except Exception as e:
            error_msg = f"Redis health check failed: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            raise

    # qdrant health check
    async def health_check_qdrant(self) -> None:
        """Check the health of Qdrant via HTTP request."""
        self.logger.info("🔍 Starting Qdrant health check...")
        try:
            qdrant_config = await self.config_service.get_config(
                config_node_constants.QDRANT.value
            )
            host = qdrant_config["host"]
            port = qdrant_config["port"]
            api_key = qdrant_config["apiKey"]

            client = QdrantClient(
                host=host, port=port, prefer_grpc=True, api_key=api_key, https=False
            )

            try:
                # Fetch collections to check gRPC connectivity
                client.get_collections()
                self.logger.info("Qdrant gRPC is healthy!")
            except Exception as e:
                error_msg = f"GRPC Qdrant health check failed: {str(e)}"
                self.logger.error(f"❌ {error_msg}")
                raise
        except Exception as e:
            error_msg = f"Qdrant health check failed: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            raise
