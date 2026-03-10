import json
import logging
from typing import Any, Dict, Optional

from msgraph.generated.models.drive_item import DriveItem  # type: ignore
from msgraph.generated.models.folder import Folder  # type: ignore
from msgraph.generated.models.item_reference import ItemReference  # type: ignore
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
from app.connectors.core.registry.types import AuthField
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.one_drive.one_drive import OneDriveDataSource

logger = logging.getLogger(__name__)


def _serialize_graph_obj(obj: Any) -> Any:
    """Recursively convert an MS Graph SDK Kiota object to a JSON-serialisable value.

    Kiota Parsable models store data in an internal backing store, so plain
    ``vars()`` only reveals ``{'backing_store': …}``.  We first try kiota's own
    ``JsonSerializationWriter``; on failure we iterate the backing store, then
    fall back to ``vars()`` + ``additional_data``.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_serialize_graph_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize_graph_obj(v) for k, v in obj.items()}

    # Kiota Parsable objects expose get_field_deserializers()
    if hasattr(obj, "get_field_deserializers"):
        try:
            from kiota_serialization_json.json_serialization_writer import (  # type: ignore
                JsonSerializationWriter,
            )
            writer = JsonSerializationWriter()
            writer.write_object_value(None, obj)
            content = writer.get_serialized_content()
            if content:
                raw = content.decode("utf-8") if isinstance(content, bytes) else content
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed:
                    return parsed
        except Exception:
            pass

        try:
            bs = getattr(obj, "backing_store", None)
            if bs is not None and hasattr(bs, "enumerate_"):
                result: Dict[str, Any] = {}
                for key, value in bs.enumerate_():
                    if not str(key).startswith("_"):
                        try:
                            result[key] = _serialize_graph_obj(value)
                        except Exception:
                            result[key] = str(value)
                additional = getattr(obj, "additional_data", None)
                if isinstance(additional, dict):
                    for k, v in additional.items():
                        if k not in result:
                            try:
                                result[k] = _serialize_graph_obj(v)
                            except Exception:
                                result[k] = str(v)
                if result:
                    return result
        except Exception:
            pass

    # Generic fallback for non-Kiota objects
    try:
        obj_dict = vars(obj)
    except TypeError:
        obj_dict = {}

    result = {}
    for k, v in obj_dict.items():
        if k.startswith("_"):
            continue
        try:
            result[k] = _serialize_graph_obj(v)
        except Exception:
            result[k] = str(v)

    additional = getattr(obj, "additional_data", None)
    if isinstance(additional, dict):
        for k, v in additional.items():
            if k not in result:
                try:
                    result[k] = _serialize_graph_obj(v)
                except Exception:
                    result[k] = str(v)

    return result if result else str(obj)


def _normalize_odata(data: Any) -> Any:
    """Normalize OData response keys so cascading placeholders resolve reliably.

    MS Graph returns collections under a ``value`` key, but LLM planners
    commonly guess ``results``.  We keep ``value`` intact and add a
    ``results`` alias pointing to the same list so both paths work.
    """
    if isinstance(data, dict):
        if "value" in data and isinstance(data["value"], list) and "results" not in data:
            data["results"] = data["value"]
    return data


def _response_json(response: object) -> str:
    """Serialize an OneDriveResponse to JSON, handling Kiota SDK objects in data."""
    out: Dict[str, Any] = {"success": getattr(response, "success", False)}
    data = getattr(response, "data", None)
    if data is not None:
        serialized = _serialize_graph_obj(data)
        out["data"] = _normalize_odata(serialized)
    error = getattr(response, "error", None)
    if error is not None:
        out["error"] = error
    message = getattr(response, "message", None)
    if message is not None:
        out["message"] = message
    return json.dumps(out)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class GetDrivesInput(BaseModel):
    """Schema for listing OneDrive drives"""
    search: Optional[str] = Field(default=None, description="Search query to filter drives")
    filter: Optional[str] = Field(default=None, description="OData filter query for drives")
    orderby: Optional[str] = Field(default=None, description="Field to order results by")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")
    top: Optional[int] = Field(default=None, description="Maximum number of drives to return")
    skip: Optional[int] = Field(default=None, description="Number of drives to skip for pagination")


class GetDriveInput(BaseModel):
    """Schema for getting a specific drive"""
    drive_id: str = Field(description="The ID of the drive to retrieve")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")
    expand: Optional[str] = Field(default=None, description="Related entities to expand")


class GetFilesInput(BaseModel):
    """Schema for listing files in a drive or folder"""
    drive_id: str = Field(description="The ID of the drive")
    folder_id: Optional[str] = Field(default=None, description="ID of the folder to list children of (defaults to root)")
    search: Optional[str] = Field(default=None, description="Search query to filter files by name or content")
    filter: Optional[str] = Field(default=None, description="OData filter query for files")
    orderby: Optional[str] = Field(default=None, description="Field to order results by (e.g. 'name', 'lastModifiedDateTime')")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")
    top: Optional[int] = Field(default=None, description="Maximum number of items to return")


class GetFileInput(BaseModel):
    """Schema for getting a specific file or folder"""
    drive_id: str = Field(description="The ID of the drive")
    item_id: str = Field(description="The ID of the file or folder")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")
    expand: Optional[str] = Field(default=None, description="Related entities to expand (e.g. 'thumbnails', 'children')")


class SearchFilesInput(BaseModel):
    """Schema for searching files across OneDrive"""
    drive_id: str = Field(description="The ID of the drive to search in")
    query: str = Field(description="Search query string to find files by name, content, or metadata")
    top: Optional[int] = Field(default=None, description="Maximum number of results to return")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")


class GetFolderChildrenInput(BaseModel):
    """Schema for listing items inside a specific folder"""
    drive_id: str = Field(description="The ID of the drive")
    folder_id: str = Field(description="The ID of the folder whose children to list")
    filter: Optional[str] = Field(default=None, description="OData filter query")
    orderby: Optional[str] = Field(default=None, description="Field to order results by")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")
    top: Optional[int] = Field(default=None, description="Maximum number of items to return")


class CreateFolderInput(BaseModel):
    """Schema for creating a new folder"""
    drive_id: str = Field(description="The ID of the drive")
    parent_folder_id: Optional[str] = Field(default=None, description="ID of the parent folder (defaults to root)")
    folder_name: str = Field(description="Name of the new folder to create")


# class DeleteItemInput(BaseModel):
#     """Schema for deleting a file or folder"""
#     drive_id: str = Field(description="The ID of the drive")
#     item_id: str = Field(description="The ID of the file or folder to delete")


class MoveItemInput(BaseModel):
    """Schema for moving a file or folder"""
    drive_id: str = Field(description="The ID of the drive")
    item_id: str = Field(description="The ID of the file or folder to move")
    new_parent_id: str = Field(description="The ID of the destination folder")
    new_name: Optional[str] = Field(default=None, description="Optional new name after moving")


class RenameItemInput(BaseModel):
    """Schema for renaming a file or folder"""
    drive_id: str = Field(description="The ID of the drive")
    item_id: str = Field(description="The ID of the file or folder to rename")
    new_name: str = Field(description="The new name for the item")


class GetVersionsInput(BaseModel):
    """Schema for getting file version history"""
    drive_id: str = Field(description="The ID of the drive")
    item_id: str = Field(description="The ID of the file")


class GetRecentFilesInput(BaseModel):
    """Schema for getting recently accessed files"""
    drive_id: str = Field(description="The ID of the drive")
    top: Optional[int] = Field(default=None, description="Maximum number of recent files to return")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")


class GetSharedWithMeInput(BaseModel):
    """Schema for getting files shared with the current user"""
    drive_id: str = Field(description="The ID of the drive")
    top: Optional[int] = Field(default=None, description="Maximum number of items to return")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")


class GetSpecificVersionInput(BaseModel):
    """Schema for getting a specific file version"""
    drive_id: str = Field(description="The ID of the drive")
    item_id: str = Field(description="The ID of the file")
    version_id: str = Field(description="The ID of the version to retrieve")
    select: Optional[str] = Field(default=None, description="Comma-separated list of fields to return")


class RestoreVersionInput(BaseModel):
    """Schema for restoring a specific file version"""
    drive_id: str = Field(description="The ID of the drive")
    item_id: str = Field(description="The ID of the file")
    version_id: str = Field(description="The ID of the version to restore as the current version")


# class CheckinFileInput(BaseModel):
#     """Schema for checking in a file"""
#     drive_id: str = Field(description="The ID of the drive")
#     item_id: str = Field(description="The ID of the file to check in")
#     comment: Optional[str] = Field(default=None, description="Optional comment for the new version created on check-in")
#     check_in_as: Optional[str] = Field(default=None, description="Version type: 'published' or 'unspecified'")


# ---------------------------------------------------------------------------
# ToolsetBuilder registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("OneDrive")\
    .in_group("Microsoft 365")\
    .with_description("OneDrive integration for file storage, search, and collaboration")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="OneDrive",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            redirect_uri="toolsets/oauth/callback/onedrive",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "Files.ReadWrite",
                    "offline_access",
                    "User.Read",
                ],
            ),
            additional_params={
                "prompt": "consent",
                "response_mode": "query",
            },
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration"),
                AuthField(
                    name="tenantId",
                    display_name="Tenant ID",
                    field_type="TEXT",
                    placeholder="common  (or your Azure AD tenant ID / domain)",
                    description=(
                        "Your Azure Active Directory tenant ID (e.g. "
                        "'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx') or domain "
                        "(e.g. 'contoso.onmicrosoft.com'). "
                        "Leave blank or enter 'common' to allow both personal Microsoft "
                        "accounts and any Azure AD tenant."
                    ),
                    required=False,
                    default_value="common",
                    min_length=0,
                    max_length=500,
                    is_secret=False,
                ),
            ],
            icon_path="/assets/icons/connectors/onedrive.svg",
            app_group="Microsoft 365",
            app_description="OneDrive OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/onedrive.svg"))\
    .build_decorator()
class OneDrive:
    """OneDrive tool exposed to the agents using OneDriveDataSource"""

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the OneDrive tool

        Args:
            client: Authenticated Microsoft Graph client
        Returns:
            None
        """
        self.client = OneDriveDataSource(client)


    def _serialize_response(response_obj: Any) -> Any:
        """Recursively convert a Graph SDK response object to a JSON-serialisable dict.

        Kiota model objects (Parsable) store their properties in an internal
        backing store rather than as plain instance attributes, so ``vars()``
        only reveals ``{'backing_store': ..., 'additional_data': {...}}``.
        We first try kiota's own JSON serialization writer which handles the
        backing store correctly.  On any failure we fall back to the previous
        ``vars()`` + ``additional_data`` approach.
        """
        if response_obj is None:
            return None
        if isinstance(response_obj, (str, int, float, bool)):
            return response_obj
        if isinstance(response_obj, list):
            return [OneDrive._serialize_response(item) for item in response_obj]
        if isinstance(response_obj, dict):
            return {k: OneDrive._serialize_response(v) for k, v in response_obj.items()}

        # ── Kiota Parsable objects ────────────────────────────────────────────
        # Kiota models implement get_field_deserializers() as part of the
        # Parsable interface.  Use kiota's JsonSerializationWriter to produce a
        # proper camelCase dict (id, subject, isOnlineMeeting, …) so that
        # placeholder paths like {{…events[0].id}} resolve correctly.
        if hasattr(response_obj, "get_field_deserializers"):
            try:
                import json as _json

                from kiota_serialization_json.json_serialization_writer import (  # type: ignore
                    JsonSerializationWriter,
                )

                writer = JsonSerializationWriter()
                writer.write_object_value(None, response_obj)
                content = writer.get_serialized_content()
                if content:
                    raw = content.decode("utf-8") if isinstance(content, bytes) else content
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict) and parsed:
                        return parsed
            except Exception:
                pass

            # Secondary fallback: iterate backing store if available
            try:
                backing_store = getattr(response_obj, "backing_store", None)
                if backing_store is not None and hasattr(backing_store, "enumerate_"):
                    result: Dict[str, Any] = {}
                    for key, value in backing_store.enumerate_():
                        if not str(key).startswith("_"):
                            try:
                                result[key] = OneDrive._serialize_response(value)
                            except Exception:
                                result[key] = str(value)
                    additional = getattr(response_obj, "additional_data", None)
                    if isinstance(additional, dict):
                        for k, v in additional.items():
                            if k not in result:
                                try:
                                    result[k] = OneDrive._serialize_response(v)
                                except Exception:
                                    result[k] = str(v)
                    if result:
                        return result
            except Exception:
                pass

        # ── Generic fallback (non-kiota objects) ─────────────────────────────
        try:
            obj_dict = vars(response_obj)
        except TypeError:
            obj_dict = {}

        result = {}
        for k, v in obj_dict.items():
            if k.startswith("_"):
                continue
            try:
                result[k] = OneDrive._serialize_response(v)
            except Exception:
                result[k] = str(v)

        additional = getattr(response_obj, "additional_data", None)
        if isinstance(additional, dict):
            for k, v in additional.items():
                if k not in result:
                    try:
                        result[k] = OneDrive._serialize_response(v)
                    except Exception:
                        result[k] = str(v)

        return result if result else str(response_obj)


    async def _resolve_folder_id(self, drive_id: str, item_id: str) -> tuple[str, Optional[str]]:
        """Validate that an item is a folder; if it's a file, return its parent folder.

        Returns:
            (resolved_folder_id, warning_message_or_None)
        """
        if item_id.lower() == "root":
            return item_id, None
        try:
            resp = await self.client.drives_get_items(
                drive_id=drive_id, driveItem_id=item_id
            )
            if not resp.success or resp.data is None:
                return item_id, None

            data = _serialize_graph_obj(resp.data)
            if not isinstance(data, dict):
                return item_id, None

            if "folder" in data:
                return item_id, None

            parent_ref = data.get("parentReference") or {}
            parent_id = parent_ref.get("id")
            if parent_id:
                file_name = data.get("name", item_id)
                return parent_id, (
                    f"'{file_name}' is a file, not a folder. "
                    f"Using its parent folder as the destination."
                )
        except Exception:
            pass
        return item_id, None

    # ------------------------------------------------------------------
    # Drive-level tools
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_drives",
        description="List all OneDrive drives accessible to the user. MUST be called first to get drive_id before using any other OneDrive tool.",
        args_schema=GetDrivesInput,
        when_to_use=[
            "User mentions 'OneDrive' and wants to see available drives",
            "User asks 'what drives do I have'",
            "ALWAYS call this first when drive_id is unknown — almost all other OneDrive tools require it",
            "Before any file operation (rename, delete, move, copy, search, list) when drive_id is not in conversation history",
        ],
        when_not_to_use=[
            "Drive ID is already known from conversation history or Reference Data",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me my OneDrive",
            "List all my drives",
            "What OneDrive drives do I have access to?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_drives(
        self,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
    ) -> tuple[bool, str]:
        """List all OneDrive drives accessible to the user

        Args:
            search: Search query to filter drives
            filter: OData filter query
            orderby: Field to order results by
            select: Fields to return
            top: Max number of results
            skip: Results to skip for pagination
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.me_list_drives(
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                top=top,
                skip=skip,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get drives: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_drive",
        description="Get details about a specific OneDrive drive including quota and owner",
        args_schema=GetDriveInput,
        when_to_use=[
            "User wants details about a specific drive",
            "User asks about storage quota or drive owner",
            "Drive ID is known and user wants drive metadata",
        ],
        when_not_to_use=[
            "User wants to list all drives (use get_drives)",
            "User wants to list files in a drive (use get_files)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get details for my OneDrive",
            "Show drive storage quota",
            "Who owns this drive?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_drive(
        self,
        drive_id: str,
        select: Optional[str] = None,
        expand: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get details about a specific OneDrive drive

        Args:
            drive_id: The ID of the drive
            select: Fields to return
            expand: Related entities to expand
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_drive_get_drive(
                drive_id=drive_id,
                select=select,
                expand=expand,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get drive {drive_id}: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # File & folder listing tools
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_files",
        description="List files and folders in the root of a OneDrive drive. Requires drive_id — call get_drives first if unknown.",
        args_schema=GetFilesInput,
        when_to_use=[
            "User wants to browse files in OneDrive",
            "User asks 'what files do I have in OneDrive'",
            "Listing root-level contents of a drive",
        ],
        when_not_to_use=[
            "User wants to search by keyword (use search_files)",
            "User wants files inside a specific folder (use get_folder_children)",
            "User wants details of a single file (use get_file)",
            "drive_id is unknown — call get_drives first to resolve it",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List my OneDrive files",
            "Show files in my OneDrive",
            "What's in my OneDrive root?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_files(
        self,
        drive_id: str,
        folder_id: Optional[str] = None,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        top: Optional[int] = None,
    ) -> tuple[bool, str]:
        """List files and folders in a OneDrive drive

        Args:
            drive_id: The ID of the drive
            folder_id: Folder ID to list children of (defaults to root)
            search: Search query to filter by name
            filter: OData filter query
            orderby: Field to order by
            select: Fields to return
            top: Max number of items
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            parent_id = folder_id or "root"
            response = await self.client.drives_items_list_children(
                drive_id=drive_id,
                driveItem_id=parent_id,
                search=search,
                filter=filter,
                orderby=orderby,
                select=select,
                top=top,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get files for drive {drive_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_folder_children",
        description="List all files and subfolders inside a specific OneDrive folder",
        args_schema=GetFolderChildrenInput,
        when_to_use=[
            "User wants to browse inside a specific folder",
            "User asks what's inside a folder by ID",
            "Navigating a folder hierarchy in OneDrive",
        ],
        when_not_to_use=[
            "User wants root-level files (use get_files)",
            "User wants to search across all files (use search_files)",
            "User wants details of one file (use get_file)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List files inside this folder",
            "What's in my Documents folder?",
            "Show subfolders of a folder",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_folder_children(
        self,
        drive_id: str,
        folder_id: str,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        select: Optional[str] = None,
        top: Optional[int] = None,
    ) -> tuple[bool, str]:
        """List all children of a specific folder in OneDrive

        Args:
            drive_id: The ID of the drive
            folder_id: The ID of the folder
            filter: OData filter query
            orderby: Field to order by
            select: Fields to return
            top: Max number of items
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_items_list_children(
                drive_id=drive_id,
                driveItem_id=folder_id,
                filter=filter,
                orderby=orderby,
                select=select,
                top=top,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get children of folder {folder_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_file",
        description="Get metadata and details for a specific file or folder in OneDrive",
        args_schema=GetFileInput,
        when_to_use=[
            "User wants details about a specific file or folder",
            "User has a file ID and wants metadata (size, dates, type)",
            "User asks about a specific OneDrive item",
        ],
        when_not_to_use=[
            "User wants to list multiple files (use get_files)",
            "User wants to search files (use search_files)",
            "File ID is unknown (use get_files or search_files first)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get details for this file",
            "Show file info for item ID",
            "What is the size and type of this OneDrive file?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_file(
        self,
        drive_id: str,
        item_id: str,
        select: Optional[str] = None,
        expand: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get details for a specific file or folder

        Args:
            drive_id: The ID of the drive
            item_id: The ID of the file or folder
            select: Fields to return
            expand: Related entities to expand
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_get_items(
                drive_id=drive_id,
                driveItem_id=item_id,
                select=select,
                expand=expand,
            )
            if response.success:
                return True, self._serialize_response(response.data)
            return False, self._serialize_response(response.data)
        except Exception as e:
            logger.error(f"Failed to get file {item_id}: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="search_files",
        description="Search for files and folders in OneDrive by name, content, or metadata. Requires drive_id — call get_drives first if unknown.",
        args_schema=SearchFilesInput,
        when_to_use=[
            "User wants to find files by keyword or name in OneDrive",
            "User asks 'find files containing X' or 'search for Y in OneDrive'",
            "User wants to locate a specific document without knowing its folder",
            "Use to resolve item_id before file operations (rename, delete, move, copy) when user mentions a file by name",
        ],
        when_not_to_use=[
            "User wants to browse all files (use get_files)",
            "User already knows the file ID (use get_file)",
            "No search keyword is provided",
            "drive_id is unknown — call get_drives first to resolve it",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for 'budget report' in OneDrive",
            "Find all PDF files in my OneDrive",
            "Where is the Q3 presentation?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def search_files(
        self,
        drive_id: str,
        query: str,
        top: Optional[int] = None,
        select: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Search for files and folders in OneDrive

        Args:
            drive_id: The ID of the drive to search
            query: Search query string
            top: Max number of results
            select: Fields to return
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_drive_search(
                drive_id=drive_id,
                q=query,
                top=top,
                select=select,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to search files with query '{query}': {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Recent & shared files
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_recent_files",
        description="Get files recently accessed or modified by the user in OneDrive",
        args_schema=GetRecentFilesInput,
        when_to_use=[
            "User asks for recently opened or modified OneDrive files",
            "User says 'show my recent files' or 'what did I work on recently'",
        ],
        when_not_to_use=[
            "User wants to browse all files (use get_files)",
            "User wants to search (use search_files)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my recent OneDrive files",
            "What did I last work on in OneDrive?",
            "List recently modified files",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_recent_files(
        self,
        drive_id: str,
        top: Optional[int] = None,
        select: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get recently accessed or modified files

        Args:
            drive_id: The ID of the drive
            top: Max number of results
            select: Fields to return
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_drive_recent(
                drive_id=drive_id,
                top=top,
                select=select,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get recent files: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="get_shared_with_me",
        description="Get files and folders that others have shared with the current user",
        args_schema=GetSharedWithMeInput,
        when_to_use=[
            "User asks 'what's been shared with me'",
            "User wants to see OneDrive files shared by colleagues",
            "User mentions 'shared files' in OneDrive context",
        ],
        when_not_to_use=[
            "User wants their own files (use get_files)",
            "User wants to share a file (sharing links not supported)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show files shared with me in OneDrive",
            "What has my team shared with me?",
            "List shared OneDrive items",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_shared_with_me(
        self,
        drive_id: str,
        top: Optional[int] = None,
        select: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get files shared with the current user

        Args:
            drive_id: The ID of the drive
            top: Max number of results
            select: Fields to return
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_drive_shared_with_me(
                drive_id=drive_id,
                top=top,
                select=select,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get shared-with-me items: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="create_folder",
        description="Create a new folder in OneDrive. Requires drive_id — call get_drives first if unknown.",
        args_schema=CreateFolderInput,
        when_to_use=[
            "User wants to create a new folder in OneDrive",
            "User says 'make a folder' or 'create a directory'",
            "Organising files requires a new folder",
        ],
        when_not_to_use=[
            "User wants to list folders (use get_files)",
            "User wants to rename a folder (use rename_item)",
            "drive_id is unknown — call get_drives first to resolve it",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a folder named 'Projects' in OneDrive",
            "Make a new folder inside Documents",
            "Add a subfolder to my OneDrive",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def create_folder(
        self,
        drive_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Create a new folder in OneDrive

        Args:
            drive_id: The ID of the drive
            folder_name: Name of the folder to create
            parent_folder_id: Parent folder ID (defaults to root)
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            parent_id = parent_folder_id or "root"
            body = DriveItem()
            body.name = folder_name
            body.folder = Folder()
            body.additional_data = {
                "@microsoft.graph.conflictBehavior": "rename",
            }
            response = await self.client.drives_items_create_children(
                drive_id=drive_id,
                driveItem_id=parent_id,
                request_body=body,
            )
            print(f"Response: {response}")
            print(f"Response data: {response.data}")
            res_json = _response_json(response)
            print("--------------------------------")
            print(f"Response success: {res_json}")
            print("--------------------------------")
            if response.success:
                return True, _response_json(response)
            return False, self._serialize_response(response.data)
        except Exception as e:
            logger.error(f"Failed to create folder '{folder_name}': {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # File operations (no upload)
    # ------------------------------------------------------------------

    # @tool(
    #     app_name="onedrive",
    #     tool_name="delete_item",
    #     description="Delete a file or folder from OneDrive. Requires drive_id and item_id — resolve via get_drives and search_files first if unknown.",
    #     args_schema=DeleteItemInput,
    #     when_to_use=[
    #         "User wants to delete a file or folder in OneDrive",
    #         "User says 'remove', 'trash', or 'delete' a OneDrive item",
    #         "Cascade: get_drives → search_files (to find item_id) → delete_item",
    #     ],
    #     when_not_to_use=[
    #         "User wants to move the item instead (use move_item)",
    #         "drive_id or item_id is unknown — call get_drives and/or search_files first to resolve them",
    #     ],
    #     primary_intent=ToolIntent.ACTION,
    #     typical_queries=[
    #         "Delete this file from OneDrive",
    #         "Remove a folder from my OneDrive",
    #         "Trash the old report in OneDrive",
    #     ],
    #     category=ToolCategory.FILE_STORAGE,
    # )
    # async def delete_item(
    #     self,
    #     drive_id: str,
    #     item_id: str,
    # ) -> tuple[bool, str]:
    #     """Delete a file or folder from OneDrive

    #     Args:
    #         drive_id: The ID of the drive
    #         item_id: The ID of the item to delete
    #     Returns:
    #         tuple[bool, str]: Success flag and JSON response
    #     """
    #     try:
    #         response = await self.client.drives_delete_items(
    #             drive_id=drive_id,
    #             driveItem_id=item_id,
    #         )
    #         if response.success:
    #             return True, json.dumps({"message": f"Item {item_id} deleted successfully"})
    #         return False, _response_json(response)
    #     except Exception as e:
    #         logger.error(f"Failed to delete item {item_id}: {e}")
    #         return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="rename_item",
        description="Rename a file or folder in OneDrive. Requires drive_id and item_id — resolve via get_drives and search_files first if unknown.",
        args_schema=RenameItemInput,
        when_to_use=[
            "User wants to rename a file or folder in OneDrive",
            "User says 'rename', 'change the name of' a OneDrive item",
            "Cascade: get_drives → search_files (to find item_id) → rename_item",
        ],
        when_not_to_use=[
            "User wants to move a file (use move_item)",
            "User wants to copy a file (use copy_item)",
            "drive_id or item_id is unknown — call get_drives and/or search_files first to resolve them",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Rename this file to 'Final Report'",
            "Change the folder name in OneDrive",
            "Rename my OneDrive document",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def rename_item(
        self,
        drive_id: str,
        item_id: str,
        new_name: str,
    ) -> tuple[bool, str]:
        """Rename a file or folder in OneDrive

        Args:
            drive_id: The ID of the drive
            item_id: The ID of the item to rename
            new_name: The new name for the item
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            body = DriveItem()
            body.name = new_name
            response = await self.client.drives_update_items(
                drive_id=drive_id,
                driveItem_id=item_id,
                request_body=body,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to rename item {item_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="onedrive",
        tool_name="move_item",
        description="Move a file or folder to a different folder in OneDrive. Requires drive_id and item_id — resolve via get_drives and search_files first if unknown.",
        args_schema=MoveItemInput,
        when_to_use=[
            "User wants to move a file or folder to another location in OneDrive",
            "User says 'move', 'transfer', or 'relocate' a OneDrive item",
            "Cascade: get_drives → search_files (to find item_id and destination folder_id) → move_item",
        ],
        when_not_to_use=[
            "User wants to copy (use copy_item)",
            "User wants to rename without moving (use rename_item)",
            "drive_id or item_id is unknown — call get_drives and/or search_files first to resolve them",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Move this file to the Archive folder",
            "Transfer my document to a different OneDrive folder",
            "Move the report into the 2025 folder",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def move_item(
        self,
        drive_id: str,
        item_id: str,
        new_parent_id: str,
        new_name: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Move a file or folder to a different folder

        Args:
            drive_id: The ID of the drive
            item_id: The ID of the item to move
            new_parent_id: The ID of the destination folder
            new_name: Optional new name after moving
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            resolved_id, warning = await self._resolve_folder_id(drive_id, new_parent_id)

            body = DriveItem()
            body.parent_reference = ItemReference()
            body.parent_reference.id = resolved_id
            if new_name:
                body.name = new_name

            response = await self.client.drives_update_items(
                drive_id=drive_id,
                driveItem_id=item_id,
                request_body=body,
            )
            if response.success:
                result = _response_json(response)
                if warning:
                    out = json.loads(result)
                    out["warning"] = warning
                    return True, json.dumps(out)
                return True, result
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to move item {item_id}: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Version history
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_file_versions",
        description="Get the version history of a file in OneDrive",
        args_schema=GetVersionsInput,
        when_to_use=[
            "User wants to see version history of a OneDrive file",
            "User asks 'show previous versions' or 'what changes were made'",
            "User wants to audit or restore an older version of a file",
        ],
        when_not_to_use=[
            "User wants file metadata (use get_file)",
            "User wants to search files (use search_files)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show version history for this OneDrive file",
            "List previous versions of my document",
            "What versions exist for this file?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_file_versions(
        self,
        drive_id: str,
        item_id: str,
    ) -> tuple[bool, str]:
        """Get the version history of a OneDrive file

        Args:
            drive_id: The ID of the drive
            item_id: The ID of the file
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_items_list_versions(
                drive_id=drive_id,
                driveItem_id=item_id,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get versions for item {item_id}: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Root folder
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_root_folder",
        description="Get the root folder of a OneDrive drive",
        args_schema=GetDriveInput,
        when_to_use=[
            "User wants to navigate to the root of a OneDrive drive",
            "User asks 'show me the top-level folder' or 'go to root'",
            "Resolving the root drive item to start browsing",
        ],
        when_not_to_use=[
            "User wants to list files in root (use get_files)",
            "User already has a folder ID (use get_folder_children)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get the root folder of my OneDrive",
            "Navigate to the top of my drive",
            "Show root drive item",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_root_folder(
        self,
        drive_id: str,
        select: Optional[str] = None,
        expand: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get the root folder of a OneDrive drive

        Args:
            drive_id: The ID of the drive
            select: Comma-separated list of fields to return
            expand: Related entities to expand
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_get_root(
                drive_id=drive_id,
                select=select,
                expand=expand,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get root folder for drive {drive_id}: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Followed items
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_followed_items",
        description="Get files and folders the current user is following in OneDrive",
        args_schema=GetRecentFilesInput,
        when_to_use=[
            "User asks 'what files am I following in OneDrive'",
            "User wants to see bookmarked or followed items",
            "User mentions 'followed' files or folders in OneDrive context",
        ],
        when_not_to_use=[
            "User wants their own files (use get_files)",
            "User wants files shared with them (use get_shared_with_me)",
            "User wants recent files (use get_recent_files)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show files I'm following in OneDrive",
            "List my followed OneDrive items",
            "What OneDrive files have I bookmarked?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_followed_items(
        self,
        drive_id: str,
        top: Optional[int] = None,
        select: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get files and folders the user is following in OneDrive

        Args:
            drive_id: The ID of the drive
            top: Maximum number of results to return
            select: Comma-separated list of fields to return
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_list_following(
                drive_id=drive_id,
                top=top,
                select=select,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(f"Failed to get followed items: {e}")
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Specific version retrieval
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="get_file_version",
        description="Get details about a specific version of a OneDrive file",
        args_schema=GetSpecificVersionInput,
        when_to_use=[
            "User wants to inspect a specific version of a file",
            "User mentions a version ID and wants its metadata or content info",
            "Auditing a particular historical snapshot of a OneDrive file",
        ],
        when_not_to_use=[
            "User wants all versions (use get_file_versions)",
            "User wants to restore a version (use restore_file_version)",
            "File ID is unknown (use get_files or search_files first)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show details for version 2.0 of this file",
            "Get info about a specific OneDrive file version",
            "What's in version ID xyz of my document?",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def get_file_version(
        self,
        drive_id: str,
        item_id: str,
        version_id: str,
        select: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get details about a specific version of a OneDrive file

        Args:
            drive_id: The ID of the drive
            item_id: The ID of the file
            version_id: The ID of the version to retrieve
            select: Comma-separated list of fields to return
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_items_get_versions(
                drive_id=drive_id,
                driveItem_id=item_id,
                driveItemVersion_id=version_id,
                select=select,
            )
            if response.success:
                return True, _response_json(response)
            return False, _response_json(response)
        except Exception as e:
            logger.error(
                f"Failed to get version {version_id} for item {item_id}: {e}"
            )
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Restore version
    # ------------------------------------------------------------------

    @tool(
        app_name="onedrive",
        tool_name="restore_file_version",
        description="Restore a specific historical version of a OneDrive file, making it the current version",
        args_schema=RestoreVersionInput,
        when_to_use=[
            "User wants to roll back a OneDrive file to an older version",
            "User says 'restore version', 'undo changes', or 'go back to a previous version'",
            "User needs to recover an earlier snapshot of a document",
        ],
        when_not_to_use=[
            "User wants to view versions (use get_file_versions)",
            "User wants to get version details (use get_file_version)",
            "File ID or version ID is unknown (retrieve them first)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Restore the file to version 2.0",
            "Roll back my OneDrive document to yesterday's version",
            "Undo recent changes and restore an older version",
        ],
        category=ToolCategory.FILE_STORAGE,
    )
    async def restore_file_version(
        self,
        drive_id: str,
        item_id: str,
        version_id: str,
    ) -> tuple[bool, str]:
        """Restore a specific version of a OneDrive file

        Args:
            drive_id: The ID of the drive
            item_id: The ID of the file
            version_id: The ID of the version to restore
        Returns:
            tuple[bool, str]: Success flag and JSON response
        """
        try:
            response = await self.client.drives_drive_items_drive_item_restore(
                drive_id=drive_id,
                driveItem_id=item_id,
                driveItemVersion_id=version_id,
            )
            if response.success:
                return True, json.dumps({
                    "message": f"Version {version_id} of item {item_id} restored successfully"
                })
            return False, _response_json(response)
        except Exception as e:
            logger.error(
                f"Failed to restore version {version_id} for item {item_id}: {e}"
            )
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Permanent delete
    # ------------------------------------------------------------------

    # @tool(
    #     app_name="onedrive",
    #     tool_name="permanent_delete_item",
    #     description="Permanently delete a file or folder from OneDrive, bypassing the recycle bin. This action is irreversible. Requires drive_id and item_id — resolve via get_drives and search_files first if unknown.",
    #     args_schema=DeleteItemInput,
    #     when_to_use=[
    #         "User explicitly wants to permanently delete a OneDrive item with no recovery option",
    #         "User says 'permanently delete', 'hard delete', or 'bypass recycle bin'",
    #         "User needs to purge a file completely from OneDrive",
    #     ],
    #     when_not_to_use=[
    #         "User wants a normal (soft) delete that allows recovery (use delete_item)",
    #         "User is unsure — always prefer delete_item for safety",
    #         "drive_id or item_id is unknown — call get_drives and/or search_files first to resolve them",
    #     ],
    #     primary_intent=ToolIntent.ACTION,
    #     typical_queries=[
    #         "Permanently delete this OneDrive file",
    #         "Hard delete this folder — I don't want it recoverable",
    #         "Purge this item from OneDrive completely",
    #     ],
    #     category=ToolCategory.FILE_STORAGE,
    # )
    # async def permanent_delete_item(
    #     self,
    #     drive_id: str,
    #     item_id: str,
    # ) -> tuple[bool, str]:
    #     """Permanently delete a file or folder from OneDrive (no recycle bin)

    #     Args:
    #         drive_id: The ID of the drive
    #         item_id: The ID of the file or folder to permanently delete
    #     Returns:
    #         tuple[bool, str]: Success flag and JSON response
    #     """
    #     try:
    #         response = await self.client.drives_drive_items_drive_item_permanent_delete(
    #             drive_id=drive_id,
    #             driveItem_id=item_id,
    #         )
    #         if response.success:
    #             return True, json.dumps({
    #                 "message": f"Item {item_id} permanently deleted successfully"
    #             })
    #         return False, _response_json(response)
    #     except Exception as e:
    #         logger.error(f"Failed to permanently delete item {item_id}: {e}")
    #         return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Check in
    # ------------------------------------------------------------------

    # @tool(
    #     app_name="onedrive",
    #     tool_name="checkin_file",
    #     description="Check in a checked-out OneDrive file, making it available to others and optionally publishing a version comment",
    #     args_schema=CheckinFileInput,
    #     when_to_use=[
    #         "User wants to check in a file they previously checked out in OneDrive",
    #         "User says 'check in', 'release lock', or 'publish changes' on a OneDrive file",
    #         "User finished editing an exclusively locked OneDrive document",
    #     ],
    #     when_not_to_use=[
    #         "File is not currently checked out (use get_file to verify status first)",
    #         "User wants to check out a file (use checkout_file)",
    #     ],
    #     primary_intent=ToolIntent.ACTION,
    #     typical_queries=[
    #         "Check in my OneDrive document",
    #         "Release the lock on this file and check it in",
    #         "Publish my changes and check in the file",
    #     ],
    #     category=ToolCategory.FILE_STORAGE,
    # )
    # async def checkin_file(
    #     self,
    #     drive_id: str,
    #     item_id: str,
    #     comment: Optional[str] = None,
    #     check_in_as: Optional[str] = None,
    # ) -> tuple[bool, str]:
    #     """Check in a checked-out OneDrive file

    #     Args:
    #         drive_id: The ID of the drive
    #         item_id: The ID of the file to check in
    #         comment: Optional comment to attach to the new version
    #         check_in_as: Version type to check in as ('published' or 'unspecified')
    #     Returns:
    #         tuple[bool, str]: Success flag and JSON response
    #     """
    #     try:
    #         body = CheckinPostRequestBody()
    #         if comment:
    #             body.comment = comment
    #         if check_in_as:
    #             body.check_in_as = check_in_as

    #         response = await self.client.drives_drive_items_drive_item_checkin(
    #             drive_id=drive_id,
    #             driveItem_id=item_id,
    #             request_body=body,
    #         )
    #         if response.success:
    #             return True, json.dumps({
    #                 "message": f"Item {item_id} checked in successfully"
    #             })
    #         return False, _response_json(response)
    #     except Exception as e:
    #         logger.error(f"Failed to check in item {item_id}: {e}")
    #         return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Check out
    # ------------------------------------------------------------------

    # @tool(
    #     app_name="onedrive",
    #     tool_name="checkout_file",
    #     description="Check out a OneDrive file to get an exclusive edit lock, preventing others from modifying it until it is checked back in",
    #     args_schema=DeleteItemInput,
    #     when_to_use=[
    #         "User wants to lock a OneDrive file for exclusive editing",
    #         "User says 'check out', 'lock this file', or 'get exclusive access' in OneDrive",
    #         "User needs to ensure nobody else edits a document while they work on it",
    #     ],
    #     when_not_to_use=[
    #         "File is already checked out by this user (check file status first)",
    #         "User wants to check in a file (use checkin_file)",
    #         "File ID is unknown (use get_files or search_files first)",
    #     ],
    #     primary_intent=ToolIntent.ACTION,
    #     typical_queries=[
    #         "Check out this OneDrive file so I can edit it exclusively",
    #         "Lock the document so no one else can change it",
    #         "Get exclusive access to this OneDrive file",
    #     ],
    #     category=ToolCategory.FILE_STORAGE,
    # )
    # async def checkout_file(
    #     self,
    #     drive_id: str,
    #     item_id: str,
    # ) -> tuple[bool, str]:
    #     """Check out a OneDrive file for exclusive editing

    #     Args:
    #         drive_id: The ID of the drive
    #         item_id: The ID of the file to check out
    #     Returns:
    #         tuple[bool, str]: Success flag and JSON response
    #     """
    #     try:
    #         response = await self.client.drives_drive_items_drive_item_checkout(
    #             drive_id=drive_id,
    #             driveItem_id=item_id,
    #         )
    #         if response.success:
    #             return True, json.dumps({
    #                 "message": f"Item {item_id} checked out successfully"
    #             })
    #         return False, _response_json(response)
    #     except Exception as e:
    #         logger.error(f"Failed to check out item {item_id}: {e}")
    #         return False, json.dumps({"error": str(e)})
