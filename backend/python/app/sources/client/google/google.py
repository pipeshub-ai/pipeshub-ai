import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.sources.google.common.connector_google_exceptions import (
    AdminAuthError,
    AdminDelegationError,
    AdminServiceError,
    GoogleAuthError,
)
from app.connectors.sources.google.common.google_token_handler import (
    CredentialKeys,
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

        if is_individual:
            try:
                #fetch saved credentials
                saved_credentials = await GoogleClient.get_individual_token(service_name, logger, config_service)
                if not saved_credentials:
                    raise ValueError("Failed to get individual token")

                # Get optimized scopes for the service
                optimized_scopes = GoogleClient._get_optimized_scopes(service_name, scopes)

                google_credentials = Credentials(
                    token=saved_credentials.get(CredentialKeys.ACCESS_TOKEN.value),
                    refresh_token=saved_credentials.get(CredentialKeys.REFRESH_TOKEN.value),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=saved_credentials.get(CredentialKeys.CLIENT_ID.value),
                    client_secret=saved_credentials.get(CredentialKeys.CLIENT_SECRET.value),
                    scopes=optimized_scopes,
                )

                # Create Google Drive service using the credentials
                client = build(service_name, version, credentials=google_credentials)
            except Exception as e:
                raise GoogleAuthError("Failed to get individual token: " + str(e)) from e
        else:
            try:
                saved_credentials = await GoogleClient.get_enterprise_token(service_name, logger, config_service)
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
    async def _get_connector_config(service_name: str,logger: logging.Logger, config_service: ConfigurationService) -> Dict:
        """Fetch connector config from etcd for the given app."""
        try:
            service_name = service_name.replace(" ", "").lower()
            config = await config_service.get_config(
                f"/services/connectors/{service_name}/config"
            )
            return config or {}
        except Exception as e:
            logger.error(f"❌ Failed to get connector config for {service_name}: {e}")
            return {}


    @staticmethod
    async def get_account_scopes(service_name: str, logger: logging.Logger, config_service: ConfigurationService) -> list:
        """Get account scopes for a specific connector (gmail/drive)."""
        config = await GoogleClient._get_connector_config(service_name, logger, config_service)
        return config.get("config", {}).get("auth", {}).get("scopes", [])


    @staticmethod
    async def get_individual_token(service_name: str, logger: logging.Logger,config_service: ConfigurationService, ) -> dict:
        """Get individual OAuth token for a specific connector (gmail/drive/calendar/)."""

        try:
            config = await GoogleClient._get_connector_config(service_name, logger, config_service)
            creds = (config or {}).get("credentials") or {}
            # Do not persist client secrets under credentials in storage; only enrich the returned view
            auth_cfg = (config or {}).get("auth", {}) or {}
            if creds:
                # Return a merged view including client info for SDK constructors
                merged = dict(creds)
                merged['clientId'] = auth_cfg.get("clientId")
                merged['clientSecret'] = auth_cfg.get("clientSecret")
                return merged
        except Exception as e:
            logger.error(f"❌ Failed to get individual token for {service_name}: {str(e)}")
            raise

    @staticmethod
    async def get_enterprise_token(
        service_name: str,
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> dict[str, Any]:
        """Handle enterprise token for a specific connector."""
        config = await GoogleClient._get_connector_config(service_name, logger, config_service)
        return config.get("auth", {})
