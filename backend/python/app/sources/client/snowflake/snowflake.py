"""Snowflake client implementation.

This module provides clients for interacting with Snowflake's SQL REST API using either:
1. OAuth token authentication
2. Personal Access Token (PAT) authentication

Snowflake SQL API Reference: https://docs.snowflake.com/en/developer-guide/sql-api/index
Authentication: https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
"""

import logging
import re
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field, PrivateAttr, SecretStr  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient
from app.sources.client.snowflake.keypair import (
    KeyPairSigner,
    account_host_label,
    load_private_key,
)
from app.utils.sql_limits import SQL_QUERY_TIMEOUT_S

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from snowflake.connector import SnowflakeConnection  # type: ignore

try:
    import snowflake.connector as snowflake_connector  # type: ignore
except ImportError:
    snowflake_connector = None

# Above the SQL API's 45 s synchronous wait, so a slow statement comes back as a
# 202 with a handle to poll rather than as a client-side timeout.
DEFAULT_TIMEOUT_S = 60.0

_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class AuthType(str, Enum):
    """Authentication type for Snowflake connector."""

    OAUTH = "OAUTH"
    PAT = "PAT"
    KEY_PAIR = "KEY_PAIR"


def sql_api_base_url(account_identifier: str) -> str:
    """The SQL API base URL for an account identifier, a hostname or a URL."""
    parsed = urlparse(account_identifier if "://" in account_identifier else f"https://{account_identifier}")
    host = parsed.netloc or parsed.path.split("/")[0]
    suffix = ".snowflakecomputing.com"
    account = host[: -len(suffix)] if host.lower().endswith(suffix) else host
    # The account becomes part of the host name: anything else (":", "#", "?", "/", "@")
    # would let the value choose another server.
    if not _ACCOUNT_NAME.fullmatch(account):
        raise ValueError(f"Invalid Snowflake account identifier: {account_identifier!r}")
    return urlunparse(("https", f"{account}.snowflakecomputing.com", "/api/v2", "", "", ""))


class SnowflakeRESTClientViaOAuth(HTTPClient):
    """Snowflake REST client via OAuth token.

    OAuth Documentation: https://docs.snowflake.com/en/user-guide/oauth-intro
    """

    def __init__(
        self,
        account_identifier: str,
        oauth_token: str,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(token=oauth_token, token_type="Bearer", timeout=timeout)
        self.account_identifier = account_identifier
        self.oauth_token = oauth_token
        self._base_url = sql_api_base_url(account_identifier)

    @staticmethod
    def _build_base_url(account_identifier: str) -> str:
        return sql_api_base_url(account_identifier)

    def get_base_url(self) -> str:
        """Get the base URL for Snowflake API."""
        return self._base_url

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        return self.account_identifier


class SnowflakeRESTClientViaPAT(HTTPClient):
    """Snowflake REST client via Personal Access Token (PAT).

    PAT Documentation: https://docs.snowflake.com/en/user-guide/programmatic-access-tokens
    """

    def __init__(
        self,
        account_identifier: str,
        pat_token: str,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(token=pat_token, token_type="Bearer", timeout=timeout)
        # Tells the SQL API which kind of bearer token this is.
        self.headers["X-Snowflake-Authorization-Token-Type"] = "PROGRAMMATIC_ACCESS_TOKEN"
        self.account_identifier = account_identifier
        self.pat_token = pat_token
        self._base_url = sql_api_base_url(account_identifier)

    @staticmethod
    def _build_base_url(account_identifier: str) -> str:
        return sql_api_base_url(account_identifier)

    def get_base_url(self) -> str:
        """Get the base URL for Snowflake API."""
        return self._base_url

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        return self.account_identifier


class SnowflakeRESTClientViaKeyPair(HTTPClient):
    """Snowflake REST client via key-pair JWT (D-501).

    A JWT lives at most an hour and a sync can run longer, so every request
    gets a current token: the data source copies the headers when it builds
    a request, and that copy would otherwise carry the old one.
    """

    def __init__(
        self,
        account_identifier: str,
        signer: KeyPairSigner,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        super().__init__(token=signer.token(), token_type="Bearer", timeout=timeout)
        self.headers["X-Snowflake-Authorization-Token-Type"] = "KEYPAIR_JWT"
        self.account_identifier = account_identifier
        self.signer = signer
        self._base_url = sql_api_base_url(account_identifier)

    async def execute(self, request: HTTPRequest, **kwargs: Any) -> HTTPResponse:  # noqa: ANN401
        authorization = f"Bearer {self.signer.token()}"
        self.headers["Authorization"] = authorization
        request.headers["Authorization"] = authorization
        return await super().execute(request, **kwargs)

    def get_base_url(self) -> str:
        return self._base_url

    def get_account_identifier(self) -> str:
        return self.account_identifier


class SnowflakeOAuthConfig(BaseModel):
    """Configuration for Snowflake client via OAuth token."""

    account_identifier: str = Field(..., description="Snowflake account identifier")
    oauth_token: str = Field(..., description="OAuth access token")
    timeout: float = Field(default=DEFAULT_TIMEOUT_S, description="Request timeout in seconds", gt=0)

    def create_client(self) -> SnowflakeRESTClientViaOAuth:
        """Create a Snowflake REST client with OAuth authentication."""
        return SnowflakeRESTClientViaOAuth(
            account_identifier=self.account_identifier,
            oauth_token=self.oauth_token,
            timeout=self.timeout,
        )


class SnowflakePATConfig(BaseModel):
    """Configuration for Snowflake client via Personal Access Token (PAT)."""

    account_identifier: str = Field(..., description="Snowflake account identifier")
    pat_token: str = Field(..., description="Personal Access Token")
    timeout: float = Field(default=DEFAULT_TIMEOUT_S, description="Request timeout in seconds", gt=0)

    def create_client(self) -> SnowflakeRESTClientViaPAT:
        """Create a Snowflake REST client with PAT authentication."""
        return SnowflakeRESTClientViaPAT(
            account_identifier=self.account_identifier,
            pat_token=self.pat_token,
            timeout=self.timeout,
        )


class SnowflakeConnectionSettings(BaseModel):
    """What a Snowflake connector's stored config says about connecting.

    Secrets are ``SecretStr``: a repr, log line or validation error never shows them.
    """

    account_identifier: str
    warehouse: str
    auth_type: AuthType
    token: SecretStr = SecretStr("")
    user: str = ""
    private_key: SecretStr = SecretStr("")
    passphrase: SecretStr = SecretStr("")
    # None: the user's default role (D-502).
    role: str | None = None

    _signer: KeyPairSigner | None = PrivateAttr(default=None)

    def signer(self) -> KeyPairSigner:
        """The JWT signer for key-pair auth, built once. Raises ValueError for a key or user that cannot be used."""
        if self._signer is None:
            key = load_private_key(self.private_key.get_secret_value(), self.passphrase.get_secret_value() or None)
            self._signer = KeyPairSigner(self.account_identifier, self.user, key)
        return self._signer

    def create_client(self, timeout: float = DEFAULT_TIMEOUT_S) -> "SnowflakeClient":
        if self.auth_type == AuthType.KEY_PAIR:
            return SnowflakeClient(
                SnowflakeRESTClientViaKeyPair(self.account_identifier, self.signer(), timeout=timeout),
            )
        if self.auth_type == AuthType.OAUTH:
            config: SnowflakeOAuthConfig | SnowflakePATConfig = SnowflakeOAuthConfig(
                account_identifier=self.account_identifier, oauth_token=self.token.get_secret_value(),
                timeout=timeout,
            )
        else:
            config = SnowflakePATConfig(
                account_identifier=self.account_identifier, pat_token=self.token.get_secret_value(),
                timeout=timeout,
            )
        return SnowflakeClient.build_with_config(config)


# The setup form's auth types: PAT under ACCESS_KEY (saved connectors carry it), and key pair.
# PAT itself is what configs written through the API carried.
_FORM_AUTH_TYPES = {"ACCESS_KEY": AuthType.PAT, "PAT": AuthType.PAT, "KEY_PAIR": AuthType.KEY_PAIR}


def parse_connection_settings(config: dict[str, Any]) -> SnowflakeConnectionSettings:
    """Read a connector's stored config; ``auth`` holds the account, warehouse, role and credentials.

    Raises ValueError with a message the user can act on, and never one that
    holds a secret. A form without ``authType`` is key pair when it carries a
    private key, else PAT.
    """
    auth = config.get("auth") or {}
    if not isinstance(auth, dict):
        raise ValueError("The Snowflake connector configuration has no auth settings.")
    form_type = auth.get("authType") or ("KEY_PAIR" if auth.get("privateKey") else "ACCESS_KEY")
    auth_type = _FORM_AUTH_TYPES.get(str(form_type).upper())
    if auth_type is None:
        raise ValueError(
            f"Snowflake does not support the auth type {form_type!r}. "
            "Edit the connector to use a personal access token or a key pair."
        )
    account = (auth.get("accountIdentifier") or "").strip()
    warehouse = (auth.get("warehouse") or "").strip()
    role = (auth.get("role") or "").strip() or None

    if not account:
        raise ValueError("The Snowflake account identifier is missing.")
    account_host_label(account)
    if not warehouse:
        raise ValueError("A Snowflake warehouse is required to read the catalog and run queries.")
    if auth_type == AuthType.KEY_PAIR:
        user = (auth.get("user") or "").strip()
        if not user:
            raise ValueError("The Snowflake user (its login name) is missing.")
        settings = SnowflakeConnectionSettings(
            account_identifier=account, warehouse=warehouse, auth_type=auth_type, role=role, user=user,
            private_key=SecretStr(auth.get("privateKey") or ""),
            passphrase=SecretStr(auth.get("privateKeyPassphrase") or ""),
        )
        # Read the key now, so a bad one fails before any request.
        settings.signer()
        return settings
    pat_token = (auth.get("patToken") or "").strip()
    if not pat_token:
        raise ValueError("The Snowflake personal access token is missing.")
    return SnowflakeConnectionSettings(
        account_identifier=account, warehouse=warehouse, auth_type=auth_type, role=role,
        token=SecretStr(pat_token),
    )


class SnowflakeClient(IClient):
    """Builder for Snowflake REST clients (OAuth or PAT)."""

    def __init__(
        self,
        client: SnowflakeRESTClientViaOAuth | SnowflakeRESTClientViaPAT | SnowflakeRESTClientViaKeyPair,
    ) -> None:
        self._client = client

    def get_client(self) -> SnowflakeRESTClientViaOAuth | SnowflakeRESTClientViaPAT | SnowflakeRESTClientViaKeyPair:
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
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "SnowflakeClient":
        """Build a client from the connector instance's stored config.

        Raises ValueError if the config is missing or incomplete.
        """
        config = await cls._get_connector_config(logger, config_service, connector_instance_id)
        return parse_connection_settings(config).create_client()

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Snowflake.

        Raises:
            ValueError: If configuration cannot be retrieved
        """
        instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
        except Exception as e:
            logger.error(f"Failed to get Snowflake connector config{instance_msg}: {e}")
            raise ValueError(f"Failed to get Snowflake connector configuration{instance_msg}") from e
        if not config:
            raise ValueError(f"Failed to get Snowflake connector configuration{instance_msg}")
        if not isinstance(config, dict):
            raise ValueError(f"Invalid Snowflake connector configuration format{instance_msg}")
        return config


class SnowflakeResponse(BaseModel):
    """Standard response wrapper for Snowflake API calls."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error code if failed")
    message: Optional[str] = Field(default=None, description="Error message if failed")
    status_code: Optional[int] = Field(
        default=None,
        description="HTTP status the Snowflake API returned, when the call reached it",
    )
    # The SQL API answers every statement-level failure with HTTP 422 — revoked
    # privilege, dropped table and suspended warehouse alike — so the status
    # discriminates nothing and this is the only signal that tells them apart.
    sql_state: Optional[str] = Field(
        default=None,
        description="SQLSTATE of a statement-level failure, from the response body",
    )
    statement_handle: Optional[str] = Field(
        default=None, description="Statement handle for async queries"
    )
    # Snowflake's own error code from the body ("390144"), which 401s and 403s carry without a SQLSTATE.
    code: str | None = Field(default=None, description="Snowflake error code from the response body")
    # When the call never got an answer: the exception's class ("ConnectTimeout"), as its text may be empty.
    exception_type: str | None = Field(default=None, description="Exception class when no response came")

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
        role: str | None = None,
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
                # Connector 4.x cancels any query at network_timeout, and 5.x maps it to a
                # connection-wide query_timeout: keep it past the server's limit for agent queries.
                "network_timeout": max(self.timeout, SQL_QUERY_TIMEOUT_S + 10),
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
        *,
        timeout_s: int | None = None,
        max_rows: int | None = None,
    ) -> tuple:
        """Execute a SQL query and return raw cursor results.

        Args:
            query: SQL query to execute
            params: Optional query parameters
            timeout_s: STATEMENT_TIMEOUT_IN_SECONDS for this statement only
            max_rows: Read at most this many rows

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

            # A statement parameter: Snowflake enforces it, and unlike ALTER SESSION it ends with this query.
            options = {"_statement_params": {"STATEMENT_TIMEOUT_IN_SECONDS": str(int(timeout_s))}} if timeout_s else {}
            if params:
                cursor.execute(query, params, **options)
            else:
                cursor.execute(query, **options)

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(max_rows) if max_rows else cursor.fetchall()

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

