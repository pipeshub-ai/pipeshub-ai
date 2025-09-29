import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.box.box import BoxDataSource

logger = logging.getLogger(__name__)


class Box:
    """Box tool exposed to the agents"""
    def __init__(self, client: object) -> None:
        """Initialize the Box tool"""
        """
        Args:
            client: Box client object
        Returns:
            None
        """
        self.client = BoxDataSource(client)

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

    @tool(
        app_name="box",
        tool_name="get_folder",
        description="Get a folder from Box",
        parameters=[
            ToolParameter(
                name="folder_id",
                type=ParameterType.STRING,
                description="ID of the folder",
                required=True
            ),
            ToolParameter(
                name="fields",
                type=ParameterType.STRING,
                description="Fields to include in the response",
                required=False
            )
        ]
    )
    def get_folder(
        self,
        folder_id: str,
        fields: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get a folder from Box"""
        """
        Args:
            folder_id: ID of the folder
            fields: Fields to include in the response
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.get_folder(
                folder_id=folder_id,
                fields=fields
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting folder: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="list_folder_items",
        description="List items in a Box folder",
        parameters=[
            ToolParameter(
                name="folder_id",
                type=ParameterType.STRING,
                description="ID of the folder",
                required=True
            ),
            ToolParameter(
                name="fields",
                type=ParameterType.STRING,
                description="Fields to include in the response",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of items to return",
                required=False
            ),
            ToolParameter(
                name="offset",
                type=ParameterType.INTEGER,
                description="Number of items to skip",
                required=False
            )
        ]
    )
    def list_folder_items(
        self,
        folder_id: str,
        fields: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> Tuple[bool, str]:
        """List items in a Box folder"""
        """
        Args:
            folder_id: ID of the folder
            fields: Fields to include in the response
            limit: Maximum number of items to return
            offset: Number of items to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.list_folder_items(
                folder_id=folder_id,
                fields=fields,
                limit=limit,
                offset=offset
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing folder items: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="create_folder",
        description="Create a folder in Box",
        parameters=[
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="Name of the folder",
                required=True
            ),
            ToolParameter(
                name="parent_id",
                type=ParameterType.STRING,
                description="ID of the parent folder",
                required=True
            )
        ]
    )
    def create_folder(
        self,
        name: str,
        parent_id: str
    ) -> Tuple[bool, str]:
        """Create a folder in Box"""
        """
        Args:
            name: Name of the folder
            parent_id: ID of the parent folder
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.create_folder(
                name=name,
                parent_id=parent_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error creating folder: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="upload_file",
        description="Upload a file to Box",
        parameters=[
            ToolParameter(
                name="file_path",
                type=ParameterType.STRING,
                description="Path to the file to upload",
                required=True
            ),
            ToolParameter(
                name="parent_id",
                type=ParameterType.STRING,
                description="ID of the parent folder",
                required=True
            ),
            ToolParameter(
                name="file_name",
                type=ParameterType.STRING,
                description="Name for the uploaded file",
                required=False
            )
        ]
    )
    def upload_file(
        self,
        file_path: str,
        parent_id: str,
        file_name: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Upload a file to Box"""
        """
        Args:
            file_path: Path to the file to upload
            parent_id: ID of the parent folder
            file_name: Name for the uploaded file
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.upload_file(
                file_path=file_path,
                parent_id=parent_id,
                file_name=file_name
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="download_file",
        description="Download a file from Box",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="ID of the file",
                required=True
            ),
            ToolParameter(
                name="download_path",
                type=ParameterType.STRING,
                description="Path to save the downloaded file",
                required=True
            )
        ]
    )
    def download_file(
        self,
        file_id: str,
        download_path: str
    ) -> Tuple[bool, str]:
        """Download a file from Box"""
        """
        Args:
            file_id: ID of the file
            download_path: Path to save the downloaded file
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.download_file(
                file_id=file_id,
                download_path=download_path
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="delete_file",
        description="Delete a file from Box",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="ID of the file",
                required=True
            )
        ]
    )
    def delete_file(self, file_id: str) -> Tuple[bool, str]:
        """Delete a file from Box"""
        """
        Args:
            file_id: ID of the file
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.delete_file(file_id=file_id))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="search",
        description="Search for items in Box",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True
            ),
            ToolParameter(
                name="scope",
                type=ParameterType.STRING,
                description="Scope of the search",
                required=False
            ),
            ToolParameter(
                name="file_extensions",
                type=ParameterType.STRING,
                description="File extensions to search for",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            )
        ]
    )
    def search(
        self,
        query: str,
        scope: Optional[str] = None,
        file_extensions: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Search for items in Box"""
        """
        Args:
            query: Search query
            scope: Scope of the search
            file_extensions: File extensions to search for
            limit: Maximum number of results to return
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.search(
                query=query,
                scope=scope,
                file_extensions=file_extensions,
                limit=limit
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="box",
        tool_name="get_user",
        description="Get current user information",
        parameters=[
            ToolParameter(
                name="fields",
                type=ParameterType.STRING,
                description="Fields to include in the response",
                required=False
            )
        ]
    )
    def get_user(self, fields: Optional[str] = None) -> Tuple[bool, str]:
        """Get current user information"""
        """
        Args:
            fields: Fields to include in the response
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use BoxDataSource method
            response = self._run_async(self.client.get_user(fields=fields))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return False, json.dumps({"error": str(e)})
