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


class AuthFieldKeys:
    """Common credential / app registration field keys."""
    TENANT_ID = "tenantId"
    CLIENT_ID = "clientId"
    CLIENT_SECRET = "clientSecret"
    INSTANCE_URL = "instanceUrl"
    HAS_ADMIN_CONSENT = "hasAdminConsent"
    AUTHORIZE_URL = "authorizeUrl"
    TOKEN_URL = "tokenUrl"
    REDIRECT_URI = "redirectUri"


class BatchConfig:
    """Batch processing and pagination defaults."""
    DEFAULT_BATCH_SIZE = 50
    DEFAULT_PAGE_SIZE = 100


class IconPaths:
    """Icon path template for connectors."""
    
    @staticmethod
    def connector_icon(connector_name: str) -> str:
        """Generate icon path for a connector."""
        return f"/assets/icons/connectors/{connector_name.lower().replace(' ', '')}.svg"
