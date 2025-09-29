import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.sources.external.notion.notion import NotionDataSource

logger = logging.getLogger(__name__)


class Notion:
    """Notion tool exposed to the agents using NotionDataSource"""

    def __init__(self, client: object) -> None:
        """Initialize the Notion tool"""
        """
        Args:
            client: Notion client object
        Returns:
            None
        """
        self.client = NotionDataSource(client)

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    def _safe_payload(self, value):  # noqa: ANN001
        """Convert NotionResponse.data (often an HTTPResponse) to JSON-serializable payload."""
        try:
            # Already JSON-serializable types
            if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
                return value

            # Try common response interfaces
            if hasattr(value, "json") and callable(getattr(value, "json")):
                try:
                    return value.json()
                except Exception:
                    pass

            if hasattr(value, "text"):
                return {"text": getattr(value, "text", "")}

            # Fallback to string
            return {"raw": str(value)}
        except Exception as e:
            logger.error(f"Failed to normalize Notion payload: {e}")
            return {"raw": str(value)}

    def _maybe_error(self, response):  # noqa: ANN001
        """Heuristic to detect HTTP error responses wrapped in success=True."""
        data = getattr(response, "data", None)
        status_code = getattr(data, "status_code", None) or getattr(data, "status", None)
        if isinstance(status_code, int) and status_code >= 400:
            # Extract body for context
            body = None
            try:
                if hasattr(data, "json") and callable(getattr(data, "json")):
                    body = data.json()
                elif hasattr(data, "text"):
                    body = data.text
            except Exception:
                body = str(data)
            return True, status_code, body
        return False, None, None

    @tool(app_name="notion", tool_name="create_page")
    def create_page(
        self,
        parent_id: str,
        title: str,
        content: Optional[str] = None,
        parent_is_database: Optional[bool] = False,
        title_property: Optional[str] = "title",
    ) -> Tuple[bool, str]:
        """Create a page in Notion"""
        """
        Args:
            parent_id: The ID of the parent page or database
            title: The title of the page
            content: The content of the page
        Returns:
            Tuple[bool, str]: True if the page is created, False otherwise
        """
        try:
            # Use NotionDataSource method - create_page expects a request_body
            # Build parent for page or database creation
            parent_block = {"database_id": parent_id} if parent_is_database else {"page_id": parent_id}

            request_body = {
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

            response = self._run_async(self.client.create_page(request_body=request_body))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "Page created successfully", "page": self._safe_payload(response.data)})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in create_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(app_name="notion", tool_name="get_page")
    def get_page(
        self,
        page_id: str,
    ) -> Tuple[bool, str]:
        """Get a page from Notion"""
        """
        Args:
            page_id: The ID of the page to get
        Returns:
            Tuple[bool, str]: True if the page is retrieved, False otherwise
        """
        try:
            # Use NotionDataSource method
            response = self._run_async(self.client.retrieve_page(page_id=page_id))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "Page retrieved successfully", "page": self._safe_payload(response.data)})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in get_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(app_name="notion", tool_name="update_page")
    def update_page(
        self,
        page_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Update a page in Notion"""
        """
        Args:
            page_id: The ID of the page to update
            title: The new title of the page
            content: The new content of the page
        Returns:
            Tuple[bool, str]: True if the page is updated, False otherwise
        """
        try:
            # Use NotionDataSource method - update_page_properties expects a request_body
            request_body = {}

            # Add title if provided
            if title:
                request_body["properties"] = {
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

            # Note: Content updates require using blocks API, not page properties
            # For now, we'll only update the title property
            if not request_body:
                return False, json.dumps({"error": "No properties to update"})

            response = self._run_async(self.client.update_page_properties(
                page_id=page_id,
                request_body=request_body
            ))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "Page updated successfully", "page": self._safe_payload(response.data)})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in update_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(app_name="notion", tool_name="delete_page")
    def delete_page(
        self,
        page_id: str,
    ) -> Tuple[bool, str]:
        """Delete a page from Notion"""
        """
        Args:
            page_id: The ID of the page to delete
        Returns:
            Tuple[bool, str]: True if the page is deleted, False otherwise
        """
        try:
            # Use NotionDataSource method - pages are blocks in Notion API
            response = self._run_async(self.client.delete_block(block_id=page_id))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "Page deleted successfully"})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in delete_page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(app_name="notion", tool_name="search")
    def search(
        self,
        query: Optional[str] = None,
        sort: Optional[dict] = None,
        filter: Optional[dict] = None,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Search Notion pages/databases shared with the integration"""
        """
        Args:
            query: Full-text query to filter results
            sort: Sort object per Notion API
            filter: Filter object per Notion API
            start_cursor: Pagination cursor
            page_size: Page size (max 100)
        Returns:
            Tuple[bool, str]: Success flag and JSON payload
        """
        try:
            request_body: dict = {}
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

            response = self._run_async(self.client.search(request_body=request_body))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "Search successful", "results": self._safe_payload(response.data)})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in search: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(app_name="notion", tool_name="list_users")
    def list_users(
        self,
        start_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """List users in the Notion workspace"""
        """
        Args:
            start_cursor: Pagination cursor
            page_size: Number of items to return (max 100)
        Returns:
            Tuple[bool, str]: Success flag and JSON payload
        """
        try:
            response = self._run_async(self.client.list_users(
                start_cursor=start_cursor,
                page_size=page_size
            ))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "Users listed successfully", "users": self._safe_payload(response.data)})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in list_users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(app_name="notion", tool_name="retrieve_user")
    def retrieve_user(
        self,
        user_id: str,
    ) -> Tuple[bool, str]:
        """Retrieve a Notion user by ID"""
        """
        Args:
            user_id: The Notion user ID to retrieve
        Returns:
            Tuple[bool, str]: Success flag and JSON payload
        """
        try:
            response = self._run_async(self.client.retrieve_user(user_id=user_id))

            is_err, code, body = self._maybe_error(response)
            if response.success and not is_err:
                return True, json.dumps({"message": "User retrieved successfully", "user": self._safe_payload(response.data)})
            else:
                err = response.error or {"status_code": code, "body": body}
                return False, json.dumps({"error": err})
        except Exception as e:
            logger.error(f"Error in retrieve_user: {e}")
            return False, json.dumps({"error": str(e)})
