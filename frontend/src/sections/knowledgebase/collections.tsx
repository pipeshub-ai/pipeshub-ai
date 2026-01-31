import { Icon } from '@iconify/react';
import refreshIcon from '@iconify-icons/mdi/refresh';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import downloadIcon from '@iconify-icons/mdi/download-outline';
import folderOpenIcon from '@iconify-icons/mdi/folder-open-outline';
import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';

import {
  Box,
  Card,
  Menu,
  Alert,
  alpha,
  styled,
  Snackbar,
  useTheme,
  MenuItem,
  ListItemIcon,
  ListItemText,
  LinearProgress,
  CircularProgress,
  Divider,
} from '@mui/material';
import { UnifiedPermission } from 'src/components/permissions/UnifiedPermissionsDialog';
import { useSocket } from 'src/hooks/use-socket';
import { useRouter } from './hooks/use-router';
import { KnowledgeBaseAPI } from './services/api';
import UploadManager from './upload-manager';
import { UploadNotification } from './components/upload-notification';
import DashboardComponent from './components/dashboard';
import { EditFolderDialog } from './components/dialogs/edit-dialogs';
import { CreateFolderDialog, DeleteConfirmDialog } from './components/dialogs';
import KbPermissionsDialog from './components/dialogs/kb-permissions-dialog';
import { renderKBDetail } from './components/kb-details';

// Import types and services
import type {
  Item,
  KnowledgeBase,
  UserPermission,
  CreatePermissionRequest,
  UpdatePermissionRequest,
  RemovePermissionRequest,
} from './types/kb';
import type { ActiveUpload, FailedFileInfo } from './types/upload';
import { ORIGIN } from './constants/knowledge-search';
import { ACTIVE_UPLOADS_STORAGE_KEY, SOCKET_EVENT_TIMEOUT_MS } from './constants/upload.constants';

type ViewMode = 'grid' | 'list';

interface MenuItemWithAction extends KnowledgeBase {
  type: string;
  action?: 'edit' | 'delete';
}

const MainContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  height: '100%',
}));

const ContentArea = styled(Box)({
  flexGrow: 1,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  height: '100%',
});

const CompactCard = styled(Card)(({ theme }) => ({
  position: 'relative',
  transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: 12,
  backgroundColor: theme.palette.background.paper,
  backdropFilter: 'blur(10px)',
  boxShadow: theme.shadows[1],
  '&:hover': {
    borderColor: alpha(theme.palette.primary.main, 0.3),
    boxShadow: theme.shadows[4],
    transform: 'translateY(-1px)',
  },
}));

export default function Collections() {
  const theme = useTheme();
  const { route, navigate, isInitialized } = useRouter();

  const loadingRef = useRef(false);
  const currentKBRef = useRef<KnowledgeBase | null>(null);
  const isViewInitiallyLoading = useRef(true);
  const searchTimeoutRef = useRef<NodeJS.Timeout>();
  const lastLoadParams = useRef<string>('');
  const fallbackTimeoutsRef = useRef<Map<string, NodeJS.Timeout>>(new Map());

  const [currentKB, setCurrentKB] = useState<KnowledgeBase | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [currentUserPermission, setCurrentUserPermission] = useState<UserPermission | null>(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  const [createFolderDialog, setCreateFolderDialog] = useState(false);
  const [uploadDialog, setUploadDialog] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [permissionsDialog, setPermissionsDialog] = useState(false);

  const [editFolderDialog, setEditFolderDialog] = useState(false);

  const [permissions, setPermissions] = useState<UnifiedPermission[]>([]);
  const [permissionsLoading, setPermissionsLoading] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(20);
  const [totalCount, setTotalCount] = useState(0);

  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const [contextItem, setContextItem] = useState<any>(null);
  const [itemToDelete, setItemToDelete] = useState<any>(null);
  const [itemToEdit, setItemToEdit] = useState<any>(null);

  const [navigationPath, setNavigationPath] = useState<
    Array<{ id: string; name: string; type: 'kb' | 'folder' }>
  >([]);

  // Track active uploads per KB/folder (Google Drive-like experience)
  // Use sessionStorage to persist across refreshes
  const loadUploadsFromStorage = (): Map<string, ActiveUpload> => {
    try {
      const stored = sessionStorage.getItem(ACTIVE_UPLOADS_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        return new Map(Object.entries(parsed));
      }
    } catch (storageError) {
      // Silently handle storage errors - user can still use the app
      if (process.env.NODE_ENV === 'development') {
        // eslint-disable-next-line no-console
        console.warn('Failed to load uploads from storage:', storageError);
      }
    }
    return new Map();
  };

  const saveUploadsToStorage = (uploads: Map<string, ActiveUpload>): void => {
    try {
      const obj = Object.fromEntries(uploads);
      sessionStorage.setItem(ACTIVE_UPLOADS_STORAGE_KEY, JSON.stringify(obj));
    } catch (storageError) {
      // Silently handle storage errors - user can still use the app
      if (process.env.NODE_ENV === 'development') {
        // eslint-disable-next-line no-console
        console.warn('Failed to save uploads to storage:', storageError);
      }
    }
  };

  const [activeUploads, setActiveUploads] = useState<Map<string, ActiveUpload>>(
    () => loadUploadsFromStorage()
  );

  // Save to storage whenever activeUploads changes
  useEffect(() => {
    saveUploadsToStorage(activeUploads);
  }, [activeUploads]);

  const stableRoute = useMemo(() => route, [route]);

  const loadKBContents = useCallback(
    async (kbId: string, folderId?: string, resetItems = true, forceReload = false) => {
      if (loadingRef.current && !forceReload) return;

      const loadId = `content-${kbId}-${folderId || 'root'}-${page}-${rowsPerPage}-${searchQuery}`;

      if (!forceReload && lastLoadParams.current === loadId) return;

      loadingRef.current = true;
      lastLoadParams.current = loadId;
      setPageLoading(true);

      try {
        const params = {
          page: page + 1,
          limit: rowsPerPage,
          search: searchQuery,
        };

        const data = await KnowledgeBaseAPI.getFolderContents(kbId, folderId, params);

        const folders = (data.folders || []).map((folder) => ({
          ...folder,
          type: 'folder' as const,
          createdAt: folder.createdAtTimestamp || Date.now(),
          updatedAt: folder.updatedAtTimestamp || Date.now(),
        }));

        const records = (data.records || []).map((record) => ({
          ...record,
          id: record._key || record.id,
          _key: record._key,
          name: record.recordName || record.name,
          type: 'file' as const,
          createdAt: record.createdAtTimestamp || Date.now(),
          updatedAt: record.updatedAtTimestamp || Date.now(),
          extension: record.fileRecord?.extension ?? undefined,
          sizeInBytes: record.fileRecord?.sizeInBytes,
          // Remove processing flag if status changed
          isProcessing: record.indexingStatus === 'PROCESSING',
        }));

        const newItems = [...folders, ...records];

        if (resetItems) {
          setItems(newItems);
        } else {
          setItems((prev) => [...prev, ...newItems]);
        }

        setCurrentUserPermission(data.userPermission);
        setTotalCount(data.pagination.totalItems);
        setHasMore(newItems.length < data.pagination.totalItems);

        isViewInitiallyLoading.current = false;
      } catch (err: any) {
        setError(err.message || 'Failed to fetch contents');
        if (resetItems) {
          setItems([]);
          setTotalCount(0);
        }
      } finally {
        setPageLoading(false);
        loadingRef.current = false;
      }
    },
    [page, searchQuery, rowsPerPage]
  );

  useEffect(() => {
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    if (currentKB && stableRoute.view !== 'dashboard') {
      searchTimeoutRef.current = setTimeout(async () => {
        if (!loadingRef.current) {
          await loadKBContents(currentKB.id, stableRoute.folderId);
        }
      }, 300);
    }

    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, [
    searchQuery,
    currentKB?.id,
    stableRoute.folderId,
    stableRoute.view,
    currentKB,
    loadKBContents,
  ]);

  const handleLoadMore = useCallback(async () => {
    if (!currentKB || loadingRef.current || loadingMore || !hasMore) return;

    loadingRef.current = true;
    setLoadingMore(true);

    try {
      const nextPage = Math.floor(items.length / rowsPerPage) + 1;
      const params = {
        page: nextPage,
        limit: rowsPerPage,
        search: searchQuery,
      };

      const data = await KnowledgeBaseAPI.getFolderContents(
        currentKB.id,
        stableRoute.folderId,
        params
      );

      const folders = (data.folders || []).map((folder) => ({
        ...folder,
        type: 'folder' as const,
        createdAt: folder.createdAtTimestamp || Date.now(),
        updatedAt: folder.updatedAtTimestamp || Date.now(),
      }));

      const records = (data.records || []).map((record) => ({
        ...record,
        name: record.recordName || record.name,
        type: 'file' as const,
        createdAt: record.createdAtTimestamp || Date.now(),
        updatedAt: record.updatedAtTimestamp || Date.now(),
        extension: record.fileRecord?.extension,
        sizeInBytes: record.fileRecord?.sizeInBytes,
      }));

      const newItems = [...folders, ...records];
      setItems((prev) => [...prev, ...newItems]);

      const totalAfterLoad = items.length + newItems.length;
      setHasMore(totalAfterLoad < data.pagination.totalItems);
    } catch (err: any) {
      setError(err.message || 'Failed to load more items');
    } finally {
      setLoadingMore(false);
      loadingRef.current = false;
    }
  }, [
    currentKB,
    items.length,
    rowsPerPage,
    searchQuery,
    loadingMore,
    hasMore,
    stableRoute.folderId,
  ]);

  useEffect(() => {
    const loadContents = async () => {
      if (isViewInitiallyLoading.current) return;

      if (currentKB && viewMode === 'list' && !searchQuery) {
        await loadKBContents(currentKB.id, stableRoute.folderId);
      }
    };

    loadContents();
  }, [page, rowsPerPage, viewMode, currentKB, loadKBContents, searchQuery, stableRoute.folderId]); // Removed dependencies that cause duplicate calls

  const loadKBPermissions = useCallback(async (kbId: string) => {
    setPermissionsLoading(true);
    try {
      const data = await KnowledgeBaseAPI.listKBPermissions(kbId);
      setPermissions(data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch permissions');
    } finally {
      setPermissionsLoading(false);
    }
  }, []);

  const navigateToKB = useCallback(
    (kb: KnowledgeBase) => {
      if (currentKBRef.current?.id === kb.id) return;

      currentKBRef.current = kb;
      setCurrentKB(kb);
      setNavigationPath([{ id: kb.id, name: kb.name, type: 'kb' }]);
      navigate({ view: 'knowledge-base', kbId: kb.id });

      // Reset state
      setItems([]);
      setPage(0);
      setSearchQuery('');
      setError(null);
      setCurrentUserPermission(null);
      setPermissions([]);
      isViewInitiallyLoading.current = true;

      setTimeout(async () => {
        if (!loadingRef.current) {
          await loadKBContents(kb.id);
        }
      }, 50);
    },
    [navigate, loadKBContents]
  );

  const navigateToFolder = useCallback(
    (folder: Item) => {
      if (!currentKB) return;

      const newPath = [
        ...navigationPath,
        { id: folder.id, name: folder.name, type: 'folder' as const },
      ];
      setNavigationPath(newPath);
      navigate({ view: 'folder', kbId: currentKB.id, folderId: folder.id });

      // Reset state
      setItems([]);
      setPage(0);
      setSearchQuery('');
      setError(null);
      isViewInitiallyLoading.current = true;

      setTimeout(async () => {
        if (!loadingRef.current) {
          await loadKBContents(currentKB.id, folder.id);
        }
      }, 50);
    },
    [currentKB, navigationPath, navigate, loadKBContents]
  );

  const navigateToDashboard = useCallback(() => {
    currentKBRef.current = null;
    setCurrentKB(null);
    setNavigationPath([]);
    navigate({ view: 'dashboard' });

    // Reset all state
    setItems([]);
    setSearchQuery('');
    setError(null);
    setCurrentUserPermission(null);
    setPermissions([]);
    isViewInitiallyLoading.current = true;
  }, [navigate]);

  const navigateBack = useCallback(() => {
    if (navigationPath.length <= 1) {
      navigateToDashboard();
    } else {
      const newPath = navigationPath.slice(0, -1);
      setNavigationPath(newPath);
      const parent = newPath[newPath.length - 1];

      // Reset state
      setItems([]);
      setPage(0);
      setSearchQuery('');
      setError(null);
      isViewInitiallyLoading.current = true;

      if (parent.type === 'kb') {
        navigate({ view: 'knowledge-base', kbId: parent.id });
        setTimeout(async () => {
          if (!loadingRef.current) {
            await loadKBContents(parent.id);
          }
        }, 50);
      } else {
        navigate({ view: 'folder', kbId: currentKB!.id, folderId: parent.id });
        setTimeout(async () => {
          if (!loadingRef.current) {
            await loadKBContents(currentKB!.id, parent.id);
          }
        }, 50);
      }
    }
  }, [navigationPath, navigateToDashboard, currentKB, navigate, loadKBContents]);

  useEffect(() => {
    if (!isInitialized) return;

    const initializeFromRoute = async () => {
      if (loadingRef.current) return;

      const routeId = `${stableRoute.view}-${stableRoute.kbId || ''}-${stableRoute.folderId || ''}`;
      if (lastLoadParams.current === routeId) return;

      lastLoadParams.current = routeId;
      isViewInitiallyLoading.current = true;

      // Reset state for all route changes
      setItems([]);
      setSearchQuery('');
      setPage(0);
      setError(null);

      if (stableRoute.view === 'dashboard') {
        // Reset to dashboard state
        currentKBRef.current = null;
        setCurrentKB(null);
        setNavigationPath([]);
        setCurrentUserPermission(null);
        setPermissions([]);
      } else if (stableRoute.view === 'knowledge-base' && stableRoute.kbId) {
        if (currentKBRef.current?.id !== stableRoute.kbId) {
          try {
            const kb = await KnowledgeBaseAPI.getKnowledgeBase(stableRoute.kbId);
            currentKBRef.current = kb;
            setCurrentKB(kb);
            setNavigationPath([{ id: kb.id, name: kb.name, type: 'kb' }]);
            setCurrentUserPermission(null);
            setPermissions([]);

            setTimeout(async () => {
              await loadKBContents(stableRoute.kbId!);
            }, 50);
          } catch (err: any) {
            setError('Knowledge base not found');
            navigate({ view: 'dashboard' });
          }
        }
      } else if (stableRoute.view === 'folder' && stableRoute.kbId && stableRoute.folderId) {
        if (currentKBRef.current?.id !== stableRoute.kbId) {
          try {
            const kb = await KnowledgeBaseAPI.getKnowledgeBase(stableRoute.kbId);
            currentKBRef.current = kb;
            setCurrentKB(kb);
            setNavigationPath([
              { id: kb.id, name: kb.name, type: 'kb' },
              { id: stableRoute.folderId, name: 'Folder', type: 'folder' },
            ]);
            setCurrentUserPermission(null);
            setPermissions([]);

            setTimeout(async () => {
              await loadKBContents(stableRoute.kbId!, stableRoute.folderId);
            }, 50);
          } catch (err: any) {
            setError('Folder not found');
            navigate({ view: 'dashboard' });
          }
        }
      }
    };

    initializeFromRoute();
  }, [
    isInitialized,
    stableRoute.view,
    stableRoute.kbId,
    stableRoute.folderId,
    loadKBContents,
    navigate,
    stableRoute,
  ]);

  const handleCreateFolder = async (name: string) => {
    if (!currentKB) return;

    setPageLoading(true);
    try {
      const newFolder = await KnowledgeBaseAPI.createFolder(
        currentKB.id,
        stableRoute.folderId || null,
        name
      );

      setItems((prev) => [
        {
          ...newFolder,
          type: 'folder' as const,
          createdAt: newFolder.createdAtTimestamp || Date.now(),
          updatedAt: newFolder.updatedAtTimestamp || Date.now(),
        },
        ...prev,
      ]);

      setSuccess('Folder created successfully');
      setCreateFolderDialog(false);
    } catch (err: any) {
      setError(err.message || 'Failed to create folder');
    } finally {
      setPageLoading(false);
    }
  };

  const handleEditFolder = async (name: string) => {
    if (!itemToEdit || !currentKB) return;

    setPageLoading(true);
    try {
      await KnowledgeBaseAPI.updateFolder(currentKB.id, itemToEdit.id, name);

      setItems((prev) =>
        prev.map((item) => (item.id === itemToEdit.id ? { ...item, name } : item))
      );

      setNavigationPath((prev) =>
        prev.map((item) => (item.id === itemToEdit.id ? { ...item, name } : item))
      );

      setSuccess('Folder updated successfully');
      setEditFolderDialog(false);
      setItemToEdit(null);
    } catch (err: any) {
      setError(err.message || 'Failed to update folder');
    } finally {
      setPageLoading(false);
    }
  };

  // Socket.IO integration for real-time updates (replaces polling)
  const { on, off, isConnected: socketConnected } = useSocket({
    onConnect: () => {
      // eslint-disable-next-line no-console
    },
    onDisconnect: () => {
      // eslint-disable-next-line no-console
    },
    onError: (socketError) => {
      // eslint-disable-next-line no-console
      console.error('Socket.IO error', socketError);
    },
  });

  // Listen for record processing completion events
  useEffect(() => {
    if (!currentKB) return;

    const handleRecordsProcessed = async (data: {
      recordIds: string[];
      orgId: string;
      kbId?: string;
      folderId?: string;
      totalRecords: number;
      timestamp: number;
    }) => {

      // Clear active uploads for this KB/folder
      if (currentKB) {
        const eventKbId = data.kbId || currentKB.id;
        // Handle folderId: it may be undefined/null (uploading to KB root) or a string (uploading to folder)
        const eventFolderId = data.folderId !== undefined && data.folderId !== null ? data.folderId : undefined;
        const currentFolderId = stableRoute.folderId || undefined;

        // Only process if this event is for the current KB
        if (eventKbId === currentKB.id) {
          // Match folder: both undefined/null means root, or both have same value
          const folderMatch = 
            (eventFolderId === undefined && currentFolderId === undefined) ||
            (eventFolderId === currentFolderId);

          if (folderMatch) {
            setActiveUploads((prev) => {
              const newMap = new Map(prev);
              // Use consistent key format: 'root' for undefined folderId
              const uploadKey = `${eventKbId}-${eventFolderId || 'root'}`;
              const upload = newMap.get(uploadKey);

              if (upload) {
                // Check if any records from this upload are in the processed list
                // Use a more lenient matching: if we have recordIds, check if any match
                // Otherwise, just mark as completed if the KB/folder matches
                const hasMatchingRecords = upload.recordIds && upload.recordIds.length > 0
                  ? upload.recordIds.some((id) => data.recordIds?.includes(id))
                  : true; // If no recordIds stored, trust the KB/folder match

                // Also check if the total count matches (additional validation)
                const countMatches = upload.files.length === data.totalRecords;

                if (hasMatchingRecords || countMatches) {
                  // Clear fallback timeout if it exists
                  const existingTimeout = fallbackTimeoutsRef.current.get(uploadKey);
                  if (existingTimeout) {
                    clearTimeout(existingTimeout);
                    fallbackTimeoutsRef.current.delete(uploadKey);
                  }
                  
                  // Mark as completed - notification will persist until user manually closes it
                  // Preserve failed files if they exist
                  const updatedUpload: ActiveUpload = { 
                    ...upload, 
                    status: 'completed',
                    // Keep failed files from initial response or previous socket events
                    failedFiles: upload.failedFiles,
                    hasFailures: upload.hasFailures || false,
                  };
                  newMap.set(uploadKey, updatedUpload);
                  
                  // Show success snackbar when processing is actually complete (Socket.IO confirmed)
                  // Only show if there are no failures
                  if (!upload.hasFailures) {
                    setSuccess(`Successfully processed ${data.totalRecords} file${data.totalRecords > 1 ? 's' : ''}`);
                  }
                } else if (upload.status !== 'completed') {
                  // Partial match - update status but don't remove yet
                  const partialUpdate = { ...upload, status: 'processing' as const };
                  newMap.set(uploadKey, partialUpdate);
                }
              }
              return newMap;
            });
          }
        }
      }

      // Update items: remove processing flag and refresh status
      if (data.recordIds && data.recordIds.length > 0) {
        setItems((prev) =>
          prev.map((item) => {
            if (data.recordIds.includes(item.id || item._key || '')) {
              return {
                ...item,
                isProcessing: false,
                // Status will be updated when we refresh
              };
            }
            return item;
          })
        );

        // Refresh the list to get updated status from server
        await loadKBContents(currentKB.id, stableRoute.folderId, true, true);
      }
    };

    // Handle failed records event
    const handleRecordsFailed = async (data: {
      failedFiles: Array<{
        recordId: string;
        fileName: string;
        filePath: string;
        error: string;
      }>;
      orgId: string;
      kbId?: string;
      folderId?: string;
      totalFailed: number;
      timestamp: number;
    }) => {
      // Update active uploads to show failed status
      if (currentKB) {
        const eventKbId = data.kbId || currentKB.id;
        const eventFolderId = data.folderId !== undefined && data.folderId !== null ? data.folderId : undefined;
        const currentFolderId = stableRoute.folderId || undefined;

        // Only process if this event is for the current KB
        if (eventKbId === currentKB.id) {
          // Match folder: both undefined/null means root, or both have same value
          const folderMatch = 
            (eventFolderId === undefined && currentFolderId === undefined) ||
            (eventFolderId === currentFolderId);

          if (folderMatch) {
            setActiveUploads((prev) => {
              const newMap = new Map(prev);
              const uploadKey = `${eventKbId}-${eventFolderId || 'root'}`;
              const upload = newMap.get(uploadKey);

              if (upload) {
                // Clear fallback timeout if it exists
                const existingTimeout = fallbackTimeoutsRef.current.get(uploadKey);
                if (existingTimeout) {
                  clearTimeout(existingTimeout);
                  fallbackTimeoutsRef.current.delete(uploadKey);
                }
                
                // Merge failed files - combine with any existing failed files from initial response
                const existingFailedFiles: FailedFileInfo[] = upload.failedFiles || [];
                const newFailedFiles: FailedFileInfo[] = data.failedFiles || [];
                // Create a map to avoid duplicates
                const failedFilesMap = new Map<string, FailedFileInfo>();
                existingFailedFiles.forEach((ff) => {
                  failedFilesMap.set(ff.fileName || ff.filePath, ff);
                });
                newFailedFiles.forEach((ff) => {
                  failedFilesMap.set(ff.fileName || ff.filePath, ff);
                });
                const mergedFailedFiles = Array.from(failedFilesMap.values());
                
                // Update upload with failed files information
                const updatedUpload = {
                  ...upload,
                  status: 'completed' as const, // Mark as completed so notification can be dismissed
                  failedFiles: mergedFailedFiles,
                  hasFailures: true,
                };
                newMap.set(uploadKey, updatedUpload);
                
                // Show error snackbar for failed files
                setError(`Failed to process ${mergedFailedFiles.length} file${mergedFailedFiles.length > 1 ? 's' : ''}`);
              }
              return newMap;
            });
          }
        }
      }

      // Remove failed files from items list (they won't appear in the UI)
      if (data.failedFiles && data.failedFiles.length > 0) {
        const failedRecordIds = data.failedFiles.map((f) => f.recordId);
        setItems((prev) =>
          prev.filter((item) => !failedRecordIds.includes(item.id || item._key || ''))
        );
      }

      // Refresh the list to ensure consistency
      if (currentKB) {
        await loadKBContents(currentKB.id, stableRoute.folderId, true, true);
      }
    };

    // Set up event listeners when socket is connected
    if (socketConnected) {
      on('records:processed', handleRecordsProcessed);
      on('records:failed', handleRecordsFailed);
    }

    // eslint-disable-next-line consistent-return
    return () => {
      if (socketConnected) {
        off('records:processed', handleRecordsProcessed);
        off('records:failed', handleRecordsFailed);
      }
    };
  }, [socketConnected, currentKB, stableRoute.folderId, loadKBContents, on, off, setError]);

  // Handle upload start - store upload info but don't show notification yet
  // Notification will be shown when dialog closes to avoid UI clutter
  const handleUploadStart = useCallback(
    (files: string[], kbId: string, folderId?: string) => {
      if (!currentKB || kbId !== currentKB.id) return;
      
      // Store upload info temporarily - notification will be shown when dialog closes
      const uploadKey = `${kbId}-${folderId || 'root'}`;
      const newUpload = {
        kbId,
        folderId,
        files,
        startTime: Date.now(),
        status: 'uploading' as const,
      };
      
      // Save to storage but don't show notification yet (will show when dialog closes)
      try {
        const stored = sessionStorage.getItem(ACTIVE_UPLOADS_STORAGE_KEY);
        const parsed = stored ? JSON.parse(stored) : {};
        parsed[uploadKey] = newUpload;
        sessionStorage.setItem(ACTIVE_UPLOADS_STORAGE_KEY, JSON.stringify(parsed));
      } catch (e) {
        console.error('handleUploadStart: Failed to save to storage:', e);
      }
    },
    [currentKB]
  );

  const handleUploadSuccess = useCallback(
    async (message?: string, records?: any[], failedFiles?: Array<{fileName: string; filePath: string; error: string}>) => {
      if (!currentKB) {
        console.error('handleUploadSuccess: No currentKB');
        return;
      }

      try {
        // Don't show success snackbar here - it will show when Socket.IO confirms processing is complete
        // Dialog is already closed by upload manager, now show notification

        const uploadKey = `${currentKB.id}-${stableRoute.folderId || 'root'}`;
        const fileNames = records ? records.map((r) => r.recordName || r.name || 'Unknown file') : [];
        const allFileNames = failedFiles 
          ? [...fileNames, ...failedFiles.map((ff) => ff.fileName || ff.filePath)]
          : fileNames;

        // Handle failed files from response - send notification immediately
        if (failedFiles && failedFiles.length > 0) {
          // Send failed files notification via socket if available
          // This ensures failed files are notified even if socket events from backend fail
          const failedFilesWithIds = failedFiles.map((ff) => ({
            recordId: `temp-${Date.now()}-${Math.random()}`, // Temporary ID for tracking
            fileName: ff.fileName,
            filePath: ff.filePath,
            error: ff.error,
          }));

          // Update active uploads with failed files
          setActiveUploads((prev) => {
            const newMap = new Map(prev);
            const upload = newMap.get(uploadKey);
            
            if (upload) {
              // If ALL files failed (no successful records), mark as completed immediately
              // If some succeeded, mark as processing (waiting for backend processing)
              const allFilesFailed = !records || records.length === 0;
              
              const updatedUpload = {
                ...upload,
                files: allFileNames,
                recordIds: records ? records.map((r) => r._key) : [],
                status: allFilesFailed ? 'completed' as const : 'processing' as const,
                failedFiles: failedFilesWithIds,
                hasFailures: true,
              };
              newMap.set(uploadKey, updatedUpload);
            }
            
            return newMap;
          });
          
          // Show error snackbar for failed files
          const failedCount = failedFiles.length;
          if (!records || records.length === 0) {
            // All files failed
            setError(`Failed to upload ${failedCount} file${failedCount > 1 ? 's' : ''}`);
          } else {
            // Partial failure - some succeeded, some failed
            setError(`${failedCount} file${failedCount > 1 ? 's' : ''} failed to upload`);
          }
        }

        // Optimistic UI update: Add records immediately if provided
        if (records && records.length > 0) {
          // Show notification when dialog closes - load from storage or create new
          setActiveUploads((prev) => {
            const newMap = new Map(prev);
            
            // Try to load from storage first (created in handleUploadStart)
            let existingUpload = newMap.get(uploadKey);
            if (!existingUpload) {
              try {
                const stored = sessionStorage.getItem(ACTIVE_UPLOADS_STORAGE_KEY);
                if (stored) {
                  const parsed = JSON.parse(stored);
                  existingUpload = parsed[uploadKey];
                }
              } catch (e) {
                // Ignore errors
              }
            }
            
            if (existingUpload) {
              // Update existing notification with record IDs and processing status
              const updatedUpload = {
                ...existingUpload,
                files: allFileNames, // Include all files (successful + failed)
                recordIds: records.map((r) => r._key),
                status: 'processing' as const,
                failedFiles: failedFiles ? failedFiles.map((ff) => ({
                  recordId: `temp-${Date.now()}-${Math.random()}`,
                  fileName: ff.fileName,
                  filePath: ff.filePath,
                  error: ff.error,
                })) : existingUpload.failedFiles,
                hasFailures: failedFiles && failedFiles.length > 0,
              };
              
              newMap.set(uploadKey, updatedUpload);
              
              // Also update in storage
              try {
                const stored = sessionStorage.getItem(ACTIVE_UPLOADS_STORAGE_KEY);
                const parsed = stored ? JSON.parse(stored) : {};
                parsed[uploadKey] = updatedUpload;
                sessionStorage.setItem(ACTIVE_UPLOADS_STORAGE_KEY, JSON.stringify(parsed));
              } catch (e) {
                // Ignore errors
              }
            } else {
              // Create new notification if it doesn't exist
              const newUpload = {
                kbId: currentKB.id,
                folderId: stableRoute.folderId,
                files: allFileNames,
                startTime: Date.now(),
                recordIds: records.map((r) => r._key),
                status: 'processing' as const,
                failedFiles: failedFiles ? failedFiles.map((ff) => ({
                  recordId: `temp-${Date.now()}-${Math.random()}`,
                  fileName: ff.fileName,
                  filePath: ff.filePath,
                  error: ff.error,
                })) : undefined,
                hasFailures: failedFiles && failedFiles.length > 0,
              };
              newMap.set(uploadKey, newUpload);
            }
            
            return newMap;
          });

          const optimisticItems: Item[] = records.map((record) => ({
            id: record._key,
            _key: record._key,
            name: record.recordName || record.name,
            type: 'file' as const,
            recordName: record.recordName,
            recordType: record.recordType,
            indexingStatus: (record.indexingStatus || 'PROCESSING') as Item['indexingStatus'],
            origin: record.origin,
            connectorName: record.connectorName,
            webUrl: record.webUrl || '',
            externalRecordId: record.externalRecordId,
            fileRecord: record.fileRecord,
            sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
            sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
            version: record.version,
            createdAt: record.createdAtTimestamp || Date.now(),
            updatedAt: record.updatedAtTimestamp || Date.now(),
            extension: record.fileRecord?.extension ?? undefined,
            sizeInBytes: record.fileRecord?.sizeInBytes,
            // Mark as processing for visual feedback
            isProcessing: record.indexingStatus === 'PROCESSING',
          }));

          // Add records optimistically to the UI
          setItems((prev) => {
            // Avoid duplicates
            const existingIds = new Set(prev.map((item) => item.id));
            const newItems = optimisticItems.filter((item) => !existingIds.has(item.id));
            return [...newItems, ...prev];
          });

          // Notification will persist until user manually closes it
          // Real-time updates will be handled via Socket.IO events
          // No polling needed - the 'records:processed' event will trigger a refresh
          
          // Fallback timeout: If socket events don't arrive within 5 seconds, mark as completed
          // This prevents the notification from getting stuck in "processing" state
          // Especially important for local storage where processing is very fast
          // Clear any existing timeout for this upload key
          const existingTimeout = fallbackTimeoutsRef.current.get(uploadKey);
          if (existingTimeout) {
            clearTimeout(existingTimeout);
          }
          
          const fallbackTimeout = setTimeout(() => {
            setActiveUploads((prev) => {
              const newMap = new Map(prev);
              const upload = newMap.get(uploadKey);
              
              // Only apply fallback if still in processing state
              if (upload && upload.status === 'processing') {
                const updatedUpload = {
                  ...upload,
                  status: 'completed' as const,
                };
                newMap.set(uploadKey, updatedUpload);
                
                // Refresh to get actual status from server
                loadKBContents(currentKB.id, stableRoute.folderId, true, true).catch(() => {
                  // Ignore errors
                });
              }
              
              // Remove timeout from ref
              fallbackTimeoutsRef.current.delete(uploadKey);
              
              return newMap;
            });
          }, SOCKET_EVENT_TIMEOUT_MS);
          
          // Store timeout ID in ref
          fallbackTimeoutsRef.current.set(uploadKey, fallbackTimeout);
        } else {
          // Fallback: Reload if no records provided
          await loadKBContents(currentKB.id, stableRoute.folderId, true, true);
        }
      } catch (err: unknown) {
        const uploadError = err instanceof Error ? err : new Error(String(err));
        setError(uploadError.message || 'Upload failed');
      }
    },
    [stableRoute.folderId, loadKBContents, currentKB]
  );

  const handleDelete = async () => {
    if (!itemToDelete) return;

    setPageLoading(true);
    try {
      if (itemToDelete.type === 'folder') {
        if (!currentKB) {
          setError('No collection selected');
          return;
        }
        await KnowledgeBaseAPI.deleteFolder(currentKB.id, itemToDelete.id);
        setItems((prev) => prev.filter((item) => item.id !== itemToDelete.id));
        setSuccess('Folder deleted successfully');
      } else if (itemToDelete.type === 'file') {
        await KnowledgeBaseAPI.deleteRecord(itemToDelete.id);
        setItems((prev) => prev.filter((item) => item.id !== itemToDelete.id));
        setSuccess('File deleted successfully');
      }

      setDeleteDialog(false);
      setItemToDelete(null);
    } catch (err: any) {
      setError(err.message || 'Failed to delete item');
    } finally {
      setPageLoading(false);
    }
  };

  const handleCreatePermissions = async (data: CreatePermissionRequest) => {
    if (!currentKB) return;
    await KnowledgeBaseAPI.createKBPermissions(currentKB.id, data);
  };

  const handleUpdatePermission = async (data: UpdatePermissionRequest) => {
    if (!currentKB) return;
    await KnowledgeBaseAPI.updateKBPermission(currentKB.id, data);
  };

  const handleRemovePermission = async (data: RemovePermissionRequest) => {
    if (!currentKB) return;
    await KnowledgeBaseAPI.removeKBPermission(currentKB.id, data);
  };

  const handleRefreshPermissions = async () => {
    if (!currentKB) return;
    await loadKBPermissions(currentKB.id);
  };

  const openPermissionsDialog = () => {
    if (currentKB) {
      loadKBPermissions(currentKB.id);
      setPermissionsDialog(true);
    }
  };

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, item: MenuItemWithAction) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
    setContextItem(item);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setContextItem(null);
  };

  const handleEditMenuAction = () => {
    if (!contextItem) return;
    setItemToEdit(contextItem);
    setEditFolderDialog(true);
    handleMenuClose();
  };

  const handleDeleteMenuAction = () => {
    if (!contextItem) return;

    setItemToDelete(contextItem);
    setDeleteDialog(true);
    handleMenuClose();
  };

  const navigateToPathIndex = useCallback(
    (index: number) => {
      if (index === 0 && navigationPath[0]?.type === 'kb') {
        const kb = navigationPath[0];
        setNavigationPath([kb]);
        navigate({ view: 'knowledge-base', kbId: kb.id });

        // Reset state
        setItems([]);
        setPage(0);
        setSearchQuery('');
        setError(null);
        setCurrentUserPermission(null);
        setPermissions([]);
        isViewInitiallyLoading.current = true;

        setTimeout(async () => {
          if (!loadingRef.current) {
            await loadKBContents(kb.id);
          }
        }, 50);
      } else if (index > 0) {
        const newPath = navigationPath.slice(0, index + 1);
        setNavigationPath(newPath);
        const target = newPath[index];
        navigate({ view: 'folder', kbId: currentKB!.id, folderId: target.id });

        // Reset state
        setItems([]);
        setPage(0);
        setSearchQuery('');
        setError(null);
        isViewInitiallyLoading.current = true;

        setTimeout(async () => {
          if (!loadingRef.current) {
            await loadKBContents(currentKB!.id, target.id);
          }
        }, 50);
      }
    },
    [navigationPath, currentKB, navigate, loadKBContents]
  );

  const handleViewChange = (
    event: React.MouseEvent<HTMLElement>,
    newView: ViewMode | null
  ): void => {
    if (newView !== null) {
      setViewMode(newView);
      if (newView === 'list' && currentKB) {
        isViewInitiallyLoading.current = true;
        setPage(0);
        setTimeout(async () => {
          if (!loadingRef.current) {
            await loadKBContents(currentKB.id, stableRoute.folderId);
          }
        }, 50);
      }
    }
  };

  const handleRefresh = useCallback(async () => {
    if (!currentKB) return;

    // Clear the last load params to force a fresh reload
    lastLoadParams.current = '';

    // Force reload with current parameters
    await loadKBContents(currentKB.id, stableRoute.folderId, true, true);
  }, [stableRoute.folderId, loadKBContents, currentKB]);

  const handleRetryIndexing = async (recordId: string) => {
    if (!currentKB) {
      setError('No KB id found, please refresh');
      return;
    }
    try {
      const response = await KnowledgeBaseAPI.reindexRecord(recordId);
      if (response.success) {
        setSuccess('File indexing started successfully');
        await loadKBContents(currentKB.id, stableRoute.folderId, true, true);
      } else {
        setError(response.reason || 'Failed to start reindexing');
      }
      handleMenuClose();
    } catch (err: any) {
      console.error('Failed to reindexing document', err);
      setError(err.response?.data?.reason || err.message || 'Failed to start reindexing');
      handleMenuClose();
    }
  };

  const handleDownload = async (recordId: string, recordName: string) => {
    try {
      await KnowledgeBaseAPI.handleDownloadDocument(recordId, recordName);
      setSuccess('Download started successfully');
    } catch (err: any) {
      console.error('Failed to download document', err);
    }
  };

  const renderContextMenu = () => {
    const canModify =
      currentUserPermission?.role === 'OWNER' || currentUserPermission?.role === 'WRITER';
    const canReindex =
      currentUserPermission?.role === 'OWNER' ||
      currentUserPermission?.role === 'WRITER' ||
      currentUserPermission?.role === 'READER';

    const folderMenuItems = [
      {
        key: 'open',
        label: 'Open',
        icon: folderOpenIcon,
        onClick: () => {
          navigateToFolder(contextItem);
          handleMenuClose();
        },
      },
      ...(canModify
        ? [
          {
            key: 'edit',
            label: 'Edit',
            icon: editIcon,
            onClick: handleEditMenuAction,
          },
        ]
        : []),
      ...(canModify
        ? [
          {
            key: 'delete',
            label: 'Delete',
            icon: deleteIcon,
            onClick: handleDeleteMenuAction,
            isDanger: true,
          },
        ]
        : []),
    ];

    const fileMenuItems = [
      {
        key: 'view-record',
        label: 'View record',
        icon: eyeIcon,
        onClick: () => {
          window.open(`/record/${contextItem.id}`, '_blank', 'noopener,noreferrer');
          handleMenuClose();
        },
      },
      {
        key: 'download',
        label: 'Download',
        icon: downloadIcon,
        onClick: () => {
          handleDownload(contextItem.id, contextItem.name);
          handleMenuClose();
        },
      },
      ...(canReindex
        ? [
          {
            key: 'reindex',
            label: 'Reindex',
            icon: refreshIcon,
            onClick: () => {
              handleRetryIndexing(contextItem.id);
              handleMenuClose();
            },
          },
        ]
        : []),
      ...(canModify
        ? [
          {
            key: 'delete',
            label: 'Delete',
            icon: deleteIcon,
            onClick: handleDeleteMenuAction,
            isDanger: true,
          },
        ]
        : []),
    ];

    const menuItems = contextItem?.type === 'folder' ? folderMenuItems : fileMenuItems;

    return (
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleMenuClose}
        PaperProps={{
          elevation: 2,
          sx: {
            minWidth: 180,
            overflow: 'hidden',
            borderRadius: 2,
            mt: 1,
            boxShadow: '0 6px 16px rgba(0,0,0,0.08)',
            border: '1px solid',
            borderColor: 'rgba(0,0,0,0.04)',
            backdropFilter: 'blur(8px)',
            '.MuiList-root': {
              py: 0.75,
            },
          },
        }}
        transformOrigin={{ horizontal: 'right', vertical: 'top' }}
        anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
        transitionDuration={150}
      >
        {menuItems.map((item, index) => {
          const isDangerItem = item.isDanger;
          const showDivider = isDangerItem && index > 0;

          return (
            <React.Fragment key={item.key}>
              {showDivider && <Divider sx={{ my: 0.75, opacity: 0.6 }} />}

              <MenuItem
                onClick={(e) => {
                  e.stopPropagation();
                  item.onClick();
                }}
                sx={{
                  py: 0.75,
                  mx: 0.75,
                  my: 0.25,
                  px: 1.5,
                  borderRadius: 1.5,
                  transition: 'all 0.15s ease',
                  ...(isDangerItem
                    ? {
                      color: 'error.main',
                      '&:hover': {
                        bgcolor: 'error.lighter',
                        transform: 'translateX(2px)',
                      },
                    }
                    : {
                      '&:hover': {
                        bgcolor: (themeVal) =>
                          themeVal.palette.mode === 'dark'
                            ? alpha('#fff', 0.06)
                            : alpha('#000', 0.04),
                        transform: 'translateX(2px)',
                      },
                    }),
                }}
              >
                <ListItemIcon
                  sx={{
                    minWidth: 30,
                    color: isDangerItem ? 'error.main' : 'inherit',
                  }}
                >
                  <Icon icon={item.icon} width={18} height={18} />
                </ListItemIcon>

                <ListItemText
                  primary={item.label}
                  primaryTypographyProps={{
                    variant: 'body2',
                    fontWeight: 500,
                    fontSize: '0.875rem',
                    letterSpacing: '0.01em',
                  }}
                />
              </MenuItem>
            </React.Fragment>
          );
        })}
      </Menu>
    );
  };

  const getDeleteMessage = () => {
    if (!itemToDelete) return '';

    const name = `<strong>${itemToDelete.name}</strong>`;
    if (itemToDelete.type === 'kb') {
      return `Are you sure you want to delete ${name}? This will permanently delete the collection and all its contents. This action cannot be undone.`;
    }
    if (itemToDelete.type === 'folder') {
      return `Are you sure you want to delete ${name}? This will permanently delete the folder and all its contents. This action cannot be undone.`;
    }
    return `Are you sure you want to delete ${name}? This action cannot be undone.`;
  };

  if (!isInitialized) {
    return (
      <MainContainer>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            height: '100vh',
          }}
        >
          <CircularProgress size={40} thickness={4} />
        </Box>
      </MainContainer>
    );
  }

  return (
    <MainContainer>
      {pageLoading && (
        <LinearProgress
          sx={{
            position: 'absolute',
            width: '100%',
            top: 0,
            zIndex: 1400,
            height: 2,
          }}
        />
      )}

      <ContentArea>
        {stableRoute.view === 'dashboard' ? (
          <DashboardComponent
            key="dashboard"
            theme={theme}
            navigateToKB={navigateToKB}
            CompactCard={CompactCard}
            isInitialized={isInitialized}
            navigate={navigate}
          />
        ) : (
          <Box key={`kb-${stableRoute.kbId}-${stableRoute.folderId || 'root'}`}>
            {renderKBDetail({
              navigationPath,
              navigateToDashboard,
              navigateToPathIndex,
              theme,
              viewMode,
              handleViewChange,
              navigateBack,
              CompactCard,
              items,
              pageLoading,
              navigateToFolder,
              handleMenuOpen,
              totalCount,
              hasMore,
              loadingMore,
              handleLoadMore,
              currentKB,
              loadKBContents,
              stableRoute,
              currentUserPermission,
              setCreateFolderDialog,
              setUploadDialog,
              openPermissionsDialog,
              handleRefresh,
              setPage,
              setRowsPerPage,
              rowsPerPage,
              page,
            })}
          </Box>
        )}
      </ContentArea>

      <CreateFolderDialog
        open={createFolderDialog}
        onClose={() => setCreateFolderDialog(false)}
        onSubmit={handleCreateFolder}
        loading={pageLoading}
      />

      <EditFolderDialog
        open={editFolderDialog}
        onClose={() => {
          setEditFolderDialog(false);
          setItemToEdit(null);
        }}
        onSubmit={handleEditFolder}
        currentName={itemToEdit?.name || ''}
        loading={pageLoading}
      />

      <UploadManager
        open={uploadDialog}
        onClose={() => {
          setUploadDialog(false);
          // When dialog closes, load and show any pending upload notifications from storage
          const stored = sessionStorage.getItem(ACTIVE_UPLOADS_STORAGE_KEY);
          if (stored) {
            try {
              const parsed = JSON.parse(stored) as Record<string, {
                kbId: string;
                folderId?: string;
                files: string[];
                startTime: number;
                recordIds?: string[];
                status?: 'uploading' | 'processing' | 'completed';
              }>;
              const uploadsMap = new Map(Object.entries(parsed));
              // Filter for current KB/folder
              const filtered = Array.from(uploadsMap.entries()).filter(([_, upload]) => {
                if (!upload) return false;
                if (currentKB && upload.kbId !== currentKB.id) return false;
                const uploadFolderId = upload.folderId || 'root';
                const viewFolderId = stableRoute.folderId || 'root';
                return uploadFolderId === viewFolderId;
              });
              
              if (filtered.length > 0) {
                // Update state to show notifications
                setActiveUploads((prev) => {
                  const newMap = new Map(prev);
                  filtered.forEach(([key, upload]) => {
                    newMap.set(key, upload);
                  });
                  return newMap;
                });
              }
            } catch (e) {
              // Ignore errors
            }
          }
        }}
        knowledgeBaseId={currentKB?.id}
        folderId={stableRoute.folderId}
        onUploadSuccess={handleUploadSuccess}
        onUploadStart={handleUploadStart}
      />

      <DeleteConfirmDialog
        open={deleteDialog}
        onClose={() => {
          setDeleteDialog(false);
          setItemToDelete(null);
        }}
        onConfirm={handleDelete}
        title="Confirm Delete"
        message={getDeleteMessage()}
        loading={pageLoading}
      />

      <KbPermissionsDialog
        open={permissionsDialog}
        onClose={() => setPermissionsDialog(false)}
        kbId={currentKB?.id || ''}
        kbName={currentKB?.name || ''}
      />

      {/* Context Menu */}
      {renderContextMenu()}

      {/* Upload Notification - Bottom Right Corner (Google Drive-like) */}
      {/* Only show notifications for the current KB/folder being viewed */}
      <UploadNotification
        uploads={activeUploads}
        currentKBId={currentKB?.id}
        currentFolderId={stableRoute.folderId}
        onDismiss={(uploadKey) => {
          setActiveUploads((prev) => {
            const newMap = new Map(prev);
            newMap.delete(uploadKey);
            return newMap;
          });
        }}
      />

      {/* Snackbars */}
      <Snackbar
        open={!!success}
        autoHideDuration={3000}
        onClose={() => setSuccess('')}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          severity="success"
          onClose={() => setSuccess('')}
          sx={{
            borderRadius: 2,
            fontSize: '0.85rem',
            fontWeight: 500,
          }}
        >
          {success}
        </Alert>
      </Snackbar>

      <Snackbar
        open={!!error}
        autoHideDuration={5000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert
          severity="error"
          onClose={() => setError(null)}
          sx={{
            borderRadius: 2,
            fontSize: '0.85rem',
            fontWeight: 500,
          }}
        >
          {error}
        </Alert>
      </Snackbar>
    </MainContainer>
  );
}
