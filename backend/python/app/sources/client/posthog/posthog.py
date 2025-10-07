import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient


class PostHogResponse(BaseModel):
    """Standardized PostHog API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.dict()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class PostHogGraphQLClientViaToken:
    """PostHog GraphQL client via Personal API Key
    Args:
        api_key: The PostHog personal API key
        endpoint: The GraphQL endpoint URL (default: PostHog Cloud)
        timeout: Request timeout in seconds
        use_header_auth: If True, use Authorization header; if False, use request body
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://app.posthog.com/api/graphql",
        timeout: int = 30,
        use_header_auth: bool = True
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.use_header_auth = use_header_auth
        self._client: Optional[GraphQLClient] = None

    def create_client(self) -> GraphQLClient:
        """Create and configure the GraphQL client"""
        headers = {}

        if self.use_header_auth:
            # Option 1: Use Authorization header with Bearer token
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._client = GraphQLClient(
            endpoint=self.endpoint,
            headers=headers,
            timeout=self.timeout
        )
        return self._client

    def get_graphql_client(self) -> GraphQLClient:
        """Get the underlying GraphQL client"""
        if self._client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._client

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint URL"""
        return self.endpoint

    async def execute_query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None
    ) -> PostHogResponse:
        """Execute a GraphQL query with PostHog authentication
        Args:
            query: GraphQL query string
            variables: Optional query variables
            operation_name: Optional operation name
        Returns:
            PostHogResponse object
        """
        if self._client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")

        # If using body authentication, add API key to variables
        if not self.use_header_auth:
            variables = (variables or {}).copy()
            variables["personal_api_key"] = self.api_key

        try:
            response = await self._client.execute(
                query=query,
                variables=variables,
                operation_name=operation_name
            )

            if response.success:
                return PostHogResponse(
                    success=True,
                    data=response.data,
                    message=response.message
                )
            else:
                return PostHogResponse(
                    success=False,
                    error=response.error,
                    message=response.message
                )
        except Exception as e:
            logging.error(f"Query execution failed: {str(e)}", exc_info=True)
            return PostHogResponse(
                success=False,
                error=str(e),
                message=f"Query execution failed: {str(e)}"
            )

    async def close(self) -> None:
        """Close the underlying GraphQL client"""
        if self._client:
            await self._client.close()


class PostHogTokenConfig(BaseModel):
    """Configuration for PostHog GraphQL client via Personal API Key
    Args:
        api_key: The PostHog personal API key
        endpoint: The GraphQL endpoint URL
        timeout: Request timeout in seconds
        use_header_auth: If True, use Authorization header; if False, use request body
        ssl: Whether to use SSL (always True for PostHog Cloud)
    """
    api_key: str
    endpoint: str = "https://app.posthog.com/api/graphql"
    timeout: int = 30
    use_header_auth: bool = True

    def create_client(self) -> PostHogGraphQLClientViaToken:
        """Create a PostHog GraphQL client"""
        return PostHogGraphQLClientViaToken(
            api_key=self.api_key,
            endpoint=self.endpoint,
            timeout=self.timeout,
            use_header_auth=self.use_header_auth
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.dict()


class PostHogClient(IClient):
    """Builder class for PostHog GraphQL clients with different construction methods"""

    def __init__(self, client: PostHogGraphQLClientViaToken) -> None:
        """Initialize with a PostHog GraphQL client object"""
        self.client = client

    def get_client(self) -> PostHogGraphQLClientViaToken:
        """Return the PostHog GraphQL client object"""
        return self.client

    def get_graphql_client(self) -> GraphQLClient:
        """Return the underlying GraphQL client"""
        return self.client.get_graphql_client()

    async def execute_query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None
    ) -> PostHogResponse:
        """Execute a GraphQL query
        Args:
            query: GraphQL query string
            variables: Optional query variables
            operation_name: Optional operation name
        Returns:
            PostHogResponse object
        """
        return await self.client.execute_query(query, variables, operation_name)

    @classmethod
    def build_with_config(cls, config: PostHogTokenConfig) -> "PostHogClient":
        """Build PostHogClient with configuration
        Args:
            config: PostHogTokenConfig instance
        Returns:
            PostHogClient instance
        """
        client = config.create_client()
        client.create_client()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "PostHogClient":
        """Build PostHogClient using configuration service and graph database service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: Graph database service instance
        Returns:
            PostHogClient instance
        """
        # TODO: Implement - fetch config from services
        # This would typically:
        # 1. Query graph_db_service for stored PostHog credentials
        # 2. Use config_service to get environment-specific settings
        # 3. Return appropriate client based on available credentials

        raise NotImplementedError("build_from_services is not yet implemented")

    async def close(self) -> None:
        """Close the client and cleanup resources"""
        await self.client.close()
