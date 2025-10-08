import asyncio
import base64
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.one_drive.one_drive import OneDriveDataSource

logger = logging.getLogger(__name__)


class OneDrive:
    """OneDrive tool exposed to the agents"""
    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the OneDrive tool"""
        """
        Args:
            client: Microsoft Graph client object
        Returns:
            None
        """
        self.client = OneDriveDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
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
        app_name="onedrive",
        tool_name="get_drives",
        description="Get OneDrive drives",
        parameters=[
            ToolParameter(
                name="search",
                type=ParameterType.STRING,
                description="Search query for drives",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter query for drives",
                required=False
            ),
            ToolParameter(
                name="orderby",
                type=ParameterType.STRING,
                description="Order by field",
                required=False
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of results to return",
                required=False
            ),
            ToolParameter(
                name="skip",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            )
        ]
    )
    def get_drives(
        self,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        expand: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get OneDrive drives"""
        """
        Args:
            search: Search query for drives
            filter: Filter query for drives
            orderby: Order by field
            select: Select specific fields
            expand: Expand related entities
            top: Number of results to return
            skip: Number of results to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OneDriveDataSource method
            response = self._run_async(self.client.me_list_drives(
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                expand=expand,
                top=top,
                skip=skip
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_drives: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_drive",
        description="Get a specific OneDrive drive",
        parameters=[
            ToolParameter(
                name="drive_id",
                type=ParameterType.STRING,
                description="The ID of the drive to get",
                required=True
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            )
        ]
    )
    def get_drive(
        self,
        drive_id: str,
        select: Optional[str] = None,
        expand: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get a specific OneDrive drive"""
        """
        Args:
            drive_id: The ID of the drive to get
            select: Select specific fields
            expand: Expand related entities
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OneDriveDataSource method
            response = self._run_async(self.client.drives_drive_get_drive(
                drive_id=drive_id,
                select=select,
                expand=expand
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_drive: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_files",
        description="Get files from a OneDrive drive",
        parameters=[
            ToolParameter(
                name="drive_id",
                type=ParameterType.STRING,
                description="The ID of the drive",
                required=True
            ),
            ToolParameter(
                name="search",
                type=ParameterType.STRING,
                description="Search query for files",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter query for files",
                required=False
            ),
            ToolParameter(
                name="orderby",
                type=ParameterType.STRING,
                description="Order by field",
                required=False
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of results to return",
                required=False
            ),
            ToolParameter(
                name="skip",
                type=ParameterType.INTEGER,
                description="Number of results to skip",
                required=False
            )
        ]
    )
    def get_files(
        self,
        drive_id: str,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        expand: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Get files from a OneDrive drive"""
        """
        Args:
            drive_id: The ID of the drive
            search: Search query for files
            filter: Filter query for files
            orderby: Order by field
            select: Select specific fields
            expand: Expand related entities
            top: Number of results to return
            skip: Number of results to skip
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OneDriveDataSource method to get root children
            # In Microsoft Graph, "root" is the special identifier for the root item
            response = self._run_async(self.client.drives_items_list_children(
                drive_id=drive_id,
                driveItem_id="root",
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                expand=expand,
                top=top,
                skip=skip
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_files: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_file",
        description="Get a specific file from OneDrive",
        parameters=[
            ToolParameter(
                name="drive_id",
                type=ParameterType.STRING,
                description="The ID of the drive",
                required=True
            ),
            ToolParameter(
                name="item_id",
                type=ParameterType.STRING,
                description="The ID of the file",
                required=True
            ),
            ToolParameter(
                name="select",
                type=ParameterType.STRING,
                description="Select specific fields",
                required=False
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Expand related entities",
                required=False
            )
        ]
    )
    def get_file(
        self,
        drive_id: str,
        item_id: str,
        select: Optional[str] = None,
        expand: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get a specific file from OneDrive"""
        """
        Args:
            drive_id: The ID of the drive
            item_id: The ID of the file
            select: Select specific fields
            expand: Expand related entities
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OneDriveDataSource method
            response = self._run_async(self.client.drives_get_items(
                drive_id=drive_id,
                driveItem_id=item_id,
                select=select,
                expand=expand
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="upload_file",
        description="Upload a file to OneDrive",
        parameters=[
            ToolParameter(
                name="drive_id",
                type=ParameterType.STRING,
                description="The ID of the drive",
                required=True
            ),
            ToolParameter(
                name="file_name",
                type=ParameterType.STRING,
                description="Name of the file to upload",
                required=True
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Content of the file to upload",
                required=True
            ),
            ToolParameter(
                name="parent_folder_id",
                type=ParameterType.STRING,
                description="ID of parent folder (optional, defaults to root)",
                required=False
            )
        ]
    )
    def upload_file(
        self,
        drive_id: str,
        file_name: str,
        content: str,
        parent_folder_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Upload a file to OneDrive"""
        """
        Args:
            drive_id: The ID of the drive
            file_name: Name of the file to upload
            content: Content of the file to upload
            parent_folder_id: ID of parent folder (optional, defaults to root)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OneDriveDataSource method to create a file in the root folder
            parent_id = parent_folder_id or "root"  # Default to root if no parent specified
            response = self._run_async(self.client.drives_items_create_children(
                drive_id=drive_id,
                driveItem_id=parent_id,
                request_body={
                    "name": file_name,
                    "file": {},
                    "@microsoft.graph.sourceUrl": f"data:text/plain;base64,{base64.b64encode(content.encode('utf-8')).decode('utf-8')}"
                }
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in upload_file: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="delete_file",
        description="Delete a file from OneDrive",
        parameters=[
            ToolParameter(
                name="drive_id",
                type=ParameterType.STRING,
                description="The ID of the drive",
                required=True
            ),
            ToolParameter(
                name="item_id",
                type=ParameterType.STRING,
                description="The ID of the file to delete",
                required=True
            )
        ]
    )
    def delete_file(
        self,
        drive_id: str,
        item_id: str
    ) -> Tuple[bool, str]:
        """Delete a file from OneDrive"""
        """
        Args:
            drive_id: The ID of the drive
            item_id: The ID of the file to delete
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OneDriveDataSource method
            response = self._run_async(self.client.drives_delete_items(
                drive_id=drive_id,
                driveItem_id=item_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in delete_file: {e}")
            return False, json.dumps({"error": str(e)})
