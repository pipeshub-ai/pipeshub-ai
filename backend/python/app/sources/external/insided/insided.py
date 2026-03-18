# ruff: noqa
"""
InSided (Gainsight Customer Communities) REST API DataSource - Auto-generated API wrapper

Generated from InSided REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.insided.insided import InSidedClient, InSidedResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class InSidedDataSource:
    """InSided REST API DataSource

    Provides async wrapper methods for InSided REST API operations:
    - Communities management
    - Categories management
    - Topics management
    - Posts management
    - Users management
    - Groups management
    - Search

    The base URL is https://api.insided.com/v2.

    All methods return InSidedResponse objects.
    """

    def __init__(self, client: InSidedClient) -> None:
        """Initialize with InSidedClient.

        Args:
            client: InSidedClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'InSidedDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> InSidedClient:
        """Return the underlying InSidedClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Communities
    # -----------------------------------------------------------------------

    async def get_communities(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """List all communities.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/communities"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_communities" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_communities")

    async def get_community(
        self,
        community_id: str,
    ) -> InSidedResponse:
        """Get a specific community by ID.

        Args:
            community_id: The community ID

        Returns:
            InSidedResponse with operation result
        """
        url = self.base_url + "/communities/{community_id}".format(community_id=community_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_community" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_community")

    # -----------------------------------------------------------------------
    # Categories
    # -----------------------------------------------------------------------

    async def get_categories(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """List all categories.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/categories"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_categories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_categories")

    async def get_category(
        self,
        category_id: str,
    ) -> InSidedResponse:
        """Get a specific category by ID.

        Args:
            category_id: The category ID

        Returns:
            InSidedResponse with operation result
        """
        url = self.base_url + "/categories/{category_id}".format(category_id=category_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_category" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_category")

    # -----------------------------------------------------------------------
    # Topics
    # -----------------------------------------------------------------------

    async def get_topics(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """List all topics.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/topics"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_topics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_topics")

    async def get_topic(
        self,
        topic_id: str,
    ) -> InSidedResponse:
        """Get a specific topic by ID.

        Args:
            topic_id: The topic ID

        Returns:
            InSidedResponse with operation result
        """
        url = self.base_url + "/topics/{topic_id}".format(topic_id=topic_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_topic" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_topic")

    # -----------------------------------------------------------------------
    # Posts
    # -----------------------------------------------------------------------

    async def get_posts(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """List all posts.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/posts"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_posts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_posts")

    async def get_post(
        self,
        post_id: str,
    ) -> InSidedResponse:
        """Get a specific post by ID.

        Args:
            post_id: The post ID

        Returns:
            InSidedResponse with operation result
        """
        url = self.base_url + "/posts/{post_id}".format(post_id=post_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_post" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_post")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """List all users.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_users")

    async def get_user(
        self,
        user_id: str,
    ) -> InSidedResponse:
        """Get a specific user by ID.

        Args:
            user_id: The user ID

        Returns:
            InSidedResponse with operation result
        """
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def get_groups(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """List all groups.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/groups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_groups")

    async def get_group(
        self,
        group_id: str,
    ) -> InSidedResponse:
        """Get a specific group by ID.

        Args:
            group_id: The group ID

        Returns:
            InSidedResponse with operation result
        """
        url = self.base_url + "/groups/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute get_group")

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self,
        q: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InSidedResponse:
        """Search across communities content.

        Args:
            q: Search query string
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InSidedResponse with operation result
        """
        query_params: dict[str, Any] = {'q': q}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/search"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute search")
