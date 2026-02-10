"""Databricks client implementation.

This module provides a client for interacting with Databricks using the official
databricks-sdk (WorkspaceClient). It supports:
1. Personal Access Token (PAT) authentication
2. OAuth token authentication (U2M and M2M)

The official SDK covers all public Databricks REST API operations including
clusters, jobs, SQL, DBFS, Unity Catalog, ML, serving, vector search, etc.,
so no raw HTTP client is needed.

SDK Documentation: https://databricks-sdk-py.readthedocs.io/
Authentication: https://docs.databricks.com/en/dev-tools/auth/index.html
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from databricks.sdk.config import Config as _Config  # type: ignore
from pydantic import BaseModel, Field, ValidationError  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient  # type: ignore

try:
    from databricks.sdk import WorkspaceClient as _WorkspaceClient  # type: ignore

    databricks_sdk_available = True
except ImportError:
    _WorkspaceClient = None
    databricks_sdk_available = False


class AuthType(str, Enum):
    """Authentication type for Databricks connector."""

    OAUTH = "OAUTH"
    PAT = "PAT"


class DatabricksSDKClient:
    """Databricks SDK client wrapping the official databricks-sdk.

    Uses the official Databricks SDK for Python (databricks-sdk) to interact
    with Databricks workspace resources including clusters, jobs, SQL warehouses,
    DBFS, and Unity Catalog.

    Supports authentication via:
    - Personal Access Token (PAT)
    - OAuth token (U2M or M2M)

    SDK Documentation: https://databricks-sdk-py.readthedocs.io/
    PyPI: https://pypi.org/project/databricks-sdk/
    """

    def __init__(
        self,
        host: str,
        token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        timeout: int = 60,
    ) -> None:
        """Initialize Databricks SDK client.

        Args:
            host: Databricks workspace URL (e.g., https://dbc-xxx.cloud.databricks.com)
            token: Personal access token or OAuth access token
            client_id: OAuth client ID for M2M authentication
            client_secret: OAuth client secret for M2M authentication
            timeout: Request timeout in seconds
        """
        if not databricks_sdk_available:
            raise ImportError(
                "databricks-sdk is required for the SDK client. "
                "Install with: pip install databricks-sdk. "
                "If using this repo, run from backend/python with the project venv: "
                "source venv/bin/activate then python, or ./venv/bin/python <script>"
            )
        self.host = self._normalize_host(host)
        self.timeout = timeout
        self._workspace_client: Optional["WorkspaceClient"] = None

        self._token = token
        self._client_id = client_id
        self._client_secret = client_secret

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Normalize the workspace host URL.

        Args:
            host: Workspace URL or hostname

        Returns:
            Normalized workspace URL with https scheme
        """
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        return host.rstrip("/")

    def connect(self) -> "DatabricksSDKClient":
        """Create the WorkspaceClient connection.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If connection fails
        """
        if self._workspace_client is not None:
            return self

        try:

            config_kwargs: Dict[str, Any] = {
                "host": self.host,
                "http_timeout_seconds": self.timeout,
            }

            if self._token:
                config_kwargs["token"] = self._token
            elif self._client_id and self._client_secret:
                config_kwargs["client_id"] = self._client_id
                config_kwargs["client_secret"] = self._client_secret

            cfg = _Config(**config_kwargs)
            self._workspace_client = _WorkspaceClient(config=cfg)
            return self

        except Exception as e:
            raise ConnectionError(
                f"Failed to connect to Databricks: {e}"
            ) from e

    def close(self) -> None:
        """Close the Databricks SDK client."""
        if self._workspace_client is not None:
            try:
                api_client = getattr(
                    self._workspace_client, "api_client", None
                )
                if api_client and hasattr(api_client, "close"):
                    api_client.close()
            except Exception as e:
                logger.warning(
                    f"Failed to close Databricks client gracefully: {e}"
                )
            finally:
                self._workspace_client = None

    def is_connected(self) -> bool:
        """Check if the SDK client is initialized."""
        return self._workspace_client is not None

    def get_workspace_client(self) -> "WorkspaceClient":
        """Get the underlying WorkspaceClient.

        Returns:
            The databricks.sdk.WorkspaceClient instance

        Raises:
            ConnectionError: If not connected
        """
        if not self.is_connected():
            self.connect()
        assert self._workspace_client is not None
        return self._workspace_client

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all clusters in the workspace.

        Returns:
            List of cluster info dictionaries

        Raises:
            RuntimeError: If the operation fails
        """
        client = self.get_workspace_client()
        try:
            clusters = []
            for c in client.clusters.list():
                clusters.append(
                    {
                        "cluster_id": c.cluster_id,
                        "cluster_name": c.cluster_name,
                        "state": str(c.state) if c.state else None,
                        "creator_user_name": c.creator_user_name,
                    }
                )
            return clusters
        except Exception as e:
            raise RuntimeError(f"Failed to list clusters: {e}") from e

    def list_sql_warehouses(self) -> List[Dict[str, Any]]:
        """List all SQL warehouses in the workspace.

        Returns:
            List of SQL warehouse info dictionaries

        Raises:
            RuntimeError: If the operation fails
        """
        client = self.get_workspace_client()
        try:
            warehouses = []
            for w in client.warehouses.list():
                warehouses.append(
                    {
                        "id": w.id,
                        "name": w.name,
                        "state": str(w.state) if w.state else None,
                        "cluster_size": w.cluster_size,
                    }
                )
            return warehouses
        except Exception as e:
            raise RuntimeError(f"Failed to list SQL warehouses: {e}") from e

    def execute_sql(
        self,
        statement: str,
        warehouse_id: str,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a SQL statement via the Statement Execution API.

        Args:
            statement: SQL statement to execute
            warehouse_id: SQL warehouse ID to execute against
            catalog: Optional catalog name
            schema: Optional schema name

        Returns:
            List of dictionaries containing query results

        Raises:
            RuntimeError: If query execution fails
        """
        client = self.get_workspace_client()
        try:
            from databricks.sdk.service.sql import StatementState  # type: ignore

            response = client.statement_execution.execute_statement(
                statement=statement,
                warehouse_id=warehouse_id,
                catalog=catalog,
                schema=schema,
            )

            if response.status and response.status.state == StatementState.SUCCEEDED:
                result = response.result
                if result and result.data_array and result.manifest:
                    columns = [
                        col.name for col in result.manifest.schema.columns
                    ]
                    return [
                        dict(zip(columns, row)) for row in result.data_array
                    ]
            return []

        except Exception as e:
            raise RuntimeError(f"SQL execution failed: {e}") from e

    def get_host(self) -> str:
        """Get the Databricks workspace host URL."""
        return self.host

    def __enter__(self) -> "DatabricksSDKClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()


class DatabricksOAuthConfig(BaseModel):
    """Configuration for Databricks client via OAuth token.

    Args:
        workspace_url: Databricks workspace URL
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
    """

    workspace_url: str = Field(
        ...,
        description="Databricks workspace URL",
    )
    oauth_token: str = Field(..., description="OAuth access token")
    timeout: int = Field(
        default=60, description="Request timeout in seconds", gt=0
    )

    def create_client(self) -> DatabricksSDKClient:
        """Create a Databricks SDK client with OAuth authentication."""
        return DatabricksSDKClient(
            host=self.workspace_url,
            token=self.oauth_token,
            timeout=self.timeout,
        )


class DatabricksPATConfig(BaseModel):
    """Configuration for Databricks client via Personal Access Token (PAT).

    Args:
        workspace_url: Databricks workspace URL
        pat_token: Personal Access Token
        timeout: Request timeout in seconds
    """

    workspace_url: str = Field(
        ...,
        description="Databricks workspace URL",
    )
    pat_token: str = Field(..., description="Personal Access Token")
    timeout: int = Field(
        default=60, description="Request timeout in seconds", gt=0
    )

    def create_client(self) -> DatabricksSDKClient:
        """Create a Databricks SDK client with PAT authentication."""
        return DatabricksSDKClient(
            host=self.workspace_url,
            token=self.pat_token,
            timeout=self.timeout,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for Databricks connector."""

    authType: AuthType = Field(
        default=AuthType.PAT,
        description="Authentication type (OAUTH or PAT)",
    )
    patToken: Optional[str] = Field(
        default=None, description="Personal Access Token for PAT auth"
    )


class CredentialsConfig(BaseModel):
    """Credentials configuration for Databricks connector."""

    access_token: Optional[str] = Field(
        default=None, description="OAuth access token"
    )


class DatabricksConnectorConfig(BaseModel):
    """Configuration model for Databricks connector from services."""

    workspaceUrl: str = Field(..., description="Databricks workspace URL")
    auth: AuthConfig = Field(
        default_factory=AuthConfig, description="Authentication configuration"
    )
    credentials: Optional[CredentialsConfig] = Field(
        default=None, description="Credentials configuration"
    )
    timeout: int = Field(
        default=60, description="Request timeout in seconds", gt=0
    )


class DatabricksClient(IClient):
    """Builder class for Databricks clients with different construction methods.

    This class provides a unified interface for creating Databricks clients
    using either OAuth or Personal Access Token (PAT) authentication.
    It wraps the official databricks-sdk WorkspaceClient.

    Example usage with OAuth:
        config = DatabricksOAuthConfig(
            workspace_url="https://dbc-xxx.cloud.databricks.com",
            oauth_token="your_oauth_token"
        )
        client = DatabricksClient.build_with_config(config)
        sdk_client = client.get_client()

    Example usage with PAT:
        config = DatabricksPATConfig(
            workspace_url="https://dbc-xxx.cloud.databricks.com",
            pat_token="dapi..."
        )
        client = DatabricksClient.build_with_config(config)
        sdk_client = client.get_client()
    """

    def __init__(
        self,
        client: DatabricksSDKClient,
    ) -> None:
        """Initialize with a Databricks SDK client.

        Args:
            client: Databricks SDK client instance
        """
        self._client = client

    def get_client(self) -> DatabricksSDKClient:
        """Return the Databricks SDK client object."""
        return self._client

    def get_workspace_url(self) -> str:
        """Return the Databricks workspace URL."""
        return self._client.get_host()

    @classmethod
    def build_with_config(
        cls,
        config: Union[DatabricksOAuthConfig, DatabricksPATConfig],
    ) -> "DatabricksClient":
        """Build DatabricksClient with configuration.

        Args:
            config: Databricks configuration instance (OAuth or PAT)

        Returns:
            DatabricksClient instance
        """
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "DatabricksClient":
        """Build DatabricksClient using configuration service.

        This method retrieves Databricks connector configuration from
        the configuration service (etcd) and creates the appropriate client.

        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            DatabricksClient instance

        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            config = DatabricksConnectorConfig.model_validate(config_dict)

            auth_type = config.auth.authType
            workspace_url = config.workspaceUrl
            timeout = config.timeout

            if auth_type == AuthType.OAUTH:
                if not config.credentials or not config.credentials.access_token:
                    raise ValueError(
                        "OAuth access token required for OAuth auth type"
                    )
                sdk_client = DatabricksSDKClient(
                    host=workspace_url,
                    token=config.credentials.access_token,
                    timeout=timeout,
                )

            elif auth_type == AuthType.PAT:
                if not config.auth.patToken:
                    raise ValueError("PAT token required for PAT auth type")
                sdk_client = DatabricksSDKClient(
                    host=workspace_url,
                    token=config.auth.patToken,
                    timeout=timeout,
                )

            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")

            return cls(client=sdk_client)

        except ValidationError as e:
            logger.error(f"Invalid Databricks connector configuration: {e}")
            raise ValueError(
                "Invalid Databricks connector configuration"
            ) from e
        except Exception as e:
            logger.error(
                f"Failed to build Databricks client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Databricks.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance ID

        Returns:
            Configuration dictionary

        Raises:
            ValueError: If configuration cannot be retrieved
        """
        instance_msg = (
            f" for instance {connector_instance_id}"
            if connector_instance_id
            else ""
        )
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get Databricks connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                raise ValueError(
                    f"Invalid Databricks connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get Databricks connector config: {e}")
            raise ValueError(
                f"Failed to get Databricks connector configuration{instance_msg}"
            ) from e


class DatabricksResponse(BaseModel):
    """Standard response wrapper for Databricks API calls."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(
        default=None, description="Error code if failed"
    )
    message: Optional[str] = Field(
        default=None, description="Error message if failed"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)
