# src/config/configuration_service.py
import hashlib
import json
import os
import threading
import time
from enum import Enum
from typing import Any

import dotenv
from cachetools import LRUCache

from app.config.encryption.encryption_service import EncryptionService
from app.config.key_value_store_factory import (
    KeyValueStoreFactory,
    StoreConfig,
    StoreType,
)
from app.config.providers.etcd3_store import Etcd3DistributedKeyValueStore

dotenv.load_dotenv()


class config_node_constants(Enum):
    """Constants for ETCD configuration paths"""

    # Service paths
    ARANGODB = "/services/arangodb"
    QDRANT = "/services/qdrant"
    REDIS = "/services/redis"
    AI_MODELS = "/services/aiModels"
    KAFKA = "/services/kafka"
    ENDPOINTS = "/services/endpoints"
    SECRET_KEYS = "/services/secretKeys"
    STORAGE = "/services/storage"

    # Non-service paths
    # LOG_LEVEL = "/logLevel"


class TokenScopes(Enum):
    """Constants for token scopes"""

    SEND_MAIL = "mail:send"
    FETCH_CONFIG = "fetch:config"
    PASSWORD_RESET = "password:reset"
    USER_LOOKUP = "user:lookup"
    TOKEN_REFRESH = "token:refresh"
    STORAGE_TOKEN = "storage:token"


class Routes(Enum):
    """Constants for routes"""

    # Token paths
    INDIVIDUAL_CREDENTIALS = "/api/v1/configurationManager/internal/connectors/individual/googleWorkspaceCredentials"
    INDIVIDUAL_REFRESH_TOKEN = (
        "/api/v1/connectors/internal/refreshIndividualConnectorToken"
    )
    BUSINESS_CREDENTIALS = "/api/v1/configurationManager/internal/connectors/business/googleWorkspaceCredentials"

    # AI Model paths
    AI_MODEL_CONFIG = "/api/v1/configurationManager/internal/aiModelsConfig"

    # Storage paths
    STORAGE_PLACEHOLDER = "/api/v1/document/internal/placeholder"
    STORAGE_DIRECT_UPLOAD = "/api/v1/document/internal/{documentId}/directUpload"
    STORAGE_UPLOAD = "/api/v1/document/internal/upload"

class WebhookConfig(Enum):
    """Constants for webhook configuration"""

    EXPIRATION_DAYS = 6
    EXPIRATION_HOURS = 23
    EXPIRATION_MINUTES = 59
    COALESCEDELAY = 60


class KafkaConfig(Enum):
    """Constants for kafka configuration"""

    CLIENT_ID_RECORDS = "record-processor"
    CLIENT_ID_MAIN = "enterprise-search"
    CLIENT_ID_LLM = "llm-configuration"


class CeleryConfig(Enum):
    """Constants for celery configuration"""

    TASK_SERIALIZER = "json"
    RESULT_SERIALIZER = "json"
    ACCEPT_CONTENT = ["json"]
    TIMEZONE = "UTC"
    ENABLE_UTC = True
    SCHEDULE = {"syncStartTime": "23:00", "syncPauseTime": "05:00"}


class RedisConfig(Enum):
    """Constants for redis configuration"""

    REDIS_DB = 0


class ConfigurationService:
    """Service to manage configuration using etcd store"""

    def __init__(self, logger):
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

        self.logger.debug("🔧 Creating ETCD store...")
        self.store = self._create_store()

        # Start watch in background
        self._start_watch()
        self.logger.debug("👀 Started ETCD watch")

        self.logger.debug("✅ ConfigurationService initialized successfully")

    def _create_store(self) -> Etcd3DistributedKeyValueStore:
        self.logger.debug("🔧 Creating ETCD store configuration...")
        self.logger.debug("ETCD URL: %s", os.getenv("ETCD_URL"))
        self.logger.debug("ETCD Timeout: %s", os.getenv("ETCD_TIMEOUT", "5.0"))
        self.logger.debug("ETCD Username: %s", os.getenv("ETCD_USERNAME", "None"))
        etcd_url = os.getenv("ETCD_URL")
        if not etcd_url:
            raise ValueError("ETCD_URL environment variable is required")

        # Remove protocol if present
        if "://" in etcd_url:
            etcd_url = etcd_url.split("://")[1]

        # Split host and port
        parts = etcd_url.split(":")
        etcd_host = parts[0]
        etcd_port = parts[1]

        config = StoreConfig(
            host=etcd_host,
            port=int(etcd_port),
            timeout=float(os.getenv("ETCD_TIMEOUT", "5.0")),
            username=os.getenv("ETCD_USERNAME", None),
            password=os.getenv("ETCD_PASSWORD", None),
        )

        def serialize(value: Any) -> bytes:
            self.logger.debug("🔄 Serializing value: %s (type: %s)", value, type(value))
            if value is None:
                self.logger.debug("⚠️ Serializing None value to empty bytes")
                return b""
            if isinstance(value, (str, int, float, bool)):
                serialized = json.dumps(value).encode("utf-8")
                self.logger.debug("✅ Serialized primitive value: %s", serialized)
                return serialized
            serialized = json.dumps(value, default=str).encode("utf-8")
            self.logger.debug("✅ Serialized complex value: %s", serialized)
            return serialized

        def deserialize(value: bytes) -> Any:
            if not value:
                self.logger.debug("⚠️ Empty bytes, returning None")
                return None
            try:
                # First try to decode as a JSON string
                decoded = value.decode("utf-8")
                # self.logger.debug("📋 Decoded UTF-8 string: %s", decoded)

                try:
                    # Try parsing as JSON
                    result = json.loads(decoded)
                    return result
                except json.JSONDecodeError:
                    # If JSON parsing fails, return the string directly
                    # self.logger.debug(
                    #     "📋 Not JSON, returning string directly")
                    return decoded

            except UnicodeDecodeError as e:
                self.logger.error("❌ Failed to decode bytes: %s", str(e))
                return None

        store = KeyValueStoreFactory.create_store(
            store_type=StoreType.ETCD3,
            serializer=serialize,
            deserializer=deserialize,
            config=config,
        )
        self.logger.debug("✅ ETCD store created successfully")
        return store

    async def load_default_config(self, overwrite: bool = False):
        """Load default configuration into etcd."""
        self.logger.debug("🔄 Starting to load default configuration")
        self.logger.debug("📂 Reading default_config.json...")

        with open("default_config.json", "r") as f:
            default_config = json.load(f)
            self.logger.debug("📋 Default config loaded: %s", default_config)

        # Process and store configuration
        for key, value in default_config.items():
            if isinstance(value, dict):
                # For nested dictionaries, store each value separately
                for sub_key, sub_value in value.items():
                    config_key = f"{key}/{sub_key}"
                    await self._store_config_value(config_key, sub_value, overwrite)
            else:
                # Store non-dict values directly
                await self._store_config_value(key, value, overwrite)

        self.logger.debug("✅ Default configuration loaded completely")

    async def _store_config_value(self, key: str, value: Any, overwrite: bool) -> bool:
        """Helper method to store a single configuration value"""
        try:
            # Check if key exists
            existing_value = await self.store.get_key(key)
            if existing_value is not None and not overwrite:
                self.logger.debug("⏭️ Skipping existing key: %s", key)
                return True

            # Convert value to JSON string
            value_json = json.dumps(value)

            EXCLUDED_KEYS = [
                config_node_constants.ENDPOINTS.value,
                config_node_constants.STORAGE.value,
            ]
            if key not in EXCLUDED_KEYS:
                # Encrypt the value
                encrypted_value = self.encryption_service.encrypt(value_json)
            else:
                encrypted_value = value_json

            self.logger.debug("🔒 Encrypted value for key %s", key)

            # Store the encrypted value
            success = await self.store.create_key(key, encrypted_value)
            if success:
                self.logger.debug("✅ Successfully stored encrypted key: %s", key)

                # Verify the stored value
                encrypted_stored_value = await self.store.get_key(key)
                if encrypted_stored_value:
                    decrypted_value = self.encryption_service.decrypt(
                        encrypted_stored_value
                    )
                    stored_value = json.loads(decrypted_value)

                    if stored_value != value:
                        self.logger.warning("⚠️ Verification failed for key: %s", key)
                        self.logger.warning("  Expected: %s", value)
                        self.logger.warning("  Got: %s", stored_value)
                        return False

                return True
            else:
                self.logger.error("❌ Failed to store key: %s", key)
                return False

        except Exception as e:
            self.logger.error(
                "❌ Failed to store config value for key %s: %s", key, str(e)
            )
            self.logger.exception("Detailed error:")
            return False

    async def has_configuration(self) -> bool:
        """Check if any configuration exists in etcd."""
        try:
            self.logger.debug("🔍 Checking for existing configuration in ETCD")
            values = await self.store.get_all_keys()
            # Check if any configuration exists
            exists = len(values) > 0
            self.logger.debug("✅ Configuration check complete. Exists: %s", exists)
            return exists
        except Exception as e:
            self.logger.error("❌ Error checking configuration existence: %s", str(e))
            self.logger.exception("Detailed error:")
            return False

    def _watch_callback(self, event):
        """Handle etcd watch events to update cache"""
        try:
            # etcd3 WatchResponse contains events
            for evt in event.events:
                key = evt.key.decode()
                self.cache.pop(key, None)
                self.logger.debug("🔄 Cache updated for key: %s", key)
        except Exception as e:
            self.logger.error("❌ Error in watch callback: %s", str(e))

    def _start_watch(self):
        """Start watching etcd changes in a background thread"""

        def watch_etcd():
            while self.store.client is None:
                self.logger.debug("🔄 Waiting for ETCD client to be initialized...")
                time.sleep(3)
            self.store.client.add_watch_prefix_callback("/", self._watch_callback)

        self.watch_thread = threading.Thread(target=watch_etcd, daemon=True)
        self.watch_thread.start()

    async def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value with LRU cache"""
        try:
            # Check cache first
            if key in self.cache:
                self.logger.debug("📦 Cache hit for key: %s", key)
                return self.cache[key]

            # If not in cache, get from etcd
            encrypted_value = await self.store.get_key(key)

            if encrypted_value is not None:
                try:
                    # Determine if value needs decryption
                    UNENCRYPTED_KEYS = [
                        config_node_constants.ENDPOINTS.value,
                        config_node_constants.STORAGE.value,
                    ]
                    needs_decryption = key not in UNENCRYPTED_KEYS

                    # Get decrypted or raw value
                    value = (
                        self.encryption_service.decrypt(encrypted_value)
                        if needs_decryption
                        else encrypted_value
                    )

                    # Parse value if it's not already a dict
                    result = json.loads(value) if not isinstance(value, dict) else value

                    # Cache and return result
                    self.cache[key] = result
                    return result

                except Exception as e:
                    self.logger.error(
                        f"❌ Failed to process value for key {key}: {str(e)}"
                    )
                    return default
            else:
                self.logger.debug(f"⚠️ No value found in ETCD for key: {key}")
                return default

        except Exception as e:
            self.logger.error("❌ Failed to get config %s: %s", key, str(e))
            self.logger.exception("Detailed error:")
            return default
