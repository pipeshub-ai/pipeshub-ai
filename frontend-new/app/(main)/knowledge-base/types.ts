// Knowledge Base Types

import type { FileType, ItemStatus, SourceType, SortOrder, ViewMode } from '@/types';

// Re-export common types for convenience
export type { FileType, ItemStatus, SourceType, SortOrder, ViewMode };

export type KnowledgeBaseItemType = 'folder' | 'file';

export interface KnowledgeBase {
  id: string;
  name: string;
  description?: string;
  createdAt: string;
  updatedAt: string;
  itemCount: number;
  ownerId: string;
  isShared: boolean;
}

export interface KnowledgeBaseItem {
  id: string;
  name: string;
  type: KnowledgeBaseItemType;
  fileType?: FileType;
  status?: ItemStatus;
  source?: SourceType;
  size?: number;
  createdAt: string;
  updatedAt: string;
  parentId: string | null;
  knowledgeBaseId: string;
  hasChildren: boolean;
  nodeType?: string;
  sourceType?: string;
  connector?: string;
}

export interface FolderTreeNode {
  id: string;
  name: string;
  children: FolderTreeNode[];
  isExpanded: boolean;
  depth: number;
  parentId: string | null;
}

// Size range options for filtering
export type SizeRange = 'lt1mb' | '1to10mb' | '10to100mb';

export interface KnowledgeBaseFilter {
  nodeTypes?: string[];           // Aligned with API: nodeTypes parameter
  recordTypes?: RecordType[];     // NEW: for filtering by record type
  indexingStatus?: IndexingStatus[]; // Aligned with API: was 'statuses'
  origins?: NodeOrigin[];         // Aligned with API: was 'sources'
  connectorIds?: string[];        // NEW: for filtering by connector
  kbIds?: string[];              // NEW: for filtering by KB
  sizeRanges?: SizeRange[];      // Keep as-is (converted to size param)
  createdAfter?: string;         // Keep as-is
  createdBefore?: string;        // Keep as-is
  createdDateType?: DateFilterType; // Date filter type for created date
  updatedAfter?: string;         // Keep as-is
  updatedBefore?: string;        // Keep as-is
  updatedDateType?: DateFilterType; // Date filter type for updated date
  searchQuery?: string;          // NEW: for search query
}

export type SortField = 'name' | 'createdAt' | 'updatedAt' | 'size' | 'nodeType';

export interface SortConfig {
  field: SortField;
  order: SortOrder;
}

// API Response types
export interface GetKnowledgeBasesResponse {
  knowledgeBases: KnowledgeBase[];
  total: number;
}

export interface GetKnowledgeBaseItemsResponse {
  items: KnowledgeBaseItem[];
  total: number;
  page: number;
  limit: number;
}

export interface CreateFolderPayload {
  name: string;
  description?: string;
  parentId: string | null;
  knowledgeBaseId: string;
}

export interface UploadFilePayload {
  file: File;
  parentId: string | null;
  knowledgeBaseId: string;
}

// ============================================================================
// KNOWLEDGE HUB GET OPERATIONS TYPES
// ============================================================================

export type NodeType = 'kb' | 'app' | 'recordGroup' | 'folder' | 'record';
export type NodeOrigin = 'KB' | 'CONNECTOR';
export type PermissionRole = 'OWNER' | 'READER' | 'WRITER';
export type RecordType = 'FILE' | 'WEBPAGE' | 'MESSAGE' | 'EMAIL' | 'TICKET';
export type IndexingStatus = 'COMPLETED' | 'IN_PROGRESS' | 'FAILED' | 'FILE_TYPE_NOT_SUPPORTED' | 'NOT_STARTED' | 'AUTO_INDEX_OFF' | 'QUEUED';
export type SharingStatus = 'private' | 'team' | 'personal' | 'shared';

/**
 * Date filter type for the DateRangePicker component
 * - 'on': Exact date match
 * - 'between': Date range (from-to)
 * - 'before': Dates before the selected date
 * - 'after': Dates after the selected date
 */
export type DateFilterType = 'on' | 'between' | 'before' | 'after';

export interface NodePermission {
  role: PermissionRole;
  canEdit: boolean;
  canDelete: boolean;
}

/**
 * Knowledge Hub Query Parameters
 * 
 * Unified query params for both getKnowledgeHubNodes and getNodeItems APIs.
 * All parameters are optional and can be combined for flexible filtering.
 */
export interface KnowledgeHubQueryParams {
  // Pagination
  page?: number;              // Page number (≥ 1)
  limit?: number;             // Items per page (1-200)
  
  // Response includes (comma-separated string)
  include?: string;           // 'counts', 'permissions', 'breadcrumbs', 'availableFilters'
  
  // Container filter (sidebar use)
  onlyContainers?: boolean;   // Return only folders/groups with children
  
  // Sorting
  sortBy?: string;            // 'name', 'createdAt', 'updatedAt', 'size', 'type'
  sortOrder?: 'asc' | 'desc'; // Sort direction
  
  // Search
  q?: string;                 // Full-text search (2-500 chars)
  
  // Node filters (comma-separated)
  nodeTypes?: string;         // Filter by node types (max 100 items)
  recordTypes?: string;       // 'FILE', 'WEBPAGE', 'MESSAGE', 'EMAIL', 'TICKET' (max 100)
  origins?: string;           // 'KB', 'CONNECTOR' (max 100)
  connectorIds?: string;      // Connector UUIDs (max 100)
  kbIds?: string;             // KB UUIDs (max 100) - used for sidebar expansion
  indexingStatus?: string;    // 'COMPLETED', 'IN_PROGRESS', 'FAILED' (max 100)
  
  // Date range filters (epoch ms format: "gte:X,lte:Y")
  createdAt?: string;         // Filter by creation date range
  updatedAt?: string;         // Filter by update date range
  
  // Size filter (bytes format: "gte:X,lte:Y")
  size?: string;              // Filter by file size range
}

/**
 * Knowledge Hub Node (used in API responses)
 */
export interface KnowledgeHubNode {
  id: string;
  name: string;
  nodeType: NodeType;
  parentId: string | null;
  origin: NodeOrigin;
  connector?: string;
  hasChildren: boolean;
  permission: NodePermission;
  sharingStatus: SharingStatus;
  createdAt?: number;
  updatedAt?: number;
  webUrl?: string;
  recordType?: RecordType | null;
  indexingStatus?: IndexingStatus | null;
  sizeInBytes?: number | null;
  mimeType?: string | null;
  extension?: string | null;
  subType?: string;
  sourceType?: string;
}

/**
 * Breadcrumb navigation item
 */
export interface Breadcrumb {
  id: string;
  name: string;
  nodeType: string;
  subType?: string;
}

/**
 * Filter option for dropdowns
 */
export interface FilterOption {
  id: string;
  label: string;
  type?: string | null;
}

/**
 * Node counts summary
 */
export interface NodeCounts {
  items: Array<{
    label: string;
    count: number;
  }>;
  total: number;
}

/**
 * Node permissions (what user can do)
 */
export interface NodePermissions {
  role: PermissionRole;
  canUpload: boolean;
  canCreateFolders: boolean;
  canEdit: boolean;
  canDelete: boolean;
  canManagePermissions: boolean;
}

/**
 * Available filter options (from API response)
 */
export interface AvailableFilters {
  nodeTypes: FilterOption[];
  recordTypes: FilterOption[];
  origins: FilterOption[];
  connectors: FilterOption[];
  kbs: FilterOption[];
  indexingStatus: FilterOption[];
}

/**
 * Applied filters (current state)
 */
export interface AppliedFilters {
  q: string | null;
  nodeTypes: string | null;
  recordTypes: string | null;
  origins: string | null;
  connectorIds: string | null;
  kbIds: string | null;
  indexingStatus: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  size: string | null;
  sortBy: string;
  sortOrder: 'asc' | 'desc';
}

/**
 * Unified API Response for Knowledge Hub operations
 * Used for both root-level and node-specific queries
 */
export interface KnowledgeHubApiResponse {
  success: boolean;
  error: string | null;
  id: string | null;
  currentNode: {
    id: string;
    name: string;
    nodeType: string;
    subType?: string;
    origin?: string;
    indexingStatus?: string;
    version?: number;
    createdAt?: number;
    updatedAt?: number;
  } | null;
  parentNode: {
    id: string;
    name: string;
    nodeType: string;
  } | null;
  items: KnowledgeHubNode[];
  pagination: {
    page: number;
    limit: number;
    totalItems: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
  filters?: {
    applied: AppliedFilters;
    available: AvailableFilters;
  };
  breadcrumbs?: Breadcrumb[];
  counts?: NodeCounts;
  permissions?: NodePermissions;
}

/**
 * Enhanced folder tree node for sidebar
 */
export interface EnhancedFolderTreeNode extends FolderTreeNode {
  nodeType: NodeType;
  hasChildren: boolean;
  isLoading?: boolean;
  permission?: NodePermission;
  origin?: NodeOrigin;
  connector?: string;
}

export type SidebarSection = 'shared' | 'private';

export interface CategorizedNodes {
  shared: EnhancedFolderTreeNode[];
  private: EnhancedFolderTreeNode[];
}

// ============================================================================
// ALL RECORDS VIEW TYPES (for future implementation)
// ============================================================================

// Page view mode for switching between collections and all-records
export type PageViewMode = 'collections' | 'all-records';

// Connector types — single source of truth in ConnectorIcon.tsx
import type { ConnectorType as _ConnectorType } from '@/app/components/ui/ConnectorIcon';
export type ConnectorType = _ConnectorType;

export interface ConnectorItem {
  id: string;
  name: string;
  itemType: string; // 'channel', 'workspace', 'project', 'drive', etc.
}

export interface Connector {
  id: string;
  name: string;
  type: ConnectorType;
  icon: string; // Material icon name
  isConnected: boolean;
  items: ConnectorItem[];
}

/**
 * App node group for sidebar display
 * Groups app nodes by their connector (e.g., "Google Workspace", "Microsoft 365")
 */
export interface AppNodeGroup {
  id: string;                    // Normalized connector name (e.g., "google-workspace")
  connectorName: string;         // Raw API connector field (e.g., "Google Workspace")
  displayName: string;           // User-facing name
  type: ConnectorType;           // For icons
  apps: KnowledgeHubNode[];      // App nodes in this group
}

// Source type for filtering (collection vs connector)
export type SourceFilter = 'all' | 'collection' | ConnectorType;

// All Records filter - extends base filter with source filtering
export interface AllRecordsFilter {
  // Type filters
  nodeTypes?: string[];           // Aligned with API: was 'types'
  recordTypes?: RecordType[];

  // Status filter
  indexingStatus?: IndexingStatus[];

  // Source filters (specific to All Records mode)
  origins?: NodeOrigin[];         // Aligned with API: replaces 'sources'
  collectionIds?: string[];       // Maps to kbIds parameter
  connectorIds?: string[];

  // Size filter
  sizeRanges?: SizeRange[];

  // Date filters
  createdAfter?: string;
  createdBefore?: string;
  createdDateType?: DateFilterType; // Date filter type for created date
  updatedAfter?: string;
  updatedBefore?: string;
  updatedDateType?: DateFilterType; // Date filter type for updated date

  // Search
  searchQuery?: string;
}

// Selection state for All Records sidebar
export type AllRecordsSidebarSelection =
  | { type: 'all' }
  | { type: 'collection'; id: string; name: string }
  | { type: 'connector'; connectorType: ConnectorType; itemId?: string; itemName?: string };

// All Record item (extends table node with source info for display)
export interface AllRecordItem extends KnowledgeHubNode {
  sourceName: string; // Collection name or Connector name
  sourceType: 'collection' | ConnectorType;
  sourceIcon?: string; // Icon for the source
}

// Sort configuration for All Records
export type AllRecordsSortField = 'name' | 'size' | 'createdAt' | 'updatedAt' | 'indexingStatus' | 'origin';

export interface AllRecordsSortConfig {
  field: AllRecordsSortField;
  order: 'asc' | 'desc';
}

// Pagination for All Records
export interface AllRecordsPagination {
  page: number;
  limit: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

// More Connectors link item (navigates to connectors page with the connector panel open)
export interface MoreConnectorLink {
  id: string;
  name: string;
  type: ConnectorType;
  connectorTypeParam: string;
}

// ============================================================================
// RECORD DETAILS API RESPONSE
// ============================================================================

export interface RecordDetailsResponse {
  record: {
    _id: string;
    id: string;
    orgId: string;
    recordName: string;
    externalRecordId: string;
    recordType: 'FILE' | 'WEBPAGE' | 'MESSAGE' | 'EMAIL' | 'TICKET';
    origin: 'UPLOAD' | 'CONNECTOR';
    createdAtTimestamp: number;
    updatedAtTimestamp: number;
    sourceCreatedAtTimestamp: number;
    sourceLastModifiedTimestamp: number;
    isDeleted: boolean;
    isArchived: boolean;
    indexingStatus: 'COMPLETED' | 'IN_PROGRESS' | 'FAILED';
    version: number;
    webUrl: string;
    mimeType: string;
    connectorId: string;
    sizeInBytes: number;
    md5Checksum: string;
    extractionStatus: 'COMPLETED' | 'IN_PROGRESS' | 'FAILED';
    isDirty: boolean;
    lastIndexTimestamp: number;
    virtualRecordId: string;
    lastExtractionTimestamp: number;
    fileRecord: {
      _key: string;
      _id: string;
      _rev: string;
      orgId: string;
      name: string;
      isFile: boolean;
      extension: string;
      mimeType: string;
      sizeInBytes: number;
      webUrl: string;
    } | null;
    mailRecord: Record<string, unknown> | null;
    ticketRecord: Record<string, unknown> | null;
  };
  knowledgeBase: {
    id: string;
    name: string;
    orgId: string;
  } | null;
  folder: {
    id: string;
    name: string;
  } | null;
  metadata: {
    departments: Array<{ id: string; name: string }>;
    categories: Array<{ id: string; name: string }>;
    subcategories1: Array<{ id: string; name: string }>;
    subcategories2: Array<{ id: string; name: string }>;
    subcategories3: Array<{ id: string; name: string }>;
    topics: Array<{ id: string; name: string }>;
    languages: Array<{ id: string; name: string }>;
  };
  permissions: Array<{
    id: string;
    name: string;
    type: string;
    relationship: 'OWNER' | 'READER' | 'WRITER';
    accessType: string;
  }>;
}
