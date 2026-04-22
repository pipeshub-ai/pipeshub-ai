// Knowledge Base API - Action Operations (CRUD)

import { apiClient } from '@/lib/api';
import type {
  KnowledgeHubApiResponse,
  NodeType,
  KnowledgeHubQueryParams,
  RecordDetailsResponse,
} from './types';
import { DEFAULT_PAGE_SIZE } from './store';

const BASE_URL = '/api/v1/knowledgeBase';

// File metadata for folder uploads (preserves folder hierarchy)
export interface FileMetadata {
  file_path: string;
  last_modified: number;
}

// ============================================================================
// TEMPORARY HELPER - Remove after API is fixed
// ============================================================================

/**
 * TEMPORARY: Filter out records with nodeType "record" from API responses
 *
 * The API currently returns records even when onlyContainers=true is requested.
 * This helper removes those records until the backend is fixed.
 *
 * TODO: Remove this function once API properly respects onlyContainers parameter
 *
 * NOTE: App nodes are NOT filtered at this level. They are filtered at the
 * categorization level (tree-builder.ts) for Collections mode only, since
 * All Records mode needs app nodes for connector grouping.
 *
 * @param items Array of nodes from API response
 * @returns Filtered array with only container nodes (kb, app, folder, recordGroup)
 */
function filterOutRecords<T extends { nodeType?: string }>(items: T[]): T[] {
  return items.filter(item => item.nodeType !== 'record');
}

// ============================================================================
// GET OPERATIONS - Knowledge Hub APIs (Read-only hierarchical navigation)
// ============================================================================

/**
 * Knowledge Hub Get Operations
 * 
 * These APIs handle all read operations for the Collections view.
 * They provide convenient wrappers around the 2 core API endpoints with
 * pre-configured parameters for common use cases.
 */
export const KnowledgeHubApi = {
  /**
   * Collections View: Initial sidebar load
   * 
   * Fetches top-level folders, KBs, and apps for the sidebar navigation tree.
   * Always includes only containers (folders, KBs, apps) with counts.
   * 
   * @returns Root navigation nodes with counts
   */
  async initializeSidebar() {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes`,
      {
        params: {
          // TEMPORARY: onlyContainers disabled until API is fixed
          // onlyContainers: true,
          page: 1,
          limit: DEFAULT_PAGE_SIZE,
          include: 'counts',
        },
        suppressErrorToast: true,
      }
    );

    // TEMPORARY: Filter out records manually until API respects onlyContainers
    // TODO: Remove this filtering once API is fixed
    // NOTE: App nodes are NOT filtered here - they're filtered at categorization level
    // for Collections mode only. All Records mode needs app nodes for connectors.
    return {
      ...data,
      items: filterOutRecords(data.items),
    };
  },

  /**
   * Collections View: Expand folder in sidebar
   * 
   * Fetches children of a specific folder/KB for sidebar expansion.
   * Returns only containers (subfolders, nested KBs).
   * 
   * @param nodeId - ID of the folder/KB to expand
   * @returns Child navigation nodes
   */
  async expandFolder(nodeId: string) {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes`,
      {
        params: {
          nodeId,
          // TEMPORARY: onlyContainers disabled until API is fixed
          // onlyContainers: true,
          page: 1,
          limit: DEFAULT_PAGE_SIZE,
        },
      }
    );

    // TEMPORARY: Filter out records manually until API respects onlyContainers
    // TODO: Remove this filtering once API is fixed
    // NOTE: App nodes are NOT filtered here - they're filtered at categorization level
    // for Collections mode only. All Records mode needs app nodes for connectors.
    return {
      ...data,
      items: filterOutRecords(data.items),
    };
  },

  /**
   * Collections View: Load data area for selected folder
   * 
   * Fetches all items (files + folders) within a selected node for the data table.
   * Includes full metadata: breadcrumbs, permissions, filters, counts.
   * 
   * @param nodeType - Type of the selected node (kb, folder, app, recordGroup)
   * @param nodeId - ID of the selected node
   * @param params - Optional filters, pagination, sorting
   * @returns Complete data area response with items and metadata
   */
  async loadFolderData(
    nodeType: NodeType,
    nodeId: string,
    params?: Partial<KnowledgeHubQueryParams>
  ) {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes/${nodeType}/${nodeId}`,
      {
        params: {
          page: 1,
          limit: DEFAULT_PAGE_SIZE,
          include: 'counts,permissions,breadcrumbs,availableFilters',
          // Data area: Never use onlyContainers (we need both folders AND files)
          ...params,
        },
      }
    );
    return data;
  },

  /**
   * Collections View: Search/filter within folder
   * 
   * Same as loadFolderData but explicitly for filtering.
   * Pass filters via params (q, recordTypes, sortBy, etc.)
   * 
   * @param nodeType - Type of the current node
   * @param nodeId - ID of the current node
   * @param filters - Search query, filters, sorting
   * @returns Filtered data area response
   */
  async filterFolderData(
    nodeType: NodeType,
    nodeId: string,
    filters: KnowledgeHubQueryParams
  ) {
    return this.loadFolderData(nodeType, nodeId, filters);
  },

  /**
   * All Records View: Global search/filter
   * 
   * Search across all accessible nodes with filters.
   * Used when no specific folder is selected (All Records mode).
   * 
   * @param params - Search query, filters, sorting, pagination
   * @returns Filtered results across all sources
   */
  async searchAllRecords(params: KnowledgeHubQueryParams) {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes`,
      {
        params: {
          page: 1,
          limit: DEFAULT_PAGE_SIZE,
          include: 'counts,permissions,availableFilters',
          // Data area: Never use onlyContainers (we need all record types)
          ...params,
        },
      }
    );
    return data;
  },

  /**
   * All Records View: Get all root items
   * 
   * Fetches all accessible items at root level (not just containers).
   * Used for populating the All Records data table.
   * 
   * @param params - Optional filters, pagination, sorting
   * @returns All root-level items with metadata
   *

  /**
   * Get node children for sidebar expansion (with onlyContainers)
   * 
   * Used when expanding nodes in the sidebar tree.
   * 
   * @param nodeType - Type of parent node
   * @param nodeId - ID of parent node
   * @param params - Optional params (onlyContainers will be applied)
   * @returns Child nodes (containers only)
   */
  async getNodeChildren(
    nodeType: NodeType,
    nodeId: string,
    params?: {
      onlyContainers?: boolean;
      page?: number;
      limit?: number;
      include?: string;
    }
  ) {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes/${nodeType}/${nodeId}`,
      {
        params: {
          // TEMPORARY: onlyContainers disabled until API is fixed
          // onlyContainers: params?.onlyContainers ?? true,
          page: params?.page ?? 1,
          limit: params?.limit ?? 50,
          include: params?.include,
        },
      }
    );

    // TEMPORARY: Filter out records manually if onlyContainers was requested
    // TODO: Remove this filtering once API is fixed
    // NOTE: App nodes are NOT filtered here - they're filtered at categorization level
    // for Collections mode only. All Records mode needs app nodes for connectors.
    if (params?.onlyContainers !== false) {
      return {
        ...data,
        items: filterOutRecords(data.items),
      };
    }

    return data;
  },

  /**
   * Get node table data (all items in a folder for data table)
   * 
   * Fetches top-level nodes. Applies container filtering based on params.
   * 
   * @param params - Query parameters
   * @returns Root nodes
   */
  async getRootNodes(params?: { page?: number; limit?: number; onlyContainers?: boolean }) {
    if (params?.onlyContainers !== false) {
      // For sidebar: use initializeSidebar which handles filtering
      return this.initializeSidebar();
    }
    
    // For data view: get all items
    return this.getAllRootItems(params);
  },

  async getAllRootItems(params?: Partial<KnowledgeHubQueryParams>) {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes`,
      {
        params: {
          page: 1,
          limit: DEFAULT_PAGE_SIZE,
          include: 'counts,permissions,breadcrumbs,availableFilters',
          // Data area: Never use onlyContainers (we need all root items including records)
          ...params,
        },
      }
    );
    return data;
  },

  // ============================================================================
  // Low-level API methods (for advanced use cases)
  // ============================================================================

  /**
   * API 1: Get Navigation Nodes (Root or Filtered)
   * 
   * Low-level method for custom queries. Prefer using the higher-level methods above.
   * 
   * GET /api/v1/knowledgeBase/knowledge-hub/nodes
   * 
   * @param params Query parameters for filtering, pagination, sorting
   * @returns Root nodes or filtered results with metadata
   */
  async getNavigationNodes(params?: KnowledgeHubQueryParams): Promise<KnowledgeHubApiResponse> {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes`,
      { params }
    );
    return data;
  },

  /**
   * API 2: Get Data Nodes (Children of Specific Node)
   * 
   * Low-level method for custom queries. Prefer using loadFolderData/filterFolderData.
   * 
   * GET /api/v1/knowledgeBase/knowledge-hub/nodes/:nodeType/:nodeId
   * 
   * @param nodeType Type of parent node (app, kb, folder, recordGroup, record)
   * @param nodeId UUID of parent node
   * @param params Query parameters for filtering, pagination, sorting
   * @returns Child items with pagination and metadata
   */
  async getDataNodes(
    nodeType: NodeType,
    nodeId: string,
    params?: KnowledgeHubQueryParams
  ): Promise<KnowledgeHubApiResponse> {
    const { data } = await apiClient.get<KnowledgeHubApiResponse>(
      `${BASE_URL}/knowledge-hub/nodes/${nodeType}/${nodeId}`,
      { params }
    );
    return data;
  },
};

// ============================================================================
// ACTION OPERATIONS - KB CRUD APIs (Create, Update, Delete)
// ============================================================================

/**
 * Knowledge Base Action Operations
 * 
 * These APIs handle all write operations for managing knowledge bases,
 * folders, and records (upload, create, update, delete, permissions).
 */
export const KnowledgeBaseApi = {
  // Get upload limits from server (max file size)
  async getUploadLimits() {
    const { data } = await apiClient.get<{ maxFileSizeBytes?: number }>(
      '/api/v1/knowledgebase/limits',
      { suppressErrorToast: true }
    );
    return data;
  },

  // List all knowledge bases
  async listKnowledgeBases() {
    const { data } = await apiClient.get<{ knowledgeBases: Record<string, unknown>[]; total: number }>(BASE_URL);
    return data;
  },

  // Create knowledge base (collection)
  async createKnowledgeBase(kbName: string, kbDescription?: string) {
    const { data } = await apiClient.post<{
      id: string;
      name: string;
      createdAtTimestamp: number;
      updatedAtTimestamp: number;
      userRole: string;
    }>(BASE_URL, {
      kbName,
      kbDescription: kbDescription || '',
      // isPrivate: false, // Reserved for future use - will distinguish workspace vs private
    }, {
      suppressErrorToast: true,
    });
    return data;
  },

  // Get single knowledge base
  async getKnowledgeBase(id: string) {
    const { data } = await apiClient.get<Record<string, unknown>>(`${BASE_URL}/${id}`);
    return data;
  },

  // Get knowledge base folder structure
  async getFolderTree(knowledgeBaseId: string) {
    const { data } = await apiClient.get<{ folders: Record<string, unknown>[] }>(`${BASE_URL}/${knowledgeBaseId}/folders`);
    return data;
  },

  // Get items in a folder (or root)
  async getItems(
    knowledgeBaseId: string,
    folderId?: string | null,
    params?: {
      page?: number;
      limit?: number;
      search?: string;
      sortField?: string;
      sortOrder?: string;
    }
  ) {
    const basePath = folderId
      ? `${BASE_URL}/${knowledgeBaseId}/folders/${folderId}/items`
      : `${BASE_URL}/${knowledgeBaseId}/items`;

    const { data } = await apiClient.get<{ items: Record<string, unknown>[]; total: number; page: number; limit: number }>(
      basePath,
      { params }
    );
    return data;
  },

  // Create folder (root or nested)
  async createFolder(knowledgeBaseId: string, name: string, description?: string, parentId?: string | null) {
    // Choose correct endpoint based on whether we're creating root or nested folder
    const endpoint = parentId
      ? `${BASE_URL}/${knowledgeBaseId}/folder/${parentId}/subfolder`
      : `${BASE_URL}/${knowledgeBaseId}/folder`;

    const { data } = await apiClient.post<Record<string, unknown>>(endpoint, {
      folderName: name,
      // Note: API currently doesn't support description in request body
      // If description is needed in future, add it here
    }, {
      suppressErrorToast: true,
    });
    return data;
  },

  // Upload files to knowledge base root
  async uploadToRoot(
    knowledgeBaseId: string,
    files: File[],
    filesMetadata?: FileMetadata[],
    onProgress?: (progress: number) => void
  ) {
    const formData = new FormData();

    // Add files array - append each file separately with key "files"
    files.forEach((file) => {
      formData.append('files', file);
    });

    // Check if this is a folder upload (metadata provided)
    if (filesMetadata && filesMetadata.length === files.length) {
      // Folder upload: use files_metadata JSON format
      formData.append('files_metadata', JSON.stringify(filesMetadata));
    } else {
      // File upload: use existing format
      files.forEach((file) => {
        formData.append('file_paths', file.name);
      });
      files.forEach((file) => {
        formData.append('last_modified', file.lastModified.toString());
      });
    }

    // Note: lowercase 'knowledgebase' in URL (not 'knowledgeBase')
    const { data } = await apiClient.post(
      `/api/v1/knowledgebase/${knowledgeBaseId}/upload`,
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        // Placeholder creation + storage can exceed the default API client timeout after bytes are sent
        timeout: 0,
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total && onProgress) {
            const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            onProgress(progress);
          }
        },
      }
    );
    return data;
  },

  // Upload files to knowledge base folder
  async uploadToFolder(
    knowledgeBaseId: string,
    folderId: string,
    files: File[],
    filesMetadata?: FileMetadata[],
    onProgress?: (progress: number) => void
  ) {
    const formData = new FormData();

    // Add files array - append each file separately with key "files"
    files.forEach((file) => {
      formData.append('files', file);
    });

    // Check if this is a folder upload (metadata provided)
    if (filesMetadata && filesMetadata.length === files.length) {
      // Folder upload: use files_metadata JSON format
      formData.append('files_metadata', JSON.stringify(filesMetadata));
    } else {
      // File upload: use existing format
      files.forEach((file) => {
        formData.append('file_paths', file.name);
      });
      files.forEach((file) => {
        formData.append('last_modified', file.lastModified.toString());
      });
    }

    // Note: lowercase 'knowledgebase' in URL (not 'knowledgeBase')
    const { data } = await apiClient.post(
      `/api/v1/knowledgebase/${knowledgeBaseId}/folder/${folderId}/upload`,
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        timeout: 0,
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total && onProgress) {
            const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            onProgress(progress);
          }
        },
      }
    );
    return data;
  },

  // Rename knowledge base
  async renameKnowledgeBase(kbId: string, kbName: string) {
    const { data } = await apiClient.put(`${BASE_URL}/${kbId}`, { kbName });
    return data;
  },

  // Delete knowledge base
  async deleteKnowledgeBase(kbId: string) {
    await apiClient.delete(`${BASE_URL}/${kbId}`, { suppressErrorToast: true });
  },

  // Delete folder (from new API structure)
  async deleteFolder(kbId: string, folderId: string) {
    await apiClient.delete(`${BASE_URL}/${kbId}/folder/${folderId}`, { suppressErrorToast: true });
  },

  // Delete record (file)
  async deleteRecord(recordId: string) {
    await apiClient.delete(`${BASE_URL}/record/${recordId}`, { suppressErrorToast: true });
  },

  // Replace record (update file)
  async replaceRecord(
    recordId: string,
    file?: File,
    recordName?: string,
    onProgress?: (progress: number) => void
  ) {
    const formData = new FormData();
    
    // Add file if provided
    if (file) {
      formData.append('file', file);
    }
    
    // Add recordName if provided
    if (recordName) {
      formData.append('recordName', recordName);
    }

    const { data } = await apiClient.put(
      `${BASE_URL}/record/${recordId}`,
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: (progressEvent) => {
          if (progressEvent.total && onProgress) {
            const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            onProgress(progress);
          }
        },
      }
    );
    return data;
  },

  // Rename item (legacy - uses PATCH endpoint)
  async renameItem(knowledgeBaseId: string, itemId: string, newName: string) {
    const { data } = await apiClient.patch<Record<string, unknown>>(`${BASE_URL}/${knowledgeBaseId}/items/${itemId}`, {
      name: newName,
    });
    return data;
  },

  // Rename folder
  async renameFolder(rootKbId: string, folderId: string, newName: string) {
    const { data } = await apiClient.put(`${BASE_URL}/${rootKbId}/folder/${folderId}`, {
      folderName: newName,
    }, {
      suppressErrorToast: true,
    });
    return data;
  },

  /**
   * Unified rename dispatcher — dispatches to renameKnowledgeBase or renameFolder
   * based on nodeType and whether the node is the root KB itself.
   */
  async renameNode(args: {
    nodeId: string;
    newName: string;
    nodeType?: string;
    rootKbId?: string;
  }) {
    const { nodeId, newName, nodeType, rootKbId } = args;
    const isFolderLike = nodeType === 'folder' || nodeType === 'recordGroup';
    if (isFolderLike && rootKbId && rootKbId !== nodeId) {
      return this.renameFolder(rootKbId, nodeId, newName);
    }
    return this.renameKnowledgeBase(nodeId, newName);
  },

  /**
   * Unified delete dispatcher — dispatches to deleteKnowledgeBase or deleteFolder
   * based on nodeType and whether the node is the root KB itself.
   */
  async deleteNode(args: {
    nodeId: string;
    nodeType?: string;
    rootKbId?: string;
  }) {
    const { nodeId, nodeType, rootKbId } = args;
    if (nodeType === 'folder' && rootKbId && rootKbId !== nodeId) {
      return this.deleteFolder(rootKbId, nodeId);
    }
    return this.deleteKnowledgeBase(nodeId);
  },

  // Rename record (file)
  async renameRecord(recordId: string, newName: string) {
    const formData = new FormData();
    formData.append('recordName', newName);
    const { data } = await apiClient.put(
      `${BASE_URL}/record/${recordId}`,
      formData,
      {
        headers: { 'Content-Type': 'multipart/form-data' },
        suppressErrorToast: true,
      }
    );
    return data;
  },

  // Move item
  async moveItem(knowledgeBaseId: string, itemId: string, newParentId: string) {
    const { data } = await apiClient.put<Record<string, unknown>>(`${BASE_URL}/${knowledgeBaseId}/record/${itemId}/move`, {
      newParentId,
    });
    return data;
  },

  // Reindex item (works for both records and folders)
  async reindexItem(recordId: string) {
    const { data } = await apiClient.post<Record<string, unknown>>(`${BASE_URL}/reindex/record/${recordId}`, undefined, { suppressErrorToast: true });
    return data;
  },

  // Reindex record group (folders inside app nodes like Sharepoint, OneDrive)
  async reindexRecordGroup(recordGroupId: string) {
    const { data } = await apiClient.post<Record<string, unknown>>(
      `${BASE_URL}/reindex/record-group/${recordGroupId}`,
      { force: false, depth: 100 }
    );
    return data;
  },

  // Download a record file via stream endpoint
  async streamDownloadRecord(recordId: string, fileName?: string): Promise<void> {
    const response = await apiClient.get(`${BASE_URL}/stream/record/${recordId}`, {
      responseType: 'blob',
      timeout: 300000,
    });

    // response.data is already a Blob with the correct MIME type when responseType is 'blob'
    const blob: Blob = response.data;

    const downloadName = fileName || 'download';

    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = downloadName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  },

  /**
   * Get full record details including metadata and permissions
   */
  async getRecordDetails(recordId: string) {
    const { data } = await apiClient.get<RecordDetailsResponse>(
      `${BASE_URL}/record/${recordId}`
    );
    return data;
  },

  /**
   * Stream record file using recordId
   * Returns blob that can be converted to object URL
   * Works for both UPLOAD (collection) and CONNECTOR (external) sources
   *
   * @param recordId - The ID of the record to stream
   * @param options.convertTo - Optional format conversion (e.g. 'pdf').
   *   Used for PPT/PPTX files which need server-side conversion to PDF for preview.
   */
  async streamRecord(recordId: string, options?: { convertTo?: string }): Promise<Blob> {
    const params: Record<string, string> = {};
    if (options?.convertTo) {
      params.convertTo = options.convertTo;
    }
    const { data } = await apiClient.get(
      `${BASE_URL}/stream/record/${recordId}`,
      { responseType: 'blob', params }
    );
    return data;
  },

  /**
   * Download record file using externalRecordId
   * Returns blob that can be converted to object URL
   */
  async downloadRecord(externalRecordId: string): Promise<Blob> {
    const { data } = await apiClient.get(
      `/api/v1/document/${externalRecordId}/download`,
      { responseType: 'blob' }
    );
    return data;
  },

  /**
   * Bulk reindex multiple records
   * @param recordIds - Array of record IDs to reindex
   * @returns Promise.allSettled results for each reindex operation
   */
  async bulkReindex(recordIds: string[]) {
    const results = await Promise.allSettled(
      recordIds.map(id => this.reindexItem(id))
    );
    return results;
  },

  /**
   * Bulk delete multiple items
   * @param items - Array of items with id, nodeType, and optional kbId
   * @returns Promise.allSettled results for each delete operation
   */
  async bulkDelete(
    items: Array<{ id: string; nodeType: 'kb' | 'folder' | 'record'; kbId?: string }>
  ) {
    const results = await Promise.allSettled(
      items.map(item => {
        if (item.nodeType === 'kb') {
          return this.deleteKnowledgeBase(item.id);
        } else if (item.nodeType === 'folder' && item.kbId) {
          return this.deleteFolder(item.kbId, item.id);
        } else {
          return this.deleteRecord(item.id);
        }
      })
    );
    return results;
  },
};

