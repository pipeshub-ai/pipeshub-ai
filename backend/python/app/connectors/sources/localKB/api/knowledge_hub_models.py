"""Knowledge Hub Unified Browse API Request and Response Models"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """Valid node types in the knowledge hub hierarchy"""
    KB = "kb"
    FOLDER = "folder"
    APP = "app"
    RECORD_GROUP = "recordGroup"
    RECORD = "record"


class OriginType(str, Enum):
    """Valid origin types for nodes"""
    KB = "KB"
    CONNECTOR = "CONNECTOR"


class ViewMode(str, Enum):
    """Valid view modes for the API"""
    CHILDREN = "children"
    SEARCH = "search"


class SortField(str, Enum):
    """Valid sort fields"""
    NAME = "name"
    CREATED_AT = "createdAt"
    UPDATED_AT = "updatedAt"
    SIZE = "size"
    TYPE = "type"


class SortOrder(str, Enum):
    """Valid sort order values"""
    ASC = "asc"
    DESC = "desc"


class RecordTypeFilter(str, Enum):
    """Valid record types for filtering"""
    FILE = "FILE"
    WEBPAGE = "WEBPAGE"
    MESSAGE = "MESSAGE"
    EMAIL = "EMAIL"
    TICKET = "TICKET"
    COMMENT = "COMMENT"
    MAIL = "MAIL"
    OTHERS = "OTHERS"


class IndexingStatusFilter(str, Enum):
    """Valid indexing status values for filtering"""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QUEUED = "QUEUED"
    PAUSED = "PAUSED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"
    EMPTY = "EMPTY"


class ConnectorFilter(str, Enum):
    """Valid connector names for filtering"""
    JIRA = "JIRA"
    SLACK = "SLACK"
    DRIVE = "DRIVE"
    ONEDRIVE = "ONEDRIVE"
    CONFLUENCE = "CONFLUENCE"
    SHAREPOINT_ONLINE = "SHAREPOINT ONLINE"
    OUTLOOK = "OUTLOOK"
    GMAIL = "GMAIL"
    NOTION = "NOTION"
    BOX = "BOX"
    DROPBOX = "DROPBOX"
    SERVICENOW = "SERVICENOW"
    KNOWLEDGE_BASE = "KB"


class IncludeOption(str, Enum):
    """Valid include options for response expansions"""
    BREADCRUMBS = "breadcrumbs"
    COUNTS = "counts"
    AVAILABLE_FILTERS = "availableFilters"
    PERMISSIONS = "permissions"


# Request Models
class DateRangeFilter(BaseModel):
    """Date range filter with optional gte/lte bounds"""
    gte: Optional[int] = Field(None, description="Greater than or equal to (epoch ms)")
    lte: Optional[int] = Field(None, description="Less than or equal to (epoch ms)")


class SizeRangeFilter(BaseModel):
    """Size range filter with optional gte/lte bounds"""
    gte: Optional[int] = Field(None, description="Greater than or equal to (bytes)")
    lte: Optional[int] = Field(None, description="Less than or equal to (bytes)")


# Response Models
class ItemPermission(BaseModel):
    """Permission info for a single node item"""
    role: str = Field(..., description="User's role on this item (OWNER, WRITER, READER, etc.)")
    canEdit: bool = Field(..., description="Whether user can edit this item")
    canDelete: bool = Field(..., description="Whether user can delete this item")


class NodeItem(BaseModel):
    """Response model for a single node in the knowledge hub hierarchy"""
    id: str = Field(..., description="Unique identifier for the node")
    name: str = Field(..., description="Display name of the node")
    nodeType: NodeType = Field(..., description="Type of the node")
    parentId: Optional[str] = Field(None, description="ID of the parent node")
    origin: OriginType = Field(..., description="Origin type (KB or CONNECTOR)")
    connector: Optional[str] = Field(None, description="Connector name (only for CONNECTOR origin)")
    recordType: Optional[str] = Field(None, description="Record type (only when nodeType is record)")
    indexingStatus: Optional[str] = Field(None, description="Indexing status (only when nodeType is record)")
    createdAt: int = Field(..., description="Creation timestamp (epoch ms)")
    updatedAt: int = Field(..., description="Update timestamp (epoch ms)")
    sizeInBytes: Optional[int] = Field(None, description="File size in bytes (only for file records)")
    mimeType: Optional[str] = Field(None, description="MIME type (only for file records)")
    extension: Optional[str] = Field(None, description="File extension (only for file records)")
    webUrl: Optional[str] = Field(None, description="Web URL for the node")
    hasChildren: bool = Field(..., description="True if node has any children (for sidebar)")
    permission: Optional[ItemPermission] = Field(None, description="User's permission on this item")

    class Config:
        use_enum_values = True
        exclude_none = True


class CurrentNode(BaseModel):
    """Response model for the current node being browsed"""
    id: str = Field(..., description="Current node ID")
    name: str = Field(..., description="Current node name")
    nodeType: str = Field(..., description="Current node type (app, kb, recordGroup, folder, record)")
    subType: Optional[str] = Field(None, description="Sub-type: connector name for apps/recordGroups, recordType for records")

    class Config:
        exclude_none = True


class BreadcrumbItem(BaseModel):
    """Response model for a breadcrumb item"""
    id: str = Field(..., description="Node ID")
    name: str = Field(..., description="Node name")
    nodeType: str = Field(..., description="Node type (app, kb, recordGroup, folder, record)")
    subType: Optional[str] = Field(None, description="Sub-type: connector name for apps/recordGroups, recordType for records")

    class Config:
        exclude_none = True


class PaginationInfo(BaseModel):
    """Response model for pagination information"""
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Items per page")
    totalItems: int = Field(..., description="Total number of items")
    totalPages: int = Field(..., description="Total number of pages")
    hasNext: bool = Field(..., description="Whether there is a next page")
    hasPrev: bool = Field(..., description="Whether there is a previous page")


class FilterOption(BaseModel):
    """Response model for a filter option"""
    id: str = Field(..., description="Filter ID value to send in requests")
    label: str = Field(..., description="Display label for the filter")


class AvailableFilters(BaseModel):
    """Response model for available filter options"""
    nodeTypes: List[FilterOption] = Field(default_factory=list, description="Available node types")
    recordTypes: List[FilterOption] = Field(default_factory=list, description="Available record types")
    origins: List[FilterOption] = Field(default_factory=list, description="Available origins")
    connectors: List[FilterOption] = Field(default_factory=list, description="Available connectors (instances)")
    kbs: List[FilterOption] = Field(default_factory=list, description="Available Knowledge Bases")
    indexingStatus: List[FilterOption] = Field(default_factory=list, description="Available indexing statuses")
    sortBy: List[FilterOption] = Field(default_factory=list, description="Available sort fields")
    sortOrder: List[FilterOption] = Field(default_factory=list, description="Available sort orders")


class AppliedFilters(BaseModel):
    """Response model for applied filters"""
    q: Optional[str] = Field(None, description="Search query")
    nodeTypes: Optional[List[str]] = Field(None, description="Applied node type filters")
    recordTypes: Optional[List[str]] = Field(None, description="Applied record type filters")
    origins: Optional[List[str]] = Field(None, description="Applied origin filters")
    connectorIds: Optional[List[str]] = Field(None, description="Applied connector instance ID filters")
    kbIds: Optional[List[str]] = Field(None, description="Applied KB ID filters")
    indexingStatus: Optional[List[str]] = Field(None, description="Applied indexing status filters")
    createdAt: Optional[DateRangeFilter] = Field(None, description="Applied created date range")
    updatedAt: Optional[DateRangeFilter] = Field(None, description="Applied updated date range")
    size: Optional[SizeRangeFilter] = Field(None, description="Applied size range")
    sortBy: str = Field("name", description="Sort field")
    sortOrder: str = Field("asc", description="Sort order")

    class Config:
        exclude_none = True


class FiltersInfo(BaseModel):
    """Response model for filters information"""
    applied: AppliedFilters = Field(..., description="Currently applied filters")
    available: Optional[AvailableFilters] = Field(None, description="Available filter options")

    class Config:
        exclude_none = True


class CountItem(BaseModel):
    """A single count item with label and count"""
    label: str = Field(..., description="Label for this count (e.g., 'folders', 'files', 'pages')")
    count: int = Field(..., description="Number of items")


class CountsInfo(BaseModel):
    """Response model for counts information"""
    items: List[CountItem] = Field(..., description="Breakdown of counts by type")
    total: int = Field(..., description="Total number of nodes")


class PermissionsInfo(BaseModel):
    """Response model for permissions information"""
    role: str = Field(..., description="User's role")
    canUpload: bool = Field(..., description="Whether user can upload files")
    canCreateFolders: bool = Field(..., description="Whether user can create folders")
    canEdit: bool = Field(..., description="Whether user can edit")
    canDelete: bool = Field(..., description="Whether user can delete")
    canManagePermissions: bool = Field(..., description="Whether user can manage permissions")


class KnowledgeHubNodesResponse(BaseModel):
    """Response model for the Knowledge Hub nodes API"""
    success: bool = Field(..., description="Whether the request was successful")
    error: Optional[str] = Field(None, description="Error message if success is False")
    id: Optional[str] = Field(None, description="Current parent node ID (null for root)")
    currentNode: Optional[CurrentNode] = Field(None, description="The node being browsed (when parentId is provided)")
    parentNode: Optional[CurrentNode] = Field(None, description="The parent of currentNode (one level up)")
    items: List[NodeItem] = Field(default_factory=list, description="List of nodes")
    pagination: Optional[PaginationInfo] = Field(None, description="Pagination information")
    filters: Optional[FiltersInfo] = Field(None, description="Filter information")
    breadcrumbs: Optional[List[BreadcrumbItem]] = Field(None, description="Breadcrumb trail")
    counts: Optional[CountsInfo] = Field(None, description="Counts summary")
    permissions: Optional[PermissionsInfo] = Field(None, description="User permissions")

    class Config:
        # Exclude None values from JSON response
        exclude_none = True


class KnowledgeHubErrorResponse(BaseModel):
    """Response model for errors"""
    success: bool = Field(False, description="Success status")
    reason: str = Field(..., description="Error reason")
    code: Optional[int] = Field(None, description="Error code")

