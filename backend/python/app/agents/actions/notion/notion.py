import json
import logging
from typing import Dict, Optional, Tuple

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.notion.notion import NotionClient, notion_api_base_url
from app.sources.external.notion.notion import NotionDataSource

logger = logging.getLogger(__name__)
# Notion's request limits: 2,000 characters per rich-text item, 100 children per request.
_MAX_TEXT = 2000
_MAX_CHILDREN = 100


# Register Notion toolset
@ToolsetBuilder("Notion")\
    .in_group("Productivity")\
    .with_description("Notion integration for pages, databases, and workspace management")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Notion",
            authorize_url=f"{notion_api_base_url()}/oauth/authorize",
            token_url=f"{notion_api_base_url()}/oauth/token",
            redirect_uri="toolsets/oauth/callback/notion",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "read",
                    "update",
                    "insert"
                ]
            ),
            fields=[
                CommonFields.client_id("Notion Integration Settings"),
                CommonFields.client_secret("Notion Integration Settings")
            ],
            icon_path=IconPaths.connector_icon("notion"),
            app_group="Productivity",
            app_description="Notion OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Notion Integration Token", "secret_your-token-here")
        ])
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("notion")))\
    .build_decorator()
class Notion:
    """Notion tool exposed to the agents using NotionDataSource"""

    def __init__(self, client: NotionClient) -> None:
        """Initialize the Notion tool

        Args:
            client: Notion client object
        """
        self.client = NotionDataSource(client)

    @staticmethod
    def _json(response: object) -> object | None:
        """The JSON body of a NotionResponse, or None when there is none (or it is not JSON)."""
        data = getattr(response, "data", None)
        if isinstance(data, (dict, list)):
            return data
        if hasattr(data, "json") and callable(data.json):
            try:
                return data.json()
            except Exception:
                return None
        return None

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
            body = self._json(response)
            # Notion's error object ({"object": "error", "code", "message"}) on a failure, or in a 2xx body.
            if isinstance(body, dict) and body.get("object") == "error":
                logger.error(f"Notion API error: {body.get('code')}: {body.get('message')}")
                return False, json.dumps({
                    "error": body.get("message", "Unknown Notion API error"),
                    "error_code": body.get("code"),
                    "status_code": body.get("status") or getattr(response, "status_code", None),
                })
            if getattr(response, "success", False):
                data = body if body is not None else str(getattr(response, "data", ""))
                return True, json.dumps({"message": success_message, "data": data})
            error = getattr(response, "error", None) or "Unknown Notion API error"
            logger.error(f"Notion API error: {error}")
            return False, json.dumps({"error": error, "status_code": getattr(response, "status_code", None)})

        except Exception as e:
            logger.error(f"Error handling response: {e}")
            return False, json.dumps({"error": str(e)})

    async def _first_data_source(self, database_id: str) -> tuple[str, dict[str, object] | None]:
        """Notion-Version 2025-09-03 keeps rows and the schema on a database's data sources: the first one's id
        (and the database), or the id itself when it already names a data source."""
        database = await self.client.retrieve_database(database_id=database_id)
        body = self._json(database) if database.success else None
        if isinstance(body, dict) and body.get("object") != "error":
            sources = body.get("data_sources") or []
            if sources:
                return sources[0]["id"], body
            return database_id, body
        return database_id, None

    async def _title_property(self, data_source_id: str) -> str:
        """The name of a data source's title property ("Name", "Task", ...)."""
        response = await self.client.retrieve_data_source_by_id(data_source_id=data_source_id)
        body = self._json(response) if response.success else None
        for name, prop in ((body or {}).get("properties") or {}).items() if isinstance(body, dict) else ():
            if isinstance(prop, dict) and prop.get("type") == "title":
                return name
        return "title"

    @staticmethod
    def _paragraphs(content: str) -> list:
        """Paragraph blocks for `content`: one per line, each split at Notion's 2,000-character rich-text limit."""
        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[i:i + _MAX_TEXT]}}]
                              if line else []},
            }
            for line in content.split("\n")
            for i in range(0, max(len(line), 1), _MAX_TEXT)
        ]

    @tool(
        path="/tools/notion/create_page",
        short_description="Create a new page in Notion",
        description=(
            "Create a new page in Notion. You MUST have a valid parent_id before calling this tool. "
            "If you don't have a parent_id, use notion.search to find existing pages/databases first, or ask the user to provide it. "
            "Valid parent_id format: 32-character UUID with dashes (e.g., '12345678-1234-1234-1234-123456789012') or 32-character hex string. "
            "Do not use placeholder IDs - always get a real ID first."
        ),
        parameters=[
            ToolParameter(name="parent_id", type=ParameterType.STRING, description="The ID of the parent page or database where this page will be created"),
            ToolParameter(name="title", type=ParameterType.STRING, description="The title/name of the page to create"),
            ToolParameter(name="content", type=ParameterType.STRING, description="Optional plain-text content for the page body; each line becomes a paragraph", required=False),
            ToolParameter(name="parent_is_database", type=ParameterType.BOOLEAN, description="Set to True if parent_id refers to a database (or one of its data sources); the page becomes a row of its first data source", required=False, default=False),
            ToolParameter(name="title_property", type=ParameterType.STRING, description="The property name for the title in database pages (found from the database when omitted)", required=False, default="title"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="create")],
    )
    async def create_page(
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
            if parent_is_database:
                # Notion-Version 2025-09-03: a row's parent is the database's data source.
                data_source_id, _ = await self._first_data_source(parent_id)
                parent_block: dict[str, object] = {"data_source_id": data_source_id}
                if not title_property or title_property == "title":
                    title_property = await self._title_property(data_source_id)
            else:
                parent_block = {"page_id": parent_id}

            request_body: Dict[str, object] = {
                "parent": parent_block,
                "properties": {
                    (title_property or "title"): {"title": [{"text": {"content": title[:_MAX_TEXT]}}]}
                }
            }
            blocks = self._paragraphs(content) if content else []
            if blocks:
                request_body["children"] = blocks[:_MAX_CHILDREN]

            response = await self.client.create_page(request_body=request_body)
            ok, result = self._handle_response(response, "Page created successfully")
            if not ok:
                return ok, result
            # The rest of a long body goes in further requests of at most 100 blocks.
            page = self._json(response) or {}
            for i in range(_MAX_CHILDREN, len(blocks), _MAX_CHILDREN):
                more = await self.client.append_block_children(
                    block_id=page.get("id"), request_body={"children": blocks[i:i + _MAX_CHILDREN]})
                if not more.success:
                    # The page exists: say so, so the agent appends the rest instead of creating it again.
                    failure = json.loads(self._handle_response(more, "")[1])
                    failure.update({
                        "partially_written": True,
                        "page_id": page.get("id"),
                        "url": page.get("url"),
                        "message": f"Page created, but only its first {i} content blocks were written; "
                                   "append the rest to this page instead of creating it again.",
                    })
                    return False, json.dumps(failure)
            return ok, result

        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/get_page",
        short_description="Get a page from Notion",
        description="Retrieve a Notion page by its ID.",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page to retrieve"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_page(self, page_id: str) -> Tuple[bool, str]:
        """Get a page from Notion.

        Args:
            page_id: The ID of the page to retrieve

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = await self.client.retrieve_page(page_id=page_id)
            return self._handle_response(response, "Page retrieved successfully")

        except Exception as e:
            logger.error(f"Error getting page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/update_page",
        short_description="Update a page in Notion",
        description="Update a Notion page's title by its ID.",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page to update"),
            ToolParameter(name="title", type=ParameterType.STRING, description="The new title of the page", required=False),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="update")],
    )
    async def update_page(
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

            # A database row's title property has its own name ("Name", "Task", ...): read it from the page.
            title_property = "title"
            page = await self.client.retrieve_page(page_id=page_id)
            if not page.success:
                return self._handle_response(page, "")
            for name, prop in ((self._json(page) or {}).get("properties") or {}).items():
                if isinstance(prop, dict) and prop.get("type") == "title":
                    title_property = name
            request_body: Dict[str, object] = {
                "properties": {
                    title_property: {
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

            response = await self.client.update_page_properties(
                    page_id=page_id,
                    request_body=request_body
                )
            return self._handle_response(response, "Page updated successfully")

        except Exception as e:
            logger.error(f"Error updating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/delete_page",
        short_description="Delete a page from Notion (archives the page)",
        description="Delete (archive) a page from Notion by its ID.",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page to delete"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="delete")],
    )
    async def delete_page(self, page_id: str) -> Tuple[bool, str]:
        """Delete (archive) a page from Notion.

        Args:
            page_id: The ID of the page to delete

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Pages are blocks in Notion API
            response = await self.client.delete_block(block_id=page_id)
            return self._handle_response(response, "Page deleted successfully")

        except Exception as e:
            logger.error(f"Error deleting page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/search",
        short_description="Search for pages and databases in Notion workspace",
        description=(
            "Search for pages and databases in the Notion workspace. "
            "Use this first before calling create_page or create_database if you need to find existing resources or get valid parent IDs. "
            "Returns list of pages/databases with their IDs which can be used as parent_id in create operations. "
            "If no query provided, returns recent pages/databases."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query text to find specific pages/databases by title or content", required=False),
            ToolParameter(name="sort", type=ParameterType.OBJECT, description="Sort configuration", required=False),
            ToolParameter(name="filter", type=ParameterType.OBJECT, description='Filter by object type: {"property": "object", "value": "page"} or {"property": "object", "value": "data_source"} (databases; "database" is accepted and sent as "data_source")', required=False),
            ToolParameter(name="start_cursor", type=ParameterType.STRING, description="Pagination cursor for getting next page of results", required=False),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Number of results to return (max 100, default 100)", required=False),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def search(
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
                # Notion-Version 2025-09-03 searches data sources, not databases: {"value": "database"} is a 400.
                request_body["filter"] = ({**filter, "value": "data_source"}
                                          if isinstance(filter, dict) and filter.get("value") == "database" else filter)
            if start_cursor is not None:
                request_body["start_cursor"] = start_cursor
            if page_size is not None:
                request_body["page_size"] = page_size

            response = await self.client.search(request_body=request_body)
            return self._handle_response(response, "Search completed successfully")

        except Exception as e:
            logger.error(f"Error searching: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/list_users",
        short_description="List users in the Notion workspace",
        description="List users in the Notion workspace with optional pagination.",
        parameters=[
            ToolParameter(name="start_cursor", type=ParameterType.STRING, description="Pagination cursor", required=False),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Number of users to return (max 100)", required=False),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def list_users(
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
            response = await self.client.list_users(
                    start_cursor=start_cursor,
                    page_size=page_size
                )
            return self._handle_response(response, "Users listed successfully")

        except Exception as e:
            logger.error(f"Error listing users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/retrieve_user",
        short_description="Retrieve a Notion user by ID",
        description="Retrieve a specific Notion user by their user ID.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="The user ID to retrieve"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def retrieve_user(self, user_id: str) -> Tuple[bool, str]:
        """Retrieve a Notion user by ID.

        Args:
            user_id: The user ID to retrieve

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = await self.client.retrieve_user(user_id=user_id)
            return self._handle_response(response, "User retrieved successfully")

        except Exception as e:
            logger.error(f"Error retrieving user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/create_database",
        short_description="Create a new database in Notion",
        description=(
            "Create a new database in Notion. You MUST have a valid parent_id (page ID) before calling this tool. "
            "Databases can only be created inside pages, not inside other databases. "
            "If you don't have a parent page ID, use notion.search to find existing pages first, or ask the user to provide it. "
            "Valid parent_id format: 32-character UUID with dashes or 32-character hex string. "
            "Do not use placeholder IDs - always get a real page ID first."
        ),
        parameters=[
            ToolParameter(name="parent_id", type=ParameterType.STRING, description="The ID of the parent PAGE where this database will be created"),
            ToolParameter(name="title", type=ParameterType.STRING, description="The title/name of the database to create"),
            ToolParameter(name="properties", type=ParameterType.OBJECT, description="Database properties schema defining columns"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="create")],
    )
    async def create_database(
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
                # Notion-Version 2025-09-03: the schema belongs to the database's first data source.
                "initial_data_source": {"properties": properties},
            }

            response = await self.client.create_database(request_body=request_body)
            return self._handle_response(response, "Database created successfully")

        except Exception as e:
            logger.error(f"Error creating database: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/query_database",
        short_description="Query a Notion database",
        description="Query a Notion database with optional filter, sort, and pagination.",
        parameters=[
            ToolParameter(name="database_id", type=ParameterType.STRING, description="The ID of the database to query (its first data source is queried); a data source ID is also accepted"),
            ToolParameter(name="filter", type=ParameterType.OBJECT, description="Filter configuration", required=False),
            ToolParameter(name="sorts", type=ParameterType.ARRAY, description="Sort configuration", required=False),
            ToolParameter(name="start_cursor", type=ParameterType.STRING, description="Pagination cursor", required=False),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Number of results (max 100)", required=False),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def query_database(
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

            # Notion-Version 2025-09-03 queries rows through a data source.
            data_source_id, _ = await self._first_data_source(database_id)

            response = await self.client.query_data_source_by_id(
                    data_source_id=data_source_id,
                    request_body=request_body
                )
            return self._handle_response(response, "Database queried successfully")

        except Exception as e:
            logger.error(f"Error querying database: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/get_database",
        short_description="Get a Notion database by ID",
        description="Retrieve a Notion database by its ID, with the columns (properties) of its first data source.",
        parameters=[
            ToolParameter(name="database_id", type=ParameterType.STRING, description="The ID of the database to retrieve"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_database(self, database_id: str) -> Tuple[bool, str]:
        """Get a Notion database by ID.

        Args:
            database_id: The ID of the database

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = await self.client.retrieve_database(database_id=database_id)
            database = self._json(response) if response.success else None
            if not isinstance(database, dict) or database.get("object") == "error":
                return self._handle_response(response, "")
            # Since 2025-09-03 the columns are on the data source: return the first one's with the database.
            sources = database.get("data_sources") or []
            if sources:
                source = await self.client.retrieve_data_source_by_id(data_source_id=sources[0]["id"])
                schema = self._json(source) if source.success else None
                if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
                    database = {**database, "properties": schema["properties"]}
            return True, json.dumps({"message": "Database retrieved successfully", "data": database})

        except Exception as e:
            logger.error(f"Error getting database: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/notion/get_page_content",
        short_description="Read the content of a Notion page",
        description=(
            "Read the blocks of a Notion page (or of a block with children) as plain text, in order. "
            "Each item has the block id, its type, its text and whether it has nested children "
            "(read those by calling this tool with the block id)."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page (or block) to read"),
            ToolParameter(name="max_blocks", type=ParameterType.INTEGER, description="Most blocks to return (default 500)", required=False),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_page_content(self, page_id: str, max_blocks: int | None = 500) -> tuple[bool, str]:
        """Read a page's blocks as plain text, following the block-children cursor.

        Args:
            page_id: The ID of the page or block
            max_blocks: Most blocks to return

        Returns:
            Tuple of (success, json_response)
        """
        try:
            limit = max(1, int(max_blocks or 500))
            blocks: list = []
            cursor: str | None = None
            while len(blocks) < limit:
                response = await self.client.retrieve_block_children(
                    block_id=page_id, start_cursor=cursor, page_size=_MAX_CHILDREN)
                body = self._json(response) if response.success else None
                if not isinstance(body, dict) or body.get("object") == "error":
                    return self._handle_response(response, "")
                for block in body.get("results") or []:
                    kind = block.get("type", "")
                    value = block.get(kind) or {}
                    text = "".join(t.get("plain_text", "") for t in value.get("rich_text") or []) \
                        if isinstance(value, dict) else ""
                    if not text and isinstance(value, dict):
                        text = value.get("title") or value.get("url") or value.get("expression") or ""
                    blocks.append({"id": block.get("id"), "type": kind, "text": text,
                                   "has_children": bool(block.get("has_children"))})
                cursor = body.get("next_cursor")
                if not body.get("has_more") or not cursor:
                    break
            return True, json.dumps({"message": "Page content retrieved successfully",
                                     "data": {"blocks": blocks[:limit], "truncated": len(blocks) > limit or
                                              bool(cursor and len(blocks) >= limit)}})

        except Exception as e:
            logger.error(f"Error getting page content: {e}")
            return False, json.dumps({"error": str(e)})
