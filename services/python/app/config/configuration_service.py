# src/config/configuration_service.py
from typing import Any
import json
import os
import dotenv
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig, StoreType
from app.config.providers.etcd3_store import Etcd3DistributedKeyValueStore
from enum import Enum
from app.utils.logger import create_logger
from app.config.encryption.encryption_service import EncryptionService

dotenv.load_dotenv()

logger = create_logger('etcd')

class config_node_constants(Enum):
    """Constants for ETCD configuration paths"""
    
    # Service paths
    ARANGODB = "/services/arangodb"
    QDRANT = "/services/qdrant"
    CONFIGURATION_MANAGER = "/services/cmBackend"
    REDIS = "/services/redis"
    AI_MODELS = "/services/aiModels"
    KAFKA = "/services/kafka"
    INDEXING = "/services/indexing"
    QUERY_BACKEND = "/services/queryBackend"
    CONNECTORS_SERVICE = "/services/connectors"
    
    # Non-service paths
    LOG_LEVEL = "/logLevel"
    
class TokenScopes(Enum):
    """Constants for token scopes"""
    SEND_MAIL = "mail:send"
    FETCH_CONFIG = "fetch:config"
    PASSWORD_RESET = "password:reset"
    USER_LOOKUP = "user:lookup"
    TOKEN_REFRESH = "token:refresh"
    
class Routes(Enum):
    """Constants for routes"""
    INDIVIDUAL_CREDENTIALS = "/api/v1/configurationManager/internal/connectors/individual/googleWorkspaceCredentials"
    INDIVIDUAL_REFRESH_TOKEN = "/api/v1/connectors/internal/refreshIndividualConnectorToken"
    BUSINESS_CREDENTIALS = "/api/v1/configurationManager/internal/connectors/business/googleWorkspaceCredentials"
    LLM_CONFIG = "/api/v1/configurationManager/internal/aiModelsConfig"
    
class WebhookConfig(Enum):
    """Constants for webhook configuration"""
    EXPIRATION_DAYS = 5
    EXPIRATION_HOURS = 11 #23
    EXPIRATION_MINUTES = 59
    COALESCEDELAY = 30
    
class KafkaConfig(Enum):
    """Constants for kafka configuration"""
    CLIENT_ID_RECORDS = "record-processor"
    CLIENT_ID_MAIN = "enterprise-search"
    
class CeleryConfig(Enum):
    """Constants for celery configuration"""
    TASK_SERIALIZER = "json"
    RESULT_SERIALIZER = "json"
    ACCEPT_CONTENT = ["json"]
    TIMEZONE = "UTC"
    ENABLE_UTC = True
    SCHEDULE = {
        "syncStartTime": "23:00",
        "syncPauseTime": "05:00"
    }
    
class RedisConfig(Enum):
    """Constants for redis configuration"""
    REDIS_DB = 0
    
class ConfigurationService:
    """Service to manage configuration using etcd store"""

    def __init__(self):
        logger.debug(
            "🔧 Initializing ConfigurationService")

        secret_key = os.getenv('SECRET_KEY')
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable is required")
        self.encryption_service = EncryptionService.get_instance("aes-256-gcm", secret_key)
        logger.debug("🔐 Initialized EncryptionService")
        
        logger.debug("🔧 Creating ETCD store...")
        self.store = self._create_store()
        logger.debug("✅ ConfigurationService initialized successfully")

    def _create_store(self) -> Etcd3DistributedKeyValueStore:
        logger.debug("🔧 Creating ETCD store configuration...")
        logger.debug("ETCD Host: %s", os.getenv('ETCD_HOST'))
        logger.debug("ETCD Port: %s", os.getenv('ETCD_PORT'))
        logger.debug("ETCD Timeout: %s", os.getenv('ETCD_TIMEOUT', '5.0'))
        logger.debug("ETCD Username: %s", os.getenv('ETCD_USERNAME', 'None'))

        config = StoreConfig(
            host=os.getenv('ETCD_HOST', 'localhost'),
            port=int(os.getenv('ETCD_PORT', '2379')),
            timeout=float(os.getenv('ETCD_TIMEOUT', '5.0')),
            username=os.getenv('ETCD_USERNAME', None),
            password=os.getenv('ETCD_PASSWORD', None)
        )

        def serialize(value: Any) -> bytes:
            logger.debug("🔄 Serializing value: %s (type: %s)",
                         value, type(value))
            if value is None:
                logger.debug("⚠️ Serializing None value to empty bytes")
                return b''
            if isinstance(value, (str, int, float, bool)):
                serialized = json.dumps(value).encode('utf-8')
                logger.debug("✅ Serialized primitive value: %s", serialized)
                return serialized
            serialized = json.dumps(value, default=str).encode('utf-8')
            logger.debug("✅ Serialized complex value: %s", serialized)
            return serialized

        def deserialize(value: bytes) -> Any:
            logger.debug("🔄 Deserializing bytes: %s", value)
            if not value:
                logger.debug("⚠️ Empty bytes, returning None")
                return None
            try:
                # First try to decode as a JSON string
                decoded = value.decode('utf-8')
                # logger.debug("📋 Decoded UTF-8 string: %s", decoded)

                try:
                    # Try parsing as JSON
                    result = json.loads(decoded)
                    logger.debug(
                        "✅ Deserialized JSON value: (type: %s)", type(result))
                    return result
                except json.JSONDecodeError:
                    # If JSON parsing fails, return the string directly
                    # logger.debug(
                    #     "📋 Not JSON, returning string directly")
                    return decoded

            except UnicodeDecodeError as e:
                logger.error("❌ Failed to decode bytes: %s", str(e))
                return None

        store = KeyValueStoreFactory.create_store(
            store_type=StoreType.ETCD3,
            serializer=serialize,
            deserializer=deserialize,
            config=config
        )
        logger.debug("✅ ETCD store created successfully")
        return store

    async def load_default_config(self, overwrite: bool = False):
        """Load default configuration into etcd."""
        logger.debug("🔄 Starting to load default configuration")
        logger.debug("📂 Reading default_config.json...")
        
        with open('default_config.json', 'r') as f:
            default_config = json.load(f)
            logger.debug("📋 Default config loaded: %s", default_config)

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
        
        logger.debug("✅ Default configuration loaded completely")

    async def _store_config_value(self, key: str, value: Any, overwrite: bool) -> bool:
        """Helper method to store a single configuration value"""
        try:
            # Check if key exists
            existing_value = await self.store.get_key(key)
            if existing_value is not None and not overwrite:
                logger.debug("⏭️ Skipping existing key: %s", key)
                return True
            
            # Convert value to JSON string
            value_json = json.dumps(value)
            
            # Encrypt the value
            encrypted_value = self.encryption_service.encrypt(value_json)
            logger.debug("🔒 Encrypted value for key %s", key)
            
            # Store the encrypted value
            success = await self.store.create_key(key, encrypted_value)
            if success:
                logger.debug("✅ Successfully stored encrypted key: %s", key)
                
                # Verify the stored value
                encrypted_stored_value = await self.store.get_key(key)
                if encrypted_stored_value:
                    decrypted_value = self.encryption_service.decrypt(encrypted_stored_value)
                    stored_value = json.loads(decrypted_value)
                    
                    if stored_value != value:
                        logger.warning("⚠️ Verification failed for key: %s", key)
                        logger.warning("  Expected: %s", value)
                        logger.warning("  Got: %s", stored_value)
                        return False
                    
                return True
            else:
                logger.error("❌ Failed to store key: %s", key)
                return False

        except Exception as e:
            logger.error("❌ Failed to store config value for key %s: %s", key, str(e))
            logger.exception("Detailed error:")
            return False

    async def has_configuration(self) -> bool:
        """Check if any configuration exists in etcd."""
        try:
            logger.debug("🔍 Checking for existing configuration in ETCD")
            values = await self.store.get_all_keys()
            # Check if any configuration exists
            exists = len(values) > 0
            logger.debug("✅ Configuration check complete. Exists: %s", exists)
            return exists
        except Exception as e:
            logger.error(
                "❌ Error checking configuration existence: %s", str(e))
            logger.exception("Detailed error:")
            return False

    async def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value with fallback to default_config"""
        try:
            full_key = key
            encrypted_value = await self.store.get_key(full_key)

            if encrypted_value is not None:
                try:
                    # Decrypt the stored value
                    decrypted_value = self.encryption_service.decrypt(encrypted_value)
                    # Parse the JSON string back to its original form
                    return json.loads(decrypted_value)
                except Exception as e:
                    logger.error(f"❌ Failed to decrypt value for key {full_key}: {str(e)}")
                    return default
            else:
                logger.error(f"❌ Value not found in ETCD for key: {full_key}")
                return False

        except Exception as e:
            logger.error("❌ Failed to get config %s: %s", key, str(e))
            logger.exception("Detailed error:")
            return default
