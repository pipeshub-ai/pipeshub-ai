import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config.configuration_service import ConfigurationService

try:
    from azure.identity import (  #type: ignore
        InteractiveBrowserCredential,
    )
    from azure.identity.aio import ClientSecretCredential  #type: ignore
    from kiota_authentication_azure.azure_identity_authentication_provider import (  #type: ignore
        AzureIdentityAuthenticationProvider,
    )
    from kiota_http.httpx_request_adapter import HttpxRequestAdapter  #type: ignore
    from msgraph import GraphServiceClient  #type: ignore
except ImportError:
    raise ImportError("azure-identity is not installed. Please install it with `pip install azure-identity`")

from app.sources.client.iclient import IClient


class GraphMode(str, Enum):
    DELEGATED = "delegated"
    APP = "app"
@dataclass
class MSGraphResponse:
    """Standardized response wrapper for Microsoft Graph operations."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate response state."""
        if self.success and self.error:
            raise ValueError("Response cannot be successful and have an error")


class MSGraphClientViaUsernamePassword:
    """Microsoft Graph client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, username: str, password: str, client_id: str, tenant_id: str, mode: GraphMode = GraphMode.APP) -> None:
        self.mode = mode
        #TODO: Implement
        pass

    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self.client

    def get_mode(self) -> GraphMode:
        return self.mode

class MSGraphClientWithCertificatePath:
    def __init__(self, certificate_path: str, tenant_id: str, client_id: str, mode: GraphMode = GraphMode.APP) -> None:
        self.mode = mode
        #TODO: Implement
        pass

    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self.client

    def get_mode(self) -> GraphMode:
        return self.mode

class MSGraphClientWithClientIdSecret:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        scopes: List[str] = ["https://graph.microsoft.com/.default"],
        mode: GraphMode = GraphMode.APP
    ) -> None:
        self.mode = mode
        if mode == GraphMode.DELEGATED:
            #Delegated (user) auth using Interactive Browser
            #Scopes: use Graph permissions you actually need (read/write as needed).
            credential = InteractiveBrowserCredential(
                client_id=client_id,
                tenant_id=tenant_id,
                redirect_uri="http://localhost:8080" #TODO: change to the actual redirect uri
                # No client_secret needed for public clients doing delegated auth
            )
            auth_provider = AzureIdentityAuthenticationProvider(credential, scopes=scopes)
            adapter = HttpxRequestAdapter(auth_provider)
            self.client = GraphServiceClient(request_adapter=adapter)
        elif mode == GraphMode.APP:
            # App-only (client credentials) auth for enterprise/service scenarios
            # Requires Application permissions + Admin consent.
            credential = ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
            auth_provider = AzureIdentityAuthenticationProvider(credential, scopes=scopes)
            adapter = HttpxRequestAdapter(auth_provider)
            self.client = GraphServiceClient(request_adapter=adapter)


    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self.client

    def get_mode(self) -> GraphMode:
        return self.mode

@dataclass
class MSGraphUsernamePasswordConfig:
    """Configuration for Microsoft Graph client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        client_id: The client id to use for authentication
        tenant_id: The tenant id to use for authentication
    """
    username: str
    password: str
    client_id: str
    tenant_id: str

    def create_client(self, mode: GraphMode = GraphMode.APP) -> MSGraphClientViaUsernamePassword:
        return MSGraphClientViaUsernamePassword(self.username, self.password, self.client_id, self.tenant_id, mode=mode)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class MSGraphClientWithClientIdSecretConfig:
    """Configuration for Microsoft Graph client via client id, client secret and tenant id
    Args:
        client_id: The client id to use for authentication
        client_secret: The client secret to use for authentication
        tenant_id: The tenant id to use for authentication
    """
    client_id: str
    client_secret: str
    tenant_id: str

    def create_client(self, mode: GraphMode = GraphMode.APP) -> MSGraphClientWithClientIdSecret:
        return MSGraphClientWithClientIdSecret(self.client_id, self.client_secret, self.tenant_id, mode=mode)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class MSGraphClientWithCertificatePathConfig:
    """Configuration for Microsoft Graph client via certificate path
    Args:
        certificate_path: The path to the certificate to use for authentication
        tenant_id: The tenant id to use for authentication
        client_id: The client id to use for authentication
    """
    certificate_path: str
    tenant_id: str
    client_id: str
    def create_client(self, mode: GraphMode = GraphMode.APP) -> MSGraphClientWithCertificatePath:
        return MSGraphClientWithCertificatePath(self.certificate_path, self.tenant_id, self.client_id, mode=mode)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class MSGraphClient(IClient):
    """Builder class for Microsoft Graph clients with different construction methods"""

    def __init__(
        self,
        client: MSGraphClientViaUsernamePassword | MSGraphClientWithClientIdSecret | MSGraphClientWithCertificatePath,
        mode: GraphMode = GraphMode.APP) -> None:
        """Initialize with a Microsoft Graph client object"""
        self.client = client
        self.mode = mode

    def get_client(self) -> MSGraphClientViaUsernamePassword | MSGraphClientWithClientIdSecret | MSGraphClientWithCertificatePath:
        """Return the Microsoft Graph client object"""
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: MSGraphUsernamePasswordConfig | MSGraphClientWithClientIdSecretConfig | MSGraphClientWithCertificatePathConfig, #type:ignore
        mode: GraphMode = GraphMode.APP) -> 'MSGraphClient':
        """
        Build MSGraphClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: MSGraphConfigBase instance
        Returns:
            MSGraphClient instance with placeholder implementation
        """
        return cls(config.create_client(mode))

    @classmethod
    async def build_from_services(
        cls,
        service_name: str,
        logger: logging.Logger,
        config_service: ConfigurationService,
        mode: GraphMode = GraphMode.APP,
    ) -> 'MSGraphClient':
        """
        Build MSGraphClient using configuration service
        Args:
            service_name: Service name
            logger: Logger instance
            config_service: Configuration service instance
            mode: Graph mode (APP or DELEGATED)
        Returns:
            MSGraphClient instance
        """
        try:
            # Get Microsoft Graph configuration from the configuration service
            config = await cls._get_connector_config(service_name.replace(" ", "").lower(), logger, config_service)

            if not config:
                raise ValueError("Failed to get Microsoft Graph connector configuration")
            auth_config = config.get("auth",{}) or {}
            # Extract configuration values
            auth_type = auth_config.get("authType", "OAUTH_ADMIN_CONSENT")  # client_secret, username_password, certificate
            tenant_id = auth_config.get("tenantId", "")
            client_id = auth_config.get("clientId", "")

            if not tenant_id or not client_id:
                raise ValueError("Tenant ID and Client ID are required for Microsoft Graph authentication")

            # Create appropriate client based on auth type
            # to be implemented
            if auth_type == "USERNAME_PASSWORD":
                username = auth_config.get("username", "")
                password = auth_config.get("password", "")
                if not username or not password:
                    raise ValueError("Username and password required for username_password auth type")
                client = MSGraphClientViaUsernamePassword(username, password, client_id, tenant_id, mode)

            # to be implemented
            # elif auth_type == "OAUTH":
            #     access_token = config.get("credentials",{}).get("accessToken", "")
            #     certificate_path = config.get("credentials",{}).get("certificatePath", "")
            #     if not certificate_path:
            #         raise ValueError("Certificate path required for certificate auth type")
            #     client = MSGraphClientWithCertificatePath(certificate_path, tenant_id, client_id, mode)

            elif auth_type == "OAUTH_ADMIN_CONSENT":  # Default to client_secret auth
                client_secret = auth_config.get("clientSecret", "")
                if not client_secret:
                    raise ValueError("Client secret required for client_secret auth type")
                scopes = auth_config.get("scopes", ["https://graph.microsoft.com/.default"])
                client = MSGraphClientWithClientIdSecret(client_id, client_secret, tenant_id, scopes, mode)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client, mode)

        except Exception as e:
            logger.error(f"Failed to build Microsoft Graph client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(service_name: str, logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Microsoft Graph."""
        try:
            config = await config_service.get_config(f"/services/connectors/{service_name}/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Microsoft Graph connector config: {e}")
            return {}
