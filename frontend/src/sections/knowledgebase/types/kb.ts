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
  name: string;
  recordName?: string;
  type: 'folder' | 'file';
  extension?: string;
  sizeInBytes?: number;
  webUrl:string;
  updatedAt: number;
  createdAt: number;
  createdAtTimestamp?: number;
  updatedAtTimestamp?: number;
  indexingStatus?: 'COMPLETED' | 'PENDING' | 'FAILED' | 'NOT_STARTED';
  parentFolderId?: string;
  fileRecord?: {
    id: string;
    name: string;
    extension: string;
    mimeType: string;
    sizeInBytes: number;
    webUrl: string;
    path: string;
    isFile: boolean;
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