# ruff: noqa
"""
Haystack REST API DataSource - Auto-generated API wrapper

Generated from Haystack REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.haystack.haystack import HaystackClient, HaystackResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class HaystackDataSource:
    """Haystack REST API DataSource

    Provides async wrapper methods for Haystack REST API operations:
    - People management
    - Teams management
    - Locations management
    - Departments management
    - Announcements management
    - Pages management
    - Search

    The base URL is https://api.haystackapp.io/v1.

    All methods return HaystackResponse objects.
    """

    def __init__(self, client: HaystackClient) -> None:
        """Initialize with HaystackClient.

        Args:
            client: HaystackClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HaystackDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> HaystackClient:
        """Return the underlying HaystackClient."""
        return self._client

    # -----------------------------------------------------------------------
    # People
    # -----------------------------------------------------------------------

    async def get_people(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> HaystackResponse:
        """List all people.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            HaystackResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/people"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_people" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_people")

    async def get_person(
        self,
        person_id: str,
    ) -> HaystackResponse:
        """Get a specific person by ID.

        Args:
            person_id: The person ID

        Returns:
            HaystackResponse with operation result
        """
        url = self.base_url + "/people/{person_id}".format(person_id=person_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_person" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_person")

    # -----------------------------------------------------------------------
    # Teams
    # -----------------------------------------------------------------------

    async def get_teams(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> HaystackResponse:
        """List all teams.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            HaystackResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/teams"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_teams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_teams")

    async def get_team(
        self,
        team_id: str,
    ) -> HaystackResponse:
        """Get a specific team by ID.

        Args:
            team_id: The team ID

        Returns:
            HaystackResponse with operation result
        """
        url = self.base_url + "/teams/{team_id}".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_team" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_team")

    # -----------------------------------------------------------------------
    # Locations
    # -----------------------------------------------------------------------

    async def get_locations(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> HaystackResponse:
        """List all locations.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            HaystackResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/locations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_locations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_locations")

    async def get_location(
        self,
        location_id: str,
    ) -> HaystackResponse:
        """Get a specific location by ID.

        Args:
            location_id: The location ID

        Returns:
            HaystackResponse with operation result
        """
        url = self.base_url + "/locations/{location_id}".format(location_id=location_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_location" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_location")

    # -----------------------------------------------------------------------
    # Departments
    # -----------------------------------------------------------------------

    async def get_departments(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> HaystackResponse:
        """List all departments.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            HaystackResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/departments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_departments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_departments")

    async def get_department(
        self,
        department_id: str,
    ) -> HaystackResponse:
        """Get a specific department by ID.

        Args:
            department_id: The department ID

        Returns:
            HaystackResponse with operation result
        """
        url = self.base_url + "/departments/{department_id}".format(department_id=department_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_department" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_department")

    # -----------------------------------------------------------------------
    # Announcements
    # -----------------------------------------------------------------------

    async def get_announcements(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> HaystackResponse:
        """List all announcements.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            HaystackResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/announcements"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_announcements" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_announcements")

    async def get_announcement(
        self,
        announcement_id: str,
    ) -> HaystackResponse:
        """Get a specific announcement by ID.

        Args:
            announcement_id: The announcement ID

        Returns:
            HaystackResponse with operation result
        """
        url = self.base_url + "/announcements/{announcement_id}".format(announcement_id=announcement_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_announcement" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_announcement")

    # -----------------------------------------------------------------------
    # Pages
    # -----------------------------------------------------------------------

    async def get_pages(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> HaystackResponse:
        """List all pages.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            HaystackResponse with operation result
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
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_pages")

    async def get_page(
        self,
        page_id: str,
    ) -> HaystackResponse:
        """Get a specific page by ID.

        Args:
            page_id: The page ID

        Returns:
            HaystackResponse with operation result
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
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute get_page")

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self,
        q: str,
        *,
        type: str | None = None,
        limit: int | None = None,
    ) -> HaystackResponse:
        """Search across Haystack content.

        Args:
            q: Search query string
            type: Filter by content type
            limit: Maximum number of results to return

        Returns:
            HaystackResponse with operation result
        """
        query_params: dict[str, Any] = {'q': q}
        if type is not None:
            query_params['type'] = type
        if limit is not None:
            query_params['limit'] = str(limit)

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
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute search")
