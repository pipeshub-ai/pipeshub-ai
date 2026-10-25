from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.notion.notion import NotionClient, NotionResponse


class NotionDataSource:
    """Auto-generated Notion API client wrapper.
    Provides async methods for all Notion API endpoints:
    - Authentication (retrieve bot user)
    - Blocks (retrieve, update, delete, children, append)
    - Pages (create, retrieve, update properties, property items)
    - Databases (create, query, retrieve, update)
    - Data Sources (create, update, retrieve, query)
    - Comments (create, retrieve all, retrieve by ID)
    - File Uploads (create, send, complete, retrieve, list)
    - Search (general search, search by title)
    - Users (list, retrieve, bot user info)
    All methods return NotionResponse objects with standardized success/data/error format.
    """

    def __init__(self, client: NotionClient) -> None:
        """Initialize with NotionClient."""
        self.http_client = client.get_client().get_client()
        if self.http_client is None:
            raise ValueError('HTTP client is not initialized')
        self.base_url = self.http_client.get_base_url()

    def get_data_source(self) -> 'NotionDataSource':
        return self

    # Authentication methods

    async def retrieve_bot_user(self, **kwargs) -> NotionResponse:
        """Retrieve your token's bot user

        HTTP GET /users/me

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/users/me"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Blocks methods

    async def retrieve_block(self, block_id: str, **kwargs) -> NotionResponse:
        """Retrieve a Block object using the ID specified

        HTTP GET /blocks/{block_id}

        Args:
            block_id (str, required): Block ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/blocks/{block_id}".format(block_id=quote(str(block_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_block(self, block_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update the content for the specified block_id based on the block type

        HTTP PATCH /blocks/{block_id}

        Args:
            block_id (str, required): Block ID to update
            request_body (Dict[str, Any], optional): Block object with updated properties

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/blocks/{block_id}".format(block_id=quote(str(block_id), safe=''))
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def delete_block(self, block_id: str, **kwargs) -> NotionResponse:
        """Set a Block object, including page blocks, to archived: true

        HTTP DELETE /blocks/{block_id}

        Args:
            block_id (str, required): Block ID to delete

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/blocks/{block_id}".format(block_id=quote(str(block_id), safe=''))
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_block_children(
        self,
        block_id: str,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
        **kwargs
    ) -> NotionResponse:
        """Return a paginated array of child block objects contained in the block

        HTTP GET /blocks/{block_id}/children

        Args:
            block_id (str, required): Block ID to get children from
            start_cursor (str, optional): Pagination cursor
            page_size (int, optional): Number of items to return (max 100)

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if start_cursor is not None:
            params['start_cursor'] = start_cursor
        if page_size is not None:
            params['page_size'] = page_size
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/blocks/{block_id}/children".format(block_id=quote(str(block_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def append_block_children(self, block_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create and append new children blocks to the parent block_id specified

        HTTP PATCH /blocks/{block_id}/children

        Args:
            block_id (str, required): Block ID to append children to
            request_body (Dict[str, Any], optional): Object containing children array of block objects

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/blocks/{block_id}/children".format(block_id=quote(str(block_id), safe=''))
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Pages methods

    async def create_page(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a new page in the specified database or as a child of an existing page

        HTTP POST /pages

        Args:
            request_body (Dict[str, Any], optional): Page object with parent, properties, and optionally children

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/pages"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_page(self, page_id: str, filter_properties: Optional[List[str]] = None, **kwargs) -> NotionResponse:
        """Retrieve a Page object using the ID specified

        HTTP GET /pages/{page_id}

        Args:
            page_id (str, required): Page ID to retrieve
            filter_properties (List[str], optional): List of property IDs to filter

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if filter_properties is not None:
            params['filter_properties'] = filter_properties
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/pages/{page_id}".format(page_id=quote(str(page_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_page_properties(self, page_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update the properties of a page in a database

        HTTP PATCH /pages/{page_id}

        Args:
            page_id (str, required): Page ID to update
            request_body (Dict[str, Any], optional): Object containing properties to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/pages/{page_id}".format(page_id=quote(str(page_id), safe=''))
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_page_property_item(
        self,
        page_id: str,
        property_id: str,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
        **kwargs
    ) -> NotionResponse:
        """Retrieve a property_item object for a given page_id and property_id

        HTTP GET /pages/{page_id}/properties/{property_id}

        Args:
            page_id (str, required): Page ID
            property_id (str, required): Property ID
            start_cursor (str, optional): Pagination cursor
            page_size (int, optional): Number of items to return (max 100)

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if start_cursor is not None:
            params['start_cursor'] = start_cursor
        if page_size is not None:
            params['page_size'] = page_size
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/pages/{page_id}/properties/{property_id}".format(page_id=quote(str(page_id), safe=''), property_id=quote(str(property_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Databases methods

    async def create_database(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a database as a subpage in the specified parent page

        HTTP POST /databases

        Args:
            request_body (Dict[str, Any], optional): Database object with parent, title, and properties

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def query_database(self, database_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Get a list of Pages contained in the database, filtered and ordered according to the filter conditions and sort criteria provided in the request

        HTTP POST /databases/{database_id}/query

        Args:
            database_id (str, required): Database ID to query
            request_body (Dict[str, Any], optional): Query object with filter, sorts, start_cursor, and page_size

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}/query".format(database_id=quote(str(database_id), safe=''))
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_database(self, database_id: str, **kwargs) -> NotionResponse:
        """Retrieve a Database object using the ID specified

        HTTP GET /databases/{database_id}

        Args:
            database_id (str, required): Database ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}".format(database_id=quote(str(database_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_database(self, database_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update an existing database as specified by the parameters

        HTTP PATCH /databases/{database_id}

        Args:
            database_id (str, required): Database ID to update
            request_body (Dict[str, Any], optional): Database object with properties to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}".format(database_id=quote(str(database_id), safe=''))
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Data Sources methods

    async def create_data_source(self, database_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a new data source for a database

        HTTP POST /databases/{database_id}/data_sources

        Args:
            database_id (str, required): Database ID to create data source for
            request_body (Dict[str, Any], optional): Data source configuration object

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}/data_sources".format(database_id=quote(str(database_id), safe=''))
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_data_source(
        self,
        database_id: str,
        data_source_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Update an existing data source

        HTTP PATCH /data_sources/{data_source_id}

        Args:
            database_id (str, required): Unused; Notion addresses data sources globally
            data_source_id (str, required): Data source ID to update
            request_body (Dict[str, Any], optional): Updated data source configuration

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        # Notion addresses data sources globally, so ``database_id`` is not part of the path.
        url = self.base_url + "/data_sources/{data_source_id}".format(data_source_id=quote(str(data_source_id), safe=''))
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_data_source(self, database_id: str, data_source_id: str, **kwargs) -> NotionResponse:
        """Retrieve a specific data source by ID

        HTTP GET /data_sources/{data_source_id}

        Args:
            database_id (str, required): Unused; Notion addresses data sources globally
            data_source_id (str, required): Data source ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        # Notion addresses data sources globally, so ``database_id`` is not part of the path.
        url = self.base_url + "/data_sources/{data_source_id}".format(data_source_id=quote(str(data_source_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def query_data_source(
        self,
        database_id: str,
        data_source_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Query a data source connected to a database

        HTTP POST /data_sources/{data_source_id}/query

        Args:
            database_id (str, required): Unused; Notion addresses data sources globally
            data_source_id (str, required): Data source ID to query
            request_body (Dict[str, Any], optional): Query parameters for the data source

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        # Notion addresses data sources globally, so ``database_id`` is not part of the path.
        url = self.base_url + "/data_sources/{data_source_id}/query".format(data_source_id=quote(str(data_source_id), safe=''))
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_data_source_by_id(self, data_source_id: str, **kwargs) -> NotionResponse:
        """Retrieve a data source by its ID (without database_id)

        HTTP GET /data_sources/{data_source_id}

        Args:
            data_source_id (str, required): Data source ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/data_sources/{data_source_id}".format(data_source_id=quote(str(data_source_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def query_data_source_by_id(
        self,
        data_source_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Query a data source by its ID to get rows (without database_id)

        HTTP POST /data_sources/{data_source_id}/query

        Args:
            data_source_id (str, required): Data source ID to query
            request_body (Dict[str, Any], optional): Query parameters (filter, sorts, start_cursor, page_size)

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/data_sources/{data_source_id}/query".format(data_source_id=quote(str(data_source_id), safe=''))
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Comments methods

    async def create_comment(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a comment in a page or existing discussion thread

        HTTP POST /comments

        Args:
            request_body (Dict[str, Any], optional): Comment object with parent and rich_text

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/comments"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_comments(
        self,
        block_id: str,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
        **kwargs
    ) -> NotionResponse:
        """Retrieve a list of un-resolved Comment objects from a page or block

        HTTP GET /comments

        Args:
            block_id (str, required): Block or page ID to get comments for
            start_cursor (str, optional): Pagination cursor
            page_size (int, optional): Number of items to return (max 100)

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if block_id is not None:
            params['block_id'] = block_id
        if start_cursor is not None:
            params['start_cursor'] = start_cursor
        if page_size is not None:
            params['page_size'] = page_size
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/comments"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_comment(self, comment_id: str, **kwargs) -> NotionResponse:
        """Retrieve a specific comment by its ID

        HTTP GET /comments/{comment_id}

        Args:
            comment_id (str, required): Comment ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/comments/{comment_id}".format(comment_id=quote(str(comment_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # File Uploads methods

    async def create_file_upload(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a new file upload request

        HTTP POST /file_uploads

        Args:
            request_body (Dict[str, Any], optional): Upload request: ``mode`` (single_part /
                multi_part / external_url), ``filename``, ``content_type``, and for multi_part
                ``number_of_parts``. Defaults to single_part, which accepts files up to 20MB.

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/file_uploads"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def send_file_upload(
        self,
        file_id: str,
        file_content: bytes,
        filename: str,
        content_type: Optional[str] = None,
        part_number: Optional[int] = None,
        **kwargs
    ) -> NotionResponse:
        """Send file bytes for a previously created file upload

        HTTP POST /file_uploads/{file_id}/send

        This endpoint requires ``multipart/form-data`` rather than JSON, which the shared
        ``HTTPClient`` cannot express, so the request is issued with httpx directly. The
        Authorization / Notion-Version headers are reused from the configured client;
        Content-Type is dropped so httpx can set the multipart boundary.

        Args:
            file_id (str, required): ``id`` from ``create_file_upload``
            file_content (bytes, required): Raw file bytes
            filename (str, required): File name, used as the multipart part filename
            content_type (str, optional): MIME type of the file
            part_number (int, optional): Part index, required for multi_part uploads

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/file_uploads/{file_id}/send".format(file_id=quote(str(file_id), safe=''))

        headers = {
            key: value
            for key, value in self.http_client.headers.items()
            if key.lower() != "content-type"
        }
        files = {"file": (filename, file_content, content_type or "application/octet-stream")}
        data = {"part_number": str(part_number)} if part_number is not None else None

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    files=files,
                    data=data,
                    params={k: str(v) for k, v in params.items() if v is not None} or None,
                )
            return NotionResponse.from_http(HTTPResponse(response))
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def complete_file_upload(self, file_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Complete a file upload process

        HTTP POST /file_uploads/{file_id}/complete

        Only required for multi_part uploads; single_part uploads are complete after send.

        Args:
            file_id (str, required): File ID to complete
            request_body (Dict[str, Any], optional): Upload completion confirmation

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/file_uploads/{file_id}/complete".format(file_id=quote(str(file_id), safe=''))
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_file_upload(self, file_id: str, **kwargs) -> NotionResponse:
        """Retrieve information about a file upload

        HTTP GET /file_uploads/{file_id}

        Args:
            file_id (str, required): File ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/file_uploads/{file_id}".format(file_id=quote(str(file_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_file_uploads(self, start_cursor: Optional[str] = None, page_size: Optional[int] = None, **kwargs) -> NotionResponse:
        """List all file uploads for the workspace

        HTTP GET /file_uploads

        Args:
            start_cursor (str, optional): Pagination cursor
            page_size (int, optional): Number of items to return (max 100)

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if start_cursor is not None:
            params['start_cursor'] = start_cursor
        if page_size is not None:
            params['page_size'] = page_size
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/file_uploads"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Search methods

    async def search(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Search all original pages, databases, and child pages/databases that are shared with the integration

        HTTP POST /search

        Args:
            request_body (Dict[str, Any], optional): Search object with query, sort, filter, start_cursor, and page_size

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/search"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Users methods

    async def list_users(self, start_cursor: Optional[str] = None, page_size: Optional[int] = None, **kwargs) -> NotionResponse:
        """Return a paginated list of Users for the workspace

        HTTP GET /users

        Args:
            start_cursor (str, optional): Pagination cursor
            page_size (int, optional): Number of items to return (max 100)

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if start_cursor is not None:
            params['start_cursor'] = start_cursor
        if page_size is not None:
            params['page_size'] = page_size
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/users"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_user(self, user_id: str, **kwargs) -> NotionResponse:
        """Retrieve a User using the ID specified

        HTTP GET /users/{user_id}

        Args:
            user_id (str, required): User ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/users/{user_id}".format(user_id=quote(str(user_id), safe=''))
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse.from_http(response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))
