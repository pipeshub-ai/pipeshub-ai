from typing import Any, Dict, List, Optional

from app.sources.client.http.http_request import HTTPRequest
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
    - SCIM v2.0 (complete enterprise user/group provisioning)
    - Organization Management (enterprise organization controls)
    - Workspace Administration (advanced admin features)
    - Integration Management (control workspace integrations)
    - Security & Compliance (SAML SSO, domain verification)
    - Content Management (enterprise content discovery & permissions)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/blocks/{block_id}".format(block_id=block_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/blocks/{block_id}".format(block_id=block_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/blocks/{block_id}".format(block_id=block_id)
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/blocks/{block_id}/children".format(block_id=block_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/blocks/{block_id}/children".format(block_id=block_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/pages/{page_id}".format(page_id=page_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/pages/{page_id}".format(page_id=page_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/pages/{page_id}/properties/{property_id}".format(page_id=page_id, property_id=property_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/databases/{database_id}/query".format(database_id=database_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/databases/{database_id}".format(database_id=database_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/databases/{database_id}".format(database_id=database_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/databases/{database_id}/data_sources".format(database_id=database_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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

        HTTP PATCH /databases/{database_id}/data_sources/{data_source_id}

        Args:
            database_id (str, required): Database ID containing the data source
            data_source_id (str, required): Data source ID to update
            request_body (Dict[str, Any], optional): Updated data source configuration

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}/data_sources/{data_source_id}".format(database_id=database_id, data_source_id=data_source_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_data_source(self, database_id: str, data_source_id: str, **kwargs) -> NotionResponse:
        """Retrieve a specific data source by ID

        HTTP GET /databases/{database_id}/data_sources/{data_source_id}

        Args:
            database_id (str, required): Database ID containing the data source
            data_source_id (str, required): Data source ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}/data_sources/{data_source_id}".format(database_id=database_id, data_source_id=data_source_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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

        HTTP POST /databases/{database_id}/data_sources/{data_source_id}/query

        Args:
            database_id (str, required): Database ID containing the data source
            data_source_id (str, required): Data source ID to query
            request_body (Dict[str, Any], optional): Query parameters for the data source

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/databases/{database_id}/data_sources/{data_source_id}/query".format(database_id=database_id, data_source_id=data_source_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/comments/{comment_id}".format(comment_id=comment_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # File Uploads methods

    async def create_file_upload(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a new file upload request

        HTTP POST /files

        Args:
            request_body (Dict[str, Any], optional): File upload request with name, file details, and parent information

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/files"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def send_file_upload(self, file_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Send file data to complete the upload

        HTTP POST /files/{file_id}/upload

        Args:
            file_id (str, required): File ID from upload session
            request_body (Dict[str, Any], optional): File binary data and metadata

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/files/{file_id}/upload".format(file_id=file_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def complete_file_upload(self, file_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Complete a file upload process

        HTTP POST /files/{file_id}/complete

        Args:
            file_id (str, required): File ID to complete
            request_body (Dict[str, Any], optional): Upload completion confirmation

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/files/{file_id}/complete".format(file_id=file_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_file_upload(self, file_id: str, **kwargs) -> NotionResponse:
        """Retrieve information about a file upload

        HTTP GET /files/{file_id}

        Args:
            file_id (str, required): File ID to retrieve

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/files/{file_id}".format(file_id=file_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_file_uploads(self, start_cursor: Optional[str] = None, page_size: Optional[int] = None, **kwargs) -> NotionResponse:
        """List all file uploads for the workspace

        HTTP GET /files

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
        url = self.base_url + "/files"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
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
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Scim methods

    async def get_scim_service_provider_config(self, **kwargs) -> NotionResponse:
        """Get SCIM service provider configuration

        HTTP GET /scim/v2/ServiceProviderConfig

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/ServiceProviderConfig"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_scim_resource_types(self, **kwargs) -> NotionResponse:
        """Get supported SCIM resource types

        HTTP GET /scim/v2/ResourceTypes

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/ResourceTypes"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_scim_users(
        self,
        start_index: Optional[int] = None,
        count: Optional[int] = None,
        filter: Optional[str] = None,
        **kwargs
    ) -> NotionResponse:
        """Retrieve a paginated list of workspace members via SCIM

        HTTP GET /scim/v2/Users

        Args:
            start_index (int, optional): 1-indexed start position
            count (int, optional): Number of results (max 100)
            filter (str, optional): Filter expression

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if start_index is not None:
            params['startIndex'] = start_index
        if count is not None:
            params['count'] = count
        if filter is not None:
            params['filter'] = filter
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Users"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_scim_user(self, user_id: str, **kwargs) -> NotionResponse:
        """Retrieve a specific workspace member by SCIM user ID

        HTTP GET /scim/v2/Users/{user_id}

        Args:
            user_id (str, required): SCIM User ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Users/{user_id}".format(user_id=user_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def create_scim_user(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a new user via SCIM provisioning

        HTTP POST /scim/v2/Users

        Args:
            request_body (Dict[str, Any], optional): SCIM user object with required attributes

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Users"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_scim_user(self, user_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update a user via SCIM (full replacement)

        HTTP PUT /scim/v2/Users/{user_id}

        Args:
            user_id (str, required): SCIM User ID
            request_body (Dict[str, Any], optional): Complete SCIM user object

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Users/{user_id}".format(user_id=user_id)
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def patch_scim_user(self, user_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Partially update a user via SCIM operations

        HTTP PATCH /scim/v2/Users/{user_id}

        Args:
            user_id (str, required): SCIM User ID
            request_body (Dict[str, Any], optional): SCIM patch operations

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Users/{user_id}".format(user_id=user_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def delete_scim_user(self, user_id: str, **kwargs) -> NotionResponse:
        """Remove a user from workspace via SCIM

        HTTP DELETE /scim/v2/Users/{user_id}

        Args:
            user_id (str, required): SCIM User ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Users/{user_id}".format(user_id=user_id)
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_scim_groups(
        self,
        start_index: Optional[int] = None,
        count: Optional[int] = None,
        filter: Optional[str] = None,
        **kwargs
    ) -> NotionResponse:
        """Retrieve a paginated list of groups via SCIM

        HTTP GET /scim/v2/Groups

        Args:
            start_index (int, optional): 1-indexed start position
            count (int, optional): Number of results (max 100)
            filter (str, optional): Filter expression

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if start_index is not None:
            params['startIndex'] = start_index
        if count is not None:
            params['count'] = count
        if filter is not None:
            params['filter'] = filter
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Groups"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def retrieve_scim_group(self, group_id: str, **kwargs) -> NotionResponse:
        """Retrieve a specific group by SCIM group ID

        HTTP GET /scim/v2/Groups/{group_id}

        Args:
            group_id (str, required): SCIM Group ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Groups/{group_id}".format(group_id=group_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def create_scim_group(self, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Create a new group via SCIM provisioning

        HTTP POST /scim/v2/Groups

        Args:
            request_body (Dict[str, Any], optional): SCIM group object with members

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Groups"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_scim_group(self, group_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update a group via SCIM (full replacement)

        HTTP PUT /scim/v2/Groups/{group_id}

        Args:
            group_id (str, required): SCIM Group ID
            request_body (Dict[str, Any], optional): Complete SCIM group object

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Groups/{group_id}".format(group_id=group_id)
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def patch_scim_group(self, group_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Partially update a group via SCIM operations

        HTTP PATCH /scim/v2/Groups/{group_id}

        Args:
            group_id (str, required): SCIM Group ID
            request_body (Dict[str, Any], optional): SCIM patch operations

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Groups/{group_id}".format(group_id=group_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def delete_scim_group(self, group_id: str, **kwargs) -> NotionResponse:
        """Remove a group via SCIM

        HTTP DELETE /scim/v2/Groups/{group_id}

        Args:
            group_id (str, required): SCIM Group ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/scim/v2/Groups/{group_id}".format(group_id=group_id)
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Organization methods

    async def get_organization_settings(self, organization_id: str, **kwargs) -> NotionResponse:
        """Get organization-level settings and configuration

        HTTP GET /organizations/{organization_id}

        Args:
            organization_id (str, required): Organization ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/organizations/{organization_id}".format(organization_id=organization_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_organization_settings(self, organization_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update organization-level settings

        HTTP PATCH /organizations/{organization_id}

        Args:
            organization_id (str, required): Organization ID
            request_body (Dict[str, Any], optional): Organization settings to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/organizations/{organization_id}".format(organization_id=organization_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_organization_workspaces(self, organization_id: str, **kwargs) -> NotionResponse:
        """List all workspaces in the organization

        HTTP GET /organizations/{organization_id}/workspaces

        Args:
            organization_id (str, required): Organization ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/organizations/{organization_id}/workspaces".format(organization_id=organization_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def claim_workspace(self, organization_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Claim ownership of eligible workspaces

        HTTP POST /organizations/{organization_id}/workspaces/claim

        Args:
            organization_id (str, required): Organization ID
            request_body (Dict[str, Any], optional): Workspace claim configuration

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/organizations/{organization_id}/workspaces/claim".format(organization_id=organization_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Workspace Admin methods

    async def get_workspace_settings(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get workspace administration settings

        HTTP GET /workspaces/{workspace_id}/admin/settings

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/settings".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_workspace_settings(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update workspace administration settings

        HTTP PATCH /workspaces/{workspace_id}/admin/settings

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Workspace settings to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/settings".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_workspace_members_admin(
        self,
        workspace_id: str,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
        **kwargs
    ) -> NotionResponse:
        """List all workspace members (admin view)

        HTTP GET /workspaces/{workspace_id}/admin/members

        Args:
            workspace_id (str, required): Workspace ID
            start_cursor (str, optional): Pagination cursor
            page_size (int, optional): Number of items to return

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
        url = self.base_url + "/workspaces/{workspace_id}/admin/members".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_member_permissions(
        self,
        workspace_id: str,
        user_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Update member permissions and roles

        HTTP PATCH /workspaces/{workspace_id}/admin/members/{user_id}

        Args:
            workspace_id (str, required): Workspace ID
            user_id (str, required): User ID
            request_body (Dict[str, Any], optional): Member permission updates

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/members/{user_id}".format(workspace_id=workspace_id, user_id=user_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def remove_workspace_member(self, workspace_id: str, user_id: str, **kwargs) -> NotionResponse:
        """Remove member from workspace

        HTTP DELETE /workspaces/{workspace_id}/admin/members/{user_id}

        Args:
            workspace_id (str, required): Workspace ID
            user_id (str, required): User ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/members/{user_id}".format(workspace_id=workspace_id, user_id=user_id)
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def transfer_member_content(
        self,
        workspace_id: str,
        user_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Transfer member's private pages to another user

        HTTP POST /workspaces/{workspace_id}/admin/members/{user_id}/transfer

        Args:
            workspace_id (str, required): Workspace ID
            user_id (str, required): User ID
            request_body (Dict[str, Any], optional): Content transfer configuration

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/members/{user_id}/transfer".format(workspace_id=workspace_id, user_id=user_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_recently_left_members(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get list of recently left members

        HTTP GET /workspaces/{workspace_id}/admin/members/recently-left

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/members/recently-left".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Integrations methods

    async def list_workspace_integrations(self, workspace_id: str, **kwargs) -> NotionResponse:
        """List all integrations in workspace

        HTTP GET /workspaces/{workspace_id}/integrations

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/integrations".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_integration_details(self, workspace_id: str, integration_id: str, **kwargs) -> NotionResponse:
        """Get details of a specific integration

        HTTP GET /workspaces/{workspace_id}/integrations/{integration_id}

        Args:
            workspace_id (str, required): Workspace ID
            integration_id (str, required): Integration ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/integrations/{integration_id}".format(workspace_id=workspace_id, integration_id=integration_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_integration_settings(
        self,
        workspace_id: str,
        integration_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Update integration settings and permissions

        HTTP PATCH /workspaces/{workspace_id}/integrations/{integration_id}

        Args:
            workspace_id (str, required): Workspace ID
            integration_id (str, required): Integration ID
            request_body (Dict[str, Any], optional): Integration settings to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/integrations/{integration_id}".format(workspace_id=workspace_id, integration_id=integration_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def remove_integration(self, workspace_id: str, integration_id: str, **kwargs) -> NotionResponse:
        """Remove integration from workspace

        HTTP DELETE /workspaces/{workspace_id}/integrations/{integration_id}

        Args:
            workspace_id (str, required): Workspace ID
            integration_id (str, required): Integration ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/integrations/{integration_id}".format(workspace_id=workspace_id, integration_id=integration_id)
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_integration_restrictions(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get workspace integration restrictions

        HTTP GET /workspaces/{workspace_id}/integration-restrictions

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/integration-restrictions".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_integration_restrictions(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update workspace integration restrictions

        HTTP PATCH /workspaces/{workspace_id}/integration-restrictions

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Integration restriction policies

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/integration-restrictions".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Security methods

    async def get_security_settings(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get workspace security settings

        HTTP GET /workspaces/{workspace_id}/security

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/security".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_security_settings(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update workspace security settings

        HTTP PATCH /workspaces/{workspace_id}/security

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Security settings to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/security".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_saml_sso_config(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get SAML SSO configuration

        HTTP GET /workspaces/{workspace_id}/security/saml

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/security/saml".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_saml_sso_config(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update SAML SSO configuration

        HTTP PATCH /workspaces/{workspace_id}/security/saml

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): SAML SSO configuration

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/security/saml".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def verify_domain(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Verify domain ownership for enterprise features

        HTTP POST /workspaces/{workspace_id}/security/domains/verify

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Domain verification data

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/security/domains/verify".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def list_verified_domains(self, workspace_id: str, **kwargs) -> NotionResponse:
        """List verified domains for workspace

        HTTP GET /workspaces/{workspace_id}/security/domains

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/security/domains".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    # Content Management methods

    async def search_workspace_content(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Enterprise content search across workspace

        HTTP POST /workspaces/{workspace_id}/admin/content/search

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Advanced search parameters for content discovery

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/content/search".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_content_permissions(self, workspace_id: str, page_id: str, **kwargs) -> NotionResponse:
        """Get detailed permissions for a page or database

        HTTP GET /workspaces/{workspace_id}/admin/content/{page_id}/permissions

        Args:
            workspace_id (str, required): Workspace ID
            page_id (str, required): Page or database ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/content/{page_id}/permissions".format(workspace_id=workspace_id, page_id=page_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_content_permissions(
        self,
        workspace_id: str,
        page_id: str,
        request_body: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> NotionResponse:
        """Update permissions for a page or database

        HTTP PATCH /workspaces/{workspace_id}/admin/content/{page_id}/permissions

        Args:
            workspace_id (str, required): Workspace ID
            page_id (str, required): Page or database ID
            request_body (Dict[str, Any], optional): Permission updates

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/content/{page_id}/permissions".format(workspace_id=workspace_id, page_id=page_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_sharing_settings(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get workspace sharing and collaboration settings

        HTTP GET /workspaces/{workspace_id}/admin/sharing-settings

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/sharing-settings".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_sharing_settings(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update workspace sharing and collaboration settings

        HTTP PATCH /workspaces/{workspace_id}/admin/sharing-settings

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Sharing settings to update

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/sharing-settings".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def bulk_content_operations(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Perform bulk operations on content

        HTTP POST /workspaces/{workspace_id}/admin/content/bulk

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Bulk operation parameters

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/content/bulk".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def get_trash_settings(self, workspace_id: str, **kwargs) -> NotionResponse:
        """Get trash retention and deletion settings

        HTTP GET /workspaces/{workspace_id}/admin/trash

        Args:
            workspace_id (str, required): Workspace ID

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/trash".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))

    async def update_trash_settings(self, workspace_id: str, request_body: Optional[Dict[str, Any]] = None, **kwargs) -> NotionResponse:
        """Update trash retention and deletion settings

        HTTP PATCH /workspaces/{workspace_id}/admin/trash

        Args:
            workspace_id (str, required): Workspace ID
            request_body (Dict[str, Any], optional): Trash retention settings

        Returns:
            NotionResponse: Standardized response wrapper with success/data/error
        """
        params: Dict[str, Any] = {}
        if kwargs:
            params.update(kwargs)
        url = self.base_url + "/workspaces/{workspace_id}/admin/trash".format(workspace_id=workspace_id)
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.http_client.headers,
            query_params={k: str(v) for k, v in params.items() if v is not None},
            body=request_body,
        )
        try:
            response = await self.http_client.execute(request)
            return NotionResponse(success=True, data=response)
        except Exception as e:
            return NotionResponse(success=False, error=str(e))
