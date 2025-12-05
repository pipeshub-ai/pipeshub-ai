// types/kb.ts

export interface KnowledgeBase {
  id: string;
  name: string;
  description?: string;
  isShared?: boolean;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  userRole: string;
  rootFolderId: string;
}

export interface Item {
  id: string;
  _key?: string; // Support both id and _key for compatibility
  name: string;
  recordName?: string;
  type: 'folder' | 'file';
  extension?: string | null;
  sizeInBytes?: number;
  webUrl: string;
  updatedAt: number;
  createdAt: number;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  indexingStatus?: 'NOT_STARTED' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'FILE_TYPE_NOT_SUPPORTED' | 'AUTO_INDEX_OFF' | 'EMPTY' | 'ENABLE_MULTIMODAL_MODELS' | 'QUEUED' | 'PROCESSING' | 'IN_PROGRESS';
  parentFolderId?: string;
  isProcessing?: boolean; // Flag for optimistic UI updates
  recordType?: string;
  origin?: string;
  connectorName?: string;
  externalRecordId?: string;
  version?: number;
  sourceCreatedAtTimestamp?: number;
  sourceLastModifiedTimestamp?: number;
  fileRecord?: {
    id?: string;
    _key?: string;
    name: string;
    extension?: string | null;
    mimeType?: string | null;
    sizeInBytes?: number;
    webUrl?: string;
    path?: string;
    isFile?: boolean;
  };
}

export interface UserPermission {
  role: string;
  canUpload: boolean;
  canCreateFolders: boolean;
  canEdit: boolean;
  canDelete: boolean;
}

export interface FolderContents {
  folders?: Item[];
  records?: Item[];
  pagination: {
    page: number;
    limit: number;
    totalItems: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
  userPermission: UserPermission;
}

export interface KBPermission {
  userId: string;
  userEmail: string;
  userName?: string;
  role: 'OWNER' | 'ORGANIZER' | 'FILEORGANIZER' | 'WRITER' | 'COMMENTER' | 'READER';
  permissionType: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
}

export interface CreatePermissionRequest {
  userIds: string[];
  teamIds: string[];
  role: string;
}

export interface UpdatePermissionRequest {
  userIds: string[];
  teamIds: string[];
  role: string;
}

export interface RemovePermissionRequest {
  userIds: string[];
  teamIds: string[];
}