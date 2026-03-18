# ruff: noqa
"""
Simpplr REST API DataSource - Auto-generated API wrapper

Generated from Simpplr REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.simpplr.simpplr import SimpplrClient, SimpplrResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class SimpplrDataSource:
    """Simpplr REST API DataSource

    Provides async wrapper methods for Simpplr REST API operations:
    - Sites management
    - Content management
    - Users management
    - Pages management
    - Events management
    - Newsletters management
    - Search
    - Analytics

    All methods return SimpplrResponse objects.
    """

    def __init__(self, client: SimpplrClient) -> None:
        """Initialize with SimpplrClient.

        Args:
            client: SimpplrClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'SimpplrDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> SimpplrClient:
        """Return the underlying SimpplrClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Sites
    # -----------------------------------------------------------------------

    async def list_sites(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """List all sites

        HTTP GET /sites

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/sites"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_sites" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute list_sites")


    async def get_site(
        self,
        site_id: str
    ) -> SimpplrResponse:
        """Get a specific site by ID

        HTTP GET /sites/{site_id}

        Args:
            site_id: The site id

        Returns:
            SimpplrResponse with operation result
        """

        url = self.base_url + "/sites/{site_id}".format(site_id=site_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_site" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_site")


    # -----------------------------------------------------------------------
    # Content
    # -----------------------------------------------------------------------

    async def list_content(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """List all content

        HTTP GET /content

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute list_content")


    async def get_content(
        self,
        content_id: str
    ) -> SimpplrResponse:
        """Get a specific content item by ID

        HTTP GET /content/{content_id}

        Args:
            content_id: The content id

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_content")


    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """List all users

        HTTP GET /users

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute list_users")


    async def get_user(
        self,
        user_id: str
    ) -> SimpplrResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user id

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_user")


    # -----------------------------------------------------------------------
    # Pages
    # -----------------------------------------------------------------------

    async def list_pages(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """List all pages

        HTTP GET /pages

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute list_pages")


    async def get_page(
        self,
        page_id: str
    ) -> SimpplrResponse:
        """Get a specific page by ID

        HTTP GET /pages/{page_id}

        Args:
            page_id: The page id

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_page")


    # -----------------------------------------------------------------------
    # Events
    # -----------------------------------------------------------------------

    async def list_events(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """List all events

        HTTP GET /events

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_events" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute list_events")


    async def get_event(
        self,
        event_id: str
    ) -> SimpplrResponse:
        """Get a specific event by ID

        HTTP GET /events/{event_id}

        Args:
            event_id: The event id

        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_event" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_event")


    # -----------------------------------------------------------------------
    # Newsletters
    # -----------------------------------------------------------------------

    async def list_newsletters(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """List all newsletters

        HTTP GET /newsletters

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/newsletters"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_newsletters" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute list_newsletters")


    async def get_newsletter(
        self,
        newsletter_id: str
    ) -> SimpplrResponse:
        """Get a specific newsletter by ID

        HTTP GET /newsletters/{newsletter_id}

        Args:
            newsletter_id: The newsletter id

        Returns:
            SimpplrResponse with operation result
        """

        url = self.base_url + "/newsletters/{newsletter_id}".format(newsletter_id=newsletter_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_newsletter" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_newsletter")


    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self,
        *,
        q: str | None = None,
        type: str | None = None,
        limit: int | None = None,
        offset: int | None = None
    ) -> SimpplrResponse:
        """Search across Simpplr content

        HTTP GET /search

        Args:
            q: Search query string
            type: Content type filter
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            SimpplrResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if q is not None:
            query_params['q'] = str(q)
        if type is not None:
            query_params['type'] = str(type)
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute search")


    # -----------------------------------------------------------------------
    # Analytics
    # -----------------------------------------------------------------------

    async def get_content_analytics(
        self
    ) -> SimpplrResponse:
        """Get content analytics

        HTTP GET /analytics/content

        Returns:
            SimpplrResponse with operation result
        """

        url = self.base_url + "/analytics/content"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute get_content_analytics")

