from enum import Enum


class config_node_constants(Enum):
    """Constants for ETCD configuration paths"""

    # Service paths
    ARANGODB = "/services/arangodb"
    NEO4J = "/services/neo4j"
    QDRANT = "/services/qdrant"
    REDIS = "/services/redis"
    AI_MODELS = "/services/aiModels"
    KAFKA = "/services/kafka"
    REDIS_STREAMS = "/services/redis-streams"
    MESSAGE_BROKER = "/services/message-broker"
    ENDPOINTS = "/services/endpoints"
    SECRET_KEYS = "/services/secretKeys"
    STORAGE = "/services/storage"
    MIGRATIONS = "/services/migrations"
    DEPLOYMENT = "/services/deployment"

    # Non-service paths
    # LOG_LEVEL = "/logLevel"

class TokenScopes(Enum):
    """Constants for internal scoped token scopes"""

    SEND_MAIL = "mail:send"
    FETCH_CONFIG = "fetch:config"
    PASSWORD_RESET = "password:reset"
    USER_LOOKUP = "user:lookup"
    TOKEN_REFRESH = "token:refresh"
    STORAGE_TOKEN = "storage:token"


class OAuthScopes(str, Enum):
    """OAuth scopes for external API access control"""

    # Semantic Search
    SEMANTIC_WRITE = "semantic:write"
    SEMANTIC_READ = "semantic:read"
    SEMANTIC_DELETE = "semantic:delete"

    # Conversations
    CONVERSATION_READ = "conversation:read"
    CONVERSATION_WRITE = "conversation:write"
    CONVERSATION_CHAT = "conversation:chat"

    # Agents
    AGENT_READ = "agent:read"
    AGENT_WRITE = "agent:write"
    AGENT_EXECUTE = "agent:execute"

    # Knowledge Base
    KB_READ = "kb:read"
    KB_WRITE = "kb:write"
    KB_DELETE = "kb:delete"
    KB_UPLOAD = "kb:upload"

    # Connectors
    CONNECTOR_READ = "connector:read"
    CONNECTOR_WRITE = "connector:write"
    CONNECTOR_SYNC = "connector:sync"
    CONNECTOR_DELETE = "connector:delete"

    # Teams
    TEAM_READ = "team:read"
    TEAM_WRITE = "team:write"


class DefaultEndpoints(Enum):
    """Constants for default endpoints"""

    CONNECTOR_ENDPOINT = "http://localhost:8088"
    INDEXING_ENDPOINT = "http://localhost:8091"
    QUERY_ENDPOINT = "http://localhost:8000"
    NODEJS_ENDPOINT = "http://localhost:3000"
    FRONTEND_ENDPOINT = "http://localhost:3001"
    STORAGE_ENDPOINT = "http://localhost:3000"  # noqa: PIE796

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
    STORAGE_UPLOAD_NEXT_VERSION = "/api/v1/document/internal/{documentId}/uploadNextVersion"
    STORAGE_DOWNLOAD = "/api/v1/document/internal/{documentId}/download"
    STORAGE_DOWNLOAD_EXTERNAL = "/api/v1/document/{documentId}/download"
    STORAGE_BUFFER = "/api/v1/document/internal/{documentId}/buffer"


class WebhookConfig(Enum):
    """Constants for webhook configuration"""

    EXPIRATION_DAYS = 6
    EXPIRATION_HOURS = 23
    EXPIRATION_MINUTES = 59
    COALESCEDELAY = 60


class KafkaConfig(Enum):
    """Constants for kafka configuration"""

    # Producer client IDs
    CLIENT_ID_RECORDS = "record-processor"
    CLIENT_ID_MAIN = "enterprise-search"
    CLIENT_ID_LLM = "llm-configuration"
    CLIENT_ID_ENTITY = "entity-producer"
    CLIENT_ID_MESSAGING_PRODUCER = "messaging_producer_client"

    # Consumer client IDs
    CLIENT_ID_ENTITY_CONSUMER = "entity_consumer_client"
    CLIENT_ID_SYNC_CONSUMER = "sync_consumer_client"
    CLIENT_ID_RECORDS_CONSUMER = "records_consumer_client"
    CLIENT_ID_AICONFIG_CONSUMER = "aiconfig_consumer_client"

    # Consumer group IDs
    GROUP_ID_ENTITY = "entity_consumer_group"
    GROUP_ID_SYNC = "sync_consumer_group"
    GROUP_ID_RECORDS = "records_consumer_group"
    GROUP_ID_AICONFIG = "aiconfig_consumer_group"


class CeleryConfig(Enum):
    """Constants for celery configuration"""

    TASK_SERIALIZER = "json"
    RESULT_SERIALIZER = "json"  # noqa: PIE796
    ACCEPT_CONTENT = ["json"]
    TIMEZONE = "UTC"
    ENABLE_UTC = True
    SCHEDULE = {"syncStartTime": "23:00", "syncPauseTime": "05:00"}


class KVStoreType(str, Enum):
    """Supported key-value store backends."""

    REDIS = "redis"
    ETCD = "etcd"


class RedisEnv(str, Enum):
    """Environment variable names for Redis configuration."""

    KV_STORE_TYPE = "KV_STORE_TYPE"
    HOST = "REDIS_HOST"
    PORT = "REDIS_PORT"
    PASSWORD = "REDIS_PASSWORD"
    DB = "REDIS_DB"


class RedisDefaults(str, Enum):
    """Default values for Redis configuration."""

    HOST = "localhost"
    PORT = "6379"
    DB = "0"


class RedisConfig(Enum):
    """Constants for redis configuration"""

    REDIS_DB = 0


class HealthCheckConfig(Enum):
    """Constants for health check configuration"""

    CONNECTOR_HEALTH_CHECK_MAX_RETRIES = 120
    CONNECTOR_HEALTH_CHECK_RETRY_INTERVAL_SECONDS = 1
