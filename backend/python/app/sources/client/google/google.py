from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import jwt  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
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
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
    GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES,
    GOOGLE_PARSER_SCOPES,
)
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
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
        graph_db_service: IGraphService,
        is_individual: Optional[bool] = False,
        version: Optional[str] = "v3", # Version of the service to build the client for [v3, v1]
        scopes: Optional[List[str]] = None, # Scopes of the service to build the client
        calendar_id: Optional[str] = 'primary', # Calendar ID to build the client for
    ) -> 'GoogleClient':
        """
        Build GoogleClient using configuration service and arango service
        Args:
            service_name: Name of the service to build the client for
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: GraphDB service instance
            org_id: Organization ID
            user_id: User ID
            is_individual: Flag to indicate if the client is for an individual user or an enterprise account
            version: Version of the service to build the client for
        Returns:
            GoogleClient instance
        """
        # TODO Cleanup this code for orgId and userId fetch
        # get org id
        query = f"""
            FOR org IN {CollectionNames.ORGS.value}
            FILTER @active == false || org.isActive == true
            RETURN org
            """

        bind_vars = {"active": True}
        orgs = await graph_db_service.execute_query(query, bind_vars=bind_vars)
        if not orgs:
            raise ValueError("Org ID not found")
        org_id = orgs[0]["_key"]
        if is_individual:
            try:
                # get user id
                query = """
                        FOR edge IN belongsTo
                            FILTER edge._to == CONCAT('organizations/', @org_id)
                            AND edge.entityType == 'ORGANIZATION'
                            LET user = DOCUMENT(edge._from)
                            FILTER @active == false OR user.isActive == true
                            RETURN user
                        """

                users = await graph_db_service.execute_query(query, bind_vars={"org_id": org_id, "active": True})
                if not users:
                    raise ValueError("User not found for the given organization.")
                user_id = users[0]["userId"]
                if not user_id:
                    raise ValueError("User ID is missing in the user document.")
                #fetch saved credentials
                saved_credentials = await GoogleClient.get_individual_token(org_id, user_id, config_service)
                if not saved_credentials:
                    raise ValueError("Failed to get individual token")
                google_credentials = Credentials(
                    token=saved_credentials.get(CredentialKeys.ACCESS_TOKEN.value),
                    refresh_token=saved_credentials.get(CredentialKeys.REFRESH_TOKEN.value),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=saved_credentials.get(CredentialKeys.CLIENT_ID.value),
                    client_secret=saved_credentials.get(CredentialKeys.CLIENT_SECRET.value),
                    scopes=merge_scopes(GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES, scopes),
                )

                # Create Google Drive service using the credentials
                client = build(service_name, version, credentials=google_credentials)
            except Exception as e:
                raise GoogleAuthError("Failed to get individual token: " + str(e)) from e
        else:
            try:
                # Use the specific connector name from service_name to fetch the right credentials
                # Map common service_name to connector key
                connector_name = "DRIVE" if service_name.lower() == "drive" else (
                    "GMAIL" if service_name.lower() == "gmail" else service_name.upper()
                )
                saved_credentials = await GoogleClient.get_enterprise_token(org_id, config_service, app_name=connector_name)
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
                            scopes=merge_scopes(GOOGLE_CONNECTOR_ENTERPRISE_SCOPES + GOOGLE_PARSER_SCOPES, scopes),
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
                client = build(
                    service_name,
                    version,
                    credentials=google_credentials,
                    cache_discovery=False,
                )
            except Exception as e:
                raise AdminServiceError(
                    "Failed to build admin service: " + str(e),
                    details={"org_id": org_id, "error": str(e)},
                )

        return cls(client)

    @staticmethod
    async def _fetch_credentials(
        *,
        org_id: str,
        config_service: ConfigurationService,
        route: Routes,
        user_id: Optional[str] = None,
        scopes: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Build JWT, resolve endpoint, call credentials API, return JSON."""
        # 1) Compose payload
        payload: Dict[str, Any] = {
            "orgId": org_id,
            "scopes": scopes or [TokenScopes.FETCH_CONFIG.value],
        }
        if user_id is not None:
            payload["userId"] = user_id

        # 2) Get secrets
        secret_keys = await config_service.get_config(config_node_constants.SECRET_KEYS.value)
        if not secret_keys:
            raise Exception("Secret keys not found")
        if not isinstance(secret_keys, dict):
            raise Exception("Secret keys must be a dictionary")
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        if not scoped_jwt_secret:
            raise Exception("Scoped JWT secret not found")

        # 3) Create JWT
        jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")
        # 4) Resolve endpoint
        endpoints = await config_service.get_config(config_node_constants.ENDPOINTS.value)
        if not endpoints:
            raise Exception("Endpoints config not found")
        if not isinstance(endpoints, dict):
            raise Exception("Endpoints must be a dictionary")
        cm = endpoints.get("cm") or {}
        nodejs_endpoint = cm.get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)
        if not nodejs_endpoint:
            raise Exception("NodeJS endpoint not found")

        # 5) Call API
        url = f"{nodejs_endpoint}{route.value}"
        http_client = HTTPClient(token=jwt_token)
        request = HTTPRequest(url=url, method="GET", body=payload)  # Note: GET with body is unusual
        response = await http_client.execute(request)
        return response.json()

    @staticmethod
    async def get_individual_token(
        org_id: str,
        user_id: str,
        config_service: ConfigurationService,
    ) -> dict[str, Any]:
        """Handle individual token."""
        return await GoogleClient._fetch_credentials(
            org_id=org_id,
            user_id=user_id,
            config_service=config_service,
            route=Routes.INDIVIDUAL_CREDENTIALS,
            scopes=[TokenScopes.FETCH_CONFIG.value],
        )

    @staticmethod
    async def get_enterprise_token(
        org_id: str,
        config_service: ConfigurationService,
        app_name: str = "DRIVE",
    ) -> dict[str, Any]:
        """Handle enterprise token for a specific connector."""
        filtered_app_name = app_name.replace(" ", "").lower()
        config = await config_service.get_config(f"/services/connectors/{filtered_app_name}/config")
        return config.get("auth", {})
