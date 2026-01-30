"""
Azure Files Client

Client wrapper for Azure File Share operations using the azure-storage-file-share SDK.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

try:
    from azure.core.exceptions import AzureError  # type: ignore
    from azure.storage.fileshare import ShareServiceClient  # type: ignore
    from azure.storage.fileshare.aio import (  # type: ignore
        ShareServiceClient as AsyncShareServiceClient,
    )
except ImportError:
    raise ImportError(
        "azure-storage-file-share is not installed. "
        "Please install it with `pip install azure-storage-file-share`"
    )

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class AzureFilesConfigurationError(Exception):
    """Custom exception for Azure Files configuration errors"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class AzureFilesShareError(Exception):
    """Custom exception for Azure Files share-related errors"""

    def __init__(
        self,
        message: str,
        share_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.share_name = share_name
        self.details = details or {}


@dataclass
class AzureFilesResponse:
    """Standardized Azure Files API response wrapper"""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


@dataclass
class AzureFilesConnectionStringConfig:
    """Configuration for Azure Files using Connection String

    Args:
        connectionString: The Azure Storage connection string
        shareName: The default share name (optional)
    """

    connectionString: str
    shareName: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate configuration after initialization"""
        if not self.connectionString or not self.connectionString.strip():
            raise AzureFilesConfigurationError(
                "connectionString cannot be empty or None",
                {"provided_value": self.connectionString},
            )

    def create_share_service_client(self) -> ShareServiceClient:
        """Create ShareServiceClient using connection string"""
        try:
            return ShareServiceClient.from_connection_string(
                conn_str=self.connectionString
            )
        except (ValueError, AzureError) as e:
            raise AzureFilesConfigurationError(
                f"Failed to create ShareServiceClient from connection string: {str(e)}",
                {"connection_string_length": len(self.connectionString)},
            ) from e

    async def create_async_share_service_client(self) -> AsyncShareServiceClient:
        """Create AsyncShareServiceClient using connection string"""
        try:
            return AsyncShareServiceClient.from_connection_string(
                conn_str=self.connectionString
            )
        except (ValueError, AzureError) as e:
            raise AzureFilesConfigurationError(
                f"Failed to create AsyncShareServiceClient from connection string: {str(e)}",
                {"connection_string_length": len(self.connectionString)},
            ) from e

    def get_account_name(self) -> Optional[str]:
        """Extract account name from connection string"""
        try:
            for part in self.connectionString.split(";"):
                if part.startswith("AccountName="):
                    return part.split("=", 1)[1]
        except IndexError as e:
            raise AzureFilesConfigurationError(
                "Could not parse AccountName from connection string",
                {"connection_string_length": len(self.connectionString)},
            ) from e
        return None

    def get_authentication_method(self) -> str:
        """Get authentication method"""
        return "connection_string"

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return {
            "authentication_method": "connection_string",
            "account_name": self.get_account_name(),
            "share_name": self.shareName,
        }


@dataclass
class AzureFilesAccountKeyConfig:
    """Configuration for Azure Files using Account Name and Key

    Args:
        accountName: The Azure storage account name
        accountKey: The Azure storage account key
        shareName: The default share name (optional)
        endpointProtocol: The endpoint protocol (https/http, default: https)
        endpointSuffix: The endpoint suffix (default: core.windows.net)
    """

    accountName: str
    accountKey: str
    shareName: Optional[str] = None
    endpointProtocol: str = "https"
    endpointSuffix: str = "core.windows.net"

    def __post_init__(self) -> None:
        """Validate configuration after initialization"""
        if not self.accountName or not self.accountName.strip():
            raise AzureFilesConfigurationError(
                "accountName cannot be empty or None",
                {"provided_value": self.accountName},
            )

        if not self.accountKey or not self.accountKey.strip():
            raise AzureFilesConfigurationError(
                "accountKey cannot be empty or None",
                {"account_name": self.accountName},
            )

        if self.endpointProtocol not in ["https", "http"]:
            raise AzureFilesConfigurationError(
                f"endpointProtocol must be 'https' or 'http', got: {self.endpointProtocol}",
                {"provided_value": self.endpointProtocol},
            )

    def create_share_service_client(self) -> ShareServiceClient:
        """Create ShareServiceClient using account name and key"""
        try:
            account_url = f"{self.endpointProtocol}://{self.accountName}.file.{self.endpointSuffix}"
            return ShareServiceClient(account_url=account_url, credential=self.accountKey)
        except Exception as e:
            raise AzureFilesConfigurationError(
                f"Failed to create ShareServiceClient with account key: {str(e)}",
                {
                    "account_name": self.accountName,
                    "account_url": f"{self.endpointProtocol}://{self.accountName}.file.{self.endpointSuffix}",
                },
            )

    async def create_async_share_service_client(self) -> AsyncShareServiceClient:
        """Create AsyncShareServiceClient using account name and key"""
        try:
            account_url = f"{self.endpointProtocol}://{self.accountName}.file.{self.endpointSuffix}"
            return AsyncShareServiceClient(
                account_url=account_url, credential=self.accountKey
            )
        except Exception as e:
            raise AzureFilesConfigurationError(
                f"Failed to create AsyncShareServiceClient with account key: {str(e)}",
                {
                    "account_name": self.accountName,
                    "account_url": f"{self.endpointProtocol}://{self.accountName}.file.{self.endpointSuffix}",
                },
            )

    def get_account_name(self) -> str:
        """Get account name"""
        return self.accountName

    def get_account_url(self) -> str:
        """Get the full account URL"""
        return f"{self.endpointProtocol}://{self.accountName}.file.{self.endpointSuffix}"

    def get_authentication_method(self) -> str:
        """Get authentication method"""
        return "account_key"

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information (without sensitive data)"""
        return {
            "authentication_method": "account_key",
            "account_name": self.accountName,
            "account_url": self.get_account_url(),
            "share_name": self.shareName,
            "endpoint_protocol": self.endpointProtocol,
            "endpoint_suffix": self.endpointSuffix,
        }

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return {
            "authentication_method": "account_key",
            "account_name": self.accountName,
            "account_url": self.get_account_url(),
            "share_name": self.shareName,
            "endpoint_protocol": self.endpointProtocol,
            "endpoint_suffix": self.endpointSuffix,
        }


class AzureFilesRESTClient:
    """Azure Files REST client that handles file share operations internally"""

    def __init__(
        self,
        config: Union[AzureFilesConnectionStringConfig, AzureFilesAccountKeyConfig],
    ) -> None:
        """Initialize with configuration"""
        self.config = config
        self._share_service_client: Optional[ShareServiceClient] = None
        self._async_share_service_client: Optional[AsyncShareServiceClient] = None

    def get_share_service_client(self) -> ShareServiceClient:
        """Get or create the ShareServiceClient"""
        if self._share_service_client is None:
            try:
                self._share_service_client = self.config.create_share_service_client()
            except Exception as e:
                raise AzureFilesConfigurationError(
                    f"Failed to create ShareServiceClient: {str(e)}",
                    {"authentication_method": self.config.get_authentication_method()},
                )
        return self._share_service_client

    async def get_async_share_service_client(self) -> AsyncShareServiceClient:
        """Get or create the AsyncShareServiceClient"""
        if self._async_share_service_client is None:
            try:
                self._async_share_service_client = (
                    await self.config.create_async_share_service_client()
                )
            except Exception as e:
                raise AzureFilesConfigurationError(
                    f"Failed to create AsyncShareServiceClient: {str(e)}",
                    {"authentication_method": self.config.get_authentication_method()},
                )
        return self._async_share_service_client

    def get_share_name(self) -> Optional[str]:
        """Get the configured share name"""
        return self.config.shareName

    def get_account_name(self) -> str:
        """Get the account name"""
        account_name = self.config.get_account_name()
        if not account_name:
            raise AzureFilesConfigurationError(
                "Could not determine account name from configuration"
            )
        return account_name

    def get_account_url(self) -> str:
        """Get the account URL"""
        if hasattr(self.config, "get_account_url"):
            return self.config.get_account_url()
        elif isinstance(self.config, AzureFilesConnectionStringConfig):
            account_name = self.config.get_account_name()
            if account_name:
                return f"https://{account_name}.file.core.windows.net"
        raise AzureFilesConfigurationError(
            "Could not determine account URL from configuration"
        )

    def get_account_key(self) -> Optional[str]:
        """Get the account key (only for account key config)"""
        if isinstance(self.config, AzureFilesAccountKeyConfig):
            return self.config.accountKey
        return None

    def get_authentication_method(self) -> str:
        """Get the authentication method being used"""
        return self.config.get_authentication_method()

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information"""
        if hasattr(self.config, "get_credentials_info"):
            return self.config.get_credentials_info()
        else:
            return self.config.to_dict()

    async def close_async_client(self) -> None:
        """Close the async share service client"""
        if self._async_share_service_client:
            await self._async_share_service_client.close()
            self._async_share_service_client = None

    async def ensure_share_exists(self, share_name: str) -> AzureFilesResponse:
        """Ensure the specified share exists, create if it doesn't"""
        try:
            async_share_service_client = await self.get_async_share_service_client()
            share_client = async_share_service_client.get_share_client(share_name)

            if not await share_client.exists():
                await share_client.create_share()
                return AzureFilesResponse(
                    success=True,
                    data={
                        "share_name": share_name,
                        "action": "created",
                        "message": f'Share "{share_name}" created successfully',
                    },
                    message=f'Share "{share_name}" created successfully',
                )
            else:
                return AzureFilesResponse(
                    success=True,
                    data={
                        "share_name": share_name,
                        "action": "exists",
                        "message": f'Share "{share_name}" already exists',
                    },
                    message=f'Share "{share_name}" already exists',
                )

        except AzureFilesShareError:
            raise
        except AzureError as e:
            raise AzureFilesShareError(
                f"Azure error while ensuring share exists: {str(e)}",
                share_name=share_name,
                details={"azure_error": str(e)},
            )
        except Exception as e:
            raise AzureFilesShareError(
                f"Unexpected error while ensuring share exists: {str(e)}",
                share_name=share_name,
                details={"unexpected_error": str(e)},
            )


class AzureFilesClient(IClient):
    """Builder class for Azure Files clients with validation"""

    def __init__(self, client: AzureFilesRESTClient) -> None:
        """Initialize with AzureFilesRESTClient"""
        self.client = client

    def get_client(self) -> AzureFilesRESTClient:
        """Return the AzureFilesRESTClient object"""
        return self.client

    def get_share_name(self) -> Optional[str]:
        """Get the configured share name"""
        return self.client.get_share_name()

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get credential information"""
        return self.client.get_credentials_info()

    def get_account_name(self) -> str:
        """Get the account name"""
        return self.client.get_account_name()

    def get_account_url(self) -> str:
        """Get the account URL"""
        return self.client.get_account_url()

    def get_account_key(self) -> Optional[str]:
        """Get the account key"""
        return self.client.get_account_key()

    def get_authentication_method(self) -> str:
        """Get the authentication method being used"""
        return self.client.get_authentication_method()

    async def ensure_share_exists(self, share_name: str) -> AzureFilesResponse:
        """Ensure the specified share exists, create if it doesn't"""
        return await self.client.ensure_share_exists(share_name)

    async def get_async_share_service_client(self) -> AsyncShareServiceClient:
        """Get or create the AsyncShareServiceClient"""
        return await self.client.get_async_share_service_client()

    async def close_async_client(self) -> None:
        """Close the async share service client"""
        await self.client.close_async_client()

    @classmethod
    def build_with_connection_string_config(
        cls, config: AzureFilesConnectionStringConfig
    ) -> "AzureFilesClient":
        """Build AzureFilesClient with connection string configuration"""
        try:
            rest_client = AzureFilesRESTClient(config)
            return cls(rest_client)
        except Exception as e:
            if isinstance(e, (AzureFilesConfigurationError, AzureFilesShareError)):
                raise
            raise AzureFilesConfigurationError(
                f"Failed to build client with connection string config: {str(e)}"
            )

    @classmethod
    def build_with_account_key_config(
        cls, config: AzureFilesAccountKeyConfig
    ) -> "AzureFilesClient":
        """Build AzureFilesClient with account key configuration"""
        try:
            rest_client = AzureFilesRESTClient(config)
            return cls(rest_client)
        except Exception as e:
            if isinstance(e, (AzureFilesConfigurationError, AzureFilesShareError)):
                raise
            raise AzureFilesConfigurationError(
                f"Failed to build client with account key config: {str(e)}"
            )

    @classmethod
    def build_with_connection_string(
        cls, connectionString: str, shareName: Optional[str] = None
    ) -> "AzureFilesClient":
        """Build AzureFilesClient with connection string directly"""
        config = AzureFilesConnectionStringConfig(
            connectionString=connectionString, shareName=shareName
        )
        return cls.build_with_connection_string_config(config)

    @classmethod
    def build_with_account_key(
        cls,
        accountName: str,
        accountKey: str,
        shareName: Optional[str] = None,
        endpointProtocol: str = "https",
        endpointSuffix: str = "core.windows.net",
    ) -> "AzureFilesClient":
        """Build AzureFilesClient with account name and key directly"""
        config = AzureFilesAccountKeyConfig(
            accountName=accountName,
            accountKey=accountKey,
            shareName=shareName,
            endpointProtocol=endpointProtocol,
            endpointSuffix=endpointSuffix,
        )
        return cls.build_with_account_key_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "AzureFilesClient":
        """Build AzureFilesClient using configuration service"""
        try:
            # Get Azure Files configuration from config service
            config_data = await cls._get_connector_config(
                config_service, connector_instance_id
            )

            # Extract configuration parameters
            auth_config = config_data.get("auth", {})
            auth_type = auth_config.get("authType", "ACCOUNT_KEY")
            share_name = auth_config.get("shareName")

            if auth_type == "CONNECTION_STRING":
                connection_string = auth_config.get("connectionString")
                if not connection_string:
                    raise AzureFilesConfigurationError(
                        "connectionString is required for CONNECTION_STRING authType"
                    )

                config = AzureFilesConnectionStringConfig(
                    connectionString=connection_string, shareName=share_name
                )
                return cls.build_with_connection_string_config(config)

            elif auth_type == "ACCOUNT_KEY":
                account_name = auth_config.get("accountName")
                account_key = auth_config.get("accountKey")
                endpoint_protocol = auth_config.get("endpointProtocol", "https")
                endpoint_suffix = auth_config.get("endpointSuffix", "core.windows.net")

                if not account_name or not account_key:
                    raise AzureFilesConfigurationError(
                        "accountName and accountKey are required for ACCOUNT_KEY authType"
                    )

                config = AzureFilesAccountKeyConfig(
                    accountName=account_name,
                    accountKey=account_key,
                    shareName=share_name,
                    endpointProtocol=endpoint_protocol,
                    endpointSuffix=endpoint_suffix,
                )
                return cls.build_with_account_key_config(config)

            else:
                raise AzureFilesConfigurationError(f"Unsupported authType: {auth_type}")

        except Exception as e:
            logger.error(f"Failed to build Azure Files client from services: {e}")
            raise AzureFilesConfigurationError(
                f"Failed to build Azure Files client: {str(e)}"
            )

    @staticmethod
    async def _get_connector_config(
        config_service: ConfigurationService, connector_instance_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get connector configuration from config service"""
        try:
            config_path = f"/services/connectors/{connector_instance_id}/config"
            config_data = await config_service.get_config(config_path)
            if not config_data:
                raise ValueError(
                    f"Failed to get Azure Files connector configuration for instance {connector_instance_id}"
                )
            return config_data
        except Exception as e:
            raise AzureFilesConfigurationError(
                f"Failed to get {connector_instance_id} configuration: {str(e)}"
            )
