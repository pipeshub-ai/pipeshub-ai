import hashlib
import json
import os
from typing import Callable, Dict, Generic, List, Optional, TypeVar, Union

import dotenv  # type: ignore
import etcd3  # type: ignore

from app.config.constants.service import config_node_constants
from app.config.constants.store_type import StoreType
from app.config.key_value_store import KeyValueStore
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore
from app.utils.encryption.encryption_service import EncryptionService
from app.utils.logger import create_logger

dotenv.load_dotenv()

logger = create_logger("etcd")

T = TypeVar("T")

class Etcd3EncryptedKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    ETCD3-based implementation of the encrypted key-value store.
    """

    def __init__(
        self,
        logger,
    ) -> None:
        self.logger = logger

        self.logger.debug("🔧 Initializing Etcd3EncryptedKeyValueStore")

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

        self.logger.debug("🔧 Creating ETCD store...")
        self.store = self._create_store()


        self.logger.debug("✅ KeyValueStore initialized successfully")

    @property
    def client(self) -> Optional[etcd3.client]:
        """Expose the underlying ETCD client for watchers and diagnostics."""
        return self.store.client


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

        def serialize(value: Union[str, int, float, bool, Dict, list, None]) -> bytes:
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

        def deserialize(value: bytes) -> Union[str, int, float, bool, dict, list, None]:
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

    async def create_key(self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None) -> bool:
        """Create a new key in etcd."""
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
                config_node_constants.MIGRATIONS.value,
            ]
            encrypt_value = key not in EXCLUDED_KEYS

            if encrypt_value:
                # Encrypt the value
                encrypted_value = self.encryption_service.encrypt(value_json)
            else:
                encrypted_value = value_json

            self.logger.debug("🔒 Encrypted value for key %s", key)

            # Store the encrypted value
            success = await self.store.create_key(key, encrypted_value, overwrite, ttl)
            if success:
                self.logger.debug("✅ Successfully stored encrypted key: %s", key)

                # Verify the stored value
                encrypted_stored_value = await self.store.get_key(key)
                if encrypted_stored_value:
                    if encrypt_value:
                        processed_value = self.encryption_service.decrypt(
                            encrypted_stored_value
                        )
                    else:
                        processed_value = encrypted_stored_value
                    # Parse value if it's not already a dict (for unencrypted keys, it's already deserialized)
                    stored_value = json.loads(processed_value) if isinstance(processed_value, str) else processed_value

                    if stored_value != value:
                        self.logger.warning("⚠️ Verification failed for key: %s", key)
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


    async def update_value(self, key: str, value: T, ttl: Optional[int] = None) -> None:
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
                    result = json.loads(value) if not isinstance(value, dict) else value

                    return result

                except Exception as e:
                    self.logger.error(
                        f"❌ Failed to process value for key {key}: {str(e)}"
                    )
                    return None
            else:
                self.logger.debug(f"⚠️ No value found in ETCD for key: {key}")
                return None

        except Exception as e:
            self.logger.error("❌ Failed to get config %s: %s", key, str(e))
            self.logger.exception("Detailed error:")
            return None

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
        """
        List all keys in a directory, decrypting encrypted keys.

        Args:
            directory: Directory path to filter keys. If empty or "/", returns all keys.
                      Otherwise, returns keys starting with this path.

        Returns:
            List of decrypted keys matching the directory prefix.
        """
        try:
            # Get all keys from etcd (they are stored encrypted)
            encrypted_keys = await self.store.get_all_keys()

            if not encrypted_keys:
                return []

            # Normalize directory prefix for matching
            directory_prefix = directory.rstrip("/") if directory and directory != "/" else ""

            UNENCRYPTED_PREFIXES = [
                config_node_constants.ENDPOINTS.value,
                config_node_constants.STORAGE.value,
                config_node_constants.MIGRATIONS.value,
            ]

            decrypted_keys = []
            for encrypted_key in encrypted_keys:
                try:
                    # Check if key is unencrypted (excluded from encryption)
                    is_unencrypted = any(encrypted_key.startswith(prefix) for prefix in UNENCRYPTED_PREFIXES)

                    if is_unencrypted:
                        decrypted_key = encrypted_key
                    else:
                        # Try to decrypt the key
                        # Encrypted format: "iv:ciphertext:authTag" (3 parts)
                        if encrypted_key.count(":") == 2:
                            try:
                                decrypted_key = self.encryption_service.decrypt(encrypted_key)
                            except Exception:
                                # Decryption failed, use as-is (might be unencrypted)
                                decrypted_key = encrypted_key
                        else:
                            # Not in encrypted format, use as-is
                            decrypted_key = encrypted_key

                    # Filter by directory prefix if provided
                    if not directory_prefix or decrypted_key.startswith(directory_prefix):
                        decrypted_keys.append(decrypted_key)

                except Exception as e:
                    self.logger.debug(f"Skipping key due to error: {e}")
                    continue

            return decrypted_keys

        except Exception as e:
            self.logger.error(f"Failed to list keys in directory {directory}: {e}")
            raise

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        return await self.store.cancel_watch(key, watch_id)

    async def close(self) -> None:
        """Clean up resources and close connection."""
        self.store.close()
