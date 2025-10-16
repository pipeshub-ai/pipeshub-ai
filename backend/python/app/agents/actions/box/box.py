import asyncio
import base64
import io
import json
import logging
import threading
from typing import Coroutine, Optional, Tuple

from box_sdk_gen.managers.files import UpdateFileByIdParent
from box_sdk_gen.managers.uploads import UploadFileAttributes

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.box.box import BoxClient
from app.sources.external.box.box import BoxDataSource

logger = logging.getLogger(__name__)


class Box:
    """Box tools exposed to agents using BoxDataSource.

    This provides CRUD operations for Box files and a search tool,
    following the same pattern as Confluence and Google Meet tools.
    """

    def __init__(self, client: BoxClient) -> None:
        """Initialize the Box tool with a data source wrapper.

        Args:
            client: An initialized `BoxClient` instance
        """
        self.client = BoxDataSource(client)
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
            logger.warning(f"Box shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle Box response and return standardized format."""
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
        app_name="box",
        tool_name="get_file",
        description="Get file information by ID from Box",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="The ID of the file to retrieve"
            )
        ],
        returns="JSON with file data"
    )
    def get_file(self, file_id: str) -> Tuple[bool, str]:
        """Get file information by ID from Box."""
        try:
            response = self._run_async(
                self.client.files_get_file_by_id(file_id=file_id)
            )
            return self._handle_response(response, "File retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="update_file",
        description="Update file information in Box",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="The ID of the file to update"
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The new name of the file",
                required=False
            ),
            ToolParameter(
                name="parent_id",
                type=ParameterType.STRING,
                description="The new parent folder ID",
                required=False
            )
        ],
        returns="JSON with file update result"
    )
    def update_file(
        self,
        file_id: str,
        name: Optional[str] = None,
        parent_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Update file information in Box."""
        try:
            # Create parent object if parent_id is provided
            parent = None
            if parent_id:
                parent = UpdateFileByIdParent(id=parent_id)

            response = self._run_async(
                self.client.files_update_file_by_id(
                    file_id=file_id,
                    name=name,
                    parent=parent
                )
            )
            return self._handle_response(response, "File updated successfully")
        except Exception as e:
            logger.error(f"Error updating file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="delete_file",
        description="Delete a file from Box",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="The ID of the file to delete"
            )
        ],
        returns="JSON with deletion result"
    )
    def delete_file(self, file_id: str) -> Tuple[bool, str]:
        """Delete a file from Box."""
        try:
            response = self._run_async(
                self.client.files_delete_file_by_id(file_id=file_id)
            )
            return self._handle_response(response, "File deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="upload_file",
        description="Upload a file to Box",
        parameters=[
            ToolParameter(
                name="file_name",
                type=ParameterType.STRING,
                description="The name of the file to upload"
            ),
            ToolParameter(
                name="parent_folder_id",
                type=ParameterType.STRING,
                description="The ID of the parent folder"
            ),
            ToolParameter(
                name="file_content",
                type=ParameterType.STRING,
                description="Base64 encoded file content"
            ),
            ToolParameter(
                name="file_description",
                type=ParameterType.STRING,
                description="Description of the file",
                required=False
            )
        ],
        returns="JSON with upload result"
    )
    def upload_file(
        self,
        file_name: str,
        parent_folder_id: str,
        file_content: str,
        file_description: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Upload a file to Box."""
        try:

            # Decode base64 content
            try:
                decoded_content = base64.b64decode(file_content)
            except Exception as exc:
                return False, json.dumps({"error": f"Invalid base64 content: {exc}"})

            # Create file-like object
            file_obj = io.BytesIO(decoded_content)

            # Create upload attributes
            attributes = UploadFileAttributes(
                name=file_name,
                parent=UploadFileAttributes.Parent(id=parent_folder_id)
            )
            if file_description:
                attributes.description = file_description

            response = self._run_async(
                self.client.uploads_upload_file(
                    attributes=attributes,
                    file=file_obj
                )
            )
            return self._handle_response(response, "File uploaded successfully")
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="search_content",
        description="Search for content in Box",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query string"
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            ),
            ToolParameter(
                name="offset",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            ),
            ToolParameter(
                name="scope",
                type=ParameterType.STRING,
                description="Search scope (e.g., 'user_content', 'enterprise_content')",
                required=False
            ),
            ToolParameter(
                name="file_extensions",
                type=ParameterType.STRING,
                description="JSON array of file extensions to filter by: ['pdf', 'docx']",
                required=False
            ),
            ToolParameter(
                name="content_types",
                type=ParameterType.STRING,
                description="JSON array of content types: ['name', 'description']",
                required=False
            ),
            ToolParameter(
                name="ancestor_folder_ids",
                type=ParameterType.STRING,
                description="JSON array of folder IDs to search within: ['123', '456']",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_content(
        self,
        query: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        scope: Optional[str] = None,
        file_extensions: Optional[str] = None,
        content_types: Optional[str] = None,
        ancestor_folder_ids: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Search for content in Box."""
        try:
            # Parse JSON arrays if provided
            file_ext_list = None
            if file_extensions:
                try:
                    file_ext_list = json.loads(file_extensions)
                    if not isinstance(file_ext_list, list):
                        raise ValueError("file_extensions must be a JSON array")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for file_extensions: {exc}"})

            content_types_list = None
            if content_types:
                try:
                    content_types_list = json.loads(content_types)
                    if not isinstance(content_types_list, list):
                        raise ValueError("content_types must be a JSON array")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for content_types: {exc}"})

            ancestor_folder_ids_list = None
            if ancestor_folder_ids:
                try:
                    ancestor_folder_ids_list = json.loads(ancestor_folder_ids)
                    if not isinstance(ancestor_folder_ids_list, list):
                        raise ValueError("ancestor_folder_ids must be a JSON array")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for ancestor_folder_ids: {exc}"})

            response = self._run_async(
                self.client.search_search_for_content(
                    query=query,
                    limit=limit,
                    offset=offset,
                    scope=scope,
                    file_extensions=file_ext_list,
                    content_types=content_types_list,
                    ancestor_folder_ids=ancestor_folder_ids_list
                )
            )
            return self._handle_response(response, "Search completed successfully")
        except Exception as e:
            logger.error(f"Error searching content: {e}")
            return False, json.dumps({"error": str(e)})
