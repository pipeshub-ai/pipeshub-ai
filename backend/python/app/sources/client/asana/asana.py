import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import asana
from asana import ApiClient, Configuration

# try:
# except ImportError:
#     raise ImportError("asana is not installed. Please install it with `pip install asana`")
from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.iclient import IClient


@dataclass
class AsanaResponse:
    """Standardized Asana API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class AsanaRESTClientViaToken:
    """Asana REST client via Personal Access Token (PAT) or Service Account Token

    Uses the modern Asana SDK v5 Configuration and ApiClient pattern.

    Args:
        access_token: Personal Access Token or Service Account Token for authentication
        base_url: Optional base URL for the Asana API
        return_page_iterator: Whether to return page iterators (default: True)
    """
    def __init__(
        self,
        access_token: str,
        base_url: Optional[str] = None,
        return_page_iterator: bool = True,
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url
        self.return_page_iterator = return_page_iterator
        self._configuration: Optional[Configuration] = None
        self._api_client: Optional[ApiClient] = None

    def create_client(self) -> ApiClient:
        """Create Asana ApiClient using Configuration with access token."""
        # Create configuration object
        self._configuration = asana.Configuration()
        self._configuration.access_token = self.access_token

        # Set optional configurations
        if self.base_url:
            self._configuration.host = self.base_url

        self._configuration.return_page_iterator = self.return_page_iterator

        # Create API client with configuration
        self._api_client = asana.ApiClient(self._configuration)

        return self._api_client

    def get_api_client(self) -> ApiClient:
        """Get the Asana ApiClient instance."""
        if self._api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._api_client

    def get_configuration(self) -> Configuration:
        """Get the Asana Configuration instance."""
        if self._configuration is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._configuration


class AsanaRESTClientViaOAuth:
    """Asana REST client via OAuth2

    For OAuth authentication with the modern Asana SDK v5.
    Note: OAuth flow must be handled externally to obtain access_token.

    Args:
        access_token: OAuth access token
        base_url: Optional base URL for the Asana API
        return_page_iterator: Whether to return page iterators (default: True)
    """
    def __init__(
        self,
        access_token: str,
        base_url: Optional[str] = None,
        return_page_iterator: bool = True,
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url
        self.return_page_iterator = return_page_iterator
        self._configuration: Optional[Configuration] = None
        self._api_client: Optional[ApiClient] = None

    def create_client(self) -> ApiClient:
        """Create Asana ApiClient using OAuth access token."""
        # Create configuration object
        self._configuration = asana.Configuration()
        self._configuration.access_token = self.access_token

        # Set optional configurations
        if self.base_url:
            self._configuration.host = self.base_url

        self._configuration.return_page_iterator = self.return_page_iterator

        # Create API client with configuration
        self._api_client = asana.ApiClient(self._configuration)

        return self._api_client

    def get_api_client(self) -> ApiClient:
        """Get the Asana ApiClient instance."""
        if self._api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._api_client

    def get_configuration(self) -> Configuration:
        """Get the Asana Configuration instance."""
        if self._configuration is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._configuration


@dataclass
class AsanaTokenConfig:
    """Configuration for Asana REST client via Personal Access Token

    Args:
        token: Personal Access Token or Service Account Token
        base_url: Optional base URL for the Asana API
        return_page_iterator: Whether to return page iterators (default: True)
        ssl: Whether to use SSL (kept for interface parity)
    """
    token: str
    base_url: Optional[str] = None
    return_page_iterator: bool = True
    ssl: bool = True

    def create_client(self) -> AsanaRESTClientViaToken:
        """Create an Asana client with token authentication."""
        return AsanaRESTClientViaToken(
            access_token=self.token,
            base_url=self.base_url,
            return_page_iterator=self.return_page_iterator,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class AsanaOAuthConfig:
    """Configuration for Asana REST client via OAuth2

    Args:
        access_token: OAuth access token (obtained from OAuth flow)
        base_url: Optional base URL for the Asana API
        return_page_iterator: Whether to return page iterators (default: True)
        ssl: Whether to use SSL (kept for interface parity)
    """
    access_token: str
    base_url: Optional[str] = None
    return_page_iterator: bool = True
    ssl: bool = True

    def create_client(self) -> AsanaRESTClientViaOAuth:
        """Create an Asana client with OAuth authentication."""
        return AsanaRESTClientViaOAuth(
            access_token=self.access_token,
            base_url=self.base_url,
            return_page_iterator=self.return_page_iterator,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class AsanaServiceAccountConfig:
    """Configuration for Asana REST client via Service Account Token

    Service Accounts are an Enterprise tier feature.
    Functionally identical to Personal Access Token in SDK usage.

    Args:
        service_account_token: Service account token for authentication
        base_url: Optional base URL for the Asana API
        return_page_iterator: Whether to return page iterators (default: True)
        ssl: Whether to use SSL (kept for interface parity)
    """
    service_account_token: str
    base_url: Optional[str] = None
    return_page_iterator: bool = True
    ssl: bool = True

    def create_client(self) -> AsanaRESTClientViaToken:
        """Create an Asana client with service account authentication."""
        return AsanaRESTClientViaToken(
            access_token=self.service_account_token,
            base_url=self.base_url,
            return_page_iterator=self.return_page_iterator,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class AsanaClient(IClient):
    """Builder class for Asana clients with different construction methods

    This class follows the same pattern as SlackClient, DropboxClient, and BoxClient
    to provide a consistent interface for client construction.

    Uses the Asana SDK v5 pattern with Configuration and ApiClient.
    """

    def __init__(
        self,
        client: AsanaRESTClientViaToken | AsanaRESTClientViaOAuth,
    ) -> None:
        """Initialize with an Asana client object

        Args:
            client: Asana REST client instance (Token or OAuth)
        """
        self.client = client

    def get_client(
        self,
    ) -> AsanaRESTClientViaToken | AsanaRESTClientViaOAuth:
        """Return the underlying Asana client wrapper object

        Returns:
            Asana REST client instance
        """
        return self.client

    def get_api_client(self) -> ApiClient:
        """Return the Asana SDK ApiClient instance

        This is the main client used to instantiate API classes like:
        - asana.TasksApi(api_client)
        - asana.ProjectsApi(api_client)
        - asana.UsersApi(api_client)

        Returns:
            Asana ApiClient instance
        """
        return self.client.get_api_client()

    def get_configuration(self) -> Configuration:
        """Return the Asana Configuration instance

        Returns:
            Asana Configuration instance
        """
        return self.client.get_configuration()

    @classmethod
    def build_with_config(
        cls,
        config: AsanaTokenConfig | AsanaOAuthConfig | AsanaServiceAccountConfig,
    ) -> "AsanaClient":
        """Build AsanaClient with configuration

        Args:
            config: Asana configuration instance (dataclass)

        Returns:
            AsanaClient instance
        """
        client = config.create_client()
        client.create_client()
        return cls(client=client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> "AsanaClient":
        """Build AsanaClient using configuration service and graph database service

        This method would typically:
        1. Query graph_db_service for stored Asana credentials
        2. Use config_service to get environment-specific settings
        3. Return appropriate client based on available credentials

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: Graph database service instance
            org_id: Optional organization ID
            user_id: Optional user ID

        Returns:
            AsanaClient instance
        """
        # TODO: Implement - fetch config from services
        # Example implementation would look like:
        #
        # # Get credentials from graph database
        # credentials = await graph_db_service.get_asana_credentials(org_id, user_id)
        #
        # # Determine auth type and create appropriate config
        # if credentials.get('oauth_access_token'):
        #     config = AsanaOAuthConfig(
        #         access_token=credentials['oauth_access_token']
        #     )
        # elif credentials.get('service_account_token'):
        #     config = AsanaServiceAccountConfig(
        #         service_account_token=credentials['service_account_token']
        #     )
        # else:
        #     config = AsanaTokenConfig(token=credentials['personal_access_token'])
        #
        # return cls.build_with_config(config)

        logger.warning("build_from_services not yet implemented for AsanaClient")
        return cls(client=None)  # type: ignore
