# ruff: noqa
"""
PlusPlus REST API DataSource - Auto-generated API wrapper

Generated from PlusPlus REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.plusplus.plusplus import PlusPlusClient, PlusPlusResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PlusPlusDataSource:
    """PlusPlus REST API DataSource

    Provides async wrapper methods for PlusPlus REST API operations:
    - Events management
    - Users management
    - Tracks management
    - Channels management
    - Content management
    - Tags
    - Enrollments

    All methods return PlusPlusResponse objects.
    """

    def __init__(self, client: PlusPlusClient) -> None:
        """Initialize with PlusPlusClient.

        Args:
            client: PlusPlusClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PlusPlusDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PlusPlusClient:
        """Return the underlying PlusPlusClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Events
    # -----------------------------------------------------------------------

    async def list_events(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all events

        HTTP GET /events

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_events" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_events")

    async def get_event(
        self,
        event_id: str
    ) -> PlusPlusResponse:
        """Get a specific event by ID

        HTTP GET /events/{event_id}

        Args:
            event_id: The event ID

        Returns:
            PlusPlusResponse with operation result
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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_event" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute get_event")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all users

        HTTP GET /users

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> PlusPlusResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user ID

        Returns:
            PlusPlusResponse with operation result
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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Tracks
    # -----------------------------------------------------------------------

    async def list_tracks(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all tracks

        HTTP GET /tracks

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/tracks"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tracks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_tracks")

    async def get_track(
        self,
        track_id: str
    ) -> PlusPlusResponse:
        """Get a specific track by ID

        HTTP GET /tracks/{track_id}

        Args:
            track_id: The track ID

        Returns:
            PlusPlusResponse with operation result
        """
        url = self.base_url + "/tracks/{track_id}".format(track_id=track_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_track" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute get_track")

    # -----------------------------------------------------------------------
    # Channels
    # -----------------------------------------------------------------------

    async def list_channels(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all channels

        HTTP GET /channels

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/channels"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_channels" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_channels")

    async def get_channel(
        self,
        channel_id: str
    ) -> PlusPlusResponse:
        """Get a specific channel by ID

        HTTP GET /channels/{channel_id}

        Args:
            channel_id: The channel ID

        Returns:
            PlusPlusResponse with operation result
        """
        url = self.base_url + "/channels/{channel_id}".format(channel_id=channel_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_channel" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute get_channel")

    # -----------------------------------------------------------------------
    # Content
    # -----------------------------------------------------------------------

    async def list_content(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all content

        HTTP GET /content

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_content")

    async def get_content(
        self,
        content_id: str
    ) -> PlusPlusResponse:
        """Get a specific content item by ID

        HTTP GET /content/{content_id}

        Args:
            content_id: The content ID

        Returns:
            PlusPlusResponse with operation result
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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute get_content")

    # -----------------------------------------------------------------------
    # Tags
    # -----------------------------------------------------------------------

    async def list_tags(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all tags

        HTTP GET /tags

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/tags"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tags" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_tags")

    # -----------------------------------------------------------------------
    # Enrollments
    # -----------------------------------------------------------------------

    async def list_enrollments(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> PlusPlusResponse:
        """List all enrollments

        HTTP GET /enrollments

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            PlusPlusResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/enrollments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_enrollments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute list_enrollments")
