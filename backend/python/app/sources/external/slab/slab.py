from typing import Any

from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.slab.graphql_op import SlabGraphQLOperations
from app.sources.client.slab.slab import (
    SlabClient,
)


class SlabDataSource:
    """
    Slab GraphQL API client wrapper
    Auto-generated wrapper for Slab GraphQL operations.
    This class provides unified access to all Slab GraphQL operations while
    maintaining proper typing and error handling.

    Coverage:
    - Organization info
    - Users listing
    - Posts (list, get, search)
    - Topics (list, get)
    - Mutations (syncPost)
    """

    def __init__(self, slab_client: SlabClient) -> None:
        """
        Initialize the Slab GraphQL data source.
        Args:
            slab_client (SlabClient): Slab client instance
        """
        self._slab_client = slab_client

    # =============================================================================
    # QUERY OPERATIONS
    # =============================================================================

    async def organization(self) -> GraphQLResponse:
        """Get organization information"""
        query = SlabGraphQLOperations.get_operation_with_fragments("query", "organization")
        variables: dict[str, Any] = {}

        try:
            return await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="organization"
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query organization: {str(e)}",
            )

    async def users(self) -> GraphQLResponse:
        """List all users in the organization"""
        query = SlabGraphQLOperations.get_operation_with_fragments("query", "users")
        variables: dict[str, Any] = {}

        try:
            return await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="users"
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query users: {str(e)}",
            )

    async def posts(
        self,
        status: str | None = None,
    ) -> GraphQLResponse:
        """List posts with optional status filter

        Args:
            status: Post status filter (e.g. PUBLISHED)
        """
        query = SlabGraphQLOperations.get_operation_with_fragments("query", "posts")
        variables: dict[str, Any] = {}
        if status is not None:
            variables["status"] = status

        try:
            return await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="posts"
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query posts: {str(e)}",
            )

    async def post(self, post_id: str) -> GraphQLResponse:
        """Get a single post by ID

        Args:
            post_id: Post ID
        """
        query = SlabGraphQLOperations.get_operation_with_fragments("query", "post")
        variables: dict[str, Any] = {"id": post_id}

        try:
            return await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="post"
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query post: {str(e)}",
            )

    async def topics(self) -> GraphQLResponse:
        """List all topics"""
        query = SlabGraphQLOperations.get_operation_with_fragments("query", "topics")
        variables: dict[str, Any] = {}

        try:
            return await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="topics"
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query topics: {str(e)}",
            )

    async def topic(self, topic_id: str) -> GraphQLResponse:
        """Get a single topic by ID with its posts

        Args:
            topic_id: Topic ID
        """
        query = SlabGraphQLOperations.get_operation_with_fragments("query", "topic")
        variables: dict[str, Any] = {"id": topic_id}

        try:
            return await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="topic"
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query topic: {str(e)}",
            )

    async def search_posts(self, query: str) -> GraphQLResponse:
        """Search posts by query string

        Args:
            query: Search query string
        """
        graphql_query = SlabGraphQLOperations.get_operation_with_fragments(
            "query", "searchPosts"
        )
        variables: dict[str, Any] = {"query": query}

        try:
            return await self._slab_client.get_client().execute(
                query=graphql_query,
                variables=variables,
                operation_name="searchPosts",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query searchPosts: {str(e)}",
            )

    # =============================================================================
    # MUTATION OPERATIONS
    # =============================================================================

    async def sync_post(self, sync_input: dict[str, Any]) -> GraphQLResponse:
        """Create or update a post via sync

        Args:
            sync_input: Sync post input object
        """
        graphql_query = SlabGraphQLOperations.get_operation_with_fragments(
            "mutation", "syncPost"
        )
        variables: dict[str, Any] = {"input": sync_input}

        try:
            return await self._slab_client.get_client().execute(
                query=graphql_query,
                variables=variables,
                operation_name="syncPost",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation syncPost: {str(e)}",
            )
