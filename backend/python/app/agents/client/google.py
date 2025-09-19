from dataclasses import dataclass
from typing import List, Optional

from app.agents.client.iclient import IClient
from app.config.configuration_service import ConfigurationService
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.connectors.sources.google.common.connector_google_exceptions import (
    AdminAuthError,
    AdminDelegationError,
    AdminServiceError,
    GoogleAuthError,
)
from app.connectors.sources.google.common.google_token_handler import (
    CredentialKeys,
    GoogleTokenHandler,
)
from app.connectors.sources.google.common.scopes import (
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
    GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES,
    GOOGLE_PARSER_SCOPES,
)

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
        arango_service,
        org_id: str,
        user_id: str,
        is_individual: Optional[bool] = False,
        version: Optional[str] = "v3",
        scopes: Optional[List[str]] = None,
        calendar_id: Optional[str] = 'primary',
        key_value_store: Optional[Etcd3EncryptedKeyValueStore] = None
    ) -> 'GoogleClient':
        """
        Build GoogleClient using configuration service and arango service
        Args:
            service_name: Name of the service to build the client for
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
            is_individual: Flag to indicate if the client is for an individual user or an enterprise account
            version: Version of the service to build the client for [v3, v1]
            scopes: Scopes of the service to build the client for
            calendar_id: Calendar ID to build the client for
        Returns:
            GoogleClient instance
        """
        # Create GoogleTokenHandler instance
        google_token_handler = GoogleTokenHandler(
            logger=logger,
            config_service=config_service,
            arango_service=arango_service,
            key_value_store=key_value_store
        )

        # Normalize which connector the credentials should come from
        # drive/gmail/selectors default to 'drive' for Drive service and 'gmail' for Gmail service
        connector_name = 'drive'
        if service_name.lower() in ['gmail']:
            connector_name = 'gmail'

        if is_individual:
            try:
                # Prefer config-based credentials first (written by Python OAuth flow)
                saved_credentials = await google_token_handler.get_individual_credentials_from_config(connector_name)
                if not saved_credentials or not saved_credentials.get(CredentialKeys.ACCESS_TOKEN.value):
                    # Fallback to NodeJS tokens manager API if config-based creds missing
                    saved_credentials = await google_token_handler.get_individual_token(org_id, user_id,app_name=connector_name)

                google_credentials = Credentials(
                    token=saved_credentials.get(CredentialKeys.ACCESS_TOKEN.value),
                    refresh_token=saved_credentials.get(CredentialKeys.REFRESH_TOKEN.value),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=saved_credentials.get(CredentialKeys.CLIENT_ID.value),
                    client_secret=saved_credentials.get(CredentialKeys.CLIENT_SECRET.value),
                    scopes=scopes or GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES,
                )

                # Create Google service using the credentials
                client = build(service_name, version, credentials=google_credentials)
            except Exception as e:
                raise GoogleAuthError("Failed to get individual token: " + str(e)) from e
        else:
            try:
                # Read service account JSON from the connector's config
                saved_credentials = await google_token_handler.get_enterprise_credentials_from_config(connector_name)
                if not saved_credentials:
                    raise AdminAuthError(
                        "Failed to get enterprise credentials",
                        details={"org_id": org_id},
                    )

                admin_email = saved_credentials.get("adminEmail")
                if not admin_email:
                    raise AdminAuthError(
                        "Admin email not found in credentials",
                        details={"org_id": org_id},
                    )
            except Exception as e:
                raise AdminAuthError("Failed to get enterprise token: " + str(e))

            try:
                google_credentials = (
                        service_account.Credentials.from_service_account_info(
                            saved_credentials,
                            scopes=scopes or GOOGLE_CONNECTOR_ENTERPRISE_SCOPES + GOOGLE_PARSER_SCOPES,
                            subject=admin_email
                        )
                    )
            except Exception as e:
                raise AdminDelegationError(
                    "Failed to create delegated credentials: " + str(e),
                    details={
                        "org_id": org_id,
                        "admin_email": admin_email,
                        "error": str(e),
                    },
                )

            try:
                # Build the requested service for enterprise as well
                client = build(
                    service_name,
                    version if service_name != 'admin' else 'directory_v1',
                    credentials=google_credentials,
                    cache_discovery=False if service_name == 'admin' else True,
                )
            except Exception as e:
                raise AdminServiceError(
                    "Failed to build admin service: " + str(e),
                    details={"org_id": org_id, "error": str(e)},
                )

        return cls(client)

