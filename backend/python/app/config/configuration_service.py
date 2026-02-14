# src/config/configuration_service.py
import asyncio
import hashlib
import os
import threading
import time
from typing import Optional, Union

import dotenv
from cachetools import LRUCache

from app.config.constants.service import config_node_constants
from app.config.key_value_store import KeyValueStore
from app.utils.encryption.encryption_service import EncryptionService

dotenv.load_dotenv()



class ConfigurationService:
    """Service to manage configuration using etcd or Redis store with caching."""

    def __init__(self, logger, key_value_store: KeyValueStore) -> None:
        self.logger = logger
        self.logger.debug("🔧 Initializing ConfigurationService")

        # Get and hash the secret key to ensure 32 bytes
        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable is required")

        # Hash the secret key to get exactly 32 bytes and convert to hex
        hashed_key = hashlib.sha256(secret_key.encode()).digest()
        hex_key = hashed_key.hex()
        self.logger.debug("🔑 Secret key hashed to 32 bytes and converted to hex")

        self.encryption_service = EncryptionService.get_instance(
            "aes-256-gcm", hex_key, logger
        )
        self.logger.debug("🔐 Initialized EncryptionService")

        # Initialize LRU cache
        self.cache = LRUCache(maxsize=1000)
        self.logger.debug("📦 Initialized LRU cache with max size 1000")

        self.store = key_value_store

        # Determine store type from environment
        self._kv_store_type = os.getenv("KV_STORE_TYPE", "etcd").lower()
        self.logger.debug("📋 KV store type: %s", self._kv_store_type)

        # Redis Pub/Sub subscription task (for Redis store)
        self._pubsub_task: Optional[asyncio.Task] = None

        # Start watch in background (etcd) or schedule Pub/Sub setup (Redis)
        self._start_watch()

        self.logger.debug("✅ ConfigurationService initialized successfully")

    async def get_config(self, key: str, default: Union[str, int, float, bool, dict, list, None] = None, use_cache: bool = True) -> Union[str, int, float, bool, dict, list, None]:
        """Get configuration value with LRU cache and environment variable fallback"""
        try:
            # Check cache first
            if use_cache and key in self.cache:
                self.logger.debug("📦 Cache hit for key: %s", key)
                return self.cache[key]

            value = await self.store.get_key(key)
            if value is None:
                # Try environment variable fallback for specific services
                env_fallback = self._get_env_fallback(key)
                if env_fallback is not None:
                    self.logger.debug("📦 Using environment variable fallback for key: %s", key)
                    self.cache[key] = env_fallback
                    return env_fallback

                self.logger.debug("📦 Cache miss for key: %s", key)
                return default
            self.cache[key] = value
            return value
        except Exception as e:
            self.logger.error("❌ Failed to get config %s: %s", key, str(e))
            # Try environment variable fallback on error
            env_fallback = self._get_env_fallback(key)
            if env_fallback is not None:
                self.logger.debug("📦 Using environment variable fallback due to error for key: %s", key)
                return env_fallback
            return default

    def _get_env_fallback(self, key: str) -> Union[dict, None]:
        """Get environment variable fallback for specific configuration keys"""
        if key == config_node_constants.KAFKA.value:
            # Kafka configuration fallback
            kafka_brokers = os.getenv("KAFKA_BROKERS")
            if kafka_brokers:
                brokers_list = [broker.strip() for broker in kafka_brokers.split(",")]
                config = {
                    "host": brokers_list[0].split(":")[0] if ":" in brokers_list[0] else brokers_list[0],
                    "port": int(brokers_list[0].split(":")[1]) if ":" in brokers_list[0] else 9092,
                    "topic": "records",
                    "bootstrap_servers": brokers_list,
                    "brokers": brokers_list,
                    "ssl": os.getenv("KAFKA_SSL", "").lower() == "true"
                }
                # Add SASL config if username is provided
                kafka_username = os.getenv("KAFKA_USERNAME")
                if kafka_username:
                    config["sasl"] = {
                        "mechanism": os.getenv("KAFKA_SASL_MECHANISM", "scram-sha-512"),
                        "username": kafka_username,
                        "password": os.getenv("KAFKA_PASSWORD", "")
                    }
                return config
        elif key == config_node_constants.ARANGODB.value:
            # ArangoDB configuration fallback
            arango_url = os.getenv("ARANGO_URL")
            if arango_url:
                return {
                    "url": arango_url,
                    "username": os.getenv("ARANGO_USERNAME", "root"),
                    "password": os.getenv("ARANGO_PASSWORD"),
                    "db": os.getenv("ARANGO_DB_NAME", "es")
                }
        elif key == config_node_constants.REDIS.value:
            # Redis configuration fallback
            redis_host = os.getenv("REDIS_HOST")
            if redis_host:
                redis_password = os.getenv("REDIS_PASSWORD", "")
                return {
                    "host": redis_host,
                    "port": int(os.getenv("REDIS_PORT", "6379")),
                    "password": redis_password if redis_password and redis_password.strip() else None
                }
        elif key == config_node_constants.QDRANT.value:
            # Qdrant configuration fallback
            qdrant_host = os.getenv("QDRANT_HOST")
            if qdrant_host:
                return {
                    "host": qdrant_host,
                    "grpcPort": int(os.getenv("QDRANT_GRPC_PORT", "6333")),
                    "apiKey": os.getenv("QDRANT_API_KEY", "qdrant")
                }
        return None

    def _start_watch(self) -> None:
        """Start watching for changes to invalidate cache.

        For etcd: Uses etcd's native watch mechanism with prefix callback.
        For Redis: Uses Redis Pub/Sub for cross-process cache invalidation.
        """
        if self._kv_store_type == "redis":
            self._start_redis_pubsub()
        else:
            # TODO: Remove etcd watch when all deployments migrate to Redis KV store
            self._start_etcd_watch()

    def _start_etcd_watch(self) -> None:
        """Start watching etcd changes in a background thread.

        TODO: Remove this method when all deployments migrate to Redis KV store.
        """

        def watch_etcd() -> None:
            # Expect store implementations to expose .client directly
            if hasattr(self.store, 'client'):
                # Wait for client to be ready
                while getattr(self.store, 'client', None) is None:
                    #TODO: The time.sleep(3) call in the watch_etcd function can introduce a significant delay during startup if the etcd client is not immediately available.
                    # fix it
                    time.sleep(3)
                try:
                    self.store.client.add_watch_prefix_callback("/", self._etcd_watch_callback)
                    self.logger.debug("👀 etcd prefix watch registered for cache invalidation")
                except Exception as e:
                    self.logger.error("❌ Failed to register etcd watch: %s", str(e))
            else:
                self.logger.debug("📋 Store doesn't expose an etcd client; skipping watch setup")

        self.watch_thread = threading.Thread(target=watch_etcd, daemon=True)
        self.watch_thread.start()

    def _start_redis_pubsub(self) -> None:
        """Start Redis Pub/Sub subscription for cache invalidation."""

        # Migration flag key (same as in Node.js kvStoreMigration.service.ts)
        # This key is stored as plain text, so we read it directly from Redis
        migration_flag_key = "/migrations/etcd_to_redis"

        async def check_migration_flag_direct(redis_client, key_prefix: str) -> bool:
            """Check migration flag directly from Redis without encryption.

            The migration flag is stored by Node.js as plain 'true' string,
            so we need to read it directly without going through EncryptedKeyValueStore.
            """
            try:
                full_key = f"{key_prefix}{migration_flag_key}"
                value = await redis_client.get(full_key)
                if value is not None:
                    # Value might be bytes
                    if isinstance(value, bytes):
                        value = value.decode("utf-8")
                    return value == "true"
                return False
            except Exception as e:
                self.logger.debug("Could not check migration flag directly: %s", str(e))
                return False

        def start_subscription() -> None:
            # Wait for client to be ready
            while getattr(self.store, 'client', None) is None:
                time.sleep(1)

            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Check if migration has been completed by reading directly from Redis
                # This handles the race condition where migration completes before
                # this service starts and subscribes to the Pub/Sub channel
                try:
                    # Get the underlying store's Redis client and key prefix
                    underlying_store = getattr(self.store, 'store', None)
                    if underlying_store:
                        redis_client = getattr(underlying_store, 'client', None)
                        key_prefix = getattr(underlying_store, 'key_prefix', 'pipeshub:kv:')
                        if redis_client:
                            migration_completed = loop.run_until_complete(
                                check_migration_flag_direct(redis_client, key_prefix)
                            )
                            if migration_completed:
                                self.clear_cache()
                                self.logger.info(
                                    "📦 Cache cleared on startup - migration from etcd to Redis was completed"
                                )
                except Exception as e:
                    self.logger.debug("Could not check migration flag: %s", str(e))

                # Subscribe to cache invalidation channel
                self._pubsub_task = loop.run_until_complete(
                    self.store.subscribe_cache_invalidation(self._redis_invalidation_callback)
                )
                self.logger.debug("👀 Redis Pub/Sub subscription registered for cache invalidation")

                # Clear cache after subscription is active to ensure any values
                # cached during the startup window are invalidated
                self.clear_cache()
                self.logger.debug("📦 Cache cleared after Pub/Sub subscription established")

                # Keep the loop running to process messages
                loop.run_until_complete(self._pubsub_task)
            except asyncio.CancelledError:
                self.logger.debug("Redis Pub/Sub subscription cancelled")
            except Exception as e:
                self.logger.error("❌ Failed to setup Redis Pub/Sub: %s", str(e))
            finally:
                loop.close()

        self.watch_thread = threading.Thread(target=start_subscription, daemon=True)
        self.watch_thread.start()

    def _redis_invalidation_callback(self, key: str) -> None:
        """Handle Redis Pub/Sub cache invalidation messages.

        Special keys:
        - __CLEAR_ALL__: Clears the entire cache (used after migration)
        """
        try:
            if key == "__CLEAR_ALL__":
                self.clear_cache()
                self.logger.info("📦 Entire cache cleared via Pub/Sub message")
            else:
                self.cache.pop(key, None)
                self.logger.debug("📦 Cache invalidated for key: %s", key)
        except Exception as e:
            self.logger.error("❌ Error in Redis cache invalidation callback: %s", str(e))

    def clear_cache(self) -> None:
        """Clear the entire in-memory LRU cache.

        This should be called after migration from etcd to Redis to ensure
        all services pick up the new configuration values.
        """
        try:
            self.cache.clear()
            self.logger.info("📦 In-memory configuration cache cleared")
        except Exception as e:
            self.logger.error("❌ Failed to clear cache: %s", str(e))

    async def set_config(self, key: str, value: Union[str, int, float, bool, dict, list]) -> bool:
        """Set configuration value with optional encryption"""
        try:
            self.logger.info("📝 set_config called for key: %s (store type: %s)", key, type(self.store).__name__)

            # Store in KV store
            try:
                await self.store.create_key(key, value, overwrite=True)
                success = True
            except Exception as store_error:
                self.logger.error("❌ Failed to create key in store: %s", str(store_error))
                success = False

            if success:
                # Update cache with value
                self.cache[key] = value
                self.logger.info("✅ Successfully set config for key: %s, now publishing cache invalidation", key)

                # Publish cache invalidation for other processes (Redis only)
                await self._publish_cache_invalidation(key)
            else:
                self.logger.error("❌ Failed to set config for key: %s", key)

            return success

        except Exception as e:
            self.logger.error("❌ Failed to set config %s: %s", key, str(e))
            return False

    async def update_config(self, key: str, value: Union[str, int, float, bool, dict, list]) -> bool:
        """Update configuration value with optional encryption"""
        try:
            # Check if key exists
            existing_value = await self.store.get_key(key)
            if existing_value is None:
                self.logger.warning("⚠️ Key %s does not exist, creating new key", key)
                return await self.set_config(key, value)

            # Update in KV store
            try:
                await self.store.update_value(key, value)
                success = True
            except Exception as store_error:
                self.logger.error("❌ Failed to update key in store: %s", str(store_error))
                success = False

            if success:
                # Update cache with value
                self.cache[key] = value
                self.logger.debug("✅ Successfully updated config for key: %s", key)

                # Publish cache invalidation for other processes (Redis only)
                await self._publish_cache_invalidation(key)
            else:
                self.logger.error("❌ Failed to update config for key: %s", key)

            return success

        except Exception as e:
            self.logger.error("❌ Failed to update config %s: %s", key, str(e))
            return False

    async def delete_config(self, key: str) -> bool:
        """Delete configuration value"""
        try:
            success = await self.store.delete_key(key)

            if success:
                # Remove from cache
                self.cache.pop(key, None)
                self.logger.debug("✅ Successfully deleted config for key: %s", key)

                # Publish cache invalidation for other processes (Redis only)
                await self._publish_cache_invalidation(key)
            else:
                self.logger.error("❌ Failed to delete config for key: %s", key)

            return success

        except Exception as e:
            self.logger.error("❌ Failed to delete config %s: %s", key, str(e))
            return False

    async def _publish_cache_invalidation(self, key: str) -> None:
        """Publish cache invalidation message for cross-process cache sync.

        Only publishes when using Redis as the KV store.
        For etcd, the watch mechanism handles cross-process invalidation.
        """
        if self._kv_store_type != "redis":
            self.logger.debug("⏭️ Skipping cache invalidation publish: KV store type is '%s', not 'redis'", self._kv_store_type)
            return

        try:
            if hasattr(self.store, 'publish_cache_invalidation'):
                self.logger.info("📤 Publishing cache invalidation for key: %s", key)
                await self.store.publish_cache_invalidation(key)
            else:
                self.logger.warning("⚠️ Store %s does not have publish_cache_invalidation method", type(self.store).__name__)
        except Exception as e:
            # Log but don't fail the operation - cache will eventually be consistent
            self.logger.warning("⚠️ Failed to publish cache invalidation for key %s: %s", key, str(e))

    async def close(self) -> None:
        """Shut down the configuration service and release resources."""
        if not hasattr(self, 'store') or self.store is None:
            return
        try:
            await self.store.close()
            self.logger.info("✅ ConfigurationService closed successfully")
        except Exception as e:
            self.logger.warning("Error closing ConfigurationService: %s", str(e))

    def _etcd_watch_callback(self, event) -> None:
        """Handle etcd watch events to update cache.

        TODO: Remove this method when all deployments migrate to Redis KV store.
        """
        try:
            # etcd3 WatchResponse contains events
            for evt in event.events:
                key = evt.key.decode()
                if key == "__CLEAR_ALL__":
                    self.clear_cache()
                    self.logger.info("📦 Entire cache cleared via etcd watch")
                else:
                    self.cache.pop(key, None)
        except Exception as e:
            self.logger.error("❌ Error in etcd watch callback: %s", str(e))
