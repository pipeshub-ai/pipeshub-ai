// components/all-records-view.tsx
import type { GridColDef, GridRowParams } from '@mui/x-data-grid';

import { Icon } from '@iconify/react';
import clearIcon from '@iconify-icons/mdi/close';
// Import icons
import searchIcon from '@iconify-icons/mdi/magnify';
import refreshIcon from '@iconify-icons/mdi/refresh';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import databaseIcon from '@iconify-icons/mdi/database';
import dotsIcon from '@iconify-icons/mdi/dots-vertical';
import codeJsonIcon from '@iconify-icons/mdi/code-json';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import languageCIcon from '@iconify-icons/mdi/language-c';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import languageGoIcon from '@iconify-icons/mdi/language-go';
// File type icons (reusing from knowledge-base-details)
import filePdfBoxIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import languagePhpIcon from '@iconify-icons/mdi/language-php';
import downloadIcon from '@iconify-icons/mdi/download-outline';
import fileWordBoxIcon from '@iconify-icons/vscode-icons/file-type-word';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';
import languageCss3Icon from '@iconify-icons/mdi/language-css3';
import languageJavaIcon from '@iconify-icons/mdi/language-java';
import languageRubyIcon from '@iconify-icons/mdi/language-ruby';
import emailOutlineIcon from '@iconify-icons/mdi/email-outline';
import fileExcelBoxIcon from '@iconify-icons/vscode-icons/file-type-excel';
import fileImageBoxIcon from '@iconify-icons/vscode-icons/file-type-image';
import languageHtml5Icon from '@iconify-icons/mdi/language-html5';
import fileArchiveBoxIcon from '@iconify-icons/mdi/archive-outline';
import languagePythonIcon from '@iconify-icons/mdi/language-python';
import noteTextOutlineIcon from '@iconify-icons/vscode-icons/file-type-text';
import fileCodeOutlineIcon from '@iconify-icons/mdi/file-code-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';
import languageMarkdownIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import fileMusicOutlineIcon from '@iconify-icons/mdi/file-music-outline';
import fileVideoOutlineIcon from '@iconify-icons/mdi/file-video-outline';
import filePowerpointBoxIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import languageJavascriptIcon from '@iconify-icons/mdi/language-javascript';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';

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
  ListItemIcon,
  ListItemText,
  InputAdornment,
  LinearProgress,
  CircularProgress,
} from '@mui/material';

import { KnowledgeBaseAPI } from '../services/api';
import DeleteRecordDialog from '../delete-record-dialog';
import KnowledgeBaseSideBar from '../knowledge-base-sidebar';
import { Filters } from '../types/knowledge-base';
import { ORIGIN } from '../constants/knowledge-search';

// Import the Filters type from the sidebar to ensure compatibility

interface AllRecordsViewProps {
  onNavigateBack: () => void;
  onNavigateToRecord?: (recordId: string) => void;
}

interface Record {
  id: string;
  recordName: string;
  recordType: string;
  indexingStatus: string;
  origin: string;
  connectorName: string;
  webUrl: string;
  externalRecordId?: string;
  fileRecord?: {
    extension?: string;
    sizeInBytes?: number;
    mimeType?: string;
  };
  sourceCreatedAtTimestamp?: number;
  sourceLastModifiedTimestamp?: number;
  version?: string;
  kb?: {
    id: string;
    name: string;
  };
  permission?: {
    role: 'OWNER' | 'WRITER' | 'READER' | 'COMMENTER' | string;
    type: string;
  };
}

interface ActionMenuItem {
  label: string;
  icon: any;
  color: string;
  onClick: () => void;
  isDanger?: boolean;
}

// Helper function to create empty filters object
const createEmptyFilters = (): Filters => ({
  indexingStatus: [],
  department: [],
  moduleId: [],
  searchTags: [],
  appSpecificRecordType: [],
  recordTypes: [],
  origin: [],
  status: [],
  connectors: [],
  app: [],
  permissions: [],
});

// Styled components
// The improved component definition
const ModernToolbar = ({ theme, ...props }: any) => (
  <Box
    sx={{
      // Add sticky positioning
      position: 'sticky',
      top: 0,
      zIndex: theme.zIndex.appBar, // Make sure it stays on top

      // Existing styles
      padding: theme.spacing(1.5, 3),
      borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`, // Slightly more visible border
      backdropFilter: 'saturate(180%) blur(10px)', // A more refined blur effect

      // Layout
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',

      // Other
      borderRadius: 0,
      boxShadow: 'none',
      minHeight: 64, // A bit more vertical space is common (e.g., 64px)

      // Smooth transitions for theme changes
      transition: theme.transitions.create(['background-color', 'border-color']),
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
      flexGrow: 1, // Let it grow to fill space
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

const AllRecordsView: React.FC<AllRecordsViewProps> = ({ onNavigateBack, onNavigateToRecord }) => {
  const theme = useTheme();
  const [records, setRecords] = useState<Record[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [page, setPage] = useState(0);
  const [limit, setLimit] = useState(20);
  const [totalCount, setTotalCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Sidebar state
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [filters, setFilters] = useState<Filters>(createEmptyFilters());

  // Action menu state
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null);
  const [menuItems, setMenuItems] = useState<ActionMenuItem[]>([]);

  // Delete dialog state
  const [deleteDialogData, setDeleteDialogData] = useState({
    open: false,
    recordId: '',
    recordName: '',
  });

  // Snackbar state
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });

  // Refs to prevent re-renders
  const loadingRef = useRef(false);
  const searchTimeoutRef = useRef<NodeJS.Timeout>();

  // Action menu handlers
  const showActionMenu = (anchorElement: HTMLElement, items: ActionMenuItem[]) => {
    setMenuItems(items);
    setMenuAnchorEl(anchorElement);
  };

  const closeActionMenu = () => {
    setMenuAnchorEl(null);
  };

  // Sidebar handlers
  const handleToggleSidebar = () => {
    setSidebarOpen((prev) => !prev);
  };

  const handleFilterChange = (newFilters: Filters) => {
    // Ensure all filter properties are arrays to prevent undefined errors
    const normalizedFilters: Filters = {
      indexingStatus: newFilters.indexingStatus || [],
      department: newFilters.department || [],
      moduleId: newFilters.moduleId || [],
      searchTags: newFilters.searchTags || [],
      appSpecificRecordType: newFilters.appSpecificRecordType || [],
      recordTypes: newFilters.recordTypes || [],
      origin: newFilters.origin || [],
      status: newFilters.status || [],
      connectors: newFilters.connectors || [],
      app: newFilters.app || [],
      permissions: newFilters.permissions || [],
    };

    setFilters(normalizedFilters);
    setPage(0); // Reset to first page when filters change
  };

  // Convert filters to API params
  const buildApiParams = useCallback(() => {
    const params: any = {
      page: page + 1,
      limit,
      search: activeSearchQuery,
    };

    // Add filters to params - convert arrays to comma-separated strings for the API
    if (filters.indexingStatus && filters.indexingStatus.length > 0) {
      params.indexingStatus = filters.indexingStatus.join(',');
    }
    if (filters.recordTypes && filters.recordTypes.length > 0) {
      params.recordTypes = filters.recordTypes.join(',');
    }
    if (filters.origin && filters.origin.length > 0) {
      params.origins = filters.origin.join(','); // Note: API expects 'origins' not 'origin'
    }
    if (filters.connectors && filters.connectors.length > 0) {
      params.connectors = filters.connectors.join(',').toUpperCase();
    }
    if (filters.permissions && filters.permissions.length > 0) {
      params.permissions = filters.permissions.join(',');
    }
    if (filters.department && filters.department.length > 0) {
      params.department = filters.department.join(',');
    }
    if (filters.moduleId && filters.moduleId.length > 0) {
      params.moduleId = filters.moduleId.join(',');
    }
    if (filters.searchTags && filters.searchTags.length > 0) {
      params.searchTags = filters.searchTags.join(',');
    }
    if (filters.appSpecificRecordType && filters.appSpecificRecordType.length > 0) {
      params.appSpecificRecordType = filters.appSpecificRecordType.join(',');
    }

    return params;
  }, [page, limit, activeSearchQuery, filters]);

  // Get file icon based on extension
  const getFileIcon = (extension: string, mimeType?: string) => {
    if ((!extension || extension === '') && mimeType) {
      // Google Workspace mime types
      switch (mimeType) {
        case 'application/vnd.google-apps.document':
          return fileWordBoxIcon;
        case 'application/vnd.google-apps.spreadsheet':
          return fileExcelBoxIcon;
        case 'application/vnd.google-apps.presentation':
          return filePowerpointBoxIcon;
        case 'application/vnd.google-apps.form':
          return noteTextOutlineIcon;
        case 'application/vnd.google-apps.drawing':
          return fileImageBoxIcon;
        case 'application/vnd.google-apps.folder':
          return folderIcon;
        case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        case 'application/vnd.microsoft.word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document':
          return fileWordBoxIcon;
        case 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        case 'application/vnd.microsoft.excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel':
          return fileExcelBoxIcon;
        case 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        case 'application/vnd.microsoft.powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint':
          return filePowerpointBoxIcon;
        case 'application/vnd.microsoft.onedrive.document':
          return fileWordBoxIcon;
        case 'application/vnd.microsoft.onedrive.spreadsheet':
          return fileExcelBoxIcon;
        case 'application/vnd.microsoft.onedrive.presentation':
          return filePowerpointBoxIcon;
        case 'application/vnd.microsoft.onedrive.drawing':
          return fileImageBoxIcon;
        case 'application/vnd.microsoft.onedrive.folder':
          return folderIcon;
        default:
          return fileDocumentOutlineIcon;
      }
    }

    const ext = extension?.toLowerCase() || '';
    switch (ext) {
      case 'pdf':
        return filePdfBoxIcon;
      case 'doc':
      case 'docx':
        return fileWordBoxIcon;
      case 'xls':
      case 'xlsx':
      case 'csv':
        return fileExcelBoxIcon;
      case 'ppt':
      case 'pptx':
        return filePowerpointBoxIcon;
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'svg':
      case 'webp':
        return fileImageBoxIcon;
      case 'zip':
      case 'rar':
      case '7z':
      case 'tar':
      case 'gz':
        return fileArchiveBoxIcon;
      case 'txt':
        return noteTextOutlineIcon;
      case 'rtf':
        return fileDocumentOutlineIcon;
      case 'md':
      case 'mdx':
        return languageMarkdownIcon;
      case 'html':
      case 'htm':
        return languageHtml5Icon;
      case 'css':
        return languageCss3Icon;
      case 'js':
      case 'ts':
      case 'jsx':
      case 'tsx':
        return languageJavascriptIcon;
      case 'json':
        return codeJsonIcon;
      case 'xml':
        return fileCodeOutlineIcon;
      case 'py':
        return languagePythonIcon;
      case 'java':
        return languageJavaIcon;
      case 'c':
      case 'cpp':
      case 'cs':
        return languageCIcon;
      case 'php':
        return languagePhpIcon;
      case 'rb':
        return languageRubyIcon;
      case 'go':
        return languageGoIcon;
      case 'sql':
        return databaseIcon;
      case 'mp3':
      case 'wav':
      case 'ogg':
      case 'flac':
        return fileMusicOutlineIcon;
      case 'mp4':
      case 'avi':
      case 'mov':
      case 'wmv':
      case 'mkv':
        return fileVideoOutlineIcon;
      case 'eml':
      case 'msg':
        return emailOutlineIcon;
      default:
        return fileDocumentOutlineIcon;
    }
  };

  // Get file icon color based on extension
  const getFileIconColor = (extension: string, mimeType?: string): string => {
    if ((!extension || extension === '') && mimeType) {
      switch (mimeType) {
        case 'application/vnd.google-apps.document':
          return '#4285F4';
        case 'application/vnd.google-apps.spreadsheet':
          return '#0F9D58';
        case 'application/vnd.google-apps.presentation':
          return '#F4B400';
        case 'application/vnd.google-apps.form':
          return '#673AB7';
        case 'application/vnd.google-apps.drawing':
          return '#DB4437';
        case 'application/vnd.google-apps.folder':
          return '#5F6368';
        case 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        case 'application/vnd.microsoft.word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document.macroEnabled.12':
        case 'application/vnd.ms-word.document':
        case 'application/vnd.microsoft.onedrive.document':
          return '#2B579A';
        case 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        case 'application/vnd.microsoft.excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel.sheet.macroEnabled.12':
        case 'application/vnd.ms-excel':
        case 'application/vnd.microsoft.onedrive.spreadsheet':
          return '#217346';
        case 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        case 'application/vnd.microsoft.powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint.presentation.macroEnabled.12':
        case 'application/vnd.ms-powerpoint':
        case 'application/vnd.microsoft.onedrive.presentation':
          return '#B7472A';
        case 'application/vnd.microsoft.onedrive.drawing':
          return '#8C6A4F';
        case 'application/vnd.microsoft.onedrive.folder':
          return '#0078D4';
        default:
          return '#1976d2';
      }
    }

    const ext = extension?.toLowerCase() || '';
    switch (ext) {
      case 'pdf':
        return '#f44336';
      case 'doc':
      case 'docx':
        return '#2196f3';
      case 'xls':
      case 'xlsx':
      case 'csv':
        return '#4caf50';
      case 'ppt':
      case 'pptx':
        return '#ff9800';
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'svg':
      case 'webp':
        return '#9c27b0';
      case 'zip':
      case 'rar':
      case '7z':
      case 'tar':
      case 'gz':
        return '#795548';
      case 'txt':
      case 'rtf':
      case 'md':
        return '#607d8b';
      case 'html':
      case 'htm':
        return '#e65100';
      case 'css':
        return '#0277bd';
      case 'js':
      case 'ts':
      case 'jsx':
      case 'tsx':
        return '#ffd600';
      case 'json':
        return '#616161';
      case 'xml':
        return '#00838f';
      case 'py':
        return '#1976d2';
      case 'java':
        return '#b71c1c';
      case 'c':
      case 'cpp':
      case 'cs':
        return '#3949ab';
      case 'php':
        return '#6a1b9a';
      case 'rb':
        return '#c62828';
      case 'go':
        return '#00acc1';
      case 'sql':
        return '#00695c';
      case 'mp3':
      case 'wav':
      case 'ogg':
      case 'flac':
        return '#283593';
      case 'mp4':
      case 'avi':
      case 'mov':
      case 'wmv':
      case 'mkv':
        return '#d81b60';
      case 'eml':
      case 'msg':
        return '#6a1b9a';
      default:
        return '#1976d2';
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  // Load all records
  const loadAllRecords = useCallback(async () => {
    if (loadingRef.current) return;

    loadingRef.current = true;
    setLoading(true);

    try {
      const params = buildApiParams();
      const data = await KnowledgeBaseAPI.getAllRecords(params);

      if (data.records && data.pagination) {
        setRecords(data.records);
        setTotalCount(data.pagination.totalCount || 0);
      } else if (Array.isArray(data)) {
        setRecords(data);
        setTotalCount(data.length);
      } else {
        setRecords([]);
        setTotalCount(0);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to fetch records');
      setRecords([]);
      setTotalCount(0);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, [buildApiParams]);

  // Load records on page/limit change or filter change
  useEffect(() => {
    loadAllRecords();
  }, [loadAllRecords]);

  // Initial load
  useEffect(() => {
    loadAllRecords();
  }, [loadAllRecords]);

  useEffect(() => {
    setPage(0);
  }, [filters]);

  const handleSearchChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(event.target.value);
  };

  const handleSearchSubmit = () => {
    // Only trigger a new search if the query has actually changed
    if (searchQuery !== activeSearchQuery) {
      setActiveSearchQuery(searchQuery);
      setPage(0); // Reset to the first page for the new search
    }
  };

  const handleClearSearch = () => {
    setSearchQuery('');
    setActiveSearchQuery(''); // Immediately clear the active search
    setPage(0);
  };

  const handlePageChange = (event: unknown, newPage: number) => {
    setPage(newPage - 1); // MUI Pagination is 1-indexed, our state is 0-indexed
  };

  const handleLimitChange = (event: any) => {
    setLimit(parseInt(event.target.value as string, 10));
    setPage(0); // Reset to first page when changing limit
  };

  const handleRowClick = (params: GridRowParams, event: React.MouseEvent): void => {
    const isCheckboxClick = (event.target as HTMLElement).closest('.MuiDataGrid-cellCheckbox');
    if (!isCheckboxClick && onNavigateToRecord) {
      onNavigateToRecord(params.id as string);
    }
  };

  const handleRefresh = () => {
    loadAllRecords();
  };

  // Handle retry indexing
  const handleRetryIndexing = async (recordId: string) => {
    try {
      const response = await KnowledgeBaseAPI.reindexRecord(recordId);
      setSnackbar({
        open: true,
        message: response.success
          ? 'File indexing started successfully'
          : 'Failed to start reindexing',
        severity: response.success ? 'success' : 'error',
      });
      // Refresh the records to show updated status
      loadAllRecords();
    } catch (err: any) {
      console.error('Failed to reindexing document', err);
    }
  };

  // Handle download document
  const handleDownload = async (externalRecordId: string, recordName: string, origin: string) => {
    try {
      await KnowledgeBaseAPI.handleDownloadDocument(externalRecordId, recordName, origin);
      setSnackbar({
        open: true,
        message: 'Download started successfully',
        severity: 'success',
      });
    } catch (err: any) {
      console.error('Failed to download document', err);
    }
  };

  // Handle delete success
  const handleDeleteSuccess = () => {
    setSnackbar({
      open: true,
      message: 'Record deleted successfully',
      severity: 'success',
    });
    loadAllRecords(); // Refresh the records
  };

  // Close the delete dialog
  const handleCloseDeleteDialog = () => {
    setDeleteDialogData({
      open: false,
      recordId: '',
      recordName: '',
    });
  };

  // DataGrid columns
  const columns: GridColDef<Record>[] = [
    {
      field: '#',
      headerName: '#',
      width: 60, // Adjust width as needed
      align: 'center',
      headerAlign: 'center',
      sortable: false,
      renderCell: (params) => {
        // Calculate the row number based on the current page and limit
        const rowIndex = params.api.getRowIndexRelativeToVisibleRows(params.row.id);
        const rowNumber = page * limit + rowIndex + 1;
        return (
          <Typography
            variant="body2"
            sx={{
              color: 'text.secondary',
              fontWeight: 500,
              mt: 2,
            }}
          >
            {rowNumber}
          </Typography>
        );
      },
    },
    {
      field: 'recordName',
      headerName: 'Name',
      flex: 1,
      minWidth: 200,
      renderCell: (params) => {
        const extension = params.row.fileRecord?.extension || '';
        const mimeType = params.row.fileRecord?.mimeType || '';
        return (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              height: '100%',
              width: '100%',
              pl: 0.5,
            }}
          >
            <Icon
              icon={
                extension
                  ? getFileIcon(extension, mimeType)
                  : params.row.recordType === 'MAIL'
                    ? getFileIcon('eml')
                    : getFileIcon('', mimeType)
              }
              style={{
                fontSize: '24px',
                color: getFileIconColor(extension, mimeType),
                marginRight: '10px',
                flexShrink: 0,
                opacity: 0.85,
              }}
            />
            <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
              {params.value}
            </Typography>
          </Box>
        );
      },
    },
    {
      field: 'recordType',
      headerName: 'Type',
      width: 100,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => (
        <Typography
          variant="caption"
          sx={{
            fontWeight: 500,
          }}
        >
          {params.value}
        </Typography>
      ),
    },
    {
      field: 'indexingStatus',
      headerName: 'Status',
      width: 180,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        const status = params.value || 'NOT_STARTED';
        let displayLabel = '';
        let color = theme.palette.text.secondary;

        // Map the indexing status to appropriate display values
        switch (status) {
          case 'COMPLETED':
            displayLabel = 'COMPLETED';
            color = theme.palette.success.main;
            break;
          case 'IN_PROGRESS':
            displayLabel = 'IN PROGRESS';
            color = theme.palette.info.main;
            break;
          case 'FAILED':
            displayLabel = 'FAILED';
            color = theme.palette.error.main;
            break;
          case 'NOT_STARTED':
            displayLabel = 'NOT STARTED';
            color = theme.palette.warning.main;
            break;
          case 'FILE_TYPE_NOT_SUPPORTED':
            displayLabel = 'FILE TYPE NOT SUPPORTED';
            color = theme.palette.text.secondary;
            break;
          case 'AUTO_INDEX_OFF':
            displayLabel = 'MANUAL SYNC';
            color = theme.palette.primary.main;
            break;
          default:
            displayLabel = status.replace(/_/g, ' ').toLowerCase();
            color = theme.palette.text.secondary;
        }

        // Capitalize first letter of each word
        displayLabel = displayLabel
          .split(' ')
          .map((word: string) => word.charAt(0).toUpperCase() + word.slice(1))
          .join(' ');

        return (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 0.75,
              mt: 2.4,
            }}
          >
            <Typography variant="caption" sx={{ color, fontWeight: 500 }}>
              {displayLabel}
            </Typography>
          </Box>
        );
      },
    },
    {
      field: 'origin',
      headerName: 'Origin',
      width: 180,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        // Show KB name if origin is UPLOAD and KB info is available
        if (params.value === 'CONNECTOR') {
          return (
            <Typography
              variant="caption"
              sx={{
                fontWeight: 500,
                color: theme.palette.primary.main,
              }}
            >
              {params.row.connectorName}
            </Typography>
          );
        }

        return (
          <Typography
            variant="caption"
            sx={{
              fontWeight: 500,
            }}
          >
            KNOWLEDGE BASE
          </Typography>
        );
      },
    },
    {
      field: 'fileRecord',
      headerName: 'Size',
      width: 100,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => {
        // Handle undefined, NaN, or invalid size values
        const size = params.value?.sizeInBytes;
        const formattedSize =
          size !== undefined && !Number.isNaN(size) && size > 0 ? formatFileSize(size) : '—';

        return (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              pr: 2,
              fontFamily: theme.typography.fontFamily,
            }}
          >
            {formattedSize}
          </Typography>
        );
      },
    },
    {
      field: 'sourceCreatedAtTimestamp',
      headerName: 'Created',
      width: 160,
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
      field: 'sourceLastModifiedTimestamp',
      headerName: 'Updated',
      width: 160,
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
      field: 'version',
      headerName: 'Version',
      width: 70,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => (
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
          {params.value || '1.0'}
        </Typography>
      ),
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 70,
      sortable: false,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        // Get file extension for dynamic tooltips
        const fileExt = params.row.fileRecord?.extension || '';
        const recordPermission = params.row.permission;
        const canReindex =
          recordPermission?.role === 'OWNER' ||
          recordPermission?.role === 'WRITER' ||
          recordPermission?.role === 'READER';
        const canModify = recordPermission?.role === 'OWNER' || recordPermission?.role === 'WRITER';
        const canDownload =
          params.row.recordType === 'FILE';
        // Get descriptive action based on file type
        const getDownloadLabel = () => {
          if (fileExt.toLowerCase().includes('pdf')) return 'Download PDF';
          if (fileExt.toLowerCase().includes('doc')) return 'Download Document';
          if (fileExt.toLowerCase().includes('xls')) return 'Download Spreadsheet';
          return 'Download File';
        };

        const handleActionsClick = (event: React.MouseEvent<HTMLButtonElement>) => {
          event.stopPropagation();

          // Create menu items dynamically
          const items: ActionMenuItem[] = [
            {
              label: 'View Details',
              icon: eyeIcon,
              color: theme.palette.primary.main,
              onClick: () => {
                if (onNavigateToRecord) {
                  onNavigateToRecord(params.row.id);
                }
              },
            },
            ...(canDownload
              ? [
                  {
                    label: getDownloadLabel(),
                    icon: downloadIcon,
                    color: theme.palette.primary.main,
                    onClick: () =>
                      handleDownload(
                        params.row.origin === ORIGIN.UPLOAD
                          ? params.row.externalRecordId!
                          : params.row.id,
                        params.row.recordName,
                        params.row.origin
                      ),
                  },
                ]
              : []),
            // Only show reindex options for OWNER and WRITER of this specific record
            ...(canReindex &&
            (params.row.indexingStatus === 'FAILED' || params.row.indexingStatus === 'NOT_STARTED')
              ? [
                  {
                    label: 'Retry Indexing',
                    icon: refreshIcon,
                    color: theme.palette.warning.main,
                    onClick: () => handleRetryIndexing(params.row.id),
                  },
                ]
              : []),
            // Only show manual indexing for OWNER and WRITER of this specific record
            ...(canReindex && params.row.indexingStatus === 'AUTO_INDEX_OFF'
              ? [
                  {
                    label: 'Start Manual Indexing',
                    icon: refreshIcon,
                    color: theme.palette.success.main,
                    onClick: () => handleRetryIndexing(params.row.id),
                  },
                ]
              : []),
            // Only show delete option for OWNER and WRITER of this specific record
            ...(canModify
              ? [
                  {
                    label: 'Delete Record',
                    icon: trashCanIcon,
                    color: theme.palette.error.main,
                    onClick: () =>
                      setDeleteDialogData({
                        open: true,
                        recordId: params.row.id,
                        recordName: params.row.recordName,
                      }),
                    isDanger: true,
                  },
                ]
              : []),
          ];

          // Show the menu
          showActionMenu(event.currentTarget, items);
        };

        return (
          <IconButton
            size="small"
            onClick={handleActionsClick}
            sx={{
              width: 28,
              height: 28,
              color: alpha(theme.palette.text.primary, 0.6),
              '&:hover': {
                backgroundColor: alpha(theme.palette.primary.main, 0.04),
                color: theme.palette.primary.main,
              },
            }}
          >
            <Icon icon={dotsIcon} fontSize={16} />
          </IconButton>
        );
      },
    },
  ];

  return (
    <Box sx={{ display: 'flex', maxHeight: '90vh', width: '100vw', overflow: 'hidden' }}>
      {/* Sidebar */}
      <KnowledgeBaseSideBar
        filters={filters}
        onFilterChange={handleFilterChange}
        openSidebar={sidebarOpen}
        onToggleSidebar={handleToggleSidebar}
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
              <Stack
                direction="row"
                alignItems="center"
                spacing={3}
                sx={{ flexGrow: 1, minWidth: 0 }}
              >
                <CompactIconButton theme={theme} onClick={onNavigateBack} size="small">
                  <Icon icon={arrowLeftIcon} fontSize={16} />
                </CompactIconButton>

                <Stack direction="row" alignItems="center" spacing={1.5}>
                  <Icon icon={databaseIcon} fontSize={24} color={theme.palette.primary.main} />
                  <Typography variant="h6" fontWeight={600}>
                    All Records
                  </Typography>
                </Stack>
              </Stack>

              <Stack direction="row" spacing={1} alignItems="center">
                <TextField
                  placeholder="Search records ..."
                  variant="outlined"
                  size="small"
                  value={searchQuery}
                  onChange={handleSearchChange}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleSearchSubmit();
                    }
                  }}
                  InputProps={{
                    // ... same InputProps as before
                    startAdornment: (
                      <InputAdornment position="start">
                        <Icon icon={searchIcon} style={{ color: theme.palette.text.secondary }} />
                      </InputAdornment>
                    ),
                    endAdornment: searchQuery && (
                      // Show either a clear button or a search button
                      <InputAdornment position="end">
                        {searchQuery ? (
                          <IconButton size="small" onClick={handleClearSearch}>
                            <Icon icon={clearIcon} fontSize={16} />
                          </IconButton>
                        ) : (
                          <Tooltip title="Search">
                            <IconButton size="small" onClick={handleSearchSubmit}>
                              <Icon icon={searchIcon} fontSize={16} />
                            </IconButton>
                          </Tooltip>
                        )}
                      </InputAdornment>
                    ),
                  }}
                  sx={{
                    width: '100%',
                    minWidth: '320px',

                    '& .MuiOutlinedInput-root': {
                      borderRadius: '10px',
                      backgroundColor: theme.palette.background.paper,
                      transition: theme.transitions.create(['background-color', 'box-shadow']),
                      border: `1px solid ${theme.palette.divider}`, // ✅ Add visible border

                      '&.Mui-focused': {
                        boxShadow: `0 0 0 2px ${theme.palette.primary.main}`,
                        borderColor: theme.palette.primary.main, // Optional: change border color on focus
                      },

                      '&:hover': {
                        borderColor: theme.palette.text.primary, // Optional: border on hover
                      },

                      '& .MuiOutlinedInput-notchedOutline': {
                        border: 'none', // Keep this disabled since we use custom border
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
            <Box
              sx={{
                flexGrow: 1,
                m: 2, // Margin is here
                minHeight: 0, // Crucial flexbox property for children to size correctly
                display: 'flex', // Make it a flex container for the Paper inside
              }}
            >
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
                {loading && records.length === 0 ? (
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'center',
                      alignItems: 'center',
                      height: '100%',
                      flexDirection: 'column',
                      gap: 2,
                    }}
                  >
                    <CircularProgress size={36} thickness={4} />
                    <Typography variant="body1" color="text.secondary">
                      Loading all records...
                    </Typography>
                  </Box>
                ) : (
                  <>
                    <Box sx={{ flexGrow: 1, height: 'calc(100% - 64px)', minHeight: 0 }}>
                      <DataGrid<Record>
                        rows={records}
                        columns={columns}
                        hideFooterPagination
                        disableRowSelectionOnClick
                        onRowClick={handleRowClick}
                        getRowId={(row) => row.id}
                        rowHeight={56}
                        localeText={{
                          noRowsLabel: 'No records found',
                        }}
                        sx={{
                          border: 'none',
                          height: '100%',
                          minWidth: 0,
                          '& .MuiDataGrid-main': {
                            minWidth: 0,
                            overflow: 'hidden',
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
                            color: 'text.primary',
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
                        py: 2,
                        borderTop: '1px solid',
                        borderColor: alpha('#000', 0.05),
                        bgcolor: alpha('#000', 0.01),
                        height: '54px',
                      }}
                    >
                      <Typography variant="body2" color="text.secondary">
                        {totalCount === 0
                          ? 'No records found'
                          : `Showing ${page * limit + 1}-${Math.min((page + 1) * limit, totalCount)} of ${totalCount} records`}
                      </Typography>

                      <Stack direction="row" spacing={2} alignItems="center">
                        <Pagination
                          count={Math.ceil(totalCount / limit)}
                          page={page + 1} // MUI Pagination is 1-indexed
                          onChange={handlePageChange}
                          color="primary"
                          size="small"
                          shape="rounded"
                          sx={{
                            '& .MuiPaginationItem-root': {
                              borderRadius: '6px',
                            },
                          }}
                        />
                        <Select
                          value={limit}
                          onChange={handleLimitChange}
                          size="small"
                          sx={{
                            minWidth: 100,
                            '& .MuiOutlinedInput-root': {
                              borderRadius: '8px',
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

            {/* Actions menu for table rows */}
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
              transitionDuration={200}
            >
              {menuItems.map((item, index) => {
                const isDangerItem = item.isDanger;
                const showDivider = isDangerItem && index > 0;

                return (
                  <React.Fragment key={index}>
                    {/* Add divider before danger items */}
                    {showDivider && <Divider sx={{ my: 0.75, opacity: 0.6 }} />}

                    <MenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        closeActionMenu(); // Explicitly close the menu
                        item.onClick(); // Then execute the action
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
                                bgcolor: (theme1) =>
                                  theme1.palette.mode === 'dark'
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
                          color: isDangerItem ? 'error.main' : item.color,
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

            {/* Delete Record Dialog */}
            <DeleteRecordDialog
              open={deleteDialogData.open}
              onClose={handleCloseDeleteDialog}
              onRecordDeleted={handleDeleteSuccess}
              recordId={deleteDialogData.recordId}
              recordName={deleteDialogData.recordName}
            />

            {/* Snackbars */}
            {/* <Snackbar
              open={!!error}
              autoHideDuration={5000}
              onClose={() => setError(null)}
              anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
              sx={{ mt: 7 }}
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
            </Snackbar> */}

            <Snackbar
              open={snackbar.open}
              autoHideDuration={3000}
              onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
              anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
              sx={{ mt: 7 }}
            >
              <Alert
                severity={snackbar.severity}
                sx={{
                  width: '100%',
                  ...(snackbar.severity === 'success' && {
                    bgcolor: theme.palette.success.main,
                    color: theme.palette.success.contrastText,
                  }),
                }}
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
