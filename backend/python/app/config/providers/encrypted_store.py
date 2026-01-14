import hashlib
import json
import os
from typing import Callable, Dict, Generic, List, Optional, TypeVar, Union

import dotenv  # type: ignore

from app.config.constants.service import config_node_constants
from app.config.constants.store_type import StoreType
from app.config.errors import (
    ConfigurationInvalidError,
    ConfigurationMigrationError,
    ConfigurationNotFoundError,
)
from app.config.key_value_store import KeyValueStore
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
from app.config.migration.kv_store_migration import check_and_migrate_if_needed
from app.utils.encryption.encryption_service import EncryptionService

dotenv.load_dotenv()

T = TypeVar("T")

# Track if migration has been checked (singleton pattern for migration)
_migration_checked = False


class EncryptedKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    Configurable encrypted key-value store that supports multiple backends (Redis, etcd).

    The backend is determined by the KV_STORE_TYPE environment variable:
    - 'redis': Uses Redis as the backend
    - 'etcd': Uses etcd as the backend (default)
    """

    def __init__(
        self,
        logger,
    ) -> None:
        self.logger = logger
        self._client = None

        self.logger.debug("Initializing EncryptedKeyValueStore")

        # Get and hash the secret key to ensure 32 bytes
        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable is required")

        # Hash the secret key to get exactly 32 bytes and convert to hex
        hashed_key = hashlib.sha256(secret_key.encode()).digest()
        hex_key = hashed_key.hex()
        self.logger.debug("Secret key hashed to 32 bytes and converted to hex")

        self.encryption_service = EncryptionService.get_instance(
            "aes-256-gcm", hex_key, logger
        )
        self.logger.debug("Initialized EncryptionService")

        # Determine store type from environment
        store_type_str = os.getenv("KV_STORE_TYPE", "etcd").lower()
        self.logger.debug("KV_STORE_TYPE: %s", store_type_str)

        # Store the store type for migration check
        self._store_type_str = store_type_str

        self.logger.debug("Creating key-value store...")
        self.store = self._create_store(store_type_str)

        self.logger.debug("KeyValueStore initialized successfully")

    async def ensure_migrated(self) -> None:
        """
        Ensure data migration from etcd to Redis has been performed if using Redis.
        This should be called during application startup.

        Raises:
            ConfigurationMigrationError: If migration fails or data is missing
        """
        global _migration_checked

        # Only check migration once per application lifecycle
        if _migration_checked:
            return

        # Only need to check migration when using Redis
        if self._store_type_str != "redis":
            _migration_checked = True
            return

        self.logger.info("Checking if etcd to Redis migration is needed...")

        result = await check_and_migrate_if_needed()

        if result is None:
            # Redis already has data, no migration needed
            self.logger.info("Redis already has configuration data. No migration needed.")
            _migration_checked = True
            return

        if result.success:
            self.logger.info(
                "Migration completed successfully. Migrated %d keys.",
                len(result.migrated_keys),
            )
            _migration_checked = True
            return

        # Migration failed or no data available
        self.logger.error(
            "CONFIGURATION MIGRATION ERROR: %s. "
            "Please ensure etcd is running and restart the application, "
            "or reconfigure all settings manually.",
            result.error,
        )
        raise ConfigurationMigrationError(
            message=result.error or "Migration failed",
            failed_keys=result.failed_keys,
        )

    @property
    def client(self) -> object:
        """Expose the underlying client for watchers and diagnostics."""
        return getattr(self.store, "client", None)

    def _create_store(self, store_type_str: str) -> KeyValueStore:
        """Create the appropriate key-value store based on configuration."""

        def serialize(value: Union[str, int, float, bool, Dict, list, None]) -> bytes:
            if value is None:
                return b""
            if isinstance(value, (str, int, float, bool)):
                return json.dumps(value).encode("utf-8")
            return json.dumps(value, default=str).encode("utf-8")

        def deserialize(value: bytes) -> Union[str, int, float, bool, dict, list, None]:
            if not value:
                return None
            try:
                decoded = value.decode("utf-8")
                try:
                    return json.loads(decoded)
                except json.JSONDecodeError:
                    return decoded
            except UnicodeDecodeError as e:
                self.logger.error("Failed to decode bytes: %s", str(e))
                return None

        if store_type_str == "redis":
            return self._create_redis_store(serialize, deserialize)
        else:
            return self._create_etcd_store(serialize, deserialize)

    def _create_redis_store(self, serialize, deserialize) -> KeyValueStore:
        """Create a Redis-backed key-value store."""
        self.logger.debug("Creating Redis store configuration...")

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD", None)
        redis_db = int(os.getenv("REDIS_DB", "0"))
        redis_key_prefix = os.getenv("REDIS_KV_PREFIX", "kv:")

        self.logger.debug("Redis Host: %s", redis_host)
        self.logger.debug("Redis Port: %s", redis_port)
        self.logger.debug("Redis DB: %s", redis_db)
        self.logger.debug("Redis Key Prefix: %s", redis_key_prefix)

        config = StoreConfig(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            db=redis_db,
            key_prefix=redis_key_prefix,
            timeout=float(os.getenv("REDIS_TIMEOUT", "10.0")),
        )

        store = KeyValueStoreFactory.create_store(
            store_type=StoreType.REDIS,
            serializer=serialize,
            deserializer=deserialize,
            config=config,
        )
        self.logger.debug("Redis store created successfully")
        return store

    def _create_etcd_store(self, serialize, deserialize) -> KeyValueStore:
        """Create an etcd-backed key-value store."""
        self.logger.debug("Creating ETCD store configuration...")

        etcd_url = os.getenv("ETCD_URL")
        if not etcd_url:
            raise ValueError("ETCD_URL environment variable is required")

        self.logger.debug("ETCD URL: %s", etcd_url)
        self.logger.debug("ETCD Timeout: %s", os.getenv("ETCD_TIMEOUT", "5.0"))

        # Remove protocol if present
        if "://" in etcd_url:
            etcd_url = etcd_url.split("://")[1]

        # Split host and port
        parts = etcd_url.split(":")
        etcd_host = parts[0]
        etcd_port = parts[1] if len(parts) > 1 else "2379"

        config = StoreConfig(
            host=etcd_host,
            port=int(etcd_port),
            timeout=float(os.getenv("ETCD_TIMEOUT", "5.0")),
            username=os.getenv("ETCD_USERNAME", None),
            password=os.getenv("ETCD_PASSWORD", None),
        )

        store = KeyValueStoreFactory.create_store(
            store_type=StoreType.ETCD3,
            serializer=serialize,
            deserializer=deserialize,
            config=config,
        )
        self.logger.debug("ETCD store created successfully")
        return store

    async def create_key(
        self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None
    ) -> bool:
        """Create a new key with optional encryption."""
        try:
            # Check if key exists
            existing_value = await self.store.get_key(key)
            if existing_value is not None and not overwrite:
                self.logger.debug("Skipping existing key: %s", key)
                return True

            # Convert value to JSON string
            value_json = json.dumps(value)

            EXCLUDED_KEYS = [
                config_node_constants.ENDPOINTS.value,
                config_node_constants.STORAGE.value,
                config_node_constants.MIGRATIONS.value,
            ]
            encrypt_value = key not in EXCLUDED_KEYS

            if encrypt_value:
                # Encrypt the value
                encrypted_value = self.encryption_service.encrypt(value_json)
            else:
                encrypted_value = value_json

            self.logger.debug("Encrypted value for key %s", key)

            # Store the encrypted value
            success = await self.store.create_key(key, encrypted_value, overwrite, ttl)
            if success:
                self.logger.debug("Successfully stored encrypted key: %s", key)

                # Verify the stored value
                encrypted_stored_value = await self.store.get_key(key)
                if encrypted_stored_value:
                    if encrypt_value:
                        processed_value = self.encryption_service.decrypt(
                            encrypted_stored_value
                        )
                    else:
                        processed_value = encrypted_stored_value
                    # Parse value if it's not already a dict
                    stored_value = (
                        json.loads(processed_value)
                        if isinstance(processed_value, str)
                        else processed_value
                    )

                    if stored_value != value:
                        self.logger.warning("Verification failed for key: %s", key)
                        return False

                return True
            else:
                self.logger.error("Failed to store key: %s", key)
                return False

        except Exception as e:
            self.logger.error(
                "Failed to store config value for key %s: %s", key, str(e)
            )
            self.logger.exception("Detailed error:")
            return False

    async def update_value(
        self, key: str, value: T, ttl: Optional[int] = None
    ) -> None:
        return await self.create_key(key, value, True, ttl)

    async def get_key(self, key: str) -> Optional[T]:
        try:
            encrypted_value = await self.store.get_key(key)

            if encrypted_value is not None:
                try:
                    # Determine if value needs decryption
                    UNENCRYPTED_KEYS = [
                        config_node_constants.ENDPOINTS.value,
                        config_node_constants.STORAGE.value,
                        config_node_constants.MIGRATIONS.value,
                    ]
                    needs_decryption = key not in UNENCRYPTED_KEYS

                    # Get decrypted or raw value
                    value = (
                        self.encryption_service.decrypt(encrypted_value)
                        if needs_decryption
                        else encrypted_value
                    )

                    # Parse value if it's not already a dict
                    result = (
                        json.loads(value) if not isinstance(value, dict) else value
                    )

                    return result

                except Exception as e:
                    self.logger.error(
                        f"Failed to process value for key {key}: {str(e)}"
                    )
                    return None
            else:
                self.logger.debug(f"No value found for key: {key}")
                return None

        except Exception as e:
            self.logger.error("Failed to get config %s: %s", key, str(e))
            self.logger.exception("Detailed error:")
            return None

    async def get_required_config(self, key: str, friendly_name: str = None) -> T:
        """
        Get a required configuration value, raising an error if not found.

        Args:
            key: The configuration key to retrieve
            friendly_name: Human-readable name for error messages

        Returns:
            The configuration value

        Raises:
            ConfigurationNotFoundError: If the configuration is not found
            ConfigurationInvalidError: If the configuration is invalid/corrupted
        """
        display_name = friendly_name or key

        try:
            value = await self.get_key(key)

            if value is None:
                self.logger.error(
                    "CONFIGURATION ERROR: Required configuration '%s' not found. "
                    "This may indicate missing migration from etcd to Redis or "
                    "the configuration was never set. Please reconfigure.",
                    display_name,
                )
                raise ConfigurationNotFoundError(
                    config_key=key,
                    suggestion=f"Please configure '{display_name}' in the admin panel.",
                )

            return value

        except ConfigurationNotFoundError:
            raise
        except Exception as e:
            self.logger.error(
                "CONFIGURATION ERROR: Failed to read configuration '%s': %s. "
                "The configuration may be corrupted. Please reconfigure.",
                display_name,
                str(e),
            )
            raise ConfigurationInvalidError(
                config_key=key,
                reason=f"Failed to read or decrypt configuration: {str(e)}",
            )

    async def validate_required_configs(self, required_keys: List[str]) -> Dict[str, bool]:
        """
        Validate that all required configurations exist.

        Args:
            required_keys: List of configuration keys that must exist

        Returns:
            Dictionary mapping keys to their validation status

        Raises:
            ConfigurationMigrationError: If any required configurations are missing
        """
        results = {}
        missing_keys = []

        for key in required_keys:
            try:
                value = await self.get_key(key)
                results[key] = value is not None
                if value is None:
                    missing_keys.append(key)
            except Exception as e:
                self.logger.error("Error validating config key %s: %s", key, str(e))
                results[key] = False
                missing_keys.append(key)

        if missing_keys:
            self.logger.error(
                "CONFIGURATION ERROR: The following required configurations are missing: %s. "
                "This may indicate incomplete migration from etcd to Redis. "
                "Please either run migration or reconfigure these settings.",
                ", ".join(missing_keys),
            )
            raise ConfigurationMigrationError(
                message="Required configurations are missing",
                failed_keys=missing_keys,
            )

        return results

    async def delete_key(self, key: str) -> bool:
        return await self.store.delete_key(key)

    async def get_all_keys(self) -> List[str]:
        return await self.store.get_all_keys()

    async def watch_key(
        self,
        key: str,
        callback: Callable[[Optional[T]], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        return await self.store.watch_key(key, callback, error_callback)

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        return await self.store.list_keys_in_directory(directory)

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        return await self.store.cancel_watch(key, watch_id)

    async def close(self) -> None:
        """Clean up resources and close connection."""
        await self.store.close()
