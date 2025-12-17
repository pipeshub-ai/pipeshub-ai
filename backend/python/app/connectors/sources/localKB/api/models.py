"""Knowledge Base Request and Response Models"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.config.constants.arangodb import ProgressStatus


class PermissionRole(str, Enum):
    """Valid permission roles for knowledge base access"""

    OWNER = "OWNER"
    ORGANIZER = "ORGANIZER"
    FILEORGANIZER = "FILEORGANIZER"
    WRITER = "WRITER"
    COMMENTER = "COMMENTER"
    READER = "READER"


class RecordType(str, Enum):
    """Valid record types"""

    FILE = "FILE"
    DRIVE = "DRIVE"
    WEBPAGE = "WEBPAGE"
    COMMENT = "COMMENT"
    MESSAGE = "MESSAGE"
    MAIL = "MAIL"
    OTHERS = "OTHERS"




class SortOrder(str, Enum):
    """Valid sort order values"""

    ASC = "asc"
    DESC = "desc"


class SourceType(str, Enum):
    """Valid source types for record filtering"""

    ALL = "all"
    LOCAL = "local"
    CONNECTOR = "connector"


# Request Models
class CreateKnowledgeBaseRequest(BaseModel):
    """Request model for creating a knowledge base"""

    name: str = Field(..., description="Name of the knowledge base", min_length=1, max_length=255)



class UpdateKnowledgeBaseRequest(BaseModel):
    """Request model for updating a knowledge base"""

    groupName: str | None = Field(None, description="Name of the knowledge base", min_length=1, max_length=255)


class CreateFolderRequest(BaseModel):
    """Request model for creating a folder"""

    userId: str = Field(..., description ="User id", min_length=1)
    orgId: str = Field(..., description ="Org id", min_length=1)
    name: str = Field(..., description="Name of the folder", min_length=1, max_length=255)


class UpdateFolderRequest(BaseModel):
    """Request model for updating a folder"""

    name: str = Field(..., description="Name of the folder", min_length=1, max_length=255)


class CreatePermissionRequest(BaseModel):
    """Request model for creating permissions"""

    requesterId : str = Field(..., description ="User id granting others access", min_length=1)
    userIds: list[str] | None = Field(None, description="List of user IDs to grant permissions to", min_items=0)
    teamIds: list[str] | None = Field(None, description="List of team IDs to grant permissions to", min_items=0)
    role: PermissionRole = Field(..., description="Role to grant")


class UpdatePermissionRequest(BaseModel):
    """Request model for updating a permission"""

    requesterId : str = Field(..., description ="User id granting others access", min_length=1)
    userIds : list[str] | None = Field(None, description ="User id", min_items=0)
    teamIds : list[str] | None = Field(None, description ="Team id", min_items=0)
    role: PermissionRole = Field(..., description="New role")

class RemovePermissionRequest(BaseModel):
    """Request model for removing a permission"""

    requesterId : str = Field(..., description ="User id granting others access", min_length=1)
    userIds : list[str] | None = Field(None, description ="User id", min_items=0)
    teamIds : list[str] | None = Field(None, description ="Team id", min_items=0)

class CreateRecordsRequest(BaseModel):
    """Request model for creating records in a folder"""

    userId : str = Field(..., description ="User id", min_length=1)
    records: list[dict[str, Any]] = Field(..., description="List of record metadata dicts")
    fileRecords: list[dict[str, Any]] = Field(..., description="List of file metadata dicts (same length as records)")


class UpdateRecordRequest(BaseModel):
    """Request model for updating a record in a folder"""

    userId : str = Field(..., description ="User id", min_length=1)
    updates: dict[str, Any] = Field(..., description="Fields to update in the record")
    fileMetadata: dict[str, Any] | None = Field(None, description="Optional file metadata for file update")

class DeleteRecordRequest(BaseModel):
    """Request model for updating a record in a folder"""

    userId : str = Field(..., description ="User id", min_length=1)
    recordIds: list[str] = Field(..., description="List of user IDs to grant permissions to",min_items=1)

# Response Models
class FileRecordResponse(BaseModel):
    """Response model for file record information"""

    id: str = Field(..., description="File record ID")
    name: str = Field(..., description="File name")
    extension: str | None = Field(None, description="File extension")
    mimeType: str | None = Field(None, description="MIME type")
    sizeInBytes: int | None = Field(None, description="File size in bytes")
    isFile: bool = Field(..., description="Whether this is a file")
    webUrl: str | None = Field(None, description="Web URL")

class FolderResponse(BaseModel):
    """Response model for folder information"""

    id: str = Field(..., description="Folder ID")
    name: str = Field(..., description="Folder name")
    createdAtTimestamp: int | None = Field(None, description="Creation timestamp")
    # path: Optional[str] = Field(None, description="Folder path")
    webUrl: str | None = Field(None, description="Web URL")

class PermissionContents(BaseModel):
    """Response model for permission information"""

    role: PermissionRole | None = Field(None, description="Permission role (None for team permissions)")
    type: str = Field(..., description="Permission type")


class RecordResponse(BaseModel):
    """Response model for record information"""

    id: str = Field(..., description="Record ID")
    externalRecordId: str = Field(..., description="External record ID")
    externalRevisionId: str | None = Field(None, description="External revision ID")
    recordName: str = Field(..., description="Record name")
    recordType: RecordType = Field(..., description="Record type")
    origin: str = Field(..., description="Origin")
    connectorName: str | None = Field(..., description="Connector name")
    indexingStatus: ProgressStatus = Field(..., description="Indexing status")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")
    sourceCreatedAtTimestamp: int | None = Field(None, description="Source creation timestamp")
    sourceLastModifiedTimestamp: int | None = Field(None, description="Source modification timestamp")
    orgId: str = Field(..., description="Organization ID")
    version: int = Field(..., description="Version")
    isDeleted: bool = Field(..., description="Deletion status")
    deletedByUserId: str | None = Field(None, description="User who deleted")
    isLatestVersion: bool | None = Field(..., description="Whether this is the latest version")
    webUrl: str | None = Field(None, description="Web URL")
    fileRecord: FileRecordResponse | None = Field(None, description="Associated file record")
    folder: FolderResponse | None = Field(None, description="Folder information")
    permission: PermissionContents | None = Field(None, description="Permission information")

class KnowledgeBaseResponse(BaseModel):
    """Response model for knowledge base information"""

    id: str = Field(..., description="Knowledge base ID")
    name: str = Field(..., description="Knowledge base name")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")
    createdBy: str = Field(..., description="Created by user ID")
    userRole: str | None = Field(None, description="User's role in this KB")
    folders: list[FolderResponse] | None = Field(None, description="List of folders")

class KnowledgeBasePaginationResponse(BaseModel):
    """Response model for knowledge base pagination information"""

    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Items per page")
    totalCount: int = Field(..., description="Total number of knowledge bases")
    totalPages: int = Field(..., description="Total number of pages")
    hasNext: bool = Field(..., description="Whether there is a next page")
    hasPrev: bool = Field(..., description="Whether there is a previous page")


class KnowledgeBaseFiltersResponse(BaseModel):
    """Response model for knowledge base filters"""

    applied: dict[str, Any] = Field(..., description="Currently applied filters")
    available: dict[str, list[str]] = Field(..., description="Available filter options")


class ListKnowledgeBaseResponse(BaseModel):
    """Response model for paginated knowledge base listing"""

    knowledgeBases: list[KnowledgeBaseResponse] = Field(..., description="List of knowledge bases")
    pagination: KnowledgeBasePaginationResponse = Field(..., description="Pagination information")
    filters: KnowledgeBaseFiltersResponse = Field(..., description="Filter information")


class PermissionResponse(BaseModel):
    """Response model for permission information"""

    id: str = Field(..., description="UUID")
    userId: str | None = Field(None, description="User ID")
    email: str | None = Field(None, description="User email")
    name: str | None = Field(None, description="User name")
    role: PermissionRole | None = Field(None, description="Permission role (None for team permissions)")
    type: str = Field(..., description="Permission type")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")


class FolderContentsResponse(BaseModel):
    """Response model for folder contents"""

    folder: FolderResponse = Field(..., description="Folder information")
    contents: list[RecordResponse] = Field(..., description="List of records in folder")
    totalItems: int = Field(..., description="Total number of items")


class PaginationResponse(BaseModel):
    """Response model for pagination information"""

    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Records per page")
    totalCount: int = Field(..., description="Total number of records")
    totalPages: int = Field(..., description="Total number of pages")


class AppliedFiltersResponse(BaseModel):
    """Response model for applied filters"""

    search: str | None = Field(None, description="Search term")
    recordTypes: list[str] | None = Field(None, description="Applied record type filters")
    origins: list[str] | None = Field(None, description="Applied origin filters")
    connectors: list[str] | None = Field(None, description="Applied connector filters")
    indexingStatus: list[str] | None = Field(None, description="Applied indexing status filters")
    permissions: list[str] | None = Field(None, description="Applied permission filters")
    source: str | None = Field(None, description="Applied source filter")
    dateRange: dict[str, int | None] | None = Field(None, description="Applied date range")


class AvailableFiltersResponse(BaseModel):
    """Response model for available filters"""

    recordTypes: list[str] = Field(..., description="Available record types")
    origins: list[str] = Field(..., description="Available origins")
    connectors: list[str] = Field(..., description="Available connectors")
    indexingStatus: list[str] = Field(..., description="Available indexing statuses")
    permissions: list[str] = Field(..., description="Available permissions")


class FiltersResponse(BaseModel):
    """Response model for filters"""

    applied: AppliedFiltersResponse = Field(..., description="Applied filters")
    available: AvailableFiltersResponse = Field(..., description="Available filters")


class ListRecordsResponse(BaseModel):
    """Response model for listing records"""

    records: list[RecordResponse] = Field(..., description="List of records")
    pagination: PaginationResponse = Field(..., description="Pagination information")
    filters: FiltersResponse = Field(..., description="Filter information")


class CreateKnowledgeBaseResponse(BaseModel):
    """Response model for creating a knowledge base"""

    id: str = Field(..., description="Knowledge base ID")
    name: str = Field(..., description="Knowledge base name")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")
    userRole: str | None = Field(None, description="User's role in this KB")

class CreateFolderResponse(BaseModel):
    """Response model for creating a folder"""

    id: str = Field(..., description="Folder ID")
    name: str = Field(..., description="Folder name")
    webUrl: str = Field(..., description="Web URL")
    # path: str = Field(..., description="Folder path")
    # parentFolderId: int = Field(..., description="Creation timestamp")


class CreateRecordsResponse(BaseModel):
    """Response model for creating records"""

    success: bool = Field(..., description="Success status")
    recordCount: int = Field(..., description="Number of records created")
    insertedRecordIds: list[str] = Field(..., description="List of inserted record IDs")
    insertedFileIds: list[str] = Field(..., description="List of inserted file IDs")
    folderId: str | None = Field(None, description="Folder ID")
    kbId: str = Field(..., description="Knowledge base ID")


class UpdateRecordResponse(BaseModel):
    """Response model for a successful record update operation.
    Reflects the detailed context returned by the service.
    """

    success: bool = Field(..., description="Indicates if the operation was successful.")
    updatedRecord: dict[str, Any] = Field(..., description="The full document of the updated record.")
    fileUpdated: bool = Field(..., description="True if the associated file metadata was also updated, otherwise False.")
    updatedFile: dict[str, Any] | None = Field(None, description="The full document of the updated file record, if applicable.")
    recordId: str = Field(..., description="The ID of the record that was updated.")
    timestamp: int = Field(..., description="The epoch timestamp (in ms) of when the update occurred.")
    location: str = Field(..., description="The location of the record, e.g., 'kb_root' or 'folder'.")
    folderId: str | None = Field(None, description="The ID of the parent folder, if the record is in a folder.")
    kb: dict[str, Any] = Field(..., description="Information about the knowledge base containing the record.")
    userPermission: str = Field(..., description="The permission role of the user who performed the update.")

class DeleteRecordResponse(BaseModel):
    """Response model for deleting a record"""

    success: bool = Field(..., description="Success status")
    message: str = Field(..., description="Response message")
    deleteType: str = Field(..., description="Type of deletion")


class CreatePermissionsResponse(BaseModel):
    """Response model for creating permissions"""

    success: bool = Field(..., description="Success status")
    grantedCount: int = Field(..., description="Number of users granted permissions")
    grantedUsers: list[str] = Field(..., description="List of users granted permissions")
    grantedTeams: list[str] = Field(..., description="List of teams granted permissions")
    role: str = Field(..., description="Granted role")
    kbId: str = Field(..., description="Knowledge base ID")
    details: dict[str, Any] = Field(..., description="Details of the permissions created")


class UpdatePermissionResponse(BaseModel):
    """Response model for updating a permission"""

    success: bool = Field(..., description="Success status")
    userIds: list[str] = Field(..., description="User ID")
    teamIds: list[str] = Field(..., description="Team ID")
    newRole: str = Field(..., description="New role")
    kbId: str = Field(..., description="Knowledge base ID")


class RemovePermissionResponse(BaseModel):
    """Response model for removing a permission"""

    success: bool = Field(..., description="Success status")
    userIds: list[str] = Field(..., description="User ID")
    teamIds: list[str] = Field(..., description="Team ID")
    kbId: str = Field(..., description="Knowledge base ID")


class ListPermissionsResponse(BaseModel):
    """Response model for listing permissions"""

    success: bool = Field(..., description="Success status")
    permissions: list[PermissionResponse] = Field(..., description="List of permissions")
    kbId: str = Field(..., description="Knowledge base ID")
    totalCount: int = Field(..., description="Total number of permissions")


class ErrorResponse(BaseModel):
    """Response model for errors"""

    success: bool = Field(False, description="Success status")
    reason: str = Field(..., description="Error reason")
    code: str = Field(..., description="Error code")


class SuccessResponse(BaseModel):
    """Response model for successful operations"""

    success: bool = Field(True, description="Success status")
    message: str | None = Field(None, description="Success message")

class ListAllRecordsResponse(ListRecordsResponse):
    """Response model for listing all records (across KBs)"""



class FileUploadResponse(BaseModel):
    """Response model for file upload"""

    success: bool = Field(..., description="Success status")
    fileId: str | None = Field(None, description="Uploaded file ID")
    fileName: str | None = Field(None, description="File name")
    fileSize: int | None = Field(None, description="File size in bytes")
    message: str | None = Field(None, description="Response message")
    error: str | None = Field(None, description="Error message if any")


class FileData(BaseModel):
    record: dict
    fileRecord: dict
    filePath: str
    lastModified: int

class UploadRecordsInKBRequest(BaseModel):
    userId: str
    orgId: str
    files: list[FileData]

class FolderInfo(BaseModel):
    # path: str
    id: str

class UploadRecordsinKBResponse(BaseModel):
    success: bool = Field(..., description="Success status")
    message: str = Field(None, description="Message")
    totalCreated: int = Field(None, description="Total created files")
    foldersCreated: int = Field(None, description="Total created folders")
    createdFolders: list[FolderInfo] = Field(None, description="Created folders info")
    failedFiles : list[str] = Field(None, description="Failed files")
    kbId : str = Field(None, description="Knowledge base id")
    parentFolderId : str | None = Field(None, description="Parent folder Id")


class UploadRecordsInFolderRequest(BaseModel):
    userId: str
    orgId: str
    files: list[FileData]

class UploadRecordsinFolderResponse(BaseModel):
    success: bool = Field(..., description="Success status")
    message: str = Field(None, description="Message")
    totalCreated: int = Field(None, description="Total created files")
    foldersCreated: int = Field(None, description="Total created folders")
    createdFolders: list[FolderInfo] = Field(None, description="Created folders info")
    failedFiles : list[str] = Field(None, description="Failed files")
    kbId : str = Field(None, description="Knowledge base id")
    parentFolderId : str | None = Field(None, description="Parent folder Id")
