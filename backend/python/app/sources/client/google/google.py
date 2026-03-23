import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.google.common.connector_google_exceptions import (
    AdminAuthError,
    AdminDelegationError,
    AdminServiceError,
    GoogleAuthError,
)
from app.connectors.sources.google.common.scopes import (
    GOOGLE_PARSER_SCOPES,
    GOOGLE_SERVICE_SCOPES,
    SERVICES_WITH_PARSER_SCOPES,
)
from app.sources.client.iclient import IClient
from app.sources.client.utils.utils import merge_scopes

try:
    from google.oauth2 import service_account  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
except ImportError:
    print("Google API client libraries not found. Please install them using 'pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib'")
    raise

class CredentialKeys(Enum):
    CLIENT_ID = "clientId"
    CLIENT_SECRET = "clientSecret"
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"

@dataclass
class GoogleAuthConfig:
    """Configuration for Google authentication"""
    credentials_path: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: Optional[List[str]] = None
    oauth_port: Optional[int] = 8080
    token_file_path: Optional[str] = "token.json"
    credentials_file_path: Optional[str] = "credentials.json"
    admin_scopes: Optional[List[str]] = None
    is_individual: Optional[bool] = False  # Flag to indicate if authentication is for an individual user.


class GoogleClient(IClient):
    """Builder class for Google Drive clients with different construction methods"""

    def __init__(self, client: object) -> None:
        """Initialize with a Google Drive client object"""
        self.client = client

    def get_client(self) -> object:
        """Return the Google Drive client object"""
        return self.client

    @staticmethod
    def _get_optimized_scopes(service_name: str, additional_scopes: Optional[List[str]] = None) -> List[str]:
        """
        Get optimized scopes for a specific service.

        Args:
            service_name: Name of the Google service
            additional_scopes: Additional scopes to merge

        Returns:
            List of optimized scopes for the service
        """
        # Get base scopes for the service
        base_scopes = GOOGLE_SERVICE_SCOPES.get(service_name, [])

        # Add parser scopes only if the service needs them
        if service_name in SERVICES_WITH_PARSER_SCOPES:
            base_scopes = merge_scopes(base_scopes, GOOGLE_PARSER_SCOPES)

        # Add additional scopes if provided
        if additional_scopes:
            base_scopes = merge_scopes(base_scopes, additional_scopes)

        return base_scopes

    @classmethod
    def build_with_client(cls, client: object) -> 'GoogleClient':
        """
        Build GoogleDriveClient with an already authenticated client
        Args:
            client: Authenticated Google Drive client object
        Returns:
            GoogleClient instance
        """
        return cls(client)

    @classmethod
    def build_with_config(cls, config: GoogleAuthConfig) -> 'GoogleClient':
        """
        Build GoogleDriveClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: GoogleAuthConfig instance
        Returns:
            GoogleClient instance with placeholder implementation
        """
        # TODO: Implement OAuth2 flow and enterprise account authentication
        # For now, return a placeholder client
        placeholder_client = None  # This will be implemented later
        return cls(placeholder_client)

    @classmethod
    async def build_from_services(
        cls,
        service_name: str, # Name of the service to build the client for [drive, admin, calendar, gmail]
        logger,
        config_service: ConfigurationService,
        is_individual: Optional[bool] = False,
        version: Optional[str] = "v3", # Version of the service to build the client for [v3, v1]
        scopes: Optional[List[str]] = None, # Scopes of the service to build the client
        calendar_id: Optional[str] = 'primary', # Calendar ID to build the client for
        user_email: Optional[str] = None, # User email for enterprise impersonation
        connector_instance_id: Optional[str] = None,
    ) -> 'GoogleClient':
        """
        Build GoogleClient using configuration service and arango service
        Args:
            service_name: Name of the service to build the client for
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: GraphDB service instance
            is_individual: Flag to indicate if the client is for an individual user or an enterprise account
            version: Version of the service to build the client for
        Returns:
            GoogleClient instance
        """

        config = await GoogleClient._get_connector_config(service_name, logger, config_service, connector_instance_id)
        if not config:
            raise ValueError(f"Failed to get Google connector configuration for instance {service_name} {connector_instance_id}")
        connector_scope = config.get("auth", {}).get("connectorScope", None)

        if is_individual or connector_scope == ConnectorScope.PERSONAL.value:
            try:
                #fetch saved credentials
                saved_credentials = await GoogleClient.get_individual_token(service_name, logger, config_service, connector_instance_id)
                if not saved_credentials:
                    raise ValueError("Failed to get individual token")

                # Validate required credential fields for OAuth token refresh
                client_id = saved_credentials.get(CredentialKeys.CLIENT_ID.value)
                client_secret = saved_credentials.get(CredentialKeys.CLIENT_SECRET.value)
                access_token = saved_credentials.get(CredentialKeys.ACCESS_TOKEN.value)
                refresh_token = saved_credentials.get(CredentialKeys.REFRESH_TOKEN.value)

                # Initialize credential_scopes with default value
                credential_scopes = scopes if scopes else []

                oauth_scopes = saved_credentials.get('scope')
                if oauth_scopes is not None:
                    if isinstance(oauth_scopes, str):
                        credential_scopes = [s.strip() for s in oauth_scopes.split()] if oauth_scopes.strip() else []
                    elif isinstance(oauth_scopes, list):
                        credential_scopes = oauth_scopes
                    logger.info("Using authorized scopes from credentials")
                else:
                    logger.warning(f"No scope found in stored credentials for {connector_instance_id}, using default scopes: {credential_scopes}")

                if not client_id or not client_secret:
                    logger.error(f"Missing OAuth client credentials (client_id: {bool(client_id)}, client_secret: {bool(client_secret)}). These are required for token refresh. Please re-authenticate the connector.")
                    raise ValueError(
                        f"Missing OAuth client credentials. Client ID present: {bool(client_id)}, "
                        f"Client Secret present: {bool(client_secret)}. These are required for token refresh. "
                    )

                # Refresh token is REQUIRED for long-term operation
                if not refresh_token:
                    logger.error(f"❌ Missing refresh_token for {connector_instance_id}")
                    raise ValueError(
                        "Missing refresh_token. Please re-authenticate the connector. "
                        "Connectors require a refresh token for automatic token renewal."
                    )

                # Access token - if missing, Google will auto-refresh on first API call
                if not access_token:
                    logger.warning(
                        f"No access_token found for connector {connector_instance_id}. "
                        f"Token will be refreshed automatically on first API call."
                    )

                google_credentials = Credentials(
                    token=access_token,
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=credential_scopes,
                )

                # Create Google Drive service using the credentials
                client = build(service_name, version, credentials=google_credentials)
            except Exception as e:
                raise GoogleAuthError("Failed to get individual token: " + str(e)) from e
        else:
            try:
                saved_credentials = await GoogleClient.get_enterprise_token(service_name, logger, config_service, connector_instance_id)
                if not saved_credentials:
                    raise AdminAuthError(
                        "Failed to get enterprise credentials",
                        details={"service_name": service_name},
                    )

                admin_email = saved_credentials.get("adminEmail")
                if not admin_email:
                    raise AdminAuthError(
                        "Admin email not found in credentials",
                        details={"service_name": service_name},
                    )
            except Exception as e:
                raise AdminAuthError("Failed to get enterprise token: " + str(e))

            try:
                # Get optimized scopes for the service
                optimized_scopes = GoogleClient._get_optimized_scopes(service_name, scopes)

                google_credentials = (
                        service_account.Credentials.from_service_account_info(
                            saved_credentials,
                            scopes=optimized_scopes,
                            # Impersonate the specific user when provided; otherwise default to admin
                            subject=(user_email or admin_email)
                        )
                    )
            except Exception as e:
                raise AdminDelegationError(
                    "Failed to create delegated credentials: " + str(e),
                    details={
                        "service_name": service_name,
                        "admin_email": admin_email,
                        "user_email": user_email,
                        "error": str(e),
                    },
                )

            try:
                client = build(
                    service_name,
                    version,
                    credentials=google_credentials,
                    cache_discovery=False,
                )
            except Exception as e:
                raise AdminServiceError(
                    "Failed to build admin service: " + str(e),
                    details={"service_name": service_name, "error": str(e)},
                )

        return cls(client)

    @staticmethod
    async def _get_connector_config(service_name: str,logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> Dict:
        """Fetch connector config from etcd for the given app."""
        try:
            service_name = service_name.replace(" ", "").lower()
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(f"Failed to get Google connector configuration for instance {service_name} {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"❌ Failed to get connector config for {service_name}: {e}")
            raise ValueError(f"Failed to get Google connector configuration for instance {service_name} {connector_instance_id}")


    @staticmethod
    async def get_account_scopes(service_name: str, logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> list:
        """Get account scopes for a specific connector (gmail/drive)."""
        config = await GoogleClient._get_connector_config(service_name, logger, config_service, connector_instance_id)
        return config.get("config", {}).get("auth", {}).get("scopes", [])


    @staticmethod
    async def get_individual_token(service_name: str, logger: logging.Logger,config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> dict:
        """Get individual OAuth token for a specific connector (gmail/drive/calendar/)."""

        try:
            config = await GoogleClient._get_connector_config(service_name, logger, config_service, connector_instance_id)
            creds = (config or {}).get("credentials") or {}
            auth_cfg = (config or {}).get("auth", {}) or {}

            if not creds:
                return {}

            # Build OAuth flow config (handles shared OAuth configs)
            client_id = auth_cfg.get("clientId")
            client_secret = auth_cfg.get("clientSecret")
            oauth_config_id = auth_cfg.get("oauthConfigId")

            # If using shared OAuth config, fetch credentials from there
            if oauth_config_id and not (client_id and client_secret):
                try:
                    # Get connector type from config or derive from service_name
                    connector_type = service_name.lower().replace(" ", "")
                    oauth_config_path = f"/services/oauth/{connector_type}"
                    oauth_configs = await config_service.get_config(oauth_config_path, default=[])

                    if isinstance(oauth_configs, list):
                        # Find the OAuth config by ID
                        for oauth_cfg in oauth_configs:
                            if oauth_cfg.get("_id") == oauth_config_id:
                                oauth_config_data = oauth_cfg.get("config", {})
                                if oauth_config_data:
                                    client_id = oauth_config_data.get("clientId") or oauth_config_data.get("client_id")
                                    client_secret = oauth_config_data.get("clientSecret") or oauth_config_data.get("client_secret")
                                    logger.info("Using shared OAuth config for token retrieval")
                                break
                except Exception as e:
                    logger.warning(f"Failed to fetch shared OAuth config: {e}, using connector auth config")

            # Return a merged view including client info for SDK constructors
            merged = dict(creds)
            merged['clientId'] = client_id
            merged['clientSecret'] = client_secret
            merged['connectorScope'] = auth_cfg.get("connectorScope")
            return merged
        except Exception as e:
            logger.error(f"❌ Failed to get individual token for {service_name}: {str(e)}")
            raise

    @staticmethod
    async def get_enterprise_token(
        service_name: str,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Handle enterprise token for a specific connector."""
        config = await GoogleClient._get_connector_config(service_name, logger, config_service, connector_instance_id)
        return config.get("auth", {})

    # =========================================================================
    # TOOLSET-BASED CLIENT CREATION (New Architecture)
    # =========================================================================

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: Dict[str, Any],
        service_name: str,
        logger: logging.Logger,
        config_service: Optional[ConfigurationService] = None,
        version: str = "v3",
        scopes: Optional[List[str]] = None,
    ) -> 'GoogleClient':
        """
        Build GoogleClient from toolset configuration stored in etcd.

        ARCHITECTURE NOTE: OAuth credentials (clientId/clientSecret) are fetched from
        the OAuth config using the oauthConfigId stored in toolset_config. This keeps
        credentials centralized and secure while allowing per-user authentication.

        Args:
            toolset_config: Toolset configuration from etcd containing:
                - credentials: { access_token, refresh_token, expires_in }
                - isAuthenticated: bool
                - oauthConfigId: ID of the OAuth config (for fetching clientId/clientSecret)
            service_name: Name of Google service (drive, calendar, gmail)
            logger: Logger instance
            config_service: ConfigurationService for fetching OAuth config (required for OAuth)
            version: API version (v1, v3, etc.)
            scopes: Optional scopes to use

        Returns:
            GoogleClient instance

        Raises:
            ValueError: If configuration is invalid or missing required fields
        """
        if not toolset_config:
            raise ValueError("Toolset configuration is required")

        # Extract auth and credentials
        auth_config = toolset_config.get("auth", {})
        credentials_config = toolset_config.get("credentials", {})
        is_authenticated = toolset_config.get("isAuthenticated", False)

        if not is_authenticated:
            raise ValueError("Toolset is not authenticated. Please complete OAuth flow first.")

        if not credentials_config:
            raise ValueError(
                "Toolset has no credentials. Please re-authenticate. "
                f"Toolset config keys: {list(toolset_config.keys())}"
            )

        # Log credential structure for debugging
        logger.debug(
            f"Toolset config structure - auth keys: {list(auth_config.keys())}, "
            f"credentials keys: {list(credentials_config.keys())}"
        )

        # Get OAuth credentials from credentials dict (stored by OAuthProvider.handle_callback)
        # OAuthToken.to_dict() stores: access_token, refresh_token, expires_in, token_type, etc.
        access_token = credentials_config.get("access_token")
        refresh_token = credentials_config.get("refresh_token")  # May be None if not provided by provider
        expires_in = credentials_config.get("expires_in")

        if not access_token:
            raise ValueError(
                f"Access token not found in toolset credentials. "
                f"Available credential keys: {list(credentials_config.keys())}"
            )

        # Fetch complete OAuth configuration from centralized OAuth config
        # This includes clientId, clientSecret, and any provider-specific fields
        try:
            from app.api.routes.toolsets import get_oauth_credentials_for_toolset

            if not config_service:
                raise ValueError(
                    "ConfigurationService is required to fetch OAuth configuration. "
                    "Please pass config_service parameter to build_from_toolset."
                )

            # Get complete OAuth config (all fields)
            oauth_config = await get_oauth_credentials_for_toolset(
                toolset_config=toolset_config,
                config_service=config_service,
                logger=logger
            )

            # Extract required fields (support both camelCase and snake_case)
            client_id = oauth_config.get("clientId") or oauth_config.get("client_id")
            client_secret = oauth_config.get("clientSecret") or oauth_config.get("client_secret")

            if not client_id or not client_secret:
                raise ValueError(
                    f"OAuth configuration is missing clientId or clientSecret. "
                    f"Available fields: {list(oauth_config.keys())}"
                )

        except Exception as e:
            logger.error(f"Failed to fetch OAuth configuration for Google {service_name}: {e}")
            raise ValueError(
                f"Failed to retrieve OAuth configuration: {str(e)}. "
                f"Please ensure the toolset instance has a valid OAuth configuration."
            ) from e

        # Warn if refresh_token is missing (will cause refresh failures when token expires)
        # Google's OAuth library requires refresh_token to automatically refresh expired tokens
        if not refresh_token:
            logger.warning(
                f"⚠️ Refresh token is missing for Google {service_name} toolset. "
                f"Token refresh will fail when access token expires (expires_in={expires_in}s). "
                f"Please re-authenticate with 'prompt=consent' to get a refresh_token. "
                f"Current credentials: access_token={'***' if access_token else 'MISSING'}, "
                f"refresh_token=None"
            )
            # Don't fail immediately - allow tool to work until token expires
            # The token will work for initial requests, but refresh will fail

        # Get optimized scopes
        optimized_scopes = cls._get_optimized_scopes(service_name, scopes)

        try:
            # Create Google credentials
            # Note: refresh_token may be None, which will cause refresh failures when token expires
            # But we allow this to let tools work until the token expires
            google_credentials = Credentials(
                token=access_token,
                refresh_token=refresh_token,  # May be None - will cause refresh failures
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=optimized_scopes,
            )

            if refresh_token:
                logger.debug(
                    f"✅ Created Google {service_name} credentials with all required fields including refresh_token"
                )
            else:
                logger.warning(
                    f"⚠️ Created Google {service_name} credentials WITHOUT refresh_token. "
                    f"Token will work until expiration ({expires_in}s), then refresh will fail."
                )

            # Create Google service client
            client = build(service_name, version, credentials=google_credentials)

            logger.info(f"Created Google {service_name} client from toolset config")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Google client from toolset: {e}")
            raise ValueError(f"Failed to create Google {service_name} client: {str(e)}") from e
