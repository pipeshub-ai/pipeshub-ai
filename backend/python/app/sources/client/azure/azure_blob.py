import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

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
        containerName: The default container name (required)
    """
    azureBlobConnectionString: str
    containerName: str

    def __post_init__(self) -> None:
        """Validate configuration after initialization"""
        if not self.azureBlobConnectionString or not self.azureBlobConnectionString.strip():
            raise AzureBlobConfigurationError(
                "azureBlobConnectionString cannot be empty or None",
                {"provided_value": self.azureBlobConnectionString}
            )

        if not self.containerName or not self.containerName.strip():
            raise AzureBlobContainerError(
                "containerName is required and cannot be empty or None",
                container_name=self.containerName
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
            'account_name': self.get_account_name(),
            'container_name': self.containerName
        }


@dataclass
class AzureBlobAccountKeyConfig:
    """Configuration for Azure Blob Storage using Account Name and Key
    Args:
        accountName: The Azure storage account name
        accountKey: The Azure storage account key
        containerName: The default container name (required)
        endpointProtocol: The endpoint protocol (https/http, default: https)
        endpointSuffix: The endpoint suffix (default: core.windows.net)
    """
    accountName: str
    accountKey: str
    containerName: str
    endpointProtocol: str = "https"
    endpointSuffix: str = "core.windows.net"

    def __post_init__(self) -> None:
        """Validate configuration after initialization"""
        if not self.accountName or not self.accountName.strip():
            raise AzureBlobConfigurationError(
                "accountName cannot be empty or None",
                {"provided_value": self.accountName}
            )

        if not self.accountKey or not self.accountKey.strip():
            raise AzureBlobConfigurationError(
                "accountKey cannot be empty or None",
                {"account_name": self.accountName}
            )

        if not self.containerName or not self.containerName.strip():
            raise AzureBlobContainerError(
                "containerName is required and cannot be empty or None",
                container_name=self.containerName
            )

        if self.endpointProtocol not in ["https", "http"]:
            raise AzureBlobConfigurationError(
                f"endpointProtocol must be 'https' or 'http', got: {self.endpointProtocol}",
                {"provided_value": self.endpointProtocol}
            )

    def create_blob_service_client(self) -> BlobServiceClient:
        """Create BlobServiceClient using account name and key"""
        try:
            account_url = f"{self.endpointProtocol}://{self.accountName}.blob.{self.endpointSuffix}"
            return BlobServiceClient(
                account_url=account_url,
                credential=self.accountKey
            )
        except Exception as e:
            raise AzureBlobConfigurationError(
                f"Failed to create BlobServiceClient with account key: {str(e)}",
                {
                    "account_name": self.accountName,
                    "account_url": f"{self.endpointProtocol}://{self.accountName}.blob.{self.endpointSuffix}"
                }
            )

    async def create_async_blob_service_client(self) -> AsyncBlobServiceClient:
        """Create AsyncBlobServiceClient using account name and key"""
        try:
            account_url = f"{self.endpointProtocol}://{self.accountName}.blob.{self.endpointSuffix}"
            return AsyncBlobServiceClient(
                account_url=account_url,
                credential=self.accountKey
            )
        except Exception as e:
            raise AzureBlobConfigurationError(
                f"Failed to create AsyncBlobServiceClient with account key: {str(e)}",
                {
                    "account_name": self.accountName,
                    "account_url": f"{self.endpointProtocol}://{self.accountName}.blob.{self.endpointSuffix}"
                }
            )

    def get_account_name(self) -> str:
        """Get account name"""
        return self.accountName

    def get_account_url(self) -> str:
        """Get the full account URL"""
        return f"{self.endpointProtocol}://{self.accountName}.blob.{self.endpointSuffix}"

    def get_authentication_method(self) -> str:
        """Get authentication method"""
        return "account_key"

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information (without sensitive data)"""
        return {
            'authentication_method': 'account_key',
            'account_name': self.accountName,
            'account_url': self.get_account_url(),
            'container_name': self.containerName,
            'endpoint_protocol': self.endpointProtocol,
            'endpoint_suffix': self.endpointSuffix
        }

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return {
            'authentication_method': 'account_key',
            'account_name': self.accountName,
            'account_url': self.get_account_url(),
            'container_name': self.containerName,
            'endpoint_protocol': self.endpointProtocol,
            'endpoint_suffix': self.endpointSuffix
        }


class AzureBlobRESTClient:
    """Azure Blob Storage REST client that handles blob operations internally"""

    def __init__(self, config: Union[AzureBlobConnectionStringConfig, AzureBlobAccountKeyConfig]) -> None: # type: ignore
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

    def get_container_name(self) -> str:
        """Get the configured container name"""
        return self.config.containerName

    def get_account_name(self) -> str:
        """Get the account name"""
        account_name = self.config.get_account_name()
        if not account_name:
            raise AzureBlobConfigurationError("Could not determine account name from configuration")
        return account_name

    def get_account_url(self) -> str:
        """Get the account URL"""
        if hasattr(self.config, 'get_account_url'):
            return self.config.get_account_url()
        elif isinstance(self.config, AzureBlobConnectionStringConfig):
            account_name = self.config.get_account_name()
            if account_name:
                return f"https://{account_name}.blob.core.windows.net" # TODO : need to make this work for Azure sovereign clouds or custom endpoints(A more robust approach would be to retrieve the account URL from the BlobServiceClient instance itself)
        raise AzureBlobConfigurationError("Could not determine account URL from configuration")

    def get_authentication_method(self) -> str:
        """Get the authentication method being used"""
        return self.config.get_authentication_method()

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information"""
        if hasattr(self.config, 'get_credentials_info'):
            return self.config.get_credentials_info()
        else:
            return self.config.to_dict()

    async def close_async_client(self) -> None:
        """Close the async blob service client"""
        if self._async_blob_service_client:
            await self._async_blob_service_client.close()
            self._async_blob_service_client = None

    async def ensure_container_exists(self) -> AzureBlobResponse:
        """Ensure the configured container exists, create if it doesn't"""
        container_name = self.get_container_name()

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

    def get_container_name(self) -> str:
        """Get the configured container name"""
        return self.client.get_container_name()

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information"""
        return self.client.get_credentials_info()

    def get_account_name(self) -> str:
        """Get the account name"""
        return self.client.get_account_name()

    def get_authentication_method(self) -> str:
        """Get the authentication method being used"""
        return self.client.get_authentication_method()

    async def ensure_container_exists(self) -> AzureBlobResponse:
        """Ensure the configured container exists, create if it doesn't"""
        return await self.client.ensure_container_exists()

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
    def build_with_account_key_config(cls, config: AzureBlobAccountKeyConfig) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient with account key configuration"""
        try:
            rest_client = AzureBlobRESTClient(config)
            return cls(rest_client)
        except Exception as e:
            if isinstance(e, (AzureBlobConfigurationError, AzureBlobContainerError)):
                raise
            raise AzureBlobConfigurationError(f"Failed to build client with account key config: {str(e)}")

    @classmethod
    def build_with_connection_string(
        cls,
        azureBlobConnectionString: str,
        containerName: str
    ) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient with connection string directly"""
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=azureBlobConnectionString,
            containerName=containerName
        )
        return cls.build_with_connection_string_config(config)

    @classmethod
    def build_with_account_key(
        cls,
        accountName: str,
        accountKey: str,
        containerName: str,
        endpointProtocol: str = "https",
        endpointSuffix: str = "core.windows.net"
    ) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient with account name and key directly"""
        config = AzureBlobAccountKeyConfig(
            accountName=accountName,
            accountKey=accountKey,
            containerName=containerName,
            endpointProtocol=endpointProtocol,
            endpointSuffix=endpointSuffix
        )
        return cls.build_with_account_key_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
    ) -> "AzureBlobClient": # type: ignore
        """Build AzureBlobClient using configuration service and graphdb service"""
        try:
            # Get Azure Blob Storage configuration from config service
            config_data = await cls._get_connector_config(config_service, "azure")

            # Extract configuration parameters
            auth_type = config_data.get("authType", "CONNECTION_STRING")
            auth_config = config_data.get("auth", {})
            container_name = auth_config.get("containerName")

            if not container_name:
                raise AzureBlobConfigurationError("containerName is required in Azure Blob Storage configuration")

            if auth_type == "CONNECTION_STRING":
                connection_string = auth_config.get("azureBlobConnectionString")
                if not connection_string:
                    raise AzureBlobConfigurationError("azureBlobConnectionString is required for CONNECTION_STRING authType")

                config = AzureBlobConnectionStringConfig(
                    azureBlobConnectionString=connection_string,
                    containerName=container_name
                )
                return cls.build_with_connection_string_config(config)

            elif auth_type == "ACCOUNT_KEY":
                account_name = auth_config.get("accountName")
                account_key = auth_config.get("accountKey")
                endpoint_protocol = auth_config.get("endpointProtocol", "https")
                endpoint_suffix = auth_config.get("endpointSuffix", "core.windows.net")

                if not account_name or not account_key:
                    raise AzureBlobConfigurationError("accountName and accountKey are required for ACCOUNT_KEY authType")

                config = AzureBlobAccountKeyConfig(
                    accountName=account_name,
                    accountKey=account_key,
                    containerName=container_name,
                    endpointProtocol=endpoint_protocol,
                    endpointSuffix=endpoint_suffix
                )
                return cls.build_with_account_key_config(config)

            else:
                raise AzureBlobConfigurationError(f"Unsupported authType: {auth_type}")

        except Exception as e:
            logger.error(f"Failed to build Azure Blob Storage client from services: {e}")
            raise AzureBlobConfigurationError(f"Failed to build Azure Blob Storage client: {str(e)}")

    @staticmethod
    async def _get_connector_config(config_service: ConfigurationService, connector_name: str) -> Dict[str, Any]:
        """Get connector configuration from config service"""
        try:
            config_path = f"/services/connectors/{connector_name}/config"
            config_data = await config_service.get_config(config_path)
            return config_data
        except Exception as e:
            raise AzureBlobConfigurationError(f"Failed to get {connector_name} configuration: {str(e)}")
