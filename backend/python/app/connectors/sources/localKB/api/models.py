"""Knowledge Base Request and Response Models"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
    MESSAGE = "MESSAGE"
    MAIL = "MAIL"
    OTHERS = "OTHERS"


class IndexingStatus(str, Enum):
    """Valid indexing status values"""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    MANUAL_SYNC = "MANUAL_SYNC"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"


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
    groupName: Optional[str] = Field(None, description="Name of the knowledge base", min_length=1, max_length=255)


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
    userIds: Optional[List[str]] = Field(None, description="List of user IDs to grant permissions to", min_items=0)
    teamIds: Optional[List[str]] = Field(None, description="List of team IDs to grant permissions to", min_items=0)
    role: PermissionRole = Field(..., description="Role to grant")


class UpdatePermissionRequest(BaseModel):
    """Request model for updating a permission"""
    requesterId : str = Field(..., description ="User id granting others access", min_length=1)
    userIds : Optional[List[str]] = Field(None, description ="User id", min_items=0)
    teamIds : Optional[List[str]] = Field(None, description ="Team id", min_items=0)
    role: PermissionRole = Field(..., description="New role")

class RemovePermissionRequest(BaseModel):
    """Request model for removing a permission"""
    requesterId : str = Field(..., description ="User id granting others access", min_length=1)
    userIds : Optional[List[str]] = Field(None, description ="User id", min_items=0)
    teamIds : Optional[List[str]] = Field(None, description ="Team id", min_items=0)

class CreateRecordsRequest(BaseModel):
    """Request model for creating records in a folder"""
    userId : str = Field(..., description ="User id", min_length=1)
    records: List[Dict[str, Any]] = Field(..., description="List of record metadata dicts")
    fileRecords: List[Dict[str, Any]] = Field(..., description="List of file metadata dicts (same length as records)")


class UpdateRecordRequest(BaseModel):
    """Request model for updating a record in a folder"""
    userId : str = Field(..., description ="User id", min_length=1)
    updates: Dict[str, Any] = Field(..., description="Fields to update in the record")
    fileMetadata: Optional[Dict[str, Any]] = Field(None, description="Optional file metadata for file update")

class DeleteRecordRequest(BaseModel):
    """Request model for updating a record in a folder"""
    userId : str = Field(..., description ="User id", min_length=1)
    recordIds: List[str] = Field(..., description="List of user IDs to grant permissions to",min_items=1)

# Response Models
class FileRecordResponse(BaseModel):
    """Response model for file record information"""
    id: str = Field(..., description="File record ID")
    name: str = Field(..., description="File name")
    extension: Optional[str] = Field(None, description="File extension")
    mimeType: Optional[str] = Field(None, description="MIME type")
    sizeInBytes: Optional[int] = Field(None, description="File size in bytes")
    isFile: bool = Field(..., description="Whether this is a file")
    webUrl: Optional[str] = Field(None, description="Web URL")

class FolderResponse(BaseModel):
    """Response model for folder information"""
    id: str = Field(..., description="Folder ID")
    name: str = Field(..., description="Folder name")
    createdAtTimestamp: Optional[int] = Field(None, description="Creation timestamp")
    # path: Optional[str] = Field(None, description="Folder path")
    webUrl: Optional[str] = Field(None, description="Web URL")

class PermissionContents(BaseModel):
    """Response model for permission information"""
    role: PermissionRole = Field(..., description="Permission role")
    type: str = Field(..., description="Permission type")


class RecordResponse(BaseModel):
    """Response model for record information"""
    id: str = Field(..., description="Record ID")
    externalRecordId: str = Field(..., description="External record ID")
    externalRevisionId: Optional[str] = Field(None, description="External revision ID")
    recordName: str = Field(..., description="Record name")
    recordType: RecordType = Field(..., description="Record type")
    origin: str = Field(..., description="Origin")
    connectorName: Optional[str] = Field(..., description="Connector name")
    indexingStatus: IndexingStatus = Field(..., description="Indexing status")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")
    sourceCreatedAtTimestamp: Optional[int] = Field(None, description="Source creation timestamp")
    sourceLastModifiedTimestamp: Optional[int] = Field(None, description="Source modification timestamp")
    orgId: str = Field(..., description="Organization ID")
    version: int = Field(..., description="Version")
    isDeleted: bool = Field(..., description="Deletion status")
    deletedByUserId: Optional[str] = Field(None, description="User who deleted")
    isLatestVersion: Optional[bool] = Field(..., description="Whether this is the latest version")
    webUrl: Optional[str] = Field(None, description="Web URL")
    fileRecord: Optional[FileRecordResponse] = Field(None, description="Associated file record")
    folder: Optional[FolderResponse] = Field(None, description="Folder information")
    permission: Optional[PermissionContents] = Field(None, description="Permission information")

class KnowledgeBaseResponse(BaseModel):
    """Response model for knowledge base information"""
    id: str = Field(..., description="Knowledge base ID")
    name: str = Field(..., description="Knowledge base name")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")
    createdBy: str = Field(..., description="Created by user ID")
    userRole: Optional[str] = Field(None, description="User's role in this KB")
    folders: Optional[List[FolderResponse]] = Field(None, description="List of folders")

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
    applied: Dict[str, Any] = Field(..., description="Currently applied filters")
    available: Dict[str, List[str]] = Field(..., description="Available filter options")


class ListKnowledgeBaseResponse(BaseModel):
    """Response model for paginated knowledge base listing"""
    knowledgeBases: List[KnowledgeBaseResponse] = Field(..., description="List of knowledge bases")
    pagination: KnowledgeBasePaginationResponse = Field(..., description="Pagination information")
    filters: KnowledgeBaseFiltersResponse = Field(..., description="Filter information")


class PermissionResponse(BaseModel):
    """Response model for permission information"""
    id: str = Field(..., description="UUID")
    userId: Optional[str] = Field(None, description="User ID")
    email: Optional[str] = Field(None, description="User email")
    name: Optional[str] = Field(None, description="User name")
    role: PermissionRole = Field(..., description="Permission role")
    type: str = Field(..., description="Permission type")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")


class FolderContentsResponse(BaseModel):
    """Response model for folder contents"""
    folder: FolderResponse = Field(..., description="Folder information")
    contents: List[RecordResponse] = Field(..., description="List of records in folder")
    totalItems: int = Field(..., description="Total number of items")


class PaginationResponse(BaseModel):
    """Response model for pagination information"""
    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Records per page")
    totalCount: int = Field(..., description="Total number of records")
    totalPages: int = Field(..., description="Total number of pages")


class AppliedFiltersResponse(BaseModel):
    """Response model for applied filters"""
    search: Optional[str] = Field(None, description="Search term")
    recordTypes: Optional[List[str]] = Field(None, description="Applied record type filters")
    origins: Optional[List[str]] = Field(None, description="Applied origin filters")
    connectors: Optional[List[str]] = Field(None, description="Applied connector filters")
    indexingStatus: Optional[List[str]] = Field(None, description="Applied indexing status filters")
    permissions: Optional[List[str]] = Field(None, description="Applied permission filters")
    source: Optional[str] = Field(None, description="Applied source filter")
    dateRange: Optional[Dict[str, Optional[int]]] = Field(None, description="Applied date range")


class AvailableFiltersResponse(BaseModel):
    """Response model for available filters"""
    recordTypes: List[str] = Field(..., description="Available record types")
    origins: List[str] = Field(..., description="Available origins")
    connectors: List[str] = Field(..., description="Available connectors")
    indexingStatus: List[str] = Field(..., description="Available indexing statuses")
    permissions: List[str] = Field(..., description="Available permissions")


class FiltersResponse(BaseModel):
    """Response model for filters"""
    applied: AppliedFiltersResponse = Field(..., description="Applied filters")
    available: AvailableFiltersResponse = Field(..., description="Available filters")


class ListRecordsResponse(BaseModel):
    """Response model for listing records"""
    records: List[RecordResponse] = Field(..., description="List of records")
    pagination: PaginationResponse = Field(..., description="Pagination information")
    filters: FiltersResponse = Field(..., description="Filter information")


class CreateKnowledgeBaseResponse(BaseModel):
    """Response model for creating a knowledge base"""
    id: str = Field(..., description="Knowledge base ID")
    name: str = Field(..., description="Knowledge base name")
    createdAtTimestamp: int = Field(..., description="Creation timestamp")
    updatedAtTimestamp: int = Field(..., description="Update timestamp")
    userRole: Optional[str] = Field(None, description="User's role in this KB")

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
    insertedRecordIds: List[str] = Field(..., description="List of inserted record IDs")
    insertedFileIds: List[str] = Field(..., description="List of inserted file IDs")
    folderId: Optional[str] = Field(None, description="Folder ID")
    kbId: str = Field(..., description="Knowledge base ID")


class UpdateRecordResponse(BaseModel):
    """
    Response model for a successful record update operation.
    Reflects the detailed context returned by the service.
    """
    success: bool = Field(..., description="Indicates if the operation was successful.")
    updatedRecord: Dict[str, Any] = Field(..., description="The full document of the updated record.")
    fileUpdated: bool = Field(..., description="True if the associated file metadata was also updated, otherwise False.")
    updatedFile: Optional[Dict[str, Any]] = Field(None, description="The full document of the updated file record, if applicable.")
    recordId: str = Field(..., description="The ID of the record that was updated.")
    timestamp: int = Field(..., description="The epoch timestamp (in ms) of when the update occurred.")
    location: str = Field(..., description="The location of the record, e.g., 'kb_root' or 'folder'.")
    folderId: Optional[str] = Field(None, description="The ID of the parent folder, if the record is in a folder.")
    kb: Dict[str, Any] = Field(..., description="Information about the knowledge base containing the record.")
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
    grantedUsers: List[str] = Field(..., description="List of users granted permissions")
    grantedTeams: List[str] = Field(..., description="List of teams granted permissions")
    role: str = Field(..., description="Granted role")
    kbId: str = Field(..., description="Knowledge base ID")
    details: Dict[str, Any] = Field(..., description="Details of the permissions created")


class UpdatePermissionResponse(BaseModel):
    """Response model for updating a permission"""
    success: bool = Field(..., description="Success status")
    userIds: List[str] = Field(..., description="User ID")
    teamIds: List[str] = Field(..., description="Team ID")
    newRole: str = Field(..., description="New role")
    kbId: str = Field(..., description="Knowledge base ID")


class RemovePermissionResponse(BaseModel):
    """Response model for removing a permission"""
    success: bool = Field(..., description="Success status")
    userIds: List[str] = Field(..., description="User ID")
    teamIds: List[str] = Field(..., description="Team ID")
    kbId: str = Field(..., description="Knowledge base ID")


class ListPermissionsResponse(BaseModel):
    """Response model for listing permissions"""
    success: bool = Field(..., description="Success status")
    permissions: List[PermissionResponse] = Field(..., description="List of permissions")
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
    message: Optional[str] = Field(None, description="Success message")

class ListAllRecordsResponse(ListRecordsResponse):
    """Response model for listing all records (across KBs)"""
    pass


class FileUploadResponse(BaseModel):
    """Response model for file upload"""
    success: bool = Field(..., description="Success status")
    fileId: Optional[str] = Field(None, description="Uploaded file ID")
    fileName: Optional[str] = Field(None, description="File name")
    fileSize: Optional[int] = Field(None, description="File size in bytes")
    message: Optional[str] = Field(None, description="Response message")
    error: Optional[str] = Field(None, description="Error message if any")


class FileData(BaseModel):
    record: dict
    fileRecord: dict
    filePath: str
    lastModified: int

class UploadRecordsInKBRequest(BaseModel):
    userId: str
    orgId: str
    files: List[FileData]

class FolderInfo(BaseModel):
    # path: str
    id: str

class UploadRecordsinKBResponse(BaseModel):
    success: bool = Field(..., description="Success status")
    message: str = Field(None, description="Message")
    totalCreated: int = Field(None, description="Total created files")
    foldersCreated: int = Field(None, description="Total created folders")
    createdFolders: List[FolderInfo] = Field(None, description="Created folders info")
    failedFiles : List[str] = Field(None, description="Failed files")
    kbId : str = Field(None, description="Knowledge base id")
    parentFolderId : Optional[str] = Field(None, description="Parent folder Id")


class UploadRecordsInFolderRequest(BaseModel):
    userId: str
    orgId: str
    files: List[FileData]

class UploadRecordsinFolderResponse(BaseModel):
    success: bool = Field(..., description="Success status")
    message: str = Field(None, description="Message")
    totalCreated: int = Field(None, description="Total created files")
    foldersCreated: int = Field(None, description="Total created folders")
    createdFolders: List[FolderInfo] = Field(None, description="Created folders info")
    failedFiles : List[str] = Field(None, description="Failed files")
    kbId : str = Field(None, description="Knowledge base id")
    parentFolderId : Optional[str] = Field(None, description="Parent folder Id")
