"""Odoo client implementation.

This module provides authentication and raw XML-RPC access to an Odoo
instance via the stdlib ``xmlrpc.client``.

Odoo External API docs: https://www.odoo.com/documentation/17.0/developer/reference/external_api.html
"""

from __future__ import annotations

import asyncio
import logging
import xmlrpc.client
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)


class OdooClient:
    """Odoo client for XML-RPC authentication and raw calls.

    xmlrpc.client is synchronous, so every network call is pushed to a
    worker thread via ``asyncio.to_thread`` to keep this usable from async
    connector code without blocking the event loop.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        api_key: str,
        timeout: int = 30,
    ) -> None:
        """Initialize the Odoo client. No network call happens here."""
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.api_key = api_key
        self.timeout = timeout

        self._uid: int | None = None
        self._models: xmlrpc.client.ServerProxy | None = None
        self._connect_lock = asyncio.Lock()  # serialize concurrent connect() calls
        # xmlrpc.client.ServerProxy reuses one HTTP connection across calls;
        # that connection is stateful (request/response must alternate), so
        # concurrent execute_kw() calls from different threads (asyncio.to_thread)
        # race on it and raise CannotSendRequest/"Idle". Serialize all calls
        # through this lock instead.
        self._call_lock = asyncio.Lock()

        logger.info(f"🔧 [OdooClient] Initialized for {username}@{self.url} (db={db})")

    async def connect(self) -> "OdooClient":
        """Authenticate against Odoo and cache the resulting uid.

        This is the actual auth check — Odoo's XML-RPC ``authenticate`` call
        is a real login handshake, not just object construction, so a bad
        db/username/api key fails right here instead of during the first sync.
        """
        if self._uid is not None:
            return self

        async with self._connect_lock:
            if self._uid is not None:
                return self

            try:
                common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
                uid = await asyncio.wait_for(
                    asyncio.to_thread(common.authenticate, self.db, self.username, self.api_key, {}),
                    timeout=self.timeout,
                )
                if not uid:
                    raise ConnectionError("Odoo rejected the credentials (authenticate returned no uid)")

                self._uid = uid
                self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
                logger.info(f"🔧 [OdooClient] Authenticated as uid={uid} ({self.username}@{self.url})")
                return self

            except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError, asyncio.TimeoutError) as e:
                logger.error(f"🔧 [OdooClient] Authentication failed: {e}")
                raise ConnectionError(f"Failed to authenticate with Odoo: {e}") from e

    def is_connected(self) -> bool:
        """Check whether authenticate() has already succeeded."""
        return self._uid is not None

    async def close(self) -> None:
        """Drop the cached session.

        XML-RPC over HTTP is stateless per-call (no pooled connection like
        asyncpg) — this just forgets the uid so the next call re-authenticates.
        """
        self._uid = None
        self._models = None

    async def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """Generic Odoo model-method call — the one raw primitive every
        higher-level DataSource operation (search_read, search_count, ...)
        will be built on top of."""
        if not self.is_connected():
            await self.connect()

        if self._models is None:
            raise RuntimeError("Odoo client not authenticated")

        try:
            async with self._call_lock:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._models.execute_kw,
                        self.db, self._uid, self.api_key, model, method, args or [], kwargs or {},
                    ),
                    timeout=self.timeout,
                )
        except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError, OSError, asyncio.TimeoutError) as e:
            logger.error(f"🔧 [OdooClient] execute_kw({model}.{method}) failed: {e}")
            raise RuntimeError(f"Odoo call failed ({model}.{method}): {e}") from e

    def get_connection_info(self) -> dict[str, Any]:
        """Get connection information (never includes the API key)."""
        return {
            "url": self.url,
            "db": self.db,
            "username": self.username,
        }

    async def __aenter__(self) -> "OdooClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class OdooConfig(BaseModel):
    """Configuration for the Odoo client."""

    url: str = Field(..., description="Odoo server URL, e.g. https://mycompany.odoo.com")
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., description="Odoo login/email")
    api_key: str = Field(
        ...,
        description="Odoo API key (Settings > Users > Account Security > API Keys)",
        validation_alias=AliasChoices("apiKey", "api_key", "password"),
    )
    timeout: int = Field(default=30, description="Request timeout in seconds", gt=0)

    def create_client(self) -> OdooClient:
        """Create an Odoo client."""
        return OdooClient(
            url=self.url,
            db=self.db,
            username=self.username,
            api_key=self.api_key,
            timeout=self.timeout,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for the Odoo connector, as stored in
    the connector's config (matches the AuthField names declared on the
    connector's @ConnectorBuilder: baseUrl, db, username, apiKey)."""

    url: str = Field(..., validation_alias=AliasChoices("baseUrl", "url"))
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., validation_alias=AliasChoices("username", "user"))
    api_key: str = Field(..., validation_alias=AliasChoices("apiKey", "api_key", "password"))


class OdooConnectorConfig(BaseModel):
    """Configuration model for the Odoo connector, as read from services."""

    auth: AuthConfig = Field(..., description="Authentication configuration")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)


class OdooClientBuilder(IClient):
    """Builder class for Odoo clients — same shape as every other
    connector's ``*ClientBuilder(IClient)`` (see PostgreSQLClientBuilder).

    Example usage:
        config = OdooConfig(url="https://mycompany.odoo.com", db="mycompany",
                             username="admin", api_key="xxxx")
        client_builder = OdooClientBuilder.build_with_config(config)
        client = client_builder.get_client()
        await client.connect()
    """

    def __init__(self, client: OdooClient) -> None:
        self._client = client

    def get_client(self) -> OdooClient:
        """Return the Odoo client object."""
        return self._client

    @classmethod
    def build_with_config(cls, config: OdooConfig) -> "OdooClientBuilder":
        """Build OdooClientBuilder from an explicit config (no I/O)."""
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "OdooClientBuilder":
        """Build OdooClientBuilder from the connector's stored config (etcd)."""
        try:
            config_dict = await cls._get_connector_config(logger, config_service, connector_instance_id)
            config = OdooConnectorConfig.model_validate(config_dict)

            client = OdooClient(
                url=config.auth.url,
                db=config.auth.db,
                username=config.auth.username,
                api_key=config.auth.api_key,
                timeout=config.timeout,
            )
            logger.info(f"🔧 [OdooClientBuilder] Built client for {config.auth.username}@{config.auth.url}")
            return cls(client=client)

        except ValidationError as e:
            logger.error(f"Invalid Odoo connector configuration: {e}")
            raise ValueError("Invalid Odoo connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build Odoo client from services: {e}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Odoo."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config or not isinstance(config, dict):
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(f"Failed to get Odoo connector configuration{instance_msg}")
            return config
        except Exception as e:
            logger.error(f"Failed to get Odoo connector config: {e}")
            instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
            raise ValueError(f"Failed to get Odoo connector configuration{instance_msg}") from e
