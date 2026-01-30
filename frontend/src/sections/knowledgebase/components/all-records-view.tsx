// components/all-records-view.tsx - Hierarchical All Records View
import type { GridColDef, GridRowParams } from '@mui/x-data-grid';

import { Icon } from '@iconify/react';
import clearIcon from '@iconify-icons/mdi/close';
import searchIcon from '@iconify-icons/mdi/magnify';
import refreshIcon from '@iconify-icons/mdi/refresh';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import databaseIcon from '@iconify-icons/mdi/database';
import dotsIcon from '@iconify-icons/mdi/dots-vertical';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import chevronRightIcon from '@iconify-icons/mdi/chevron-right';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import homeIcon from '@iconify-icons/mdi/home';
import languageCIcon from '@iconify-icons/vscode-icons/file-type-c3';
import filePdfBoxIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import languagePhpIcon from '@iconify-icons/vscode-icons/file-type-php3';
import downloadIcon from '@iconify-icons/mdi/download-outline';
import fileWordBoxIcon from '@iconify-icons/vscode-icons/file-type-word';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';
import languageCss3Icon from '@iconify-icons/vscode-icons/file-type-css';
import languageJavaIcon from '@iconify-icons/vscode-icons/file-type-java';
import languageRubyIcon from '@iconify-icons/vscode-icons/file-type-ruby';
import emailOutlineIcon from '@iconify-icons/vscode-icons/file-type-outlook';
import fileExcelBoxIcon from '@iconify-icons/vscode-icons/file-type-excel';
import fileImageBoxIcon from '@iconify-icons/vscode-icons/file-type-image';
import languageHtml5Icon from '@iconify-icons/vscode-icons/file-type-html';
import fileArchiveBoxIcon from '@iconify-icons/vscode-icons/file-type-zip2';
import languagePythonIcon from '@iconify-icons/vscode-icons/file-type-python';
import noteTextOutlineIcon from '@iconify-icons/vscode-icons/file-type-text';
import fileCodeOutlineIcon from '@iconify-icons/vscode-icons/file-type-source';
import cloudIcon from '@iconify-icons/mdi/cloud-outline';
import libraryIcon from '@iconify-icons/mdi/library';
import React, { useRef, useState, useEffect, useCallback } from 'react';
import languageMarkdownIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import fileMusicOutlineIcon from '@iconify-icons/vscode-icons/file-type-audio';
import fileVideoOutlineIcon from '@iconify-icons/vscode-icons/file-type-video';
import filePowerpointBoxIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import languageJavascriptIcon from '@iconify-icons/vscode-icons/file-type-js-official';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';
// Additional premium file type icons
import fileSvgIcon from '@iconify-icons/vscode-icons/file-type-svg';
import fileJsonIcon from '@iconify-icons/vscode-icons/file-type-json';
import fileYamlIcon from '@iconify-icons/vscode-icons/file-type-yaml';
import fileTypescriptIcon from '@iconify-icons/vscode-icons/file-type-typescript-official';
import fileGoIcon from '@iconify-icons/vscode-icons/file-type-go-gopher';
import fileSqlIcon from '@iconify-icons/vscode-icons/file-type-sql';
// Premium image icons
import imageIcon from '@iconify-icons/mdi/image';
import panoramaIcon from '@iconify-icons/mdi/panorama';
import gifIcon from '@iconify-icons/mdi/file-gif-box';
// Node type icons
import folderMultipleIcon from '@iconify-icons/mdi/folder-multiple';
import folderOpenIcon from '@iconify-icons/mdi/folder-open';
import appsIcon from '@iconify-icons/mdi/apps';

import { DataGrid } from '@mui/x-data-grid';
import {
  Box,
  Fade,
  Menu,
  Paper,
  Stack,
  Alert,
  alpha,
  Select,
  Tooltip,
  Divider,
  Snackbar,
  useTheme,
  MenuItem,
  TextField,
  Typography,
  IconButton,
  Pagination,
  Breadcrumbs,
  Link,
  Chip,
  ListItemIcon,
  ListItemText,
  InputAdornment,
  LinearProgress,
  CircularProgress,
  Skeleton,
  Dialog,
  Button,
} from '@mui/material';

import { KnowledgeBaseAPI } from '../services/api';
import DeleteRecordDialog from '../delete-record-dialog';
import DynamicFilterSidebar, { AppliedFilters, AvailableFilters } from './dynamic-filter-sidebar';
import { ORIGIN } from '../constants/knowledge-search';
import { getExtensionFromMimeType, getFileIcon, getFileIconColor } from '../utils/utils';

// New Props Interface - receives state from URL via parent
interface AllRecordsViewProps {
  // Current navigation state from URL
  nodeType?: string;
  nodeId?: string;
  page: number;
  limit: number;
  q?: string;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  filters: AppliedFilters;
  // Callbacks
  onUpdateUrl: (params: Record<string, string | undefined>) => void;
  onNavigateToRecord: (recordId: string) => void;
}

// Hub Node interface for hierarchical data
interface HubNode {
  id: string;
  name: string;
  nodeType: 'app' | 'kb' | 'folder' | 'record' | 'recordGroup';
  parentId: string | null;
  origin: 'KB' | 'CONNECTOR';
  connector: string;
  recordType?: string;
  indexingStatus?: string;
  hasChildren: boolean;
  createdAt?: number;
  updatedAt?: number;
  sizeInBytes?: number;
  mimeType?: string;
  extension?: string;
  webUrl?: string;
  externalRecordId?: string;
  permission?: {
    role: string;
    canEdit: boolean;
    canDelete: boolean;
  };
}

interface Breadcrumb {
  id: string;
  name: string;
  nodeType: string;
  subType?: string;
}

interface ActionMenuItem {
  label: string;
  icon: any;
  color: string;
  onClick: () => void;
  isDanger?: boolean;
}

// Styled components
const ModernToolbar = ({ theme, ...props }: any) => (
  <Box
    sx={{
      position: 'sticky',
      top: 0,
      zIndex: theme.zIndex.appBar,
      padding: theme.spacing(2, 3),
      borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
      backdropFilter: 'saturate(180%) blur(12px)',
      backgroundColor: alpha(theme.palette.background.paper, 0.8),
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      gap: 3,
      borderTopLeftRadius: 12,
      borderTopRightRadius: 12,
      boxShadow: `0 1px 3px ${alpha(theme.palette.common.black, 0.02)}`,
      minHeight: 64,
      transition: theme.transitions.create(['background-color', 'border-color', 'box-shadow'], {
        duration: 200,
      }),
    }}
    {...props}
  />
);

const CompactIconButton = ({ theme, ...props }: any) => (
  <IconButton
    sx={{
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
    }}
    {...props}
  />
);

const MainContentContainer = ({ theme, sidebarOpen, ...props }: any) => (
  <Box
    sx={{
      flexGrow: 1,
      minWidth: 0,
      transition: theme.transitions.create(['margin-left', 'width'], {
        easing: theme.transitions.easing.sharp,
        duration: theme.transitions.duration.leavingScreen,
      }),
      maxHeight: '100vh',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}
    {...props}
  />
);

// DataGrid Skeleton Loader
const DataGridSkeleton: React.FC<{ rowCount?: number }> = ({ rowCount = 10 }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  return (
    <Box
      sx={{
        flexGrow: 1,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header Skeleton */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          height: '56px',
          px: 2,
          borderBottom: '1px solid',
          borderColor: 'divider',
          backgroundColor: alpha('#000', 0.02),
        }}
      >
        <Skeleton variant="text" width={60} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width="20%" height={20} sx={{ mr: 'auto' }} />
        <Skeleton variant="text" width={100} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width={100} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width={120} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width={80} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width={120} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width={120} height={20} sx={{ mr: 3 }} />
        <Skeleton variant="text" width={60} height={20} />
      </Box>

      {/* Rows Skeleton */}
      <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
        {Array.from(new Array(rowCount)).map((_, index) => (
          <Box
            key={index}
            sx={{
              display: 'flex',
              alignItems: 'center',
              height: '56px',
              px: 2,
              ml: 1,
              borderBottom: '1px solid',
              borderColor: alpha('#000', 0.05),
            }}
          >
            {/* # column */}
            <Skeleton
              variant="text"
              width={40}
              height={16}
              sx={{
                mr: 2,
                bgcolor: alpha(theme.palette.text.disabled, isDark ? 0.08 : 0.06),
              }}
            />

            {/* Name column with icon */}
            <Box sx={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 200, mr: 2 }}>
              <Skeleton
                variant="circular"
                width={24}
                height={24}
                sx={{
                  mr: 1.5,
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                }}
              />
              <Skeleton
                variant="text"
                width="60%"
                height={16}
                sx={{ bgcolor: alpha(theme.palette.text.primary, isDark ? 0.1 : 0.08) }}
              />
            </Box>

            {/* Type column */}
            <Skeleton
              variant="rounded"
              width={100}
              height={24}
              sx={{
                mr: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.divider, 0.5),
              }}
            />

            {/* Status column */}
            <Skeleton
              variant="text"
              width={100}
              height={16}
              sx={{
                mr: 2,
                bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06),
              }}
            />

            {/* Source column */}
            <Box sx={{ display: 'flex', alignItems: 'center', width: 120, mr: 2 }}>
              <Skeleton
                variant="circular"
                width={18}
                height={18}
                sx={{
                  mr: 1,
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                }}
              />
              <Skeleton
                variant="text"
                width="60%"
                height={14}
                sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
              />
            </Box>

            {/* Size column */}
            <Skeleton
              variant="text"
              width={70}
              height={14}
              sx={{
                mr: 2,
                bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06),
              }}
            />

            {/* Created column */}
            <Box sx={{ width: 120, mr: 2 }}>
              <Skeleton
                variant="text"
                width="80%"
                height={14}
                sx={{
                  mb: 0.25,
                  bgcolor: alpha(theme.palette.text.primary, isDark ? 0.08 : 0.06),
                }}
              />
              <Skeleton
                variant="text"
                width="60%"
                height={12}
                sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
              />
            </Box>

            {/* Updated column */}
            <Box sx={{ width: 120, mr: 2 }}>
              <Skeleton
                variant="text"
                width="80%"
                height={14}
                sx={{
                  mb: 0.25,
                  bgcolor: alpha(theme.palette.text.primary, isDark ? 0.08 : 0.06),
                }}
              />
              <Skeleton
                variant="text"
                width="60%"
                height={12}
                sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
              />
            </Box>

            {/* Actions column */}
            <Skeleton
              variant="circular"
              width={32}
              height={32}
              sx={{ bgcolor: alpha(theme.palette.text.secondary, isDark ? 0.08 : 0.06) }}
            />
          </Box>
        ))}
      </Box>
    </Box>
  );
};

const AllRecordsView: React.FC<AllRecordsViewProps> = ({
  nodeType,
  nodeId,
  page,
  limit,
  q,
  sortBy,
  sortOrder,
  filters,
  onUpdateUrl,
  onNavigateToRecord,
}) => {
  const theme = useTheme();

  // Data state
  const [items, setItems] = useState<HubNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [totalCount, setTotalCount] = useState(0);
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([]);
  const [counts, setCounts] = useState<any>(null);
  const [availableFilters, setAvailableFilters] = useState<AvailableFilters>({});
  const [permissions, setPermissions] = useState<any>(null);
  const [refreshCounter, setRefreshCounter] = useState(0);

  // UI state
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [searchQueryLocal, setSearchQueryLocal] = useState(q || '');
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null);
  const [menuItems, setMenuItems] = useState<ActionMenuItem[]>([]);

  // Delete dialog state
  const [deleteDialogData, setDeleteDialogData] = useState({
    open: false,
    recordId: '',
    recordName: '',
  });

  // Force reindex dialog state
  const [forceReindexDialog, setForceReindexDialog] = useState({
    open: false,
    id: '',
    name: '',
    type: 'record' as 'record' | 'recordGroup',
  });

  // Snackbar state
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });

  // Sync search input with URL param
  useEffect(() => {
    setSearchQueryLocal(q || '');
  }, [q]);

  // Load data whenever any dependency changes
  useEffect(() => {
    // Clear items immediately to prevent showing stale data
    setItems([]);
    setTotalCount(0);
    setLoading(true);

    const fetchData = async () => {
      try {
        const params: any = {
          page,
          limit,
          include: 'counts,permissions,breadcrumbs,availableFilters',
          q: q || undefined,
        };

        // Add sort params if they exist
        if (sortBy) {
          params.sortBy = sortBy;
        }
        if (sortOrder) {
          params.sortOrder = sortOrder;
        }

        // Add filters if they exist and have values
        if (filters.recordTypes && filters.recordTypes.length > 0) {
          params.recordTypes = filters.recordTypes.join(',');
        }
        if (filters.origins && filters.origins.length > 0) {
          params.origins = filters.origins.join(',');
        }
        if (filters.connectorIds && filters.connectorIds.length > 0) {
          params.connectorIds = filters.connectorIds.join(',');
        }
        if (filters.kbIds && filters.kbIds.length > 0) {
          params.kbIds = filters.kbIds.join(',');
        }
        if (filters.indexingStatus && filters.indexingStatus.length > 0) {
          params.indexingStatus = filters.indexingStatus.join(',');
        }

        let data;
        if (!nodeType || !nodeId) {
          // Load root level nodes
          data = await KnowledgeBaseAPI.getKnowledgeHubNodes(params);
        } else {
          // Load specific node children
          data = await KnowledgeBaseAPI.getKnowledgeHubNodeChildren(nodeType, nodeId, params);
        }

        // Update state with fresh data from API
        setItems(data.items || []);
        setTotalCount(data.pagination?.totalItems || 0);
        setBreadcrumbs(data.breadcrumbs || []);
        setCounts(data.counts);
        setAvailableFilters(data.filters?.available || {});
        setPermissions(data.permissions || null);
      } catch (error) {
        console.error('Failed to load data:', error);
        setItems([]);
        setTotalCount(0);
        setSnackbar({
          open: true,
          message: 'Failed to load data. Please try again.',
          severity: 'error',
        });
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [nodeType, nodeId, page, limit, sortBy, sortOrder, q, filters, refreshCounter]);

  // Navigation handlers
  const handleRowClick = (node: HubNode) => {
    if (node.hasChildren) {
      // Navigate into node
      onUpdateUrl({
        nodeType: node.nodeType,
        nodeId: node.id,
        page: '1',
        limit: limit.toString(),
        sortBy,
        sortOrder,
        q: undefined,
         // Clear filters and search query
        recordTypes: undefined,
        origins: undefined,
        connectorIds: undefined,
        kbIds: undefined,
        indexingStatus: undefined,
      });
    } else if (node.nodeType === 'record') {
      onNavigateToRecord(node.id);
    }
  };

  const handleBreadcrumbClick = (breadcrumb: Breadcrumb | null, index: number) => {
    if (index === 0 || breadcrumb === null) {
      // Navigate to root
      onUpdateUrl({
        page: '1',
        limit: limit.toString(),
        sortBy,
        sortOrder,
        q: q || undefined,
        // Keep filters, remove nodeType/nodeId
        recordTypes: filters.recordTypes?.join(',') || undefined,
        origins: filters.origins?.join(',') || undefined,
        connectorIds: filters.connectorIds?.join(',') || undefined,
        kbIds: filters.kbIds?.join(',') || undefined,
        indexingStatus: filters.indexingStatus?.join(',') || undefined,
      });
    } else {
      // Navigate to specific breadcrumb
      onUpdateUrl({
        nodeType: breadcrumb.nodeType,
        nodeId: breadcrumb.id,
        page: '1',
        limit: limit.toString(),
        sortBy,
        sortOrder,
        q: q || undefined,
        // Keep filters
        recordTypes: filters.recordTypes?.join(',') || undefined,
        origins: filters.origins?.join(',') || undefined,
        connectorIds: filters.connectorIds?.join(',') || undefined,
        kbIds: filters.kbIds?.join(',') || undefined,
        indexingStatus: filters.indexingStatus?.join(',') || undefined,
      });
    }
  };

  const handlePageChange = (event: unknown, newPage: number) => {
    onUpdateUrl({
      nodeType,
      nodeId,
      page: newPage.toString(), // 1-indexed page
      limit: limit.toString(),
      sortBy,
      sortOrder,
      q: q || undefined,
      recordTypes: filters.recordTypes?.join(',') || undefined,
      origins: filters.origins?.join(',') || undefined,
      connectorIds: filters.connectorIds?.join(',') || undefined,
      kbIds: filters.kbIds?.join(',') || undefined,
      indexingStatus: filters.indexingStatus?.join(',') || undefined,
    });
  };

  const handleLimitChange = (event: any) => {
    onUpdateUrl({
      nodeType,
      nodeId,
      page: '1', // Reset to page 1 when changing limit
      limit: event.target.value,
      sortBy,
      sortOrder,
      q: q || undefined,
      recordTypes: filters.recordTypes?.join(',') || undefined,
      origins: filters.origins?.join(',') || undefined,
      connectorIds: filters.connectorIds?.join(',') || undefined,
      kbIds: filters.kbIds?.join(',') || undefined,
      indexingStatus: filters.indexingStatus?.join(',') || undefined,
    });
  };

  const handleSearchSubmit = () => {
    onUpdateUrl({
      nodeType,
      nodeId,
      page: '1',
      limit: limit.toString(),
      sortBy,
      sortOrder,
      q: searchQueryLocal || undefined,
      recordTypes: filters.recordTypes?.join(',') || undefined,
      origins: filters.origins?.join(',') || undefined,
      connectorIds: filters.connectorIds?.join(',') || undefined,
      kbIds: filters.kbIds?.join(',') || undefined,
      indexingStatus: filters.indexingStatus?.join(',') || undefined,
    });
  };

  const handleClearSearch = () => {
    setSearchQueryLocal('');
    onUpdateUrl({
      nodeType,
      nodeId,
      page: '1',
      limit: limit.toString(),
      sortBy,
      sortOrder,
      q: undefined, // Explicitly clear the search query param
      recordTypes: filters.recordTypes?.join(',') || undefined,
      origins: filters.origins?.join(',') || undefined,
      connectorIds: filters.connectorIds?.join(',') || undefined,
      kbIds: filters.kbIds?.join(',') || undefined,
      indexingStatus: filters.indexingStatus?.join(',') || undefined,
    });
  };

  const handleRefresh = () => {
    setRefreshCounter((prev) => prev + 1);
  };

  const handleFilterChange = (newFilters: AppliedFilters) => {
    onUpdateUrl({
      nodeType,
      nodeId,
      page: '1', // Reset to page 1 when filters change
      limit: limit.toString(),
      sortBy: newFilters.sortBy || undefined,
      sortOrder: newFilters.sortOrder || undefined,
      q: q || undefined,
      recordTypes: newFilters.recordTypes?.join(',') || undefined,
      origins: newFilters.origins?.join(',') || undefined,
      connectorIds: newFilters.connectorIds?.join(',') || undefined,
      kbIds: newFilters.kbIds?.join(',') || undefined,
      indexingStatus: newFilters.indexingStatus?.join(',') || undefined,
    });
  };

  // Helper functions
  const getConnectorIconPath = (connectorType?: string): string => {
    if (!connectorType) return '/assets/icons/connectors/default.svg';

    return `/assets/icons/connectors/${connectorType.replace(' ', '').toLowerCase()}.svg`;
  };

  // Get MDI icon and color for node types (kb, folder, recordGroup)
  const getNodeTypeIcon = (type: string, hasChildren: boolean): { icon: any; color: string } => {
    switch (type) {
      case 'kb':
        return { icon: folderMultipleIcon, color: theme.palette.success.main };
      case 'folder':
        return hasChildren
          ? { icon: folderOpenIcon, color: theme.palette.warning.main }
          : { icon: folderIcon, color: theme.palette.text.secondary };
      case 'recordGroup':
        return { icon: folderOpenIcon, color: theme.palette.info.main };
      case 'app':
        return { icon: appsIcon, color: theme.palette.primary.main };
      default:
        return { icon: folderIcon, color: theme.palette.text.secondary };
    }
  };

  const getNodeIcon = (node: HubNode) => {
    if (node.hasChildren) {
      if (node.nodeType === 'app') return cloudIcon;
      if (node.nodeType === 'kb') return libraryIcon;
      if (node.nodeType === 'folder' || node.nodeType === 'recordGroup') return folderIcon;
    }

    // For records, use file type icon
    return getFileIcon(node.extension || '', node.mimeType);
  };


  const formatFileSize = (bytes: number): string => {
    if (!bytes || bytes === 0) return '—';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  // Action handlers
  const handleRetryIndexing = async (recordId: string) => {
    try {
      const response = await KnowledgeBaseAPI.reindexRecord(recordId);
      setSnackbar({
        open: true,
        message: response.success
          ? 'File indexing started successfully'
          : response.reason || 'Failed to start reindexing',
        severity: response.success ? 'success' : 'error',
      });
      setRefreshCounter((prev) => prev + 1);
    } catch (err: any) {
      console.error('Failed to reindexing document', err);
      setSnackbar({
        open: true,
        message: err.response?.data?.reason || err.message || 'Failed to start reindexing',
        severity: 'error',
      });
    }
  };

  const handleRetryIndexingFolder = async (folderId: string) => {
    try {
      const response = await KnowledgeBaseAPI.reindexRecord(folderId, false, 100);
      setSnackbar({
        open: true,
        message: response.success
          ? 'Folder indexing started successfully'
          : response.reason || 'Failed to start reindexing',
        severity: response.success ? 'success' : 'error',
      });
      setRefreshCounter((prev) => prev + 1);
    } catch (err: any) {
      console.error('Failed to reindex folder', err);
      setSnackbar({
        open: true,
        message: err.response?.data?.reason || err.message || 'Failed to start reindexing',
        severity: 'error',
      });
    }
  };

  const handleRetryIndexingRecordGroup = async (recordGroupId: string) => {
    try {
      const response = await KnowledgeBaseAPI.reindexRecordGroup(recordGroupId, false, 100);
      setSnackbar({
        open: true,
        message: response.success
          ? 'Record group indexing started successfully'
          : response.message || 'Failed to start reindexing',
        severity: response.success ? 'success' : 'error',
      });
      setRefreshCounter((prev) => prev + 1);
    } catch (err: any) {
      console.error('Failed to reindex record group', err);
      setSnackbar({
        open: true,
        message: err.response?.data?.message || err.message || 'Failed to start reindexing',
        severity: 'error',
      });
    }
  };

  const handleForceReindex = async () => {
    try {
      const { id, type } = forceReindexDialog;
      let response;
      if (type === 'record') {
        response = await KnowledgeBaseAPI.reindexRecord(id, true);
      } else {
        response = await KnowledgeBaseAPI.reindexRecordGroup(id, true);
      }
      setSnackbar({
        open: true,
        message: response.success
          ? `${type === 'record' ? 'Record' : 'Record group'} force reindex started successfully`
          : response.message || 'Failed to start force reindexing',
        severity: response.success ? 'success' : 'error',
      });
      setForceReindexDialog({ open: false, id: '', name: '', type: 'record' });
      setRefreshCounter((prev) => prev + 1);
    } catch (err: any) {
      console.error('Failed to force reindex', err);
      setSnackbar({
        open: true,
        message: err.response?.data?.message || err.message || 'Failed to start force reindexing',
        severity: 'error',
      });
    }
  };

  // Handle download document
  const handleDownload = async (recordId: string, recordName: string) => {
    try {
      await KnowledgeBaseAPI.handleDownloadDocument(recordId, recordName);
      setSnackbar({
        open: true,
        message: 'Download started successfully',
        severity: 'success',
      });
    } catch (err: any) {
      console.error('Failed to download document', err);
    }
  };

  const handleDeleteSuccess = () => {
    setSnackbar({
      open: true,
      message: 'Record deleted successfully',
      severity: 'success',
    });
    setRefreshCounter((prev) => prev + 1);
  };

  const closeActionMenu = () => {
    setMenuAnchorEl(null);
  };

  const showActionMenu = (anchorElement: HTMLElement, menuActions: ActionMenuItem[]) => {
    setMenuItems(menuActions);
    setMenuAnchorEl(anchorElement);
  };

  // DataGrid columns
  const columns: GridColDef<HubNode>[] = [
    {
      field: '#',
      headerName: '#',
      width: 60,
      align: 'center',
      headerAlign: 'center',
      sortable: false,
      renderCell: (params) => {
        const rowIndex = params.api.getRowIndexRelativeToVisibleRows(params.row.id);
        const rowNumber = (page - 1) * limit + rowIndex + 1;
        return (
          <Typography variant="caption" sx={{ color: 'text.disabled', fontWeight: 500 }}>
            {rowNumber}
          </Typography>
        );
      },
    },
    {
      field: 'name',
      headerName: 'Name',
      flex: 1,
      minWidth: 200,
      renderCell: (params) => {
        const node = params.row;
        const isNonClickable = !node.hasChildren && node.nodeType !== 'record';

        // Determine icon rendering strategy based on node type
        const isRecord = node.nodeType === 'record';
        const isApp = node.nodeType === 'app';
        const isNodeTypeWithIcon = node.nodeType === 'kb' || node.nodeType === 'folder' || node.nodeType === 'recordGroup';

        // For connectors (app), use SVG path
        const connectorIconPath = isApp ? getConnectorIconPath(node.connector) : null;

        // Get MDI icon and color for node types (kb, folder, recordGroup)
        const nodeTypeDisplay = isNodeTypeWithIcon ? getNodeTypeIcon(node.nodeType, node.hasChildren) : null;

        const content = (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              height: '100%',
              width: '100%',
              pl: 0.5,
            }}
          >
            {/* Use connector icon path for app nodes */}
            {connectorIconPath ? (
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  mr: 1.5,
                  flexShrink: 0,
                  width: 28,
                  height: 28,
                }}
              >
                <Box
                  component="img"
                  src={connectorIconPath}
                  alt={node.name}
                  sx={{
                    width: 26,
                    height: 26,
                    flexShrink: 0,
                  }}
                />
              </Box>
            ) : nodeTypeDisplay ? (
              /* Use MDI icons for kb, folder, recordGroup */
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  mr: 1.5,
                  flexShrink: 0,
                  width: 28,
                  height: 28,
                }}
              >
                <Icon
                  icon={nodeTypeDisplay.icon}
                  style={{
                    fontSize: '26px',
                    color: nodeTypeDisplay.color,
                    flexShrink: 0,
                  }}
                />
              </Box>
            ) : (
              /* For record nodes, use premium file type icons based on extension/mimeType */
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mr: 1.5,
                      flexShrink: 0,
                      width: 28,
                      height: 28,
                    }}
                  >
                    <Icon
                      icon={getFileIcon(node.extension || '', node.mimeType)}
                      style={{
                        fontSize: '28px',
                        flexShrink: 0,
                        color: getFileIconColor(node.extension || '', node.mimeType),
                      }}
                    />
                  </Box>
                )}
            <Typography
              variant="body2"
              noWrap
              sx={{
                fontWeight: node.hasChildren ? 600 : 400,
                fontSize: '0.875rem',
                color: 'text.primary',
              }}
            >
              {node.name}
            </Typography>
            {isNonClickable && (
              <Chip
                label="empty"
                size="small"
                sx={{
                  ml: 1,
                  height: 18,
                  fontSize: '0.65rem',
                  fontWeight: 500,
                  backgroundColor: theme.palette.mode === 'dark' ? alpha(theme.palette.divider, 0.4) : 'transparent',
                  border: `1px solid ${alpha(theme.palette.divider, 0.4)}`,
                  color: 'text.disabled',
                  '& .MuiChip-label': {
                    px: 0.75,
                    py: 0,
                  },
                }}
              />
            )}
          </Box>
        );

        return isNonClickable ? (
          <Tooltip title="This folder is empty" arrow placement="right">
            {content}
          </Tooltip>
        ) : (
          content
        );
      },
    },
    {
      field: 'nodeType',
      headerName: 'Type',
      width: 130,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        const typeLabels: Record<string, string> = {
          app: 'Connector',
          kb: 'Collection',
          folder: 'Folder',
          recordGroup: 'Record Group',
          record: params.row.recordType?.split('_').join(' ') || 'File',
        };
        return (
          <Chip
            label={typeLabels[params.value] || params.value}
            size="small"
            variant="outlined"
            sx={{
              fontSize: '0.7rem',
              height: 24,
              borderRadius: 1,
              borderColor: 'divider',
              color: 'text.secondary',
            }}
          />
        );
      },
    },
    {
      field: 'indexingStatus',
      headerName: 'Status',
      width: 130,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        if (params.row.nodeType !== 'record')           return (
          <Typography variant="caption" color="text.secondary">
            —
          </Typography>
        );

        const status = params.value || 'NOT_STARTED';
        let displayLabel = '';
        let color = theme.palette.text.secondary;

        switch (status) {
          case 'COMPLETED':
            displayLabel = 'Completed';
            color = theme.palette.success.main;
            break;
          case 'IN_PROGRESS':
            displayLabel = 'In Progress';
            color = theme.palette.info.main;
            break;
          case 'FAILED':
            displayLabel = 'Failed';
            color = theme.palette.error.main;
            break;
          case 'NOT_STARTED':
            displayLabel = 'Not Started';
            color = theme.palette.warning.main;
            break;
          case 'PAUSED':
            displayLabel = 'Paused';
            color = theme.palette.warning.main;
            break;
          case 'QUEUED':
            displayLabel = 'Queued';
            color = theme.palette.info.main;
            break;
          case 'FILE_TYPE_NOT_SUPPORTED':
            displayLabel = 'Not Supported';
            color = theme.palette.text.secondary;
            break;
          case 'AUTO_INDEX_OFF':
            displayLabel = 'MANUAL INDEXING';
            color = theme.palette.primary.main;
            break;
          case 'EMPTY':
            displayLabel = 'EMPTY';
            color = theme.palette.text.secondary;
            break;
          case 'ENABLE_MULTIMODAL_MODELS':
            displayLabel = 'ENABLE MULTIMODAL MODELS';
            color = theme.palette.text.secondary;
            break;
          case 'CONNECTOR_DISABLED':
            displayLabel = 'CONNECTOR DISABLED';
            color = theme.palette.warning.main;
            break;
          default:
            displayLabel = status.replace(/_/g, ' ');
        }

        return (
          <Typography variant="caption" sx={{ color, fontWeight: 500 }}>
            {displayLabel}
          </Typography>
        );
      },
    },
    {
      field: 'origin',
      headerName: 'Source',
      width: 170,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        const node = params.row;
        
        if (params.value === 'CONNECTOR') {
          // Show connector icon + connector name with premium styling
          return (
            <Box sx={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: 1, 
              justifyContent: 'center',
              height: '100%',
              width: '100%'
            }}>
              <Box
                component="img"
                src={getConnectorIconPath(node.connector)}
                alt={node.connector}
                sx={{
                  width: 20,
                  height: 20,
                  flexShrink: 0,
                }}
              />
              <Typography variant="caption" sx={{ fontWeight: 600, color: theme.palette.primary.main }}>
                {node.connector}
              </Typography>
            </Box>
          );
        }
        
        // Show KB icon + "Knowledge Base" text with premium styling
        return (
          <Box sx={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: 1, 
            justifyContent: 'center',
            height: '100%',
            width: '100%'
          }}>
            <Icon
              icon={folderMultipleIcon}
              style={{
                fontSize: '20px',
                color: theme.palette.success.main,
                flexShrink: 0,
              }}
            />
            <Typography variant="caption" sx={{ fontWeight: 600, color: theme.palette.primary.main }}>
            Collection
            </Typography>
          </Box>
        );
      },
    },
    {
      field: 'sizeInBytes',
      headerName: 'Size',
      width: 90,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        if (params.row.nodeType !== 'record' || !params.value)            return (
          <Typography variant="caption" color="text.secondary">
            —
          </Typography>
        );
        return (
          <Typography variant="caption" color="text.secondary" sx={{ pr: 1.5 }}>
            {formatFileSize(params.value)}
          </Typography>
        );
      },
    },
    {
      field: 'createdAt',
      headerName: 'Created',
      width: 140,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => {
        const timestamp = params.value;

        if (!timestamp) {
          return (
            <Typography variant="caption" color="text.secondary">
              —
            </Typography>
          );
        }

        try {
          const date = new Date(timestamp);

          if (Number.isNaN(date.getTime())) {
            return (
              <Typography variant="caption" color="text.secondary">
                —
              </Typography>
            );
          }

          return (
            <Box sx={{ pl: 0.5, mt: 1.5 }}>
              <Typography
                variant="caption"
                display="block"
                color="text.primary"
                sx={{ fontWeight: 500 }}
              >
                {date.toLocaleDateString(undefined, {
                  year: 'numeric',
                  month: 'short',
                  day: 'numeric',
                })}
              </Typography>
              <Typography variant="caption" display="block" color="text.secondary">
                {date.toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </Typography>
            </Box>
          );
        } catch (e) {
          return (
            <Typography variant="caption" color="text.secondary">
              —
            </Typography>
          );
        }
      },
    },
    {
      field: 'updatedAt',
      headerName: 'Updated',
      width: 140,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => {
        const timestamp = params.value;

        if (!timestamp) {
          return (
            <Typography variant="caption" color="text.secondary">
              —
            </Typography>
          );
        }

        try {
          const date = new Date(timestamp);

          if (Number.isNaN(date.getTime())) {
            return (
              <Typography variant="caption" color="text.secondary">
                —
              </Typography>
            );
          }

          return (
            <Box sx={{ pl: 0.5, mt: 1.5 }}>
              <Typography
                variant="caption"
                display="block"
                color="text.primary"
                sx={{ fontWeight: 500 }}
              >
                {date.toLocaleDateString(undefined, {
                  year: 'numeric',
                  month: 'short',
                  day: 'numeric',
                })}
              </Typography>
              <Typography variant="caption" display="block" color="text.secondary">
                {date.toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </Typography>
            </Box>
          );
        } catch (e) {
          return (
            <Typography variant="caption" color="text.secondary">
              —
            </Typography>
          );
        }
      },
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 80,
      sortable: false,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        const node = params.row;

        const handleActionsClick = (event: React.MouseEvent<HTMLButtonElement>) => {
          event.stopPropagation();

          const menuActions: ActionMenuItem[] = [
            {
              label: node.hasChildren ? 'Open' : 'View Details',
              icon: eyeIcon,
              color: theme.palette.primary.main,
              onClick: () => {
                if (node.hasChildren) {
                  handleRowClick(node);
                } else {
                  onNavigateToRecord(node.id);
                }
              },
            },
          ];

          // Add download option for records
          if (node.nodeType === 'record' && node.recordType === 'FILE') {
            menuActions.push({
              label: 'Download',
              icon: downloadIcon,
              color: theme.palette.primary.main,
              onClick: () =>
                handleDownload(
                  node.origin === ORIGIN.UPLOAD ? node.externalRecordId ?? node.id : node.id,
                  node.name
                ),
            });
          }

          // Add reindex options
          if (node.nodeType === 'record') {
            // Force reindex for completed records only
            if (node.indexingStatus === 'COMPLETED') {
              menuActions.push({
                label: 'Force Reindex',
                icon: refreshIcon,
                color: theme.palette.info.main,
                onClick: () =>
                  setForceReindexDialog({ open: true, id: node.id, name: node.name, type: 'record' }),
              });
            }
            // For records
            if (node.indexingStatus === 'FAILED' || node.indexingStatus === 'NOT_STARTED' || node.indexingStatus === 'PAUSED' || node.indexingStatus === 'QUEUED' || node.indexingStatus === 'AUTO_INDEX_OFF') {
              menuActions.push({
                label: 'Retry Indexing',
                icon: refreshIcon,
                color: theme.palette.warning.main,
                onClick: () => handleRetryIndexing(node.id),
              });
            }
          } 
          else if (node.nodeType === 'recordGroup') {
            // For recordGroups (with depth: 100, uses reindexRecordGroup API)
              menuActions.push({
                label: 'Manual index',
                icon: refreshIcon,
                color: theme.palette.warning.main,
                onClick: () => handleRetryIndexingRecordGroup(node.id),
              });
            
          }

          // Add delete option for records with permissions
          if (
            node.nodeType === 'record' &&
            node.permission?.canDelete &&
            node.origin !== ORIGIN.CONNECTOR
          ) {
            menuActions.push({
              label: 'Delete Record',
              icon: trashCanIcon,
              color: theme.palette.error.main,
              onClick: () =>
                setDeleteDialogData({ open: true, recordId: node.id, recordName: node.name }),
              isDanger: true,
            });
          }

          showActionMenu(event.currentTarget, menuActions);
        };

        return (
          <IconButton
            size="small"
            onClick={handleActionsClick}
            sx={{
              width: 32,
              height: 32,
              color: alpha(theme.palette.text.secondary, 0.5),
              transition: 'all 0.2s',
              '&:hover': {
                backgroundColor: alpha(theme.palette.primary.main, 0.08),
                color: theme.palette.primary.main,
                transform: 'scale(1.1)',
              },
            }}
          >
            <Icon icon={dotsIcon} fontSize={18} />
          </IconButton>
        );
      },
    },
  ];

  return (
    <Box sx={{ display: 'flex', maxHeight: '90vh', width: '100vw', overflow: 'hidden' }}>
      {/* Dynamic Filter Sidebar */}
      <DynamicFilterSidebar
        availableFilters={availableFilters}
        appliedFilters={filters}
        onFilterChange={handleFilterChange}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
        isLoading={loading}
      />

      {/* Main Content */}
      <MainContentContainer theme={theme} sidebarOpen={sidebarOpen}>
        <Fade in timeout={300}>
          <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            {loading && (
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

            <ModernToolbar theme={theme} elevation={0}>
              <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'row', gap: 0.5, flexWrap: 'wrap' }}>
                {/* Title Row */}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                  <Icon icon={databaseIcon} fontSize={22} color={theme.palette.primary.main} />
                  <Typography variant="h6" fontWeight={600} sx={{ fontSize: '1.125rem', whiteSpace: 'nowrap' }}>
                    All Records
                  </Typography>
                  {counts && (
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                      {counts.total} items
                    </Typography>
                  )}
                   {/* Breadcrumbs Row - Only shown when navigating into folders */}
                </Box>
                {breadcrumbs && breadcrumbs.length > 0 && (() => {
                    // Truncate breadcrumbs if too many (show first 1, ..., last 2)
                    const MAX_VISIBLE_ITEMS = 3;
                    const showEllipsis = breadcrumbs.length > MAX_VISIBLE_ITEMS;
                    let visibleBreadcrumbs: (Breadcrumb | 'ellipsis')[] = [];
                    
                    if (showEllipsis) {
                      visibleBreadcrumbs = [
                        ...breadcrumbs.slice(0, 0),
                        'ellipsis',
                        ...breadcrumbs.slice(-2),
                      ];
                    } else {
                      visibleBreadcrumbs = [...breadcrumbs];
                    }

                    return (
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 0.75,
                          pl: 4.75, // Align with title (icon width + gap)
                          flexWrap: 'wrap',
                        }}
                      >
                       {/* Compact Back Button */}
                       <Box
                         component="button"
                         onClick={() => {
                           if (breadcrumbs.length > 1) {
                             const previousBreadcrumb = breadcrumbs[breadcrumbs.length - 2];
                             handleBreadcrumbClick(previousBreadcrumb, breadcrumbs.length - 1);
                           } else {
                             handleBreadcrumbClick(null, 0);
                           }
                         }}
                         sx={{
                           width: 32,
                           height: 32,
                           borderRadius: 1,
                           border: `1px solid ${theme.palette.divider}`,
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

                       {/* Compact Breadcrumb Navigation */}
                       <Breadcrumbs
                         separator={
                           <Icon
                             icon={chevronRightIcon}
                             fontSize={14}
                             color={theme.palette.text.disabled}
                           />
                         }
                         sx={{
                           flex: 1,
                           minWidth: 0,
                           '& .MuiBreadcrumbs-separator': {
                             mx: 0.5,
                           },
                           '& .MuiBreadcrumbs-ol': {
                             flexWrap: 'nowrap',
                             alignItems: 'center',
                             overflow: 'hidden',
                           },
                           '& .MuiBreadcrumbs-li': {
                             minWidth: 0,
                           },
                         }}
                       >
                         {/* Home/All Records - Compact Link */}
                         <Box
                           component="button"
                           onClick={() => handleBreadcrumbClick(null, 0)}
                           sx={{
                             display: 'flex',
                             alignItems: 'center',
                             padding: '4px 6px',
                             borderRadius: '4px',
                             fontSize: '0.8125rem',
                             fontWeight: 500,
                             color: 'text.secondary',
                             backgroundColor: 'transparent',
                             border: 'none',
                             cursor: 'pointer',
                             transition: 'all 0.2s ease',
                             minWidth: 0,
                             '&:hover': {
                               backgroundColor: 'action.hover',
                               color: 'text.primary',
                             },
                           }}
                         >
                           <Icon icon={homeIcon} fontSize={14} />
                           <Typography
                             sx={{
                               display: { xs: 'none', sm: 'inline' },
                               ml: 0.5,
                               fontSize: '0.8125rem',
                             }}
                           >
                             All Records
                           </Typography>
                         </Box>

                         {/* Breadcrumb Items - Compact Links */}
                         {visibleBreadcrumbs.map((item, displayIndex) => {
                           if (item === 'ellipsis') {
                             return (
                               <Box
                                 key="ellipsis"
                                 component="button"
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
                                   cursor: 'default',
                                   fontSize: '0.8125rem',
                                   fontWeight: 600,
                                 }}
                               >
                                 ...
                               </Box>
                             );
                           }

                           const actualIndex = breadcrumbs.findIndex((b) => b.id === item.id);
                           const isLast = actualIndex === breadcrumbs.length - 1;
                           
                           return (
                             <Box
                               key={item.id}
                               component={isLast ? 'span' : 'button'}
                               onClick={isLast ? undefined : () => handleBreadcrumbClick(item, actualIndex + 1)}
                               sx={{
                                 display: 'flex',
                                 alignItems: 'center',
                                 padding: '4px 6px',
                                 borderRadius: '4px',
                                 fontSize: '0.8125rem',
                                 fontWeight: 500,
                                 color: isLast ? 'text.primary' : 'text.secondary',
                                 backgroundColor: 'transparent',
                                 border: 'none',
                                 cursor: isLast ? 'default' : 'pointer',
                                 transition: 'all 0.2s ease',
                                 minWidth: 0,
                                 ...(!isLast && {
                                   '&:hover': {
                                     backgroundColor: 'action.hover',
                                     color: 'text.primary',
                                   },
                                 }),
                               }}
                             >
                               <Icon icon={folderIcon} fontSize={14} />
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
                             </Box>
                           );
                         })}
                       </Breadcrumbs>
                      </Box>
                    );
                  })()}
              </Box>

              <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ flexShrink: 0, pt: 0.25 }}>
                <TextField
                  placeholder="Search records..."
                  variant="outlined"
                  size="small"
                  value={searchQueryLocal}
                  onChange={(e) => {
                    const newValue = e.target.value;
                    setSearchQueryLocal(newValue);
                    // If the search input becomes empty, automatically clear the search
                    if (newValue.trim() === '' && q) {
                      handleClearSearch();
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleSearchSubmit();
                    }
                  }}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Icon icon={searchIcon} style={{ color: theme.palette.text.secondary }} />
                      </InputAdornment>
                    ),
                    endAdornment: searchQueryLocal && (
                      <InputAdornment position="end">
                        <IconButton size="small" onClick={handleClearSearch}>
                          <Icon icon={clearIcon} fontSize={16} />
                        </IconButton>
                      </InputAdornment>
                    ),
                  }}
                  sx={{
                    width: 320,
                    '& .MuiOutlinedInput-root': {
                      borderRadius: '10px',
                      backgroundColor: theme.palette.background.paper,
                      transition: theme.transitions.create(['background-color', 'box-shadow']),
                      border: `1px solid ${theme.palette.divider}`,

                      '&.Mui-focused': {
                        boxShadow: `0 0 0 2px ${theme.palette.primary.main}`,
                        borderColor: theme.palette.primary.main,
                      },
                      '&:hover': {
                        borderColor: theme.palette.text.primary,
                      },
                      '& .MuiOutlinedInput-notchedOutline': {
                        border: 'none',
                      },
                    },

                    '& .MuiInputBase-input::placeholder': {
                      color: theme.palette.text.secondary,
                      opacity: 0.8,
                    },
                  }}
                />

                <Tooltip title="Refresh Data">
                  <CompactIconButton theme={theme} onClick={handleRefresh}>
                    <Icon icon={refreshIcon} fontSize={16} />
                  </CompactIconButton>
                </Tooltip>
              </Stack>
            </ModernToolbar>

            <Box sx={{ flexGrow: 1, m: 2.5, minHeight: 0, display: 'flex' }}>
              <Paper
                elevation={0}
                sx={{
                  flexGrow: 1,
                  overflow: 'hidden',
                  width: '100%',
                  display: 'flex',
                  flexDirection: 'column',
                  borderRadius: 2,
                  minHeight: '80vh',
                }}
              >
                {loading && items.length === 0 ? (
                  <DataGridSkeleton rowCount={limit} />
                ) : (
                  <>
                    <Box sx={{ flexGrow: 1, height: 'calc(100% - 64px)', minHeight: 0, overflow: 'hidden', position: 'relative' }}>
                      <DataGrid<HubNode>
                        rows={items}
                        columns={columns}
                        hideFooterPagination
                        disableRowSelectionOnClick
                        onRowClick={(params, event) => {
                          const isCheckboxClick = (event.target as HTMLElement).closest(
                            '.MuiDataGrid-cellCheckbox'
                          );
                          if (
                            !isCheckboxClick &&
                            (params.row.hasChildren || params.row.nodeType === 'record')
                          ) {
                            handleRowClick(params.row);
                          }
                        }}
                        getRowId={(row) => row.id}
                        rowHeight={56}
                        getRowClassName={(params) =>
                          !params.row.hasChildren && params.row.nodeType !== 'record'
                            ? 'row-non-clickable'
                            : ''
                        }
                        localeText={{
                          noRowsLabel: 'No records found',
                        }}
                        sx={{
                          border: 'none',
                          height: '100%',
                          // Custom scrollbar styles for visibility on Mac
                          '& .MuiDataGrid-virtualScroller': {
                            '&::-webkit-scrollbar': {
                              width: '6px',
                              height: '6px',
                            },
                            '&::-webkit-scrollbar-track': {
                              backgroundColor: 'transparent',
                            },
                            '&::-webkit-scrollbar-thumb': {
                              backgroundColor: theme.palette.mode === 'dark'
                                ? alpha(theme.palette.text.secondary, 0.25)
                                : alpha(theme.palette.text.secondary, 0.16),
                              borderRadius: '3px',
                              '&:hover': {
                                backgroundColor: theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.text.secondary, 0.4)
                                  : alpha(theme.palette.text.secondary, 0.24),
                              },
                            },
                          },
                          '& .MuiDataGrid-columnHeaders': {
                            backgroundColor: alpha('#000', 0.02),
                            borderBottom: '1px solid',
                            borderColor: 'divider',
                            minHeight: '56px !important',
                            height: '56px !important',
                            maxHeight: '56px !important',
                            lineHeight: '56px !important',
                          },
                          '& .MuiDataGrid-columnHeader': {
                            height: '56px !important',
                            maxHeight: '56px !important',
                            lineHeight: '56px !important',
                          },
                          '& .MuiDataGrid-columnHeaderTitle': {
                            fontWeight: 600,
                            fontSize: '0.875rem',
                            color: theme.palette.text.primary,
                            letterSpacing: '0.02em',
                          },
                          '& .MuiDataGrid-cell': {
                            border: 'none',
                            padding: 0,
                            maxHeight: '56px !important',
                            minHeight: '56px !important',
                            height: '56px !important',
                            lineHeight: '56px !important',
                          },
                          '& .MuiDataGrid-cellContent': {
                            maxHeight: '56px !important',
                            height: '56px !important',
                            lineHeight: '56px !important',
                          },
                          '& .MuiDataGrid-row': {
                            maxHeight: '56px !important',
                            minHeight: '56px !important',
                            height: '56px !important',
                            ml: 1,
                            borderBottom: '1px solid',
                            borderColor: alpha('#000', 0.05),
                            '&:hover': {
                              backgroundColor: alpha('#1976d2', 0.03),
                              cursor: 'pointer',
                            },
                            '&.Mui-selected': {
                              backgroundColor: alpha('#1976d2', 0.08),
                              '&:hover': {
                                backgroundColor: alpha('#1976d2', 0.12),
                              },
                            },
                          },
                          '& .MuiDataGrid-cell:focus, .MuiDataGrid-cell:focus-within': {
                            outline: 'none',
                          },
                          '& .MuiDataGrid-columnHeader:focus, .MuiDataGrid-columnHeader:focus-within':
                          {
                            outline: 'none',
                          },
                        }}
                        slotProps={{
                          cell: {
                            onMouseEnter: (e: any) => {
                              const rowId = e.currentTarget.parentElement?.dataset.id;
                              const row = items.find((item) => item.id === rowId);
                              if (row && !row.hasChildren && row.nodeType !== 'record') {
                                e.currentTarget.parentElement?.setAttribute(
                                  'title',
                                  'This item is empty and cannot be opened'
                                );
                              }
                            },
                          },
                        }}
                      />
                    </Box>

                    {/* Pagination footer */}
                    <Box
                      sx={{
                        flexShrink: 0,
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        px: 3,
                        py: 2.5,
                        borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                        bgcolor: alpha(theme.palette.background.neutral, 0.3),
                        backdropFilter: 'blur(8px)',
                        borderBottomLeftRadius: 12,
                        borderBottomRightRadius: 12,
                        minHeight: '72px',
                      }}
                    >
                      <Typography variant="body2" color="text.secondary" fontWeight={500}>
                        {totalCount === 0
                          ? 'No records found'
                          : `Showing ${(page - 1) * limit + 1}-${Math.min(page * limit, totalCount)} of ${totalCount} records`}
                      </Typography>

                      <Stack direction="row" spacing={2} alignItems="center">
                        <Pagination
                          count={Math.ceil(totalCount / limit)}
                          page={page}
                          onChange={handlePageChange}
                          color="primary"
                          size="medium"
                          shape="rounded"
                          sx={{
                            '& .MuiPaginationItem-root': {
                              fontWeight: 500,
                              borderRadius: 2,
                            },
                            '& .Mui-selected': {
                              fontWeight: 700,
                            },
                          }}
                        />
                        <Select
                          value={limit}
                          onChange={handleLimitChange}
                          size="small"
                          sx={{
                            minWidth: 120,
                            borderRadius: 2,
                            fontWeight: 500,
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderColor: alpha(theme.palette.divider, 0.2),
                            },
                            '&:hover .MuiOutlinedInput-notchedOutline': {
                              borderColor: theme.palette.primary.main,
                            },
                          }}
                        >
                          <MenuItem value={10}>10 per page</MenuItem>
                          <MenuItem value={20}>20 per page</MenuItem>
                          <MenuItem value={50}>50 per page</MenuItem>
                          <MenuItem value={100}>100 per page</MenuItem>
                        </Select>
                      </Stack>
                    </Box>
                  </>
                )}
              </Paper>
            </Box>

            {/* Actions menu */}
            <Menu
              anchorEl={menuAnchorEl}
              open={Boolean(menuAnchorEl)}
              onClose={closeActionMenu}
              PaperProps={{
                elevation: 2,
                sx: {
                  minWidth: 180,
                  overflow: 'hidden',
                  borderRadius: 2,
                  mt: 1,
                },
              }}
            >
              {menuItems.map((item, index) => {
                const isDangerItem = item.isDanger;
                const showDivider = isDangerItem && index > 0;

                return (
                  <React.Fragment key={index}>
                    {showDivider && <Divider sx={{ my: 0.75 }} />}
                    <MenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        closeActionMenu();
                        item.onClick();
                      }}
                      sx={{
                        py: 0.75,
                        mx: 0.75,
                        my: 0.25,
                        px: 1.5,
                        borderRadius: 1.5,
                        ...(isDangerItem && {
                          color: 'error.main',
                          '&:hover': { bgcolor: 'error.lighter' },
                        }),
                      }}
                    >
                      <ListItemIcon
                        sx={{ minWidth: 30, color: isDangerItem ? 'error.main' : item.color }}
                      >
                        <Icon icon={item.icon} width={18} height={18} />
                      </ListItemIcon>
                      <ListItemText
                        primary={item.label}
                        primaryTypographyProps={{
                          variant: 'body2',
                          fontWeight: 500,
                          fontSize: '0.875rem',
                        }}
                      />
                    </MenuItem>
                  </React.Fragment>
                );
              })}
            </Menu>

            {/* Delete Record Dialog */}
            <DeleteRecordDialog
              open={deleteDialogData.open}
              onClose={() => setDeleteDialogData({ open: false, recordId: '', recordName: '' })}
              onRecordDeleted={handleDeleteSuccess}
              recordId={deleteDialogData.recordId}
              recordName={deleteDialogData.recordName}
            />

            {/* Force Reindex Dialog */}
            <Dialog
              open={forceReindexDialog.open}
              onClose={() => setForceReindexDialog({ open: false, id: '', name: '', type: 'record' })}
              maxWidth="sm"
              fullWidth
              PaperProps={{
                sx: {
                  borderRadius: 2,
                  boxShadow: theme.shadows[24],
                },
              }}
            >
              <Box
                sx={{
                  p: 3,
                  pb: 2,
                  borderBottom: `1px solid ${theme.palette.divider}`,
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                  <Icon
                    icon={refreshIcon}
                    fontSize={24}
                    color={theme.palette.warning.main}
                  />
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    Force Reindex Confirmation
                  </Typography>
                </Box>
              </Box>

              <Box sx={{ p: 3 }}>
                <Alert severity="warning" sx={{ mb: 2 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500, mb: 0.5 }}>
                    ⚠️ Extra charges may apply
                  </Typography>
                  <Typography variant="body2">
                    Force reindexing will reprocess this {forceReindexDialog.type === 'record' ? 'record' : 'record group'} even though it&apos;s already completed. This action may incur additional processing charges.
                  </Typography>
                </Alert>

                <Typography variant="body2" color="text.secondary">
                  Are you sure you want to force reindex <strong>&quot;{forceReindexDialog.name}&quot;</strong>?
                </Typography>
              </Box>

              <Box
                sx={{
                  p: 2,
                  px: 3,
                  borderTop: `1px solid ${theme.palette.divider}`,
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: 1,
                }}
              >
                <Button
                  variant="outlined"
                  onClick={() =>
                    setForceReindexDialog({ open: false, id: '', name: '', type: 'record' })
                  }
                  sx={{
                    borderRadius: 1.5,
                    textTransform: 'none',
                    px: 3,
                  }}
                >
                  Cancel
                </Button>
                <Button
                  variant="contained"
                  color="warning"
                  onClick={handleForceReindex}
                  startIcon={<Icon icon={refreshIcon} fontSize={18} />}
                  sx={{
                    borderRadius: 1.5,
                    textTransform: 'none',
                    px: 3,
                    boxShadow: 'none',
                    '&:hover': {
                      boxShadow: theme.shadows[4],
                    },
                  }}
                >
                  Force Reindex
                </Button>
              </Box>
            </Dialog>

            {/* Snackbar */}
            <Snackbar
              open={snackbar.open}
              autoHideDuration={3000}
              onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
              anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
              sx={{ mt: 7 }}
            >
              <Alert
                severity={snackbar.severity}
                sx={{ width: '100%' }}
                onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
              >
                {snackbar.message}
              </Alert>
            </Snackbar>
          </Box>
        </Fade>
      </MainContentContainer>
    </Box>
  );
};

export default AllRecordsView;
