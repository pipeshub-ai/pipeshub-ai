import asyncio
import json
import logging
import threading
from typing import Coroutine, Dict, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.notion.notion import NotionClient
from app.sources.external.notion.notion import NotionDataSource

logger = logging.getLogger(__name__)


class Notion:
    """Notion tool exposed to the agents using NotionDataSource"""

    def __init__(self, client: NotionClient) -> None:
        """Initialize the Notion tool

        Args:
            client: Notion client object
        """
        self.client = NotionDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop"""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, object]) -> object:
        """Run a coroutine safely from sync context via a dedicated loop.

        Args:
            coro: Coroutine to execute

        Returns:
            Result from the executed coroutine (NotionResponse)
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"Notion shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response: object,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle Notion API response and return standardized tuple.

        Args:
            response: NotionResponse object
            success_message: Message to return on success

        Returns:
            Tuple of (success_flag, json_string)
        """
        try:
            # Check if response indicates success
            if hasattr(response, 'success') and response.success:
                # Extract data from response
                data = None
                if hasattr(response, 'data'):
                    response_data = response.data

                    # If data is HTTPResponse, extract JSON
                    if hasattr(response_data, 'json') and callable(response_data.json):
                        try:
                            data = response_data.json()
                        except Exception:
                            data = str(response_data)
                    elif isinstance(response_data, (dict, list)):
                        data = response_data
                    else:
                        data = str(response_data)

                # Notion API returns HTTP 200 OK but with error in response body
                if isinstance(data, dict) and data.get("object") == "error":
                    logger.error(f"Notion API error in response data: {data}")
                    return False, json.dumps({
                        "error": data.get("message", "Unknown Notion API error"),
                        "error_code": data.get("code"),
                        "status": data.get("status"),
                        "details": data
                    })

                return True, json.dumps({
                    "message": success_message,
                    "data": data
                })
            else:
                # Extract error information
                error = {}
                if hasattr(response, 'error'):
                    error = response.error or {}

                # Try to get status code from nested data
                if hasattr(response, 'data'):
                    response_data = response.data
                    status_code = (
                        getattr(response_data, 'status_code', None) or
                        getattr(response_data, 'status', None)
                    )
                    if status_code:
                        error['status_code'] = status_code

                logger.error(f"Notion API error: {error}")
                return False, json.dumps({"error": error})

        except Exception as e:
            logger.error(f"Error handling response: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="create_page",
        description="""Create a new page in Notion. **IMPORTANT**: You MUST have a valid parent_id before calling this tool.
        If you don't have a parent_id, use notion.search to find existing pages/databases first, or ask the user to provide it.
        Valid parent_id format: 32-character UUID with dashes (e.g., '12345678-1234-1234-1234-123456789012') or 32-character hex string.
        **DO NOT** use placeholder IDs - always get a real ID first.""",
        parameters=[
            ToolParameter(
                name="parent_id",
                type=ParameterType.STRING,
                description="""The ID of the parent page or database where this page will be created.
                REQUIRED format: Valid Notion page/database ID (32-char UUID or hex string).
                1. Use notion.search to find existing pages/databases, OR
                2. Ask the user to provide the parent page/database ID (found in Notion URL)""",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title/name of the page to create",
                required=True
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Optional markdown content to add to the page body",
                required=False
            ),
            ToolParameter(
                name="parent_is_database",
                type=ParameterType.BOOLEAN,
                description="Set to True if parent_id refers to a database, False if it's a page (default: False)",
                required=False
            ),
            ToolParameter(
                name="title_property",
                type=ParameterType.STRING,
                description="The property name for the title in database pages (default: 'title')",
                required=False
            ),
        ],
        returns="JSON with success status and created page details, or error with explanation"
    )
    def create_page(
        self,
        parent_id: str,
        title: str,
        content: Optional[str] = None,
        parent_is_database: Optional[bool] = False,
        title_property: Optional[str] = "title",
    ) -> Tuple[bool, str]:
        """Create a page in Notion.

        Args:
            parent_id: The ID of the parent page or database
            title: The title of the page
            content: Optional content for the page
            parent_is_database: Whether parent is a database
            title_property: Title property name

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Build parent block
            parent_block = (
                {"database_id": parent_id} if parent_is_database
                else {"page_id": parent_id}
            )

            # Build request body
            request_body: Dict[str, object] = {
                "parent": parent_block,
                "properties": {
                    (title_property or "title"): {
                        "title": [
                            {
                                "text": {
                                    "content": title
                                }
                            }
                        ]
                    }
                }
            }

            # Add content if provided
            if content:
                request_body["children"] = [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": content
                                    }
                                }
                            ]
                        }
                    }
                ]

            response = self._run_async(
                self.client.create_page(request_body=request_body)
            )
            return self._handle_response(response, "Page created successfully")

        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="get_page",
        description="Get a page from Notion",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to retrieve",
                required=True
            ),
        ],
        returns="JSON with page details"
    )
    def get_page(self, page_id: str) -> Tuple[bool, str]:
        """Get a page from Notion.

        Args:
            page_id: The ID of the page to retrieve

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = self._run_async(
                self.client.retrieve_page(page_id=page_id)
            )
            return self._handle_response(response, "Page retrieved successfully")

        except Exception as e:
            logger.error(f"Error getting page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="update_page",
        description="Update a page in Notion",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to update",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The new title of the page",
                required=False
            ),
        ],
        returns="JSON with success status and updated page details"
    )
    def update_page(
        self,
        page_id: str,
        title: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Update a page in Notion.

        Args:
            page_id: The ID of the page to update
            title: Optional new title for the page

        Returns:
            Tuple of (success, json_response)
        """
        try:
            if not title:
                return False, json.dumps({
                    "error": "No properties to update. Please provide a title."
                })

            # Build request body
            request_body: Dict[str, object] = {
                "properties": {
                    "title": {
                        "title": [
                            {
                                "text": {
                                    "content": title
                                }
                            }
                        ]
                    }
                }
            }

            response = self._run_async(
                self.client.update_page_properties(
                    page_id=page_id,
                    request_body=request_body
                )
            )
            return self._handle_response(response, "Page updated successfully")

        except Exception as e:
            logger.error(f"Error updating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="delete_page",
        description="Delete a page from Notion (archives the page)",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to delete",
                required=True
            ),
        ],
        returns="JSON with success status"
    )
    def delete_page(self, page_id: str) -> Tuple[bool, str]:
        """Delete (archive) a page from Notion.

        Args:
            page_id: The ID of the page to delete

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Pages are blocks in Notion API
            response = self._run_async(
                self.client.delete_block(block_id=page_id)
            )
            return self._handle_response(response, "Page deleted successfully")

        except Exception as e:
            logger.error(f"Error deleting page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="search",
        description="""Search for pages and databases in Notion workspace.
        **USE THIS FIRST** before calling notion.create_page or notion.create_database if you need to find existing resources or get valid parent IDs.
        Returns list of pages/databases with their IDs which can be used as parent_id in create operations.
        If no query provided, returns recent pages/databases.""",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query text to find specific pages/databases by title or content. Leave empty to get recent pages/databases.",
                required=False
            ),
            ToolParameter(
                name="sort",
                type=ParameterType.DICT,
                description="Sort configuration (e.g., {'direction': 'ascending', 'timestamp': 'last_edited_time'})",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.DICT,
                description="Filter configuration to narrow results (e.g., {'value': 'page', 'property': 'object'} to only get pages)",
                required=False
            ),
            ToolParameter(
                name="start_cursor",
                type=ParameterType.STRING,
                description="Pagination cursor for getting next page of results",
                required=False
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Number of results to return (max 100, default 100)",
                required=False
            ),
        ],
        returns="JSON with array of pages/databases including their IDs, titles, and metadata. Use the 'id' field from results as parent_id for creating new pages."
    )
    def search(
        self,
        query: Optional[str] = None,
        sort: Optional[Dict[str, object]] = None,
        filter: Optional[Dict[str, object]] = None,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Search Notion pages and databases.

        Args:
            query: Search query text
            sort: Sort configuration
            filter: Filter configuration
            start_cursor: Pagination cursor
            page_size: Number of results to return

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Build request body
            request_body: Dict[str, object] = {}
            if query is not None:
                request_body["query"] = query
            if sort is not None:
                request_body["sort"] = sort
            if filter is not None:
                request_body["filter"] = filter
            if start_cursor is not None:
                request_body["start_cursor"] = start_cursor
            if page_size is not None:
                request_body["page_size"] = page_size

            response = self._run_async(
                self.client.search(request_body=request_body)
            )
            return self._handle_response(response, "Search completed successfully")

        except Exception as e:
            logger.error(f"Error searching: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="list_users",
        description="List users in the Notion workspace",
        parameters=[
            ToolParameter(
                name="start_cursor",
                type=ParameterType.STRING,
                description="Pagination cursor",
                required=False
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Number of users to return (max 100)",
                required=False
            ),
        ],
        returns="JSON with list of users"
    )
    def list_users(
        self,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """List users in the Notion workspace.

        Args:
            start_cursor: Pagination cursor
            page_size: Number of users to return
        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = self._run_async(
                self.client.list_users(
                    start_cursor=start_cursor,
                    page_size=page_size
                )
            )
            return self._handle_response(response, "Users listed successfully")

        except Exception as e:
            logger.error(f"Error listing users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="retrieve_user",
        description="Retrieve a Notion user by ID",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="The user ID to retrieve",
                required=True
            ),
        ],
        returns="JSON with user details"
    )
    def retrieve_user(self, user_id: str) -> Tuple[bool, str]:
        """Retrieve a Notion user by ID.

        Args:
            user_id: The user ID to retrieve

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = self._run_async(
                self.client.retrieve_user(user_id=user_id)
            )
            return self._handle_response(response, "User retrieved successfully")

        except Exception as e:
            logger.error(f"Error retrieving user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="create_database",
        description="""Create a new database in Notion. **IMPORTANT**: You MUST have a valid parent_id (page ID) before calling this tool.
        Databases can only be created inside pages, not inside other databases.
        If you don't have a parent page ID, use notion.search to find existing pages first, or ask the user to provide it.
        Valid parent_id format: 32-character UUID with dashes or 32-character hex string.
        **DO NOT** use placeholder IDs - always get a real page ID first.""",
        parameters=[
            ToolParameter(
                name="parent_id",
                type=ParameterType.STRING,
                description="""The ID of the parent PAGE where this database will be created (databases must be inside pages).
                REQUIRED format: Valid Notion page ID (32-char UUID or hex string).
                1. Use notion.search to find existing pages, OR
                2. Ask the user to provide the parent page ID (found in Notion URL)""",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title/name of the database to create",
                required=True
            ),
            ToolParameter(
                name="properties",
                type=ParameterType.DICT,
                description="""Database properties schema defining columns. Example: {'Name': {'title': {}}, 'Status': {'select': {'options': [{'name': 'To Do'}]}}}
                Each property defines a column with its type (title, text, select, multi_select, date, etc.)""",
                required=True
            ),
        ],
        returns="JSON with success status and created database details, or error with explanation"
    )
    def create_database(
        self,
        parent_id: str,
        title: str,
        properties: Dict[str, object],
    ) -> Tuple[bool, str]:
        """Create a database in Notion.

        Args:
            parent_id: The ID of the parent page
            title: The title of the database
            properties: Database properties schema

        Returns:
            Tuple of (success, json_response)
        """
        try:
            request_body: Dict[str, object] = {
                "parent": {"page_id": parent_id},
                "title": [
                    {
                        "type": "text",
                        "text": {
                            "content": title
                        }
                    }
                ],
                "properties": properties
            }

            response = self._run_async(
                self.client.create_database(request_body=request_body)
            )
            return self._handle_response(response, "Database created successfully")

        except Exception as e:
            logger.error(f"Error creating database: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="query_database",
        description="Query a Notion database",
        parameters=[
            ToolParameter(
                name="database_id",
                type=ParameterType.STRING,
                description="The ID of the database to query",
                required=True
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.DICT,
                description="Filter configuration",
                required=False
            ),
            ToolParameter(
                name="sorts",
                type=ParameterType.LIST,
                description="Sort configuration",
                required=False
            ),
            ToolParameter(
                name="start_cursor",
                type=ParameterType.STRING,
                description="Pagination cursor",
                required=False
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Number of results (max 100)",
                required=False
            ),
        ],
        returns="JSON with query results"
    )
    def query_database(
        self,
        database_id: str,
        filter: Optional[Dict[str, object]] = None,
        sorts: Optional[list] = None,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Query a Notion database.

        Args:
            database_id: The ID of the database
            filter: Filter configuration
            sorts: Sort configuration
            start_cursor: Pagination cursor
            page_size: Number of results to return

        Returns:
            Tuple of (success, json_response)
        """
        try:
            request_body: Dict[str, object] = {}
            if filter is not None:
                request_body["filter"] = filter
            if sorts is not None:
                request_body["sorts"] = sorts
            if start_cursor is not None:
                request_body["start_cursor"] = start_cursor
            if page_size is not None:
                request_body["page_size"] = page_size

            response = self._run_async(
                self.client.query_database(
                    database_id=database_id,
                    request_body=request_body
                )
            )
            return self._handle_response(response, "Database queried successfully")

        except Exception as e:
            logger.error(f"Error querying database: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="notion",
        tool_name="get_database",
        description="Get a Notion database by ID",
        parameters=[
            ToolParameter(
                name="database_id",
                type=ParameterType.STRING,
                description="The ID of the database to retrieve",
                required=True
            ),
        ],
        returns="JSON with database details"
    )
    def get_database(self, database_id: str) -> Tuple[bool, str]:
        """Get a Notion database by ID.

        Args:
            database_id: The ID of the database

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = self._run_async(
                self.client.retrieve_database(database_id=database_id)
            )
            return self._handle_response(response, "Database retrieved successfully")

        except Exception as e:
            logger.error(f"Error getting database: {e}")
            return False, json.dumps({"error": str(e)})
