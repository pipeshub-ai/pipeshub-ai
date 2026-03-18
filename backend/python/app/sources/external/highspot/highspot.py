# ruff: noqa
"""
Highspot REST API DataSource - Auto-generated API wrapper

Generated from Highspot REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.highspot.highspot import HighspotClient, HighspotResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class HighspotDataSource:
    """Highspot REST API DataSource

    Provides async wrapper methods for Highspot REST API operations:
    - Spots management
    - Items management
    - Pitches management
    - Groups management
    - Users management
    - Analytics (content and engagement)

    All methods return HighspotResponse objects.
    """

    def __init__(self, client: HighspotClient) -> None:
        """Initialize with HighspotClient.

        Args:
            client: HighspotClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HighspotDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> HighspotClient:
        """Return the underlying HighspotClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Spots
    # -----------------------------------------------------------------------

    async def list_spots(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> HighspotResponse:
        """List all spots

        HTTP GET /spots

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            HighspotResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/spots"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_spots" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute list_spots")

    async def get_spot(
        self,
        spot_id: str
    ) -> HighspotResponse:
        """Get a specific spot by ID

        HTTP GET /spots/{spot_id}

        Args:
            spot_id: The spot ID

        Returns:
            HighspotResponse with operation result
        """
        url = self.base_url + "/spots/{spot_id}".format(spot_id=spot_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_spot" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_spot")

    async def get_spot_items(
        self,
        spot_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> HighspotResponse:
        """Get items in a specific spot

        HTTP GET /spots/{spot_id}/items

        Args:
            spot_id: The spot ID
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            HighspotResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/spots/{spot_id}/items".format(spot_id=spot_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_spot_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_spot_items")

    # -----------------------------------------------------------------------
    # Items
    # -----------------------------------------------------------------------

    async def list_items(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> HighspotResponse:
        """List all items

        HTTP GET /items

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            HighspotResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/items"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute list_items")

    async def get_item(
        self,
        item_id: str
    ) -> HighspotResponse:
        """Get a specific item by ID

        HTTP GET /items/{item_id}

        Args:
            item_id: The item ID

        Returns:
            HighspotResponse with operation result
        """
        url = self.base_url + "/items/{item_id}".format(item_id=item_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_item")

    # -----------------------------------------------------------------------
    # Pitches
    # -----------------------------------------------------------------------

    async def list_pitches(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> HighspotResponse:
        """List all pitches

        HTTP GET /pitches

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            HighspotResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/pitches"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pitches" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute list_pitches")

    async def get_pitch(
        self,
        pitch_id: str
    ) -> HighspotResponse:
        """Get a specific pitch by ID

        HTTP GET /pitches/{pitch_id}

        Args:
            pitch_id: The pitch ID

        Returns:
            HighspotResponse with operation result
        """
        url = self.base_url + "/pitches/{pitch_id}".format(pitch_id=pitch_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_pitch" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_pitch")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def list_groups(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> HighspotResponse:
        """List all groups

        HTTP GET /groups

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            HighspotResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

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
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute list_groups")

    async def get_group(
        self,
        group_id: str
    ) -> HighspotResponse:
        """Get a specific group by ID

        HTTP GET /groups/{group_id}

        Args:
            group_id: The group ID

        Returns:
            HighspotResponse with operation result
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
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_group")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> HighspotResponse:
        """List all users

        HTTP GET /users

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            HighspotResponse with operation result
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
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> HighspotResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user ID

        Returns:
            HighspotResponse with operation result
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
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Analytics
    # -----------------------------------------------------------------------

    async def get_content_analytics(
        self
    ) -> HighspotResponse:
        """Get content analytics

        HTTP GET /analytics/content

        Returns:
            HighspotResponse with operation result
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
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_content_analytics")

    async def get_engagement_analytics(
        self
    ) -> HighspotResponse:
        """Get engagement analytics

        HTTP GET /analytics/engagement

        Returns:
            HighspotResponse with operation result
        """
        url = self.base_url + "/analytics/engagement"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_engagement_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute get_engagement_analytics")
