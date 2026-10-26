"""
Azure Files Client

Client wrapper for Azure File Share operations using the azure-storage-file-share SDK.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

try:
    from azure.core.exceptions import AzureError  # type: ignore
    from azure.storage.fileshare import ShareServiceClient  # type: ignore
    from azure.storage.fileshare._shared.policies_async import (  # type: ignore
        ExponentialRetry,
    )
    from azure.storage.fileshare.aio import (  # type: ignore
        ShareServiceClient as AsyncShareServiceClient,
    )
except ImportError:
    raise ImportError(
        "azure-storage-file-share is not installed. "
        "Please install it with `pip install azure-storage-file-share`"
    )

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.address_guard import check_url_allowed
from app.sources.client.iclient import IClient
from app.sources.client.resilience import parse_retry_after

# Longest Retry-After honoured, so one answer cannot park the sync.
RETRY_AFTER_CAP_SECONDS = 60.0


class RetryAfterExponentialRetry(ExponentialRetry):
    """The SDK's default retry (the async clients build this class themselves when
    given none), except that a Retry-After sent with a 5xx or 408 sets the wait."""

    def get_backoff_time(self, settings: dict[str, Any]) -> float:
        history = settings.get("history") or []
        response = history[-1].http_response if history else None
        retry_after = parse_retry_after(response.headers) if response is not None else None
        if retry_after is not None:
            return min(retry_after, RETRY_AFTER_CAP_SECONDS)
        return super().get_backoff_time(settings)


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
    # Azure's x-ms-error-code (e.g. ShareNotFound), so callers can tell "gone" from any other failure.
    error_code: Optional[str] = None
    # Marker for the next page of a paged listing; None after the last page.
    continuation_token: Optional[str] = None

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
                conn_str=self.connectionString, retry_policy=RetryAfterExponentialRetry()
            )
        except (ValueError, AzureError) as e:
            raise AzureFilesConfigurationError(
                f"Failed to create AsyncShareServiceClient from connection string: {str(e)}",
                {"connection_string_length": len(self.connectionString)},
            ) from e

    def get_account_name(self) -> Optional[str]:
        """AccountName from the connection string, else as the SDK reads it from
        FileEndpoint (a portal SAS connection string has no AccountName)."""
        for part in self.connectionString.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "AccountName" and value:
                return value
        try:
            return self.create_share_service_client().account_name or None
        except AzureFilesConfigurationError:
            return None

    def get_account_key(self) -> Optional[str]:
        """AccountKey from the connection string; None for a SAS connection string."""
        for part in self.connectionString.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "AccountKey" and value:
                return value
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
                account_url=account_url, credential=self.accountKey, retry_policy=RetryAfterExponentialRetry()
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


@dataclass
class AzureFilesEntraIdConfig:
    """Configuration for Azure Files using a Microsoft Entra ID service principal.

    Lets admins grant a scoped RBAC role (Storage File Data Privileged Reader) and
    disable account key access on the storage account.
    """

    accountUrl: str
    tenantId: str
    clientId: str
    clientSecret: str
    shareName: str | None = None

    def __post_init__(self) -> None:
        missing = [k for k in ("accountUrl", "tenantId", "clientId", "clientSecret") if not (getattr(self, k) or "").strip()]
        if missing:
            raise AzureFilesConfigurationError(f"{', '.join(missing)} cannot be empty for Entra ID authentication")
        self.accountUrl = self.accountUrl.strip().rstrip("/")

    def create_share_service_client(self) -> ShareServiceClient:
        from azure.identity import ClientSecretCredential  # type: ignore

        # Azure Files accepts OAuth only with token_intent="backup".
        return ShareServiceClient(
            account_url=self.accountUrl,
            credential=ClientSecretCredential(self.tenantId, self.clientId, self.clientSecret),
            token_intent="backup",
        )

    async def create_async_share_service_client(self) -> AsyncShareServiceClient:
        from azure.identity.aio import ClientSecretCredential  # type: ignore

        return AsyncShareServiceClient(
            account_url=self.accountUrl,
            credential=ClientSecretCredential(self.tenantId, self.clientId, self.clientSecret),
            token_intent="backup",
            retry_policy=RetryAfterExponentialRetry(),
        )

    def get_account_name(self) -> str | None:
        host = urlparse(self.accountUrl).hostname or ""
        return host.split(".", 1)[0] or None

    def get_account_url(self) -> str:
        return self.accountUrl

    def get_authentication_method(self) -> str:
        return "entra_id"

    def to_dict(self) -> dict:
        return {"authentication_method": "entra_id", "account_name": self.get_account_name(), "share_name": self.shareName}


class AzureFilesRESTClient:
    """Azure Files REST client that handles file share operations internally"""

    def __init__(
        self,
        config: AzureFilesConnectionStringConfig | AzureFilesAccountKeyConfig | AzureFilesEntraIdConfig,
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
                client = await self.config.create_async_share_service_client()
                # Only the async client sends requests; the sync one only names endpoints.
                await asyncio.to_thread(check_url_allowed, client.url)
                self._async_share_service_client = client
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
        """The account's file endpoint (sovereign clouds and custom endpoints included), without any SAS."""
        if hasattr(self.config, "get_account_url"):
            return self.config.get_account_url()
        try:
            endpoint = self.get_share_service_client().primary_endpoint.split("?", 1)[0].rstrip("/")
        except Exception as e:
            raise AzureFilesConfigurationError(
                "Could not determine account URL from configuration"
            ) from e
        if not endpoint:
            raise AzureFilesConfigurationError(
                "Could not determine account URL from configuration"
            )
        return endpoint

    def get_account_key(self) -> Optional[str]:
        """The account key, if the credentials carry one (account key or key connection string)."""
        if isinstance(self.config, AzureFilesAccountKeyConfig):
            return self.config.accountKey
        if isinstance(self.config, AzureFilesConnectionStringConfig):
            return self.config.get_account_key()
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
            credential = self._async_share_service_client.credential
            await self._async_share_service_client.close()
            self._async_share_service_client = None
            # An Entra ID credential holds its own HTTP session.
            if hasattr(credential, "close"):
                await credential.close()


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

            if auth_type == "ENTRA_ID":
                return cls(AzureFilesRESTClient(AzureFilesEntraIdConfig(
                    accountUrl=auth_config.get("accountUrl", ""),
                    tenantId=auth_config.get("tenantId", ""),
                    clientId=auth_config.get("clientId", ""),
                    clientSecret=auth_config.get("clientSecret", ""),
                    shareName=share_name,
                )))

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
