# ruff: noqa
"""
Panopto REST API DataSource - Auto-generated API wrapper

Generated from Panopto REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.panopto.panopto import PanoptoClient, PanoptoResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PanoptoDataSource:
    """Panopto REST API DataSource

    Provides async wrapper methods for Panopto REST API operations:
    - Sessions (recordings) management
    - Session viewers
    - Folders management
    - Folder sessions
    - Users management
    - Groups management
    - Search
    - View statistics

    The base URL is domain-specific and determined by the PanoptoClient
    configuration. Create a client with the desired domain and pass it here.

    All methods return PanoptoResponse objects.
    """

    def __init__(self, client: PanoptoClient) -> None:
        """Initialize with PanoptoClient.

        Args:
            client: PanoptoClient instance with configured authentication and domain
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PanoptoDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PanoptoClient:
        """Return the underlying PanoptoClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Sessions (Recordings)
    # -----------------------------------------------------------------------

    async def get_sessions(
        self,
        *,
        folder_id: str | None = None,
        search_query: str | None = None,
        sort_field: str | None = None,
        sort_order: str | None = None,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get all sessions (recordings)

        Args:
            folder_id: Filter by folder ID
            search_query: Search query string
            sort_field: Field to sort by
            sort_order: Sort order (Asc or Desc)
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if folder_id is not None:
            query_params['folderId'] = folder_id
        if search_query is not None:
            query_params['searchQuery'] = search_query
        if sort_field is not None:
            query_params['sortField'] = sort_field
        if sort_order is not None:
            query_params['sortOrder'] = sort_order
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

        url = self.base_url + "/sessions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_sessions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_sessions")

    async def get_session(
        self,
        session_id: str,
    ) -> PanoptoResponse:
        """Get a specific session by ID

        Args:
            session_id: The session (recording) ID

        Returns:
            PanoptoResponse with operation result
        """
        url = self.base_url + "/sessions/{session_id}".format(session_id=session_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_session" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_session")

    async def get_session_viewers(
        self,
        session_id: str,
        *,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get viewers of a specific session

        Args:
            session_id: The session (recording) ID
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

        url = self.base_url + "/sessions/{session_id}/viewers".format(session_id=session_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_session_viewers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_session_viewers")

    # -----------------------------------------------------------------------
    # Folders
    # -----------------------------------------------------------------------

    async def get_folders(
        self,
        *,
        parent_folder_id: str | None = None,
        search_query: str | None = None,
        sort_field: str | None = None,
        sort_order: str | None = None,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get all folders

        Args:
            parent_folder_id: Filter by parent folder ID
            search_query: Search query string
            sort_field: Field to sort by
            sort_order: Sort order (Asc or Desc)
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if parent_folder_id is not None:
            query_params['parentFolderId'] = parent_folder_id
        if search_query is not None:
            query_params['searchQuery'] = search_query
        if sort_field is not None:
            query_params['sortField'] = sort_field
        if sort_order is not None:
            query_params['sortOrder'] = sort_order
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

        url = self.base_url + "/folders"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_folders")

    async def get_folder(
        self,
        folder_id: str,
    ) -> PanoptoResponse:
        """Get a specific folder by ID

        Args:
            folder_id: The folder ID

        Returns:
            PanoptoResponse with operation result
        """
        url = self.base_url + "/folders/{folder_id}".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_folder")

    async def get_folder_sessions(
        self,
        folder_id: str,
        *,
        sort_field: str | None = None,
        sort_order: str | None = None,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get sessions in a specific folder

        Args:
            folder_id: The folder ID
            sort_field: Field to sort by
            sort_order: Sort order (Asc or Desc)
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if sort_field is not None:
            query_params['sortField'] = sort_field
        if sort_order is not None:
            query_params['sortOrder'] = sort_order
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

        url = self.base_url + "/folders/{folder_id}/sessions".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder_sessions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_folder_sessions")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        search_query: str | None = None,
        sort_field: str | None = None,
        sort_order: str | None = None,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get all users

        Args:
            search_query: Search query string
            sort_field: Field to sort by
            sort_order: Sort order (Asc or Desc)
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if search_query is not None:
            query_params['searchQuery'] = search_query
        if sort_field is not None:
            query_params['sortField'] = sort_field
        if sort_order is not None:
            query_params['sortOrder'] = sort_order
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

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
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_users")

    async def get_user(
        self,
        user_id: str,
    ) -> PanoptoResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID

        Returns:
            PanoptoResponse with operation result
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
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def get_groups(
        self,
        *,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get all groups

        Args:
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

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
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_groups")

    async def get_group(
        self,
        group_id: str,
    ) -> PanoptoResponse:
        """Get a specific group by ID

        Args:
            group_id: The group ID

        Returns:
            PanoptoResponse with operation result
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
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_group")

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self,
        *,
        query: str,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Search for sessions and folders

        Args:
            query: Search query string (required)
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['query'] = query
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

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
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute search")

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    async def get_view_stats(
        self,
        *,
        session_id: str,
        page_number: int | None = None,
        max_number_results: int | None = None,
    ) -> PanoptoResponse:
        """Get view statistics for a session

        Args:
            session_id: The session ID to get stats for (required)
            page_number: Page number for pagination
            max_number_results: Maximum number of results per page

        Returns:
            PanoptoResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['sessionId'] = session_id
        if page_number is not None:
            query_params['pageNumber'] = str(page_number)
        if max_number_results is not None:
            query_params['maxNumberResults'] = str(max_number_results)

        url = self.base_url + "/stats/views"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_view_stats" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute get_view_stats")
