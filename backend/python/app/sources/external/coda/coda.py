"""
Coda REST API DataSource - Auto-generated API wrapper

Generated from Coda REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.coda.coda import CodaClient, CodaResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class CodaDataSource:
    """Coda REST API DataSource

    Provides async wrapper methods for Coda REST API operations:
    - User / Account information
    - Doc CRUD and management
    - Table and Row operations
    - Column management
    - Page operations
    - Formula and Control access
    - Permission management
    - Category listing

    The base URL is determined by the CodaClient's configured base URL
    (default: https://coda.io/apis/v1).

    All methods return CodaResponse objects.
    """

    def __init__(self, client: CodaClient) -> None:
        """Initialize with CodaClient.

        Args:
            client: CodaClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'CodaDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> CodaClient:
        """Return the underlying CodaClient."""
        return self._client

    async def whoami(
        self
    ) -> CodaResponse:
        """Get information about the current user

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/whoami"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed whoami" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute whoami")

    async def list_docs(
        self,
        *,
        is_owner: bool | None = None,
        query: str | None = None,
        source_doc: str | None = None,
        is_starred: bool | None = None,
        in_gallery: bool | None = None,
        workspace_id: str | None = None,
        folder_id: str | None = None,
        limit: int | None = None,
        page_token: str | None = None
    ) -> CodaResponse:
        """List available Coda docs

        Args:
            is_owner: Show only docs owned by the user
            query: Search term to filter docs
            source_doc: Show only docs copied from the specified source doc
            is_starred: Show only starred docs
            in_gallery: Show only docs in the gallery
            workspace_id: Show only docs in the given workspace
            folder_id: Show only docs in the given folder
            limit: Maximum number of results to return
            page_token: An opaque token for pagination

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if is_owner is not None:
            query_params['is_owner'] = str(is_owner).lower()
        if query is not None:
            query_params['query'] = query
        if source_doc is not None:
            query_params['source_doc'] = source_doc
        if is_starred is not None:
            query_params['is_starred'] = str(is_starred).lower()
        if in_gallery is not None:
            query_params['in_gallery'] = str(in_gallery).lower()
        if workspace_id is not None:
            query_params['workspace_id'] = workspace_id
        if folder_id is not None:
            query_params['folder_id'] = folder_id
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token

        url = self.base_url + "/docs"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_docs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_docs")

    async def get_doc(
        self,
        doc_id: str
    ) -> CodaResponse:
        """Get info about a specific doc

        Args:
            doc_id: The ID of the doc

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_doc" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_doc")

    async def create_doc(
        self,
        title: str | None = None,
        source_doc: str | None = None,
        timezone: str | None = None,
        folder_id: str | None = None
    ) -> CodaResponse:
        """Create a new Coda doc

        Args:
            title: Title of the new doc
            source_doc: ID of a doc to copy
            timezone: Timezone for the doc
            folder_id: ID of the folder to create the doc in

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs"

        body: dict[str, Any] = {}
        if title is not None:
            body['title'] = title
        if source_doc is not None:
            body['source_doc'] = source_doc
        if timezone is not None:
            body['timezone'] = timezone
        if folder_id is not None:
            body['folder_id'] = folder_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_doc" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute create_doc")

    async def delete_doc(
        self,
        doc_id: str
    ) -> CodaResponse:
        """Delete a doc

        Args:
            doc_id: The ID of the doc to delete

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_doc" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute delete_doc")

    async def list_tables(
        self,
        doc_id: str,
        limit: int | None = None,
        page_token: str | None = None,
        sort_by: str | None = None,
        table_types: str | None = None
    ) -> CodaResponse:
        """List tables in a doc

        Args:
            doc_id: The ID of the doc
            limit: Maximum number of results to return
            page_token: An opaque token for pagination
            sort_by: Sort order of the results
            table_types: Comma-separated list of table types to include

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if table_types is not None:
            query_params['table_types'] = table_types

        url = self.base_url + "/docs/{doc_id}/tables".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tables" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_tables")

    async def get_table(
        self,
        doc_id: str,
        table_id_or_name: str
    ) -> CodaResponse:
        """Get info about a specific table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}".format(doc_id=doc_id, table_id_or_name=table_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_table" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_table")

    async def list_rows(
        self,
        doc_id: str,
        table_id_or_name: str,
        *,
        limit: int | None = None,
        page_token: str | None = None,
        query: str | None = None,
        sort_by: str | None = None,
        use_column_names: bool | None = None,
        value_format: str | None = None,
        visible_only: bool | None = None
    ) -> CodaResponse:
        """List rows in a table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            limit: Maximum number of results to return
            page_token: An opaque token for pagination
            query: Search query to filter rows
            sort_by: Sort order of the results
            use_column_names: Use column names instead of column IDs in the response
            value_format: Format of cell values (simple, simpleWithArrays, rich)
            visible_only: Show only visible rows

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token
        if query is not None:
            query_params['query'] = query
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if use_column_names is not None:
            query_params['use_column_names'] = str(use_column_names).lower()
        if value_format is not None:
            query_params['value_format'] = value_format
        if visible_only is not None:
            query_params['visible_only'] = str(visible_only).lower()

        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/rows".format(doc_id=doc_id, table_id_or_name=table_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_rows" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_rows")

    async def get_row(
        self,
        doc_id: str,
        table_id_or_name: str,
        row_id_or_name: str,
        *,
        use_column_names: bool | None = None,
        value_format: str | None = None
    ) -> CodaResponse:
        """Get a specific row in a table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            row_id_or_name: The ID or name of the row
            use_column_names: Use column names instead of column IDs
            value_format: Format of cell values

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if use_column_names is not None:
            query_params['use_column_names'] = str(use_column_names).lower()
        if value_format is not None:
            query_params['value_format'] = value_format

        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}".format(doc_id=doc_id, table_id_or_name=table_id_or_name, row_id_or_name=row_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_row" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_row")

    async def insert_rows(
        self,
        doc_id: str,
        table_id_or_name: str,
        rows: list[dict[str, Any]],
        key_columns: list[str] | None = None
    ) -> CodaResponse:
        """Insert or upsert rows in a table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            rows: Array of row objects to insert
            key_columns: Optional column IDs for upsert key matching

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/rows".format(doc_id=doc_id, table_id_or_name=table_id_or_name)

        body: dict[str, Any] = {}
        body['rows'] = rows
        if key_columns is not None:
            body['key_columns'] = key_columns

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed insert_rows" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute insert_rows")

    async def update_row(
        self,
        doc_id: str,
        table_id_or_name: str,
        row_id_or_name: str,
        row: dict[str, Any]
    ) -> CodaResponse:
        """Update a specific row in a table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            row_id_or_name: The ID or name of the row
            row: Row object with cells to update

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}".format(doc_id=doc_id, table_id_or_name=table_id_or_name, row_id_or_name=row_id_or_name)

        body: dict[str, Any] = {}
        body['row'] = row

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_row" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute update_row")

    async def delete_row(
        self,
        doc_id: str,
        table_id_or_name: str,
        row_id_or_name: str
    ) -> CodaResponse:
        """Delete a specific row from a table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            row_id_or_name: The ID or name of the row to delete

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}".format(doc_id=doc_id, table_id_or_name=table_id_or_name, row_id_or_name=row_id_or_name)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_row" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute delete_row")

    async def list_columns(
        self,
        doc_id: str,
        table_id_or_name: str,
        *,
        limit: int | None = None,
        page_token: str | None = None,
        visible_only: bool | None = None
    ) -> CodaResponse:
        """List columns in a table

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            limit: Maximum number of results to return
            page_token: An opaque token for pagination
            visible_only: Show only visible columns

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token
        if visible_only is not None:
            query_params['visible_only'] = str(visible_only).lower()

        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/columns".format(doc_id=doc_id, table_id_or_name=table_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_columns" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_columns")

    async def get_column(
        self,
        doc_id: str,
        table_id_or_name: str,
        column_id_or_name: str
    ) -> CodaResponse:
        """Get info about a specific column

        Args:
            doc_id: The ID of the doc
            table_id_or_name: The ID or name of the table
            column_id_or_name: The ID or name of the column

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/tables/{table_id_or_name}/columns/{column_id_or_name}".format(doc_id=doc_id, table_id_or_name=table_id_or_name, column_id_or_name=column_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_column" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_column")

    async def list_pages(
        self,
        doc_id: str,
        limit: int | None = None,
        page_token: str | None = None
    ) -> CodaResponse:
        """List pages in a doc

        Args:
            doc_id: The ID of the doc
            limit: Maximum number of results to return
            page_token: An opaque token for pagination

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token

        url = self.base_url + "/docs/{doc_id}/pages".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_pages")

    async def get_page(
        self,
        doc_id: str,
        page_id_or_name: str
    ) -> CodaResponse:
        """Get info about a specific page

        Args:
            doc_id: The ID of the doc
            page_id_or_name: The ID or name of the page

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/pages/{page_id_or_name}".format(doc_id=doc_id, page_id_or_name=page_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_page")

    async def update_page(
        self,
        doc_id: str,
        page_id_or_name: str,
        name: str | None = None,
        subtitle: str | None = None,
        icon_name: str | None = None,
        image_url: str | None = None
    ) -> CodaResponse:
        """Update a page in a doc

        Args:
            doc_id: The ID of the doc
            page_id_or_name: The ID or name of the page
            name: New name for the page
            subtitle: New subtitle for the page
            icon_name: Name of the icon for the page
            image_url: URL of the cover image for the page

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/pages/{page_id_or_name}".format(doc_id=doc_id, page_id_or_name=page_id_or_name)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if subtitle is not None:
            body['subtitle'] = subtitle
        if icon_name is not None:
            body['icon_name'] = icon_name
        if image_url is not None:
            body['image_url'] = image_url

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute update_page")

    async def list_formulas(
        self,
        doc_id: str,
        limit: int | None = None,
        page_token: str | None = None,
        sort_by: str | None = None
    ) -> CodaResponse:
        """List named formulas in a doc

        Args:
            doc_id: The ID of the doc
            limit: Maximum number of results to return
            page_token: An opaque token for pagination
            sort_by: Sort order of the results

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token
        if sort_by is not None:
            query_params['sort_by'] = sort_by

        url = self.base_url + "/docs/{doc_id}/formulas".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_formulas" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_formulas")

    async def get_formula(
        self,
        doc_id: str,
        formula_id_or_name: str
    ) -> CodaResponse:
        """Get info about a specific formula

        Args:
            doc_id: The ID of the doc
            formula_id_or_name: The ID or name of the formula

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/formulas/{formula_id_or_name}".format(doc_id=doc_id, formula_id_or_name=formula_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_formula" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_formula")

    async def list_controls(
        self,
        doc_id: str,
        limit: int | None = None,
        page_token: str | None = None,
        sort_by: str | None = None
    ) -> CodaResponse:
        """List controls in a doc

        Args:
            doc_id: The ID of the doc
            limit: Maximum number of results to return
            page_token: An opaque token for pagination
            sort_by: Sort order of the results

        Returns:
            CodaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if page_token is not None:
            query_params['page_token'] = page_token
        if sort_by is not None:
            query_params['sort_by'] = sort_by

        url = self.base_url + "/docs/{doc_id}/controls".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_controls" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_controls")

    async def get_control(
        self,
        doc_id: str,
        control_id_or_name: str
    ) -> CodaResponse:
        """Get info about a specific control

        Args:
            doc_id: The ID of the doc
            control_id_or_name: The ID or name of the control

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/controls/{control_id_or_name}".format(doc_id=doc_id, control_id_or_name=control_id_or_name)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_control" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute get_control")

    async def list_permissions(
        self,
        doc_id: str
    ) -> CodaResponse:
        """List permissions for a doc

        Args:
            doc_id: The ID of the doc

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/docs/{doc_id}/acl/permissions".format(doc_id=doc_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_permissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_permissions")

    async def list_categories(
        self
    ) -> CodaResponse:
        """List available doc categories

        Returns:
            CodaResponse with operation result
        """
        url = self.base_url + "/categories"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CodaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_categories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CodaResponse(success=False, error=str(e), message="Failed to execute list_categories")
