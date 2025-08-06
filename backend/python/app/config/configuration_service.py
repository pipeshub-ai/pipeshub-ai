# src/config/configuration_service.py
import hashlib
import os
import threading
import time
from typing import Union

import dotenv
from cachetools import LRUCache

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
        """Get configuration value with LRU cache"""
        try:
            # Check cache first
            if use_cache and key in self.cache:
                self.logger.debug("📦 Cache hit for key: %s", key)
                return self.cache[key]

            value = await self.store.get_key(key)
            if value is None:
                self.logger.debug("📦 Cache miss for key: %s", key)
                return default
            self.cache[key] = value
            return value
        except Exception as e:
            self.logger.error("❌ Failed to get config %s: %s", key, str(e))
            self.logger.exception("Detailed error:")
            return default

    def _start_watch(self) -> None:
        """Start watching etcd changes in a background thread"""

        def watch_etcd() -> None:
            # Check if the store has a client attribute (for ETCD stores)
            if hasattr(self.store, 'client'):
                while self.store.client is None:
                    self.logger.debug("🔄 Waiting for ETCD client to be initialized...")
                    time.sleep(3)
                self.store.client.add_watch_prefix_callback("/", self._watch_callback)
            else:
                # For in-memory stores, we don't need to watch for external changes
                self.logger.debug("📋 Store doesn't have client attribute, skipping watch setup")

        self.watch_thread = threading.Thread(target=watch_etcd, daemon=True)
        self.watch_thread.start()

    def _watch_callback(self, event) -> None:
        """Handle etcd watch events to update cache"""
        try:
            # etcd3 WatchResponse contains events
            for evt in event.events:
                key = evt.key.decode()
                self.cache.pop(key, None)
                self.logger.debug("🔄 Cache updated for key: %s", key)
        except Exception as e:
            self.logger.error("❌ Error in watch callback: %s", str(e))
