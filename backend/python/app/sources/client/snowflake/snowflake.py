"""Snowflake client implementation.

This module provides clients for interacting with Snowflake's SQL REST API using either:
1. OAuth token authentication
2. Personal Access Token (PAT) authentication

Snowflake SQL API Reference: https://docs.snowflake.com/en/developer-guide/sql-api/index
Authentication: https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field, ValidationError  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from snowflake.connector import SnowflakeConnection  # type: ignore

try:
    import snowflake.connector as snowflake_connector  # type: ignore
except ImportError:
    snowflake_connector = None

class AuthType(str, Enum):
    """Authentication type for Snowflake connector."""

    OAUTH = "OAUTH"
    PAT = "PAT"


class SnowflakeRESTClientViaOAuth(HTTPClient):
    """Snowflake REST client via OAuth token.

    Uses Snowflake's OAuth authentication where an access token is obtained
    through the OAuth flow (e.g., Snowflake OAuth, External OAuth).

    OAuth Documentation: https://docs.snowflake.com/en/user-guide/oauth-intro

    Args:
        account_identifier: Snowflake account identifier
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        account_identifier: str,
        oauth_token: str,
        timeout: float = 30.0,
    ) -> None:
        """Initialize Snowflake REST client with OAuth token.

        Args:
            account_identifier: Snowflake account identifier
            oauth_token: OAuth access token obtained through Snowflake OAuth flow
            timeout: Request timeout in seconds
        """
        super().__init__(token=oauth_token, token_type="Bearer", timeout=timeout)
        self.account_identifier = account_identifier
        self.oauth_token = oauth_token
        self._base_url = self._build_base_url(account_identifier)

    @staticmethod
    def _build_base_url(account_identifier: str) -> str:
        """Build the Snowflake SQL API base URL from account identifier.

        Args:
            account_identifier: Snowflake account identifier (can be a full URL,
                partial URL, or just the account name)

        Returns:
            Base URL for Snowflake SQL API
        """
        # Parse the account identifier as a URL
        parsed = urlparse(account_identifier if "://" in account_identifier else f"https://{account_identifier}")
        # Extract the account name from netloc
        netloc = parsed.netloc or parsed.path.split("/")[0] if parsed.path else account_identifier
        # Remove the .snowflakecomputing.com suffix if present
        if netloc.endswith(".snowflakecomputing.com"):
            account = netloc[:-len(".snowflakecomputing.com")]
        else:
            # If no suffix, assume netloc is the account name
            account = netloc

        # Build the base URL using urlunparse for proper URL construction
        base_url = urlunparse((
            "https",
            f"{account}.snowflakecomputing.com",
            "/api/v2",
            "",
            "",
            ""
        ))
        return base_url

    def get_base_url(self) -> str:
        """Get the base URL for Snowflake API."""
        return self._base_url

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        return self.account_identifier


class SnowflakeRESTClientViaPAT(HTTPClient):
    """Snowflake REST client via Personal Access Token (PAT).

    Uses Snowflake's PAT authentication where a programmatic access token
    is generated for API access.

    PAT Documentation: https://docs.snowflake.com/en/user-guide/programmatic-access-tokens

    Args:
        account_identifier: Snowflake account identifier
        pat_token: Personal Access Token
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        account_identifier: str,
        pat_token: str,
        timeout: float = 30.0,
    ) -> None:
        """Initialize Snowflake REST client with Personal Access Token.

        Args:
            account_identifier: Snowflake account identifier
            pat_token: Personal Access Token for authentication
            timeout: Request timeout in seconds
        """
        logger.debug(f"🔧 [PAT] SnowflakeRESTClientViaPAT.__init__ called")
        logger.debug(f"🔧 [PAT] account_identifier: '{account_identifier}'")
        logger.debug(f"🔧 [PAT] pat_token (first 10 chars): '{pat_token[:10] if pat_token else 'None'}...'")
        logger.debug(f"🔧 [PAT] timeout: {timeout}")
        
        # Initialize with PAT token using Bearer format
        # When using X-Snowflake-Authorization-Token-Type header, Snowflake expects Bearer format
        # See: https://docs.snowflake.com/en/user-guide/programmatic-access-tokens
        super().__init__(token=pat_token, token_type="Bearer", timeout=timeout)
        
        # Required header for PAT authentication with REST API v2
        # This tells Snowflake the type of token being used
        self.headers["X-Snowflake-Authorization-Token-Type"] = "PROGRAMMATIC_ACCESS_TOKEN"
        
        self.account_identifier = account_identifier
        self.pat_token = pat_token
        self._base_url = self._build_base_url(account_identifier)
        
        logger.debug(f"🔧 [PAT] Final headers: {self.headers}")
        logger.debug(f"🔧 [PAT] Final base_url: '{self._base_url}'")
        logger.info(f"🔧 [PAT] SnowflakeRESTClientViaPAT initialized successfully with base_url: '{self._base_url}'")

    @staticmethod
    def _build_base_url(account_identifier: str) -> str:
        """Build the Snowflake SQL API base URL from account identifier.

        Args:
            account_identifier: Snowflake account identifier (can be a full URL,
                partial URL, or just the account name)

        Returns:
            Base URL for Snowflake SQL API
        """
        logger.debug(f"🔧 [PAT] _build_base_url called with account_identifier: '{account_identifier}'")
        
        # Parse the account identifier as a URL
        parsed = urlparse(account_identifier if "://" in account_identifier else f"https://{account_identifier}")
        logger.debug(f"🔧 [PAT] Parsed URL: scheme='{parsed.scheme}', netloc='{parsed.netloc}', path='{parsed.path}'")

        # Extract the account name from netloc
        netloc = parsed.netloc or parsed.path.split("/")[0] if parsed.path else account_identifier
        logger.debug(f"🔧 [PAT] Extracted netloc: '{netloc}'")

        # Remove the .snowflakecomputing.com suffix if present
        if netloc.endswith(".snowflakecomputing.com"):
            account = netloc[:-len(".snowflakecomputing.com")]
            logger.debug(f"🔧 [PAT] Removed .snowflakecomputing.com suffix, account: '{account}'")
        else:
            # If no suffix, assume netloc is the account name
            account = netloc
            logger.debug(f"🔧 [PAT] No .snowflakecomputing.com suffix found, using netloc as account: '{account}'")

        # Build the base URL using urlunparse for proper URL construction
        base_url = urlunparse((
            "https",
            f"{account}.snowflakecomputing.com",
            "/api/v2",
            "",
            "",
            ""
        ))
        logger.debug(f"🔧 [PAT] Built base_url: '{base_url}'")
        return base_url

    def get_base_url(self) -> str:
        """Get the base URL for Snowflake API."""
        logger.debug(f"🔧 [PAT] get_base_url() returning: '{self._base_url}'")
        return self._base_url

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        logger.debug(f"🔧 [PAT] get_account_identifier() returning: '{self.account_identifier}'")
        return self.account_identifier


class SnowflakeOAuthConfig(BaseModel):
    """Configuration for Snowflake client via OAuth token.

    OAuth flow endpoints depend on the OAuth provider (Snowflake OAuth or External OAuth).

    Args:
        account_identifier: Snowflake account identifier
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
    """

    account_identifier: str = Field(
        ...,
        description="Snowflake account identifier"
    )
    oauth_token: str = Field(..., description="OAuth access token")
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)

    def create_client(self) -> SnowflakeRESTClientViaOAuth:
        """Create a Snowflake REST client with OAuth authentication."""
        return SnowflakeRESTClientViaOAuth(
            account_identifier=self.account_identifier,
            oauth_token=self.oauth_token,
            timeout=self.timeout,
        )


class SnowflakePATConfig(BaseModel):
    """Configuration for Snowflake client via Personal Access Token (PAT).

    Args:
        account_identifier: Snowflake account identifier
        pat_token: Personal Access Token
        timeout: Request timeout in seconds
    """

    account_identifier: str = Field(
        ...,
        description="Snowflake account identifier"
    )
    pat_token: str = Field(..., description="Personal Access Token")
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)

    def create_client(self) -> SnowflakeRESTClientViaPAT:
        """Create a Snowflake REST client with PAT authentication."""
        return SnowflakeRESTClientViaPAT(
            account_identifier=self.account_identifier,
            pat_token=self.pat_token,
            timeout=self.timeout,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for Snowflake connector."""

    authType: AuthType = Field(default=AuthType.PAT, description="Authentication type (OAUTH or PAT)")
    patToken: Optional[str] = Field(default=None, description="Personal Access Token for PAT auth")


class CredentialsConfig(BaseModel):
    """Credentials configuration for Snowflake connector."""

    access_token: Optional[str] = Field(default=None, description="OAuth access token")


class SnowflakeConnectorConfig(BaseModel):
    """Configuration model for Snowflake connector from services."""

    accountIdentifier: str = Field(..., description="Snowflake account identifier")
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Authentication configuration")
    credentials: Optional[CredentialsConfig] = Field(
        default=None, description="Credentials configuration"
    )
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)


class SnowflakeClient(IClient):
    """Builder class for Snowflake clients with different construction methods.

    This class provides a unified interface for creating Snowflake clients
    using either OAuth or Personal Access Token (PAT) authentication.

    Example usage with OAuth:
        config = SnowflakeOAuthConfig(
            account_identifier="your_account_identifier",
            oauth_token="your_oauth_token"
        )
        client = SnowflakeClient.build_with_config(config)
        rest_client = client.get_client()

    Example usage with PAT:
        config = SnowflakePATConfig(
            account_identifier="your_account_identifier",
            pat_token="your_pat_token"
        )
        client = SnowflakeClient.build_with_config(config)
        rest_client = client.get_client()
    """

    def __init__(
        self,
        client: Union[SnowflakeRESTClientViaOAuth, SnowflakeRESTClientViaPAT],
    ) -> None:
        """Initialize with a Snowflake REST client.

        Args:
            client: Snowflake REST client (OAuth or PAT)
        """
        self._client = client

    def get_client(self) -> Union[SnowflakeRESTClientViaOAuth, SnowflakeRESTClientViaPAT]:
        """Return the Snowflake REST client object."""
        return self._client

    def get_base_url(self) -> str:
        """Return the Snowflake API base URL."""
        return self._client.get_base_url()

    def get_account_identifier(self) -> str:
        """Return the Snowflake account identifier."""
        return self._client.get_account_identifier()

    @classmethod
    def build_with_config(
        cls,
        config: Union[SnowflakeOAuthConfig, SnowflakePATConfig],
    ) -> "SnowflakeClient":
        """Build SnowflakeClient with configuration.

        Args:
            config: Snowflake configuration instance (OAuth or PAT)

        Returns:
            SnowflakeClient instance
        """
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "SnowflakeClient":
        """Build SnowflakeClient using configuration service.

        This method retrieves Snowflake connector configuration from
        the configuration service (etcd) and creates the appropriate client.

        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            SnowflakeClient instance

        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            logger.debug(f"🔧 [SnowflakeClient] build_from_services called with connector_instance_id: {connector_instance_id}")
            
            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            logger.debug(f"🔧 [SnowflakeClient] Raw config_dict keys: {list(config_dict.keys()) if config_dict else 'None'}")
            logger.debug(f"🔧 [SnowflakeClient] Raw config_dict: {config_dict}")

            config = SnowflakeConnectorConfig.model_validate(config_dict)
            logger.debug(f"🔧 [SnowflakeClient] Validated config - accountIdentifier: '{config.accountIdentifier}'")
            logger.debug(f"🔧 [SnowflakeClient] Validated config - auth.authType: '{config.auth.authType}'")
            logger.debug(f"🔧 [SnowflakeClient] Validated config - timeout: {config.timeout}")

            auth_type = config.auth.authType
            account_identifier = config.accountIdentifier
            timeout = config.timeout

            if auth_type == AuthType.OAUTH:
                logger.debug(f"🔧 [SnowflakeClient] Using OAuth authentication")
                if not config.credentials or not config.credentials.access_token:
                    raise ValueError("OAuth access token required for OAuth auth type")
                client = SnowflakeRESTClientViaOAuth(
                    account_identifier=account_identifier,
                    oauth_token=config.credentials.access_token,
                    timeout=timeout,
                )

            elif auth_type == AuthType.PAT:
                logger.debug(f"🔧 [SnowflakeClient] Using PAT authentication")
                logger.debug(f"🔧 [SnowflakeClient] patToken present: {bool(config.auth.patToken)}")
                if not config.auth.patToken:
                    raise ValueError("PAT token required for PAT auth type")
                client = SnowflakeRESTClientViaPAT(
                    account_identifier=account_identifier,
                    pat_token=config.auth.patToken,
                    timeout=timeout,
                )

            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")

            logger.info(f"🔧 [SnowflakeClient] Successfully built client with base_url: '{client.get_base_url()}'")
            return cls(client=client)

        except ValidationError as e:
            logger.error(f"Invalid Snowflake connector configuration: {e}")
            raise ValueError("Invalid Snowflake connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build Snowflake client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Snowflake.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance ID

        Returns:
            Configuration dictionary

        Raises:
            ValueError: If configuration cannot be retrieved
        """
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(
                    f"Failed to get Snowflake connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(
                    f"Invalid Snowflake connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get Snowflake connector config: {e}")
            instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
            raise ValueError(
                f"Failed to get Snowflake connector configuration{instance_msg}"
            ) from e


class SnowflakeResponse(BaseModel):
    """Standard response wrapper for Snowflake API calls."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error code if failed")
    message: Optional[str] = Field(default=None, description="Error message if failed")
    statement_handle: Optional[str] = Field(
        default=None, description="Statement handle for async queries"
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

class SnowflakeSDKClient:
    """Snowflake SDK client for direct SQL query execution.

    Uses the official Snowflake Python connector (snowflake-connector-python)
    for executing SQL queries directly against Snowflake.

    Supports authentication via:
    - OAuth token
    - Username/Password
    - External browser (SSO)

    SDK Documentation: https://docs.snowflake.com/en/developer-guide/python-connector/python-connector
    """

    def __init__(
        self,
        account_identifier: str,
        warehouse: Optional[str] = None,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        role: Optional[str] = None,
        oauth_token: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        authenticator: Optional[str] = None,
        timeout: int = 60,
    ) -> None:
        """Initialize Snowflake SDK client.

        Args:
            account_identifier: Snowflake account identifier
            warehouse: Default warehouse to use
            database: Default database to use
            schema: Default schema to use
            role: Role to use for the session
            oauth_token: OAuth access token for authentication
            user: Username for password authentication
            password: Password for password authentication
            authenticator: Authentication method ('oauth', 'externalbrowser', etc.)
            timeout: Connection timeout in seconds
        """
        if snowflake_connector is None:
            raise ImportError("snowflake-connector-python is required for SQL SDK client. Install with: pip install snowflake-connector-python")
        self.account_identifier = self._clean_account_identifier(account_identifier)
        self.warehouse = warehouse
        self.database = database
        self.schema = schema
        self.role = role
        self.timeout = timeout
        self._connection: Optional["SnowflakeConnection"] = None

        # Store auth credentials
        self._oauth_token = oauth_token
        self._user = user
        self._password = password
        self._authenticator = authenticator or ("oauth" if oauth_token else None)

    @staticmethod
    def _clean_account_identifier(account_identifier: str) -> str:
        """Clean account identifier to extract just the account name.

        Args:
            account_identifier: Full or partial account identifier

        Returns:
            Clean account identifier for SDK connection
        """
        # Remove URL components if present
        account = account_identifier.replace("https://", "").replace("http://", "")
        account = account.replace(".snowflakecomputing.com", "")
        # Remove any trailing paths
        account = account.split("/")[0]
        return account

    def connect(self) -> "SnowflakeSDKClient":
        """Establish connection to Snowflake.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If connection fails
        """
        if self._connection is not None:
            return self

        try:
            connect_params: Dict[str, Any] = {
                "account": self.account_identifier,
                "login_timeout": self.timeout,
                "network_timeout": self.timeout,
            }

            # Add optional parameters
            if self.warehouse:
                connect_params["warehouse"] = self.warehouse
            if self.database:
                connect_params["database"] = self.database
            if self.schema:
                connect_params["schema"] = self.schema
            if self.role:
                connect_params["role"] = self.role

            # Authentication
            if self._oauth_token:
                connect_params["token"] = self._oauth_token
                connect_params["authenticator"] = "oauth"
            elif self._user and self._password:
                connect_params["user"] = self._user
                connect_params["password"] = self._password
            elif self._authenticator == "externalbrowser":
                connect_params["authenticator"] = "externalbrowser"
                if self._user:
                    connect_params["user"] = self._user

            self._connection = snowflake_connector.connect(**connect_params)  # type: ignore
            return self

        except Exception as e:
            raise ConnectionError(f"Failed to connect to Snowflake: {e}") from e

    def close(self) -> None:
        """Close the Snowflake connection."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception as e:
                logger.warning(f"Failed to close Snowflake connection gracefully: {e}")
            finally:
                self._connection = None

    def is_connected(self) -> bool:
        """Check if connection is active."""
        return self._connection is not None and not self._connection.is_closed()

    def execute_query(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any]]] = None,
        timeout: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        Args:
            query: SQL query to execute
            params: Optional query parameters (for parameterized queries)
            timeout: Optional query timeout in seconds

        Returns:
            List of dictionaries containing query results

        Raises:
            ConnectionError: If not connected
            RuntimeError: If query execution fails
        """
        if not self.is_connected():
            self.connect()

        try:
            cursor = self._connection.cursor()  # type: ignore
            if timeout is not None:
                if not isinstance(timeout, int) or timeout < 0:
                    raise ValueError("Query timeout must be a non-negative integer.")
                cursor.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout}")

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            # Get column names
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            # Fetch all results
            rows = cursor.fetchall()

            # Convert to list of dicts
            results = [dict(zip(columns, row)) for row in rows]

            cursor.close()
            return results

        except Exception as e:
            raise RuntimeError(f"Query execution failed: {e}") from e

    def execute_query_raw(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any]]] = None,
    ) -> tuple:
        """Execute a SQL query and return raw cursor results.

        Args:
            query: SQL query to execute
            params: Optional query parameters

        Returns:
            Tuple of (columns, rows) where columns is list of column names
            and rows is list of tuples

        Raises:
            ConnectionError: If not connected
            RuntimeError: If query execution fails
        """
        if not self.is_connected():
            self.connect()

        try:
            cursor = self._connection.cursor()  # type: ignore

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

            cursor.close()
            return (columns, rows)

        except Exception as e:
            raise RuntimeError(f"Query execution failed: {e}") from e

    def execute_many(
        self,
        query: str,
        params_list: List[Union[Dict[str, Any], List[Any], tuple]],
    ) -> int:
        """Execute a SQL query multiple times with different parameters.

        Args:
            query: SQL query to execute
            params_list: List of parameter sets

        Returns:
            Number of rows affected

        Raises:
            ConnectionError: If not connected
            RuntimeError: If query execution fails
        """
        if not self.is_connected():
            self.connect()

        try:
            cursor = self._connection.cursor()  # type: ignore
            cursor.executemany(query, params_list)
            rowcount = cursor.rowcount
            cursor.close()
            return rowcount

        except Exception as e:
            raise RuntimeError(f"Query execution failed: {e}") from e

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        return self.account_identifier

    def __enter__(self) -> "SnowflakeSDKClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

