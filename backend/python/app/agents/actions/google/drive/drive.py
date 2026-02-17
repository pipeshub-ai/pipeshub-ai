import asyncio
import json
import logging
from typing import Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.drive.drive import GoogleDriveDataSource

logger = logging.getLogger(__name__)

# Pydantic schemas for Google Drive tools
class GetFilesListInput(BaseModel):
    """Schema for getting files list"""
    corpora: Optional[str] = Field(default=None, description="Bodies of items to query")
    drive_id: Optional[str] = Field(default=None, description="ID of the shared drive to search")
    order_by: Optional[str] = Field(default=None, description="Sort keys")
    page_size: Optional[int] = Field(default=None, description="Maximum number of files to return per page")
    page_token: Optional[str] = Field(default=None, description="Token for pagination")
    query: Optional[str] = Field(default=None, description="Search query for filtering files")
    spaces: Optional[str] = Field(default=None, description="Spaces to query")


class GetFileDetailsInput(BaseModel):
    """Schema for getting file details"""
    fileId: Optional[str] = Field(default=None, description="The ID of the file to get details for")
    acknowledge_abuse: Optional[bool] = Field(default=None, description="Whether to acknowledge risk of downloading malware")
    supports_all_drives: Optional[bool] = Field(default=None, description="Whether requesting app supports both My Drives and shared drives")


class CreateFolderInput(BaseModel):
    """Schema for creating a folder"""
    folderName: Optional[str] = Field(default=None, description="The name of the folder to create")
    parent_folder_id: Optional[str] = Field(default=None, description="ID of parent folder")


class DeleteFileInput(BaseModel):
    """Schema for deleting a file"""
    file_id: str = Field(description="The ID of the file to delete")
    supports_all_drives: Optional[bool] = Field(default=None, description="Whether app supports shared drives")


class CopyFileInput(BaseModel):
    """Schema for copying a file"""
    file_id: str = Field(description="The ID of the file to copy")
    new_name: Optional[str] = Field(default=None, description="New name for the copied file")
    parent_folder_id: Optional[str] = Field(default=None, description="ID of parent folder for the copy")


class SearchFilesInput(BaseModel):
    """Schema for searching files"""
    query: str = Field(description="Search query (e.g., 'name contains \"report\"', 'mimeType=\"application/pdf\"')")
    page_size: Optional[int] = Field(default=None, description="Maximum number of results to return")
    order_by: Optional[str] = Field(default=None, description="Sort order")


class DownloadFileInput(BaseModel):
    """Schema for downloading a file"""
    fileId: Optional[str] = Field(default=None, description="The ID of the file to download")
    mimeType: Optional[str] = Field(default=None, description="MIME type for export (only used for Google Workspace documents)")


class UploadFileInput(BaseModel):
    """Schema for uploading a file"""
    file_name: Optional[str] = Field(default=None, description="Name of the file to upload")
    content: Optional[str] = Field(default=None, description="Content of the file to upload")
    mime_type: Optional[str] = Field(default=None, description="MIME type of the file")
    parent_folder_id: Optional[str] = Field(default=None, description="ID of parent folder")


class GetFilePermissionsInput(BaseModel):
    """Schema for getting file permissions"""
    file_id: str = Field(description="The ID of the file to get permissions for")
    page_size: Optional[int] = Field(default=None, description="Maximum number of permissions to return")


class GetSharedDrivesInput(BaseModel):
    """Schema for getting shared drives"""
    page_size: Optional[int] = Field(default=None, description="Maximum number of drives to return per page")
    query: Optional[str] = Field(default=None, description="Search query for shared drives")


# Register Google Drive toolset
@ToolsetBuilder("Drive")\
    .in_group("Google Workspace")\
    .with_description("Google Drive integration for file management, search, and collaboration")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Drive",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="toolsets/oauth/callback/drive",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file",
                    "https://www.googleapis.com/auth/drive.metadata.readonly"
                ]
            ),
            token_access_type="offline",
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            },
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/drive.svg",
            app_group="Google Workspace",
            app_description="Drive OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/drive.svg"))\
    .build_decorator()
class GoogleDrive:
    """Drive tool exposed to the agents using DriveDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Drive tool"""
        """
        Args:
            client: Authenticated Google Drive client
        Returns:
            None
        """
        self.client = GoogleDriveDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)

    @tool(
        app_name="drive",
        tool_name="get_files_list",
        description="Get list of files in Google Drive",
        args_schema=GetFilesListInput,
        when_to_use=[
            "User mentions 'Drive' or 'Google Drive'",
            "List/browse file requests",
            "'my files' with Drive context"
        ],
        when_not_to_use=[
            "No Drive mention (use retrieval)",
            "Search by content (use drive.search_files)",
            "Create files (use other tools)"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me my Drive files",
            "List all files in my Drive",
            "What files do I have in Drive?"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def get_files_list(
        self,
        corpora: Optional[str] = None,
        drive_id: Optional[str] = None,
        order_by: Optional[str] = None,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        query: Optional[str] = None,
        spaces: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get the list of files in Google Drive"""
        """
        Args:
            corpora: Bodies of items to query
            drive_id: ID of shared drive to search
            order_by: Sort order for results
            page_size: Number of files per page
            page_token: Pagination token
            query: Search query for filtering
            spaces: Spaces to query
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Format query if provided
            formatted_query = None
            if query:
                # Check for invalid query operators that Google Drive doesn't support
                invalid_operators = ['size=', 'size =', 'size>', 'size<', 'size>=', 'size<=']
                has_invalid_operator = any(op in query.lower() for op in invalid_operators)

                if has_invalid_operator:
                    # If query contains unsupported operators like size=, ignore the query
                    # and return all files (client-side filtering will be needed)
                    logger.warning(f"Query contains unsupported operator: {query}. Ignoring query parameter.")
                    formatted_query = None
                elif not any(op in query.lower() for op in ['name contains', 'mimetype', 'modifiedtime', 'createdtime', '=', 'trashed']):
                    # If it's a simple text query, wrap it in name contains
                    formatted_query = f'name contains "{query}"'
                else:
                    # Clean up the query - remove spaces around operators
                    formatted_query = query.replace(' = ', '=').replace(' =', '=').replace('= ', '=')

            # Use GoogleDriveDataSource method
            files = self._run_async(self.client.files_list(
                corpora=corpora,
                driveId=drive_id,
                orderBy=order_by,
                pageSize=page_size,
                pageToken=page_token,
                q=formatted_query,
                spaces=spaces
            ))

            # Get files list
            files_list = files.get("files", [])

            # Apply client-side filtering if size query was detected
            if query and formatted_query is None and any(op in query.lower() for op in ['size=', 'size =', 'size>', 'size<', 'size>=', 'size<=']):
                files_list = self._filter_files_by_size(files_list, query)

            # Prepare response data
            response_data = {
                "files": files_list,
                "nextPageToken": files.get("nextPageToken", None),
                "totalResults": len(files_list),
                "original_query": query,
                "formatted_query": formatted_query
            }

            # Add warning if query was ignored
            if query and formatted_query is None:
                response_data["warning"] = f"Query '{query}' contains unsupported operators and was processed with client-side filtering."

            return True, json.dumps(response_data)
        except Exception as e:
            logger.error(f"Failed to get files list: {e}")
            return False, json.dumps({"error": str(e)})

    def _filter_files_by_size(self, files: list, size_condition: str) -> list:
        """Helper method to filter files by size client-side since Google Drive API doesn't support size queries"""
        try:
            # Parse size condition (e.g., "size=0", "size>1000", "size<=500")
            import re

            # Extract operator and value
            match = re.match(r'size\s*([><=]+)\s*(\d+)', size_condition.lower())
            if not match:
                return files

            operator = match.group(1).strip()
            value = int(match.group(2))

            filtered_files = []
            for file in files:
                file_size = int(file.get('size', 0))

                if operator == '=' and file_size == value:
                    filtered_files.append(file)
                elif operator == '>' and file_size > value:
                    filtered_files.append(file)
                elif operator == '>=' and file_size >= value:
                    filtered_files.append(file)
                elif operator == '<' and file_size < value:
                    filtered_files.append(file)
                elif operator == '<=' and file_size <= value:
                    filtered_files.append(file)

            return filtered_files

        except Exception as e:
            logger.error(f"Error filtering files by size: {e}")
            return files

    @tool(
        app_name="drive",
        tool_name="get_file_details",
        description="Get detailed information about a file",
        args_schema=GetFileDetailsInput,
        when_to_use=[
            "User wants details about a specific file",
            "User mentions 'Drive' + has file ID",
            "User asks about file metadata"
        ],
        when_not_to_use=[
            "User wants to list files (use get_files_list)",
            "User wants to search files (use search_files)",
            "User wants info ABOUT Drive (use retrieval)",
            "No Drive mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get details for file ID",
            "Show file information",
            "What is this file?"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def get_file_details(
        self,
        fileId: Optional[str] = None,
        acknowledge_abuse: Optional[bool] = None,
        supports_all_drives: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get detailed information about a specific file"""
        """
        Args:
            fileId: The ID of the file
            acknowledge_abuse: Whether to acknowledge malware risk
            supports_all_drives: Whether app supports shared drives
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Validate required parameters
            if not fileId:
                return False, json.dumps({
                    "error": "Missing required parameter: fileId is required for get_file_details"
                })

            # Use GoogleDriveDataSource method
            file = self._run_async(self.client.files_get(
                fileId=fileId,
                acknowledgeAbuse=acknowledge_abuse,
                supportsAllDrives=supports_all_drives
            ))

            return True, json.dumps(file)
        except Exception as e:
            logger.error(f"Failed to get file details for {fileId}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="create_folder",
        description="Create a new folder in Google Drive",
        args_schema=CreateFolderInput,
        when_to_use=[
            "User wants to create a folder",
            "User mentions 'Drive' + wants to create folder",
            "User asks to make a new folder"
        ],
        when_not_to_use=[
            "User wants to list files (use get_files_list)",
            "User wants to search files (use search_files)",
            "User wants info ABOUT Drive (use retrieval)",
            "No Drive mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create folder in Drive",
            "Make a new folder",
            "Add folder to Drive"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def create_folder(
        self,
        folderName: Optional[str] = None,
        parent_folder_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Create a new folder in Google Drive"""
        """
        Args:
            folderName: Name of the folder to create
            parent_folder_id: ID of parent folder
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Validate required parameters
            if not folderName:
                return False, json.dumps({
                    "error": "Missing required parameter: folderName is required for create_folder"
                })

            # Create folder metadata
            folder_metadata = {
                "name": folderName,
                "mimeType": "application/vnd.google-apps.folder"
            }
            if parent_folder_id:
                folder_metadata["parents"] = [parent_folder_id]

            # Use GoogleDriveDataSource method - pass body in kwargs
            folder = self._run_async(self.client.files_create(
                enforceSingleParent=True,
                ignoreDefaultVisibility=True,
                keepRevisionForever=False,
                ocrLanguage=None,
                supportsAllDrives=False,
                supportsTeamDrives=False,
                useContentAsIndexableText=False,
                **{"body": folder_metadata}  # Pass metadata as body in kwargs
            ))

            return True, json.dumps({
                "folder_id": folder.get("id", ""),
                "folder_name": folder.get("name", ""),
                "folder_parents": folder.get("parents", []),
                "folder_mimeType": folder.get("mimeType", ""),
                "folder_createdTime": folder.get("createdTime", ""),
                "folder_webViewLink": folder.get("webViewLink", "")
            })
        except Exception as e:
            logger.error(f"Failed to create folder {folderName}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="search_files",
        description="Search for files in Google Drive using query syntax",
        args_schema=SearchFilesInput,
        when_to_use=[
            "User wants to search for files by name/content in Drive",
            "User mentions 'Drive' + wants to search files",
            "User asks to find files matching criteria"
        ],
        when_not_to_use=[
            "User wants to list all files (use get_files_list)",
            "User wants info ABOUT Drive (use retrieval)",
            "No Drive mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for files named 'report' in Drive",
            "Find PDF files in my Drive",
            "Search Drive for documents"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def search_files(
        self,
        query: str,
        page_size: Optional[int] = None,
        order_by: Optional[str] = None
    ) -> tuple[bool, str]:
        """Search for files in Google Drive using query syntax"""
        """
        Args:
            query: Search query with Drive query syntax
            page_size: Maximum number of results
            order_by: Sort order for results
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Convert simple text queries to proper Google Drive query syntax
            if not any(op in query.lower() for op in ['name contains', 'mimetype', 'modifiedtime', 'createdtime', '=']):
                # If it's a simple text query, wrap it in name contains
                formatted_query = f'name contains "{query}"'
            else:
                formatted_query = query

            # Use GoogleDriveDataSource method
            files = self._run_async(self.client.files_list(
                q=formatted_query,
                pageSize=page_size,
                orderBy=order_by
            ))

            return True, json.dumps({
                "files": files.get("files", []),
                "nextPageToken": files.get("nextPageToken", None),
                "totalResults": len(files.get("files", [])),
                "query": query,
                "formatted_query": formatted_query
            })
        except Exception as e:
            logger.error(f"Failed to search files with query '{query}': {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="get_drive_info",
        description="Get information about the user's Drive",
        when_to_use=[
            "User wants Drive account/storage info",
            "User mentions 'Drive' + wants account details",
            "User asks about Drive storage/quota"
        ],
        when_not_to_use=[
            "User wants files (use get_files_list)",
            "User wants to search files (use search_files)",
            "User wants info ABOUT Drive (use retrieval)",
            "No Drive mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get my Drive info",
            "Show Drive storage quota",
            "What's my Drive account info?"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def get_drive_info(self) -> tuple[bool, str]:
        """Get information about the user's Drive"""
        """
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleDriveDataSource method
            about = self._run_async(self.client.about_get())

            return True, json.dumps({
                "user": about.get("user", {}),
                "storageQuota": about.get("storageQuota", {}),
                "maxUploadSize": about.get("maxUploadSize", ""),
                "appInstalled": about.get("appInstalled", False),
                "exportFormats": about.get("exportFormats", {}),
                "importFormats": about.get("importFormats", {})
            })
        except Exception as e:
            logger.error(f"Failed to get drive info: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="get_shared_drives",
        description="Get list of shared drives",
        args_schema=GetSharedDrivesInput,
        when_to_use=[
            "User wants to list shared/team drives",
            "User mentions 'Drive' + wants shared drives",
            "User asks for team drives"
        ],
        when_not_to_use=[
            "User wants files (use get_files_list)",
            "User wants to search files (use search_files)",
            "User wants info ABOUT Drive (use retrieval)",
            "No Drive mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List shared drives",
            "Show team drives in Drive",
            "Get all shared drives"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def get_shared_drives(
        self,
        page_size: Optional[int] = None,
        query: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get list of shared drives"""
        """
        Args:
            page_size: Maximum number of drives to return
            query: Search query for shared drives
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleDriveDataSource method
            drives = self._run_async(self.client.drives_list(
                pageSize=page_size,
                q=query
            ))

            return True, json.dumps({
                "drives": drives.get("drives", []),
                "nextPageToken": drives.get("nextPageToken", None),
                "totalResults": len(drives.get("drives", []))
            })
        except Exception as e:
            logger.error(f"Failed to get shared drives: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="get_file_permissions",
        description="Get permissions for a specific file",
        args_schema=GetFilePermissionsInput,
        when_to_use=[
            "User wants to see file permissions/sharing",
            "User mentions 'Drive' + wants file permissions",
            "User asks who has access to file"
        ],
        when_not_to_use=[
            "User wants file details (use get_file_details)",
            "User wants to list files (use get_files_list)",
            "User wants info ABOUT Drive (use retrieval)",
            "No Drive mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get permissions for file",
            "Show who has access to file",
            "What are file permissions?"
        ],
        category=ToolCategory.FILE_STORAGE
    )
    def get_file_permissions(
        self,
        file_id: str,
        page_size: Optional[int] = None
    ) -> tuple[bool, str]:
        """Get permissions for a specific file"""
        """
        Args:
            file_id: The ID of the file
            page_size: Maximum number of permissions to return
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleDriveDataSource method
            permissions = self._run_async(self.client.permissions_list(
                fileId=file_id,
                pageSize=page_size
            ))

            return True, json.dumps({
                "permissions": permissions.get("permissions", []),
                "nextPageToken": permissions.get("nextPageToken", None),
                "file_id": file_id
            })
        except Exception as e:
            logger.error(f"Failed to get permissions for file {file_id}: {e}")
            return False, json.dumps({"error": str(e)})


    # @tool(
    #     app_name="drive",
    #     tool_name="download_file",
    #     description="Download file content from Google Drive",
    #     args_schema=DownloadFileInput,
    # )
    # def download_file(
    #     self,
    #     fileId: Optional[str] = None,
    #     mimeType: Optional[str] = None
    # ) -> tuple[bool, str]:
    #     """Download file content from Google Drive"""
    #     """
    #     Args:
    #         fileId: The ID of the file to download
    #         mimeType: MIME type for export (only used for Google Workspace documents)
    #     Returns:
    #         tuple[bool, str]: True if successful, False otherwise
    #     """
    #     try:
    #         # Validate required parameters
    #         if not fileId:
    #             return False, json.dumps({
    #                 "error": "Missing required parameter: fileId is required for download_file"
    #             })

    #         # First get file details to check if it's exportable
    #         file_details = self._run_async(self.client.files_get(fileId=fileId))
    #         file_mime_type = file_details.get("mimeType", "")
    #         file_name = file_details.get("name", "unknown")

    #         # Check if it's a Google Workspace document that can be exported
    #         if file_mime_type.startswith("application/vnd.google-apps"):
    #             # Use export functionality for Google Workspace docs
    #             if not mimeType:
    #                 # Default export formats for common Google Workspace types
    #                 export_formats = {
    #                     "application/vnd.google-apps.document": "text/plain",
    #                     "application/vnd.google-apps.spreadsheet": "text/csv",
    #                     "application/vnd.google-apps.presentation": "text/plain",
    #                     "application/vnd.google-apps.drawing": "image/png"
    #                 }
    #                 mimeType = export_formats.get(file_mime_type, "text/plain")

    #             # Export the file using data source
    #             content = self._run_async(self.client.files_export(
    #                 fileId=fileId,
    #                 mimeType=mimeType
    #             ))
    #         else:
    #             # Regular file download using data source - don't pass mimeType for regular files
    #             content = self._run_async(self.client.files_download(
    #                 fileId=fileId
    #             ))

    #         # Handle different content types
    #         if isinstance(content, bytes):
    #             # Try to decode as text
    #             try:
    #                 text_content = content.decode('utf-8')
    #             except UnicodeDecodeError:
    #                 # If it's binary, encode as base64
    #                 import base64
    #                 text_content = base64.b64encode(content).decode('utf-8')
    #                 return True, json.dumps({
    #                     "file_id": fileId,
    #                     "file_name": file_name,
    #                     "content_type": "binary",
    #                     "content": text_content,
    #                     "size": len(content),
    #                     "message": f"Downloaded binary file '{file_name}' (base64 encoded)"
    #                 })
    #         else:
    #             text_content = str(content)

    #         return True, json.dumps({
    #             "file_id": fileId,
    #             "file_name": file_name,
    #             "content_type": "text",
    #             "content": text_content,
    #             "size": len(text_content),
    #             "mime_type": mimeType or file_mime_type,
    #             "message": f"Downloaded file '{file_name}' successfully"
    #         })

    #     except Exception as e:
    #         logger.error(f"Failed to download file {fileId}: {e}")
    #         return False, json.dumps({"error": str(e)})

    # @tool(
    #     app_name="drive",
    #     tool_name="upload_file",
    #     description="Upload a file to Google Drive",
    #     args_schema=UploadFileInput,
    # )
    # def upload_file(
    #     self,
    #     file_name: Optional[str] = None,
    #     content: Optional[str] = None,
    #     mime_type: Optional[str] = None,
    #     parent_folder_id: Optional[str] = None
    # ) -> tuple[bool, str]:
    #     """Upload a file to Google Drive"""
    #     """
    #     Args:
    #         file_name: Name of the file to upload
    #         content: Content of the file
    #         mime_type: MIME type of the file
    #         parent_folder_id: ID of parent folder
    #     Returns:
    #         tuple[bool, str]: True if successful, False otherwise
    #     """
    #     try:
    #         # Validate required parameters
    #         if not file_name or not content:
    #             return False, json.dumps({
    #                 "error": "Missing required parameters: file_name and content are required for upload_file"
    #             })

    #         # Default MIME type
    #         if not mime_type:
    #             if file_name.endswith('.txt'):
    #                 mime_type = 'text/plain'
    #             elif file_name.endswith('.md'):
    #                 mime_type = 'text/markdown'
    #             elif file_name.endswith('.json'):
    #                 mime_type = 'application/json'
    #             else:
    #                 mime_type = 'text/plain'

    #         # Convert content to bytes
    #         content_bytes = content.encode('utf-8')

    #         # Create file metadata
    #         file_metadata = {
    #             'name': file_name,
    #             'mimeType': mime_type
    #         }
    #         if parent_folder_id:
    #             file_metadata['parents'] = [parent_folder_id]

    #         # Use GoogleDriveDataSource method for file upload with media
    #         file = self._run_async(self.client.files_create_with_media(
    #             file_metadata=file_metadata,
    #             content=content_bytes,
    #             mime_type=mime_type,
    #             enforceSingleParent=True,
    #             ignoreDefaultVisibility=True,
    #             keepRevisionForever=False,
    #             ocrLanguage=None,
    #             supportsAllDrives=False,
    #             supportsTeamDrives=False,
    #             useContentAsIndexableText=False
    #         ))

    #         return True, json.dumps({
    #             "file_id": file.get("id", ""),
    #             "file_name": file.get("name", ""),
    #             "mime_type": file.get("mimeType", ""),
    #             "web_view_link": file.get("webViewLink", ""),
    #             "parents": file.get("parents", []),
    #             "size": len(content_bytes),
    #             "message": f"File '{file_name}' uploaded successfully to Google Drive with content."
    #         })

    #     except Exception as e:
    #         logger.error(f"Failed to upload file '{file_name}': {e}")
    #         return False, json.dumps({"error": str(e)})

    # @tool(
    #     app_name="drive",
    #     tool_name="delete_file",
    #     description="Delete a file from Google Drive",
    #     args_schema=DeleteFileInput,
    # )
    # def delete_file(
    #     self,
    #     file_id: str,
    #     supports_all_drives: Optional[bool] = None
    # ) -> tuple[bool, str]:
    #     """Delete a file from Google Drive"""
    #     """
    #     Args:
    #         file_id: The ID of the file to delete
    #         supports_all_drives: Whether app supports shared drives
    #     Returns:
    #         tuple[bool, str]: True if successful, False otherwise
    #     """
    #     try:
    #         # Use GoogleDriveDataSource method
    #         self._run_async(self.client.files_delete(
    #             fileId=file_id,
    #             supportsAllDrives=supports_all_drives
    #         ))

    #         return True, json.dumps({
    #             "message": f"File {file_id} deleted successfully"
    #         })
    #     except Exception as e:
    #         logger.error(f"Failed to delete file {file_id}: {e}")
    #         return False, json.dumps({"error": str(e)})

    # @tool(
    #     app_name="drive",
    #     tool_name="copy_file",
    #     description="Copy a file in Google Drive",
    #     args_schema=CopyFileInput,
    # )
    # def copy_file(
    #     self,
    #     file_id: str,
    #     new_name: Optional[str] = None,
    #     parent_folder_id: Optional[str] = None
    # ) -> tuple[bool, str]:
    #     """Copy a file in Google Drive"""
    #     """
    #     Args:
    #         file_id: The ID of the file to copy
    #         new_name: New name for the copied file
    #         parent_folder_id: ID of parent folder for the copy
    #     Returns:
    #         tuple[bool, str]: True if successful, False otherwise
    #     """
    #     try:
    #         copy_metadata = {}
    #         if new_name:
    #             copy_metadata["name"] = new_name
    #         if parent_folder_id:
    #             copy_metadata["parents"] = [parent_folder_id]

    #         # Use GoogleDriveDataSource method - pass body as a parameter
    #         copied_file = self._run_async(self.client.files_copy(
    #             fileId=file_id,
    #             enforceSingleParent=True,
    #             ignoreDefaultVisibility=True,
    #             keepRevisionForever=False,
    #             ocrLanguage=None,
    #             supportsAllDrives=False,
    #             supportsTeamDrives=False,
    #             body=copy_metadata if copy_metadata else None
    #         ))

    #         return True, json.dumps({
    #             "copied_file_id": copied_file.get("id", ""),
    #             "copied_file_name": copied_file.get("name", ""),
    #             "copied_file_parents": copied_file.get("parents", []),
    #             "copied_file_mimeType": copied_file.get("mimeType", ""),
    #             "copied_file_webViewLink": copied_file.get("webViewLink", "")
    #         })
    #     except Exception as e:
    #         logger.error(f"Failed to copy file {file_id}: {e}")
    #         return False, json.dumps({"error": str(e)})
