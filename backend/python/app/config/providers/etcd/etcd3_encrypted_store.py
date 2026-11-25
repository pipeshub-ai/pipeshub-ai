import ast
import json
from collections.abc import Collection
from typing import Callable, Dict, Generic, List, Optional, TypeVar, Union

import dotenv  # type: ignore
import etcd3  # type: ignore

from app.config.constants.service import config_node_constants
from app.config.constants.store_type import StoreType
from app.config.key_value_store import KeyValueStore
from app.config.key_value_store_factory import KeyValueStoreFactory
from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore
from app.config.providers.etcd.etcd_env import etcd_store_config
from app.utils.encryption.component_key import process_cipher_key
from app.utils.encryption.encryption_service import EncryptionService
from app.utils.logger import create_logger

dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))

logger = create_logger("etcd")

# Constants
ENCRYPTED_KEY_PARTS_COUNT = 2  # Number of colons in encrypted format: "iv:ciphertext:authTag"

UNENCRYPTED_KEYS = (
    config_node_constants.ENDPOINTS.value,
    config_node_constants.STORAGE.value,
    config_node_constants.MIGRATIONS.value,
    config_node_constants.DEPLOYMENT.value,
)
# Directories whose keys are all plaintext: Node reads them without the Python encryption
# (the artifact-rollout membership entries, which hold no secret).
UNENCRYPTED_PREFIXES = ("/services/platform/artifactRollout/membership/",)


def is_unencrypted_key(key: str) -> bool:
    return key in UNENCRYPTED_KEYS or key.startswith(UNENCRYPTED_PREFIXES)

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

        # The platform cipher's key (ADR-KEY-02): HKDF of SECRET_KEY, or of this component's own key on the
        # sandbox plane, which never holds SECRET_KEY.
        hex_key = process_cipher_key()

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
        config = etcd_store_config()
        if config is None:
            raise ValueError("ETCD_URL environment variable is required")

        def serialize(value: Union[str, int, float, bool, Dict, list, None]) -> bytes:
            if value is None:
                self.logger.debug("⚠️ Serializing None value to empty bytes")
                return b""
            if isinstance(value, (str, int, float, bool)):
                serialized = json.dumps(value).encode("utf-8")
            else:
                serialized = json.dumps(value, default=str).encode("utf-8")
            self.logger.debug(
                "✅ Serialized %s value (%d bytes)", type(value).__name__, len(serialized)
            )
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
                return False  # Key was not created (already exists)

            # Convert value to JSON string
            value_json = json.dumps(value)

            encrypt_value = not is_unencrypted_key(key)

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

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Optional[T]:
        try:
            # Forwarded, or the backend's own swallow of an unreadable value
            # answers None here and reads as a missing key.
            if raise_on_error:
                encrypted_value = await self.store.get_key(key, raise_on_error=True)
            else:
                encrypted_value = await self.store.get_key(key)

            if encrypted_value is not None:
                try:
                    return self._decode_value(key, encrypted_value)
                except Exception as e:
                    # A value came back and could not be read: not the same as
                    # no value, so a caller that asked must not see None.
                    if raise_on_error:
                        raise
                    self.logger.error(
                        "Failed to process value for key %s: %s", key, type(e).__name__
                    )
                    return None
            else:
                self.logger.debug(f"⚠️ No value found in ETCD for key: {key}")
                return None

        except Exception as e:
            # Raised for the caller that asked to handle it; logged only when answered as None.
            if raise_on_error:
                raise
            self.logger.error("Failed to get config %s: %s", key, type(e).__name__)
            return None

    def _decode_value(self, key: str, stored: object) -> T | None:
        """Turn what the inner store returned for ``key`` into the caller's value."""
        needs_decryption = not is_unencrypted_key(key)

        value = (
            self.encryption_service.decrypt(stored)
            if needs_decryption
            else stored
        )

        # Parse value — already-deserialized types pass through
        if isinstance(value, (dict, list, int, float)):
            return value
        if needs_decryption:
            return json.loads(value)
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            try:
                # Legacy fallback: unencrypted values may have been
                # stored via Python's str() (single quotes,
                # True/False/None) instead of json.dumps().
                # Read-only: the next create_key() call will
                # overwrite with proper JSON, upgrading in place.
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return value

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
        def on_change(stored: object) -> None:
            # A decryption failure raised here reaches error_callback through
            # the inner store, rather than handing the watcher ciphertext.
            callback(None if stored is None else self._decode_value(key, stored))

        return await self.store.watch_key(key, on_change, error_callback)

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        """
        List all keys in a directory, decrypting encrypted keys.

        Args:
            directory: Directory path to filter keys. If empty or "/", returns all keys.
                      Otherwise, returns keys starting with this path.

        Returns:
            List of decrypted keys matching the directory prefix.
        """
        # Get all keys from etcd (they are stored encrypted)
        encrypted_keys = await self.store.get_all_keys()

        if not encrypted_keys:
            return []

        # Kept as given: stripping the trailing slash let "/a/b/" also match "/a/b" and "/a/b-2/...".
        directory_prefix = directory if directory != "/" else ""

        decrypted_keys = []
        for encrypted_key in encrypted_keys:
            try:
                # Check if key is unencrypted (excluded from encryption)
                is_unencrypted = any(encrypted_key.startswith(prefix) for prefix in UNENCRYPTED_KEYS)

                if is_unencrypted:
                    decrypted_key = encrypted_key
                else:
                    # Try to decrypt the key
                    # Encrypted format: "iv:ciphertext:authTag" (3 parts)
                    if encrypted_key.count(":") == ENCRYPTED_KEY_PARTS_COUNT:
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

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        return await self.store.cancel_watch(key, watch_id)

    async def close(self) -> None:
        """Clean up resources and close connection."""
        await self.store.close()

    # Without these the base-class no-ops answer, and a ConfigurationService on
    # this store never hears that another process changed a key it has cached.
    async def subscribe_changes(
        self, callback: Callable[[str], None], *, keys: Collection[str] | None = None
    ) -> object | None:
        if keys is None:
            return await self.store.subscribe_changes(callback)
        return await self.store.subscribe_changes(callback, keys=keys)

    async def publish_change(self, key: str) -> None:
        await self.store.publish_change(key)

    async def unsubscribe_changes(self, handle: object) -> None:
        await self.store.unsubscribe_changes(handle)
