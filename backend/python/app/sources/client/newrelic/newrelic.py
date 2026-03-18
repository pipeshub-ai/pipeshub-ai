"""NewRelic client implementation.

This module provides a client for interacting with the NewRelic NerdGraph
(GraphQL) API using the existing GraphQL client base.

NewRelic uses NerdGraph as its primary API:
- Endpoint: https://api.newrelic.com/graphql
- Authentication: Api-Key header (NOT Bearer token)

Authentication Reference: https://docs.newrelic.com/docs/apis/intro-apis/new-relic-api-keys/
NerdGraph Reference: https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/
"""

import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Default endpoint
# ---------------------------------------------------------------------------

NEWRELIC_GRAPHQL_ENDPOINT = "https://api.newrelic.com/graphql"

# ---------------------------------------------------------------------------
# GraphQL client class
# ---------------------------------------------------------------------------


class NewRelicGraphQLClientViaApiKey(GraphQLClient):
    """NewRelic NerdGraph client via API key.

    NewRelic uses a custom header `Api-Key` for authentication (not
    the standard Authorization: Bearer pattern).

    Args:
        api_key: NewRelic API key (e.g., NRAK-XXXXX)
        endpoint: NerdGraph endpoint URL
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str = NEWRELIC_GRAPHQL_ENDPOINT,
        timeout: int = 30,
    ) -> None:
        api_key = api_key.strip() if api_key else ""
        if not api_key:
            raise ValueError("NewRelic API key cannot be empty")

        headers = {
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }
        super().__init__(
            endpoint=endpoint,
            headers=headers,
            timeout=timeout,
        )
        self.api_key = api_key

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint."""
        return self.endpoint

    @override
    def get_auth_header(self) -> str | None:
        """Get the authorization header value.

        NewRelic uses Api-Key header, not Authorization.
        This returns the Api-Key value for reference.
        """
        return f"Api-Key {self.api_key}"

    def get_api_key(self) -> str:
        """Get the API key."""
        return self.api_key

    def set_api_key(self, api_key: str) -> None:
        """Set the API key and update the Api-Key header."""
        self.api_key = api_key
        self.headers["Api-Key"] = api_key


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class NewRelicApiKeyConfig(BaseModel):
    """Configuration for NewRelic NerdGraph client via API key.

    Args:
        api_key: NewRelic API key (NRAK-XXXXX)
        endpoint: NerdGraph endpoint URL
        timeout: Request timeout in seconds
    """

    api_key: str = Field(..., description="NewRelic API key (NRAK-XXXXX)")
    endpoint: str = Field(
        default=NEWRELIC_GRAPHQL_ENDPOINT,
        description="NerdGraph endpoint URL",
    )
    timeout: int = Field(
        default=30, description="Request timeout in seconds", gt=0
    )

    def create_client(self) -> NewRelicGraphQLClientViaApiKey:
        """Create a NewRelic NerdGraph client."""
        return NewRelicGraphQLClientViaApiKey(
            api_key=self.api_key,
            endpoint=self.endpoint,
            timeout=self.timeout,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class NewRelicAuthConfig(BaseModel):
    """Auth section of the NewRelic connector configuration from etcd."""

    authType: str = "API_KEY"
    apiKey: str | None = None

    class Config:
        extra = "allow"


class NewRelicConnectorConfig(BaseModel):
    """Top-level NewRelic connector configuration from etcd."""

    auth: NewRelicAuthConfig = Field(default_factory=NewRelicAuthConfig)
    timeout: int = 30

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class NewRelicClient(IClient):
    """Builder class for NewRelic NerdGraph clients.

    NewRelic only supports API key authentication for NerdGraph.
    """

    def __init__(
        self,
        client: NewRelicGraphQLClientViaApiKey,
    ) -> None:
        """Initialize with a NewRelic NerdGraph client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> NewRelicGraphQLClientViaApiKey:
        """Return the NewRelic NerdGraph client object."""
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: NewRelicApiKeyConfig,
    ) -> "NewRelicClient":
        """Build NewRelicClient with configuration.

        Args:
            config: NewRelicApiKeyConfig instance

        Returns:
            NewRelicClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "NewRelicClient":
        """Build NewRelicClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            NewRelicClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get NewRelic connector configuration"
                )

            connector_config = NewRelicConnectorConfig.model_validate(
                raw_config
            )

            api_key = connector_config.auth.apiKey or ""
            if not api_key:
                raise ValueError(
                    "API key required for NewRelic authentication"
                )

            timeout = connector_config.timeout

            client = NewRelicGraphQLClientViaApiKey(
                api_key=api_key, timeout=timeout
            )
            return cls(client)

        except Exception as e:
            logger.error(
                f"Failed to build NewRelic client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for NewRelic."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get NewRelic connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get NewRelic connector config: {e}")
            raise ValueError(
                f"Failed to get NewRelic connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
