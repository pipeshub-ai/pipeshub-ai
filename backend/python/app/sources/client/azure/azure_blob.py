import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

try:
    from azure.core.exceptions import AzureError  # type: ignore
    from azure.storage.blob import BlobServiceClient  # type: ignore
    from azure.storage.blob.aio import (  # type: ignore
        BlobServiceClient as AsyncBlobServiceClient,
    )
except ImportError:
    raise ImportError("azure-storage-blob is not installed. Please install it with `pip install azure-storage-blob`")

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class AzureBlobConfigurationError(Exception):
    """Custom exception for Azure Blob Storage configuration errors"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class AzureBlobContainerError(Exception):
    """Custom exception for Azure Blob Storage container-related errors"""
    def __init__(self, message: str, container_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.container_name = container_name
        self.details = details or {}


@dataclass
class AzureBlobResponse:
    """Standardized Azure Blob Storage API response wrapper"""
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


@dataclass
class AzureBlobConnectionStringConfig:
    """Configuration for Azure Blob Storage using Connection String
    Args:
        azureBlobConnectionString: The Azure Blob Storage connection string
    """
    azureBlobConnectionString: str

    def __post_init__(self) -> None:
        """Validate configuration after initialization"""
        if not self.azureBlobConnectionString or not self.azureBlobConnectionString.strip():
            raise AzureBlobConfigurationError(
                "azureBlobConnectionString cannot be empty or None",
                {"provided_value": self.azureBlobConnectionString}
            )

    def create_blob_service_client(self) -> BlobServiceClient:
        """Create BlobServiceClient using connection string"""
        try:
            return BlobServiceClient.from_connection_string(
                conn_str=self.azureBlobConnectionString
            )
        except (ValueError, AzureError) as e:
            raise AzureBlobConfigurationError(
                f"Failed to create BlobServiceClient from connection string: {str(e)}",
                {"connection_string_length": len(self.azureBlobConnectionString)}
            ) from e

    async def create_async_blob_service_client(self) -> AsyncBlobServiceClient:
        """Create AsyncBlobServiceClient using connection string"""
        try:
            return AsyncBlobServiceClient.from_connection_string(
                conn_str=self.azureBlobConnectionString
            )
        except (ValueError, AzureError) as e:
            raise AzureBlobConfigurationError(
                f"Failed to create BlobServiceClient from connection string: {str(e)}",
                {"connection_string_length": len(self.azureBlobConnectionString)}
            ) from e

    def get_account_name(self) -> Optional[str]:
        """Extract account name from connection string"""
        try:
            for part in self.azureBlobConnectionString.split(';'):
                if part.startswith('AccountName='):
                    return part.split('=', 1)[1]
        except IndexError as e:
            raise AzureBlobConfigurationError(
                "Could not parse AccountName from connection string",
                {"connection_string_length": len(self.azureBlobConnectionString)}
            ) from e
        return None

    def get_authentication_method(self) -> str:
        """Get authentication method"""
        return "connection_string"

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return {
            'authentication_method': 'connection_string',
            'account_name': self.get_account_name()
        }


class AzureBlobRESTClient:
    """Azure Blob Storage REST client that handles blob operations internally"""

    def __init__(self, config: AzureBlobConnectionStringConfig) -> None:
        """Initialize with configuration"""
        self.config = config
        self._blob_service_client: Optional[BlobServiceClient] = None
        self._async_blob_service_client: Optional[AsyncBlobServiceClient] = None

    def get_blob_service_client(self) -> BlobServiceClient:
        """Get or create the BlobServiceClient"""
        if self._blob_service_client is None:
            try:
                self._blob_service_client = self.config.create_blob_service_client()
            except Exception as e:
                raise AzureBlobConfigurationError(
                    f"Failed to create BlobServiceClient: {str(e)}",
                    {"authentication_method": self.config.get_authentication_method()}
                )
        return self._blob_service_client

    async def get_async_blob_service_client(self) -> AsyncBlobServiceClient:
        """Get or create the AsyncBlobServiceClient"""
        if self._async_blob_service_client is None:
            try:
                self._async_blob_service_client = await self.config.create_async_blob_service_client()
            except Exception as e:
                raise AzureBlobConfigurationError(
                    f"Failed to create AsyncBlobServiceClient: {str(e)}",
                    {"authentication_method": self.config.get_authentication_method()}
                )
        return self._async_blob_service_client


    def get_account_name(self) -> str:
        """Get the account name"""
        account_name = self.config.get_account_name()
        if not account_name:
            raise AzureBlobConfigurationError("Could not determine account name from configuration")
        return account_name

    def get_account_url(self) -> str:
        """Get the account URL"""
        account_name = self.config.get_account_name()
        if account_name:
            return f"https://{account_name}.blob.core.windows.net" # TODO : need to make this work for Azure sovereign clouds or custom endpoints(A more robust approach would be to retrieve the account URL from the BlobServiceClient instance itself)
        raise AzureBlobConfigurationError("Could not determine account URL from configuration")

    def get_authentication_method(self) -> str:
        """Get the authentication method being used"""
        return self.config.get_authentication_method()

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information"""
        return self.config.to_dict()

    async def close_async_client(self) -> None:
        """Close the async blob service client"""
        if self._async_blob_service_client:
            await self._async_blob_service_client.close()
            self._async_blob_service_client = None

    async def ensure_container_exists(self, container_name: str) -> AzureBlobResponse:
        """Ensure the specified container exists, create if it doesn't"""
        if not container_name:
            raise AzureBlobContainerError(
                "containerName is required for this operation",
                container_name=None,
                details={"error": "No container name provided"}
            )

        try:
            async_blob_service_client = await self.get_async_blob_service_client()
            container_client = async_blob_service_client.get_container_client(container_name)

            if not await container_client.exists():
                await container_client.create_container()
                return AzureBlobResponse(
                    success=True,
                    data={
                        'container_name': container_name,
                        'action': 'created',
                        'message': f'Container "{container_name}" created successfully'
                    },
                    message=f'Container "{container_name}" created successfully'
                )
            else:
                return AzureBlobResponse(
                    success=True,
                    data={
                        'container_name': container_name,
                        'action': 'exists',
                        'message': f'Container "{container_name}" already exists'
                    },
                    message=f'Container "{container_name}" already exists'
                )

        except AzureBlobContainerError:
            raise
        except AzureError as e:
            raise AzureBlobContainerError(
                f"Azure error while ensuring container exists: {str(e)}",
                container_name=container_name,
                details={"azure_error": str(e)}
            )
        except Exception as e:
            raise AzureBlobContainerError(
                f"Unexpected error while ensuring container exists: {str(e)}",
                container_name=container_name,
                details={"unexpected_error": str(e)}
            )


class AzureBlobClient(IClient):
    """Builder class for Azure Blob Storage clients with validation"""

    def __init__(self, client: AzureBlobRESTClient) -> None: # type: ignore
        """Initialize with AzureBlobRESTClient"""
        self.client = client

    def get_client(self) -> AzureBlobRESTClient:
        """Return the AzureBlobRESTClient object"""
        return self.client


    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information"""
        return self.client.get_credentials_info()

    def get_account_name(self) -> str:
        """Get the account name"""
        return self.client.get_account_name()

    def get_authentication_method(self) -> str:
        """Get the authentication method being used"""
        return self.client.get_authentication_method()

    async def ensure_container_exists(self, container_name: str) -> AzureBlobResponse:
        """Ensure the specified container exists, create if it doesn't"""
        return await self.client.ensure_container_exists(container_name)

    async def get_async_blob_service_client(self) -> AsyncBlobServiceClient:
        """Get or create the AsyncBlobServiceClient"""
        return await self.client.get_async_blob_service_client()

    async def close_async_client(self) -> None:
        """Close the async blob service client"""
        await self.client.close_async_client()

    @classmethod
    def build_with_connection_string_config(cls, config: AzureBlobConnectionStringConfig) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient with connection string configuration"""
        try:
            rest_client = AzureBlobRESTClient(config)
            return cls(rest_client)
        except Exception as e:
            if isinstance(e, (AzureBlobConfigurationError, AzureBlobContainerError)):
                raise
            raise AzureBlobConfigurationError(f"Failed to build client with connection string config: {str(e)}")

    @classmethod
    def build_with_connection_string(
        cls,
        azureBlobConnectionString: str
    ) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient with connection string directly"""
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=azureBlobConnectionString
        )
        return cls.build_with_connection_string_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient using configuration service and graphdb service"""
        try:
            # Get Azure Blob Storage configuration from config service
            config_data = await cls._get_connector_config(config_service, connector_instance_id)

            # Extract configuration parameters
            auth_config = config_data.get("auth", {})
            connection_string = auth_config.get("azureBlobConnectionString")
            if not connection_string:
                raise AzureBlobConfigurationError("azureBlobConnectionString is required for Azure Blob Storage authentication")

            config = AzureBlobConnectionStringConfig(
                azureBlobConnectionString=connection_string
            )
            return cls.build_with_connection_string_config(config)

        except Exception as e:
            logger.error(f"Failed to build Azure Blob Storage client from services: {e}")
            raise AzureBlobConfigurationError(f"Failed to build Azure Blob Storage client: {str(e)}")

    @staticmethod
    async def _get_connector_config(config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Get connector configuration from config service"""
        try:
            config_path = f"/services/connectors/{connector_instance_id}/config"
            config_data = await config_service.get_config(config_path)
            if not config_data:
                raise ValueError(f"Failed to get Azure Blob Storage connector configuration for instance {connector_instance_id}")
            return config_data
        except Exception as e:
            raise AzureBlobConfigurationError(f"Failed to get {connector_instance_id} configuration: {str(e)}")
