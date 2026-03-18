# ruff: noqa
"""
Loopio REST API DataSource - Auto-generated API wrapper

Generated from Loopio REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.loopio.loopio import LoopioClient, LoopioResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class LoopioDataSource:
    """Loopio REST API DataSource

    Provides async wrapper methods for Loopio REST API operations:
    - Projects management
    - Entries management
    - Library management
    - Categories management
    - Users management
    - Groups management
    - Tags management

    The base URL is https://api.loopio.com/v1.

    All methods return LoopioResponse objects.
    """

    def __init__(self, client: LoopioClient) -> None:
        """Initialize with LoopioClient.

        Args:
            client: LoopioClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'LoopioDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> LoopioClient:
        """Return the underlying LoopioClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Projects
    # -----------------------------------------------------------------------

    async def get_projects(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all projects.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/projects"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_projects")

    async def get_project(
        self,
        project_id: str,
    ) -> LoopioResponse:
        """Get a specific project by ID.

        Args:
            project_id: The project ID

        Returns:
            LoopioResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_project" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_project")

    # -----------------------------------------------------------------------
    # Entries
    # -----------------------------------------------------------------------

    async def get_entries(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all entries.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/entries"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_entries" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_entries")

    async def get_entry(
        self,
        entry_id: str,
    ) -> LoopioResponse:
        """Get a specific entry by ID.

        Args:
            entry_id: The entry ID

        Returns:
            LoopioResponse with operation result
        """
        url = self.base_url + "/entries/{entry_id}".format(entry_id=entry_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_entry")

    # -----------------------------------------------------------------------
    # Library
    # -----------------------------------------------------------------------

    async def get_library_items(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all library items.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/library"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_library_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_library_items")

    async def get_library_item(
        self,
        library_id: str,
    ) -> LoopioResponse:
        """Get a specific library item by ID.

        Args:
            library_id: The library item ID

        Returns:
            LoopioResponse with operation result
        """
        url = self.base_url + "/library/{library_id}".format(library_id=library_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_library_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_library_item")

    # -----------------------------------------------------------------------
    # Categories
    # -----------------------------------------------------------------------

    async def get_categories(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all categories.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_categories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_categories")

    async def get_category(
        self,
        category_id: str,
    ) -> LoopioResponse:
        """Get a specific category by ID.

        Args:
            category_id: The category ID

        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_category" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_category")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all users.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_users")

    async def get_user(
        self,
        user_id: str,
    ) -> LoopioResponse:
        """Get a specific user by ID.

        Args:
            user_id: The user ID

        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def get_groups(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all groups.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_groups")

    async def get_group(
        self,
        group_id: str,
    ) -> LoopioResponse:
        """Get a specific group by ID.

        Args:
            group_id: The group ID

        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_group")

    # -----------------------------------------------------------------------
    # Tags
    # -----------------------------------------------------------------------

    async def get_tags(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> LoopioResponse:
        """List all tags.

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip

        Returns:
            LoopioResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_tags" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_tags")

    async def get_tag(
        self,
        tag_id: str,
    ) -> LoopioResponse:
        """Get a specific tag by ID.

        Args:
            tag_id: The tag ID

        Returns:
            LoopioResponse with operation result
        """
        url = self.base_url + "/tags/{tag_id}".format(tag_id=tag_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_tag" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute get_tag")
