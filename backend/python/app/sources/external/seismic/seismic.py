# ruff: noqa
"""
Seismic REST API DataSource - Auto-generated API wrapper

Generated from Seismic REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.seismic.seismic import SeismicClient, SeismicResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class SeismicDataSource:
    """Seismic REST API DataSource

    Provides async wrapper methods for Seismic REST API operations:
    - Library content management
    - Library folders management
    - Teamsites management
    - Users management
    - Workspace documents management
    - LiveSend links
    - Analytics

    All methods return SeismicResponse objects.
    """

    def __init__(self, client: SeismicClient) -> None:
        """Initialize with SeismicClient.

        Args:
            client: SeismicClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'SeismicDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> SeismicClient:
        """Return the underlying SeismicClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Library Content
    # -----------------------------------------------------------------------

    async def list_library_content(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """List all library content

        HTTP GET /library/content

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/library/content"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_library_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute list_library_content")

    async def get_library_content(
        self,
        content_id: str
    ) -> SeismicResponse:
        """Get a specific library content item by ID

        HTTP GET /library/content/{content_id}

        Args:
            content_id: The content ID

        Returns:
            SeismicResponse with operation result
        """
        url = self.base_url + "/library/content/{content_id}".format(content_id=content_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_library_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_library_content")

    # -----------------------------------------------------------------------
    # Library Folders
    # -----------------------------------------------------------------------

    async def list_library_folders(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """List all library folders

        HTTP GET /library/folders

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/library/folders"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_library_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute list_library_folders")

    async def get_library_folder(
        self,
        folder_id: str
    ) -> SeismicResponse:
        """Get a specific library folder by ID

        HTTP GET /library/folders/{folder_id}

        Args:
            folder_id: The folder ID

        Returns:
            SeismicResponse with operation result
        """
        url = self.base_url + "/library/folders/{folder_id}".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_library_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_library_folder")

    # -----------------------------------------------------------------------
    # Teamsites
    # -----------------------------------------------------------------------

    async def list_teamsites(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """List all teamsites

        HTTP GET /teamsites

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/teamsites"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_teamsites" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute list_teamsites")

    async def get_teamsite(
        self,
        teamsite_id: str
    ) -> SeismicResponse:
        """Get a specific teamsite by ID

        HTTP GET /teamsites/{teamsite_id}

        Args:
            teamsite_id: The teamsite ID

        Returns:
            SeismicResponse with operation result
        """
        url = self.base_url + "/teamsites/{teamsite_id}".format(teamsite_id=teamsite_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_teamsite" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_teamsite")

    async def get_teamsite_content(
        self,
        teamsite_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """Get content in a specific teamsite

        HTTP GET /teamsites/{teamsite_id}/content

        Args:
            teamsite_id: The teamsite ID
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/teamsites/{teamsite_id}/content".format(teamsite_id=teamsite_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_teamsite_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_teamsite_content")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """List all users

        HTTP GET /users

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
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
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> SeismicResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user ID

        Returns:
            SeismicResponse with operation result
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
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Workspace Documents
    # -----------------------------------------------------------------------

    async def list_workspace_documents(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """List all workspace documents

        HTTP GET /workspace/documents

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/workspace/documents"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_workspace_documents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute list_workspace_documents")

    async def get_workspace_document(
        self,
        document_id: str
    ) -> SeismicResponse:
        """Get a specific workspace document by ID

        HTTP GET /workspace/documents/{document_id}

        Args:
            document_id: The document ID

        Returns:
            SeismicResponse with operation result
        """
        url = self.base_url + "/workspace/documents/{document_id}".format(document_id=document_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_workspace_document" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_workspace_document")

    # -----------------------------------------------------------------------
    # LiveSend Links
    # -----------------------------------------------------------------------

    async def list_livesend_links(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> SeismicResponse:
        """List all LiveSend links

        HTTP GET /livesend/links

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            SeismicResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/livesend/links"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_livesend_links" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute list_livesend_links")

    # -----------------------------------------------------------------------
    # Analytics
    # -----------------------------------------------------------------------

    async def get_content_analytics(
        self
    ) -> SeismicResponse:
        """Get content analytics

        HTTP GET /analytics/content

        Returns:
            SeismicResponse with operation result
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
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute get_content_analytics")
