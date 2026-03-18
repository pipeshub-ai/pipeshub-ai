"""
Lucid REST API DataSource - Auto-generated API wrapper

Generated from Lucid REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.lucid.lucid import LucidClient, LucidResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class LucidDataSource:
    """Lucid REST API DataSource

    Provides async wrapper methods for Lucid REST API operations:
    - User profile management
    - Document CRUD operations
    - Folder management
    - Page listing
    - Data source operations

    The base URL is https://api.lucid.co/v1.

    All methods return LucidResponse objects.
    """

    def __init__(self, client: LucidClient) -> None:
        """Initialize with LucidClient.

        Args:
            client: LucidClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'LucidDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> LucidClient:
        """Return the underlying LucidClient."""
        return self._client

    async def get_current_user(
        self
    ) -> LucidResponse:
        """Get the current authenticated user details (API v1)

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/users/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_users(
        self,
        pageSize: int | None = None,
        cursor: str | None = None
    ) -> LucidResponse:
        """List users in the account (API v1)

        Args:
            pageSize: Number of results per page
            cursor: Cursor for pagination

        Returns:
            LucidResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if pageSize is not None:
            query_params['pageSize'] = str(pageSize)
        if cursor is not None:
            query_params['cursor'] = cursor

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
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def list_documents(
        self,
        pageSize: int | None = None,
        cursor: str | None = None,
        product: str | None = None
    ) -> LucidResponse:
        """List all documents accessible to the authenticated user (API v1)

        Args:
            pageSize: Number of results per page
            cursor: Cursor for pagination
            product: Filter by product (e.g., lucidchart, lucidspark)

        Returns:
            LucidResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if pageSize is not None:
            query_params['pageSize'] = str(pageSize)
        if cursor is not None:
            query_params['cursor'] = cursor
        if product is not None:
            query_params['product'] = product

        url = self.base_url + "/documents"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_documents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute list_documents")

    async def get_document(
        self,
        documentId: str
    ) -> LucidResponse:
        """Get a specific document by ID (API v1)

        Args:
            documentId: The document ID

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/documents/{documentId}".format(documentId=documentId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_document" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute get_document")

    async def create_document(
        self,
        title: str | None = None,
        product: str | None = None,
        folderId: str | None = None
    ) -> LucidResponse:
        """Create a new document (API v1)

        Args:
            title: The title of the document
            product: The product type (e.g., lucidchart, lucidspark)
            folderId: The folder ID to create the document in

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/documents"

        body: dict[str, Any] = {}
        if title is not None:
            body['title'] = title
        if product is not None:
            body['product'] = product
        if folderId is not None:
            body['folderId'] = folderId

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_document" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute create_document")

    async def delete_document(
        self,
        documentId: str
    ) -> LucidResponse:
        """Delete a document by ID (API v1)

        Args:
            documentId: The document ID to delete

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/documents/{documentId}".format(documentId=documentId)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_document" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute delete_document")

    async def list_folders(
        self,
        pageSize: int | None = None,
        cursor: str | None = None
    ) -> LucidResponse:
        """List all folders accessible to the authenticated user (API v1)

        Args:
            pageSize: Number of results per page
            cursor: Cursor for pagination

        Returns:
            LucidResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if pageSize is not None:
            query_params['pageSize'] = str(pageSize)
        if cursor is not None:
            query_params['cursor'] = cursor

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
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute list_folders")

    async def get_folder(
        self,
        folderId: str
    ) -> LucidResponse:
        """Get a specific folder by ID (API v1)

        Args:
            folderId: The folder ID

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/folders/{folderId}".format(folderId=folderId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute get_folder")

    async def list_folder_documents(
        self,
        folderId: str,
        pageSize: int | None = None,
        cursor: str | None = None
    ) -> LucidResponse:
        """List documents in a specific folder (API v1)

        Args:
            folderId: The folder ID
            pageSize: Number of results per page
            cursor: Cursor for pagination

        Returns:
            LucidResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if pageSize is not None:
            query_params['pageSize'] = str(pageSize)
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/folders/{folderId}/documents".format(folderId=folderId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_folder_documents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute list_folder_documents")

    async def create_folder(
        self,
        name: str,
        parentFolderId: str | None = None
    ) -> LucidResponse:
        """Create a new folder (API v1)

        Args:
            name: The name of the folder
            parentFolderId: The parent folder ID

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/folders"

        body: dict[str, Any] = {}
        body['name'] = name
        if parentFolderId is not None:
            body['parentFolderId'] = parentFolderId

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute create_folder")

    async def list_pages(
        self,
        documentId: str
    ) -> LucidResponse:
        """List all pages in a document (API v1)

        Args:
            documentId: The document ID

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/pages/{documentId}".format(documentId=documentId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute list_pages")

    async def list_data_sources(
        self
    ) -> LucidResponse:
        """List all data sources (API v1)

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/data-sources"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_data_sources" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute list_data_sources")

    async def get_data_source_by_id(
        self,
        dataSourceId: str
    ) -> LucidResponse:
        """Get a specific data source by ID (API v1)

        Args:
            dataSourceId: The data source ID

        Returns:
            LucidResponse with operation result
        """
        url = self.base_url + "/data-sources/{dataSourceId}".format(dataSourceId=dataSourceId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LucidResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_data_source_by_id" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LucidResponse(success=False, error=str(e), message="Failed to execute get_data_source_by_id")
