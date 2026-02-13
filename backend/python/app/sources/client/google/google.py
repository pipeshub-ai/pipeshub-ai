import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.google.common.connector_google_exceptions import (
    AdminAuthError,
    AdminDelegationError,
    AdminServiceError,
    GoogleAuthError,
)
from app.connectors.sources.google.common.google_token_handler import (
    CredentialKeys,
)
from app.sources.client.iclient import IClient

try:
    from google.oauth2 import service_account  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
except ImportError:
    print("Google API client libraries not found. Please install them using 'pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib'")
    raise


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
                oauth_scopes = saved_credentials.get('scope')
                if oauth_scopes:
                    # Handle both string (space-separated) and list formats
                    if isinstance(oauth_scopes, str):
                        credential_scopes = [s.strip() for s in oauth_scopes.split()] if oauth_scopes.strip() else []
                    else:
                        credential_scopes = oauth_scopes if isinstance(oauth_scopes, list) else []
                    logger.info(f"Using authorized scopes from credentials: {credential_scopes}")
                else:
                    # Fallback: this should rarely happen
                    logger.warning(f"No scope found in stored credentials for {connector_instance_id}")
                    credential_scopes = []

                if not client_id or not client_secret:
                    logger.error(f"Missing OAuth client credentials (client_id: {bool(client_id)}, client_secret: {bool(client_secret)}). These are required for token refresh. Please re-authenticate the connector.")
                    raise ValueError(
                        f"Missing OAuth client credentials (client_id: {bool(client_id)}, "
                        f"client_secret: {bool(client_secret)}). These are required for token refresh. "
                        f"Please re-authenticate the connector."
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
                oauth_scopes = saved_credentials.get('scope')
                if oauth_scopes:
                    # Handle both string (space-separated) and list formats
                    if isinstance(oauth_scopes, str):
                        credential_scopes = [s.strip() for s in oauth_scopes.split()] if oauth_scopes.strip() else []
                    else:
                        credential_scopes = oauth_scopes if isinstance(oauth_scopes, list) else []
                    logger.info(f"Using authorized scopes from credentials: {credential_scopes}")
                else:
                    # Fallback: this should rarely happen
                    logger.warning(f"No scope found in stored credentials for {connector_instance_id}")
                    credential_scopes = []
            except Exception as e:
                raise AdminAuthError("Failed to get enterprise token: " + str(e))

            try:
                google_credentials = (
                        service_account.Credentials.from_service_account_info(
                            saved_credentials,
                            scopes=credential_scopes,
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
