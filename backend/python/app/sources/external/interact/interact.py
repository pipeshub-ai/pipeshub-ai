# ruff: noqa
"""
Interact (Interact Intranet) REST API DataSource - Auto-generated API wrapper

Generated from Interact REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.interact.interact import InteractClient, InteractResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class InteractDataSource:
    """Interact REST API DataSource

    Provides async wrapper methods for Interact REST API operations:
    - Users management
    - Content management
    - Pages management
    - News management
    - Communities management
    - Events management
    - Search

    The base URL is https://api.interact-intranet.com/v1.

    All methods return InteractResponse objects.
    """

    def __init__(self, client: InteractClient) -> None:
        """Initialize with InteractClient.

        Args:
            client: InteractClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'InteractDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> InteractClient:
        """Return the underlying InteractClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """List all users.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
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
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_users")

    async def get_user(
        self,
        user_id: str,
    ) -> InteractResponse:
        """Get a specific user by ID.

        Args:
            user_id: The user ID

        Returns:
            InteractResponse with operation result
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
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Content
    # -----------------------------------------------------------------------

    async def get_content_list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """List all content items.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/content"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_content_list")

    async def get_content(
        self,
        content_id: str,
    ) -> InteractResponse:
        """Get a specific content item by ID.

        Args:
            content_id: The content ID

        Returns:
            InteractResponse with operation result
        """
        url = self.base_url + "/content/{content_id}".format(content_id=content_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_content")

    # -----------------------------------------------------------------------
    # Pages
    # -----------------------------------------------------------------------

    async def get_pages(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """List all pages.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/pages"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_pages")

    async def get_page(
        self,
        page_id: str,
    ) -> InteractResponse:
        """Get a specific page by ID.

        Args:
            page_id: The page ID

        Returns:
            InteractResponse with operation result
        """
        url = self.base_url + "/pages/{page_id}".format(page_id=page_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_page")

    # -----------------------------------------------------------------------
    # News
    # -----------------------------------------------------------------------

    async def get_news_list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """List all news items.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/news"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_news_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_news_list")

    async def get_news(
        self,
        news_id: str,
    ) -> InteractResponse:
        """Get a specific news item by ID.

        Args:
            news_id: The news ID

        Returns:
            InteractResponse with operation result
        """
        url = self.base_url + "/news/{news_id}".format(news_id=news_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_news" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_news")

    # -----------------------------------------------------------------------
    # Communities
    # -----------------------------------------------------------------------

    async def get_communities(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """List all communities.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
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
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_communities" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_communities")

    async def get_community(
        self,
        community_id: str,
    ) -> InteractResponse:
        """Get a specific community by ID.

        Args:
            community_id: The community ID

        Returns:
            InteractResponse with operation result
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
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_community" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_community")

    # -----------------------------------------------------------------------
    # Events
    # -----------------------------------------------------------------------

    async def get_events(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """List all events.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/events"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_events" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_events")

    async def get_event(
        self,
        event_id: str,
    ) -> InteractResponse:
        """Get a specific event by ID.

        Args:
            event_id: The event ID

        Returns:
            InteractResponse with operation result
        """
        url = self.base_url + "/events/{event_id}".format(event_id=event_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_event" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute get_event")

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self,
        q: str,
        *,
        type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> InteractResponse:
        """Search across Interact intranet content.

        Args:
            q: Search query string
            type: Filter by content type
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            InteractResponse with operation result
        """
        query_params: dict[str, Any] = {'q': q}
        if type is not None:
            query_params['type'] = type
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
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute search")
