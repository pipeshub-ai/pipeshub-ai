import asyncio
import json
import logging
import threading
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.dropbox.dropbox_ import DropboxClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.dropbox.dropbox_ import DropboxDataSource

logger = logging.getLogger(__name__)


class Dropbox:
    """Dropbox tool exposed to the agents"""
    def __init__(self, client: DropboxClient) -> None:
        """Initialize the Dropbox tool"""
        """
        Args:
            client: Dropbox client object
        Returns:
            None
        """
        self.client = DropboxDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(target=self._start_background_loop, daemon=True)
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop"""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Run a coroutine safely from sync or async contexts via a dedicated loop."""
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
            logger.warning(f"Dropbox shutdown encountered an issue: {exc}")

    @tool(
        app_name="dropbox",
        tool_name="get_account_info",
        description="Get current account information",
        parameters=[]
    )
    def get_account_info(self) -> Tuple[bool, str]:
        """Get current account information"""
        """
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.users_get_current_account())

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="list_folder",
        description="List contents of a folder",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path of the folder to list",
                required=True
            ),
            ToolParameter(
                name="recursive",
                type=ParameterType.BOOLEAN,
                description="Whether to list recursively",
                required=False
            ),
            ToolParameter(
                name="include_media_info",
                type=ParameterType.BOOLEAN,
                description="Whether to include media info",
                required=False
            ),
            ToolParameter(
                name="include_deleted",
                type=ParameterType.BOOLEAN,
                description="Whether to include deleted files",
                required=False
            )
        ]
    )
    def list_folder(
        self,
        path: str,
        recursive: Optional[bool] = None,
        include_media_info: Optional[bool] = None,
        include_deleted: Optional[bool] = None
    ) -> Tuple[bool, str]:
        """List contents of a folder"""
        """
        Args:
            path: Path of the folder to list
            recursive: Whether to list recursively
            include_media_info: Whether to include media info
            include_deleted: Whether to include deleted files
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.files_list_folder(
                path=path,
                recursive=recursive,
                include_media_info=include_media_info,
                include_deleted=include_deleted
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing folder: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="get_metadata",
        description="Get metadata for a file or folder",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path of the file or folder",
                required=True
            ),
            ToolParameter(
                name="include_media_info",
                type=ParameterType.BOOLEAN,
                description="Whether to include media info",
                required=False
            ),
            ToolParameter(
                name="include_deleted",
                type=ParameterType.BOOLEAN,
                description="Whether to include deleted files",
                required=False
            )
        ]
    )
    def get_metadata(
        self,
        path: str,
        include_media_info: Optional[bool] = None,
        include_deleted: Optional[bool] = None
    ) -> Tuple[bool, str]:
        """Get metadata for a file or folder"""
        """
        Args:
            path: Path of the file or folder
            include_media_info: Whether to include media info
            include_deleted: Whether to include deleted files
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.files_get_metadata(
                path=path,
                include_media_info=include_media_info,
                include_deleted=include_deleted
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting metadata: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="download_file",
        description="Download a file from Dropbox",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path of the file to download",
                required=True
            )
        ]
    )
    def download_file(self, path: str) -> Tuple[bool, str]:
        """Download a file from Dropbox"""
        """
        Args:
            path: Path of the file to download
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.files_download(path=path))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="upload_file",
        description="Upload a file to Dropbox",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path where to upload the file",
                required=True
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Content of the file to upload",
                required=True
            ),
            ToolParameter(
                name="mode",
                type=ParameterType.STRING,
                description="Write mode (add, overwrite, update)",
                required=False
            )
        ]
    )
    def upload_file(
        self,
        path: str,
        content: str,
        mode: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Upload a file to Dropbox"""
        """
        Args:
            path: Path where to upload the file
            content: Content of the file to upload
            mode: Write mode (add, overwrite, update)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Convert textual content to bytes as required by DataSource
            file_bytes = content.encode('utf-8')

            # Use DropboxDataSource method (expects bytes in 'f' argument)
            response = self._run_async(self.client.files_upload(
                f=file_bytes,
                path=path,
                mode=mode if mode is not None else 'add'
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="delete_file",
        description="Delete a file or folder from Dropbox",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path of the file or folder to delete",
                required=True
            )
        ]
    )
    def delete_file(self, path: str) -> Tuple[bool, str]:
        """Delete a file or folder from Dropbox"""
        """
        Args:
            path: Path of the file or folder to delete
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.files_delete_v2(path=path))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="create_folder",
        description="Create a folder in Dropbox",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path where to create the folder",
                required=True
            )
        ]
    )
    def create_folder(self, path: str) -> Tuple[bool, str]:
        """Create a folder in Dropbox"""
        """
        Args:
            path: Path where to create the folder
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.files_create_folder_v2(path=path))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error creating folder: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="search",
        description="Search for files and folders",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True
            ),
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path to search in",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of results",
                required=False
            )
        ]
    )
    def search(
        self,
        query: str,
        path: Optional[str] = None,
        max_results: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Search for files and folders"""
        """
        Args:
            query: Search query
            path: Path to search in
            max_results: Maximum number of results
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.files_search_v2(
                query=query,
                path=path,
                max_results=max_results
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="dropbox",
        tool_name="get_shared_link",
        description="Get a shared link for a file or folder",
        parameters=[
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="Path of the file or folder",
                required=True
            ),
            ToolParameter(
                name="settings",
                type=ParameterType.STRING,
                description="Settings for the shared link",
                required=False
            )
        ]
    )
    def get_shared_link(
        self,
        path: str,
        settings: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get a shared link for a file or folder"""
        """
        Args:
            path: Path of the file or folder
            settings: Settings for the shared link
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use DropboxDataSource method
            response = self._run_async(self.client.sharing_create_shared_link_with_settings(
                path=path,
                settings=settings
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting shared link: {e}")
            return False, json.dumps({"error": str(e)})
