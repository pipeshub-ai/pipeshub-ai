import { Icon } from '@iconify/react';
import { useNavigate } from 'react-router';
import brainIcon from '@iconify-icons/mdi/brain';
import refreshIcon from '@iconify-icons/mdi/refresh';
import homeIcon from '@iconify-icons/mdi/home-outline';
import viewListIcon from '@iconify-icons/mdi/view-list';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import editIcon from '@iconify-icons/mdi/pencil-outline';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import downloadIcon from '@iconify-icons/mdi/download-outline';
import chevronRightIcon from '@iconify-icons/mdi/chevron-right';
import viewGridIcon from '@iconify-icons/mdi/view-grid-outline';
import folderOpenIcon from '@iconify-icons/mdi/folder-open-outline';
import folderPlusIcon from '@iconify-icons/mdi/folder-plus-outline';
import fileUploadIcon from '@iconify-icons/mdi/file-upload-outline';
import accountMultipleIcon from '@iconify-icons/mdi/account-multiple';
import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';

import {
  Box,
  Card,
  Menu,
  Fade,
  Paper,
  Stack,
  Alert,
  alpha,
  Button,
  styled,
  Snackbar,
  useTheme,
  MenuItem,
  Typography,
  IconButton,
  Breadcrumbs,
  ListItemIcon,
  ListItemText,
  ToggleButton,
  LinearProgress,
  Link as MuiLink,
  CircularProgress,
  ToggleButtonGroup,
  Divider,
} from '@mui/material';

import UploadManager from './upload-manager';
import { useRouter } from './hooks/use-router';
import { ListView } from './components/list-view';
import { KnowledgeBaseAPI } from './services/api';
import { GridView } from './components/grid-view';
import DashboardComponent from './components/dashboard';
import AllRecordsView from './components/all-records-view';
// Import the new edit dialogs
import { EditFolderDialog, EditKnowledgeBaseDialog } from './components/dialogs/edit-dialogs';
import {
  CreateFolderDialog,
  DeleteConfirmDialog,
  ManagePermissionsDialog,
  CreateKnowledgeBaseDialog,
} from './components/dialogs';

// Import types and services
import type {
  Item,
  KBPermission,
  KnowledgeBase,
  UserPermission,
  CreatePermissionRequest,
  UpdatePermissionRequest,
} from './types/kb';

type ViewMode = 'grid' | 'list';

// Extended type for menu handling
interface MenuItemWithAction extends KnowledgeBase {
  type: string;
  action?: 'edit' | 'delete';
}

// Modern Styled Components - Sleek and Minimalistic
const MainContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}));

const ContentArea = styled(Box)({
  flexGrow: 1,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
});

const ModernToolbar = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(1, 1.5),
  borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  backgroundColor: alpha(theme.palette.background.paper, 0.9),
  backdropFilter: 'blur(20px)',
  display: 'flex',
  flexDirection: 'column', // Base for mobile
  gap: theme.spacing(1.5),
  borderRadius: 0,
  boxShadow: 'none',
  minHeight: 'auto',

  // Tablet View (md breakpoint) - Horizontal, space-between
  [theme.breakpoints.up('md')]: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: theme.spacing(2),
    padding: theme.spacing(1.5, 3),
  },

  // Large Desktop View (lg breakpoint) - Same as tablet, just ensuring consistency
  [theme.breakpoints.up('lg')]: {
    minHeight: 60,
  },
}));

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

const MinimalBreadcrumb = styled(MuiLink)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(0.5),
  padding: theme.spacing(0.5, 0.75),
  borderRadius: 8,
  textDecoration: 'none',
  fontSize: '0.8rem',
  fontWeight: 500,
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  color: theme.palette.text.secondary,
  transition: 'all 0.15s ease',
  minWidth: 0,
  maxWidth: 150,

  '& > span': {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },

  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
    color: theme.palette.primary.main,
    textDecoration: 'none',
  },
  '&:last-of-type': {
    color: theme.palette.text.primary,
    fontWeight: 600,
  },

  [theme.breakpoints.down('sm')]: {
    fontSize: '0.75rem',
    padding: theme.spacing(0.4, 0.6),
    maxWidth: 100,
  },
}));

const ActionButton = styled(Button)(({ theme }) => ({
  height: 32,
  padding: `${theme.spacing(0.75)} ${theme.spacing(1.75)}`, // replaces px/py
  borderRadius: '4px',
  textTransform: 'none',
  fontSize: '0.8125rem',
  fontWeight: 500,
  minWidth: 100,
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.23)' : 'rgba(0,0,0,0.23)',
  color: theme.palette.mode === 'dark' ? '#E0E0E0' : '#4B5563',
  backgroundColor: 'transparent',
  '&:hover': {
    borderColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.4)' : 'rgba(0,0,0,0.4)',
    backgroundColor: theme.palette.mode === 'dark' ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)',
  },
}));

const CompactIconButton = styled(IconButton)(({ theme }) => ({
  width: 36,
  height: 36,
  borderRadius: 10,
  border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
  backgroundColor: alpha(theme.palette.background.paper, 0.8),
  backdropFilter: 'blur(10px)',
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.08),
    borderColor: alpha(theme.palette.primary.main, 0.2),
    transform: 'scale(1.05)',
  },
}));

export default function KnowledgeBaseComponent() {
  const theme = useTheme();
  const { route, navigate, isInitialized } = useRouter();
  const nav = useNavigate();

  // ===== REFS TO PREVENT RACE CONDITIONS =====
  const loadingRef = useRef(false);
  const currentKBRef = useRef<KnowledgeBase | null>(null);
  const isViewInitiallyLoading = useRef(true);
  const searchTimeoutRef = useRef<NodeJS.Timeout>();
  const lastLoadParams = useRef<string>(''); // Track last load to prevent duplicates

  // ===== STATE MANAGEMENT =====
  const [allKBs, setAllKBs] = useState<KnowledgeBase[]>([]);
  const [hasMoreKBs, setHasMoreKBs] = useState(true);
  const [loadingMoreKBs, setLoadingMoreKBs] = useState(false);
  const [currentSearchQuery, setCurrentSearchQuery] = useState('');
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [currentKB, setCurrentKB] = useState<KnowledgeBase | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [currentUserPermission, setCurrentUserPermission] = useState<UserPermission | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  // Dialog states
  const [createKBDialog, setCreateKBDialog] = useState(false);
  const [createFolderDialog, setCreateFolderDialog] = useState(false);
  const [uploadDialog, setUploadDialog] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [permissionsDialog, setPermissionsDialog] = useState(false);

  // New edit dialog states
  const [editKBDialog, setEditKBDialog] = useState(false);
  const [editFolderDialog, setEditFolderDialog] = useState(false);

  // Permission state
  const [permissions, setPermissions] = useState<KBPermission[]>([]);
  const [permissionsLoading, setPermissionsLoading] = useState(false);

  // Search, Filter & Pagination
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(20);
  const [totalCount, setTotalCount] = useState(0);
  const [dashboardPage, setDashboardPage] = useState(0);
  const [dashboardRowsPerPage, setDashboardRowsPerPage] = useState(10);
  const [dashboardTotalCount, setDashboardTotalCount] = useState(0);

  // Context menu
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const [contextItem, setContextItem] = useState<any>(null);
  const [itemToDelete, setItemToDelete] = useState<any>(null);
  const [itemToEdit, setItemToEdit] = useState<any>(null);

  // Navigation path for breadcrumbs
  const [navigationPath, setNavigationPath] = useState<
    Array<{ id: string; name: string; type: 'kb' | 'folder' }>
  >([]);

  // ===== STABLE MEMOIZED VALUES =====
  // Create stable references to prevent unnecessary re-renders
  const stableRoute = useMemo(() => route, [route]);

  // ===== OPTIMIZED API CALL FUNCTIONS =====
  // Fixed: Stable callback with proper dependencies
  const loadKnowledgeBases = useCallback(
    async (query = '', isLoadMore = false) => {
      if (!isInitialized || loadingRef.current) return;

      // Prevent loading more if already loading more or no more data
      if (isLoadMore && (loadingMoreKBs || !hasMoreKBs)) return;

      const loadId = `kb-${query}-${isLoadMore ? dashboardPage + 1 : dashboardPage}`;
      if (lastLoadParams.current === loadId) return; // Prevent duplicate calls

      loadingRef.current = true;
      lastLoadParams.current = loadId;

      // Set appropriate loading states
      if (isLoadMore) {
        setLoadingMoreKBs(true);
      } else {
        setLoading(true);
        // Reset pagination for new search
        if (query !== currentSearchQuery) {
          setDashboardPage(0);
          setAllKBs([]);
          setHasMoreKBs(true);
        }
      }

      setCurrentSearchQuery(query);

      try {
        const currentPage = isLoadMore ? dashboardPage + 1 : dashboardPage;
        const params = {
          page: currentPage + 1,
          limit: dashboardRowsPerPage,
          search: query,
        };

        const data = await KnowledgeBaseAPI.getKnowledgeBases(params);

        if (data.knowledgeBases && data.pagination) {
          const newKBs = data.knowledgeBases;

          if (isLoadMore) {
            setAllKBs((prev) => [...prev, ...newKBs]);
            setDashboardPage(currentPage);
          } else {
            setAllKBs(newKBs);
            setKnowledgeBases(newKBs);
          }

          setDashboardTotalCount(data.pagination.totalCount);
          const totalPages = Math.ceil(data.pagination.totalCount / dashboardRowsPerPage);
          setHasMoreKBs(currentPage + 1 < totalPages);
        } else {
          const newKBs = Array.isArray(data) ? data : [];
          if (isLoadMore) {
            setAllKBs((prev) => [...prev, ...newKBs]);
          } else {
            setAllKBs(newKBs);
            setKnowledgeBases(newKBs);
          }
          setDashboardTotalCount(newKBs.length);
          setHasMoreKBs(false);
        }
      } catch (err: any) {
        setError(err.message || 'Failed to fetch knowledge bases');
        if (!isLoadMore) {
          setAllKBs([]);
          setKnowledgeBases([]);
        }
      } finally {
        setLoading(false);
        setLoadingMoreKBs(false);
        loadingRef.current = false;
      }
    },
    [
      isInitialized,
      dashboardRowsPerPage,
      currentSearchQuery,
      dashboardPage,
      loadingMoreKBs,
      hasMoreKBs,
    ]
  );

  const loadKBContents = useCallback(
    async (kbId: string, folderId?: string, resetItems = true, forceReload = false) => {
      if (loadingRef.current && !forceReload) return;

      const loadId = `content-${kbId}-${folderId || 'root'}-${page}-${rowsPerPage}-${searchQuery}`;

      // Skip deduplication if force reload is requested
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
          name: record.recordName || record.name,
          type: 'file' as const,
          createdAt: record.createdAtTimestamp || Date.now(),
          updatedAt: record.updatedAtTimestamp || Date.now(),
          extension: record.fileRecord?.extension,
          sizeInBytes: record.fileRecord?.sizeInBytes,
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
    [page, searchQuery, rowsPerPage] // Empty dependencies to keep function stable
  );

  // ===== SEARCH DEBOUNCING =====
  // Fixed: Separate search effect with proper cleanup
  useEffect(() => {
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    // Only search if we have a current KB and not in dashboard view
    if (currentKB && stableRoute.view !== 'dashboard') {
      searchTimeoutRef.current = setTimeout(() => {
        if (!loadingRef.current) {
          loadKBContents(currentKB.id, stableRoute.folderId);
        }
      }, 300);
    }

    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, [searchQuery, currentKB?.id, stableRoute.folderId, stableRoute.view,currentKB,loadKBContents]); // Stable dependencies

  // ===== LOAD MORE HANDLER =====
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

  // ===== PAGINATION EFFECT =====
  // Fixed: Only reload for pagination changes, not search (handled separately)
  useEffect(() => {
    if (isViewInitiallyLoading.current) return;

    if (currentKB && viewMode === 'list' && !searchQuery) {
      // Don't reload if search is active
      loadKBContents(currentKB.id, stableRoute.folderId);
    }
  }, [page, rowsPerPage, viewMode,currentKB,loadKBContents,searchQuery,stableRoute.folderId]); // Removed dependencies that cause duplicate calls

  // ===== PERMISSION LOADING =====
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

  // ===== NAVIGATION HANDLERS =====
  const navigateToKB = useCallback(
    (kb: KnowledgeBase) => {
      if (currentKBRef.current?.id === kb.id) return;

      currentKBRef.current = kb;
      setCurrentKB(kb);
      setNavigationPath([{ id: kb.id, name: kb.name, type: 'kb' }]);
      navigate({ view: 'knowledge-base', kbId: kb.id });

      // Reset states for new KB
      setItems([]);
      setPage(0);
      setSearchQuery('');
      isViewInitiallyLoading.current = true;

      // Load contents with delay to prevent race conditions
      setTimeout(() => {
        if (!loadingRef.current) {
          loadKBContents(kb.id);
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

      // Reset states for new folder
      setItems([]);
      setPage(0);
      setSearchQuery('');
      isViewInitiallyLoading.current = true;

      setTimeout(() => {
        if (!loadingRef.current) {
          loadKBContents(currentKB.id, folder.id);
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
    setItems([]);
    setSearchQuery('');
    isViewInitiallyLoading.current = true;
  }, [navigate]);

  const navigateBack = useCallback(() => {
    if (navigationPath.length <= 1) {
      navigateToDashboard();
    } else {
      const newPath = navigationPath.slice(0, -1);
      setNavigationPath(newPath);
      const parent = newPath[newPath.length - 1];

      setItems([]);
      setPage(0);
      setSearchQuery('');
      isViewInitiallyLoading.current = true;

      if (parent.type === 'kb') {
        navigate({ view: 'knowledge-base', kbId: parent.id });
        setTimeout(() => {
          if (!loadingRef.current) {
            loadKBContents(parent.id);
          }
        }, 50);
      } else {
        navigate({ view: 'folder', kbId: currentKB!.id, folderId: parent.id });
        setTimeout(() => {
          if (!loadingRef.current) {
            loadKBContents(currentKB!.id, parent.id);
          }
        }, 50);
      }
    }
  }, [navigationPath, navigateToDashboard, currentKB, navigate, loadKBContents]);

  // ===== ROUTE INITIALIZATION =====
  // Fixed: Single effect for route initialization with proper deduplication
  useEffect(() => {
    if (!isInitialized) return;

    const initializeFromRoute = async () => {
      if (loadingRef.current) return;

      const routeId = `${stableRoute.view}-${stableRoute.kbId || ''}-${stableRoute.folderId || ''}`;
      if (lastLoadParams.current === routeId) return;

      lastLoadParams.current = routeId;
      isViewInitiallyLoading.current = true;

      if (stableRoute.view === 'dashboard') {
        loadKnowledgeBases();
      } else if (stableRoute.view === 'knowledge-base' && stableRoute.kbId) {
        if (currentKBRef.current?.id !== stableRoute.kbId) {
          try {
            const kb = await KnowledgeBaseAPI.getKnowledgeBase(stableRoute.kbId);
            currentKBRef.current = kb;
            setCurrentKB(kb);
            setNavigationPath([{ id: kb.id, name: kb.name, type: 'kb' }]);
            setItems([]);
            setSearchQuery('');

            setTimeout(() => {
              loadKBContents(stableRoute.kbId!);
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
            setItems([]);
            setSearchQuery('');

            setTimeout(() => {
              loadKBContents(stableRoute.kbId!, stableRoute.folderId);
            }, 50);
          } catch (err: any) {
            setError('Folder not found');
            navigate({ view: 'dashboard' });
          }
        }
      }
    };

    initializeFromRoute();
  }, [isInitialized, stableRoute.view, stableRoute.kbId, stableRoute.folderId,loadKBContents,loadKnowledgeBases,navigate]); // Stable dependencies only

  // ===== CRUD HANDLERS =====
  const handleCreateKB = async (name: string) => {
    setPageLoading(true);
    try {
      const newKB = await KnowledgeBaseAPI.createKnowledgeBase(name);
      setKnowledgeBases((prev) => [...prev, newKB]);
      setAllKBs((prev) => [...prev, newKB]); // Update both lists
      setSuccess('Knowledge base created successfully');
      setCreateKBDialog(false);
    } catch (err: any) {
      setError(err.message || 'Failed to create knowledge base');
    } finally {
      setPageLoading(false);
    }
  };

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

  // ===== EDIT HANDLERS =====
  const handleEditKB = async (name: string) => {
    if (!itemToEdit) return;

    setPageLoading(true);
    try {
      await KnowledgeBaseAPI.updateKnowledgeBase(itemToEdit.id, name);

      // Update knowledge bases list
      setKnowledgeBases((prev) =>
        prev.map((kb) => (kb.id === itemToEdit.id ? { ...kb, name } : kb))
      );

      // Update allKBs list
      setAllKBs((prev) => prev.map((kb) => (kb.id === itemToEdit.id ? { ...kb, name } : kb)));

      // Update current KB if it's the one being edited
      if (currentKB?.id === itemToEdit.id) {
        setCurrentKB((prev) => (prev ? { ...prev, name } : prev));
        setNavigationPath((prev) =>
          prev.map((item) => (item.id === itemToEdit.id ? { ...item, name } : item))
        );
      }

      setSuccess('Knowledge base updated successfully');
      setEditKBDialog(false);
      setItemToEdit(null);
    } catch (err: any) {
      setError(err.message || 'Failed to update knowledge base');
    } finally {
      setPageLoading(false);
    }
  };

  const handleEditFolder = async (name: string) => {
    if (!itemToEdit || !currentKB) return;

    setPageLoading(true);
    try {
      await KnowledgeBaseAPI.updateFolder(currentKB.id, itemToEdit.id, name);

      // Update items list
      setItems((prev) =>
        prev.map((item) => (item.id === itemToEdit.id ? { ...item, name } : item))
      );

      // Update navigation path if it's the current folder
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

  const handleUploadSuccess = useCallback(async () => {
    if (!currentKB) return;

    try {
      setSuccess('Successfully uploaded file(s)');
      setUploadDialog(false);

      // Reload contents without race conditions
      setTimeout(() => {
        if (!loadingRef.current) {
          loadKBContents(currentKB.id, stableRoute.folderId);
        }
      }, 100);
    } catch (err: any) {
      setError(err.message || 'Upload failed');
    }
  }, [stableRoute.folderId, loadKBContents,currentKB]);

  // ===== DELETE HANDLER =====
  const handleDelete = async () => {
    if (!itemToDelete) return;

    setPageLoading(true);
    try {
      if (itemToDelete.type === 'kb') {
        await KnowledgeBaseAPI.deleteKnowledgeBase(itemToDelete.id);
        setKnowledgeBases((prev) => prev.filter((kb) => kb.id !== itemToDelete.id));
        setAllKBs((prev) => prev.filter((kb) => kb.id !== itemToDelete.id));
        setSuccess('Knowledge base deleted successfully');
        if (currentKB?.id === itemToDelete.id) {
          navigateToDashboard();
        }
      } else if (itemToDelete.type === 'folder') {
        if (!currentKB) {
          setError('No knowledge base selected');
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

  // ===== PERMISSION HANDLERS =====
  const handleCreatePermissions = async (data: CreatePermissionRequest) => {
    if (!currentKB) return;
    await KnowledgeBaseAPI.createKBPermissions(currentKB.id, data);
  };

  const handleUpdatePermission = async (userId: string, data: UpdatePermissionRequest) => {
    if (!currentKB) return;
    await KnowledgeBaseAPI.updateKBPermission(currentKB.id, userId, data);
  };

  const handleRemovePermission = async (userId: string) => {
    if (!currentKB) return;
    await KnowledgeBaseAPI.removeKBPermission(currentKB.id, userId);
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

  // ===== MENU HANDLERS =====
  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, item: MenuItemWithAction) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
    setContextItem(item);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setContextItem(null);
  };

  const handleDashboardEdit = useCallback((kb: KnowledgeBase) => {
    setItemToEdit({ ...kb, type: 'kb' });
    setEditKBDialog(true);
  }, []);

  const handleDashboardDelete = useCallback((kb: KnowledgeBase) => {
    setItemToDelete({ ...kb, type: 'kb' });
    setDeleteDialog(true);
  }, []);

  const handleCreateKBDialog = useCallback((open: boolean) => {
    setCreateKBDialog(open);
  }, []);

  const handleEditMenuAction = () => {
    if (!contextItem) return;

    setItemToEdit(contextItem);
    if (contextItem.type === 'kb') {
      setEditKBDialog(true);
    } else if (contextItem.type === 'folder') {
      setEditFolderDialog(true);
    }
    handleMenuClose();
  };

  const handleDeleteMenuAction = () => {
    if (!contextItem) return;

    setItemToDelete(contextItem);
    setDeleteDialog(true);
    handleMenuClose();
  };

  // ===== BREADCRUMB NAVIGATION =====
  const navigateToPathIndex = useCallback(
    (index: number) => {
      if (index === 0 && navigationPath[0]?.type === 'kb') {
        const kb = navigationPath[0];
        setNavigationPath([kb]);
        navigate({ view: 'knowledge-base', kbId: kb.id });

        setItems([]);
        setPage(0);
        setSearchQuery('');
        isViewInitiallyLoading.current = true;

        setTimeout(() => {
          if (!loadingRef.current) {
            loadKBContents(kb.id);
          }
        }, 50);
      } else if (index > 0) {
        const newPath = navigationPath.slice(0, index + 1);
        setNavigationPath(newPath);
        const target = newPath[index];
        navigate({ view: 'folder', kbId: currentKB!.id, folderId: target.id });

        setItems([]);
        setPage(0);
        setSearchQuery('');
        isViewInitiallyLoading.current = true;

        setTimeout(() => {
          if (!loadingRef.current) {
            loadKBContents(currentKB!.id, target.id);
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
        setTimeout(() => {
          if (!loadingRef.current) {
            loadKBContents(currentKB.id, stableRoute.folderId);
          }
        }, 50);
      }
    }
  };

  // Fixed refresh button handler
  const handleRefresh = useCallback(() => {
    if (!currentKB) return;

    // Clear the last load params to force a fresh reload
    lastLoadParams.current = '';

    // Force reload with current parameters
    loadKBContents(currentKB.id, stableRoute.folderId, true, true);
  }, [stableRoute.folderId, loadKBContents,currentKB]);

  // ===== RENDER FUNCTIONS =====
  const renderKBDetail = () => (
    <Fade in timeout={300}>
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        {/* Compact Modern Toolbar */}
        <ModernToolbar
          elevation={0}
          sx={{
            borderBottom: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            backgroundColor: 'background.paper',
            px: { xs: 2, sm: 3 },
            py: 1,
            minHeight: 'auto',
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={2}
            sx={{
              width: '100%',
              minHeight: 48,
            }}
          >
            {/* Back Button */}
            <Box
              component="button"
              onClick={navigateBack}
              sx={{
                width: 32,
                height: 32,
                borderRadius: 1,
                border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                backgroundColor: 'transparent',
                color: 'text.secondary',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                flexShrink: 0,
                '&:hover': {
                  borderColor: 'action.active',
                  color: 'text.primary',
                  backgroundColor: 'action.hover',
                },
              }}
            >
              <Icon icon={arrowLeftIcon} fontSize={16} />
            </Box>

            {/* Breadcrumbs */}
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>{renderSmartBreadcrumbs()}</Box>
              
            {/* View Toggle */}
            <ToggleButtonGroup
              value={viewMode}
              exclusive
              onChange={handleViewChange}
              size="small"
              sx={{
                flexShrink: 0,
                '& .MuiToggleButton-root': {
                  border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                  borderRadius: 1,
                  px: 1.5,
                  py: 0.5,
                  minWidth: 36,
                  height: 32,
                  color: 'text.secondary',
                  '&.Mui-selected': {
                    backgroundColor: 'action.selected',
                    color: 'text.primary',
                    borderColor: 'divider',
                  },
                  '&:hover': {
                    backgroundColor: 'action.hover',
                  },
                },
              }}
            >
              <ToggleButton value="list">
                <Icon icon={viewListIcon} fontSize={16} />
              </ToggleButton>
              <ToggleButton value="grid">
                <Icon icon={viewGridIcon} fontSize={16} />
              </ToggleButton>
            </ToggleButtonGroup>

            {/* Action Buttons */}
            <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
              {currentUserPermission?.canCreateFolders && (
                <ActionButton
                  variant="outlined"
                  size="small"
                  startIcon={
                    <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>
                      <Icon icon={folderPlusIcon} fontSize={14} />
                    </Box>
                  }
                  onClick={() => setCreateFolderDialog(true)}
                  sx={{
                    height: 32,
                    px: { xs: 1, sm: 1.5 },
                    borderRadius: 1,
                    borderColor: 'divider',
                    color: 'text.secondary',
                    backgroundColor: 'transparent',
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    textTransform: 'none',
                    minWidth: { xs: 'auto', sm: 'auto' },
                    '&:hover': {
                      borderColor: 'action.active',
                      backgroundColor: 'action.hover',
                      color: 'text.primary',
                    },
                  }}
                >
                  <Box sx={{ display: { xs: 'inline', sm: 'none' } }}>
                    <Icon icon={folderPlusIcon} fontSize={16} />
                  </Box>
                  <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>New Folder</Box>
                </ActionButton>
              )}

              {currentUserPermission?.canUpload && (
                <ActionButton
                  variant="outlined"
                  size="small"
                  startIcon={
                    <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>
                      <Icon icon={fileUploadIcon} fontSize={14} />
                    </Box>
                  }
                  onClick={() => setUploadDialog(true)}
                  sx={{
                    height: 32,
                    px: { xs: 1, sm: 1.5 },
                    borderRadius: 1,
                    borderColor: 'primary.main',
                    color: 'primary.main',
                    backgroundColor: 'transparent',
                    fontSize: '0.8125rem',
                    fontWeight: 500,
                    textTransform: 'none',
                    minWidth: { xs: 'auto', sm: 'auto' },
                    '&:hover': {
                      backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                    },
                  }}
                >
                  <Box sx={{ display: { xs: 'inline', sm: 'none' } }}>
                    <Icon icon={fileUploadIcon} fontSize={16} />
                  </Box>
                  <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>Upload</Box>
                </ActionButton>
              )}

              {/* Utility Buttons */}
              <Box
                component="button"
                onClick={openPermissionsDialog}
                sx={{
                  width: 32,
                  height: 32,
                  borderRadius: 1,
                  border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                  backgroundColor: 'transparent',
                  color: 'text.secondary',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    borderColor: 'action.active',
                    color: 'text.primary',
                    backgroundColor: 'action.hover',
                  },
                }}
              >
                <Icon icon={accountMultipleIcon} fontSize={16} />
              </Box>

              <Box
                component="button"
                onClick={handleRefresh}
                sx={{
                  width: 32,
                  height: 32,
                  borderRadius: 1,
                  border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
                  backgroundColor: 'transparent',
                  color: 'text.secondary',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    borderColor: 'action.active',
                    color: 'text.primary',
                    backgroundColor: 'action.hover',
                  },
                }}
              >
                <Icon icon={refreshIcon} fontSize={16} />
              </Box>
            </Stack>
          </Stack>
        </ModernToolbar>

        {/* Content Area */}
        <Box sx={{ flexGrow: 1, overflow: 'auto', p: { xs: 1.5, sm: 2, md: 3 } }}>
          {items.length === 0 && !pageLoading ? (
            <Fade in>
              <Box
                sx={{
                  textAlign: 'center',
                  py: { xs: 4, sm: 6, md: 8 },
                  color: 'text.secondary',
                  px: 2,
                }}
              >
                <Icon
                  icon={folderIcon}
                  style={{
                    fontSize: 48,
                    marginBottom: 16,
                    opacity: 0.3,
                    color: theme.palette.text.secondary,
                  }}
                />
                <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
                  This location is empty
                </Typography>
                <Typography variant="body2" sx={{ mb: 3 }}>
                  Upload files or create folders to get started
                </Typography>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={2}
                  justifyContent="center"
                  sx={{ maxWidth: 300, mx: 'auto' }}
                >
                  <ActionButton
                    variant="outlined"
                    startIcon={<Icon icon={fileUploadIcon} fontSize={14} />}
                    onClick={() => setUploadDialog(true)}
                    size="small"
                    sx={{
                      width: { xs: '100%', sm: 'auto' },
                      borderColor: 'primary.main',
                      color: 'primary.main',
                      '&:hover': {
                        backgroundColor: (themeVal) => alpha(themeVal.palette.primary.main, 0.05),
                      },
                    }}
                  >
                    Upload Files
                  </ActionButton>
                  <ActionButton
                    variant="outlined"
                    startIcon={<Icon icon={folderPlusIcon} fontSize={14} />}
                    onClick={() => setCreateFolderDialog(true)}
                    size="small"
                    sx={{
                      width: { xs: '100%', sm: 'auto' },
                    }}
                  >
                    Create Folder
                  </ActionButton>
                </Stack>
              </Box>
            </Fade>
          ) : viewMode === 'grid' ? (
            <GridView
              items={items}
              pageLoading={pageLoading}
              navigateToFolder={navigateToFolder}
              handleMenuOpen={handleMenuOpen}
              CompactCard={CompactCard}
              CompactIconButton={CompactIconButton}
              totalCount={totalCount}
              hasMore={hasMore}
              loadingMore={loadingMore}
              onLoadMore={handleLoadMore}
            />
          ) : (
            <ListView
              items={items}
              pageLoading={pageLoading}
              navigateToFolder={navigateToFolder}
              handleMenuOpen={handleMenuOpen}
              totalCount={totalCount}
              rowsPerPage={rowsPerPage}
              page={page}
              setPage={setPage}
              setRowsPerPage={setRowsPerPage}
              currentKB={currentKB}
              loadKBContents={loadKBContents}
              route={stableRoute}
              CompactIconButton={CompactIconButton}
            />
          )}
        </Box>
      </Box>
    </Fade>
  );

  // ===== BREADCRUMB COMPONENTS =====
  const renderSmartBreadcrumbs = () => {
    const maxVisibleItems = 4;
    const pathLength = navigationPath.length;

    if (pathLength <= maxVisibleItems) {
      return (
        <Breadcrumbs
          separator={
            <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
          }
          sx={{
            '& .MuiBreadcrumbs-separator': {
              mx: 0.5,
            },
            '& .MuiBreadcrumbs-ol': {
              flexWrap: 'nowrap',
              overflow: 'hidden',
            },
          }}
        >
          <CompactBreadcrumb component="button" onClick={navigateToDashboard} isHome>
            <Icon icon={homeIcon} fontSize={14} />
            <Typography
              sx={{ display: { xs: 'none', sm: 'inline' }, ml: 0.5, fontSize: '0.8125rem' }}
            >
              Home
            </Typography>
          </CompactBreadcrumb>
          {navigationPath.map((item, index) => (
            <CompactBreadcrumb
              key={item.id}
              component="button"
              onClick={() => navigateToPathIndex(index)}
              isLast={index === pathLength - 1}
            >
              <Icon icon={item.type === 'kb' ? brainIcon : folderIcon} fontSize={14} />
              <Typography
                sx={{
                  ml: 0.5,
                  fontSize: '0.8125rem',
                  maxWidth: { xs: 80, sm: 120 },
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {item.name}
              </Typography>
            </CompactBreadcrumb>
          ))}
        </Breadcrumbs>
      );
    }

    // Condensed version for deep nesting
    const currentItem = navigationPath[pathLength - 1];
    const parentItem = pathLength > 1 ? navigationPath[pathLength - 2] : null;
    const kbItem = navigationPath[0];

    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ minWidth: 0, overflow: 'hidden' }}
      >
        <CompactBreadcrumb component="button" onClick={navigateToDashboard} isHome>
          <Icon icon={homeIcon} fontSize={14} />
          <Typography
            sx={{ display: { xs: 'none', sm: 'inline' }, ml: 0.5, fontSize: '0.8125rem' }}
          >
            Home
          </Typography>
        </CompactBreadcrumb>

        <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />

        <CompactBreadcrumb component="button" onClick={() => navigateToPathIndex(0)}>
          <Icon icon={brainIcon} fontSize={14} />
          <Typography
            sx={{
              ml: 0.5,
              fontSize: '0.8125rem',
              maxWidth: { xs: 60, sm: 100 },
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {kbItem.name}
          </Typography>
        </CompactBreadcrumb>

        {pathLength > 2 && (
          <>
            <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
            <SimpleBreadcrumbDropdown
              hiddenItems={navigationPath.slice(1, pathLength - 1)}
              onItemClick={navigateToPathIndex}
            />
          </>
        )}

        {parentItem && pathLength > 1 && (
          <>
            <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
            <CompactBreadcrumb
              component="button"
              onClick={() => navigateToPathIndex(pathLength - 2)}
            >
              <Icon icon={folderIcon} fontSize={14} />
              <Typography
                sx={{
                  ml: 0.5,
                  fontSize: '0.8125rem',
                  maxWidth: { xs: 50, sm: 80 },
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {parentItem.name}
              </Typography>
            </CompactBreadcrumb>
          </>
        )}

        <Icon icon={chevronRightIcon} fontSize={14} color={theme.palette.text.disabled} />
        <CompactBreadcrumb component="span" isLast>
          <Icon icon={folderIcon} fontSize={14} />
          <Typography
            sx={{
              ml: 0.5,
              fontSize: '0.8125rem',
              maxWidth: { xs: 60, sm: 100 },
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {currentItem.name}
          </Typography>
        </CompactBreadcrumb>
      </Stack>
    );
  };

  const CompactBreadcrumb = styled('div')<{
    isHome?: boolean;
    isLast?: boolean;
    component?: string;
  }>(({ theme: muiTheme, isHome, isLast }) => ({
    display: 'flex',
    alignItems: 'center',
    padding: '4px 6px',
    borderRadius: 4,
    fontSize: '0.8125rem',
    fontWeight: 500,
    color: isLast ? muiTheme.palette.text.primary : muiTheme.palette.text.secondary,
    backgroundColor: 'transparent',
    border: 'none',
    cursor: isLast ? 'default' : 'pointer',
    transition: 'all 0.2s ease',
    minWidth: 0,

    ...(!isLast && {
      '&:hover': {
        backgroundColor: muiTheme.palette.action.hover,
        color: muiTheme.palette.text.primary,
      },
    }),
  }));

  const SimpleBreadcrumbDropdown = ({
    hiddenItems,
    onItemClick,
  }: {
    hiddenItems: Array<{ id: string; name: string; type: 'kb' | 'folder' }>;
    onItemClick: (index: number) => void;
  }) => {
    const [dropdownAnchor, setDropdownAnchor] = useState<null | HTMLElement>(null);

    return (
      <>
        <Box
          component="button"
          onClick={(e) => setDropdownAnchor(e.currentTarget)}
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minWidth: 24,
            height: 24,
            borderRadius: 0.5,
            backgroundColor: 'transparent',
            border: 'none',
            color: 'text.secondary',
            cursor: 'pointer',
            fontSize: '0.8125rem',
            fontWeight: 600,
            '&:hover': {
              backgroundColor: 'action.hover',
              color: 'text.primary',
            },
          }}
        >
          ...
        </Box>

        <Menu
          anchorEl={dropdownAnchor}
          open={Boolean(dropdownAnchor)}
          onClose={() => setDropdownAnchor(null)}
          PaperProps={{
            sx: {
              borderRadius: 1.5,
              minWidth: 180,
              border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
              boxShadow: (themeVal) => themeVal.shadows[3],
              mt: 0.5,
            },
          }}
        >
          {hiddenItems.map((item, relativeIndex) => {
            const actualIndex = relativeIndex + 1;
            return (
              <MenuItem
                key={item.id}
                onClick={() => {
                  onItemClick(actualIndex);
                  setDropdownAnchor(null);
                }}
                sx={{
                  fontSize: '0.8125rem',
                  py: 1,
                  px: 1.5,
                  minHeight: 'auto',
                }}
              >
                <ListItemIcon sx={{ minWidth: 28 }}>
                  <Icon icon={item.type === 'kb' ? brainIcon : folderIcon} fontSize={16} />
                </ListItemIcon>
                <ListItemText
                  primary={item.name}
                  sx={{
                    '& .MuiListItemText-primary': {
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                    },
                  }}
                />
              </MenuItem>
            );
          })}
        </Menu>
      </>
    );
  };

  // ===== RETRY INDEXING =====
  const handleRetryIndexing = async (recordId: string) => {
    if (!currentKB) {
      setError('No KB id found, please refresh');
      return;
    }
    try {
      const response = await KnowledgeBaseAPI.reindexRecord(recordId);
      if (response.success) {
        setSuccess('File indexing started successfully');
      } else {
        setError('Failed to start reindexing');
      }
      handleMenuClose();
      setTimeout(() => {
        if (!loadingRef.current) {
          loadKBContents(currentKB.id, stableRoute.folderId);
        }
      }, 100);
    } catch (err: any) {
      console.error('Failed to reindexing document', err);
    }
  };

  // ===== CONTEXT MENU =====
  const renderContextMenu = () => {
    const canModify =
      currentUserPermission?.role === 'OWNER' || currentUserPermission?.role === 'WRITER';

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
          handleMenuClose();
        },
      },
      ...(canModify
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
      return `Are you sure you want to delete ${name}? This will permanently delete the knowledge base and all its contents. This action cannot be undone.`;
    }
    if (itemToDelete.type === 'folder') {
      return `Are you sure you want to delete ${name}? This will permanently delete the folder and all its contents. This action cannot be undone.`;
    }
    return `Are you sure you want to delete ${name}? This action cannot be undone.`;
  };

  // ===== LOADING STATE =====
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

  // ===== MAIN RENDER =====
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
        {stableRoute.view === 'all-records' ? (
          <AllRecordsView
            onNavigateBack={navigateToDashboard}
            onNavigateToRecord={(recordId) => {
              window.open(`/record/${recordId}`, '_blank', 'noopener,noreferrer');
            }}
          />
        ) : stableRoute.view === 'dashboard' ? (
          <DashboardComponent
            theme={theme}
            navigateToKB={navigateToKB}
            onEdit={handleDashboardEdit}
            onDelete={handleDashboardDelete}
            setCreateKBDialog={handleCreateKBDialog}
            CompactCard={CompactCard}
            ActionButton={ActionButton}
            isInitialized={isInitialized}
            navigate={navigate}
          />
        ) : (
          renderKBDetail()
        )}
      </ContentArea>

      {/* ===== DIALOGS ===== */}
      <CreateKnowledgeBaseDialog
        open={createKBDialog}
        onClose={() => setCreateKBDialog(false)}
        onSubmit={handleCreateKB}
        loading={pageLoading}
      />

      <CreateFolderDialog
        open={createFolderDialog}
        onClose={() => setCreateFolderDialog(false)}
        onSubmit={handleCreateFolder}
        loading={pageLoading}
      />

      <EditKnowledgeBaseDialog
        open={editKBDialog}
        onClose={() => {
          setEditKBDialog(false);
          setItemToEdit(null);
        }}
        onSubmit={handleEditKB}
        currentName={itemToEdit?.name || ''}
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
        onClose={() => setUploadDialog(false)}
        knowledgeBaseId={currentKB?.id}
        folderId={stableRoute.folderId}
        onUploadSuccess={handleUploadSuccess}
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

      <ManagePermissionsDialog
        open={permissionsDialog}
        onClose={() => setPermissionsDialog(false)}
        kbId={currentKB?.id || ''}
        kbName={currentKB?.name || ''}
        permissions={permissions}
        onCreatePermissions={handleCreatePermissions}
        onUpdatePermission={handleUpdatePermission}
        onRemovePermission={handleRemovePermission}
        onRefresh={handleRefreshPermissions}
        loading={permissionsLoading}
      />

      {/* Context Menu */}
      {renderContextMenu()}

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
