import asyncio
import json
import logging
from typing import Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.drive.drive import GoogleDriveDataSource

logger = logging.getLogger(__name__)

class GoogleDrive:
    """Google Drive tool exposed to the agents using GoogleDriveDataSource"""
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
        parameters=[
            ToolParameter(
                name="corpora",
                type=ParameterType.STRING,
                description="Bodies of items to query (user, domain, drive, allDrives)",
                required=False
            ),
            ToolParameter(
                name="drive_id",
                type=ParameterType.STRING,
                description="ID of the shared drive to search",
                required=False
            ),
            ToolParameter(
                name="order_by",
                type=ParameterType.STRING,
                description="Sort keys: createdTime, modifiedTime, name, etc.",
                required=False
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of files to return per page",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Token for pagination to get next page of files",
                required=False
            ),
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query for filtering files",
                required=False
            ),
            ToolParameter(
                name="spaces",
                type=ParameterType.STRING,
                description="Spaces to query (drive, appDataFolder)",
                required=False
            )
        ]
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
        parameters=[
            ToolParameter(
                name="fileId",
                type=ParameterType.STRING,
                description="The ID of the file to get details for",
                required=False
            ),
            ToolParameter(
                name="acknowledge_abuse",
                type=ParameterType.BOOLEAN,
                description="Whether to acknowledge risk of downloading malware",
                required=False
            ),
            ToolParameter(
                name="supports_all_drives",
                type=ParameterType.BOOLEAN,
                description="Whether requesting app supports both My Drives and shared drives",
                required=False
            )
        ]
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
        parameters=[
            ToolParameter(
                name="folderName",
                type=ParameterType.STRING,
                description="The name of the folder to create",
                required=False
            ),
            ToolParameter(
                name="parent_folder_id",
                type=ParameterType.STRING,
                description="ID of parent folder (optional, defaults to root)",
                required=False
            )
        ]
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
        tool_name="delete_file",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="The ID of the file to delete",
                required=True
            ),
            ToolParameter(
                name="supports_all_drives",
                type=ParameterType.BOOLEAN,
                description="Whether requesting app supports both My Drives and shared drives",
                required=False
            )
        ]
    )
    def delete_file(
        self,
        file_id: str,
        supports_all_drives: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Delete a file from Google Drive"""
        """
        Args:
            file_id: The ID of the file to delete
            supports_all_drives: Whether app supports shared drives
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleDriveDataSource method
            self._run_async(self.client.files_delete(
                fileId=file_id,
                supportsAllDrives=supports_all_drives
            ))

            return True, json.dumps({
                "message": f"File {file_id} deleted successfully"
            })
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="copy_file",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="The ID of the file to copy",
                required=True
            ),
            ToolParameter(
                name="new_name",
                type=ParameterType.STRING,
                description="New name for the copied file",
                required=False
            ),
            ToolParameter(
                name="parent_folder_id",
                type=ParameterType.STRING,
                description="ID of parent folder for the copy",
                required=False
            )
        ]
    )
    def copy_file(
        self,
        file_id: str,
        new_name: Optional[str] = None,
        parent_folder_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Copy a file in Google Drive"""
        """
        Args:
            file_id: The ID of the file to copy
            new_name: New name for the copied file
            parent_folder_id: ID of parent folder for the copy
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            copy_metadata = {}
            if new_name:
                copy_metadata["name"] = new_name
            if parent_folder_id:
                copy_metadata["parents"] = [parent_folder_id]

            # Use GoogleDriveDataSource method - pass body as a parameter
            copied_file = self._run_async(self.client.files_copy(
                fileId=file_id,
                enforceSingleParent=True,
                ignoreDefaultVisibility=True,
                keepRevisionForever=False,
                ocrLanguage=None,
                supportsAllDrives=False,
                supportsTeamDrives=False,
                body=copy_metadata if copy_metadata else None
            ))

            return True, json.dumps({
                "copied_file_id": copied_file.get("id", ""),
                "copied_file_name": copied_file.get("name", ""),
                "copied_file_parents": copied_file.get("parents", []),
                "copied_file_mimeType": copied_file.get("mimeType", ""),
                "copied_file_webViewLink": copied_file.get("webViewLink", "")
            })
        except Exception as e:
            logger.error(f"Failed to copy file {file_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="search_files",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query (e.g., 'name contains \"report\"', 'mimeType=\"application/pdf\"')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            ),
            ToolParameter(
                name="order_by",
                type=ParameterType.STRING,
                description="Sort order (createdTime, modifiedTime, name, etc.)",
                required=False
            )
        ]
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
        parameters=[]
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
        parameters=[
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of shared drives to return",
                required=False
            ),
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query for shared drives",
                required=False
            )
        ]
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
        tool_name="download_file",
        parameters=[
            ToolParameter(
                name="fileId",
                type=ParameterType.STRING,
                description="The ID of the file to download",
                required=False
            ),
            ToolParameter(
                name="mimeType",
                type=ParameterType.STRING,
                description="MIME type for export (only used for Google Workspace documents like Docs, Sheets, Slides)",
                required=False
            )
        ]
    )
    def download_file(
        self,
        fileId: Optional[str] = None,
        mimeType: Optional[str] = None
    ) -> tuple[bool, str]:
        """Download file content from Google Drive"""
        """
        Args:
            fileId: The ID of the file to download
            mimeType: MIME type for export (only used for Google Workspace documents)
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Validate required parameters
            if not fileId:
                return False, json.dumps({
                    "error": "Missing required parameter: fileId is required for download_file"
                })

            # First get file details to check if it's exportable
            file_details = self._run_async(self.client.files_get(fileId=fileId))
            file_mime_type = file_details.get("mimeType", "")
            file_name = file_details.get("name", "unknown")

            # Check if it's a Google Workspace document that can be exported
            if file_mime_type.startswith("application/vnd.google-apps"):
                # Use export functionality for Google Workspace docs
                if not mimeType:
                    # Default export formats for common Google Workspace types
                    export_formats = {
                        "application/vnd.google-apps.document": "text/plain",
                        "application/vnd.google-apps.spreadsheet": "text/csv",
                        "application/vnd.google-apps.presentation": "text/plain",
                        "application/vnd.google-apps.drawing": "image/png"
                    }
                    mimeType = export_formats.get(file_mime_type, "text/plain")

                # Export the file using data source
                content = self._run_async(self.client.files_export(
                    fileId=fileId,
                    mimeType=mimeType
                ))
            else:
                # Regular file download using data source - don't pass mimeType for regular files
                content = self._run_async(self.client.files_download(
                    fileId=fileId
                ))

            # Handle different content types
            if isinstance(content, bytes):
                # Try to decode as text
                try:
                    text_content = content.decode('utf-8')
                except UnicodeDecodeError:
                    # If it's binary, encode as base64
                    import base64
                    text_content = base64.b64encode(content).decode('utf-8')
                    return True, json.dumps({
                        "file_id": fileId,
                        "file_name": file_name,
                        "content_type": "binary",
                        "content": text_content,
                        "size": len(content),
                        "message": f"Downloaded binary file '{file_name}' (base64 encoded)"
                    })
            else:
                text_content = str(content)

            return True, json.dumps({
                "file_id": fileId,
                "file_name": file_name,
                "content_type": "text",
                "content": text_content,
                "size": len(text_content),
                "mime_type": mimeType or file_mime_type,
                "message": f"Downloaded file '{file_name}' successfully"
            })

        except Exception as e:
            logger.error(f"Failed to download file {fileId}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="upload_file",
        parameters=[
            ToolParameter(
                name="file_name",
                type=ParameterType.STRING,
                description="Name of the file to upload",
                required=False
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Content of the file to upload",
                required=False
            ),
            ToolParameter(
                name="mime_type",
                type=ParameterType.STRING,
                description="MIME type of the file (e.g., 'text/plain', 'application/pdf')",
                required=False
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
        file_name: Optional[str] = None,
        content: Optional[str] = None,
        mime_type: Optional[str] = None,
        parent_folder_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Upload a file to Google Drive"""
        """
        Args:
            file_name: Name of the file to upload
            content: Content of the file
            mime_type: MIME type of the file
            parent_folder_id: ID of parent folder
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Validate required parameters
            if not file_name or not content:
                return False, json.dumps({
                    "error": "Missing required parameters: file_name and content are required for upload_file"
                })

            # Default MIME type
            if not mime_type:
                if file_name.endswith('.txt'):
                    mime_type = 'text/plain'
                elif file_name.endswith('.md'):
                    mime_type = 'text/markdown'
                elif file_name.endswith('.json'):
                    mime_type = 'application/json'
                else:
                    mime_type = 'text/plain'

            # Convert content to bytes
            content_bytes = content.encode('utf-8')

            # Create file metadata
            file_metadata = {
                'name': file_name,
                'mimeType': mime_type
            }
            if parent_folder_id:
                file_metadata['parents'] = [parent_folder_id]

            # Use GoogleDriveDataSource method for file upload with media
            file = self._run_async(self.client.files_create_with_media(
                file_metadata=file_metadata,
                content=content_bytes,
                mime_type=mime_type,
                enforceSingleParent=True,
                ignoreDefaultVisibility=True,
                keepRevisionForever=False,
                ocrLanguage=None,
                supportsAllDrives=False,
                supportsTeamDrives=False,
                useContentAsIndexableText=False
            ))

            return True, json.dumps({
                "file_id": file.get("id", ""),
                "file_name": file.get("name", ""),
                "mime_type": file.get("mimeType", ""),
                "web_view_link": file.get("webViewLink", ""),
                "parents": file.get("parents", []),
                "size": len(content_bytes),
                "message": f"File '{file_name}' uploaded successfully to Google Drive with content."
            })

        except Exception as e:
            logger.error(f"Failed to upload file '{file_name}': {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="drive",
        tool_name="get_file_permissions",
        parameters=[
            ToolParameter(
                name="file_id",
                type=ParameterType.STRING,
                description="The ID of the file to get permissions for",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of permissions to return",
                required=False
            )
        ]
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
