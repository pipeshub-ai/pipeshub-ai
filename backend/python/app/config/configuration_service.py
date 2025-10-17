# src/config/configuration_service.py
import hashlib
import os
import threading
import time
from typing import Union

import dotenv
from cachetools import LRUCache

from app.config.constants.service import config_node_constants
from app.config.key_value_store import KeyValueStore
from app.utils.encryption.encryption_service import EncryptionService

dotenv.load_dotenv()



class ConfigurationService:
    """Service to manage configuration using etcd store"""

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

        # Start watch in background
        self._start_watch()
        self.logger.debug("👀 Started ETCD watch")

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
                return {
                    "host": brokers_list[0].split(":")[0] if ":" in brokers_list[0] else brokers_list[0],
                    "port": int(brokers_list[0].split(":")[1]) if ":" in brokers_list[0] else 9092,
                    "topic": "records",
                    "bootstrap_servers": brokers_list,
                    "brokers": brokers_list
                }
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
        """Start watching etcd changes in a background thread"""

        def watch_etcd() -> None:
            # Expect store implementations to expose .client directly
            if hasattr(self.store, 'client'):
                # Wait for client to be ready
                while getattr(self.store, 'client', None) is None:
                    time.sleep(3)
                try:
                    self.store.client.add_watch_prefix_callback("/", self._watch_callback)
                    self.logger.debug("👀 ETCD prefix watch registered for cache invalidation")
                except Exception as e:
                    self.logger.error("❌ Failed to register ETCD watch: %s", str(e))
            else:
                self.logger.debug("📋 Store doesn't expose an ETCD client; skipping watch setup")

        self.watch_thread = threading.Thread(target=watch_etcd, daemon=True)
        self.watch_thread.start()

    async def set_config(self, key: str, value: Union[str, int, float, bool, dict, list]) -> bool:
        """Set configuration value with optional encryption"""
        try:

            # Store in etcd
            try:
                await self.store.create_key(key, value, overwrite=True)
                success = True
            except Exception as store_error:
                self.logger.error("❌ Failed to create key in store: %s", str(store_error))
                success = False

            if success:
                # Update cache with value
                self.cache[key] = value
                self.logger.debug("✅ Successfully set config for key: %s", key)
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

            # Update in etcd
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
            else:
                self.logger.error("❌ Failed to delete config for key: %s", key)

            return success

        except Exception as e:
            self.logger.error("❌ Failed to delete config %s: %s", key, str(e))
            return False

    def _watch_callback(self, event) -> None:
        """Handle etcd watch events to update cache"""
        try:
            # etcd3 WatchResponse contains events
            for evt in event.events:
                key = evt.key.decode()
                self.cache.pop(key, None)
        except Exception as e:
            self.logger.error("❌ Error in watch callback: %s", str(e))
