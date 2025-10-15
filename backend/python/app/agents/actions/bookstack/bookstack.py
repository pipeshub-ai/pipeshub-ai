import asyncio
import json
import logging
import threading
from typing import Coroutine, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.bookstack.bookstack import BookStackClient
from app.sources.external.bookstack.bookstack import BookStackDataSource

logger = logging.getLogger(__name__)


class BookStack:
    """BookStack tools exposed to agents using BookStackDataSource.

    This provides CRUD operations for BookStack pages and a search tool,
    following the same pattern as Confluence and Google Meet tools.
    """

    def __init__(self, client: BookStackClient) -> None:
        """Initialize the BookStack tool with a data source wrapper.

        Args:
            client: An initialized `BookStackClient` instance
        """
        self.client = BookStackDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, object]) -> object:
        """Run a coroutine safely from sync context via a dedicated loop."""
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
            logger.warning(f"BookStack shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle BookStack response and return standardized format."""
        try:
            if hasattr(response, 'success') and response.success:
                return True, json.dumps({
                    "message": success_message,
                    "data": response.data or {}
                })
            else:
                error_msg = getattr(response, 'error', 'Unknown error')
                return False, json.dumps({"error": error_msg})
        except Exception as e:
            logger.error(f"Error handling response: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="bookstack",
        tool_name="create_page",
        description="Create a new page in BookStack",
        parameters=[
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The name/title of the page"
            ),
            ToolParameter(
                name="book_id",
                type=ParameterType.INTEGER,
                description="The ID of the book to create the page in",
                required=False
            ),
            ToolParameter(
                name="chapter_id",
                type=ParameterType.INTEGER,
                description="The ID of the chapter to create the page in",
                required=False
            ),
            ToolParameter(
                name="html",
                type=ParameterType.STRING,
                description="The HTML content of the page",
                required=False
            ),
            ToolParameter(
                name="markdown",
                type=ParameterType.STRING,
                description="The Markdown content of the page",
                required=False
            ),
            ToolParameter(
                name="tags",
                type=ParameterType.STRING,
                description="JSON array of tag objects: [{'name': 'tag1', 'value': 'value1'}]",
                required=False
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.INTEGER,
                description="Priority/order of the page",
                required=False
            )
        ],
        returns="JSON with page creation result"
    )
    def create_page(
        self,
        name: str,
        book_id: Optional[int] = None,
        chapter_id: Optional[int] = None,
        html: Optional[str] = None,
        markdown: Optional[str] = None,
        tags: Optional[str] = None,
        priority: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Create a new page in BookStack."""
        try:
            # Parse tags if provided
            tags_list = None
            if tags:
                try:
                    tags_list = json.loads(tags)
                    if not isinstance(tags_list, list):
                        raise ValueError("tags must be a JSON array")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for tags: {exc}"})

            response = self._run_async(
                self.client.create_page(
                    name=name,
                    book_id=book_id,
                    chapter_id=chapter_id,
                    html=html,
                    markdown=markdown,
                    tags=tags_list,
                    priority=priority
                )
            )
            return self._handle_response(response, "Page created successfully")
        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="bookstack",
        tool_name="get_page",
        description="Get a page by ID from BookStack",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.INTEGER,
                description="The ID of the page to retrieve"
            )
        ],
        returns="JSON with page data"
    )
    def get_page(self, page_id: int) -> Tuple[bool, str]:
        """Get a page by ID from BookStack."""
        try:
            response = self._run_async(
                self.client.get_page(page_id=page_id)
            )
            return self._handle_response(response, "Page retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="bookstack",
        tool_name="update_page",
        description="Update an existing page in BookStack",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.INTEGER,
                description="The ID of the page to update"
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The new name/title of the page",
                required=False
            ),
            ToolParameter(
                name="book_id",
                type=ParameterType.INTEGER,
                description="The new book ID",
                required=False
            ),
            ToolParameter(
                name="chapter_id",
                type=ParameterType.INTEGER,
                description="The new chapter ID",
                required=False
            ),
            ToolParameter(
                name="html",
                type=ParameterType.STRING,
                description="The new HTML content",
                required=False
            ),
            ToolParameter(
                name="markdown",
                type=ParameterType.STRING,
                description="The new Markdown content",
                required=False
            ),
            ToolParameter(
                name="tags",
                type=ParameterType.STRING,
                description="JSON array of tag objects: [{'name': 'tag1', 'value': 'value1'}]",
                required=False
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.INTEGER,
                description="New priority/order of the page",
                required=False
            )
        ],
        returns="JSON with page update result"
    )
    def update_page(
        self,
        page_id: int,
        name: Optional[str] = None,
        book_id: Optional[int] = None,
        chapter_id: Optional[int] = None,
        html: Optional[str] = None,
        markdown: Optional[str] = None,
        tags: Optional[str] = None,
        priority: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Update an existing page in BookStack."""
        try:
            # Parse tags if provided
            tags_list = None
            if tags:
                try:
                    tags_list = json.loads(tags)
                    if not isinstance(tags_list, list):
                        raise ValueError("tags must be a JSON array")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for tags: {exc}"})

            response = self._run_async(
                self.client.update_page(
                    page_id=page_id,
                    book_id=book_id,
                    chapter_id=chapter_id,
                    name=name,
                    html=html,
                    markdown=markdown,
                    tags=tags_list,
                    priority=priority
                )
            )
            return self._handle_response(response, "Page updated successfully")
        except Exception as e:
            logger.error(f"Error updating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="bookstack",
        tool_name="delete_page",
        description="Delete a page from BookStack",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.INTEGER,
                description="The ID of the page to delete"
            )
        ],
        returns="JSON with deletion result"
    )
    def delete_page(self, page_id: int) -> Tuple[bool, str]:
        """Delete a page from BookStack."""
        try:
            response = self._run_async(
                self.client.delete_page(page_id=page_id)
            )
            return self._handle_response(response, "Page deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="bookstack",
        tool_name="search_all",
        description="Search across all content in BookStack",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query string",
                required=False
            ),
            ToolParameter(
                name="page",
                type=ParameterType.INTEGER,
                description="Page number for pagination",
                required=False
            ),
            ToolParameter(
                name="count",
                type=ParameterType.INTEGER,
                description="Number of results per page",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_all(
        self,
        query: Optional[str] = None,
        page: Optional[int] = None,
        count: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Search across all content in BookStack."""
        try:
            response = self._run_async(
                self.client.search_all(
                    query=query,
                    page=page,
                    count=count
                )
            )
            return self._handle_response(response, "Search completed successfully")
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return False, json.dumps({"error": str(e)})
