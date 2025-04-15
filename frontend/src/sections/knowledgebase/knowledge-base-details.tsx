import type { Icon as IconifyIcon } from '@iconify/react';
import type { GridColDef, GridRowParams } from '@mui/x-data-grid';

import { Icon } from '@iconify/react';
import { useNavigate } from 'react-router';
import { useDropzone } from 'react-dropzone';
import closeIcon from '@iconify-icons/mdi/close';
import React, { useState, useEffect } from 'react';
import uploadIcon from '@iconify-icons/mdi/upload';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import refreshIcon from '@iconify-icons/mdi/refresh';
import databaseIcon from '@iconify-icons/mdi/database';
import cloudIcon from '@iconify-icons/mdi/cloud-upload';
import dotsIcon from '@iconify-icons/mdi/dots-vertical';
import codeJsonIcon from '@iconify-icons/mdi/code-json';
import languageCIcon from '@iconify-icons/mdi/language-c';
import folderIcon from '@iconify-icons/mdi/folder-outline';
import viewColumnIcon from '@iconify-icons/mdi/view-column';
import languageGoIcon from '@iconify-icons/mdi/language-go';
import filePdfBoxIcon from '@iconify-icons/mdi/file-pdf-box';
import languagePhpIcon from '@iconify-icons/mdi/language-php';
import downloadIcon from '@iconify-icons/mdi/download-outline';
import fileWordBoxIcon from '@iconify-icons/mdi/file-word-box';
import trashCanIcon from '@iconify-icons/mdi/trash-can-outline';
import filePlusIcon from '@iconify-icons/mdi/file-plus-outline';
import languageCss3Icon from '@iconify-icons/mdi/language-css3';
import languageJavaIcon from '@iconify-icons/mdi/language-java';
import languageRubyIcon from '@iconify-icons/mdi/language-ruby';
import emailOutlineIcon from '@iconify-icons/mdi/email-outline';
import fileExcelBoxIcon from '@iconify-icons/mdi/file-excel-box';
import fileImageBoxIcon from '@iconify-icons/mdi/file-image-box';
import languageHtml5Icon from '@iconify-icons/mdi/language-html5';
import fileArchiveBoxIcon from '@iconify-icons/mdi/archive-outline';
import languagePythonIcon from '@iconify-icons/mdi/language-python';
import noteTextOutlineIcon from '@iconify-icons/mdi/note-text-outline';
import fileCodeOutlineIcon from '@iconify-icons/mdi/file-code-outline';
import languageMarkdownIcon from '@iconify-icons/mdi/language-markdown';
import fileMusicOutlineIcon from '@iconify-icons/mdi/file-music-outline';
import fileVideoOutlineIcon from '@iconify-icons/mdi/file-video-outline';
import filePowerpointBoxIcon from '@iconify-icons/mdi/file-powerpoint-box';
import languageJavascriptIcon from '@iconify-icons/mdi/language-javascript';
import fileDocumentOutlineIcon from '@iconify-icons/mdi/file-document-outline';

import { DataGrid } from '@mui/x-data-grid';
import {
  Box,
  Chip,
  Menu,
  Paper,
  Stack,
  alpha,
  Alert,
  Button,
  Dialog,
  Select,
  styled,
  Popover,
  Divider,
  Checkbox,
  MenuItem,
  Snackbar,
  useTheme,
  FormGroup,
  TextField,
  Typography,
  Pagination,
  IconButton,
  AlertTitle,
  DialogTitle,
  ListItemIcon,
  ListItemText,
  DialogContent,
  DialogActions,
  FormControlLabel,
  CircularProgress,
} from '@mui/material';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';
import { useUsers } from 'src/context/UserContext';

import DeleteRecordDialog from './delete-record-dialog';
import { handleDownloadDocument, uploadKnowledgeBaseFiles } from './utils';

import type { Record, KnowledgeBaseDetailsProps } from './types/knowledge-base';

interface ColumnVisibilityModel {
  [key: string]: boolean;
}

interface FileSizeErrorState {
  show: boolean;
  files: File[];
}

interface UploadErrorState {
  show: boolean;
  message: string;
}

interface ActionMenuItem {
  label: string;
  icon: any;
  color: string;
  onClick: () => void;
  isDanger?: boolean;
}

const StyledButton = styled(Button)(({ theme }) => ({
  borderRadius: theme.shape.borderRadius,
  textTransform: 'none',
  fontWeight: 600,
  boxShadow: theme.shadows[2],
  '&:hover': {
    boxShadow: theme.shadows[4],
  },
}));

// Maximum file size: 30MB in bytes
const MAX_FILE_SIZE = 30 * 1024 * 1024;

export default function KnowledgeBaseDetails({
  knowledgeBaseData,
  onSearchChange,
  loading,
  pagination,
  onPageChange,
  onLimitChange,
}: KnowledgeBaseDetailsProps) {
  const [columnVisibilityModel, setColumnVisibilityModel] = useState<ColumnVisibilityModel>({});
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [openUploadDialog, setOpenUploadDialog] = useState<boolean>(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState<boolean>(false);
  const [fileSizeError, setFileSizeError] = useState<FileSizeErrorState>({
    show: false,
    files: [],
  });
  const [deleteDialogData, setDeleteDialogData] = useState({
    open: false,
    recordId: '',
    recordName: '',
  });
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });
  const theme = useTheme();

  // State for action menu
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null);
  const [menuItems, setMenuItems] = useState<ActionMenuItem[]>([]);

  const [uploadError, setUploadError] = useState<UploadErrorState>({ show: false, message: '' });
  const users = useUsers();

  const navigate = useNavigate();

  // Action menu handlers
  const showActionMenu = (anchorElement: HTMLElement, items: ActionMenuItem[]) => {
    setMenuItems(items);
    setMenuAnchorEl(anchorElement);
  };

  const closeActionMenu = () => {
    setMenuAnchorEl(null);
  };

  const handleRowClick = (params: GridRowParams, event: React.MouseEvent): void => {
    const isCheckboxClick = (event.target as HTMLElement).closest('.MuiDataGrid-cellCheckbox');
    if (!isCheckboxClick) {
      navigate(`/record/${params.id}`);
    }
  };

  // Validate file size
  const validateFileSize = (filesToCheck: File[]) => {
    const oversizedFiles = filesToCheck.filter((file) => file.size > MAX_FILE_SIZE);
    return {
      valid: oversizedFiles.length === 0,
      oversizedFiles,
    };
  };

  // Modified onDrop function with file size validation
  const onDrop = (acceptedFiles: File[]) => {
    // Check for files exceeding size limit
    const { valid, oversizedFiles } = validateFileSize(acceptedFiles);

    if (!valid) {
      // Show error for oversized files
      setFileSizeError({
        show: true,
        files: oversizedFiles,
      });

      // Only keep files that are within the size limit
      const validFiles = acceptedFiles.filter((file) => file.size <= MAX_FILE_SIZE);
      setFiles(validFiles);
    } else {
      // All files are valid
      setFileSizeError({ show: false, files: [] });
      setFiles(acceptedFiles);
    }
  };

  // Enhanced dropzone with file size validation
  const { getRootProps, getInputProps, isDragActive, fileRejections } = useDropzone({
    onDrop,
    multiple: true,
    maxSize: MAX_FILE_SIZE,
    onDropRejected: (rejectedFiles) => {
      const oversizedFiles = rejectedFiles
        .filter((file) => file.errors.some((error) => error.code === 'file-too-large'))
        .map((file) => file.file);

      if (oversizedFiles.length > 0) {
        setFileSizeError({
          show: true,
          files: oversizedFiles,
        });
      }
    },
  });

  // Monitor fileRejections for size issues
  useEffect(() => {
    if (fileRejections.length > 0) {
      const oversizedFiles = fileRejections
        .filter((file) => file.errors.some((error) => error.code === 'file-too-large'))
        .map((file) => file.file);

      if (oversizedFiles.length > 0) {
        setFileSizeError({
          show: true,
          files: oversizedFiles,
        });
      }
    }
  }, [fileRejections]);

  const handleFileSizeErrorClose = () => {
    setFileSizeError({ show: false, files: [] });
  };

  const handleUploadErrorClose = () => {
    setUploadError({ show: false, message: '' });
  };

  const handleSearchInputChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    onSearchChange(event.target.value);
  };

  // Format file size
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  // Get file icon based on extension
  const getFileIcon = (extension: string): React.ComponentProps<typeof IconifyIcon>['icon'] => {
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
        return noteTextOutlineIcon; // Changed to a more visible text icon
      case 'rtf':
        return fileDocumentOutlineIcon;
      case 'md':
        return languageMarkdownIcon; // More specific markdown icon
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
  const getFileIconColor = (extension: string): string => {
    const ext = extension?.toLowerCase() || '';

    switch (ext) {
      case 'pdf':
        return '#f44336'; // Red
      case 'doc':
      case 'docx':
        return '#2196f3'; // Blue
      case 'xls':
      case 'xlsx':
      case 'csv':
        return '#4caf50'; // Green
      case 'ppt':
      case 'pptx':
        return '#ff9800'; // Orange
      case 'jpg':
      case 'jpeg':
      case 'png':
      case 'gif':
      case 'svg':
      case 'webp':
        return '#9c27b0'; // Purple
      case 'zip':
      case 'rar':
      case '7z':
      case 'tar':
      case 'gz':
        return '#795548'; // Brown
      case 'txt':
      case 'rtf':
      case 'md':
        return '#607d8b'; // Blue Grey
      case 'html':
      case 'htm':
        return '#e65100'; // Deep Orange
      case 'css':
        return '#0277bd'; // Light Blue
      case 'js':
      case 'ts':
      case 'jsx':
      case 'tsx':
        return '#ffd600'; // Yellow
      case 'json':
        return '#616161'; // Grey
      case 'xml':
        return '#00838f'; // Cyan
      case 'py':
        return '#1976d2'; // Blue
      case 'java':
        return '#b71c1c'; // Dark Red
      case 'c':
      case 'cpp':
      case 'cs':
        return '#3949ab'; // Indigo
      case 'php':
        return '#6a1b9a'; // Deep Purple
      case 'rb':
        return '#c62828'; // Red
      case 'go':
        return '#00acc1'; // Cyan
      case 'sql':
        return '#00695c'; // Teal
      case 'mp3':
      case 'wav':
      case 'ogg':
      case 'flac':
        return '#283593'; // Indigo
      case 'mp4':
      case 'avi':
      case 'mov':
      case 'wmv':
      case 'mkv':
        return '#d81b60'; // Pink
      case 'eml':
      case 'msg':
        return '#6a1b9a'; // Deep Purple
      default:
        return '#1976d2'; // Default Blue
    }
  };

  const columns: GridColDef<Record>[] = [
    {
      field: 'recordName',
      headerName: 'Name',
      flex: 1,
      minWidth: 200,
      renderCell: (params) => {
        const extension = params.row.fileRecord?.extension || '';
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
              icon={extension ? getFileIcon(extension) : folderIcon}
              style={{
                fontSize: '20px',
                color: getFileIconColor(extension),
                marginRight: '12px',
                flexShrink: 0,
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
      width: 110,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => (
        <Box
          sx={{
            width: '100%',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            mt: 1.5,
          }}
        >
          <Chip
            label={params.value}
            size="small"
            color={params.value === 'FILE' ? 'primary' : 'secondary'}
            sx={{
              height: 24,
              minWidth: 70,
              fontWeight: 500,
              fontSize: '0.75rem',
            }}
          />
        </Box>
      ),
    },
    {
      field: 'indexingStatus',
      headerName: 'Status',
      width: 120,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        const status = params.value || 'NOT STARTED';
        let color: 'default' | 'primary' | 'success' | 'error' | 'info' | 'warning' = 'default';

        switch (status) {
          case 'COMPLETED':
            color = 'success';
            break;
          case 'IN_PROGRESS':
            color = 'info';
            break;
          case 'FAILED':
            color = 'error';
            break;
          case 'NOT STARTED':
            color = 'warning';
            break;
          default:
            color = 'warning';
        }

        return (
          <Box
            sx={{
              width: '100%',
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              mt: 1.5,
            }}
          >
            <Chip
              label={status || 'PENDING'}
              size="small"
              color={color}
              sx={{
                height: 24,
                minWidth: 85,
                fontWeight: 500,
                fontSize: '0.75rem',
              }}
            />
          </Box>
        );
      },
    },
    {
      field: 'origin',
      headerName: 'Origin',
      width: 120,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => (
        <Box
          sx={{
            width: '100%',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            mt: 1.5,
          }}
        >
          <Chip
            label={params.value}
            size="small"
            variant="outlined"
            sx={{
              height: 24,
              minWidth: 70,
              fontWeight: 500,
              fontSize: '0.75rem',
            }}
          />
        </Box>
      ),
    },
    {
      field: 'fileRecord',
      headerName: 'File Size',
      width: 120,
      align: 'right',
      headerAlign: 'right',
      renderCell: (params) => (
        <Box
          sx={{
            width: '100%',
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            pr: 2,
            mt: 1.5,
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {params.value ? formatFileSize(params.value.sizeInBytes) : '-'}
          </Typography>
        </Box>
      ),
    },
    {
      field: 'createdAtTimestamp',
      headerName: 'Created',
      width: 170,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            justifyContent: 'center',
            pl: 2,
          }}
        >
          <Typography variant="body2" lineHeight={1.4}>
            {params.value ? new Date(params.value).toLocaleDateString() : '-'}
          </Typography>
          <Typography variant="caption" color="text.secondary" lineHeight={1.4}>
            {params.value
              ? new Date(params.value).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : ''}
          </Typography>
        </Box>
      ),
    },
    {
      field: 'updatedAtTimestamp',
      headerName: 'Updated',
      width: 170,
      align: 'left',
      headerAlign: 'left',
      renderCell: (params) => (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            justifyContent: 'center',
            pl: 2,
          }}
        >
          <Typography variant="body2" lineHeight={1.4}>
            {params.value ? new Date(params.value).toLocaleDateString() : '-'}
          </Typography>
          <Typography variant="caption" color="text.secondary" lineHeight={1.4}>
            {params.value
              ? new Date(params.value).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : ''}
          </Typography>
        </Box>
      ),
    },
    {
      field: 'version',
      headerName: 'Version',
      width: 140,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => (
        <Box
          sx={{
            width: '100%',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 1,
            mt: 1.5,
          }}
        >
          <Chip
            label={params.value || '1.0'}
            size="small"
            variant="outlined"
            sx={{
              minWidth: 40,
              height: 24,
              fontWeight: 500,
              fontSize: '0.75rem',
            }}
          />
        </Box>
      ),
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 90,
      sortable: false,
      align: 'center',
      headerAlign: 'center',
      renderCell: (params) => {
        // Get file extension for dynamic tooltips
        const fileExt = params.row.fileRecord?.extension || '';

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
              color: '#1976d2',
              onClick: () => navigate(`/record/${params.row.id}`),
            },
            {
              label: getDownloadLabel(),
              icon: downloadIcon,
              color: '#1976d2',
              onClick: () =>
                handleDownloadDocument(params.row.externalRecordId, params.row.recordName),
            },
            ...(params.row.indexingStatus === 'FAILED'
              ? [
                  {
                    label: 'Retry Indexing',
                    icon: refreshIcon,
                    color: '#ff9800',
                    onClick: () => handleRetryIndexing(params.row.id),
                  },
                ]
              : []),
            ...(params.row.indexingStatus === 'NOT_STARTED'
              ? [
                  {
                    label: 'Start Indexing',
                    icon: refreshIcon,
                    color: '#ff9800',
                    onClick: () => handleRetryIndexing(params.row.id),
                  },
                ]
              : []),
            {
              label: 'Delete Record',
              icon: trashCanIcon,
              color: '#f44336',
              onClick: () =>
                setDeleteDialogData({
                  open: true,
                  recordId: params.row.id,
                  recordName: params.row.recordName,
                }),
              isDanger: true,
            },
          ];

          // Show the menu
          showActionMenu(event.currentTarget, items);
        };

        const handleRetryIndexing = async (recordId: string) => {
          try {
            const response = await axios.post(
              `${CONFIG.backendUrl}/api/v1/knowledgeBase/reindex/record/${recordId}`
            );
            console.log(response);
            setSnackbar({
              open: true,
              message: response.data.reindexResponse.success
                ? 'File indexing started'
                : 'Failed to start reindexing',
              severity: response.data.reindexResponse.success ? 'success' : 'error',
            });
            console.log(response.data.reindexResponse.success);
          } catch (error) {
            console.log('error in re indexing', error);
            setSnackbar({
              open: true,
              message: 'Failed to start reindexing',
              severity: 'error',
            });
          }
        };

        return (
          <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center', mt: 1 }}>
            <IconButton
              size="small"
              onClick={handleActionsClick}
              sx={{
                width: 28,
                height: 28,
                borderRadius: 1.5,
                color: 'text.secondary',
                transition: 'all 0.15s ease-in-out',
                '&:hover': {
                  backgroundColor: alpha('#1976d2', 0.06),
                  color: 'primary.main',
                  transform: 'translateY(-1px)',
                },
                '&:active': {
                  transform: 'translateY(0)',
                },
              }}
            >
              <Icon icon={dotsIcon} fontSize={18} />
            </IconButton>
          </Box>
        );
      },
    },
  ];

  const handleColumnVisibilityClick = (event: React.MouseEvent<HTMLElement>): void => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleUploadDialogClose = () => {
    setOpenUploadDialog(false);
    setFiles([]);
    setFileSizeError({ show: false, files: [] });
    setUploadError({ show: false, message: '' });
  };

  // File size error alert component
  const FileSizeErrorAlert = () => {
    if (!fileSizeError.show) return null;

    return (
      <Box sx={{ mb: 3 }}>
        <Alert
          severity="error"
          onClose={handleFileSizeErrorClose}
          sx={{
            borderRadius: '8px',
            '& .MuiAlert-message': { width: '100%' },
          }}
        >
          <AlertTitle>File size exceeds limit</AlertTitle>
          <Typography variant="body2" sx={{ mb: 1 }}>
            The following file(s) exceed the maximum upload size of 30MB:
          </Typography>
          <Box
            sx={{
              maxHeight: '100px',
              overflowY: 'auto',
              bgcolor: alpha('#f44336', 0.05),
              borderRadius: '4px',
              p: 1,
            }}
          >
            {fileSizeError.files.map((file, index) => (
              <Typography key={index} variant="body2" sx={{ mb: 0.5 }}>
                • {file.name} ({formatFileSize(file.size)})
              </Typography>
            ))}
          </Box>
          <Typography variant="body2" sx={{ mt: 1, fontWeight: 500 }}>
            Please reduce file size or select different files.
          </Typography>
        </Alert>
      </Box>
    );
  };

  // Upload error alert component
  const UploadErrorAlert = () => {
    if (!uploadError.show) return null;

    return (
      <Box sx={{ mb: 3 }}>
        <Alert severity="error" onClose={handleUploadErrorClose} sx={{ borderRadius: '8px' }}>
          <AlertTitle>Upload Failed</AlertTitle>
          <Typography variant="body2">{uploadError.message}</Typography>
        </Alert>
      </Box>
    );
  };

  // Modified handleUpload function with file size validation
  const handleUpload = async () => {
    if (files.length === 0) {
      console.error('No files selected for upload.');
      return;
    }

    // Double-check file sizes before uploading
    const { valid, oversizedFiles } = validateFileSize(files);
    if (!valid) {
      setFileSizeError({
        show: true,
        files: oversizedFiles,
      });
      return;
    }

    try {
      const formData = new FormData();
      files.forEach((file) => {
        formData.append('files', file);
      });

      setUploading(true);
      await uploadKnowledgeBaseFiles(formData);

      handleUploadDialogClose();
      // Trigger a refresh of the knowledge base data
      onSearchChange('');
    } catch (error: any) {
      console.error('Error uploading files:', error);
      setUploadError({
        show: true,
        message: error.message || 'Failed to upload files. Please try again.',
      });
    } finally {
      setUploading(false);
    }
  };

  const handleColumnToggle = (field: string): void => {
    setColumnVisibilityModel((prev) => ({
      ...prev,
      [field]: !prev[field],
    }));
  };

  const handleShowAll = (): void => {
    const allVisible: ColumnVisibilityModel = {};
    columns.forEach((column) => {
      allVisible[column.field] = true;
    });
    setColumnVisibilityModel(allVisible);
  };

  const handleReset = () => {
    setColumnVisibilityModel({});
  };

  const handleDeleteSuccess = () => {
    // Trigger a refresh using the search change handler
    onSearchChange('');
  };

  // Close the delete dialog
  const handleCloseDeleteDialog = () => {
    setDeleteDialogData({
      open: false,
      recordId: '',
      recordName: '',
    });
  };

  const open = Boolean(anchorEl);
  const id = open ? 'column-visibility-popover' : undefined;

  return (
    <Box sx={{ height: 'calc(100vh - 100px)', width: '100%', px: 1 }}>
      {/* Header section */}
      <Paper
        elevation={0}
        sx={{
          mb: 2.5,
          borderRadius: '12px',
        }}
      >
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Typography variant="h5" fontWeight={600} color="text.primary">
            Knowledge Base
          </Typography>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <TextField
              placeholder="Search files..."
              variant="outlined"
              size="small"
              onChange={handleSearchInputChange}
              InputProps={{
                startAdornment: (
                  <Icon icon={magnifyIcon} style={{ marginRight: 8, color: '#757575' }} />
                ),
              }}
              sx={{
                width: 240,
                '& .MuiOutlinedInput-root': {
                  borderRadius: '8px',
                },
              }}
            />
            <Button
              variant="outlined"
              startIcon={<Icon icon={viewColumnIcon} />}
              onClick={handleColumnVisibilityClick}
              sx={{
                width: 100,
                borderRadius: '8px',
              }}
            >
              Columns
            </Button>
            <StyledButton
              variant="outlined"
              startIcon={<Icon icon={uploadIcon} />}
              onClick={() => setOpenUploadDialog(true)}
              sx={{
                width: 100,
                borderRadius: '8px',
              }}
            >
              Upload
            </StyledButton>
          </Stack>
        </Stack>
      </Paper>

      {/* Main content area */}
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          height: 'calc(100vh - 200px)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {loading ? (
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
              Loading knowledge base data...
            </Typography>
          </Box>
        ) : (
          <>
            <Box sx={{ flexGrow: 1, height: 'calc(100% - 64px)' }}>
              <DataGrid<Record>
                rows={knowledgeBaseData?.records || []}
                columns={columns}
                hideFooterPagination
                checkboxSelection
                disableRowSelectionOnClick
                columnVisibilityModel={columnVisibilityModel}
                onColumnVisibilityModelChange={(newModel) => setColumnVisibilityModel(newModel)}
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
                  // Styling for the checkbox cell
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

            {/* Pagination footer */}
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
                {knowledgeBaseData?.pagination?.totalCount === 0
                  ? 'No records found'
                  : `Showing ${(pagination.page - 1) * pagination.limit + 1}-
                  ${Math.min(pagination.page * pagination.limit, knowledgeBaseData?.pagination?.totalCount || 0)} 
                  of ${knowledgeBaseData?.pagination?.totalCount || 0} records`}
              </Typography>

              <Stack direction="row" spacing={2} alignItems="center">
                <Pagination
                  count={knowledgeBaseData?.pagination?.totalPages || 1}
                  page={pagination.page}
                  onChange={(event, value) => onPageChange(value)}
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
                  value={pagination.limit}
                  onChange={(event) => onLimitChange(parseInt(event.target.value as string, 10))}
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

      {/* Column visibility popover */}
      <Popover
        id={id}
        open={open}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'right',
        }}
        transformOrigin={{
          vertical: 'top',
          horizontal: 'right',
        }}
        PaperProps={{
          elevation: 2,
          sx: {
            borderRadius: '12px',
            mt: 1,
            width: 280,
            p: 2,
            boxShadow: '0 4px 20px rgba(0,0,0,0.1)',
          },
        }}
      >
        <Typography variant="subtitle1" sx={{ mb: 1.5, fontWeight: 600 }}>
          Column Visibility
        </Typography>
        <Divider sx={{ mb: 1.5 }} />

        <Box sx={{ maxHeight: 300, overflow: 'auto', pr: 1 }}>
          <FormGroup>
            {columns.map((column) => (
              <FormControlLabel
                key={column.field}
                control={
                  <Checkbox
                    checked={columnVisibilityModel[column.field] !== false}
                    onChange={() => handleColumnToggle(column.field)}
                    size="small"
                    color="primary"
                  />
                }
                label={<Typography variant="body2">{column.headerName}</Typography>}
                sx={{ mb: 0.5 }}
              />
            ))}
          </FormGroup>
        </Box>

        <Divider sx={{ my: 1.5 }} />

        <Stack direction="row" spacing={1} justifyContent="space-between">
          <Button
            size="small"
            variant="outlined"
            onClick={handleReset}
            startIcon={<Icon icon={refreshIcon} />}
            sx={{ borderRadius: '8px' }}
          >
            Reset
          </Button>
          <Button
            size="small"
            variant="contained"
            onClick={handleShowAll}
            startIcon={<Icon icon={eyeIcon} />}
            sx={{ borderRadius: '8px' }}
          >
            Show All
          </Button>
        </Stack>
      </Popover>

      {/* Upload dialog */}
      <Dialog
        open={openUploadDialog}
        onClose={handleUploadDialogClose}
        maxWidth="md"
        fullWidth
        PaperProps={{
          elevation: 3,
          sx: { borderRadius: '12px' },
        }}
      >
        <DialogTitle
          sx={{ px: 3, py: 2.5, borderBottom: '1px solid', borderColor: alpha('#000', 0.08) }}
        >
          <Typography variant="h6" fontWeight={600}>
            Upload Documents
          </Typography>
        </DialogTitle>

        {uploading ? (
          <DialogContent
            sx={{
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              alignItems: 'center',
              minHeight: '250px',
              py: 4,
            }}
          >
            <CircularProgress size={40} thickness={4} sx={{ mb: 3 }} />
            <Typography variant="subtitle1" fontWeight={500}>
              Uploading your files...
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              This may take a few moments
            </Typography>
          </DialogContent>
        ) : (
          <DialogContent sx={{ px: 3, py: 3 }}>
            {/* File Size Error Alert */}
            <FileSizeErrorAlert />

            {/* Upload Error Alert */}
            <UploadErrorAlert />

            <Box
              {...getRootProps()}
              sx={{
                border: '2px dashed',
                borderColor: isDragActive ? 'primary.main' : alpha('#000', 0.15),
                borderRadius: '12px',
                p: 4,
                textAlign: 'center',
                cursor: 'pointer',
                mb: 3,
                transition: 'all 0.2s ease-in-out',
                bgcolor: isDragActive ? alpha('#1976d2', 0.04) : 'transparent',
                '&:hover': {
                  borderColor: 'primary.main',
                  bgcolor: alpha('#1976d2', 0.04),
                },
              }}
            >
              <input {...getInputProps()} />
              <Icon
                icon={cloudIcon}
                style={{
                  fontSize: '48px',
                  marginBottom: '16px',
                  color: isDragActive ? '#1976d2' : '#757575',
                }}
              />
              <Typography variant="h6" sx={{ mb: 1, fontWeight: 500 }}>
                {isDragActive ? 'Drop the files here...' : 'Drag and drop files here'}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                or click to browse from your computer
              </Typography>
              <Button
                variant="outlined"
                size="small"
                startIcon={<Icon icon={filePlusIcon} />}
                sx={{ borderRadius: '8px' }}
              >
                Select Files
              </Button>
              <Typography variant="caption" display="block" sx={{ mt: 2, color: 'text.secondary' }}>
                Maximum file size: 30MB
              </Typography>
            </Box>

            {files.length > 0 && (
              <Paper
                variant="outlined"
                sx={{
                  mt: 3,
                  p: 2,
                  borderRadius: '12px',
                  bgcolor: alpha('#000', 0.01),
                  border: `1px solid ${alpha('#000', 0.08)}`,
                }}
              >
                <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
                  <Typography variant="subtitle1" fontWeight={500}>
                    Selected Files ({files.length})
                  </Typography>
                  <Button
                    size="small"
                    color="error"
                    variant="text"
                    onClick={() => setFiles([])}
                    startIcon={<Icon icon={trashCanIcon} />}
                    sx={{ borderRadius: '8px' }}
                  >
                    Clear All
                  </Button>
                </Stack>

                <Box
                  sx={{
                    maxHeight: '200px',
                    overflow: 'auto',
                    '&::-webkit-scrollbar': {
                      width: '4px',
                    },
                    '&::-webkit-scrollbar-thumb': {
                      backgroundColor: 'rgba(0,0,0,.2)',
                      borderRadius: '4px',
                    },
                  }}
                >
                  {files.map((file, index) => {
                    const extension = file.name.split('.').pop() || '';
                    return (
                      <Stack
                        key={file.name + index}
                        direction="row"
                        alignItems="center"
                        spacing={1.5}
                        sx={{
                          mb: 1,
                          p: 1.5,
                          borderRadius: '8px',
                          '&:hover': {
                            bgcolor: 'background.paper',
                          },
                        }}
                      >
                        <Icon
                          icon={getFileIcon(extension)}
                          style={{
                            fontSize: '24px',
                            color: getFileIconColor(extension),
                            flexShrink: 0,
                          }}
                        />
                        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                          <Typography variant="body2" noWrap title={file.name} fontWeight={500}>
                            {file.name}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {formatFileSize(file.size)}
                          </Typography>
                        </Box>
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => setFiles((prev) => prev.filter((_, i) => i !== index))}
                          sx={{ p: 0.5, flexShrink: 0 }}
                        >
                          <Icon icon={closeIcon} fontSize={18} />
                        </IconButton>
                      </Stack>
                    );
                  })}
                </Box>
              </Paper>
            )}
          </DialogContent>
        )}

        <DialogActions
          sx={{ px: 3, py: 2.5, borderTop: '1px solid', borderColor: alpha('#000', 0.08) }}
        >
          <Button
            onClick={handleUploadDialogClose}
            disabled={uploading}
            color="inherit"
            sx={{ borderRadius: '8px' }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleUpload}
            variant="contained"
            disabled={files.length === 0 || uploading}
            startIcon={<Icon icon={cloudIcon} />}
            sx={{ borderRadius: '8px' }}
          >
            Upload
          </Button>
        </DialogActions>
      </Dialog>

      {/* Actions menu for table rows */}
      <Menu
        anchorEl={menuAnchorEl}
        open={Boolean(menuAnchorEl)}
        onClose={closeActionMenu}
        onClick={closeActionMenu}
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
        {menuItems
          .map((item, index) => {
            const isDangerItem = index > 0 && item.isDanger;

            return [
              // If it's a danger item and not the first item, add a divider before it
              isDangerItem && <Divider key={`divider-${index}`} sx={{ my: 0.75, opacity: 0.6 }} />,
              <MenuItem
                key={`item-${index}`}
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
              </MenuItem>,
            ].filter(Boolean); // Filter out null/undefined elements (when isDangerItem is false)
          })
          .flat()}{' '}
        {/* Flatten the array to remove nested arrays */}
      </Menu>
      {/* Delete Record Dialog */}
      <DeleteRecordDialog
        open={deleteDialogData.open}
        onClose={handleCloseDeleteDialog}
        onRecordDeleted={handleDeleteSuccess}
        recordId={deleteDialogData.recordId}
        recordName={deleteDialogData.recordName}
      />
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
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
  );
}
