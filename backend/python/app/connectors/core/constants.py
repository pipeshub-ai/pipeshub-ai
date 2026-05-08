"""Constants shared across all connectors (config paths, OAuth keys, batch sizes)."""


class ConfigPaths:
    """Configuration path templates for etcd / configuration service."""
    CONNECTOR_CONFIG = "/services/connectors/{connector_id}/config"
    OAUTH_CONFIG = "/services/oauth/{connector_type}"


class OAuthConfigKeys:
    """Standard keys used in OAuth configurations."""
    OAUTH_CONFIG_ID = "oauthConfigId"
    AUTH = "auth"
    CREDENTIALS = "credentials"
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"
    CONFIG = "config"
    SCOPE = "scope"
    SCOPES = "scopes"  # OAuth permission scopes (plural, different from connector scope)
    TOKEN_ACCESS_TYPE = "tokenAccessType"
    ADDITIONAL_PARAMS = "additionalParams"


class AuthFieldKeys:
    """Common credential / app registration field keys."""
    TENANT_ID = "tenantId"
    CLIENT_ID = "clientId"
    CLIENT_ID_ALT = "client_id"
    CLIENT_SECRET = "clientSecret"
    CLIENT_SECRET_ALT = "client_secret"
    INSTANCE_URL = "instanceUrl"
    HAS_ADMIN_CONSENT = "hasAdminConsent"
    AUTHORIZE_URL = "authorizeUrl"
    TOKEN_URL = "tokenUrl"
    REDIRECT_URI = "redirectUri"


class OAuthDefaults:
    """Default values for OAuth configuration."""
    MAX_URL_LENGTH = 2000


class BatchConfig:
    """Batch processing and pagination defaults."""
    DEFAULT_BATCH_SIZE = 50
    DEFAULT_PAGE_SIZE = 100


class CommonStrings:
    """Short string literals shared across connectors (delimiters, punctuation, whitespace, etc.)."""

    COMMA = ","


class IconPaths:
    """Icon path template for connectors."""

    @staticmethod
    def connector_icon(connector_name: str) -> str:
        """Generate icon path for a connector."""
        return f"/icons/connectors/{connector_name.lower().replace(' ', '')}.svg"


class OAuthRedirectPaths:
    """OAuth redirect path template for connector OAuth (relative to app base)."""

    CONNECTOR_CALLBACK = "connectors/oauth/callback/{connector_name}"


class ConnectorRegistryCategories:
    """Shared registry UI category labels for connectors (reuse across connector modules)."""

    IT_SERVICE_MANAGEMENT = "IT Service Management"
    KNOWLEDGE_MANAGEMENT = "Knowledge Management"


class ConnectorRequestKeys:
    """Keys used in connector API request/response bodies."""
    CONNECTOR_TYPE = "connectorType"
    INSTANCE_NAME = "instanceName"
    CONFIG = "config"
    OAUTH_CONFIG_ID = "oauthConfigId"
    AUTH_TYPE = "authType"
    BASE_URL = "baseUrl"
    SCOPE = "scope"  # Connector scope (team/personal)
    CONNECTOR_SCOPE = "connectorScope"


class ConnectorRegistryAuthMetadataKeys:
    """Keys in connector registry `config.auth` metadata (schemas, per-auth-type OAuth)."""

    SCHEMAS = "schemas"
    OAUTH_CONFIGS = "oauthConfigs"


class ConnectorStateKeys:
    """Keys for connector state/status fields (typically in connector documents)."""
    IS_CONFIGURED = "isConfigured"
    IS_AUTHENTICATED = "isAuthenticated"
    IS_ACTIVE = "isActive"
    PENDING_FULL_SYNC = "pendingFullSync"
    UPDATED_AT_TIMESTAMP = "updatedAtTimestamp"
    UPDATED_BY = "updatedBy"
    CREATED_AT_TIMESTAMP = "createdAtTimestamp"
    CREATED_BY = "createdBy"


CONNECTOR_EMAIL_IDENTITY_INFO = (
    "This connector identifies users by email. Each person should sign in to the platform "
    "with the same address they use in the connected app, so only their own data and access "
    "rules apply. Different emails can mean missing content."
)
