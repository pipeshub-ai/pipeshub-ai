// ListView.tsx
import type { GridColDef } from '@mui/x-data-grid';

import React from 'react';
import { Icon } from '@iconify/react';
import databaseIcon from '@iconify-icons/mdi/database';
import codeJsonIcon from '@iconify-icons/mdi/code-json';
import dotsIcon from '@iconify-icons/mdi/dots-vertical';
// Import specific icons for better type safety
import folderIcon from '@iconify-icons/mdi/folder-outline';
import languageGoIcon from '@iconify-icons/mdi/language-go';
import filePdfBoxIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import languagePhpIcon from '@iconify-icons/mdi/language-php';
import fileWordBoxIcon from '@iconify-icons/vscode-icons/file-type-word';
import languageCss3Icon from '@iconify-icons/mdi/language-css3';
import languageJavaIcon from '@iconify-icons/mdi/language-java';
import languageRubyIcon from '@iconify-icons/mdi/language-ruby';
import emailOutlineIcon from '@iconify-icons/mdi/email-outline';
import fileExcelBoxIcon from '@iconify-icons/vscode-icons/file-type-excel';
import fileImageBoxIcon from '@iconify-icons/vscode-icons/file-type-image';
import languageHtml5Icon from '@iconify-icons/mdi/language-html5';
import fileArchiveBoxIcon from '@iconify-icons/mdi/archive-outline';
import languagePythonIcon from '@iconify-icons/mdi/language-python';
import noteTextOutlineIcon from '@iconify-icons/mdi/note-text-outline';
import languageMarkdownIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import fileMusicOutlineIcon from '@iconify-icons/mdi/file-music-outline';
import fileVideoOutlineIcon from '@iconify-icons/mdi/file-video-outline';
import filePowerpointBoxIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import languageJavascriptIcon from '@iconify-icons/mdi/language-javascript';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';


import { DataGrid } from '@mui/x-data-grid';
import {
  Box,
  Paper,
  Stack,
  alpha,
  Select,
  Divider,
  Skeleton,
  MenuItem,
  useTheme,
  ListItem,
  Typography,
  Pagination,
  ListItemText
} from '@mui/material';

interface ListViewProps {
  items: any[];
  pageLoading: boolean;
  navigateToFolder: (item: any) => void;
  handleMenuOpen: (event: React.MouseEvent<HTMLElement>, item: any) => void;
  totalCount: number;
  rowsPerPage: number;
  page: number;
  setPage: (page: number) => void;
  setRowsPerPage: (rows: number) => void;
  currentKB: any;
  loadKBContents: (kbId: string, folderId?: string) => void;
  route: any;
  CompactIconButton: any;
}

export const ListView: React.FC<ListViewProps> = ({
  items,
  pageLoading,
  navigateToFolder,
  handleMenuOpen,
  totalCount,
  rowsPerPage,
  page,
  setPage,
  setRowsPerPage,
  currentKB,
  loadKBContents,
  route,
  CompactIconButton,
}) => {
  const theme = useTheme();

  // Enhanced file icon mapping - exactly like KnowledgeBaseDetails
  const getFileIcon = (extension: string, type: string, mimeType?: string) => {
    if (type === 'folder') return folderIcon;

    // Handle mime types first (like in KnowledgeBaseDetails)
    if ((!extension || extension === '') && mimeType) {
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
      case 'py':
        return languagePythonIcon;
      case 'java':
        return languageJavaIcon;
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

  // Enhanced file icon color mapping - exactly like KnowledgeBaseDetails
  const getFileIconColor = (extension: string, type: string, mimeType?: string): string => {
    if (type === 'folder') return '#ff9800'; // Orange for folders like in original

    // Handle mime types first
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
      case 'py':
        return '#1976d2';
      case 'java':
        return '#b71c1c';
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
    if (!bytes || bytes === 0) return '—';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  const formatDate = (timestamp: number) => {
    if (!timestamp) return null;
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return null;

    return {
      date: date.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      }),
      time: date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      }),
    };
  };

  const getStatusDisplay = (status: string) => {
    if (!status) return { label: '', color: theme.palette.text.secondary };

    let displayLabel = '';
    let color = theme.palette.text.secondary;

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

    return { label: displayLabel, color };
  };

  const columns: GridColDef[] = [
    {
      field: 'name',
      headerName: 'Name',
      flex: 1,
      minWidth: 200,
      renderCell: (params) => {
        const item = params.row;
        const extension = item.extension || item.fileRecord?.extension || '';
        const mimeType = item.fileRecord?.mimeType || item.mimeType || '';

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
                  ? getFileIcon(extension, item.type, mimeType)
                  : item.recordType === 'MAIL'
                    ? getFileIcon('eml', 'file')
                    : getFileIcon('', item.type, mimeType)
              }
              style={{
                fontSize: '26px',
                color: getFileIconColor(extension, item.type, mimeType),
                marginRight: '10px',
                flexShrink: 0,
                opacity: 0.85,
              }}
            />
            <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
              {item.name || item.recordName}
            </Typography>
          </Box>
        );
      },
    },
    {
      field: 'indexingStatus',
      headerName: 'Status',
      width: 180,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        const item = params.row;
        if (!item.indexingStatus || item.type === 'folder') {
          return null;
        }

        const { label, color } = getStatusDisplay(item.indexingStatus);

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
              {label}
            </Typography>
          </Box>
        );
      },
    },
    {
      field: 'origin',
      headerName: 'Origin',
      width: 110,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => (
        <Typography variant="caption" sx={{ fontWeight: 500 }}>
          {params.row.origin || 'LOCAL'}
        </Typography>
      ),
    },
    {
      field: 'size',
      headerName: 'Size',
      width: 100,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => {
        const item = params.row;
        const size = item.sizeInBytes || item.fileRecord?.sizeInBytes;
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
        const item = params.row;
        const timestamp = item.sourceCreatedAtTimestamp;
        const formatted = formatDate(timestamp);

        if (!formatted) {
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
              {formatted.date}
            </Typography>
            <Typography variant="caption" display="block" color="text.secondary">
              {formatted.time}
            </Typography>
          </Box>
        );
      },
    },
    {
      field: 'sourceLastModifiedTimestamp',
      headerName: 'Updated',
      width: 160,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => {
        const item = params.row;
        const timestamp = item.sourceLastModifiedTimestamp;
        const formatted = formatDate(timestamp);

        if (!formatted) {
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
              {formatted.date}
            </Typography>
            <Typography variant="caption" display="block" color="text.secondary">
              {formatted.time}
            </Typography>
          </Box>
        );
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
          {params.row.version || '1'}
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
      renderCell: (params) => (
        <CompactIconButton
          size="small"
          onClick={(e: React.MouseEvent<HTMLElement>) => {
            e.stopPropagation();
            handleMenuOpen(e, params.row);
          }}
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
        </CompactIconButton>
      ),
    },
  ];

  const handleRowClick = (params: any, event: React.MouseEvent) => {
    const isCheckboxClick = (event.target as HTMLElement).closest('.MuiDataGrid-cellCheckbox');
    if (!isCheckboxClick && params.row.type === 'folder') {
      navigateToFolder(params.row);
    }else {
      window.open(`/record/${params.row.id}`, '_blank')
    }
  };

  if (pageLoading) {
    return (
      <Paper
        elevation={0}
        sx={{
          height: 600,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          overflow: 'hidden',
        }}
      >
        <Box sx={{ flexGrow: 1 }}>
          {Array.from(new Array(8)).map((_, index) => (
            <React.Fragment key={index}>
              <ListItem sx={{ py: 2, px: 3 }}>
                <ListItemText
                  primary={<Skeleton variant="text" width="40%" height={24} />}
                  secondary={<Skeleton variant="text" width="60%" height={20} />}
                />
                <ListItemText
                  primary={<Skeleton variant="text" width="80%" height={24} />}
                />
              </ListItem>
              {index < 5 && <Divider />}
            </React.Fragment>
          ))}
        </Box>
      </Paper>
    );
  }

  return (
    <Paper
      elevation={0}
      sx={{
        overflow: 'hidden',
        height: 'calc(100vh - 200px)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Box sx={{ flexGrow: 1, height: 'calc(100% - 64px)' }}>
        <DataGrid
          rows={items}
          columns={columns}
          hideFooterPagination
          checkboxSelection
          disableRowSelectionOnClick
          onRowClick={handleRowClick}
          getRowId={(row) => row.id}
          rowHeight={56}
          localeText={{
            noRowsLabel: 'No records uploaded for knowledge base',
          }}
          sx={{
            border: 'none',
            height: '100%',
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
            '& .MuiDataGrid-columnHeader:focus, .MuiDataGrid-columnHeader:focus-within': {
              outline: 'none',
            },
            '& .MuiDataGrid-columnHeaderCheckbox, & .MuiDataGrid-cellCheckbox': {
              width: '56px !important',
              minWidth: '56px !important',
              maxWidth: '56px !important',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            },
            '& .MuiCheckbox-root': {
              padding: '4px',
            },
          }}
        />
      </Box>

      {/* Enhanced pagination footer - exactly like KnowledgeBaseDetails */}
      <Box
        sx={{
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
            : `Showing ${page * rowsPerPage + 1}-${Math.min((page + 1) * rowsPerPage, totalCount)} of ${totalCount} records`}
        </Typography>

        <Stack direction="row" spacing={2} alignItems="center">
          <Pagination
            count={Math.ceil(totalCount / rowsPerPage) || 1}
            page={page + 1}
            onChange={(event, value) => setPage(value - 1)}
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
            value={rowsPerPage}
            onChange={(event) => {
              setRowsPerPage(parseInt(event.target.value as string, 10));
              setPage(0);
            }}
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
    </Paper>
  );
};

export default ListView;
