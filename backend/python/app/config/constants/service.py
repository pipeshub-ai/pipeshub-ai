from enum import Enum


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


class DefaultEndpoints(Enum):
    """Constants for default endpoints"""

    CONNECTOR_ENDPOINT = "http://localhost:8088"
    INDEXING_ENDPOINT = "http://localhost:8091"
    QUERY_ENDPOINT = "http://localhost:8000"
    NODEJS_ENDPOINT = "http://localhost:3000"
    FRONTEND_ENDPOINT = "http://localhost:3001"
    STORAGE_ENDPOINT = "http://localhost:3000"

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
    STORAGE_DOWNLOAD = "/api/v1/document/internal/{documentId}/download"


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
    CLIENT_ID_ENTITY = "entity-producer"


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
