import asyncio
import io
import json
import logging
from typing import Any, Dict, Optional

from googleapiclient.http import MediaIoBaseDownload
from pydantic import BaseModel, Field

from app.agents.actions.util.parse_file import FileContentParser
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import (
    args_template,
    error_message,
    list_summary,
    parse_json_maybe,
)
from app.config.constants.arangodb import Connectors, OriginTypes
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
from app.connectors.core.registry.types import DocumentationLink
from app.models.entities import FileRecord, RecordType
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.drive.drive import GoogleDriveDataSource

logger = logging.getLogger(__name__)

_MAX_FILE_CONTENT_BYTES = 50 * 1024 * 1024  # 50 MB

# Maps Google Workspace MIME types to (export_mime_type, file_extension) tuples.
# Only types whose export succeeds reliably via the Drive API are listed here.
# Types absent from this map (Form, Site, Script, Shortcut, Jam, Map, …) either
# have no text export or return HTTP 403; they are rejected with an explicit message
# rather than falling back to a doomed text/plain export.
_GOOGLE_WORKSPACE_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": ("text/plain", "txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "csv"),
    "application/vnd.google-apps.presentation": ("text/plain", "txt"),
}

# Operators that indicate a query is already in Google Drive query syntax.
# Checked against query.lower() so all entries must be lowercase.
_STRUCTURED_QUERY_OPERATORS = frozenset([
    'name contains', 'fulltext contains', 'mimetype', 'modifiedtime',
    'createdtime', '=', 'trashed', 'in parents', 'in owners', 'in readers',
    'in writers', 'sharedwithme', 'starred',
])

# Operators that Google Drive does not support server-side (require client-side
# filtering). Kept as a single source of truth used in both the detection and
# the post-call filtering branches.
_SIZE_OPERATORS = ('size=', 'size =', 'size>', 'size<', 'size>=', 'size<=')


def _drive_file_label(entry: dict) -> str:
    return entry.get("name") or entry.get("id") or "?"


def _get_file_content_result_summary(_args: dict, result: Any) -> Optional[str]:
    """Unlike the other Drive tools, a successful response here is a bare
    JSON list of parsed content chunks (see `FileContentParser.parse`), not
    a `{"error"/"files": ...}` dict — so `list_summary`/`entity_summary`
    don't apply; only the error path needs the shared JSON parsing."""
    if result.is_error:
        return f"Failed: {error_message(parse_json_maybe(result.content))}"
    return "Fetched file content"


def _raw_drive_service(data_source: GoogleDriveDataSource) -> object:
    """Return the googleapiclient Drive ``Resource`` (unwrap ``GoogleClient`` if present)."""
    inner = data_source.client
    if hasattr(inner, "files"):
        return inner
    return inner.get_client()


def _execute_media_download(request: object) -> bytes:
    """Stream a get_media or export_media request to bytes (chunked download)."""
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer.read()


def _is_structured_query(query: str) -> bool:
    """Return True if *query* already uses Drive query syntax."""
    q = query.lower()
    return any(op in q for op in _STRUCTURED_QUERY_OPERATORS)


def _is_size_query(query: str) -> bool:
    """Return True if *query* contains a size operator unsupported by the API."""
    q = query.lower()
    return any(op in q for op in _SIZE_OPERATORS)


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
    parent_folder_id: Optional[str] = Field(
        default=None,
        description=(
            "ID (not name) of the folder whose direct children you want to list. "
            "When provided, the tool automatically adds \"'<id>' in parents\" to the query. "
            "Use get_file_details or search_files to resolve a folder name to its ID first."
        ),
    )


class GetFileDetailsInput(BaseModel):
    """Schema for getting file details"""
    fileId: Optional[str] = Field(default=None, description="The ID of the file to get details for")
    acknowledge_abuse: Optional[bool] = Field(default=None, description="Whether to acknowledge risk of downloading malware")
    supports_all_drives: Optional[bool] = Field(default=None, description="Whether requesting app supports both My Drives and shared drives")


class CreateFolderInput(BaseModel):
    """Schema for creating a folder"""
    folderName: Optional[str] = Field(default=None, description="The name of the folder to create")
    parent_folder_id: Optional[str] = Field(
        default=None,
        description=(
            "ID (not name) of the parent folder. Required when creating inside a "
            "specific folder or Shared Drive. Use search_files or get_file_details "
            "to resolve a folder name to its ID first."
        ),
    )


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


class GetFileContentInput(BaseModel):
    """Schema for reading file content"""
    file_id: str = Field(description="The ID of the file to read")


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
            icon_path=IconPaths.connector_icon("drive"),
            app_group="Google Workspace",
            app_description="Drive OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("drive"))
        .add_documentation_link(DocumentationLink(
            "Google Drive API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/google-workspace/drive",
            "pipeshub",
        )))\
    .build_decorator()
class GoogleDrive:
    """Drive tool exposed to the agents using DriveDataSource"""
    def __init__(self, client: GoogleClient, state: Optional[ChatState] = None, **kwargs) -> None:
        """Initialize the Google Drive tool

        Args:
            client: Authenticated Google Drive client
            state: Chat state for model config and configuration service
        """
        self.client = GoogleDriveDataSource(client)
        self.state: Optional[ChatState] = state or kwargs.get("state")

    async def _files_get_media_bytes(
        self,
        file_id: str,
        acknowledge_abuse: Optional[bool] = None,
        supports_all_drives: Optional[bool] = None,
    ) -> bytes:
        api = _raw_drive_service(self.client)
        kwargs: Dict[str, Any] = {"fileId": file_id}
        if acknowledge_abuse is not None:
            kwargs["acknowledgeAbuse"] = acknowledge_abuse
        if supports_all_drives is not None:
            kwargs["supportsAllDrives"] = supports_all_drives
        request = api.files().get_media(**kwargs)  # type: ignore
        return await asyncio.to_thread(_execute_media_download, request)

    async def _files_export_media_bytes(self, file_id: str, export_mime: str) -> bytes:
        api = _raw_drive_service(self.client)
        request = api.files().export_media(
            fileId=file_id,
            mimeType=export_mime,
        )  # type: ignore
        return await asyncio.to_thread(_execute_media_download, request)

    @tool(
        path="/tools/drive/get_files_list",
        short_description="List files in Google Drive",
        description=(
            "Get list of files in Google Drive with optional filtering, pagination, and folder scoping. "
            "Supports Google Drive query syntax for filtering by name, MIME type, modified time, etc. "
            "Use parent_folder_id to list direct children of a specific folder."
        ),
        parameters=[
            ToolParameter(name="corpora", type=ParameterType.STRING, description="Bodies of items to query", required=False),
            ToolParameter(name="drive_id", type=ParameterType.STRING, description="ID of the shared drive to search", required=False),
            ToolParameter(name="order_by", type=ParameterType.STRING, description="Sort keys", required=False),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Maximum number of files to return per page", required=False),
            ToolParameter(name="page_token", type=ParameterType.STRING, description="Token for pagination", required=False),
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query for filtering files", required=False),
            ToolParameter(name="spaces", type=ParameterType.STRING, description="Spaces to query", required=False),
            ToolParameter(
                name="parent_folder_id",
                type=ParameterType.STRING,
                description=(
                    "ID (not name) of the folder whose direct children you want to list. "
                    "When provided, the tool automatically adds \"'<id>' in parents\" to the query. "
                    "Use get_file_details or search_files to resolve a folder name to its ID first."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searching Drive: "{args["query"]}"' if args.get("query") else "Listing Drive files"
        ),
        result_summary=list_summary(("files",), _drive_file_label, "file"),
    )
    async def get_files_list(
        self,
        corpora: Optional[str] = None,
        drive_id: Optional[str] = None,
        order_by: Optional[str] = None,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        query: Optional[str] = None,
        spaces: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get the list of files in Google Drive.

        Args:
            corpora: Bodies of items to query
            drive_id: ID of shared drive to search
            order_by: Sort order for results
            page_size: Number of files per page
            page_token: Pagination token
            query: Search query for filtering
            spaces: Spaces to query
            parent_folder_id: Folder ID whose children to list. The tool builds
                the ``'<id>' in parents`` clause automatically — do not include
                it in *query* as well.
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Format query if provided
            formatted_query = None
            if query:
                if _is_size_query(query):
                    # Size operators are unsupported server-side; fetch all files
                    # and apply client-side filtering after the API call.
                    logger.warning(f"Query contains unsupported operator: {query}. Ignoring query parameter.")
                    formatted_query = None
                elif not _is_structured_query(query):
                    formatted_query = f'name contains "{query}"'
                else:
                    # Clean up the query - remove spaces around operators
                    formatted_query = query.replace(' = ', '=').replace(' =', '=').replace('= ', '=')

            # Build the `in parents` clause from the explicit folder-ID parameter
            # so the LLM never has to construct it (and can't accidentally pass a
            # folder name instead of an ID).
            if parent_folder_id:
                parents_clause = f"'{parent_folder_id}' in parents"
                formatted_query = (
                    f"({formatted_query}) and {parents_clause}"
                    if formatted_query
                    else parents_clause
                )

            # `driveId` only accepts an actual shared-drive ID. `"root"` is a *fileId*
            # alias for My Drive's root, not a drive ID — drop it (and any empty value)
            # so we don't trip Google's "driveId must be set iff corpora=drive" rule.
            if drive_id in (None, "", "root"):
                drive_id = None

            # Google requires `corpora=drive` iff `driveId` is set. If the caller passed
            # an explicit driveId with a different corpora, normalize to `drive`.
            if drive_id and (corpora is None or corpora.lower() != "drive"):
                corpora = "drive"

            # Conversely, `corpora=drive` without a driveId is invalid — fall back to
            # the broader `allDrives` scope.
            if corpora and corpora.lower() == "drive" and not drive_id:
                corpora = "allDrives"

            # `includeItemsFromAllDrives` / `supportsAllDrives` are required whenever
            # the request scopes a shared drive or all drives.
            scopes_shared_drives = bool(drive_id) or (
                corpora is not None and any(c in corpora.lower() for c in ("drive", "alldrives"))
            )

            # Use GoogleDriveDataSource method
            files = await self.client.files_list(
                corpora=corpora,
                driveId=drive_id,
                orderBy=order_by,
                pageSize=page_size,
                pageToken=page_token,
                q=formatted_query,
                spaces=spaces,
                includeItemsFromAllDrives=True if scopes_shared_drives else None,
                supportsAllDrives=True if scopes_shared_drives else None,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, webContentLink, parents, size, modifiedTime, createdTime)",
            )

            # Get files list
            files_list = files.get("files", [])

            # Apply client-side filtering if size query was detected
            if query and formatted_query is None and _is_size_query(query):
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
        path="/tools/drive/get_file_details",
        short_description="Get metadata for a specific Drive file",
        description=(
            "Get detailed information about a specific file in Google Drive by its file ID. "
            "Returns metadata including name, MIME type, size, dates, owners, parents, and sharing status."
        ),
        parameters=[
            ToolParameter(name="fileId", type=ParameterType.STRING, description="The ID of the file to get details for", required=False),
            ToolParameter(name="acknowledge_abuse", type=ParameterType.BOOLEAN, description="Whether to acknowledge risk of downloading malware", required=False),
            ToolParameter(name="supports_all_drives", type=ParameterType.BOOLEAN, description="Whether requesting app supports both My Drives and shared drives", required=False),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
    )
    async def get_file_details(
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
            file = await self.client.files_get(
                fileId=fileId,
                acknowledgeAbuse=acknowledge_abuse,
                supportsAllDrives=supports_all_drives,
                fields="id, name, mimeType, webViewLink, webContentLink, parents, size, modifiedTime, createdTime, fileExtension, owners, shared",
            )

            return True, json.dumps(file)
        except Exception as e:
            logger.error(f"Failed to get file details for {fileId}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/drive/create_folder",
        short_description="Create a new folder in Google Drive",
        description=(
            "Create a new folder in Google Drive. Optionally specify a parent folder ID "
            "to create it inside a specific folder or Shared Drive."
        ),
        parameters=[
            ToolParameter(name="folderName", type=ParameterType.STRING, description="The name of the folder to create", required=False),
            ToolParameter(
                name="parent_folder_id",
                type=ParameterType.STRING,
                description=(
                    "ID (not name) of the parent folder. Required when creating inside a "
                    "specific folder or Shared Drive. Use search_files or get_file_details "
                    "to resolve a folder name to its ID first."
                ),
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="write")],
    )
    async def create_folder(
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

            # supportsAllDrives must be True whenever the parent could be in a
            # Shared Drive (IDs starting with "0A" are always Shared Drive folders).
            # Setting it unconditionally is safe for My Drive parents too.
            supports_all_drives = bool(parent_folder_id)

            # Use GoogleDriveDataSource method - pass body in kwargs
            folder = await self.client.files_create(
                enforceSingleParent=True,
                ignoreDefaultVisibility=True,
                keepRevisionForever=False,
                ocrLanguage=None,
                supportsAllDrives=supports_all_drives,
                supportsTeamDrives=supports_all_drives,
                useContentAsIndexableText=False,
                fields="id, name, mimeType, webViewLink, parents, createdTime",
                **{"body": folder_metadata}  # Pass metadata as body in kwargs
            )

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
        path="/tools/drive/search_files",
        short_description="Search for files in Google Drive",
        description=(
            "Search for files in Google Drive using query syntax. Supports Google Drive query operators "
            "such as 'name contains', 'mimeType=', 'modifiedTime>', etc. Simple text queries are "
            "automatically converted to 'name contains \"<query>\"'."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query (e.g., 'name contains \"report\"', 'mimeType=\"application/pdf\"')", required=True),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Maximum number of results to return", required=False),
            ToolParameter(name="order_by", type=ParameterType.STRING, description="Sort order", required=False),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
        args_summary=args_template('Searching Google Drive: "{query}"', "query"),
        result_summary=list_summary(("files",), _drive_file_label, "file"),
    )
    async def search_files(
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
            if not _is_structured_query(query):
                formatted_query = f'name contains "{query}"'
            else:
                formatted_query = query

            # Use GoogleDriveDataSource method
            files = await self.client.files_list(
                q=formatted_query,
                pageSize=page_size,
                orderBy=order_by,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, webContentLink, parents, size, modifiedTime, createdTime)",
            )

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
        path="/tools/drive/get_drive_info",
        short_description="Get Drive account and storage info",
        description=(
            "Get information about the user's Google Drive account, including storage quota, "
            "max upload size, user profile, and supported export/import formats."
        ),
        parameters=[],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
    )
    async def get_drive_info(self) -> tuple[bool, str]:
        """Get information about the user's Drive"""
        """
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleDriveDataSource method
            about = await self.client.about_get()

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
        path="/tools/drive/get_shared_drives",
        short_description="List shared/team drives",
        description="Get list of shared drives (team drives) available to the user, with optional search filtering.",
        parameters=[
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Maximum number of drives to return per page", required=False),
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query for shared drives", required=False),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
    )
    async def get_shared_drives(
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
            drives = await self.client.drives_list(
                pageSize=page_size,
                q=query
            )

            return True, json.dumps({
                "drives": drives.get("drives", []),
                "nextPageToken": drives.get("nextPageToken", None),
                "totalResults": len(drives.get("drives", []))
            })
        except Exception as e:
            logger.error(f"Failed to get shared drives: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/drive/get_file_permissions",
        short_description="Get sharing permissions for a file",
        description="Get the list of permissions (who has access) for a specific file in Google Drive.",
        parameters=[
            ToolParameter(name="file_id", type=ParameterType.STRING, description="The ID of the file to get permissions for", required=True),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Maximum number of permissions to return", required=False),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
    )
    async def get_file_permissions(
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
            permissions = await self.client.permissions_list(
                fileId=file_id,
                pageSize=page_size
            )

            return True, json.dumps({
                "permissions": permissions.get("permissions", []),
                "nextPageToken": permissions.get("nextPageToken", None),
                "file_id": file_id
            })
        except Exception as e:
            logger.error(f"Failed to get permissions for file {file_id}: {e}")
            return False, json.dumps({"error": str(e)})


    @tool(
        path="/tools/drive/get_file_content",
        short_description="Read text content of a Drive file",
        description=(
            "Download and return the text content of a Google Drive file. "
            "Supports PDF, DOCX, XLSX, PPTX, HTML, XML, CSV, TSV, MD, MDX, TXT, "
            "and Google Workspace documents (Docs, Sheets, Slides). "
            "Use get_file_details for metadata only, or search_files/get_files_list to find the file ID first."
        ),
        parameters=[
            ToolParameter(name="file_id", type=ParameterType.STRING, description="The ID of the file to read", required=True),
        ],
        tags=[Tag(key="category", value="file_storage"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching content for Drive file {file_id}", "file_id"),
        result_summary=_get_file_content_result_summary,
    )
    async def get_file_content(
        self,
        file_id: str,
    ) -> tuple[bool, str]:
        try:
            file_info = await self.client.files_get(
                fileId=file_id,
                fields="id,name,mimeType,size,fileExtension",
                supportsAllDrives=True,
            )

            mime_type: str = file_info.get("mimeType", "")
            file_name: str = file_info.get("name", f"document_{file_id}")
            file_size = file_info.get("size")
            file_extension: str = file_info.get("fileExtension", "")

            if mime_type == "application/vnd.google-apps.folder":
                return False, json.dumps({"error": "Cannot read content of a folder"})

            if file_size is not None and int(file_size) > _MAX_FILE_CONTENT_BYTES:
                return False, json.dumps([{
                    "error": "File is too large to be processed",
                }])

            state = self.state or {}
            model_name = state.get("model_name")
            model_key = state.get("model_key")
            configuration_service = state.get("config_service")

            if configuration_service is None:
                return False, json.dumps({
                    "error": "Missing required dependency: config_service is not available in agent state"
                })

            is_workspace_doc = mime_type.startswith("application/vnd.google-apps.")
            if is_workspace_doc:
                export_format = _GOOGLE_WORKSPACE_EXPORT_FORMATS.get(mime_type)
                if export_format is None:
                    short_type = mime_type.removeprefix("application/vnd.google-apps.")
                    return False, json.dumps({
                        "error": (
                            f"Google Workspace type '{short_type}' does not support text export. "
                            "Supported types: Docs, Sheets, Slides."
                        )
                    })
                export_mime, ext = export_format
                raw = await self._files_export_media_bytes(file_id, export_mime)
                effective_mime = export_mime
                # Google does not populate `size` for Workspace files, so the
                # pre-download guard above is always skipped for this path.
                # Enforce the cap on the exported bytes instead.
                if len(raw) > _MAX_FILE_CONTENT_BYTES:
                    return False, json.dumps({
                        "error": "File is too large to be processed",
                    })
            else:
                raw = await self._files_get_media_bytes(
                    file_id,
                    supports_all_drives=True,
                )
                ext = (file_extension or "").strip().lower().lstrip(".")
                effective_mime = mime_type

            if not isinstance(raw, (bytes, bytearray)):
                return False, json.dumps({
                    "error": "Unexpected download response type; expected raw bytes",
                    "detail": str(raw)[:500],
                })

            org_id = state.get("org_id") or ""
            tool_to_toolset_map = state.get("tool_to_toolset_map") or {}
            connector_id = tool_to_toolset_map.get("drive.get_file_content")
            if not connector_id:
                return False, json.dumps({
                    "error": "Toolset mapping for drive.get_file_content is not available in agent state"
                })

            record_name = file_name if file_name else f"document.{ext}"
            file_record = FileRecord(
                org_id=org_id,
                record_name=record_name,
                record_type=RecordType.FILE,
                external_record_id=file_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.GOOGLE_DRIVE,
                connector_id=connector_id,
                mime_type=effective_mime or "application/octet-stream",
                extension=ext,
                is_file=True,
            )

            parser = FileContentParser(logger=logger, config_service=configuration_service)
            status, payload = await parser.parse(
                file_record,
                bytes(raw),
                model_name,
                model_key,
                configuration_service,
            )
            serialized = [item.model_dump() for item in payload]
            if status:
                return True, json.dumps(serialized)
            return False, json.dumps(serialized)
        except Exception as e:
            logger.error("Failed to get content for file %s: %s", file_id, e)
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
