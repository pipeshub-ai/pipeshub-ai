"""Slab client implementation.

This module provides a client for interacting with the Slab GraphQL API using
an organization-level API token (Bearer token).

Slab uses a GraphQL API exclusively (no REST endpoints).

Authentication Reference: https://slab.com/api/
GraphQL Endpoint: POST https://api.slab.com/v1/graphql
"""

import logging
from typing import Any

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# GraphQL client class
# ---------------------------------------------------------------------------


class SlabGraphQLClientViaToken(GraphQLClient):
    """Slab GraphQL client via API token.

    Slab uses organization-level API tokens passed as Bearer tokens
    in the Authorization header.

    Args:
        token: The Slab API token
        timeout: Request timeout in seconds
    """

    def __init__(self, token: str, timeout: int = 30) -> None:
        token = token.strip() if token else ""
        if not token:
            raise ValueError("Slab API token cannot be empty")

        auth_header = f"Bearer {token}" if not token.startswith("Bearer ") else token

        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }
        super().__init__(
            endpoint="https://api.slab.com/v1/graphql",
            headers=headers,
            timeout=timeout,
        )
        self.token = token

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint."""
        return self.endpoint

    @override
    def get_auth_header(self) -> str | None:
        """Get the authorization header value."""
        if self.token and not self.token.startswith("Bearer "):
            return f"Bearer {self.token}"
        return self.token

    def get_token(self) -> str:
        """Get the token."""
        return self.token

    def set_token(self, token: str) -> None:
        """Set the token and update Authorization header."""
        self.token = token
        if token and not token.startswith("Bearer "):
            self.headers["Authorization"] = f"Bearer {token}"
        else:
            self.headers["Authorization"] = token


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class SlabTokenConfig(BaseModel):
    """Configuration for Slab GraphQL client via API token.

    Args:
        token: Slab API token
        timeout: Request timeout in seconds
        endpoint: GraphQL endpoint (defaults to Slab's endpoint)
    """

    token: str = Field(..., description="Slab API token")
    timeout: int = Field(
        default=30, description="Request timeout in seconds", gt=0
    )
    endpoint: str = Field(
        default="https://api.slab.com/v1/graphql",
        description="GraphQL endpoint URL",
    )

    def create_client(self) -> SlabGraphQLClientViaToken:
        """Create a Slab GraphQL client."""
        return SlabGraphQLClientViaToken(self.token, self.timeout)


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class SlabClient(IClient):
    """Builder class for Slab GraphQL clients.

    Slab only supports organization-level API token authentication.
    """

    def __init__(
        self,
        client: SlabGraphQLClientViaToken,
    ) -> None:
        """Initialize with a Slab GraphQL client object."""
        self.client = client

    @override
    def get_client(self) -> SlabGraphQLClientViaToken:
        """Return the Slab GraphQL client object."""
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: SlabTokenConfig,
    ) -> "SlabClient":
        """Build SlabClient with configuration.

        Args:
            config: SlabTokenConfig instance

        Returns:
            SlabClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "SlabClient":
        """Build SlabClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            SlabClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get Slab connector configuration")

            auth_config: dict[str, Any] = raw_config.get("auth", {})
            timeout: int = raw_config.get("timeout", 30)

            token = auth_config.get("apiToken", "")
            if not token:
                raise ValueError("API token required for Slab authentication")

            client = SlabGraphQLClientViaToken(token, timeout)
            return cls(client)

        except Exception as e:
            logger.error(
                f"Failed to build Slab client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Slab."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Slab connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return dict(raw)  # type: ignore[arg-type]
        except Exception as e:
            logger.error(f"Failed to get Slab connector config: {e}")
            raise ValueError(
                f"Failed to get Slab connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
